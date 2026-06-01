import logging
import re
from datetime import datetime, timezone

from . import keys

log = logging.getLogger("matbutiker")

URL_BASE = "https://external.api.coop.se/dke/offers"
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36"
)

# Cachad skrapad offers-nyckel (dke) om env-nyckeln saknas/roterats.
_scraped_key = None


def _money(s):
    """'69,90' / '69,90 kr' -> 69.9."""
    if s is None:
        return None
    if isinstance(s, (int, float)):
        return float(s)
    m = re.search(r"\d+[.,]?\d*", str(s))
    return float(m.group(0).replace(",", ".")) if m else None


def _parse_comparison(text):
    """'69,90/kg.' -> (69.9, 'kg')."""
    if not text or "/" not in text:
        return None, None
    val, _, unit = text.partition("/")
    return _money(val), unit.strip().rstrip(".") or None


async def _resolve_key(client, env_key, force=False):
    global _scraped_key
    if env_key and not force:
        return env_key
    if _scraped_key and not force:
        return _scraped_key
    _scraped_key = await keys.scrape_coop_offers_key(client)
    return _scraped_key


async def _get(client, ledger, key):
    return await client.get(
        f"{URL_BASE}/{ledger}",
        params={"api-version": "v2"},
        headers={
            "Ocp-Apim-Subscription-Key": key,
            "Accept": "application/json",
            "Origin": "https://www.coop.se",
            "User-Agent": UA,
        },
        timeout=30,
    )


async def fetch_offers(client, store_id, ledger, env_key=None):
    """Hämta Coops erbjudanden (digitala reklambladet, strukturerat) för en butik.

    Anropet sker mot ledgerAccountNumber, men erbjudandena lagras under butikens
    store_id (samma nyckel som offers-routen använder)."""
    if not ledger:
        return []
    key = await _resolve_key(client, env_key)
    r = await _get(client, ledger, key)
    if r.status_code == 401:
        log.info("Coop offers: 401, skrapar ny dke-nyckel")
        key = await _resolve_key(client, env_key, force=True)
        r = await _get(client, ledger, key)
    r.raise_for_status()
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return [_map(o, store_id, now) for o in (r.json() or [])]


def _map(o, store_id, fetched_at):
    c = o.get("content") or {}
    pi = o.get("priceInformation") or {}
    comp_v, comp_u = _parse_comparison(c.get("comparativePriceText"))
    price = pi.get("discountValue")
    price_text = None
    if price is not None:
        price_text = f"{price:.2f}".replace(".", ",").rstrip("0").rstrip(",") + " kr"
    image = c.get("imageUrl")
    if image and image.startswith("//"):
        image = "https:" + image
    ext = o.get("externalId")
    return {
        "chain": "coop",
        "store_id": str(store_id),
        "offer_id": str(o.get("id")),
        "name": c.get("title"),
        "brand": c.get("brand"),
        "package": c.get("amountInformation") or None,
        "price": float(price) if price is not None else None,
        "price_text": price_text,
        "comparison_price": c.get("comparativePriceText"),
        "comparison_value": comp_v,
        "comparison_unit": comp_u,
        "category_raw": o.get("categoryGroup"),
        "category_id": None,
        "mechanic_type": pi.get("dealType"),
        "valid_to": (o.get("campaignEndDate") or "")[:10] or None,
        "eans": [ext] if ext else [],
        "image": image,
        "member_price": 1 if pi.get("isMemberPrice") else 0,
        "savings": None,  # Coops dke/offers exponerar inte ordinarie pris
        "fetched_at": fetched_at,
    }
