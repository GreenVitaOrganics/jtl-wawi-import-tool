# JTL Import Tool

Automatisiertes Tool zum Erstellen von JTL-Ameise CSV-Importdateien aus Knistermann-Proformarechnungen.

## Funktionsweise

```
PDF-Rechnung → PDF-Parser → Knistermann-Scraping → Blackleaf-Preischeck → Preiskalkulation → CSV-Export
```

### Ablauf im Detail:

1. **PDF-Parser** – Liest die Proformarechnung und extrahiert Artikeldaten (Nummer, Name, Menge, EK-Preis, Mengeneinheit)
2. **Knistermann Scraper** – Loggt sich im B2B-Shop ein und scrapt für jeden Artikel:
   - VE-Informationen (Verpackungseinheit, z.B. "20x 25ml" = 20 Stück)
   - Produktbilder (URLs in höchster Auflösung)
   - Lagerstatus (Ampel: 🟢 grün / 🟡 gelb / 🔴 rot)
   - Staffelpreise
3. **Blackleaf Preischeck** – Sucht Produkte auf blackleaf.de und extrahiert VK-Preise
4. **VE-Umrechnung** – Berechnet den EK-Einzelpreis: `EK_Einzelpreis = VE_Preis / VE_Menge`
5. **VK-Kalkulation** – Berechnet den optimalen VK-Preis:
   - Wenn Blackleaf-Preis vorhanden: `VK = Blackleaf_Preis × 0.9` (10% günstiger)
   - Sonst: `VK = EK_Einzelpreis × 2.5 × 1.19` (Formel)
   - Es wird der **niedrigere** Wert genommen
6. **CSV-Export** – Erstellt eine JTL-Ameise-kompatible CSV-Datei

## Installation

```bash
cd /home/ubuntu/jtl_import_tool
pip install -r requirements.txt
```

## Verwendung

### Vollständiger Durchlauf (mit Scraping):
```bash
python main.py /pfad/zur/rechnung.pdf
```

### Offline-Modus (nur PDF-Parsing + Kalkulation):
```bash
python main.py /pfad/zur/rechnung.pdf --skip-knistermann --skip-blackleaf
```

### Mit ausführlicher Ausgabe:
```bash
python main.py /pfad/zur/rechnung.pdf -v
```

### Eigenes Ausgabeverzeichnis:
```bash
python main.py /pfad/zur/rechnung.pdf --output /tmp/export
```

## Projektstruktur

```
jtl_import_tool/
├── main.py                          # Hauptscript (CLI)
├── config.py                        # Konfiguration (Login, URLs, Faktoren)
├── requirements.txt                 # Python-Abhängigkeiten
├── README.md                        # Diese Datei
├── modules/
│   ├── __init__.py
│   ├── pdf_parser.py                # PDF-Rechnung parsen
│   ├── knistermann_scraper.py       # Knistermann Shop scrapen
│   ├── blackleaf_scraper.py         # Blackleaf.de Preischeck
│   ├── price_calculator.py          # Preiskalkulation (EK→VK)
│   └── jtl_exporter.py             # CSV-Export für JTL-Ameise
└── output/                          # Exportierte CSV-Dateien
    └── jtl_import_YYYYMMDD_HHMMSS.csv
```

## CSV-Format

Die exportierte CSV enthält folgende Spalten (Semikolon-getrennt, UTF-8 BOM):

| Spalte | Beschreibung |
|--------|-------------|
| Artikelnummer | Lieferanten-Artikelnummer |
| Artikelname | Produktname |
| Beschreibung | Kurzbeschreibung |
| EK Netto | Einkaufspreis netto (Einzelstück!) |
| Std. VK Brutto | Verkaufspreis brutto |
| MwSt-Satz | MwSt-Satz (19%) |
| Lagerbestand | Geschätzte Lagermenge |
| Bild1–Bild5 | Bild-URLs |
| VE-Info | VE-Beschreibung (z.B. "20x 25ml") |
| VE-Menge | Stückzahl in der VE |
| Mengeneinheit | Stk./Display/Pckg./Box |
| Kategorie | Kategorie-Pfad |
| Export-Hinweis | z.B. "Kein Export! NUR DE!" |
| Lagerampel | gruen/gelb/rot |
| Blackleaf-VK | Blackleaf-Preis (falls gefunden) |
| EK-VE-Preis | Original VE-Preis aus Rechnung |
| VK-Methode | Berechnungsmethode |
| Marge-% | Berechnete Marge |
| Shop-URL | Knistermann Produkt-URL |

## Konfiguration

Alle Einstellungen befinden sich in `config.py`:

- **Login-Daten** – Knistermann E-Mail und Passwort
- **Preisfaktoren** – Markup (2.5×), Blackleaf-Discount (0.9×), MwSt (1.19)
- **Scraping** – Timeouts, Delays, User-Agent
- **Artikel-Filter** – Präfixe die übersprungen werden (z.B. "UPS-PORTO")

## Hinweise

- Der Knistermann-Shop ist ein B2B-Shop mit Login-Pflicht
- Alle Preise im Shop sind **Netto-Preise**
- VE-Informationen sind nicht standardisiert und werden aus Titel, Beschreibung und Dropdown extrahiert
- Bei nicht gefundenen Blackleaf-Preisen wird die Formel-Methode verwendet
- Versandkosten-Positionen (UPS-PORTO-*) werden automatisch übersprungen
