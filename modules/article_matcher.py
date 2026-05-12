"""
Intelligentes Artikel-Matching für JTL Import.

Vergleicht neue Artikel aus der Rechnung mit bestehenden JTL-Artikeln.
Unterstützt:
- Exaktes Matching über Artikelnummer
- Fuzzy-Matching über Artikelnamen (ohne Farben)
- Farbenerkennung und -extraktion
- Varianten-Erkennung (gleicher Artikel in anderer Farbe)
"""

import logging
import re
import unicodedata
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import List, Optional, Tuple, Dict

import config

logger = logging.getLogger(__name__)

# Versuche fuzzywuzzy/thefuzz zu importieren, Fallback auf difflib
try:
    from thefuzz import fuzz, process as fuzz_process
    HAS_FUZZY = True
except ImportError:
    try:
        from fuzzywuzzy import fuzz, process as fuzz_process
        HAS_FUZZY = True
    except ImportError:
        HAS_FUZZY = False
        logger.info("thefuzz/fuzzywuzzy nicht installiert – verwende difflib als Fallback")


# ── Datenmodelle ──────────────────────────────────────────────────
@dataclass
class MatchResult:
    """Ergebnis eines Artikel-Matchings."""
    match_type: str = "none"         # "exact", "fuzzy", "color_variant", "none"
    confidence: float = 0.0          # 0–100 Prozent
    matched_item: object = None      # JTLItem (falls gefunden)
    matched_name: str = ""           # Name des gefundenen Artikels
    color_detected: str = ""         # Erkannte Farbe im neuen Artikel
    existing_colors: List[str] = field(default_factory=list)  # Farben in bestehenden Varianten
    message: str = ""                # Erklärung des Match-Ergebnisses


# ── Farberkennung ─────────────────────────────────────────────────
# Kompilierte Regex-Patterns für Farben
_COLOR_PATTERN = None


def _get_color_pattern() -> re.Pattern:
    """Erstellt ein kompiliertes Regex-Pattern für alle Farbkeywords."""
    global _COLOR_PATTERN
    if _COLOR_PATTERN is None:
        # Sortiere nach Länge (längste zuerst) um "Blau" vor "B" zu matchen
        sorted_colors = sorted(config.COLOR_KEYWORDS, key=len, reverse=True)
        escaped = [re.escape(c) for c in sorted_colors]
        pattern_str = r"\b(" + "|".join(escaped) + r")\b"
        _COLOR_PATTERN = re.compile(pattern_str, re.IGNORECASE)
    return _COLOR_PATTERN


def extract_colors(text: str) -> List[str]:
    """
    Extrahiert alle Farbkeywords aus einem Text.

    Args:
        text: Artikelname oder Beschreibung

    Returns:
        Liste der gefundenen Farben (lowercase).
    """
    pattern = _get_color_pattern()
    matches = pattern.findall(text)
    # Normalisiere und dedupliziere
    colors = list(dict.fromkeys(m.lower() for m in matches))
    return colors


def remove_colors(text: str) -> str:
    """
    Entfernt alle Farbkeywords aus einem Text.

    Args:
        text: Artikelname oder Beschreibung

    Returns:
        Text ohne Farben, bereinigt.
    """
    pattern = _get_color_pattern()
    cleaned = pattern.sub("", text)
    # Mehrfache Leerzeichen bereinigen
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    # Führende/trailing Kommas, Bindestriche etc. entfernen
    cleaned = re.sub(r"^[,\-/\s]+|[,\-/\s]+$", "", cleaned).strip()
    return cleaned


# ── Packungsgröße-Extraktion ───────────────────────────────────────
def extract_pack_size(text: str) -> Optional[int]:
    """
    Extrahiert die Packungsgröße aus einem Artikelnamen.

    Erkennt Muster wie "50er", "100er", "25er Packung", "250er".

    Args:
        text: Artikelname oder Beschreibung

    Returns:
        Packungsgröße als Integer, oder None wenn keine erkannt.
    """
    if not text:
        return None

    # Muster: "50er", "100er Packung", "250er Pack" etc.
    m = re.search(r"\b(\d+)er\b", text)
    if m:
        return int(m.group(1))
    return None


