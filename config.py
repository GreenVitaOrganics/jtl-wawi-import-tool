"""
Konfigurationsdatei für das JTL Import Tool.
Enthält Login-Daten, URLs und Einstellungen.
"""

# ── Knistermann Shop ──────────────────────────────────────────────
KNISTERMANN_BASE_URL = "https://shop.knistermann.de"
KNISTERMANN_LOGIN_URL = f"{KNISTERMANN_BASE_URL}/PrivateLogin/index/requireReload"
KNISTERMANN_SEARCH_URL = f"{KNISTERMANN_BASE_URL}/search"
KNISTERMANN_EMAIL = "info@green-vita-organics.de"
KNISTERMANN_PASSWORD = "13510"

# ── Blackleaf Shop ────────────────────────────────────────────────
BLACKLEAF_BASE_URL = "https://www.blackleaf.de"
BLACKLEAF_SEARCH_URL = f"{BLACKLEAF_BASE_URL}/search"

# ── Preiskalkulation ──────────────────────────────────────────────
# VK = MIN(blackleaf_preis * BLACKLEAF_DISCOUNT, ek_einzel * MARKUP_FACTOR * MwSt)
BLACKLEAF_DISCOUNT = 0.9          # 10 % günstiger als Blackleaf
MARKUP_FACTOR = 2.5               # EK × 2.5
MWST_RATE = 1.19                  # 19 % MwSt

# ── Ausgabe ───────────────────────────────────────────────────────
OUTPUT_DIR = "output"
CSV_SEPARATOR = ";"
CSV_ENCODING = "utf-8-sig"        # UTF-8 mit BOM (Excel-kompatibel)

# ── Scraping ──────────────────────────────────────────────────────
REQUEST_TIMEOUT = 30              # Sekunden
REQUEST_DELAY = 1.5               # Pause zwischen Requests (höflich bleiben)
MAX_RETRIES = 3
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/125.0.0.0 Safari/537.36"
)

# ── Artikel die übersprungen werden (z.B. Versandkosten) ─────────
SKIP_ARTICLE_PREFIXES = ["UPS-PORTO", "PORTO", "VERSAND"]

# ── Bild-Auflösung (Knistermann) ─────────────────────────────────
PREFERRED_IMAGE_SIZE = "800x800"  # Verfügbar: 200x200, 600x600, 800x800, 1280x1280
