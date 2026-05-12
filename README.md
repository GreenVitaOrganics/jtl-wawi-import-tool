# JTL Import Tool

Automatisiertes Tool zum Import von Knistermann-Proformarechnungen in JTL Wawi – direkt über die **REST API** oder als **CSV für JTL-Ameise**.

## Funktionsweise

```
PDF-Rechnung → PDF-Parser → Knistermann-Scraping → Blackleaf-Preischeck
    → JTL API Matching → Preisstrategie → JTL API Sync / CSV-Export
```

### Ablauf im Detail:

1. **PDF-Parser** – Liest die Proformarechnung und extrahiert Artikeldaten (Nummer, Name, Menge, EK-Preis, Mengeneinheit)
2. **Knistermann Scraper** – Loggt sich im B2B-Shop ein und scrapt für jeden Artikel:
   - VE-Informationen (Verpackungseinheit, z.B. "20x 25ml" = 20 Stück)
   - Produktbilder (URLs in höchster Auflösung)
   - Lagerstatus (Ampel: 🟢 grün / 🟡 gelb / 🔴 rot)
   - Staffelpreise
3. **Blackleaf Preischeck** – Sucht Produkte auf blackleaf.de und extrahiert VK-Preise
4. **JTL API Matching** (NEU) – Vergleicht Artikel mit dem JTL Wawi Bestand:
   - Exaktes Matching über Artikelnummer
   - Fuzzy-Matching über Artikelnamen (ohne Farben)
   - Farbvarianten-Erkennung
5. **Preisstrategie** (NEU) – Bestimmt Preise basierend auf dem Matching:
   - **Artikel existiert** → EK + VK nach Formel updaten
   - **Farbvariante existiert** → EK neu, VK von bestehender Variante übernehmen
   - **Neuer Artikel** → EK + VK nach Formel berechnen
6. **JTL API Sync** (NEU) – Artikel direkt in JTL Wawi anlegen/aktualisieren
7. **CSV-Export** – Erstellt eine JTL-Ameise-kompatible CSV-Datei (immer als Backup)
8. **JSON-Report** – Detaillierter Import-Report (angelegt/aktualisiert/Fehler)

## Installation

```bash
cd /home/ubuntu/jtl_import_tool
pip install -r requirements.txt
```

## JTL Wawi API Setup

### Voraussetzungen:
1. JTL Wawi mit aktiviertem POS-Server (REST API)
2. API-Benutzer mit API-Key (in JTL Wawi → Admin → Benutzer → Bearbeiten → Key erzeugen)
3. POS-Server muss gestartet sein (Worker → Servereinstellungen → "Rest-Server beim Workerstart starten")

### Konfiguration in `config.py`:
```python
JTL_API_URL = "https://194.163.144.151:443"
JTL_API_KEY = "FCC3C8D9-1872-4DBA-8053-5EBF323FFAEA"
JTL_VERIFY_SSL = False  # Bei Self-Signed Certificates
```

### API-Dokumentation (Swagger):
Wenn der POS-Server läuft, ist die Swagger-Doku erreichbar unter:
```
https://194.163.144.151:443/rest/v1/swagger
```

## Verwendung

### Standard: JTL Wawi REST API Modus
```bash
python main.py /pfad/zur/rechnung.pdf
python main.py /pfad/zur/rechnung.pdf --mode api
```

### Simulation (Dry-Run, keine Änderungen an JTL):
```bash
python main.py /pfad/zur/rechnung.pdf --mode api --dry-run
```

### CSV-Export (Fallback für JTL-Ameise):
```bash
python main.py /pfad/zur/rechnung.pdf --mode csv
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

## Kommandozeilen-Optionen

| Option | Beschreibung |
|--------|-------------|
| `pdf_path` | Pfad zur Proformarechnung-PDF (Pflicht) |
| `--mode api` | JTL Wawi REST API (Standard) |
| `--mode csv` | CSV-Export für JTL-Ameise |
| `--dry-run` | Nur Simulation, keine Änderungen an JTL Wawi |
| `--skip-knistermann` | Knistermann-Scraping überspringen |
| `--skip-blackleaf` | Blackleaf-Preischeck überspringen |
| `--output`, `-o` | Ausgabeverzeichnis |
| `--verbose`, `-v` | Debug-Level Ausgabe |

## Projektstruktur

```
jtl_import_tool/
├── main.py                          # Hauptscript (CLI)
├── config.py                        # Konfiguration (API, Login, Faktoren)
├── requirements.txt                 # Python-Abhängigkeiten
├── README.md                        # Diese Datei
├── modules/
│   ├── __init__.py
│   ├── pdf_parser.py                # PDF-Rechnung parsen
│   ├── knistermann_scraper.py       # Knistermann Shop scrapen
│   ├── blackleaf_scraper.py         # Blackleaf.de Preischeck
│   ├── price_calculator.py          # Preiskalkulation + Preisstrategie
│   ├── article_matcher.py           # Intelligentes Artikel-Matching (NEU)
│   ├── jtl_api_client.py            # JTL Wawi REST API Client (NEU)
│   └── jtl_exporter.py             # CSV-Export für JTL-Ameise
└── output/                          # Exportierte Dateien
    ├── jtl_import_YYYYMMDD_HHMMSS.csv
    └── import_report_YYYYMMDD_HHMMSS.json
