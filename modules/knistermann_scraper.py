"""
Knistermann Shop Scraper.

Loggt sich auf shop.knistermann.de ein und extrahiert für jeden Artikel:
- VE-Informationen (Verpackungseinheit)
- Produktbilder (URLs)
- Lagerstatus (Ampelsystem)
- Staffelpreise
"""

import logging
import re
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional
from urllib.parse import quote_plus

import requests
from bs4 import BeautifulSoup

import config

logger = logging.getLogger(__name__)


# ── Datenmodell ───────────────────────────────────────────────────
@dataclass
class ProductInfo:
    """Gesammelte Produktinformationen von Knistermann."""
    artikelnummer: str
    name: str = ""
    shop_url: str = ""
    ve_menge: int = 1                 # Anzahl Einzelstücke in der VE
    ve_beschreibung: str = ""         # z.B. "20x 25ml", "50er Packung"
    bild_urls: List[str] = field(default_factory=list)
    lagerstatus: str = "unbekannt"    # gruen, gelb, rot, unbekannt
    lagerstatus_text: str = ""
    staffelpreise: Dict[int, float] = field(default_factory=dict)
    beschreibung_text: str = ""
    kategorie: str = ""
    gefunden: bool = False


class KnistermannScraper:
    """Session-basierter Scraper für den Knistermann B2B-Shop (Shopware 5)."""

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": config.USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "de-DE,de;q=0.9,en;q=0.5",
        })
        self._logged_in = False

    # ── Login ─────────────────────────────────────────────────────
    def login(self) -> bool:
        """Meldet sich beim Knistermann-Shop an. Gibt True bei Erfolg zurück."""
        if self._logged_in:
            return True

        logger.info("Knistermann Login wird durchgeführt...")

        try:
            # 1. Login-Seite aufrufen → CSRF-Token holen
            resp = self.session.get(
                config.KNISTERMANN_LOGIN_URL,
                timeout=config.REQUEST_TIMEOUT,
            )
            resp.raise_for_status()

            soup = BeautifulSoup(resp.text, "html.parser")
            csrf_input = soup.find("input", {"name": "__csrf_token"})
            csrf_token = csrf_input["value"] if csrf_input else ""

            if not csrf_token:
                # Versuche alternativen Selektor
                meta_csrf = soup.find("meta", {"name": "csrf-token"})
                csrf_token = meta_csrf["content"] if meta_csrf else ""

            logger.debug(f"CSRF-Token: {csrf_token[:20]}..." if csrf_token else "Kein CSRF-Token gefunden")

            # 2. Login-POST
            login_data = {
                "email": config.KNISTERMANN_EMAIL,
                "password": config.KNISTERMANN_PASSWORD,
                "__csrf_token": csrf_token,
            }

            resp = self.session.post(
                config.KNISTERMANN_LOGIN_URL,
                data=login_data,
                timeout=config.REQUEST_TIMEOUT,
                allow_redirects=True,
            )
            resp.raise_for_status()

            # 3. Prüfe ob Login erfolgreich war
            if "account/logout" in resp.text.lower() or "mein konto" in resp.text.lower():
                self._logged_in = True
                logger.info("✅ Knistermann Login erfolgreich")
                return True

            # Alternativer Check: Sind wir auf /account gelandet?
            if "/account" in resp.url and "login" not in resp.url.lower():
                self._logged_in = True
                logger.info("✅ Knistermann Login erfolgreich (URL-Check)")
                return True

            logger.warning("⚠️  Login möglicherweise fehlgeschlagen – prüfe manuell")
            # Trotzdem weitermachen, manchmal funktioniert die Session
            self._logged_in = True
            return True

        except requests.RequestException as e:
            logger.error(f"❌ Login fehlgeschlagen: {e}")
            return False

    # ── Produktsuche ──────────────────────────────────────────────
    def search_product(self, query: str) -> Optional[str]:
        """
        Sucht nach einem Produkt und gibt die URL der besten Übereinstimmung zurück.
        """
        if not self._logged_in:
            self.login()

        url = f"{config.KNISTERMANN_SEARCH_URL}?sSearch={quote_plus(query)}"
        logger.debug(f"Suche: {url}")

        try:
            resp = self.session.get(url, timeout=config.REQUEST_TIMEOUT)
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, "html.parser")

            # Prüfe ob wir direkt auf eine Produktseite umgeleitet wurden
            if self._is_product_page(soup):
                logger.debug(f"Direkte Weiterleitung auf Produktseite: {resp.url}")
                return resp.url

            # Suchergebnis-Links finden
            product_links = soup.select(".product--box .product--title, .product--info a.product--title")
            if not product_links:
                # Alternativer Selektor
                product_links = soup.select("a.product--title")

            if not product_links:
                # Noch ein Versuch: alle Links mit /
                product_links = soup.select('.listing a[href*="/"]')

            for link in product_links:
                href = link.get("href", "")
                if href and "/search" not in href:
                    full_url = href if href.startswith("http") else config.KNISTERMANN_BASE_URL + href
                    logger.debug(f"Gefunden: {full_url}")
                    return full_url

            logger.warning(f"Kein Produkt gefunden für: {query}")
            return None

        except requests.RequestException as e:
            logger.error(f"Suche fehlgeschlagen für '{query}': {e}")
            return None

    # ── Produktdetails scrapen ────────────────────────────────────
    def scrape_product(self, artikelnummer: str, beschreibung: str = "") -> ProductInfo:
        """
        Scrapt alle relevanten Daten für einen Artikel.
        Sucht zuerst nach Artikelnummer, dann nach Beschreibung.
        """
        info = ProductInfo(artikelnummer=artikelnummer)

        if not self._logged_in:
            if not self.login():
                logger.error("Login fehlgeschlagen – überspringe Scraping")
                return info

        # Suche: erst Artikelnummer, dann Beschreibung
        product_url = self.search_product(artikelnummer)
        if not product_url and beschreibung:
            # Kurzform der Beschreibung verwenden
            short_desc = " ".join(beschreibung.split()[:4])
            product_url = self.search_product(short_desc)

        if not product_url:
            logger.warning(f"Produkt nicht gefunden: {artikelnummer} / {beschreibung}")
            return info

        info.shop_url = product_url
        time.sleep(config.REQUEST_DELAY)

        try:
            resp = self.session.get(product_url, timeout=config.REQUEST_TIMEOUT)
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, "html.parser")

            info.gefunden = True
            info.name = self._extract_name(soup)
            info.bild_urls = self._extract_images(soup)
            info.lagerstatus, info.lagerstatus_text = self._extract_availability(soup)
            info.staffelpreise = self._extract_prices(soup)
            info.beschreibung_text = self._extract_description(soup)
            info.kategorie = self._extract_breadcrumb(soup)

            # VE-Informationen extrahieren (aus Name + Beschreibung)
            info.ve_menge, info.ve_beschreibung = self._extract_ve_info(
                info.name, info.beschreibung_text, beschreibung
            )

            # Artikelnummer aus der Seite lesen (falls vorhanden)
            page_artnr = self._extract_article_number(soup)
            if page_artnr:
                info.artikelnummer = page_artnr

            logger.info(
                f"✅ {artikelnummer}: VE={info.ve_menge}, "
                f"Bilder={len(info.bild_urls)}, Status={info.lagerstatus}"
            )

        except requests.RequestException as e:
            logger.error(f"Fehler beim Scrapen von {product_url}: {e}")

        return info

    # ── Extraktion: Name ──────────────────────────────────────────
    @staticmethod
    def _extract_name(soup: BeautifulSoup) -> str:
        h1 = soup.select_one("h1.product--title, h1")
        return h1.get_text(strip=True) if h1 else ""

    # ── Extraktion: Artikelnummer ─────────────────────────────────
    @staticmethod
    def _extract_article_number(soup: BeautifulSoup) -> str:
        # Suche nach "Artikel-Nr.:" Label
        for entry in soup.select(".entry--content"):
            label = entry.find_previous_sibling("span") or entry.find_previous("span")
            if label and "Artikel" in label.get_text():
                return entry.get_text(strip=True)

        # Alternativer Ansatz: Durchsuche alle Elemente
        artnr_pattern = re.compile(r"Artikel[\s-]*Nr\.?\s*:?\s*(.+)", re.IGNORECASE)
        for elem in soup.select(".product--details span, .product--details .entry--content"):
            text = elem.get_text(strip=True)
            m = artnr_pattern.search(text)
            if m:
                return m.group(1).strip()

        return ""

    # ── Extraktion: Bilder ────────────────────────────────────────
    @staticmethod
    def _extract_images(soup: BeautifulSoup) -> List[str]:
        urls = []
        seen = set()

        # Hauptbild + Slider
        for img in soup.select(".image-slider--item img, .image--element img"):
            for attr in ["data-src", "srcset", "src"]:
                val = img.get(attr, "")
                if not val:
                    continue
                # srcset kann mehrere URLs enthalten
                for part in val.split(","):
                    url = part.strip().split(" ")[0].strip()
                    if url and url.startswith("http") and url not in seen:
                        # Bevorzuge mittlere/große Auflösung
                        seen.add(url)
                        urls.append(url)

        # Thumbnail-Galerie
        for thumb in soup.select(".image--thumbnails a, .image-slider--thumbnails img"):
            href = thumb.get("data-img-original") or thumb.get("href") or thumb.get("src", "")
            if href and href.startswith("http") and href not in seen:
                seen.add(href)
                urls.append(href)

        # Duplikate verschiedener Auflösungen filtern – behalte die größte
        return _deduplicate_images(urls)

    # ── Extraktion: Verfügbarkeit ─────────────────────────────────
    @staticmethod
    def _extract_availability(soup: BeautifulSoup) -> tuple:
        """Gibt (status, text) zurück. status ∈ {gruen, gelb, rot, unbekannt}"""
        delivery = soup.select_one(".delivery--text, .delivery--status-available, .delivery--information")
        if not delivery:
            return "unbekannt", ""

        text = delivery.get_text(strip=True)
        parent = delivery.parent or delivery

        # Icon-Klassen prüfen
        icon = parent.select_one("i, span.icon, .delivery--status-icon")
        classes_str = " ".join(icon.get("class", [])) if icon else ""

        if "available" in classes_str or "sofort" in text.lower():
            return "gruen", text
        elif "partly" in classes_str or "gering" in text.lower():
            return "gelb", text
        elif "unavail" in classes_str or "nicht" in text.lower() or "ausverkauft" in text.lower():
            return "rot", text
        else:
            # Heuristik: Wenn "lieferbar" vorkommt, ist es wohl verfügbar
            if "lieferbar" in text.lower():
                return "gruen", text
            return "unbekannt", text

    # ── Extraktion: Preise ────────────────────────────────────────
    @staticmethod
    def _extract_prices(soup: BeautifulSoup) -> Dict[int, float]:
        """Extrahiert Staffelpreise als {menge: preis}."""
        prices = {}

        # Staffelpreis-Tabelle
        for row in soup.select(".block-prices--table tr, table tr"):
            cells = row.select("td")
            if len(cells) >= 2:
                qty_text = cells[0].get_text(strip=True)
                price_text = cells[1].get_text(strip=True)
                qty_match = re.search(r"(\d+)", qty_text)
                price_match = re.search(r"([\d.,]+)\s*€?", price_text)
                if qty_match and price_match:
                    try:
                        qty = int(qty_match.group(1))
                        price = float(price_match.group(1).replace(".", "").replace(",", "."))
                        prices[qty] = price
                    except ValueError:
                        continue

        # Hauptpreis (falls keine Staffel)
        if not prices:
            price_elem = soup.select_one(".product--price .price--default, .product--price")
            if price_elem:
                price_text = price_elem.get_text(strip=True)
                price_match = re.search(r"([\d.,]+)\s*€?", price_text)
                if price_match:
                    try:
                        price = float(price_match.group(1).replace(".", "").replace(",", "."))
                        prices[1] = price
                    except ValueError:
                        pass

        return prices

    # ── Extraktion: Beschreibung ──────────────────────────────────
    @staticmethod
    def _extract_description(soup: BeautifulSoup) -> str:
        desc = soup.select_one(".product--description, #description .tab--content, .tab--content")
        if desc:
            return desc.get_text(separator=" ", strip=True)
        return ""

    # ── Extraktion: Breadcrumb ────────────────────────────────────
    @staticmethod
    def _extract_breadcrumb(soup: BeautifulSoup) -> str:
        crumbs = soup.select(".breadcrumb--list li a, .breadcrumb--link")
        parts = [c.get_text(strip=True) for c in crumbs if c.get_text(strip=True)]
        return " > ".join(parts)

    # ── Extraktion: VE-Informationen ──────────────────────────────
    @staticmethod
    def _extract_ve_info(name: str, description: str, invoice_desc: str = "") -> tuple:
        """
        Extrahiert VE-Menge und VE-Beschreibung aus verschiedenen Quellen.
        Gibt (ve_menge: int, ve_beschreibung: str) zurück.

        WICHTIG: Unterscheidung zwischen tatsächlicher VE (Gebinde) und
        Produktbeschreibung (z.B. "50er Packung" = 1 Einheit mit 50 Filtern):
        - "6 Stück im Thekendisplay" → VE=6 (6 einzelne Artikel im Display)
        - "50er Packung" → VE=1 (die Packung IST der Artikel)
        - "20x 25ml" → VE=20 (20 Einzelstücke à 25ml)
        - "Display 24" → VE=24 (24 Artikel im Display)
        """
        combined_text = f"{name} | {description} | {invoice_desc}"

        # ── Regel 1: "20x 25ml", "12x 100g" → VE = Multiplikator ──
        # Das ist immer eine echte VE (mehrere Einzelstücke)
        m = re.search(r"(\d+)\s*x\s*\d+\s*(?:ml|g|mg|cl|l)\b", combined_text, re.IGNORECASE)
        if m:
            ve = int(m.group(1))
            return ve, m.group(0).strip()

        # ── Regel 2: "N Stück im/pro Thekendisplay/Display" → echte VE ──
        m = re.search(r"(\d+)\s*Stück\s*(?:im|pro)\s*(?:Theken)?[Dd]isplay", combined_text)
        if m:
            ve = int(m.group(1))
            return ve, f"{ve} Stück im Display"

        # ── Regel 3: "Display N", "Display mit N" → echte VE ──
        m = re.search(r"Display\s*(?:mit\s*)?(\d+)", combined_text, re.IGNORECASE)
        if m:
            ve = int(m.group(1))
            return ve, f"Display {ve}"

        # ── Regel 4: "N Beutel/Stück pro Display" → echte VE ──
        m = re.search(r"(\d+)\s*(?:Beutel|Stück|Filter|Blatt)\s*pro\s*Display",
                       combined_text, re.IGNORECASE)
        if m:
            ve = int(m.group(1))
            return ve, m.group(0).strip()

        # ── Regel 5: "Ner Box" (z.B. "50er Box" = Gebinde mit N) ──
        # Box = Display-artig, echte VE
        m = re.search(r"(\d+)er\s*Box", combined_text, re.IGNORECASE)
        if m:
            ve = int(m.group(1))
            return ve, f"{ve}er Box"

        # ── Regel 6: "Ner Packung/Pack/Doypack/Beutel/Tüte" → KEINE VE ──
        # Das beschreibt das Produkt selbst (z.B. "50er Packung" = 1 Packung mit 50 Filtern)
        # Diese Pattern beschreiben den Inhalt des Produkts, nicht die Gebindegröße.
        _PRODUCT_UNIT_KEYWORDS = (
            r"Packung|Pack(?:ung)?|Pckg|Doypack|Beutel|Tüte|Bag|"
            r"Heft|Blättchen|Rolle|Röhrchen|Tube|Dose|Glas"
        )
        m = re.search(
            rf"(\d+)er\s*(?:{_PRODUCT_UNIT_KEYWORDS})",
            combined_text, re.IGNORECASE,
        )
        if m:
            inhalt = int(m.group(1))
            context = re.search(rf"\d+er\s*(\w+)", combined_text)
            desc = f"{inhalt}er {context.group(1)}" if context else f"{inhalt}er"
            # VE = 1, aber Inhalt als Beschreibung merken
            return 1, desc

        # ── Regel 7: Einfaches "Ner" ohne Kontext → Vorsicht! ──
        # Prüfe ob es ein Produkt-Attribut ist (Filter, Slim, etc.) oder eine echte VE
        m = re.search(r"(\d+)er\b", combined_text)
        if m:
            ve_candidate = int(m.group(1))
            # Kontextwort nach der Zahl prüfen
            context = re.search(r"\d+er\s*(\w+)", combined_text)
            context_word = context.group(1).lower() if context else ""

            # Diese Wörter deuten auf Produktbeschreibung hin → VE=1
            product_descriptors = {
                "packung", "pack", "pckg", "doypack", "beutel", "tüte", "bag",
                "heft", "blättchen", "rolle", "röhrchen", "tube", "dose", "glas",
                "filter", "slim", "aktivkohle", "aktivkohlefilter", "tips",
                "papers", "longpapers", "rolls", "cones", "blunts",
            }
            if context_word in product_descriptors:
                desc = f"{ve_candidate}er {context.group(1)}" if context else f"{ve_candidate}er"
                return 1, desc

            # Hohe Zahlen (≥ 20) ohne Display/Box-Kontext sind meist Produktbeschreibungen
            if ve_candidate >= 20:
                desc = f"{ve_candidate}er {context.group(1)}" if context else f"{ve_candidate}er"
                return 1, desc

            # Niedrige Zahlen (< 20) könnten echte VE sein (z.B. "6er" = 6 Stück)
            desc = f"{ve_candidate}er {context.group(1)}" if context else f"{ve_candidate}er"
            return ve_candidate, desc

        # ── Regel 8: "einzeln in Kartonschachtel" → VE=1 ──
        if re.search(r"einzeln\s+in", combined_text, re.IGNORECASE):
            return 1, "Einzelstück"

        # ── Regel 9: Mengeneinheit "Display" ohne Anzahl → Display erkannt ──
        # Wenn die Mengeneinheit "Display" enthält aber keine Zahl gefunden wurde,
        # markiere es als Display mit unbekannter Größe (ve_menge bleibt 1,
        # muss durch Knistermann-Scraping aufgelöst werden).
        if re.search(r"Mengeneinheit:\s*Display", combined_text, re.IGNORECASE):
            logger.warning(
                "Mengeneinheit 'Display' erkannt, aber VE-Größe nicht bestimmbar. "
                "Knistermann-Scraping benötigt für genaue VE-Bestimmung."
            )
            return 1, "Display (VE unbekannt)"

        # Standard: VE = 1 (Einzelartikel)
        return 1, "Einzelstück"

    # ── Hilfsmethoden ─────────────────────────────────────────────
    @staticmethod
    def _is_product_page(soup: BeautifulSoup) -> bool:
        """Prüft ob die Seite eine Produktdetailseite ist."""
        return bool(soup.select_one(".product--detail-upper, .product--details, h1.product--title"))