# ── Artikelname-Normalisierung ────────────────────────────────────
def normalize_article_name(name: str, remove_color: bool = True) -> str:
    """
    Normalisiert einen Artikelnamen für den Vergleich.

    Schritte:
    1. Farben entfernen (optional)
    2. Sonderzeichen entfernen (®, ™, ©)
    3. Umlaute normalisieren
    4. Kleinschreibung
    5. Mehrfache Leerzeichen bereinigen
    6. VE-Informationen entfernen (z.B. "20x 25ml", "500er")

    Args:
        name:          Artikelname
        remove_color:  Ob Farben entfernt werden sollen

    Returns:
        Normalisierter Name.
    """
    if not name:
        return ""

    result = name

    # Farben entfernen
    if remove_color:
        result = remove_colors(result)

    # Sonderzeichen entfernen
    result = re.sub(r"[®™©]", "", result)

    # VE-Informationen entfernen
    result = re.sub(r"\d+\s*x\s*\d+\s*(?:ml|g|mg|cl|l)\b", "", result, flags=re.IGNORECASE)
    result = re.sub(r"\d+er\b", "", result)
    result = re.sub(r"\bDisplay\s*\d*\b", "", result, flags=re.IGNORECASE)
    result = re.sub(r"\b\d+\s*(?:Stück|Stk\.?|pcs?)\b", "", result, flags=re.IGNORECASE)

    # Größen-Angaben entfernen (z.B. "0,5ml", "5,9mm")
    result = re.sub(r"\d+[.,]?\d*\s*(?:ml|mm|cm|g|mg|l)\b", "", result, flags=re.IGNORECASE)

    # Sonderzeichen → Leerzeichen
    result = re.sub(r"[/\-_,;:()[\]{}]", " ", result)

    # Kleinschreibung
    result = result.lower().strip()

    # Mehrfache Leerzeichen
    result = re.sub(r"\s+", " ", result).strip()

    return result


# ── Ähnlichkeitsberechnung ────────────────────────────────────────
def calculate_similarity(name1: str, name2: str) -> float:
    """
    Berechnet die Ähnlichkeit zwischen zwei normalisierten Artikelnamen.

    Verwendet thefuzz/fuzzywuzzy falls verfügbar, sonst difflib.

    Args:
        name1: Erster normalisierter Name
        name2: Zweiter normalisierter Name

    Returns:
        Ähnlichkeit in Prozent (0–100).
    """
    if not name1 or not name2:
        return 0.0

    if HAS_FUZZY:
        # Verschiedene Fuzzy-Matching-Strategien
        ratio = fuzz.ratio(name1, name2)
        partial = fuzz.partial_ratio(name1, name2)
        token_sort = fuzz.token_sort_ratio(name1, name2)
        token_set = fuzz.token_set_ratio(name1, name2)

        # Gewichteter Durchschnitt (token_set ist für Teilmengen am besten)
        score = max(
            ratio * 0.2 + token_sort * 0.3 + token_set * 0.5,
            partial * 0.3 + token_set * 0.7,
        )
        return round(score, 1)
    else:
        # Fallback: difflib SequenceMatcher
        ratio = SequenceMatcher(None, name1, name2).ratio() * 100

        # Token-basierter Vergleich
        tokens1 = set(name1.split())
        tokens2 = set(name2.split())
        if tokens1 and tokens2:
            intersection = tokens1 & tokens2
            union = tokens1 | tokens2
            jaccard = (len(intersection) / len(union)) * 100
            score = max(ratio, jaccard)
        else:
            score = ratio

        return round(score, 1)


