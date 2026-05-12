#!/usr/bin/env python3
"""
JTL Import Tool – Hauptscript.

Liest eine Knistermann-Proformarechnung (PDF), scrapt Produktdaten,
berechnet VK-Preise und synchronisiert Artikel mit JTL Wawi (API oder CSV).

Verwendung:
    python main.py <pfad_zur_rechnung.pdf>
    python main.py <pfad_zur_rechnung.pdf> --mode api
    python main.py <pfad_zur_rechnung.pdf> --mode csv
    python main.py <pfad_zur_rechnung.pdf> --mode api --dry-run
    python main.py <pfad_zur_rechnung.pdf> --skip-knistermann --skip-blackleaf
"""

import argparse
import json
import logging
import os
import sys
import time
from datetime import datetime
from typing import Dict, List, Optional

# Projektverzeichnis als Modul-Root registrieren
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import config
from modules.pdf_parser import parse_pdf, InvoiceItem
from modules.knistermann_scraper import KnistermannScraper, ProductInfo
from modules.blackleaf_scraper import BlackleafScraper
from modules.price_calculator import calculate_prices, calculate_ek_per_unit, get_price_strategy, format_price_summary, PriceResult
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


# ── Import-Report ─────────────────────────────────────────────────
class ImportReport:
    """Sammelt Ergebnisse des Import-Prozesses für den JSON-Report."""

    def __init__(self):
        self.timestamp = datetime.now().isoformat()
        self.mode = "csv"
        self.dry_run = False
        self.pdf_path = ""
        self.invoice_number = ""
        self.invoice_date = ""
        self.total_positions = 0
        self.skipped_positions = 0
        self.articles_created = []
        self.articles_updated = []
        self.articles_color_variant = []
        self.articles_failed = []
        self.api_connected = False
        self.api_url = ""
        self.errors = []
        self.warnings = []

    def add_created(self, artikelnummer: str, name: str, ek: float, vk: float, strategy: str):
        self.articles_created.append({
            "artikelnummer": artikelnummer, "name": name,
            "ek_netto": ek, "vk_brutto": vk, "strategy": strategy,
        })

    def add_updated(self, artikelnummer: str, name: str, ek: float, vk: float,
                    matched_name: str, confidence: float):
        self.articles_updated.append({
            "artikelnummer": artikelnummer, "name": name,
            "ek_netto": ek, "vk_brutto": vk,
            "matched_name": matched_name, "confidence": confidence,
        })

    def add_color_variant(self, artikelnummer: str, name: str, ek: float, vk: float,
                          color: str, matched_name: str, vk_overridden: bool):
        self.articles_color_variant.append({
            "artikelnummer": artikelnummer, "name": name,
            "ek_netto": ek, "vk_brutto": vk,
            "color": color, "matched_name": matched_name,
            "vk_overridden": vk_overridden,
        })

    def add_failed(self, artikelnummer: str, name: str, error: str):
        self.articles_failed.append({
            "artikelnummer": artikelnummer, "name": name, "error": error,
        })

    def add_error(self, message: str):
        self.errors.append({"timestamp": datetime.now().isoformat(), "message": message})

    def add_warning(self, message: str):
        self.warnings.append({"timestamp": datetime.now().isoformat(), "message": message})

    def to_dict(self) -> dict:
        return {
            "timestamp": self.timestamp,
            "mode": self.mode,
            "dry_run": self.dry_run,
            "pdf_path": self.pdf_path,
            "invoice_number": self.invoice_number,
            "invoice_date": self.invoice_date,
            "api_connected": self.api_connected,
            "api_url": self.api_url,
            "summary": {
                "total_positions": self.total_positions,
                "skipped_positions": self.skipped_positions,
                "articles_processed": (
                    len(self.articles_created) + len(self.articles_updated) +
                    len(self.articles_color_variant)
                ),
                "articles_created": len(self.articles_created),
                "articles_updated": len(self.articles_updated),
                "articles_color_variant": len(self.articles_color_variant),
                "articles_failed": len(self.articles_failed),
            },
            "details": {
                "created": self.articles_created,
                "updated": self.articles_updated,
                "color_variants": self.articles_color_variant,
                "failed": self.articles_failed,
            },
            "errors": self.errors,
            "warnings": self.warnings,
        }

    def save(self, output_dir: str) -> str:
        """Speichert den Report als JSON-Datei."""
        os.makedirs(output_dir, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filepath = os.path.join(output_dir, f"import_report_{timestamp}.json")
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, indent=2, ensure_ascii=False)
        logger.info(f"📋 Import-Report: {filepath}")
        return filepath


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
    mode: str = "api",
    dry_run: bool = False,
    skip_knistermann: bool = False,
    skip_blackleaf: bool = False,
    output_dir: str = None,
) -> str:
    """
    Hauptprozess: PDF → Scrape → Kalkulation → JTL API / CSV.

    Args:
        pdf_path:          Pfad zur Proformarechnung-PDF
        mode:              "api" (JTL REST API) oder "csv" (CSV-Export)
        dry_run:           Nur Simulation, keine Änderungen
        skip_knistermann:  Knistermann-Scraping überspringen (Offline-Modus)
        skip_blackleaf:    Blackleaf-Preischeck überspringen
        output_dir:        Ausgabeverzeichnis

    Returns:
        Pfad zur erstellten Datei (CSV oder JSON-Report).
    """
    if output_dir is None:
        output_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), config.OUTPUT_DIR)

    report = ImportReport()
    report.mode = mode
    report.dry_run = dry_run
    report.pdf_path = pdf_path

    # ── 1. PDF parsen ─────────────────────────────────────────────
    logger.info("=" * 60)
    logger.info("SCHRITT 1: PDF-Rechnung parsen")
    logger.info("=" * 60)

    invoice = parse_pdf(pdf_path)
    artikel_items = [item for item in invoice.positionen if not should_skip_article(item)]

    report.invoice_number = invoice.rechnungsnummer
    report.invoice_date = invoice.datum
    report.total_positions = len(invoice.positionen)
    report.skipped_positions = len(invoice.positionen) - len(artikel_items)

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
            report.add_warning("Knistermann Login fehlgeschlagen")

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

    # ── 4. JTL API Matching & Preisstrategie ──────────────────────
    logger.info("")
    logger.info("=" * 60)
    if mode == "api":
        logger.info("SCHRITT 4: JTL Wawi API – Artikel-Matching & Preisstrategie")
    else:
        logger.info("SCHRITT 4: Preiskalkulation")
    logger.info("=" * 60)

    # API-Client und Matcher initialisieren (nur im API-Modus)
    jtl_client = None
    matcher = None
    api_available = False

    if mode == "api":
        try:
            from modules.jtl_api_client import JTLApiClient
            from modules.article_matcher import ArticleMatcher

            jtl_client = JTLApiClient()
            report.api_url = config.JTL_API_URL

            if jtl_client.test_connection():
                api_available = True
                report.api_connected = True
                logger.info("JTL API verbunden – lade bestehende Artikel...")

                # Alle Artikel aus JTL laden
                jtl_articles = jtl_client.get_all_articles()
                logger.info(f"📦 {len(jtl_articles)} Artikel aus JTL Wawi geladen")

                matcher = ArticleMatcher(jtl_articles)
            else:
                logger.warning("⚠️  JTL API nicht erreichbar – Fallback auf CSV-Modus")
                report.add_warning("JTL API nicht erreichbar – Fallback auf CSV")
                mode = "csv"

        except Exception as e:
            logger.error(f"❌ JTL API Fehler: {e}")
            report.add_error(f"JTL API Initialisierung fehlgeschlagen: {e}")
            logger.warning("⚠️  Fallback auf CSV-Modus")
            mode = "csv"

    # ── Artikel verarbeiten ───────────────────────────────────────
    jtl_articles_out = []
    price_results = []

    for item in artikel_items:
        artnr = item.artikelnummer

        # VE-Menge bestimmen
        kn = knistermann_data.get(artnr, ProductInfo(artikelnummer=artnr))
        ve_menge = kn.ve_menge if kn.gefunden and kn.ve_menge > 1 else 1

        # Fallback: VE aus Rechnungsbeschreibung parsen
        if ve_menge == 1:
            ve_menge, ve_desc = KnistermannScraper._extract_ve_info(
                item.beschreibung, "", item.beschreibung
            )
            if ve_menge > 1 and not kn.ve_beschreibung:
                kn.ve_beschreibung = ve_desc

        # Blackleaf-Preis
        bl = blackleaf_data.get(artnr)
        bl_preis = bl.preis_brutto if bl and bl.gefunden else None

        # ── Preisstrategie bestimmen ──────────────────────────────
        artikelname = kn.name if kn.name else item.beschreibung

        if api_available and matcher:
            # Matching gegen JTL-Bestand
            from modules.article_matcher import ArticleMatcher
            match_result = matcher.find_match(artnr, artikelname)

            logger.info(
                f"  [{artnr}] Match: {match_result.match_type} "
                f"({match_result.confidence:.0f}%) – {match_result.message}"
            )

            # Preisstrategie
            prices = get_price_strategy(
                ek_ve_preis=item.ek_preis,
                ve_menge=ve_menge,
                match_type=match_result.match_type,
                matched_item=match_result.matched_item,
                blackleaf_preis=bl_preis,
            )
        else:
            # Standard-Kalkulation (CSV-Modus)
            prices = calculate_prices(
                ek_ve_preis=item.ek_preis,
                ve_menge=ve_menge,
                blackleaf_preis=bl_preis,
            )
            prices.strategy = "new"
            match_result = None

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
            artikelname=artikelname,
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
        jtl_articles_out.append(jtl_article)

        # ── JTL API: Artikel anlegen/aktualisieren ────────────────
        if api_available and jtl_client and not dry_run:
            try:
                _sync_to_jtl_api(
                    jtl_client, jtl_article, prices, match_result, report
                )
            except Exception as e:
                logger.error(f"❌ API-Fehler für {artnr}: {e}")
                report.add_failed(artnr, artikelname, str(e))

        elif api_available and dry_run:
            # Dry-Run: Nur simulieren
            action = {
                "update": "AKTUALISIEREN",
                "color_variant": "FARBVARIANTE ANLEGEN",
                "new": "NEU ANLEGEN",
            }.get(prices.strategy, "?")
            logger.info(f"  🔍 DRY-RUN: Würde {artnr} → {action}")

            # Für Report trotzdem erfassen
            if prices.strategy == "new":
                report.add_created(artnr, artikelname, prices.ek_einzelpreis,
                                   prices.vk_brutto, prices.strategy)
            elif prices.strategy == "update":
                matched_name = match_result.matched_name if match_result else ""
                confidence = match_result.confidence if match_result else 0
                report.add_updated(artnr, artikelname, prices.ek_einzelpreis,
                                   prices.vk_brutto, matched_name, confidence)
            elif prices.strategy == "color_variant":
                color = match_result.color_detected if match_result else ""
                matched_name = match_result.matched_name if match_result else ""
                report.add_color_variant(artnr, artikelname, prices.ek_einzelpreis,
                                         prices.vk_brutto, color, matched_name,
                                         prices.vk_overridden)

    # Preisübersicht anzeigen
    print("\n" + format_price_summary(price_results))

    # ── 5. Export (CSV und/oder JSON-Report) ──────────────────────
    logger.info("")
    logger.info("=" * 60)
    logger.info("SCHRITT 5: Export")
    logger.info("=" * 60)

    output_path = ""

    # CSV immer exportieren (als Fallback / Dokumentation)
    csv_path = export_csv(jtl_articles_out, output_dir=output_dir)
    output_path = csv_path

    # JSON-Report speichern
    report_path = report.save(output_dir)

    # Zusammenfassung
    print_summary(jtl_articles_out)
    _print_api_summary(report, mode, dry_run)

    logger.info(f"📁 CSV-Datei: {csv_path}")
    logger.info(f"📋 JSON-Report: {report_path}")
    logger.info(f"📊 {len(jtl_articles_out)} Artikel verarbeitet")

    return output_path


