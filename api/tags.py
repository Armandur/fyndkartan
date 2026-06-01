"""Normalisering av butikstjänst-taggar.

Råetiketten (kedjespecifik, t.ex. "Posten Brev & paket") får en lista av
kanoniska typer (en tagg kan vara flera, t.ex. postal + parcel) via:
  1. en editerbar override-mappning (tag_map, sätts i admin-UI), annars
  2. den regelbaserade `seed_types` (seed).

För paket-/post-taggar bevaras dessutom speditören (`provider`, t.ex. DHL/
PostNord) vid sidan av typerna. Typerna härleds vid läsning, så ändringar i
admin-UI:t slår igenom direkt utan omsynk."""

from .adapters.base import classify_provider, seed_types

# label -> [kanoniska typer] (admin-override). Laddas från DB vid uppstart.
TAG_MAP = {}

# Aktuell kanonisk vokabulär (editerbar i admin-UI). Laddas från DB vid uppstart.
CANONICAL = []


def set_types(types):
    CANONICAL[:] = list(types)


def valid_type(t):
    return t in CANONICAL


def effective_types(label):
    return TAG_MAP.get(label) or seed_types(label)


def build_tag(label):
    tag = {"types": effective_types(label), "label": label}
    provider = classify_provider(label)
    if provider:
        tag["provider"] = provider
    return tag


def set_map(mapping):
    TAG_MAP.clear()
    TAG_MAP.update(mapping or {})


def put(label, types):
    TAG_MAP[label] = list(types)


def remove(label):
    TAG_MAP.pop(label, None)
