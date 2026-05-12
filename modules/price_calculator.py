"""
Preiskalkulation für JTL Import.

Berechnet:
1. EK-Einzelpreis = VE_Preis / VE_Menge
2. VK-Preis = MIN(Blackleaf_Preis × 0.9, EK_Einzelpreis × 2.5 × 1.19)

Preis-Strategie bei JTL API Integration:
- Fall 1: Artikel existiert bereits (gleiche Farbe) → EK + VK updaten nach Formel
- Fall 2: Farbvariante existiert → EK neu, VK von bestehender Variante übernehmen
- Fall 3: Komplett neuer Artikel → EK + VK nach Formel berechnen
"""

import logging
from dataclasses import dataclass
from typing import Optional

import config

logger = logging.getLogger(__name__)


@dataclass
class PriceResult:
    """Ergebnis der Preiskalkulation."""
    ek_ve_preis: float              # Original-EK aus Rechnung (VE-Preis)
    ve_menge: int                   # Stückzahl in der VE
    ek_einzelpreis: float           # EK pro Einzelstück (netto)
    vk_brutto: float                # Berechneter VK-Preis (brutto)
    blackleaf_preis: Optional[float] = None  # Blackleaf-VK (brutto)
    vk_methode: str = ""            # Wie wurde der VK berechnet?
    marge_prozent: float = 0.0      # Marge in Prozent
    strategy: str = ""              # Preis-Strategie: "update", "color_variant", "new"
    vk_overridden: bool = False     # VK wurde von bestehender Variante übernommen


def get_price_strategy(
    ek_ve_preis: float,
    ve_menge: int,
    match_type: str,
    matched_item: object = None,
    blackleaf_preis: Optional[float] = None,
) -> PriceResult:
    """
    Bestimmt die Preis-Strategie basierend auf dem Artikel-Matching.

    Args:
        ek_ve_preis:     EK netto aus der Rechnung (VE-Preis)
        ve_menge:        Stückzahl in der VE
        match_type:      Art des Matchings: "exact", "fuzzy", "color_variant", "none"
        matched_item:    Gefundener JTL-Artikel (JTLItem) oder None
        blackleaf_preis: VK brutto von Blackleaf.de

    Returns:
        PriceResult mit Preis-Strategie.
    """
    # EK-Einzelpreis berechnen
    if ve_menge <= 0:
        ve_menge = 1
    ek_einzelpreis = round(ek_ve_preis / ve_menge, 4)

    # ── Fall 1: Artikel existiert bereits (exakt oder fuzzy) ──────
    if match_type in ("exact", "fuzzy") and matched_item is not None:
        # EK + VK nach Formel updaten
        prices = calculate_prices(
            ek_ve_preis=ek_ve_preis,
            ve_menge=ve_menge,
            blackleaf_preis=blackleaf_preis,
        )
        prices.strategy = "update"
        logger.info(
            f"  Strategie UPDATE: Artikel existiert → "
            f"EK={prices.ek_einzelpreis:.2f}€, VK={prices.vk_brutto:.2f}€"
        )
        return prices

    # ── Fall 2: Farbvariante existiert ────────────────────────────
    if match_type == "color_variant" and matched_item is not None:
        # EK neu berechnen, VK von bestehender Variante übernehmen
        existing_vk = getattr(matched_item, "vk_brutto", 0.0) or 0.0

        if existing_vk > 0:
            # VK von der bestehenden Variante übernehmen
            vk_brutto = existing_vk
            methode = (
                f"VK übernommen von Variante '{getattr(matched_item, 'name', '?')}': "
                f"{existing_vk:.2f}€"
            )
            vk_overridden = True
        else:
            # Kein VK vorhanden → Formel verwenden
            prices = calculate_prices(
                ek_ve_preis=ek_ve_preis,
                ve_menge=ve_menge,
                blackleaf_preis=blackleaf_preis,
            )
            vk_brutto = prices.vk_brutto
            methode = prices.vk_methode + " (Variante ohne VK)"
            vk_overridden = False

        # Marge berechnen
        vk_netto = vk_brutto / config.MWST_RATE
        marge = round(((vk_netto - ek_einzelpreis) / ek_einzelpreis) * 100, 1) if ek_einzelpreis > 0 else 0.0

        result = PriceResult(
            ek_ve_preis=ek_ve_preis,
            ve_menge=ve_menge,
            ek_einzelpreis=round(ek_einzelpreis, 2),
            vk_brutto=vk_brutto,
            blackleaf_preis=blackleaf_preis,
            vk_methode=methode,
            marge_prozent=marge,
            strategy="color_variant",
            vk_overridden=vk_overridden,
        )
        logger.info(
            f"  Strategie FARBVARIANTE: EK={result.ek_einzelpreis:.2f}€, "
            f"VK={'übernommen' if vk_overridden else 'berechnet'}={result.vk_brutto:.2f}€"
        )
        return result

    # ── Fall 3: Komplett neuer Artikel ────────────────────────────
    prices = calculate_prices(
        ek_ve_preis=ek_ve_preis,
        ve_menge=ve_menge,
        blackleaf_preis=blackleaf_preis,
    )
    prices.strategy = "new"
    logger.info(
        f"  Strategie NEU: EK={prices.ek_einzelpreis:.2f}€, VK={prices.vk_brutto:.2f}€"
    )
    return prices


