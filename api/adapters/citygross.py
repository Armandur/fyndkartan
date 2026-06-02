"""City Gross butiks-adapter (Bergendahls).

`GET /api/v1/PageData/stores` ger hela beståndet (~39 butiker, ingen auth) med adress,
koordinater, veckoöppettider (mon-sun + helgdagar) och serviceutbud (booleans -> tags).
storeNumber (för erbjudanden via Axfood-infra) resolvas separat när offers byggs;
butiks-id:t bevaras i `native` (siteId) för det.
"""

from datetime import date

from .base import day_entry, exception_entry, make_store

URL = "https://www.citygross.se/api/v1/PageData/stores"
UA = "matbutiker-sync/1.0"

# serviceutbud (booleans) -> svensk etikett; seedas till rätt typ där `seed_types` har en
# regel (Bageri/ATG/Självscanning/Svenska Spel/Uttagsautomat/PostNord/Schenker), annars
# 'other' (admin kan mappa). En tagg lagras bara som {label}; typer härleds vid läsning.
_SERVICES = {
    "fish": "Fiskdisk",
    "deli": "Delikatessdisk",
    "bakery": "Bageri",
    "catering": "Catering",
    "atg": "ATG",
    "scanning": "Självscanning",
    "svenskaSpel": "Svenska Spel",
    "atm": "Uttagsautomat",
    "postnord": "PostNord-ombud",
    "schenker": "Schenker-ombud",
    "wifi": "WiFi",
    "swan": "Svanenmärkt butik",
}
_DAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]


async def fetch_all(client):
    headers = {"Accept": "application/json", "User-Agent": UA}
    r = await client.get(URL, headers=headers, timeout=30)
    r.raise_for_status()
    rows = [s.get("data") or {} for s in r.json()]
    return [
        _map(s)
        for s in rows
        if s.get("type") == "StorePage" and s.get("ispublished") and (s.get("storeName") or "").strip()
    ]


def _t(iso):
    return iso[11:16] if iso and len(iso) >= 16 else None


def _coords(loc):
    parts = (loc.get("coordinates") or "").split(",")
    try:
        return float(parts[0]), float(parts[1])
    except (ValueError, IndexError):
        return None, None


def _today(oh):
    d = oh.get(_DAYS[date.today().weekday()])
    if not d:
        return None
    o, c = _t(d.get("opens")), _t(d.get("closes"))
    return f"{o}-{c}" if o and c and o != c else "Stängt"


def _week(oh):
    out = []
    for i, day in enumerate(_DAYS):
        d = oh.get(day)
        if not d:
            continue
        o, c = _t(d.get("opens")), _t(d.get("closes"))
        out.append(day_entry(i, o, c, not (o and c and o != c)))
    return out or None


def _exceptions(oh):
    out = []
    for h in oh.get("holidays") or []:
        o, c = _t(h.get("opens")), _t(h.get("closes"))
        out.append(exception_entry((h.get("date") or "")[:10] or None, h.get("name"), o, c, not (o and c and o != c)))
    return out or None


def _map(s):
    addr = s.get("address") or {}
    contact = s.get("contactInformation") or {}
    oh = s.get("openingHours") or {}
    lat, lng = _coords(s.get("storeLocation") or {})
    url = s.get("url") or ""
    return make_store(
        "citygross",
        s.get("id"),
        (s.get("storeName") or "").strip(),
        brand="city_gross",
        street=(addr.get("streetAddress") or "").strip() or None,
        postal_code=(addr.get("zipCode") or "").strip() or None,
        city=(addr.get("city") or "").strip() or None,
        lat=lat,
        lng=lng,
        phone=(contact.get("phone") or "").strip() or None,
        email=(contact.get("email") or "").strip() or None,
        oh_today=_today(oh),
        raw=oh,
        week=_week(oh),
        exceptions=_exceptions(oh),
        link_store=("https://www.citygross.se" + url) if url else None,
        tags=[{"label": lbl} for key, lbl in _SERVICES.items() if (s.get("services") or {}).get(key)],
        native={"siteId": s.get("siteId"), "id": s.get("id")},
    )
