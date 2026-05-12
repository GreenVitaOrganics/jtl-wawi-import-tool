#!/usr/bin/env python3
"""
Test-Script für Bug 1 (VE-Division) und Bug 2 (Packungsgrößen-Matching).
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import logging
logging.basicConfig(level=logging.DEBUG, format="%(levelname)s | %(name)s | %(message)s")

from modules.knistermann_scraper import KnistermannScraper
from modules.article_matcher import ArticleMatcher, extract_pack_size, extract_colors, normalize_article_name
from modules.price_calculator import calculate_prices, get_price_strategy

print("=" * 70)
print("TEST 1: VE-Division für JAYSAFE")
print("=" * 70)

# Szenario A: Scraper hat Daten (Normalfall)
# Die Beschreibung enthält "6 Stück im Thekendisplay"
ve, desc = KnistermannScraper._extract_ve_info(
    "JAYSAFE® Premium Joint Holder Case",  # name
    "einzeln in Kartonschachtel\n6 Stück im Thekendisplay",  # description (from Knistermann)
    "JAYSAFE® Premium Joint Holder Case/ Joint Hülle, 127mm, Green",  # invoice_desc
)
print(f"Szenario A (Scraper OK): VE={ve}, Beschreibung='{desc}'")
assert ve == 6, f"FAIL: Erwartet VE=6, bekommen VE={ve}"
print("  ✅ VE=6 korrekt erkannt")

# Preisberechnung mit VE=6
prices = calculate_prices(ek_ve_preis=26.40, ve_menge=6, blackleaf_preis=None)
print(f"  EK/VE=26.40€, VE=6 → EK/Stück={prices.ek_einzelpreis:.2f}€")
assert abs(prices.ek_einzelpreis - 4.40) < 0.01, f"FAIL: Erwartet 4.40€, bekommen {prices.ek_einzelpreis:.2f}€"
print("  ✅ EK/Stück=4.40€ korrekt")

# Szenario B: Scraper ausgefallen, aber Mengeneinheit "Display" vorhanden
# In main.py wird jetzt: f"{item.beschreibung} | Mengeneinheit: {item.mengeneinheit}"
ve2, desc2 = KnistermannScraper._extract_ve_info(
    "JAYSAFE® Premium Joint Holder Case/ Joint Hülle, 127mm, Green | Mengeneinheit: Display",  # name with mengeneinheit
    "",  # empty description (scraper down)
    "JAYSAFE® Premium Joint Holder Case/ Joint Hülle, 127mm, Green",  # invoice_desc
)
print(f"\nSzenario B (Scraper down): VE={ve2}, Beschreibung='{desc2}'")
print(f"  ℹ️  Mengeneinheit 'Display' erkannt, VE-Größe nicht bestimmbar ohne Scraper")

# Szenario C: Scraper down, aber Beschreibungstext enthält Display-Info
ve3, desc3 = KnistermannScraper._extract_ve_info(
    "JAYSAFE® Premium Joint Holder Case/ Joint Hülle, 127mm, Green | Mengeneinheit: Display",
    "6 Stück im Thekendisplay",  # description from cached/available data
    "JAYSAFE® Premium Joint Holder Case/ Joint Hülle, 127mm, Green",
)
print(f"\nSzenario C (Beschreibung mit VE-Info): VE={ve3}, Beschreibung='{desc3}'")
assert ve3 == 6, f"FAIL: Erwartet VE=6, bekommen VE={ve3}"
print("  ✅ VE=6 korrekt erkannt aus Beschreibungstext")

# ScreenUrin: "20x 25ml" in Beschreibung
ve4, desc4 = KnistermannScraper._extract_ve_info(
    "ScreenUrin - Clean Urin, Nachfüllpack 20x 25ml | Mengeneinheit: Display",
    "",
    "ScreenUrin - Clean Urin, Nachfüllpack 20x 25ml",
)
print(f"\nScreenUrin: VE={ve4}, Beschreibung='{desc4}'")
assert ve4 == 20, f"FAIL: Erwartet VE=20, bekommen VE={ve4}"
print("  ✅ VE=20 korrekt erkannt")

# PURIZE 50er: VE=1 (Packung ist der Artikel)
ve5, desc5 = KnistermannScraper._extract_ve_info(
    "PURIZE Aktivkohlefilter, XTRA Slim Blazy Susan PURPLE, ø 5,9mm, 50er Packung",
    "",
    "PURIZE Aktivkohlefilter, XTRA Slim Blazy Susan PURPLE, ø 5,9mm, 50er Packung",
)
print(f"\nPURIZE 50er: VE={ve5}, Beschreibung='{desc5}'")
assert ve5 == 1, f"FAIL: Erwartet VE=1, bekommen VE={ve5}"
print("  ✅ VE=1 korrekt (50er Packung ist der Artikel)")


print("\n" + "=" * 70)
print("TEST 2: Packungsgrößen-Extraktion")
print("=" * 70)

test_cases = [
    ("PURIZE Aktivkohlefilter 50er Packung", 50),
    ("Purize 100er Aktivekohlefilter xtra slim", 100),
    ("PURIZE 250er Pack", 250),
    ("JAYSAFE Premium Joint Holder Case 127mm", None),
    ("Purize Aktivekohlefilter xtra slim 5,9mm WEIß 50er", 50),
]

for name, expected in test_cases:
    result = extract_pack_size(name)
    status = "✅" if result == expected else "❌"
    print(f"  {status} '{name}' → {result} (erwartet: {expected})")
    assert result == expected, f"FAIL: Erwartet {expected}, bekommen {result}"


print("\n" + "=" * 70)
print("TEST 3: Varianten-Matching mit Packungsgröße")
print("=" * 70)

# Simuliere JTL-Artikel wie in der Wawi-Screenshot
from dataclasses import dataclass

@dataclass
class MockJTLItem:
    name: str
    article_number: str = ""
    sku: str = ""
    item_id: int = 0
    vk_brutto: float = 0.0

jtl_articles = [
    MockJTLItem(name="Purize Aktivekohlefilter xtra slim 5,9mm WEIß 50er", article_number="1216", vk_brutto=9.50),
    MockJTLItem(name="Purize Aktivekohlefilter xtra slim 5,9mm GELB 50er", article_number="1169", vk_brutto=9.50),
    MockJTLItem(name="Purize 100er Aktivekohlefilter xtra slim 5,9mm Blazy Susan rosa", article_number="2681-ROSA", vk_brutto=17.50),
    MockJTLItem(name="Purize 100er Aktivekohlefilter xtra slim 5,9mm BLAU", article_number="1210-100", vk_brutto=17.50),
    MockJTLItem(name="PURIZE Aktivkohlefilter regular ø 9mm 50er Packung Blazy Susan lila", article_number="2681", vk_brutto=9.50),
    MockJTLItem(name="JAYSAFE® Premium Joint Hülle, 127mm, schwarz", article_number="JS-TUBE-100", vk_brutto=10.00),
]

matcher = ArticleMatcher(jtl_articles)

# PURIZE 50er Purple should match against 50er variant, not 100er
new_article = "PURIZE Aktivkohlefilter, XTRA Slim Blazy Susan PURPLE, ø 5,9mm, 50er Packung"
result = matcher.find_match("", new_article)

print(f"\nNeuer Artikel: {new_article}")
print(f"  Match-Typ: {result.match_type}")
print(f"  Confidence: {result.confidence:.0f}%")
print(f"  Matched: {result.matched_name}")
print(f"  Farbe: {result.color_detected}")

# Check that matched item is a 50er variant
matched_pack = extract_pack_size(result.matched_name)
print(f"  Matched Packungsgröße: {matched_pack}er")
print(f"  Matched VK: {result.matched_item.vk_brutto:.2f}€")

if matched_pack == 50:
    print("  ✅ Korrekt: 50er Variante gematcht!")
elif matched_pack == 100:
    print("  ❌ FEHLER: 100er Variante gematcht statt 50er!")
else:
    print(f"  ⚠️  Packungsgröße {matched_pack} – prüfen ob korrekt")

# VK should be ~9.50€ (from 50er variant), not ~17.50€
if result.matched_item.vk_brutto < 15.0:
    print(f"  ✅ VK korrekt: {result.matched_item.vk_brutto:.2f}€ (50er-Niveau)")
else:
    print(f"  ❌ VK zu hoch: {result.matched_item.vk_brutto:.2f}€ (wahrscheinlich 100er)")


# PURIZE 50er Pink - same test
new_article2 = "PURIZE Aktivkohlefilter, XTRA Slim Blazy Susan PINK, ø 5,9mm, 50er Packung"
result2 = matcher.find_match("", new_article2)
print(f"\nNeuer Artikel: {new_article2}")
print(f"  Match-Typ: {result2.match_type}")
print(f"  Matched: {result2.matched_name}")
matched_pack2 = extract_pack_size(result2.matched_name)
print(f"  Matched Packungsgröße: {matched_pack2}er")
print(f"  Matched VK: {result2.matched_item.vk_brutto:.2f}€")

if matched_pack2 == 50 or result2.matched_item.vk_brutto < 15.0:
    print("  ✅ Korrekt: 50er Variante bevorzugt!")
else:
    print("  ❌ FEHLER: Falsche Variante!")


print("\n" + "=" * 70)
print("ZUSAMMENFASSUNG")
print("=" * 70)
print("✅ Alle Tests abgeschlossen!")
