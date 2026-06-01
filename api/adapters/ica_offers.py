import json
import logging
import re
from datetime import datetime, timezone

log = logging.getLogger("matbutiker")

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36"
)


def _extract_array(s, key):
    """Plocka ut en balanserad JS-array `"key":[ ... ]` ur __INITIAL_DATA__-bloben."""
    i = s.find('"' + key + '":')
    if i < 0:
        return None
    i = s.index("[", i)
    depth, j, instr, esc = 0, i, False, False
    while j < len(s):
        c = s[j]
        if esc:
            esc = False
        elif c == "\\":
            esc = True
        elif c == '"':
            instr = not instr
        elif not instr:
            if c == "[":
                depth += 1
            elif c == "]":
                depth -= 1
                if depth == 0:
                    return s[i : j + 1]
        j += 1
    return None


def _price_oren(s):
    """'60:00' -> 60.0, '30:-' -> 30.0. Svensk pris-notation (':' = öre-separator)."""
    if not s:
        return None
    m = re.match(r"\s*(\d+)\s*[:.,]\s*(\d{0,2}|-)", str(s))
    if not m:
        m2 = re.match(r"\s*(\d+)\s*$", str(s))
        return float(m2.group(1)) if m2 else None
    krona = int(m.group(1))
    ore = m.group(2)
    ore = 0 if ore in ("", "-") else int(ore)
    return krona + ore / 100


def _parse_comparison(cp):
    """'60:00/kg' -> (60.0, 'kg')."""
    if not cp or "/" not in cp:
        return None, None
    val, _, unit = cp.partition("/")
    return _price_oren(val), unit.strip() or None


def parse_offers(html, chain, store_id):
    raw = _extract_array(html, "weeklyOffers")
    if not raw:
        return []
    cleaned = re.sub(r":\s*undefined", ":null", raw)
    try:
        items = json.loads(cleaned)
    except json.JSONDecodeError as e:
        log.warning("ICA offers: kunde inte parsa weeklyOffers (%s)", e)
        return []
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return [_map(o, chain, store_id, now) for o in items if o]


def _map(o, chain, store_id, fetched_at):
    d = o.get("details") or {}
    cat = o.get("category") or {}
    pm = o.get("parsedMechanics") or {}
    eans = o.get("eans") or []
    comp_val, comp_unit = _parse_comparison(o.get("comparisonPrice"))
    # Pris: value2 ur parsedMechanics för enkla erbjudanden; mechanicInfo som visningstext.
    price = _price_oren(pm.get("value2")) if pm.get("benefitType") == "FIXED" else None
    # Besparing: ordinarie pris/st * antal - erbjudandepris (hanterar multibuy).
    st = (o.get("stores") or [{}])[0] or {}
    regular = _price_oren(st.get("regularPrice"))
    qty = pm.get("quantity") or 1
    savings = round(regular * qty - price, 2) if regular is not None and price is not None else None
    image = eans[0].get("image") if eans else None
    return {
        "chain": chain,
        "store_id": str(store_id),
        "offer_id": str(o.get("id")),
        "name": d.get("name"),
        "brand": d.get("brand"),
        "package": d.get("packageInformation") or None,
        "price": price,
        "price_text": d.get("mechanicInfo"),
        "comparison_price": o.get("comparisonPrice"),
        "comparison_value": comp_val,
        "comparison_unit": comp_unit,
        "category_raw": cat.get("articleGroupName"),
        "category_id": cat.get("articleGroupId"),
        "mechanic_type": pm.get("type"),
        "valid_to": (o.get("validTo") or "")[:10] or None,
        "eans": [e.get("id") for e in eans if e.get("id")],
        "image": image,
        "member_price": None,
        "savings": savings,
        "fetched_at": fetched_at,
    }


async def fetch_offers(client, offers_url, store_id):
    """Hämta och parsa erbjudanden för en ICA-butik från dess server-renderade sida."""
    if not offers_url:
        return []
    r = await client.get(offers_url, headers={"User-Agent": UA}, timeout=30)
    r.raise_for_status()
    return parse_offers(r.text, "ica", store_id)
