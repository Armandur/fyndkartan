import asyncio
import logging
from datetime import datetime, timezone

from babel import Locale

log = logging.getLogger("matbutiker")

# Axfood anger ursprung på engelska ('Sweden'/'Norway'/'Italy'); översätt till svenska via
# babel (engelskt namn -> ISO-kod -> svenskt namn) så det matchar övriga kedjors origin-format.
_EN_NAME_TO_CODE = {n.lower(): c for c, n in Locale("en").territories.items() if len(c) == 2}
_SV_TERRITORIES = Locale("sv").territories


def _country_en_to_sv(name):
    code = _EN_NAME_TO_CODE.get((name or "").strip().lower())
    return _SV_TERRITORIES.get(code) if code else None

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36"
)

# Willys och Hemköp delar samma SAP Commerce-storefront. Domänen avgör kedjan.
DOMAIN = {"willys": "www.willys.se", "hemkop": "www.hemkop.se"}

_EAN_CONCURRENCY = 10
_EAN_RETRIES = 3       # försök vid 429/5xx/timeout innan koden ges upp (-> tom)
_EAN_BACKOFF = 1.0     # bas-sekunder, exponentiell (1, 2, 4...); Retry-After respekteras vid 429


async def fetch_p_meta(client, chain, codes):
    """{code: {"ean":..., "category":..., "origin":...}} via produktdetaljen
    (`/axfood/rest/p/{code}`). category = googleAnalyticsCategory (pipe-path); origin =
    `tradeItemCountryOfOrigin` översatt till svenska (None om saknas - ofta för färskvaror).
    Bunden parallellism + 429/5xx-medveten retry med exponentiell backoff (skonsamt vid throttling;
    backoffen håller semafor-platsen -> sänker farten automatiskt när Axfood stryper)."""
    domain = DOMAIN.get(chain)
    if not domain or not codes:
        return {}
    headers = {"Accept": "application/json", "User-Agent": UA}
    sem = asyncio.Semaphore(_EAN_CONCURRENCY)
    empty = {"ean": "", "category": None, "origin": None}
    blocked = {"ean": "", "category": None, "origin": None, "blocked": True}

    async def one(code):
        async with sem:
            for attempt in range(_EAN_RETRIES + 1):
                try:
                    r = await client.get(f"https://{domain}/axfood/rest/p/{code}", headers=headers, timeout=15)
                    if r.status_code == 200:
                        d = r.json()
                        return code, {
                            "ean": d.get("ean") or "",
                            "category": d.get("googleAnalyticsCategory") or None,
                            "origin": _country_en_to_sv(d.get("tradeItemCountryOfOrigin")),
                        }
                    if r.status_code == 404:
                        return code, empty  # genuint ej funnen, ingen retry/block
                    if r.status_code == 403:
                        return code, blocked  # WAF-block: fail-fast (ingen per-kod-retry); circuit-breakern sköter cooldown
                    # 429 / 5xx -> transient throttle: backa av och försök om
                    if attempt < _EAN_RETRIES:
                        ra = r.headers.get("Retry-After")
                        wait = float(ra) if (ra or "").isdigit() else _EAN_BACKOFF * (2 ** attempt)
                        await asyncio.sleep(min(wait, 30))
                        continue
                    return code, blocked  # retries slut -> markera blockerad (circuit-breaker räknar)
                except Exception as e:  # noqa: BLE001
                    if attempt < _EAN_RETRIES:
                        await asyncio.sleep(_EAN_BACKOFF * (2 ** attempt))
                        continue
                    log.warning("Axfood meta %s misslyckades: %s", code, e)
                    return code, blocked
        return code, blocked

    return dict(await asyncio.gather(*(one(c) for c in codes)))


async def fetch_eans(client, chain, codes):
    """{code: ean} via produktdetaljen (ean='' när ingen hittades)."""
    meta = await fetch_p_meta(client, chain, codes)
    return {c: m["ean"] for c, m in meta.items()}


def _money(s):
    """'169,00 kr' / '153,11 kr' -> float."""
    if not s:
        return None
    s = s.replace("kr", "").replace("\xa0", " ").strip().replace(" ", "").replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return None


async def fetch_offers(client, chain, store_id):
    """Hämta butikens kampanjer (e-handelns campaigns-API, butiks-scopat).

    OBS: kampanjpris ≠ garanterat fysiskt butikspris. EAN hämtas inte här - det
    ligger i produktdetaljen (`/axfood/rest/p/{code}`) och resolvas separat per code.
    """
    domain = DOMAIN.get(chain)
    if not domain:
        return []
    headers = {"Accept": "*/*", "content-type": "application/json", "User-Agent": UA}
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    # size=1000 returnerar hela kampanjbeståndet i ett anrop (max ~500 i praktiken).
    # OBS: sidparametern heter `page` (inte `currentPage`, som ignoreras) - vi
    # behöver den inte här, men en stor `size` är enklare och säkrare.
    r = await client.get(
        f"https://{domain}/search/campaigns",
        params={"storeId": store_id, "size": 1000},
        headers=headers,
        timeout=30,
    )
    r.raise_for_status()
    results = r.json().get("results") or []
    if len(results) >= 1000:
        log.warning("Axfood %s/%s: 1000 kampanjer (kan vara avhugget)", chain, store_id)
    return [_map(p, chain, store_id, now) for p in results]


def _map(p, chain, store_id, fetched_at):
    promo = (p.get("potentialPromotions") or [{}])[0]
    promo_price = (promo.get("price") or {}).get("value")
    # Kampanjpriset ligger i promotionen; produktens priceValue är ordpriset.
    price = promo_price if promo_price is not None else p.get("priceValue")
    price_text = (promo.get("price") or {}).get("formattedValue") or p.get("price") or None

    comp_unit = p.get("comparePriceUnit") or None
    comp_value = _money(p.get("comparePrice"))
    comp_price = None
    if p.get("comparePrice"):
        comp_price = p["comparePrice"] + (f"/{comp_unit}" if comp_unit else "")

    valid_to = None
    vu = promo.get("validUntil")
    if vu:
        valid_to = datetime.fromtimestamp(vu / 1000, timezone.utc).strftime("%Y-%m-%d")

    member = promo.get("campaignType") == "LOYALTY" or "Klubbpris" in (
        promo.get("textLabelGenerated") or ""
    )
    mechanic = promo.get("promotionType") or promo.get("textLabelGenerated") or None
    package = p.get("productLine2") or p.get("displayVolume") or None
    return {
        "chain": chain,
        "store_id": str(store_id),
        "offer_id": str(p.get("code")),  # = Axfood-artikelkod, nyckel för EAN-resolve
        "name": p.get("name"),
        "brand": p.get("manufacturer"),
        "package": package,
        "price": price,
        "price_text": price_text,
        "comparison_price": comp_price,
        "comparison_value": comp_value,
        "comparison_unit": comp_unit,
        "category_raw": p.get("googleAnalyticsCategory") or None,
        "category_id": None,
        "mechanic_type": mechanic,
        "valid_to": valid_to,
        "eans": [],  # resolvas separat via code -> EAN
        "image": (p.get("image") or {}).get("url"),
        "member_price": 1 if member else 0,
        "savings": round(p["savingsAmount"], 2) if p.get("savingsAmount") is not None else None,
        "fetched_at": fetched_at,
    }
