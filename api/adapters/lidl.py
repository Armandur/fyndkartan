import asyncio
import logging

from . import keys
from .base import make_store

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
