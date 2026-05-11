"""
Blackleaf.de Preischeck-Scraper.

Sucht Produkte auf blackleaf.de und extrahiert VK-Preise (brutto).
Wird für die Preiskalkulation (10 % günstiger als Blackleaf) verwendet.
"""

import logging
import re
import time
from dataclasses import dataclass
from typing import Optional
from urllib.parse import quote_plus

import requests
from bs4 import BeautifulSoup

import config

logger = logging.getLogger(__name__)


@dataclass
class BlackleafResult:
    """Ergebnis einer Blackleaf-Preissuche."""
    artikelname: str = ""
    preis_brutto: Optional[float] = None   # Brutto-VK in EUR
    produkt_url: str = ""
    gefunden: bool = False


class BlackleafScraper:
    """Scraper für blackleaf.de – öffentlicher Shop, kein Login nötig."""

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": config.USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "de-DE,de;q=0.9,en;q=0.5",
        })

    def search_product(self, query: str) -> BlackleafResult:
        """
        Sucht ein Produkt auf Blackleaf und gibt den VK-Preis zurück.
        Versucht verschiedene Suchbegriffe falls nötig.
        """
        result = BlackleafResult()
        time.sleep(config.REQUEST_DELAY)

        # Verschiedene Suchstrategien
        search_terms = _build_search_terms(query)

        for term in search_terms:
            try:
                found = self._search_and_extract(term)
                if found and found.gefunden:
                    return found
            except Exception as e:
                logger.debug(f"Suche fehlgeschlagen für '{term}': {e}")
                continue

        logger.info(f"⚠️  Blackleaf: Nichts gefunden für '{query}'")
        return result

    def _search_and_extract(self, query: str) -> Optional[BlackleafResult]:
        """Führt eine Suche durch und extrahiert den Preis des besten Treffers."""
        url = f"{config.BLACKLEAF_BASE_URL}/search?sSearch={quote_plus(query)}"
        logger.debug(f"Blackleaf Suche: {url}")

        resp = self.session.get(url, timeout=config.REQUEST_TIMEOUT)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")

        # Prüfe ob wir direkt auf eine Produktseite geleitet wurden
        if self._is_product_page(soup):
            return self._extract_from_product_page(soup, resp.url)

        # Suchergebnisse parsen
        products = soup.select(
            ".product--box, .product--info, "
            ".listing--container .product--box, "
            ".search--results .product--box"
        )

        if not products:
            # Alternativer Selektor für neuere Shopware-Themes
            products = soup.select("[data-product-box], .product-box")

        if not products:
            return None

        # Erstes Produkt nehmen
        product = products[0]

        # Preis extrahieren
        price_elem = product.select_one(
            ".product--price .price--default, "
            ".product--price, "
            ".price--unit"
        )
        if not price_elem:
            # Link zur Produktseite folgen
            link = product.select_one("a.product--title, a.product--image, a[href]")
            if link and link.get("href"):
                href = link["href"]
                if not href.startswith("http"):
                    href = config.BLACKLEAF_BASE_URL + href
                time.sleep(config.REQUEST_DELAY)
                resp2 = self.session.get(href, timeout=config.REQUEST_TIMEOUT)
                soup2 = BeautifulSoup(resp2.text, "html.parser")
                return self._extract_from_product_page(soup2, href)
            return None

        result = BlackleafResult()
        result.gefunden = True

        # Preis parsen
        price_text = price_elem.get_text(strip=True)
        result.preis_brutto = _parse_price(price_text)

        # Name
        name_elem = product.select_one(".product--title, a.product--title")
        result.artikelname = name_elem.get_text(strip=True) if name_elem else ""

        # URL
        link = product.select_one("a.product--title, a.product--image, a[href]")
        if link and link.get("href"):
            href = link["href"]
            result.produkt_url = href if href.startswith("http") else config.BLACKLEAF_BASE_URL + href

        return result

    def _extract_from_product_page(self, soup: BeautifulSoup, url: str) -> BlackleafResult:
        """Extrahiert Preis von einer Produktdetailseite."""
        result = BlackleafResult()
        result.produkt_url = url

        # Name
        h1 = soup.select_one("h1.product--title, h1")
        result.artikelname = h1.get_text(strip=True) if h1 else ""

        # Preis
        price_elem = soup.select_one(
            ".product--price .price--default, "
            ".product--price .price--content, "
            ".product--price, "
            "meta[itemprop='price']"
        )

        if price_elem:
            if price_elem.name == "meta":
                try:
                    result.preis_brutto = float(price_elem["content"])
                except (ValueError, KeyError):
                    pass
            else:
                price_text = price_elem.get_text(strip=True)
                result.preis_brutto = _parse_price(price_text)

        result.gefunden = result.preis_brutto is not None
        return result

    @staticmethod
    def _is_product_page(soup: BeautifulSoup) -> bool:
        return bool(soup.select_one(
            ".product--detail-upper, .product--details, "
            "h1.product--title, [itemprop='product']"
        ))


# ── Hilfsfunktionen ──────────────────────────────────────────────
def _parse_price(text: str) -> Optional[float]:
    """Parst einen Preis-String zu float. Erwartet Euro-Format."""
    text = text.replace("€", "").replace("*", "").replace("ab", "").strip()
    # "1.234,56" → 1234.56
    m = re.search(r"([\d.]+,\d{2})", text)
    if m:
        price_str = m.group(1).replace(".", "").replace(",", ".")
        try:
            return float(price_str)
        except ValueError:
            return None
    # Fallback: "12.99"
    m = re.search(r"(\d+\.\d{2})", text)
    if m:
        try:
            return float(m.group(1))
        except ValueError:
            return None
    return None


def _build_search_terms(query: str) -> list:
    """
    Erzeugt mehrere Suchbegriffe aus einem Rechnungs-Beschreibungstext.
    Strategie: vom Spezifischen zum Allgemeinen.
    """
    terms = []

    # 1. Originaltext (bereinigt)
    clean = re.sub(r"[®™©]", "", query)
    clean = re.sub(r"\s+", " ", clean).strip()
    terms.append(clean)

    # 2. Markenname + Schlüsselwort (erste 3 Wörter)
    words = clean.split()
    if len(words) > 3:
        terms.append(" ".join(words[:3]))

    # 3. Nur Markenname (erstes Wort, wenn es groß geschrieben ist)
    if words and words[0][0].isupper():
        # Marke + Produkttyp
        brand = words[0]
        for keyword in ["Aktivkohlefilter", "Filter", "Kartusche", "Drehtablett",
                        "Joint Holder", "Joint Hülle", "Urin", "Zigarettenpapier",
                        "Teerblocker", "Pre-Roll"]:
            if keyword.lower() in clean.lower():
                terms.append(f"{brand} {keyword}")
                break

    return terms


# ── CLI-Test ─────────────────────────────────────────────────────
if __name__ == "__main__":
    logging.basicConfig(level=logging.DEBUG, format="%(levelname)s | %(message)s")
    scraper = BlackleafScraper()

    test_queries = [
        "JAYSAFE Premium Joint Holder Case 127mm",
        "ScreenUrin Clean Urin Nachfüllpack 20x 25ml",
        "PURIZE XTRA Slim Size Multicolor Aktivkohlefilter 500er",
    ]

    for q in test_queries:
        result = scraper.search_product(q)
        status = "✅" if result.gefunden else "❌"
        price = f"{result.preis_brutto:.2f} €" if result.preis_brutto else "—"
        print(f"{status} {q[:50]:50s} → {price}")
