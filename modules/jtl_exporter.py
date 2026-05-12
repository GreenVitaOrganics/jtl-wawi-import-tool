"""
JTL-Ameise CSV-Exporter.

Erstellt eine CSV-Datei im JTL-Ameise-kompatiblen Format für den Artikel-Import.
Format: UTF-8 BOM, Semikolon-getrennt.
"""

import csv
import logging
import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import List, Optional

import config

logger = logging.getLogger(__name__)


@dataclass
class JTLArticle:
    """Datenmodell für einen JTL-Ameise-Importartikel."""
    artikelnummer: str                   # Lieferanten-Artikelnummer
    artikelname: str                     # Produktname
    ek_netto: float                      # Einkaufspreis netto (Einzelstück)
    vk_brutto: float                     # Verkaufspreis brutto
    bild_urls: List[str]                 # Bild-URLs (bis zu 20)
    lagermenge: Optional[int] = None     # Lagermenge (None = unbekannt)
    lagerampel: str = ""                 # gruen/gelb/rot
    ve_info: str = ""                    # VE-Beschreibung
    ve_menge: int = 1                    # Stückzahl in VE
    mengeneinheit: str = "Stk."          # Mengeneinheit
    mwst_satz: float = 19.0             # MwSt-Satz in %
    kategorie: str = ""                  # Kategorie-Pfad
    beschreibung: str = ""               # Kurzbeschreibung
    export_hinweis: Optional[str] = None # z.B. "Kein Export! NUR DE!"
    ek_ve_preis: float = 0.0            # Original EK VE-Preis
    vk_methode: str = ""                # Wie wurde VK berechnet
    marge_prozent: float = 0.0          # Marge
    blackleaf_preis: Optional[float] = None
    shop_url: str = ""                  # Knistermann Shop-URL


# ── CSV-Spalten für JTL-Ameise ────────────────────────────────────
JTL_COLUMNS = [
    "Artikelnummer",
    "Artikelname",
    "Beschreibung",
    "EK Netto",
    "Std. VK Brutto",
    "MwSt-Satz",
    "Lagerbestand",
    "Bild1",
    "Bild2",
    "Bild3",
    "Bild4",
    "Bild5",
    "VE-Info",
    "VE-Menge",
    "Mengeneinheit",
    "Kategorie",
    "Export-Hinweis",
    "Lagerampel",
    "Blackleaf-VK",
    "EK-VE-Preis",
    "VK-Methode",
    "Marge-%",
    "Shop-URL",
]


def export_csv(articles: List[JTLArticle], output_dir: str = None) -> str:
    """
    Exportiert Artikel als CSV für JTL-Ameise.

    Args:
        articles:   Liste der zu exportierenden Artikel
        output_dir: Ausgabeverzeichnis (default: config.OUTPUT_DIR)

    Returns:
        Pfad zur erstellten CSV-Datei.
    """
    if output_dir is None:
        output_dir = config.OUTPUT_DIR

    # Verzeichnis erstellen
    os.makedirs(output_dir, exist_ok=True)

    # Dateiname mit Zeitstempel
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"jtl_import_{timestamp}.csv"
    filepath = os.path.join(output_dir, filename)

    logger.info(f"Exportiere {len(articles)} Artikel nach {filepath}")

    with open(filepath, "w", newline="", encoding=config.CSV_ENCODING) as f:
        writer = csv.DictWriter(
            f,
            fieldnames=JTL_COLUMNS,
            delimiter=config.CSV_SEPARATOR,
            quoting=csv.QUOTE_MINIMAL,
        )
        writer.writeheader()

        for article in articles:
            row = _article_to_row(article)
            writer.writerow(row)

    logger.info(f"✅ CSV exportiert: {filepath} ({len(articles)} Artikel)")
    return filepath


def _article_to_row(article: JTLArticle) -> dict:
    """Konvertiert einen JTLArticle in eine CSV-Zeile."""
    row = {
        "Artikelnummer": article.artikelnummer,
        "Artikelname": article.artikelname,
        "Beschreibung": article.beschreibung,
        "EK Netto": f"{article.ek_netto:.2f}",
        "Std. VK Brutto": f"{article.vk_brutto:.2f}",
        "MwSt-Satz": f"{article.mwst_satz:.0f}",
        "Lagerbestand": str(article.lagermenge) if article.lagermenge is not None else "",
        "VE-Info": article.ve_info,
        "VE-Menge": str(article.ve_menge),
        "Mengeneinheit": article.mengeneinheit,
        "Kategorie": article.kategorie,
        "Export-Hinweis": article.export_hinweis or "",
        "Lagerampel": article.lagerampel,
        "Blackleaf-VK": f"{article.blackleaf_preis:.2f}" if article.blackleaf_preis else "",
        "EK-VE-Preis": f"{article.ek_ve_preis:.2f}",
        "VK-Methode": article.vk_methode,
        "Marge-%": f"{article.marge_prozent:.1f}",
        "Shop-URL": article.shop_url,
    }

    # Bilder (bis zu 5 Spalten in der CSV)
    for i in range(5):
        key = f"Bild{i+1}"
        row[key] = article.bild_urls[i] if i < len(article.bild_urls) else ""

    return row


def _lagermenge_from_ampel(ampel: str) -> Optional[int]:
    """Schätzt eine Lagermenge basierend auf dem Ampelstatus."""
    mapping = {
        "gruen": 10,
        "gelb": 3,
        "rot": 0,
    }
    return mapping.get(ampel)


def print_summary(articles: List[JTLArticle]) -> None:
    """Gibt eine Zusammenfassung der exportierten Artikel aus."""
    print(f"\n{'='*110}")
    print(f"  JTL Import Zusammenfassung – {len(articles)} Artikel")
    print(f"{'='*110}")
    print(f"  {'ArtNr':<20s} {'Name':<30s} {'VE':>4s} {'EK/VE':>8s} {'EK/Stk':>8s} {'VK':>8s} {'Marge':>7s} {'Ampel':>6s} {'Bilder':>6s}")
    print(f"  {'─'*20} {'─'*30} {'─'*4} {'─'*8} {'─'*8} {'─'*8} {'─'*7} {'─'*6} {'─'*6}")

    total_ek = 0
    total_vk = 0

    for a in articles:
        name = a.artikelname[:28] + ".." if len(a.artikelname) > 30 else a.artikelname
        ampel_icon = {"gruen": "🟢", "gelb": "🟡", "rot": "🔴"}.get(a.lagerampel, "⚪")
        print(
            f"  {a.artikelnummer:<20s} {name:<30s} "
            f"{a.ve_menge:>4d} {a.ek_ve_preis:>8.2f} {a.ek_netto:>8.2f} "
            f"{a.vk_brutto:>8.2f} {a.marge_prozent:>6.1f}% "
            f"{ampel_icon:>6s} {len(a.bild_urls):>6d}"
        )
        total_ek += a.ek_netto
        total_vk += a.vk_brutto

    print(f"  {'─'*20} {'─'*30} {'─'*4} {'─'*8} {'─'*8} {'─'*8}")
    print(f"  {'SUMME':<20s} {'':<30s} {'':>4s} {'':>8s} {total_ek:>8.2f} {total_vk:>8.2f}")
    print(f"{'='*110}\n")
