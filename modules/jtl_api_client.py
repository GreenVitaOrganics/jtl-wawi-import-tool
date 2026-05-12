"""
JTL Wawi REST API Client.

Stellt die Verbindung zur JTL Wawi REST API her und bietet Methoden
zum Lesen, Erstellen und Aktualisieren von Artikeln.

Authentifizierung: Bearer Token (Base64-kodierter API-Key)
Endpoint-Basis: https://<IP>:<PORT>/rest/eazybusiness/v1/
"""

import base64
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

import config

logger = logging.getLogger(__name__)

# SSL-Warnungen unterdrücken bei Self-Signed Certs
if not config.JTL_VERIFY_SSL:
    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


# ── Datenmodelle ──────────────────────────────────────────────────
@dataclass
class JTLItem:
    """Repräsentiert einen Artikel aus JTL Wawi."""
    item_id: int = 0
    article_number: str = ""        # Interne Artikelnummer in JTL
    name: str = ""
    sku: str = ""                   # Lieferanten-SKU
    ek_netto: float = 0.0
    vk_brutto: float = 0.0
    vk_netto: float = 0.0
    stock: int = 0
    category: str = ""
    description: str = ""
    tax_rate: float = 19.0
    is_parent: bool = False
    parent_id: int = 0
    variations: List[Dict] = field(default_factory=list)
    images: List[str] = field(default_factory=list)
    custom_fields: Dict[str, Any] = field(default_factory=dict)
    raw_data: Dict[str, Any] = field(default_factory=dict)


class JTLApiError(Exception):
    """Fehler bei der JTL API-Kommunikation."""
    def __init__(self, message: str, status_code: int = 0, response_body: str = ""):
        super().__init__(message)
        self.status_code = status_code
        self.response_body = response_body


