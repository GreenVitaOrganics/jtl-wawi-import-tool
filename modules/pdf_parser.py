"""
PDF-Parser für Knistermann Proformarechnungen.

Extrahiert Artikeldaten (Artikelnummer, Beschreibung, Menge, EK-Preis, …)
aus einer PDF-Rechnung.  Unterstützt sowohl das direkte Lesen einer PDF
als auch den Fallback auf eine bereits vorhandene JSON-Datei.
"""

import json
import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

import pdfplumber

logger = logging.getLogger(__name__)


# ── Datenmodell ───────────────────────────────────────────────────
@dataclass
class InvoiceItem:
    """Einzelne Rechnungsposition."""
    pos: int
    artikelnummer: str
    beschreibung: str
    menge: float
    mengeneinheit: str
    ek_preis: float          # Netto-Stückpreis laut Rechnung
    mwst_prozent: float
    total_eur: float
    export_hinweis: Optional[str] = None


@dataclass
class Invoice:
    """Gesamte Rechnung mit Metadaten und Positionen."""
    rechnungsnummer: str
    datum: str
    kundennummer: str
    bestell_referenz: str
    positionen: List[InvoiceItem] = field(default_factory=list)
    nettobetrag: float = 0.0
    bruttobetrag: float = 0.0


# ── Hilfsfunktionen ──────────────────────────────────────────────
def _parse_german_float(text: str) -> float:
    """Wandelt deutsches Zahlenformat '1.234,56' → 1234.56"""
    text = text.strip().replace("€", "").replace("*", "").strip()
    text = text.replace(".", "").replace(",", ".")
    return float(text)


def _clean_text(text: str) -> str:
    """Entfernt überflüssige Leerzeichen."""
    return re.sub(r"\s+", " ", text).strip()


# ── PDF-Parsing ──────────────────────────────────────────────────
def parse_pdf(pdf_path: str) -> Invoice:
    """
    Parst eine Knistermann Proformarechnung-PDF und gibt ein Invoice-Objekt zurück.

    Strategie:
    1. Versuche zuerst, Zeilen aus der PDF zu extrahieren (pdfplumber).
    2. Fallback: Prüfe ob eine rechnung_analyse.json neben der PDF liegt.
    """
    pdf_path = Path(pdf_path)
    if not pdf_path.exists():
        raise FileNotFoundError(f"PDF nicht gefunden: {pdf_path}")

    logger.info(f"Parsing PDF: {pdf_path}")

    # Versuche PDF-Parsing
    try:
        invoice = _parse_pdf_direct(pdf_path)
        if invoice.positionen:
            return invoice
        logger.warning("PDF-Parsing ergab 0 Positionen – versuche JSON-Fallback")
    except Exception as e:
        logger.warning(f"Direktes PDF-Parsing fehlgeschlagen: {e}")

    # Fallback: JSON laden
    json_candidates = [
        pdf_path.parent / "rechnung_analyse.json",
        Path("/home/ubuntu/rechnung_analyse.json"),
    ]
    for json_path in json_candidates:
        if json_path.exists():
            logger.info(f"Fallback: Lade JSON aus {json_path}")
            return _parse_from_json(json_path)

    raise ValueError(f"Konnte keine Rechnungsdaten aus {pdf_path} extrahieren und kein JSON-Fallback gefunden")


def _parse_pdf_direct(pdf_path: Path) -> Invoice:
    """Direktes Parsing der PDF mit pdfplumber."""
    full_text = ""
    tables_data = []

    with pdfplumber.open(str(pdf_path)) as pdf:
        for page in pdf.pages:
            page_text = page.extract_text() or ""
            full_text += page_text + "\n"
            # Tabellen extrahieren
            page_tables = page.extract_tables()
            if page_tables:
                tables_data.extend(page_tables)

    if not full_text.strip():
        raise ValueError("PDF enthält keinen extrahierbaren Text")

    # Metadaten extrahieren
    rechnungsnr = _extract_pattern(full_text, r"(?:Proformarechnung|Rechnung)[^\d]*(\d{10})")
    datum = _extract_pattern(full_text, r"(\d{2}\.\d{2}\.\d{4})")
    kundennr = _extract_pattern(full_text, r"Kd\.?\s*Nr\.?[:\s]*(\d+)")
    bestell_ref = _extract_pattern(full_text, r"(?:Referenz|Best\.?\s*NR)[:\s/]*(\d+)")

    invoice = Invoice(
        rechnungsnummer=rechnungsnr or "unbekannt",
        datum=datum or "unbekannt",
        kundennummer=kundennr or "",
        bestell_referenz=bestell_ref or "",
    )

    # Positionen aus Tabellen extrahieren
    positionen = _extract_positions_from_tables(tables_data)

    # Fallback: Positionen aus Text extrahieren
    if not positionen:
        positionen = _extract_positions_from_text(full_text)

    invoice.positionen = positionen

    # Summen
    netto_match = re.search(r"Netto[:\s]*([0-9.,]+)", full_text)
    brutto_match = re.search(r"(?:Brutto|Gesamt|Zahlbetrag)[:\s]*(?:EUR\s*)?([0-9.,]+)", full_text)
    if netto_match:
        invoice.nettobetrag = _parse_german_float(netto_match.group(1))
    if brutto_match:
        invoice.bruttobetrag = _parse_german_float(brutto_match.group(1))

    logger.info(f"PDF geparst: {len(positionen)} Positionen gefunden")
    return invoice


def _extract_pattern(text: str, pattern: str) -> Optional[str]:
    """Sucht ein Regex-Pattern im Text und gibt die erste Gruppe zurück."""
    m = re.search(pattern, text, re.IGNORECASE)
    return m.group(1) if m else None