def _sync_to_jtl_api(
    client,
    article: JTLArticle,
    prices: PriceResult,
    match_result,
    report: ImportReport,
):
    """
    Synchronisiert einen einzelnen Artikel mit der JTL Wawi API.

    Args:
        client:       JTLApiClient
        article:      Zu synchronisierender Artikel
        prices:       Berechnete Preise
        match_result: Ergebnis des Artikel-Matchings
        report:       ImportReport für die Dokumentation
    """
    artnr = article.artikelnummer
    name = article.artikelname

    # Payload vorbereiten
    payload = client.build_article_payload(
        artikelnummer=artnr,
        name=name,
        ek_netto=prices.ek_einzelpreis,
        vk_brutto=prices.vk_brutto,
        beschreibung=article.beschreibung,
        mwst_satz=article.mwst_satz,
        kategorie=article.kategorie,
        bild_urls=article.bild_urls,
        lagermenge=article.lagermenge,
        ve_info=article.ve_info,
        export_hinweis=article.export_hinweis,
    )

    if prices.strategy == "update" and match_result and match_result.matched_item:
        # Bestehenden Artikel aktualisieren
        item_id = match_result.matched_item.item_id
        success = client.update_article(item_id, payload)
        if success:
            logger.info(f"  ✅ {artnr} aktualisiert (ID={item_id})")
            report.add_updated(
                artnr, name, prices.ek_einzelpreis, prices.vk_brutto,
                match_result.matched_name, match_result.confidence,
            )
        else:
            report.add_failed(artnr, name, "Update fehlgeschlagen")

    elif prices.strategy == "color_variant":
        # Neue Farbvariante anlegen
        created = client.create_article(payload)
        if created:
            logger.info(f"  ✅ {artnr} als Farbvariante angelegt")
            color = match_result.color_detected if match_result else ""
            matched_name = match_result.matched_name if match_result else ""
            report.add_color_variant(
                artnr, name, prices.ek_einzelpreis, prices.vk_brutto,
                color, matched_name, prices.vk_overridden,
            )
        else:
            report.add_failed(artnr, name, "Farbvariante konnte nicht angelegt werden")

    else:
        # Neuen Artikel anlegen
        created = client.create_article(payload)
        if created:
            logger.info(f"  ✅ {artnr} neu angelegt")
            report.add_created(artnr, name, prices.ek_einzelpreis,
                               prices.vk_brutto, prices.strategy)
        else:
            report.add_failed(artnr, name, "Artikel konnte nicht angelegt werden")