# ── Artikel-Matcher ───────────────────────────────────────────────
class ArticleMatcher:
    """
    Intelligentes Artikel-Matching gegen JTL-Bestandsartikel.

    Findet zu einem neuen Artikel den passenden bestehenden JTL-Artikel
    und bestimmt ob es sich um ein Update, eine neue Farbvariante oder
    einen komplett neuen Artikel handelt.
    """

    def __init__(self, jtl_articles: list = None):
        """
        Args:
            jtl_articles: Liste von JTLItem-Objekten aus der JTL Wawi API
        """
        self.jtl_articles = jtl_articles or []
        self._name_index = {}    # normalisierter_name → [JTLItem, ...]
        self._number_index = {}  # artikelnummer → JTLItem

        if self.jtl_articles:
            self._build_index()

    def _build_index(self):
        """Erstellt Indizes für schnelles Matching."""
        for item in self.jtl_articles:
            # Artikelnummer-Index
            if item.article_number:
                self._number_index[item.article_number.upper()] = item
            if item.sku:
                self._number_index[item.sku.upper()] = item

            # Name-Index (normalisiert, ohne Farbe)
            norm_name = normalize_article_name(item.name, remove_color=True)
            if norm_name:
                if norm_name not in self._name_index:
                    self._name_index[norm_name] = []
                self._name_index[norm_name].append(item)

        logger.info(
            f"ArticleMatcher Index: {len(self._number_index)} Nummern, "
            f"{len(self._name_index)} normalisierte Namen"
        )

    def update_articles(self, jtl_articles: list):
        """Aktualisiert die Artikel-Liste und baut den Index neu auf."""
        self.jtl_articles = jtl_articles
        self._name_index.clear()
        self._number_index.clear()
        self._build_index()

    def find_match(
        self,
        artikelnummer: str,
        artikelname: str,
        threshold: float = None,
    ) -> MatchResult:
        """
        Sucht den besten Match für einen neuen Artikel.

        Strategie:
        1. Exaktes Matching über Artikelnummer
        2. Fuzzy-Matching über normalisierten Namen (ohne Farbe)
        3. Farbvarianten-Erkennung

        Args:
            artikelnummer: Lieferanten-Artikelnummer
            artikelname:   Artikelname aus der Rechnung
            threshold:     Minimum-Ähnlichkeit für Fuzzy-Match (default: config)

        Returns:
            MatchResult mit Typ, Confidence und gefundenem Artikel.
        """
        if threshold is None:
            threshold = config.FUZZY_MATCH_THRESHOLD

        # ── Schritt 1: Exaktes Nummer-Matching ───────────────────
        exact = self._match_by_number(artikelnummer)
        if exact:
            colors_new = extract_colors(artikelname)
            colors_existing = extract_colors(exact.name)
            return MatchResult(
                match_type="exact",
                confidence=100.0,
                matched_item=exact,
                matched_name=exact.name,
                color_detected=colors_new[0] if colors_new else "",
                existing_colors=colors_existing,
                message=f"Exakter Treffer über Artikelnummer '{artikelnummer}'",
            )

        # ── Schritt 2: Fuzzy Name-Matching (ohne Farbe) ──────────
        new_colors = extract_colors(artikelname)
        norm_new = normalize_article_name(artikelname, remove_color=True)

        if not norm_new:
            return MatchResult(
                match_type="none",
                message=f"Artikelname zu kurz nach Normalisierung: '{artikelname}'",
            )

        # Packungsgröße des neuen Artikels extrahieren
        new_pack_size = extract_pack_size(artikelname)

        # Sammle ALLE Kandidaten oberhalb eines Mindest-Schwellenwerts
        # (nicht nur den besten), um pack-size-aware Auswahl zu ermöglichen
        all_candidates: List[Tuple[float, str, list]] = []  # (score, norm_name, items)
        best_score = 0.0

        for norm_existing, items in self._name_index.items():
            score = calculate_similarity(norm_new, norm_existing)
            if score > best_score:
                best_score = score
            if score >= threshold:
                all_candidates.append((score, norm_existing, items))

        if all_candidates:
            # Sortiere: höchster Score zuerst
            all_candidates.sort(key=lambda x: x[0], reverse=True)
            best_score = all_candidates[0][0]

            # Sammle alle Items aus allen Kandidaten-Gruppen mit Score nahe dem besten
            # (innerhalb von 15 Punkten), um Packungsgrößen vergleichen zu können
            score_cutoff = best_score - 15.0
            best_items = []
            for score, norm_name, items in all_candidates:
                if score >= score_cutoff:
                    best_items.extend(items)
                else:
                    break

            # ── Packungsgrößen-Matching: bevorzuge gleiche Größe ──
            if new_pack_size is not None and len(best_items) > 1:
                def _pack_size_priority(item):
                    item_pack = extract_pack_size(item.name)
                    if item_pack == new_pack_size:
                        return 0  # Beste Priorität: gleiche Größe
                    elif item_pack is None:
                        return 1  # Mittel: keine Größe erkannt
                    else:
                        return 2  # Niedrig: andere Größe
                best_items = sorted(best_items, key=_pack_size_priority)
                logger.debug(
                    f"  Packungsgrößen-Matching: Neu={new_pack_size}er, "
                    f"Sortiert: {[(i.name, extract_pack_size(i.name)) for i in best_items[:3]]}"
                )

            # Prüfe ob es eine Farbvariante ist
            existing_colors = []
            for item in best_items:
                item_colors = extract_colors(item.name)
                existing_colors.extend(item_colors)
            existing_colors = list(dict.fromkeys(existing_colors))

            new_color = new_colors[0] if new_colors else ""

            if new_color and new_color.lower() in [c.lower() for c in existing_colors]:
                # Gleiche Farbe existiert bereits → Update
                # Finde den Artikel mit der gleichen Farbe UND passender Packungsgröße
                matched = best_items[0]
                candidates_same_color = []
                for item in best_items:
                    item_colors = [c.lower() for c in extract_colors(item.name)]
                    if new_color.lower() in item_colors:
                        candidates_same_color.append(item)

                if candidates_same_color:
                    # Bei mehreren gleichen Farben: bevorzuge gleiche Packungsgröße
                    matched = candidates_same_color[0]
                    if new_pack_size is not None and len(candidates_same_color) > 1:
                        for cand in candidates_same_color:
                            if extract_pack_size(cand.name) == new_pack_size:
                                matched = cand
                                break

                return MatchResult(
                    match_type="exact",
                    confidence=best_score,
                    matched_item=matched,
                    matched_name=matched.name,
                    color_detected=new_color,
                    existing_colors=existing_colors,
                    message=f"Fuzzy-Match ({best_score:.0f}%): gleiche Farbvariante gefunden",
                )

            elif new_color and existing_colors:
                # Andere Farbe → Farbvariante
                # Bevorzuge Variante mit gleicher Packungsgröße als Referenz
                ref_item = best_items[0]  # Bereits nach Packungsgröße sortiert
                return MatchResult(
                    match_type="color_variant",
                    confidence=best_score,
                    matched_item=ref_item,
                    matched_name=ref_item.name,
                    color_detected=new_color,
                    existing_colors=existing_colors,
                    message=(
                        f"Farbvariante ({best_score:.0f}%): "
                        f"Neue Farbe '{new_color}', existierend: {existing_colors}"
                    ),
                )

            else:
                # Kein Farb-Unterschied erkennbar → Fuzzy-Match
                return MatchResult(
                    match_type="fuzzy",
                    confidence=best_score,
                    matched_item=best_items[0],
                    matched_name=best_items[0].name,
                    color_detected=new_color,
                    existing_colors=existing_colors,
                    message=f"Fuzzy-Match ({best_score:.0f}%): Ähnlicher Artikel gefunden",
                )

        # ── Schritt 3: Kein Match gefunden ────────────────────────
        return MatchResult(
            match_type="none",
            confidence=best_score,
            color_detected=new_colors[0] if new_colors else "",
            message=f"Kein passender Artikel gefunden (beste Ähnlichkeit: {best_score:.0f}%)",
        )

    def _match_by_number(self, artikelnummer: str) -> object:
        """Sucht einen Artikel per exakter Artikelnummer."""
        if not artikelnummer:
            return None
        return self._number_index.get(artikelnummer.upper())

    def find_similar_articles(
        self,
        product_name: str,
        top_n: int = 5,
    ) -> List[Tuple[object, float, str]]:
        """
        Findet die ähnlichsten JTL-Artikel zu einem Produktnamen.

        Args:
            product_name: Produktname (aus der Rechnung)
            top_n:        Maximale Anzahl Ergebnisse

        Returns:
            Liste von (JTLItem, Ähnlichkeit, normalisierter_Name) Tupeln,
            sortiert nach Ähnlichkeit (absteigend).
        """
        norm_new = normalize_article_name(product_name, remove_color=True)
        if not norm_new:
            return []

        results = []
        for norm_existing, items in self._name_index.items():
            score = calculate_similarity(norm_new, norm_existing)
            if score > 30:  # Mindest-Ähnlichkeit
                for item in items:
                    results.append((item, score, norm_existing))

        # Sortiere nach Ähnlichkeit (absteigend)
        results.sort(key=lambda x: x[1], reverse=True)
        return results[:top_n]


