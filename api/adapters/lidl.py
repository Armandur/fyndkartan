import asyncio
import logging
from datetime import date as _date

from . import keys
from .base import day_entry, exception_entry, make_store

log = logging.getLogger("matbutiker")

URL = "https://live.api.schwarz/odj/stores-api/v2/myapi/stores-frontend/stores"

ICON_MAP = {
    "parking": "parking",
    "parking-disabled-people": "parking_disabled",
    "e-charging-station": "e_charging",
    "lidl-plus-vertical": "loyalty_app",
    "lidl-plus": "loyalty_app",
    "bakery": "bakery",
}

# Cachad skrapad nyckel (om env-nyckeln roterats bort under körning).
_scraped_key = None


def _headers(key):
    return {
        "Accept": "*/*",
        "Origin": "https://www.lidl.se",
        "Referer": "https://www.lidl.se/",
        "x-apikey": key,
    }


async def fetch_all(client, env_key, boxes, sleep=0.12):
    """Schwarz stores-api ger bara butiker inom en geo_box -> svep ett rutnät och
    deduplicera på objectNumber. Vid 401 skrapas en ny x-apikey från base.js."""
    global _scraped_key
    key = _scraped_key or env_key
    if not key:
        key = _scraped_key = await keys.scrape_lidl_key(client)

    seen = {}
    refreshed = False
    for (lat0, lng0, lat1, lng1) in boxes:
        offset, limit = 0, 100
        while True:
            # Bygg query manuellt: geo_box ska ha literala ',' och ':' (inte url-kodade).
            q = (
                f"?country_code=SE&limit={limit}&offset={offset}"
                f"&geo_box={lat0},{lng0}:{lat1},{lng1}"
            )
            r = await client.get(URL + q, headers=_headers(key), timeout=30)
            if r.status_code == 401 and not refreshed:
                log.info("Lidl: 401, skrapar ny x-apikey")
                key = _scraped_key = await keys.scrape_lidl_key(client)
                refreshed = True
                continue  # försök samma ruta igen med ny nyckel
            if r.status_code != 200:
                break
            data = r.json()
            items = data.get("items") or []
            total = (data.get("meta") or {}).get("total", 0)
            for s in items:
                on = s.get("objectNumber")
                if on and on not in seen:
                    seen[on] = _map(s)
            offset += limit
            if offset >= total or not items:
                break
            await asyncio.sleep(sleep)
        await asyncio.sleep(sleep)
    return list(seen.values())


def _map(s):
    a = s.get("address") or {}
    md = s.get("marketingData") or {}
    tags = []
    for ic in md.get("infoIcons") or []:
        ods = ic.get("odsName") or ic.get("name")
        if ods:
            tags.append({"type": ICON_MAP.get(ods, "other"), "label": ods})
    street = " ".join(x for x in [a.get("streetName"), a.get("streetNumber")] if x) or None
    status = (s.get("status") or {}).get("name")
    return make_store(
        "lidl",
        s.get("objectNumber"),
        s.get("storeName"),
        brand="lidl",
        street=street,
        postal_code=a.get("zip"),
        city=a.get("city"),
        lat=a.get("latitude"),
        lng=a.get("longitude"),
        oh_today=_today(s.get("openingHours")),
        raw=s.get("openingHours"),
        week=_week(s.get("openingHours")),
        exceptions=_exceptions(s.get("openingHours")),
        open_now=True if status == "open" else (False if status else None),
        native={
            "objectNumber": s.get("objectNumber"),
            "offerRegion": md.get("offerRegion"),
            "zone": md.get("zone"),
        },
        tags=tags,
    )


def _today(oh):
    items = (oh or {}).get("items") or []
    if items and items[0].get("timeRanges"):
        tr = items[0]["timeRanges"][0]
        return f"{tr['from'][11:16]}-{tr['to'][11:16]}"
    return None


def _weekday(item):
    try:
        return _date.fromisoformat(item.get("date") or "").weekday()  # 0=mån..6=sön
    except ValueError:
        return None


def _week(oh):
    """Lidls per-datum-schema (kommande dagar) -> normaliserad vecka. Regelbundna dagar
    (REGULAR/SUNDAY_REPEAT); tom timeRanges = stängt. En veckodag kan saknas om ett
    helgdatum ligger på den i fönstret (avsaknad = okänt)."""
    by_day = {}
    for it in (oh or {}).get("items") or []:
        if it.get("reason") == "SPECIAL_DAY":
            continue
        d = _weekday(it)
        if d is None or d in by_day:
            continue
        tr = (it.get("timeRanges") or [{}])[0]
        if it.get("timeRanges"):
            by_day[d] = day_entry(d, tr.get("from", "")[11:16], tr.get("to", "")[11:16], False)
        else:
            by_day[d] = day_entry(d, None, None, True)
    return [by_day[d] for d in sorted(by_day)] or None


def _exceptions(oh):
    """Lidl-datum markerade SPECIAL_DAY -> daterade avvikelser."""
    out = []
    for it in (oh or {}).get("items") or []:
        if it.get("reason") != "SPECIAL_DAY":
            continue
        ranges = it.get("timeRanges") or []
        if ranges:
            tr = ranges[0]
            out.append(exception_entry(it.get("date"), None, tr.get("from", "")[11:16], tr.get("to", "")[11:16], False))
        else:
            out.append(exception_entry(it.get("date"), None, None, None, True))
    return out or None