def _print_api_summary(report: ImportReport, mode: str, dry_run: bool):
    """Gibt eine Zusammenfassung der API-Aktionen aus."""
    data = report.to_dict()
    summary = data["summary"]

    print(f"\n{'='*70}")
    prefix = "🔍 DRY-RUN " if dry_run else ""
    print(f"  {prefix}Import-Zusammenfassung ({mode.upper()}-Modus)")
    print(f"{'='*70}")
    print(f"  Rechnung:         {report.invoice_number} vom {report.invoice_date}")
    print(f"  API verbunden:    {'✅ Ja' if report.api_connected else '❌ Nein'}")
    if report.api_url:
        print(f"  API URL:          {report.api_url}")
    print(f"  {'─'*66}")
    print(f"  Gesamt Positionen:      {summary['total_positions']}")
    print(f"  Übersprungen:           {summary['skipped_positions']}")
    print(f"  Verarbeitet:            {summary['articles_processed']}")
    print(f"    ├── Neu angelegt:     {summary['articles_created']}")
    print(f"    ├── Aktualisiert:     {summary['articles_updated']}")
    print(f"    ├── Farbvarianten:    {summary['articles_color_variant']}")
    print(f"    └── Fehler:           {summary['articles_failed']}")

    if report.errors:
        print(f"\n  ⚠️  Fehler:")
        for err in report.errors:
            print(f"    • {err['message']}")

    if report.warnings:
        print(f"\n  ℹ️  Warnungen:")
        for warn in report.warnings:
            print(f"    • {warn['message']}")

    print(f"{'='*70}\n")