```

## Preisstrategie

| Situation | EK | VK | Beispiel |
|-----------|----|----|----------|
| Artikel existiert bereits (gleiche Farbe) | Nach Formel updaten | Nach Formel updaten | "Sanaleo VaPen Lemon" existiert → Update |
| Andere Farbvariante existiert | EK neu berechnen | VK von bestehender Variante übernehmen | "VaPen Blue Dream" existiert, "VaPen Lemon" ist neu |
| Komplett neuer Artikel | Nach Formel | Nach Formel (Blackleaf -10% oder EK×2.5×1.19) | Völlig neues Produkt |

### VK-Formeln:
- **Blackleaf-Methode**: `VK = Blackleaf_Preis × 0.9` (10% günstiger als Blackleaf)
- **Aufschlags-Methode**: `VK = EK_Einzelpreis × 2.5 × 1.19` (Markup + MwSt)
- Es wird der **niedrigere** Wert genommen

### VE-Größen (Verpackungseinheiten):

Das Tool unterscheidet intelligent zwischen **echter VE** (Gebinde mit mehreren Einzelstücken) und **Produktbeschreibung** (z.B. "50er Packung" = 1 Einheit):

| Produkt | Beschreibung | VE | EK (VE) | EK/Stück | Erklärung |
|---------|-------------|-----|---------|----------|-----------|
| JAYSAFE Green | "6 Stück im Thekendisplay" | 6 | 22,98€ | **3,83€** | Display mit 6 Einzelstücken → EK ÷ 6 |
| PURIZE XTRA Slim | "50er Packung" | 1 | 4,47€ | **4,47€** | Packung IST der Artikel → keine Teilung |
| ScreenUrin | "20x 25ml" | 20 | 130,00€ | **6,50€** | 20 Beutel → EK ÷ 20 |
| OCB Slim | "50er Heft" | 1 | 0,85€ | **0,85€** | Heft IST der Artikel → keine Teilung |

**Regel-Logik:**
- `"N Stück im Display"`, `"Display N"`, `"Nx Yml"` → **echte VE** (EK wird geteilt)
- `"Ner Packung/Pack/Beutel/Heft"` → **Produktbeschreibung** (VE=1, keine Teilung)
- `"Ner"` mit Zahl ≥ 20 ohne Display-Kontext → **Produktbeschreibung** (VE=1)
- `"Ner"` mit Zahl < 20 → **vermutlich echte VE** (z.B. "6er" = 6 Stück)

## Artikel-Matching (Fuzzy)

Das Tool verwendet intelligentes Matching um Artikel in JTL Wawi zu finden:

1. **Exakt**: Artikelnummer stimmt überein (100% Sicherheit)
2. **Fuzzy**: Artikelname ähnlich (≥85% Übereinstimmung nach Normalisierung)
3. **Farbvariante**: Gleicher Basis-Artikel, andere Farbe erkannt

### Farbenerkennung:
- **Deutsch**: Grün, Schwarz, Rot, Blau, Weiß, Gelb, Orange, Lila, Rosa, Pink, Braun, Grau...
- **Englisch**: Green, Black, Red, Blue, White, Yellow, Orange, Purple, Pink, Brown, Grey...
- **Spezial**: Lemon, Tropical, Dream, Multicolor, Transparent, Neon...

### Normalisierung:
Beim Fuzzy-Matching werden folgende Elemente entfernt:
- Farben (s.o.)
- VE-Informationen (z.B. "20x 25ml", "500er")
- Sonderzeichen (®, ™, ©)
- Größenangaben (z.B. "0,5ml", "5,9mm")

## JSON-Report

Jeder Import erstellt einen detaillierten JSON-Report:

```json
{
  "timestamp": "2026-05-12T10:30:00",
  "mode": "api",
  "dry_run": false,
  "invoice_number": "2026142117",
  "api_connected": true,
  "summary": {
    "total_positions": 20,
    "articles_created": 5,
    "articles_updated": 12,
    "articles_color_variant": 2,
    "articles_failed": 0
  },
  "details": {
    "created": [...],
    "updated": [...],
    "color_variants": [...],
    "failed": [...]
  }
}
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

- **JTL API** – URL, API-Key, SSL, Timeouts, Retry-Logik
- **Artikel-Matching** – Fuzzy-Threshold (85%), Farbkeywords (DE+EN)
- **Login-Daten** – Knistermann E-Mail und Passwort
- **Preisfaktoren** – Markup (2.5×), Blackleaf-Discount (0.9×), MwSt (1.19)
- **Scraping** – Timeouts, Delays, User-Agent
- **Artikel-Filter** – Präfixe die übersprungen werden (z.B. "UPS-PORTO")

## Error-Handling

- **API nicht erreichbar** → Automatischer Fallback auf CSV-Export
- **Authentifizierung fehlschlägt** → Fehlermeldung + CSV-Fallback
- **Einzelner Artikel fehlschlägt** → Wird im Report dokumentiert, Rest wird verarbeitet
- **SSL-Zertifikat ungültig** → `JTL_VERIFY_SSL = False` in config.py

## Hinweise

- Der Knistermann-Shop ist ein B2B-Shop mit Login-Pflicht
- Alle Preise im Shop sind **Netto-Preise**
- VE-Informationen werden aus Titel, Beschreibung und Dropdown extrahiert
- Bei nicht gefundenen Blackleaf-Preisen wird die Formel-Methode verwendet
- Versandkosten-Positionen (UPS-PORTO-*) werden automatisch übersprungen
- Die JTL Wawi REST API ist in der Open-Beta-Phase – Endpunkte können sich ändern
- JTL Wawi synchronisiert automatisch mit JTL POS & Shopify
