"""City Gross erbjudande-adapter (Loop54 veckans-erbjudanden, nationell katalog).

`GET /api/v1/Loop54/category/2930/products?currentWeekDiscountOnly=true` ger veckans
erbjudanden (nationella - samma för alla City Gross-butiker, ingen butiks-cookie). Varje
item bär **EAN inline** (`gtin`) + jämförpris -> går rakt in i compare. Ordinarie pris
ligger i `currentPrice`; själva erbjudandet i `promotions[].priceDetails` (kampanj-/
medlemspris). `store_id` ignoreras (samma offers för alla butiker)."""

from .base import now_iso

URL = "https://www.citygross.se/api/v1/Loop54/category/2930/products"
IMG_BASE = "https://www.citygross.se/images/products/"
UA = "matbutiker-sync/1.0"

# Loop54-enhetskoder -> våra jämförenheter.
_UNITS = {"KGM": "kg", "LTR": "l", "GRM": "g", "PCE": "st", "MTR": "m"}


async def fetch_offers(client, store_id):
    headers = {"Accept": "application/json", "User-Agent": UA,
               "Referer": "https://www.citygross.se/matvaror/veckans-erbjudande"}
    params = {"categoryName": "Veckans erbjudanden", "currentWeekDiscountOnly": "true",
              "skip": 0, "take": 300}
    r = await client.get(URL, params=params, headers=headers, timeout=30)
    r.raise_for_status()
    items = r.json().get("items") or []
    out = [_map(store_id, it) for it in items if it.get("gtin")]
    return [o for o in out if o]


def _map(store_id, it):
    prices = (it.get("productStoreDetails") or {}).get("prices") or {}
    cur = prices.get("currentPrice") or {}
    promo = (prices.get("promotions") or [{}])[0]
    pd = promo.get("priceDetails") or {}
    # Erbjudandepriset = promotionens pris (annars ordinarie currentPrice).
    price = pd.get("price") if pd else cur.get("price")
    if price is None:
        return None
    comp_v = pd.get("comparativePrice") if pd else cur.get("comparativePrice")
    comp_u = _UNITS.get(pd.get("comparativePriceUnit") if pd else cur.get("comparativePriceUnit"))
    ordinary = cur.get("price")
    savings = round(ordinary - price, 2) if (ordinary and ordinary > price) else None
    minq = promo.get("minQuantity") or 1
    price_text = f"{minq} för {price} kr" if minq and minq > 1 else f"{price} kr"
    imgs = it.get("images") or []
    return {
        "chain": "citygross",
        "store_id": str(store_id),
        "offer_id": str(it.get("id")),
        "name": it.get("name"),
        "brand": (it.get("brand") or "").strip().title() or None,
        "package": it.get("descriptiveSize"),
        "price": price,
        "price_text": price_text,
        "comparison_price": f"{comp_v} kr/{comp_u}" if comp_v and comp_u else None,
        "comparison_value": comp_v,
        "comparison_unit": comp_u,
        "category_raw": it.get("superCategory"),
        "category_id": None,
        "mechanic_type": promo.get("effectType"),
        "valid_to": (promo.get("to") or "")[:10] or None,
        "eans": [it["gtin"]],
        "image": (IMG_BASE + imgs[0]["url"]) if imgs and imgs[0].get("url") else None,
        "member_price": 1 if promo.get("membersOnly") else 0,
        "savings": savings,
        "fetched_at": now_iso(),
    }
