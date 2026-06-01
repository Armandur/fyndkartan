"""Normalisering av butikstjänst-taggar.

Råetiketten (kedjespecifik, t.ex. "Receptfria läkemedel") får en kanonisk typ
via:
  1. en editerbar override-mappning (tag_map, sätts i admin-UI), annars
  2. den regelbaserade `classify_service` (seed).

Typen beräknas vid läsning, så ändringar i admin-UI:t slår igenom direkt utan
omsynk."""

from .adapters.base import classify_service

# label -> kanonisk typ (admin-override). Laddas från DB vid uppstart.
TAG_TYPES = {}


def effective_type(label):
    return TAG_TYPES.get(label) or classify_service(label)


def set_map(mapping):
    TAG_TYPES.clear()
    TAG_TYPES.update(mapping or {})


def put(label, type_):
    TAG_TYPES[label] = type_


def remove(label):
    TAG_TYPES.pop(label, None)
