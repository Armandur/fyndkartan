"""Tillverkar-/varumärkesnormalisering: råa brand-namn -> kanonisk form så varianter (skiftläge,
legal-suffix, regional entitet) grupperas. Derive-at-read (som categories/tags); MANUFACTURER_MAP
(grupperingsnyckel -> display-override) injiceras från DB vid uppstart. Fristående (ingen database-
import) för att undvika cykel.

`manufacturer_key(raw)` = aggressiv grupperingsnyckel (gemener, strippa legal-suffix/region, punkt/
whitespace). `canonical(raw)` = MAP-override per nyckel, annars rensad default (legal-suffix bort).
KONSERVATIVT: bara tydliga legal-entiteter (AB/GmbH/A/S...) strippas, INTE "Foods"/"Group" (ofta del
av namnet, t.ex. "Dava Foods"); region (Sverige/Nordic) strippas bara när den föregår ett legal-suffix.
"""
import re

MANUFACTURER_MAP = {}  # key -> kanoniskt display-namn (override)

_LEGAL = r"(?:ab|aktiebolag|gmbh|a/s|a/b|oy|ab oy|ltd|inc|co|& co|kb|hb|as|bv|n\.?v\.?|s\.?a\.?|s\.?l\.?|spa)"
_REGION = r"(?:sverige|sweden|nordic|norden|scandinavia|skandinavien|international|europe|nordics)"
_LEGAL_RX = re.compile(rf"\s+(?:{_REGION}\s+)?{_LEGAL}\.?\s*$", re.I)
_WS = re.compile(r"\s+")


def set_map(mapping):
    """Ersätt grupperingsnyckel->display-mappningen (från DB vid uppstart)."""
    global MANUFACTURER_MAP
    MANUFACTURER_MAP = dict(mapping or {})


def _strip_legal(s):
    prev = None
    while prev != s:  # upprepade trailing-suffix ("X Sverige AB", "X AB Oy")
        prev = s
        s = _LEGAL_RX.sub("", s).strip()
    return s


def manufacturer_key(raw):
    """Grupperingsnyckel: gemener, legal-suffix/region bort, punkt/&/whitespace normaliserat."""
    if not raw:
        return ""
    s = _strip_legal(raw.strip())
    s = re.sub(r"[.,&]", " ", s.lower())
    return _WS.sub(" ", s).strip()


def canonical(raw):
    """Kanoniskt display-namn: MAP-override per nyckel, annars rensad default (legal-suffix bort)."""
    if not raw:
        return None
    key = manufacturer_key(raw)
    if key in MANUFACTURER_MAP:
        return MANUFACTURER_MAP[key]
    return _strip_legal(raw.strip()) or raw.strip()
