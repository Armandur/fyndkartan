"""Kategorinormalisering: kedjornas råa kategorier -> kanonisk kategori.

Speglar tags.py: derive-at-read (råkategorin är sanning, mappningen härleds vid
läsning så admin-ändringar slår igenom utan omsynk). CATEGORY_MAP injiceras vid
uppstart från DB (set_map); ingen database-import här (undviker cykel).

Nyckel: Axfood (willys+hemkop) delar taxonomi -> chain_key "axfood", raw_key =
första pipe-segmentet. ICA/Coop: chain_key = kedjan, raw_key = hela råsträngen.
"""

import re

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
    """Normaliserings-nyckeln för en råkategori (Axfood: första pipe-segmentet, gemener -
    offers ger gemen googleAnalyticsCategory men crawlens avdelnings-fallback kan vara versal)."""
    if chain in _AXFOOD:
        return "axfood", (raw.split("|")[0].lower() if raw else "")
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
        return CATEGORY_MAP.get(("axfood", raw.split("|")[0].lower()))
    if source == "coop":
        return CATEGORY_MAP.get(("coop_nav", raw))
    if source == "ica":
        return CATEGORY_MAP.get(("ica_nav", raw))
    return None


# Färsk frukt/grönt-viktvaror (slump-EAN) saknar produktdetalj och faller till `ovrigt`. Namn-
# fallback: tydliga färskvaru-termer som HELORD -> frukt_gront. Körs BARA när kategorin annars
# blir ovrigt (se database), så "Tomatketchup"/"Krossade tomater" (som har kategori) inte träffas.
_PRODUCE_WORDS = frozenset((
    "banan", "bananer", "äpple", "äpplen", "päron", "apelsin", "apelsiner", "citron", "citroner",
    "lime", "gurka", "gurkor", "paprika", "purjolök", "salladslök", "rödlök", "gullök", "vitlök",
    "potatis", "morot", "morötter", "broccoli", "blomkål", "sallad", "isbergssallad", "avokado",
    "mango", "ananas", "kiwi", "melon", "vattenmelon", "vindruvor", "druvor", "jordgubbar",
    "blåbär", "hallon", "björnbär", "spenat", "zucchini", "aubergine", "rädisor", "rödbetor",
    "fänkål", "champinjoner", "champinjon", "ingefära", "clementin", "clementiner", "mandarin",
    "nektarin", "nektariner", "persika", "persikor", "plommon", "körsbär", "granatäpple",
    "grapefrukt", "persilja", "dill", "basilika", "mynta", "koriander", "vitkål", "rödkål", "lök",
))
_WORD_RX = re.compile(r"[a-zåäö]+")


def category_from_name(name):
    """Sista-utvägs namn-fallback för viktvaror utan produktdetalj: tydliga frukt/grönt-termer
    (helord) -> 'frukt_gront', annars None."""
    return "frukt_gront" if set(_WORD_RX.findall((name or "").lower())) & _PRODUCE_WORDS else None


def label(key):
    return _LABELS.get(key, key)