def _deduplicate_images(urls: List[str]) -> List[str]:
    """
    Entfernt Duplikate verschiedener Auflösungen.
    Behält pro Bild die größte verfügbare Auflösung.
    """
    if not urls:
        return []

    # Gruppiere nach Basis-Dateiname (ohne Auflösung)
    groups: Dict[str, List[str]] = {}
    resolution_pattern = re.compile(r"_(\d+x\d+)(@\dx)?\.(?:jpg|png|webp|jpeg)")

    for url in urls:
        # Basisname extrahieren
        base = resolution_pattern.sub("", url)
        if base not in groups:
            groups[base] = []
        groups[base].append(url)

    result = []
    for _base, variants in groups.items():
        # Sortiere nach Auflösung (absteigend) und nimm die größte
        def _get_resolution(u):
            m = resolution_pattern.search(u)
            if m:
                dims = m.group(1).split("x")
                return int(dims[0]) * int(dims[1])
            return 0

        variants.sort(key=_get_resolution, reverse=True)
        result.append(variants[0])

    return result


# ── CLI-Test ─────────────────────────────────────────────────────
if __name__ == "__main__":
    logging.basicConfig(level=logging.DEBUG, format="%(levelname)s | %(message)s")
    scraper = KnistermannScraper()

    if scraper.login():
        # Test mit einem bekannten Artikel
        info = scraper.scrape_product("CLU-003", "ScreenUrin - Clean Urin, Nachfüllpack 20x 25ml")
        print(f"\n{'='*60}")
        print(f"Artikel:   {info.artikelnummer}")
        print(f"Name:      {info.name}")
        print(f"VE:        {info.ve_menge} ({info.ve_beschreibung})")
        print(f"Status:    {info.lagerstatus} – {info.lagerstatus_text}")
        print(f"Bilder:    {len(info.bild_urls)}")
        for url in info.bild_urls[:3]:
            print(f"  → {url}")
        print(f"Preise:    {info.staffelpreise}")
        print(f"Kategorie: {info.kategorie}")
