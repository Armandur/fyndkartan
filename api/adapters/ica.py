import logging

from . import ica_token
from .base import make_store, tags_from_services

log = logging.getLogger("matbutiker")

URL = "https://apim-pub.gw.ica.se/sverige/digx/mdsastoresearch/v1/storeslist"

PROFILE_BRAND = {
    "Maxi": "maxi_ica",
    "Kvantum": "ica_kvantum",
    "Supermarket": "ica_supermarket",
    "Nära": "ica_nara",
}


async def fetch_all(client, env_token=None):
    """Hämta alla ICA-butiker. Tokenet skrapas automatiskt från ica.se/butiker/
    (publikt, kortlivat ~1h) och förnyas vid behov. env_token används bara som
    nödfallback om skrapningen misslyckas."""
    try:
        token = await ica_token.get_token(client)
    except Exception as e:  # noqa: BLE001
        if env_token:
            log.warning("ICA token-skrap misslyckades (%s) - faller tillbaka på ICA_TOKEN", e)
            token = env_token
        else:
            raise

    base_headers = {
        "Accept": "application/json",
        "Origin": "https://www.ica.se",
        "Referer": "https://www.ica.se/",
    }
    out, skip, take = [], 0, 50
    seen_ids = set()
    refreshed = False
    max_pages = 100  # säkerhetstak: ~5000 butiker, hindrar oändlig loop om skip ignoreras
    for _ in range(max_pages):
        r = await client.get(
            URL,
            params={"url": "/", "sort": "FromStore", "skip": skip, "take": take},
            headers={**base_headers, "Authorization": f"Bearer {token}"},
            timeout=20,
        )
        if r.status_code == 401 and not refreshed:
            # Token kan ha gått ut mitt i synken - skrapa ett nytt en gång och fortsätt.
            log.info("ICA: 401, förnyar token och försöker igen")
            token = await ica_token.get_token(client, force=True)
            refreshed = True
            continue
        r.raise_for_status()
        rows = r.json()
        if not rows:
            break
        page_ids = {s.get("storeId") for s in rows}
        if page_ids <= seen_ids:  # API:t ger samma rader igen -> sluta paginera
            break
        seen_ids |= page_ids
        out.extend(_map(s) for s in rows)
        if len(rows) < take:
            break
        skip += take
    return out


def _map(s):
    addr = s.get("address") or {}
    coords = addr.get("coordinates") or {}
    # OBS: hos ICA är coordinateX = latitud, coordinateY = longitud (och de är strängar).
    lat = _f(coords.get("coordinateX"))
    lng = _f(coords.get("coordinateY"))
    offers = (((s.get("highlightUrls") or {}).get("offers")) or {}).get("url")
    store_id = s.get("storeId")
    return make_store(
        "ica",
        store_id,
        s.get("storeName"),
        brand=PROFILE_BRAND.get(s.get("profile"), "ica"),
        street=addr.get("street"),
        postal_code=addr.get("postalCode"),
        city=addr.get("city"),
        lat=lat,
        lng=lng,
        phone=s.get("phoneNumber"),
        email=s.get("emailAddress"),
        oh_today=_today(s.get("openingHours")),
        raw=s.get("openingHours"),
        link_store=s.get("bhsUrl"),
        link_offers=offers,
        link_online=s.get("onlineUrl") or f"https://handlaprivatkund.ica.se/stores/{store_id}",
        tags=tags_from_services(s.get("services")),
        native={"accountNumber": s.get("accountNumber"), "profile": s.get("profile")},
    )


def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _today(oh):
    t = (oh or {}).get("today") or {}
    if t.get("isClosed"):
        return "Stängt"
    if t.get("opens") and t.get("closes"):
        return f"{t['opens']}-{t['closes']}"
    return None