def calculate_prices(
    ek_ve_preis: float,
    ve_menge: int,
    blackleaf_preis: Optional[float] = None,
) -> PriceResult:
    """
    Berechnet EK-Einzelpreis und VK-Preis.

    Args:
        ek_ve_preis:     EK netto aus der Rechnung (= Preis für die gesamte VE)
        ve_menge:        Anzahl Einzelstücke in der VE (z.B. 20 bei "20x 25ml")
        blackleaf_preis: VK brutto von Blackleaf.de (None wenn nicht gefunden)

    Returns:
        PriceResult mit allen berechneten Preisen.
    """
    # Sicherheit: VE-Menge nie 0
    if ve_menge <= 0:
        ve_menge = 1
        logger.warning("VE-Menge war ≤ 0, setze auf 1")

    # ── Schritt 1: EK-Einzelpreis ─────────────────────────────────
    ek_einzelpreis = round(ek_ve_preis / ve_menge, 4)

    # ── Schritt 2: VK-Formel (EK × 2.5 × 1.19) ──────────────────
    vk_formel = round(ek_einzelpreis * config.MARKUP_FACTOR * config.MWST_RATE, 2)

    # ── Schritt 3: VK basierend auf Blackleaf ─────────────────────
    vk_blackleaf = None
    if blackleaf_preis is not None and blackleaf_preis > 0:
        vk_blackleaf = round(blackleaf_preis * config.BLACKLEAF_DISCOUNT, 2)

    # ── Schritt 4: Niedrigeren Wert nehmen ────────────────────────
    if vk_blackleaf is not None:
        vk_brutto = min(vk_formel, vk_blackleaf)
        if vk_brutto == vk_blackleaf:
            methode = f"Blackleaf ({blackleaf_preis:.2f}€ × {config.BLACKLEAF_DISCOUNT})"
        else:
            methode = f"Formel (EK {ek_einzelpreis:.2f}€ × {config.MARKUP_FACTOR} × {config.MWST_RATE})"
    else:
        vk_brutto = vk_formel
        methode = f"Formel (EK {ek_einzelpreis:.2f}€ × {config.MARKUP_FACTOR} × {config.MWST_RATE})"

    # ── Schritt 5: Marge berechnen ────────────────────────────────
    # VK netto = VK brutto / 1.19
    vk_netto = vk_brutto / config.MWST_RATE
    if ek_einzelpreis > 0:
        marge = round(((vk_netto - ek_einzelpreis) / ek_einzelpreis) * 100, 1)
    else:
        marge = 0.0

    result = PriceResult(
        ek_ve_preis=ek_ve_preis,
        ve_menge=ve_menge,
        ek_einzelpreis=round(ek_einzelpreis, 2),
        vk_brutto=vk_brutto,
        blackleaf_preis=blackleaf_preis,
        vk_methode=methode,
        marge_prozent=marge,
    )

    logger.debug(
        f"Preis: EK_VE={ek_ve_preis:.2f} / VE={ve_menge} = "
        f"EK_Einzel={ek_einzelpreis:.2f} → VK={vk_brutto:.2f} ({methode})"
    )

    return result


def format_price_summary(results: list) -> str:
    """Formatiert eine Preisübersicht als Text-Tabelle."""
    lines = [
        f"{'ArtNr':<20s} {'EK/VE':>8s} {'VE':>5s} {'EK/Stk':>8s} "
        f"{'BL-VK':>8s} {'VK':>8s} {'Marge':>7s}  Methode",
        "─" * 100,
    ]
    for artnr, pr in results:
        bl = f"{pr.blackleaf_preis:.2f}" if pr.blackleaf_preis else "—"
        lines.append(
            f"{artnr:<20s} {pr.ek_ve_preis:>8.2f} {pr.ve_menge:>5d} {pr.ek_einzelpreis:>8.2f} "
            f"{bl:>8s} {pr.vk_brutto:>8.2f} {pr.marge_prozent:>6.1f}%  {pr.vk_methode}"
        )
    return "\n".join(lines)


# ── CLI-Test ─────────────────────────────────────────────────────
if __name__ == "__main__":
    logging.basicConfig(level=logging.DEBUG)

    # Beispiel: CLU-003 – 130 € für 20x 25ml, Blackleaf 18.90 € pro Beutel
    r = calculate_prices(ek_ve_preis=130.0, ve_menge=20, blackleaf_preis=18.90)
    print(f"CLU-003: EK/Stk = {r.ek_einzelpreis:.2f}€, VK = {r.vk_brutto:.2f}€ ({r.vk_methode})")

    # Beispiel: PURIZE-112 – 35 € für 500er Packung, kein Blackleaf
    r2 = calculate_prices(ek_ve_preis=35.0, ve_menge=500, blackleaf_preis=None)
    print(f"PURIZE-112: EK/Stk = {r2.ek_einzelpreis:.4f}€, VK = {r2.vk_brutto:.2f}€ ({r2.vk_methode})")
