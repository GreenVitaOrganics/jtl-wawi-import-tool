#!/usr/bin/env python3
"""
JTL Import Tool – Hauptscript.

Liest eine Knistermann-Proformarechnung (PDF), scrapt Produktdaten,
berechnet VK-Preise und exportiert eine CSV für JTL-Ameise.

Verwendung:
    python main.py <pfad_zur_rechnung.pdf>
    python main.py <pfad_zur_rechnung.pdf> --skip-knistermann --skip-blackleaf
"""

import argparse
import logging
import os
import sys
import time

# Projektverzeichnis als Modul-Root registrieren
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import config
from modules.pdf_parser import parse_pdf, InvoiceItem
from modules.knistermann_scraper import KnistermannScraper, ProductInfo
from modules.blackleaf_scraper import BlackleafScraper
from modules.price_calculator import calculate_prices, format_price_summary
from modules.jtl_exporter import JTLArticle, export_csv, print_summary

# ── Logging ───────────────────────────────────────────────────────
LOG_FORMAT = "%(asctime)s │ %(levelname)-7s │ %(name)s │ %(message)s"
LOG_DATE = "%H:%M:%S"


def setup_logging(verbose: bool = False):
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(level=level, format=LOG_FORMAT, datefmt=LOG_DATE)
    # Externe Bibliotheken leiser stellen
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("chardet").setLevel(logging.WARNING)


logger = logging.getLogger("jtl_import")


# ── Hilfsfunktionen ──────────────────────────────────────────────
def should_skip_article(item: InvoiceItem) -> bool:
    """Prüft ob ein Artikel übersprungen werden soll (z.B. Versandkosten)."""
    for prefix in config.SKIP_ARTICLE_PREFIXES:
        if item.artikelnummer.upper().startswith(prefix.upper()):
            return True
    return False


