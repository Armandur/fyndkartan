"""Kategorinormalisering: kedjornas råa kategorier -> kanonisk kategori.

Speglar tags.py: derive-at-read (råkategorin är sanning, mappningen härleds vid
läsning så admin-ändringar slår igenom utan omsynk). CATEGORY_MAP injiceras vid
uppstart från DB (set_map); ingen database-import här (undviker cykel).

Nyckel: Axfood (willys+hemkop) delar taxonomi -> chain_key "axfood", raw_key =
första pipe-segmentet. ICA/Coop: chain_key = kedjan, raw_key = hela råsträngen.
"""

from .config import CANONICAL_CATEGORIES, DEFAULT_CATEGORY_MAP

CATEGORY_MAP = dict(DEFAULT_CATEGORY_MAP)  # (chain_key, raw_key) -> kanonisk nyckel
CANONICAL = [{"key": k, "label": lbl} for k, lbl in CANONICAL_CATEGORIES]
_LABELS = {k: lbl for k, lbl in CANONICAL_CATEGORIES}
_AXFOOD = ("willys", "hemkop")


def set_map(mapping):
    """Ersätt mappningen (från DB vid uppstart). Tom -> behåll seed."""
    global CATEGORY_MAP
    CATEGORY_MAP = dict(mapping) if mapping else dict(DEFAULT_CATEGORY_MAP)


def raw_key(chain, raw):
    """Normaliserings-nyckeln för en råkategori (Axfood: första pipe-segmentet)."""
    if chain in _AXFOOD:
        return "axfood", (raw.split("|")[0] if raw else "")
    return chain, (raw or "")


def category_for(chain, raw):
    """Kanonisk kategori-nyckel för (chain, råkategori), 'ovrigt' om omappad/saknas."""
    if not raw:
        return "ovrigt"
    return CATEGORY_MAP.get(raw_key(chain, raw), "ovrigt")


def category_from_detail(source, raw):
    """Kanonisk kategori ur produktdetaljens råkategori (rikare än offer-nivån).
    Axfood = `googleAnalyticsCategory` (pipe-path, första segmentet via axfood-mappningen);
    Coop = `navCategories` topp-namn (egen mappning under 'coop_nav'). None om omappad."""
    if not raw:
        return None
    if source in _AXFOOD:
        return CATEGORY_MAP.get(("axfood", raw.split("|")[0]))
    if source == "coop":
        return CATEGORY_MAP.get(("coop_nav", raw))
    return None


def label(key):
    return _LABELS.get(key, key)
