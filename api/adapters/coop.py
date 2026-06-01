import asyncio
import logging

from . import keys
from .base import day_entry, exception_entry, expand_sv_label, make_store, tags_from_services

log = logging.getLogger("matbutiker")

URL = "https://proxy.api.coop.se/external/store/stores/map"
PARAMS = {"api-version": "v2", "conceptIds": "12,6,95", "invertFilter": "true"}
DETAIL_URL = "https://proxy.api.coop.se/external/store/stores"

# Hur många detalj-anrop som körs samtidigt (snällt mot Coops gateway).
_DETAIL_CONCURRENCY = 8

# Cachad skrapad nyckel (om env-nyckeln roterats bort under körning).
_scraped_key = None


async def _get(client, key):
    return await client.get(
        URL,
        params=PARAMS,
        headers={"Ocp-Apim-Subscription-Key": key, "Accept": "application/json"},
        timeout=30,
    )


async def _detail(client, key, ledger, sem):
    """Hämta butiksdetaljen för services (-> tags) + concept (-> brand)."""
    if not ledger:
        return None
    async with sem:
        try:
            r = await client.get(
                f"{DETAIL_URL}/{ledger}",
                params={"api-version": "v5"},
                headers={"Ocp-Apim-Subscription-Key": key, "Accept": "application/json"},
                timeout=20,
            )
            if r.status_code == 200:
                return r.json()
        except Exception as e:  # noqa: BLE001 - logga, hoppa butiken
            log.warning("Coop detalj %s misslyckades: %s", ledger, e)
    return None


async def fetch_all(client, env_key=None):
    global _scraped_key
    key = _scraped_key or env_key
    if not key:
        key = _scraped_key = await keys.scrape_coop_key(client)

    r = await _get(client, key)
    if r.status_code == 401:
        # Nyckeln kan ha roterats - skrapa en ny och försök igen.
        log.info("Coop: 401, skrapar ny subscription-nyckel")
        key = _scraped_key = await keys.scrape_coop_key(client)
        r = await _get(client, key)
    r.raise_for_status()
    stores = r.json()

    # Berika med detalj (services -> tags, concept -> brand) via bundna parallella anrop.
    sem = asyncio.Semaphore(_DETAIL_CONCURRENCY)
    details = await asyncio.gather(
        *(_detail(client, key, s.get("ledgerAccountNumber"), sem) for s in stores)
    )
    return [_map(s, d) for s, d in zip(stores, details)]


def _brand(concept):
    name = (concept or {}).get("name") or "Coop"
    return (
        name.lower()
        .replace("å", "a")
        .replace("ä", "a")
        .replace("ö", "o")
        .replace(" ", "_")
    )


def _week(detail):
    """Normaliserad vecka ur Coops etikettgrupperade `openingHours` (tider HH:MM:SS).
    Hoppar special-datum (de hör hemma i exceptions)."""
    out = []
    for g in (detail or {}).get("openingHours") or []:
        if g.get("isSpecialDate"):
            continue
        closed = bool(g.get("isClosed"))
        for d in expand_sv_label(g.get("text")):
            out.append(day_entry(d, g.get("openFrom"), g.get("openTo"), closed))
    return sorted(out, key=lambda e: e["day"]) or None


def _exceptions(detail):
    """Coops `futureIrregularOpeningHours` (helgdagar med datum) -> avvikelser."""
    out = []
    for g in (detail or {}).get("futureIrregularOpeningHours") or []:
        date = (g.get("date") or "")[:10] or None
        out.append(exception_entry(date, g.get("text"), g.get("openFrom"), g.get("openTo"), bool(g.get("isClosed"))))
    return out or None


def _map(s, detail):
    ledger = s.get("ledgerAccountNumber")
    url = s.get("url") or ""
    detail = detail or {}
    services = detail.get("services") or []
    return make_store(
        "coop",
        s.get("storeId"),
        s.get("name"),
        brand=_brand(detail.get("concept")) if detail.get("concept") else "coop",
        street=s.get("address"),
        postal_code=s.get("postalCode"),
        city=s.get("city"),
        lat=s.get("latitude"),
        lng=s.get("longitude"),
        phone=s.get("phone"),
        email=detail.get("email"),
        oh_today=s.get("openingHoursToday"),
        raw={
            "openingHours": detail.get("openingHours"),
            "futureIrregularOpeningHours": detail.get("futureIrregularOpeningHours"),
        } if detail.get("openingHours") else None,
        week=_week(detail),
        exceptions=_exceptions(detail),
        link_store="https://www.coop.se" + url if url else None,
        link_offers=f"https://dr.coop.se/butik/{ledger}" if ledger else None,
        tags=tags_from_services(services),
        native={
            "ledgerAccountNumber": ledger,
            "storeId": s.get("storeId"),
            "concept": (detail.get("concept") or {}).get("name"),
            "ownerName": detail.get("ownerName"),
        },
    )
