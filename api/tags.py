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

# Speditör-vokabulär + override-mappning (label -> speditör). Laddas vid uppstart.
PROVIDERS = []
PROVIDER_MAP = {}


def set_types(types):
    CANONICAL[:] = list(types)


def set_providers(providers):
    PROVIDERS[:] = list(providers)


def set_provider_map(mapping):
    PROVIDER_MAP.clear()
    PROVIDER_MAP.update(mapping or {})


def effective_provider(label):
    """Speditör för en etikett: override (provider_map) annars regelbaserad
    classify_provider. Filtreras mot vokabulären - borttagen speditör -> None."""
    p = PROVIDER_MAP.get(label) or classify_provider(label)
    return p if p in PROVIDERS else None


def put_provider(label, provider):
    PROVIDER_MAP[label] = provider


def remove_provider(label):
    PROVIDER_MAP.pop(label, None)


def valid_type(t):
    return t in CANONICAL


def effective_types(label):
    """Kanoniska typer för en etikett. Filtrerar mot vokabulären så att en seed-
    producerad typ som tagits bort (inte längre i CANONICAL) faller till 'other'."""
    types = TAG_MAP.get(label) or seed_types(label)
    valid = [t for t in types if t in CANONICAL]
    if valid:
        return valid
    return ["other"] if "other" in CANONICAL else []


def build_tag(label):
    tag = {"types": effective_types(label), "label": label}
    provider = effective_provider(label)
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