# ── Hauptprozess ─────────────────────────────────────────────────
def process_invoice(
    pdf_path: str,
    skip_knistermann: bool = False,
    skip_blackleaf: bool = False,
    output_dir: str = None,
) -> str:
    """
    Hauptprozess: PDF → Scrape → Kalkulation → CSV.

    Args:
        pdf_path:          Pfad zur Proformarechnung-PDF
        skip_knistermann:  Knistermann-Scraping überspringen (Offline-Modus)
        skip_blackleaf:    Blackleaf-Preischeck überspringen
        output_dir:        Ausgabeverzeichnis für CSV

    Returns:
        Pfad zur erstellten CSV-Datei.
    """
    if output_dir is None:
        output_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), config.OUTPUT_DIR)

    # ── 1. PDF parsen ─────────────────────────────────────────────
    logger.info("=" * 60)
    logger.info("SCHRITT 1: PDF-Rechnung parsen")
    logger.info("=" * 60)

    invoice = parse_pdf(pdf_path)
    artikel_items = [item for item in invoice.positionen if not should_skip_article(item)]

    logger.info(f"Rechnung: {invoice.rechnungsnummer} vom {invoice.datum}")
    logger.info(f"Positionen: {len(invoice.positionen)} (davon {len(artikel_items)} Artikel)")
    skipped = len(invoice.positionen) - len(artikel_items)
    if skipped:
        logger.info(f"Übersprungen: {skipped} (Versand/Porto)")

    # ── 2. Knistermann scrapen ────────────────────────────────────
    logger.info("")
    logger.info("=" * 60)
    logger.info("SCHRITT 2: Knistermann Shop scrapen")
    logger.info("=" * 60)

    knistermann_data = {}   # artikelnummer → ProductInfo

    if skip_knistermann:
        logger.info("⏭️  Knistermann-Scraping übersprungen (--skip-knistermann)")
    else:
        scraper = KnistermannScraper()
        if scraper.login():
            for i, item in enumerate(artikel_items, 1):
                logger.info(f"[{i}/{len(artikel_items)}] Scraping: {item.artikelnummer} – "
                           f"{item.beschreibung[:50]}")
                product_info = scraper.scrape_product(item.artikelnummer, item.beschreibung)
                knistermann_data[item.artikelnummer] = product_info

                if i < len(artikel_items):
                    time.sleep(config.REQUEST_DELAY)
        else:
            logger.error("❌ Knistermann Login fehlgeschlagen – fahre ohne Shop-Daten fort")

    # ── 3. Blackleaf Preischeck ───────────────────────────────────
    logger.info("")
    logger.info("=" * 60)
    logger.info("SCHRITT 3: Blackleaf.de Preischeck")
    logger.info("=" * 60)

    blackleaf_data = {}     # artikelnummer → BlackleafResult

    if skip_blackleaf:
        logger.info("⏭️  Blackleaf-Preischeck übersprungen (--skip-blackleaf)")
    else:
        bl_scraper = BlackleafScraper()
        for i, item in enumerate(artikel_items, 1):
            logger.info(f"[{i}/{len(artikel_items)}] Blackleaf: {item.beschreibung[:50]}")
            result = bl_scraper.search_product(item.beschreibung)
            blackleaf_data[item.artikelnummer] = result

            if i < len(artikel_items):
                time.sleep(config.REQUEST_DELAY)

    # ── 4. VE-Umrechnung & Preiskalkulation ───────────────────────
    logger.info("")
    logger.info("=" * 60)
    logger.info("SCHRITT 4: Preiskalkulation")
    logger.info("=" * 60)

    jtl_articles = []
    price_results = []

    for item in artikel_items:
        artnr = item.artikelnummer

        # VE-Menge bestimmen (aus Knistermann oder Rechnungsbeschreibung)
        kn = knistermann_data.get(artnr, ProductInfo(artikelnummer=artnr))
        ve_menge = kn.ve_menge if kn.gefunden and kn.ve_menge > 1 else 1

        # Fallback: VE aus Rechnungsbeschreibung parsen
        if ve_menge == 1:
            from modules.knistermann_scraper import KnistermannScraper
            ve_menge, ve_desc = KnistermannScraper._extract_ve_info(
                item.beschreibung, "", item.beschreibung
            )
            if ve_menge > 1 and not kn.ve_beschreibung:
                kn.ve_beschreibung = ve_desc

        # Blackleaf-Preis
        bl = blackleaf_data.get(artnr)
        bl_preis = bl.preis_brutto if bl and bl.gefunden else None

        # Preisberechnung
        prices = calculate_prices(
            ek_ve_preis=item.ek_preis,
            ve_menge=ve_menge,
            blackleaf_preis=bl_preis,
        )
        price_results.append((artnr, prices))

        # Lagermenge basierend auf Ampel schätzen
        lagermenge = None
        if kn.lagerstatus == "gruen":
            lagermenge = 10
        elif kn.lagerstatus == "gelb":
            lagermenge = 3
        elif kn.lagerstatus == "rot":
            lagermenge = 0

        # JTL-Artikel erstellen
        jtl_article = JTLArticle(
            artikelnummer=artnr,
            artikelname=kn.name if kn.name else item.beschreibung,
            ek_netto=prices.ek_einzelpreis,
            vk_brutto=prices.vk_brutto,
            bild_urls=kn.bild_urls if kn.gefunden else [],
            lagermenge=lagermenge,
            lagerampel=kn.lagerstatus,
            ve_info=kn.ve_beschreibung or f"VE={ve_menge}",
            ve_menge=ve_menge,
            mengeneinheit=item.mengeneinheit,
            mwst_satz=item.mwst_prozent,
            kategorie=kn.kategorie,
            beschreibung=item.beschreibung,
            export_hinweis=item.export_hinweis,
            ek_ve_preis=item.ek_preis,
            vk_methode=prices.vk_methode,
            marge_prozent=prices.marge_prozent,
            blackleaf_preis=bl_preis,
            shop_url=kn.shop_url,
        )
        jtl_articles.append(jtl_article)

    # Preisübersicht anzeigen
    print("\n" + format_price_summary(price_results))

    # ── 5. CSV-Export ─────────────────────────────────────────────
    logger.info("")
    logger.info("=" * 60)
    logger.info("SCHRITT 5: JTL-Ameise CSV Export")
    logger.info("=" * 60)

    csv_path = export_csv(jtl_articles, output_dir=output_dir)

    # Zusammenfassung
    print_summary(jtl_articles)
    logger.info(f"📁 CSV-Datei: {csv_path}")
    logger.info(f"📊 {len(jtl_articles)} Artikel exportiert")

    return csv_path


# ── CLI ───────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(
        description="JTL Import Tool – Proformarechnung → JTL-Ameise CSV",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Beispiele:
  python main.py rechnung.pdf
  python main.py rechnung.pdf --skip-knistermann --skip-blackleaf
  python main.py rechnung.pdf -v --output /tmp/export
        """,
    )
    parser.add_argument(
        "pdf_path",
        help="Pfad zur Proformarechnung-PDF",
    )
    parser.add_argument(
        "--skip-knistermann",
        action="store_true",
        help="Knistermann-Scraping überspringen (Offline-Modus)",
    )
    parser.add_argument(
        "--skip-blackleaf",
        action="store_true",
        help="Blackleaf-Preischeck überspringen",
    )
    parser.add_argument(
        "--output", "-o",
        default=None,
        help=f"Ausgabeverzeichnis (default: {config.OUTPUT_DIR}/)",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Ausführliche Ausgabe (Debug-Level)",
    )

    args = parser.parse_args()
    setup_logging(verbose=args.verbose)

    # Prüfe ob PDF existiert
    if not os.path.exists(args.pdf_path):
        logger.error(f"Datei nicht gefunden: {args.pdf_path}")
        sys.exit(1)

    try:
        csv_path = process_invoice(
            pdf_path=args.pdf_path,
            skip_knistermann=args.skip_knistermann,
            skip_blackleaf=args.skip_blackleaf,
            output_dir=args.output,
        )
        print(f"\n✅ Fertig! CSV-Datei: {csv_path}")
    except KeyboardInterrupt:
        print("\n\n⚠️  Abgebrochen durch Benutzer.")
        sys.exit(130)
    except Exception as e:
        logger.error(f"❌ Fehler: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