def _extract_positions_from_tables(tables: list) -> List[InvoiceItem]:
    """Extrahiert Positionen aus pdfplumber-Tabellen."""
    items = []
    for table in tables:
        for row in table:
            if not row or len(row) < 5:
                continue
            # Versuche, eine Position zu erkennen (erste Spalte = Zahl)
            try:
                cells = [c.strip() if c else "" for c in row]
                # Typisches Format: Pos | ArtNr | Beschreibung | Menge | ME | Preis | MwSt | Total
                pos_str = cells[0]
                if not pos_str or not re.match(r"^\d+$", pos_str):
                    continue

                pos = int(pos_str)
                artikelnr = cells[1] if len(cells) > 1 else ""
                beschreibung = cells[2] if len(cells) > 2 else ""

                # Menge, Preis, Total – je nach Spaltenanzahl
                menge = _parse_german_float(cells[3]) if len(cells) > 3 and cells[3] else 1.0
                mengeneinheit = cells[4] if len(cells) > 4 and cells[4] else "Stk."
                ek_preis = _parse_german_float(cells[5]) if len(cells) > 5 and cells[5] else 0.0
                mwst = _parse_german_float(cells[6]) if len(cells) > 6 and cells[6] else 19.0
                total = _parse_german_float(cells[7]) if len(cells) > 7 and cells[7] else ek_preis * menge

                # Export-Hinweis erkennen
                export_hinweis = None
                full_row_text = " ".join(cells)
                if "Kein Export" in full_row_text or "NUR DE" in full_row_text:
                    export_hinweis = "Kein Export! NUR DE!"

                items.append(InvoiceItem(
                    pos=pos,
                    artikelnummer=artikelnr,
                    beschreibung=_clean_text(beschreibung),
                    menge=menge,
                    mengeneinheit=mengeneinheit,
                    ek_preis=ek_preis,
                    mwst_prozent=mwst,
                    total_eur=total,
                    export_hinweis=export_hinweis,
                ))
            except (ValueError, IndexError):
                continue
    return items


def _extract_positions_from_text(text: str) -> List[InvoiceItem]:
    """Fallback: Positionen per Regex aus dem Fließtext extrahieren."""
    items = []
    # Pattern: Pos ArtNr Beschreibung Menge ME Preis MwSt Total
    pattern = re.compile(
        r"(\d+)\s+"                        # Position
        r"([A-Z0-9][\w\-]+)\s+"            # Artikelnummer
        r"(.+?)\s+"                         # Beschreibung
        r"(\d+[,.]?\d*)\s+"                # Menge
        r"(Stk\.|Display|Pckg\.|Box)\s+"   # Mengeneinheit
        r"(\d+[,.]?\d*)\s+"                # EK-Preis
        r"(\d+[,.]?\d*)\s+"                # MwSt
        r"(\d+[,.]?\d*)",                  # Total
        re.MULTILINE
    )
    for m in pattern.finditer(text):
        try:
            items.append(InvoiceItem(
                pos=int(m.group(1)),
                artikelnummer=m.group(2),
                beschreibung=_clean_text(m.group(3)),
                menge=_parse_german_float(m.group(4)),
                mengeneinheit=m.group(5),
                ek_preis=_parse_german_float(m.group(6)),
                mwst_prozent=_parse_german_float(m.group(7)),
                total_eur=_parse_german_float(m.group(8)),
            ))
        except (ValueError, IndexError):
            continue
    return items


# ── JSON-Fallback ────────────────────────────────────────────────
def _parse_from_json(json_path: Path) -> Invoice:
    """Lädt Rechnungsdaten aus einer bereits erstellten JSON-Datei."""
    with open(json_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    invoice = Invoice(
        rechnungsnummer=data.get("rechnungsnummer", "unbekannt"),
        datum=data.get("auftragsbestaetigungsdatum", "unbekannt"),
        kundennummer=data.get("kundennummer", ""),
        bestell_referenz=data.get("bestell_referenz", ""),
        nettobetrag=data.get("summen", {}).get("nettobetrag", 0.0),
        bruttobetrag=data.get("summen", {}).get("bruttobetrag", 0.0),
    )

    for pos_data in data.get("positionen", []):
        invoice.positionen.append(InvoiceItem(
            pos=pos_data["pos"],
            artikelnummer=pos_data["artikelnummer"],
            beschreibung=pos_data["beschreibung"],
            menge=pos_data["menge"],
            mengeneinheit=pos_data["mengeneinheit"],
            ek_preis=pos_data["ek_preis"],
            mwst_prozent=pos_data["mwst_prozent"],
            total_eur=pos_data["total_eur"],
            export_hinweis=pos_data.get("export_hinweis"),
        ))

    logger.info(f"JSON geparst: {len(invoice.positionen)} Positionen")
    return invoice


# ── CLI-Test ─────────────────────────────────────────────────────
if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.DEBUG)
    path = sys.argv[1] if len(sys.argv) > 1 else "/home/ubuntu/Uploads/Proformarechnung 2026142117.pdf"
    inv = parse_pdf(path)
    print(f"\nRechnung: {inv.rechnungsnummer} | Datum: {inv.datum}")
    print(f"Positionen: {len(inv.positionen)}")
    for item in inv.positionen:
        print(f"  [{item.pos}] {item.artikelnummer:20s} {item.beschreibung[:50]:50s}  "
              f"{item.menge:>5.0f} {item.mengeneinheit:8s} {item.ek_preis:>8.2f} €")