# ── CLI ───────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(
        description="JTL Import Tool – Proformarechnung → JTL Wawi API / CSV",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Beispiele:
  python main.py rechnung.pdf                          # API-Modus (Standard)
  python main.py rechnung.pdf --mode api               # JTL Wawi REST API
  python main.py rechnung.pdf --mode csv               # CSV-Export (Fallback)
  python main.py rechnung.pdf --mode api --dry-run     # Simulation (keine Änderungen)
  python main.py rechnung.pdf --skip-knistermann --skip-blackleaf
  python main.py rechnung.pdf -v --output /tmp/export
        """,
    )
    parser.add_argument(
        "pdf_path",
        help="Pfad zur Proformarechnung-PDF",
    )
    parser.add_argument(
        "--mode", "-m",
        choices=["api", "csv"],
        default="api",
        help="Modus: 'api' = JTL Wawi REST API (Standard), 'csv' = CSV-Export",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Nur Simulation – keine Änderungen an JTL Wawi",
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
        result_path = process_invoice(
            pdf_path=args.pdf_path,
            mode=args.mode,
            dry_run=args.dry_run,
            skip_knistermann=args.skip_knistermann,
            skip_blackleaf=args.skip_blackleaf,
            output_dir=args.output,
        )
        dry_hint = " (DRY-RUN – keine Änderungen)" if args.dry_run else ""
        print(f"\n✅ Fertig!{dry_hint}")
        print(f"   Ausgabe: {result_path}")
    except KeyboardInterrupt:
        print("\n\n⚠️  Abgebrochen durch Benutzer.")
        sys.exit(130)
    except Exception as e:
        logger.error(f"❌ Fehler: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