class JTLApiClient:
    """
    Client für die JTL Wawi REST API (v1).

    Verbindet sich über HTTPS mit dem JTL Wawi POS-Server und
    authentifiziert per Bearer Token (Base64-kodierter API-Key).
    """

    def __init__(
        self,
        api_url: str = None,
        api_key: str = None,
        verify_ssl: bool = None,
        timeout: int = None,
    ):
        self.api_url = (api_url or config.JTL_API_URL).rstrip("/")
        self.api_key = api_key or config.JTL_API_KEY
        self.verify_ssl = verify_ssl if verify_ssl is not None else config.JTL_VERIFY_SSL
        self.timeout = timeout or config.JTL_API_TIMEOUT

        # Bearer Token erstellen (Base64-kodierter API-Key)
        self._bearer_token = base64.b64encode(
            self.api_key.encode("utf-8")
        ).decode("utf-8")

        # Session mit Retry-Logik
        self.session = self._create_session()
        self._connected = False

    def _create_session(self) -> requests.Session:
        """Erstellt eine Session mit Retry-Logik und Auth-Headers."""
        session = requests.Session()
        session.headers.update({
            "Authorization": f"Bearer {self._bearer_token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        })
        session.verify = self.verify_ssl

        # Retry-Strategie
        retry_strategy = Retry(
            total=config.JTL_API_MAX_RETRIES,
            backoff_factor=config.JTL_API_RETRY_DELAY,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
        )
        adapter = HTTPAdapter(max_retries=retry_strategy)
        session.mount("https://", adapter)
        session.mount("http://", adapter)

        return session

    @property
    def base_url(self) -> str:
        """Basis-URL für API-Endpunkte."""
        return f"{self.api_url}/rest/eazybusiness/v1"

    # ── Verbindungstest ───────────────────────────────────────────
    def test_connection(self) -> bool:
        """
        Testet die Verbindung zur JTL Wawi API.
        Gibt True zurück wenn die Verbindung erfolgreich ist.
        """
        try:
            logger.info(f"Teste Verbindung zu {self.api_url}...")
            # Customer-Endpoint ist stabil und gibt immer 200 zurück
            resp = self.session.get(
                f"{self.base_url}/Customer",
                params={"PageSize": 1, "PageNumber": 1},
                timeout=self.timeout,
            )
            if resp.status_code == 200:
                self._connected = True
                try:
                    data = resp.json()
                    total = data.get("TotalItems", "?")
                    logger.info(f"✅ JTL API Verbindung erfolgreich ({total} Kunden in DB)")
                except Exception:
                    logger.info("✅ JTL API Verbindung erfolgreich")
                return True
            elif resp.status_code in (401, 403):
                logger.error(f"❌ JTL API Authentifizierung fehlgeschlagen (HTTP {resp.status_code})")
                return False
            else:
                logger.warning(f"⚠️  JTL API antwortet mit HTTP {resp.status_code}")
                # Könnte trotzdem funktionieren
                self._connected = True
                return True

        except requests.ConnectionError as e:
            logger.error(f"❌ Verbindung zu {self.api_url} fehlgeschlagen: {e}")
            return False
        except requests.Timeout:
            logger.error(f"❌ Timeout bei Verbindung zu {self.api_url}")
            return False
        except Exception as e:
            logger.error(f"❌ Unerwarteter Fehler: {e}")
            return False

    # ── API-Request Hilfsmethode ──────────────────────────────────
    def _request(
        self,
        method: str,
        endpoint: str,
        params: Dict = None,
        data: Dict = None,
        retries: int = 0,
    ) -> Optional[Dict]:
        """
        Führt einen API-Request durch mit Fehlerbehandlung.

        Args:
            method:   HTTP-Methode (GET, POST, PUT, PATCH, DELETE)
            endpoint: API-Endpunkt (ohne Basis-URL)
            params:   Query-Parameter
            data:     Request-Body (wird als JSON gesendet)
            retries:  Aktuelle Retry-Zählung

        Returns:
            JSON-Response als Dict oder None bei Fehler.
        """
        url = f"{self.base_url}/{endpoint.lstrip('/')}"

        try:
            logger.debug(f"API {method} {url} | params={params} | body={json.dumps(data)[:200] if data else None}")

            resp = self.session.request(
                method=method,
                url=url,
                params=params,
                json=data,
                timeout=self.timeout,
            )

            # Erfolgreiche Antworten
            if resp.status_code in (200, 201):
                try:
                    return resp.json()
                except json.JSONDecodeError:
                    return {"status": "ok", "raw": resp.text}

            # 204 No Content (z.B. nach erfolgreichem Update)
            if resp.status_code == 204:
                return {"status": "ok", "message": "No Content"}

            # 404 Not Found
            if resp.status_code == 404:
                logger.debug(f"API 404: {endpoint}")
                return None

            # Authentifizierungsfehler
            if resp.status_code in (401, 403):
                raise JTLApiError(
                    f"Authentifizierung fehlgeschlagen (HTTP {resp.status_code})",
                    status_code=resp.status_code,
                    response_body=resp.text,
                )

            # Andere Fehler mit Retry
            if retries < config.JTL_API_MAX_RETRIES:
                logger.warning(
                    f"API HTTP {resp.status_code} – Retry {retries + 1}/{config.JTL_API_MAX_RETRIES}"
                )
                time.sleep(config.JTL_API_RETRY_DELAY * (retries + 1))
                return self._request(method, endpoint, params, data, retries + 1)

            raise JTLApiError(
                f"API-Fehler: HTTP {resp.status_code}",
                status_code=resp.status_code,
                response_body=resp.text[:500],
            )

        except requests.ConnectionError as e:
            if retries < config.JTL_API_MAX_RETRIES:
                logger.warning(f"Verbindungsfehler – Retry {retries + 1}: {e}")
                time.sleep(config.JTL_API_RETRY_DELAY * (retries + 1))
                return self._request(method, endpoint, params, data, retries + 1)
            raise JTLApiError(f"Verbindung fehlgeschlagen nach {retries} Versuchen: {e}")

        except requests.Timeout:
            if retries < config.JTL_API_MAX_RETRIES:
                logger.warning(f"Timeout – Retry {retries + 1}")
                time.sleep(config.JTL_API_RETRY_DELAY * (retries + 1))
                return self._request(method, endpoint, params, data, retries + 1)
            raise JTLApiError(f"Timeout nach {retries} Versuchen")

    # ── Artikel abrufen ───────────────────────────────────────────
    def get_articles(
        self,
        page: int = 1,
        page_size: int = 100,
        search: str = None,
    ) -> List[JTLItem]:
        """
        Ruft eine Seite von Artikeln ab.

        JTL API erfordert SearchKeyWord oder CategoryKey.

        Args:
            page:      Seitennummer (1-basiert)
            page_size: Anzahl Artikel pro Seite (max. 100)
            search:    Optionaler Suchbegriff (wird als SearchKeyWord verwendet)

        Returns:
            Liste von JTLItem-Objekten.
        """
        params = {
            "PageNumber": page,
            "PageSize": min(page_size, 100),
        }
        if search:
            params["SearchKeyWord"] = search

        result = self._request("GET", "items", params=params)
        if not result:
            return []

        items = []
        # JTL API gibt ein Paging-Objekt zurück
        item_list = (
            result.get("Items", []) or
            result.get("items", []) or
            result.get("data", []) or
            (result if isinstance(result, list) else [])
        )
        if isinstance(item_list, list):
            for item_data in item_list:
                items.append(self._parse_item(item_data))

        logger.debug(f"get_articles: {len(items)} Artikel auf Seite {page}")
        return items

    def get_all_articles(self, search: str = None) -> List[JTLItem]:
        """
        Ruft ALLE Artikel ab.

        Wenn ein Suchbegriff angegeben wird, wird SearchKeyWord verwendet.
        Ohne Suchbegriff werden alle Kategorien durchiteriert, da die JTL API
        SearchKeyWord mit mindestens 3 Zeichen erfordert.

        Args:
            search: Optionaler Suchbegriff (mind. 3 Zeichen)

        Returns:
            Komplette Liste aller JTLItem-Objekte.
        """
        if search and len(search) >= 3:
            return self._get_articles_by_search(search)
        return self._get_all_articles_by_category()

    def _get_articles_by_search(self, search: str) -> List[JTLItem]:
        """Lädt Artikel über SearchKeyWord (paginiert)."""
        all_items = []
        page = 1
        page_size = 100

        while True:
            items = self.get_articles(page=page, page_size=page_size, search=search)
            if not items:
                break
            all_items.extend(items)
            logger.info(f"  Seite {page}: {len(items)} Artikel geladen (gesamt: {len(all_items)})")
            if len(items) < page_size:
                break
            page += 1
            time.sleep(0.3)

        return all_items

    def _get_all_articles_by_category(self) -> List[JTLItem]:
        """
        Lädt ALLE Artikel über Kategorien-Iteration.
        JTL API erfordert SearchKeyWord (≥3 Zeichen) oder CategoryKey.
        """
        all_items = []
        seen_keys = set()

        # Erst alle Kategorien laden
        logger.info("  Lade Kategorien...")
        categories = self._get_categories()
        logger.info(f"  {len(categories)} Kategorien gefunden")

        for i, cat in enumerate(categories):
            cat_key = cat.get("CategoryKey")
            cat_name = cat.get("Name", "?")
            if not cat_key:
                continue

            page = 1
            page_size = 100
            cat_count = 0

            while True:
                params = {
                    "CategoryKey": cat_key,
                    "PageNumber": page,
                    "PageSize": page_size,
                }
                result = self._request("GET", "items", params=params)
                if not result:
                    break

                item_list = result.get("Items", [])
                if not item_list:
                    break

                for item_data in item_list:
                    item = self._parse_item(item_data)
                    # Deduplizierung über ItemKey
                    if item.item_id and item.item_id not in seen_keys:
                        seen_keys.add(item.item_id)
                        all_items.append(item)
                        cat_count += 1

                total = result.get("TotalItems", 0)
                if len(item_list) < page_size or page * page_size >= total:
                    break
                page += 1
                time.sleep(0.2)

            if cat_count > 0:
                logger.debug(f"  Kategorie '{cat_name}' ({cat_key}): {cat_count} Artikel")

        logger.info(f"  Gesamt: {len(all_items)} eindeutige Artikel geladen")
        return all_items

    def _get_categories(self) -> List[Dict]:
        """Lädt alle Kategorien aus der JTL API."""
        all_cats = []
        page = 1
        page_size = 100

        while True:
            result = self._request("GET", "categories", params={
                "PageNumber": page, "PageSize": page_size
            })
            if not result:
                break
            cats = result.get("Items", [])
            if not cats:
                break
            all_cats.extend(cats)
            total = result.get("TotalItems", 0)
            if len(cats) < page_size or page * page_size >= total:
                break
            page += 1

        return all_cats

    def get_article_by_id(self, item_id: int) -> Optional[JTLItem]:
        """Ruft einen einzelnen Artikel per ID ab."""
        result = self._request("GET", f"items/{item_id}")
        if result:
            return self._parse_item(result)
        return None

    def get_article_by_number(self, article_number: str) -> Optional[JTLItem]:
        """
        Sucht einen Artikel nach seiner Artikelnummer.

        Args:
            article_number: Die zu suchende Artikelnummer (z.B. "CLU-003")

        Returns:
            JTLItem wenn gefunden, sonst None.
        """
        # Direkte Suche über SearchKeyWord
        items = self.get_articles(search=article_number, page_size=10)
        for item in items:
            if (item.article_number.upper() == article_number.upper() or
                    item.sku.upper() == article_number.upper()):
                return item

        logger.debug(f"Artikel nicht gefunden per Nummer: {article_number}")
        return None

    def search_articles(self, query: str, max_results: int = 20) -> List[JTLItem]:
        """
        Sucht Artikel nach einem Suchbegriff (Name, Nummer, etc.).

        Args:
            query:       Suchbegriff
            max_results: Maximale Anzahl Ergebnisse

        Returns:
            Liste von JTLItem-Objekten.
        """
        return self.get_articles(search=query, page_size=min(max_results, 100))

    # ── Artikel erstellen ─────────────────────────────────────────
    def create_article(self, article_data: Dict) -> Optional[JTLItem]:
        """
        Erstellt einen neuen Artikel in JTL Wawi.

        HINWEIS: JTL Wawi REST API v1 unterstützt kein POST /items.
        Neue Artikel werden in die CSV für JTL-Ameise geschrieben.
        Diese Methode loggt eine Warnung und gibt None zurück.

        Returns:
            Immer None (Artikel wird per CSV erstellt).
        """
        sku = article_data.get('Sku', article_data.get('ArticleNumber', '?'))
        logger.warning(
            f"⚠️  Artikel '{sku}' kann nicht per API erstellt werden "
            f"(JTL REST API v1 unterstützt kein POST). → CSV-Import nötig."
        )
        return None

    # ── Artikel-Preise aktualisieren (über /items/{id}/prices) ─────
    def update_article(self, item_id: int, update_data: Dict) -> bool:
        """
        Aktualisiert die Verkaufspreise eines bestehenden Artikels.

        Nutzt den PUT /items/{id}/prices Endpoint, da PUT /items/{id}
        in JTL Wawi REST API v1 einen Serialisierungsfehler hat und
        POST/PATCH nicht unterstützt werden.

        HINWEIS: Nur VK-Preise können über die API geändert werden.
        EK-Preise werden in die CSV für JTL-Ameise geschrieben.

        Args:
            item_id:     JTL-interne Artikel-ID
            update_data: Dictionary mit mindestens 'SalesPrice' oder 'GrossPrice'

        Returns:
            True bei Erfolg, False bei Fehler.
        """
        logger.info(f"Aktualisiere Preise für Artikel ID={item_id}")

        # Erst aktuelle Preise lesen
        try:
            current_prices = self._request("GET", f"items/{item_id}/prices")
            if current_prices is None:
                logger.warning(f"Keine Preise für Artikel {item_id} gefunden")
                current_prices = []
        except JTLApiError:
            current_prices = []

        # Neuen VK-Preis aus update_data extrahieren
        new_vk_netto = update_data.get("SalesPrice")
        new_vk_brutto = update_data.get("GrossPrice")

        if new_vk_netto is None and new_vk_brutto is not None:
            new_vk_netto = round(new_vk_brutto / 1.19, 7)
        elif new_vk_netto is not None and new_vk_brutto is None:
            new_vk_brutto = round(new_vk_netto * 1.19, 2)

        if new_vk_netto is None:
            logger.warning(f"Kein VK-Preis in update_data für Artikel {item_id}")
            return False

        # Preis-Array bauen: bestehende Preise aktualisieren oder neuen anlegen
        price_array = []
        vk_updated = False

        for price in current_prices:
            if price.get("SalesPlattform") is None and price.get("GrossNetTyp") == "net":
                # Standard VK (netto) → aktualisieren
                price["Value"] = round(new_vk_netto, 7)
                vk_updated = True
            price_array.append(price)

        if not vk_updated:
            # Kein VK-Eintrag vorhanden → neuen anlegen
            price_array.append({
                "Value": round(new_vk_netto, 7),
                "SalesPlattform": None,
                "Assignment": None,
                "GrossNetTyp": "net",
                "Currency": "EUR",
                "TaxRateDefault": 19.0,
                "TaxRateKey": 1,
                "FromQuantity": 0,
                "Percent": 0.0,
            })

        # PUT /items/{id}/prices
        try:
            result = self._request("PUT", f"items/{item_id}/prices", data=price_array)
            if result is not None:
                logger.info(
                    f"✅ Artikel {item_id} VK aktualisiert: "
                    f"netto={new_vk_netto:.4f}€, brutto={new_vk_brutto:.2f}€"
                )
                return True
        except JTLApiError as e:
            logger.error(f"❌ Preise für Artikel {item_id} konnten nicht aktualisiert werden: {e}")

        return False

    # ── Artikel-Daten für API vorbereiten ─────────────────────────
    @staticmethod
    def build_article_payload(
        artikelnummer: str,
        name: str,
        ek_netto: float,
        vk_brutto: float,
        beschreibung: str = "",
        mwst_satz: float = 19.0,
        kategorie: str = "",
        bild_urls: List[str] = None,
        lagermenge: int = None,
        ve_info: str = "",
        export_hinweis: str = None,
    ) -> Dict:
        """
        Erstellt ein Artikel-Payload-Dictionary für die JTL API.

        Konvertiert die internen Artikeldaten in das von der
        JTL Wawi REST API erwartete Format.
        """
        # VK netto berechnen
        vk_netto = round(vk_brutto / (1 + mwst_satz / 100), 4)

        payload = {
            "Sku": artikelnummer,
            "ArticleNumber": artikelnummer,
            "Name": name,
            "PurchasePrice": round(ek_netto, 4),
            "SalesPrice": round(vk_netto, 4),
            "GrossPrice": round(vk_brutto, 2),
            "TaxClassId": 1 if mwst_satz == 19.0 else 2,
            "TaxRate": mwst_satz,
            "IsActive": True,
        }

        if beschreibung:
            payload["ShortDescription"] = beschreibung
        if kategorie:
            payload["CategoryPath"] = kategorie
        if lagermenge is not None:
            payload["Stock"] = lagermenge
        if ve_info:
            payload["PackingUnit"] = ve_info
        if export_hinweis:
            payload["InternalNote"] = export_hinweis

        # Bilder
        if bild_urls:
            payload["Images"] = [
                {"Url": url, "Position": i + 1}
                for i, url in enumerate(bild_urls[:10])
            ]

        return payload

    # ── Internes Parsing ──────────────────────────────────────────
    def _parse_item(self, data: Dict) -> JTLItem:
        """
        Parst ein API-Response-Dictionary in ein JTLItem-Objekt.

        JTL API Felder (tatsächlich beobachtet):
        - ItemKey: int (interne ID)
        - SKU: str (Artikelnummer)
        - ItemName: str (Artikelname)
        - StockAvailable: float (Lagerbestand)
        - IsSimpleVariation, IsVariationCombinationParent: bool
        - SalesPrice: {SalesPriceNet, SalesPriceGross, TaxRate, TaxClass, Discount}
        """
        if not isinstance(data, dict):
            return JTLItem()

        # SalesPrice-Objekt auslesen
        sales_price = data.get("SalesPrice", {}) or {}
        if not isinstance(sales_price, dict):
            sales_price = {}

        item = JTLItem(
            item_id=data.get("ItemKey", 0) or data.get("Id", 0) or data.get("id", 0),
            article_number=str(data.get("SKU", "") or data.get("Sku", "") or data.get("ArticleNumber", "")),
            name=data.get("ItemName", "") or data.get("Name", "") or data.get("name", ""),
            sku=str(data.get("SKU", "") or data.get("Sku", "")),
            ek_netto=float(data.get("PurchasePrice", 0) or data.get("purchasePrice", 0) or 0),
            vk_netto=float(sales_price.get("SalesPriceNet", 0) or 0),
            vk_brutto=float(sales_price.get("SalesPriceGross", 0) or 0),
            stock=int(float(data.get("StockAvailable", 0) or data.get("Stock", 0) or 0)),
            category=data.get("CategoryPath", "") or data.get("categoryPath", "") or "",
            description=data.get("ShortDescription", "") or data.get("Description", "") or "",
            tax_rate=float(sales_price.get("TaxRate", 19.0) or 19.0),
            is_parent=bool(data.get("IsVariationCombinationParent", False)),
            parent_id=int(data.get("ParentId", 0) or data.get("parentId", 0) or 0),
            raw_data=data,
        )

        # Falls VK brutto = 0, aus netto berechnen
        if item.vk_brutto == 0 and item.vk_netto > 0:
            item.vk_brutto = round(item.vk_netto * (1 + item.tax_rate / 100), 2)

        # Variationen
        variations = data.get("Variations", []) or data.get("variations", []) or []
        if isinstance(variations, list):
            item.variations = variations

        # Bilder
        images = data.get("Images", []) or data.get("images", []) or []
        if isinstance(images, list):
            for img in images:
                if isinstance(img, dict):
                    url = img.get("Url") or img.get("url") or img.get("Path") or ""
                    if url:
                        item.images.append(url)
                elif isinstance(img, str):
                    item.images.append(img)

        return item

    # ── Swagger/Endpoints erkunden ────────────────────────────────
    def get_swagger_info(self) -> Optional[Dict]:
        """
        Versucht die Swagger-Dokumentation abzurufen,
        um verfügbare Endpoints zu entdecken.
        """
        swagger_urls = [
            f"{self.api_url}/rest/v1/swagger",
            f"{self.api_url}/rest/swagger/ui/index",
            f"{self.api_url}/rest/eazybusiness/v1/swagger",
        ]

        for url in swagger_urls:
            try:
                resp = self.session.get(url, timeout=self.timeout)
                if resp.status_code == 200:
                    try:
                        return resp.json()
                    except json.JSONDecodeError:
                        logger.debug(f"Swagger unter {url} gefunden (HTML)")
                        return {"url": url, "type": "html"}
            except Exception:
                continue

        logger.debug("Keine Swagger-Dokumentation gefunden")
        return None

    # ── Kontext-Manager ───────────────────────────────────────────
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.session.close()

    def __repr__(self):
        return f"JTLApiClient(url={self.api_url}, connected={self._connected})"


# ── CLI-Test ──────────────────────────────────────────────────────
if __name__ == "__main__":
    logging.basicConfig(level=logging.DEBUG, format="%(levelname)s | %(name)s | %(message)s")

    client = JTLApiClient()
    print(f"API Client: {client}")
    print(f"Base URL: {client.base_url}")
    print(f"Bearer Token: {client._bearer_token[:20]}...")

    # Verbindungstest
    connected = client.test_connection()
    print(f"\nVerbindung: {'✅ OK' if connected else '❌ Fehlgeschlagen'}")

    if connected:
        # Erste Artikel abrufen
        articles = client.get_articles(page_size=5)
        print(f"\n{len(articles)} Artikel geladen:")
        for art in articles:
            print(f"  [{art.item_id}] {art.article_number:20s} {art.name[:50]:50s} "
                  f"EK={art.ek_netto:.2f} VK={art.vk_brutto:.2f}")
