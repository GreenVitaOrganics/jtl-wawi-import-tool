"""
Konfigurationsdatei für das JTL Import Tool.
Enthält Login-Daten, URLs und Einstellungen.
"""

# ── JTL Wawi REST API ────────────────────────────────────────────
JTL_API_URL = "https://194.163.144.151:443"
JTL_API_KEY = "FCC3C8D9-1872-4DBA-8053-5EBF323FFAEA"
JTL_API_TIMEOUT = 30
JTL_VERIFY_SSL = False            # Bei Self-Signed Certificates
JTL_API_MAX_RETRIES = 3
JTL_API_RETRY_DELAY = 2          # Sekunden zwischen Retry-Versuchen

# ── Artikel-Matching ──────────────────────────────────────────────
FUZZY_MATCH_THRESHOLD = 85        # Prozent Ähnlichkeit für Fuzzy-Matching
COLOR_KEYWORDS_DE = [
    "grün", "schwarz", "rot", "blau", "weiß", "gelb",
    "orange", "lila", "violett", "rosa", "pink", "braun",
    "grau", "silber", "gold", "türkis", "beige", "bordeaux",
    "neon", "multicolor", "bunt", "transparent", "klar",
]
COLOR_KEYWORDS_EN = [
    "green", "black", "red", "blue", "white", "yellow",
    "orange", "purple", "violet", "pink", "brown",
    "grey", "gray", "silver", "gold", "turquoise", "beige",
    "neon", "multicolor", "colorful", "transparent", "clear",
    "lemon", "tropical", "dream",
]
COLOR_KEYWORDS = COLOR_KEYWORDS_DE + COLOR_KEYWORDS_EN

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