# ── CLI-Test ──────────────────────────────────────────────────────
if __name__ == "__main__":
    logging.basicConfig(level=logging.DEBUG, format="%(levelname)s | %(message)s")

    # Test Farbenerkennung
    test_names = [
        "JAYSAFE Premium Joint Holder Case 127mm, Green",
        "Sanaleo Kartusche 0,5ml Boost für Sanaleo VaPen, Lemon",
        "Sanaleo Kartusche 0,5ml Boost für Sanaleo VaPen, Blue Dream",
        "Sanaleo Kartusche 0,5ml Classic für Sanaleo VaPen, Tropical",
        "PURIZE Aktivkohlefilter, XTRA Slim YELLOW",
        "PURIZE XTRA Slim Size Multicolor Aktivkohlefilter",
        "ScreenUrin - Clean Urin, Nachfüllpack 20x 25ml",
    ]

    print("=== Farbenerkennung ===")
    for name in test_names:
        colors = extract_colors(name)
        cleaned = remove_colors(name)
        normalized = normalize_article_name(name)
        print(f"  Original:     {name}")
        print(f"  Farben:       {colors}")
        print(f"  Ohne Farbe:   {cleaned}")
        print(f"  Normalisiert: {normalized}")
        print()

    print("=== Ähnlichkeitsvergleich ===")
    pairs = [
        ("Sanaleo Kartusche 0,5ml Boost für Sanaleo VaPen, Lemon",
         "Sanaleo Kartusche 0,5ml Boost für Sanaleo VaPen, Blue Dream"),
        ("PURIZE Aktivkohlefilter, XTRA Slim YELLOW",
         "PURIZE Aktivkohlefilter, XTRA Slim Size Multicolor"),
        ("JAYSAFE Premium Joint Holder Case 127mm",
         "OCB Rice King Size Slim Zigarettenpapier"),
    ]

    for name1, name2 in pairs:
        norm1 = normalize_article_name(name1)
        norm2 = normalize_article_name(name2)
        score = calculate_similarity(norm1, norm2)
        print(f"  '{norm1}' vs '{norm2}'")
        print(f"  Ähnlichkeit: {score:.1f}%")
        print()
