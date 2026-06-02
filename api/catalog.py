"""Unified katalog-sök mot kedjornas NATIVA sök-API:er (hela sortimentet, ej bara offers).

Till skillnad från `database.list_products` (som söker offers-cachen, butikslokala deals) är
detta en live fan-out mot varje kedjas katalog-sök -> **nationellt/representativt hyllpris**
för hela sortimentet. En upptäckts-funktion, inte prisjämförelse (priserna är inte butiks-
lokala och inte erbjudanden). Normaliseras till en gemensam form och grupperas på EAN
cross-chain. Lidl saknas (ingen EAN i deras sök).

Per kedja en `_search_<chain>` som returnerar normaliserade item-dicts; `catalog_search`
fan-out:ar parallellt (per-kedja timeout -> delresultat om en kedja är seg/nere), grupperar
på EAN (annars (chain, namn)) och bygger CatalogProduct-formen.

Auth: ICA (token + flaggskepps-accountNumber), Coop (perso-nyckel, scrape-on-401 via
details._resolve_coop_key). City Gross + Axfood kräver ingen nyckel. Axfood-EAN resolvas
via `ean_cache` (warmad ur offers) -> många katalog-koder saknar EAN och blir fristående.
"""

import asyncio
import logging
import re

from . import categories, config, database as db, details, matching
from .adapters import ica_token

log = logging.getLogger("matbutiker")

UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
_CHAIN_TIMEOUT = 12  # per kedja; delresultat om en är seg


def _price_num(v):
    """Pris -> float. Tål tal och svenska prissträngar ('68,90 kr', '153,11 kr/kg')."""
    if isinstance(v, (int, float)):
        return float(v)
    m = re.search(r"(\d+)[.,](\d{1,2})", str(v or ""))
    if m:
        return float(f"{m.group(1)}.{m.group(2)}")
    m = re.search(r"\d+", str(v or ""))
    return float(m.group(0)) if m else None


def _origin_list(s):
    """Komma/snedstreck-separerad ursprungssträng -> lista (titelfall: 'SVERIGE'->'Sverige')."""
    if not s:
        return None
    parts = [p.strip().title() for p in re.split(r"[,/]", str(s)) if p.strip()]
    return parts or None


# --- City Gross (Loop54 search/quick, ingen auth) -----------------------------------------
_CG_UNITS = {"KGM": "kg", "LTR": "l", "GRM": "g", "PCE": "st", "MTR": "m"}
_CG_IMG_BASE = "https://www.citygross.se/images/products/"


def _norm_citygross(it):
    prices = (it.get("productStoreDetails") or {}).get("prices") or {}
    cur = prices.get("currentPrice") or {}
    price = cur.get("price")
    if price is None:
        return None
    imgs = it.get("images") or []
    origin = it.get("countryOfOrigin") or it.get("originStatement")
    return {
        "chain": "citygross",
        "ean": matching.normalize_ean(it.get("gtin")),
        "name": it.get("name"),
        "brand": (it.get("brand") or "").strip().title() or None,
        "origin": _origin_list(origin),
        "image": (_CG_IMG_BASE + imgs[0]["url"]) if imgs and imgs[0].get("url") else None,
        "category": categories.category_for("citygross", it.get("superCategory")),
        "package_size": it.get("descriptiveSize"),
        "package_value": None,
        "package_unit": None,
        "price": price,
        "comparison_value": cur.get("comparativePrice"),
        "comparison_unit": _CG_UNITS.get(cur.get("comparativePriceUnit")),
    }


async def _search_citygross(client, q, limit):
    r = await client.get(
        "https://www.citygross.se/api/v1/Loop54/search/quick",
        params={"SearchQuery": q, "skip": 0, "take": limit},
        headers={"User-Agent": UA, "Accept": "application/json"}, timeout=_CHAIN_TIMEOUT,
    )
    r.raise_for_status()
    items = ((r.json().get("searchResults") or {}).get("products")) or []
    return [n for it in items if it.get("gtin") for n in [_norm_citygross(it)] if n]


# --- Coop (personalization/search/global, perso-nyckel) -----------------------------------
def _coop_img(url):
    return (url or "").replace("http://", "https://").replace(".tiff", ".jpg") or None


def _norm_coop(it):
    base = details._parse_coop_item(it)  # origin (str) + category_raw (navCategories-topp)
    sp = it.get("salesPriceData") or {}
    cp = it.get("comparativePriceData") or {}
    cu = it.get("comparativePriceUnit") or {}
    size = it.get("packageSize")
    unit = it.get("packageSizeUnit")
    return {
        "chain": "coop",
        "ean": matching.normalize_ean(it.get("ean")),
        "name": it.get("name"),
        "brand": (it.get("manufacturerName") or "").strip() or None,
        "origin": _origin_list(base.get("origin")),
        "image": _coop_img(it.get("imageUrl")),
        "category": categories.category_from_detail("coop", base.get("category_raw")) or "ovrigt",
        "package_size": (f"{size:g} {unit}" if size and unit else (str(size) if size else None)),
        "package_value": float(size) if isinstance(size, (int, float)) else None,
        "package_unit": (unit or "").lower() or None,
        "price": sp.get("b2cPrice"),
        "comparison_value": cp.get("b2cPrice"),
        "comparison_unit": (cu.get("unit") or None),
    }


async def _search_coop(client, q, limit):
    key = await details._resolve_coop_key(client)
    items = await _coop_search_req(client, q, limit, key)
    return [n for it in items if it.get("ean") for n in [_norm_coop(it)] if n]


async def _coop_search_req(client, q, limit, key):
    import json as _json
    url = "https://external.api.coop.se/personalization/search/global"
    params = {"api-version": "v1", "store": config.COOP_DETAIL_STORE,
              "groups": "CUSTOMER_PRIVATE", "direct": "true"}
    headers = {"Ocp-Apim-Subscription-Key": key, "Content-Type": "application/json",
               "Origin": "https://www.coop.se", "Accept": "application/json", "User-Agent": UA}
    body = _json.dumps({"query": q, "resultsOptions": {"skip": 0, "take": limit}})
    r = await client.post(url, params=params, headers=headers, content=body, timeout=_CHAIN_TIMEOUT)
    if r.status_code in (401, 403):
        key = await details._resolve_coop_key(client, force=True)
        headers["Ocp-Apim-Subscription-Key"] = key
        r = await client.post(url, params=params, headers=headers, content=body, timeout=_CHAIN_TIMEOUT)
    r.raise_for_status()
    return ((r.json().get("results") or {}).get("items")) or []


# --- ICA (globalsearch quicksearch, token + accountNumber) --------------------------------
def _norm_ica(it):
    return {
        "chain": "ica",
        "ean": matching.normalize_ean(it.get("gtin")),
        "name": it.get("displayName") or it.get("title"),
        "brand": None,  # ICA-söket bär inget separat varumärke
        "origin": _origin_list(it.get("countryOfOriginName")),
        "image": it.get("image"),
        "category": categories.category_for("ica", it.get("mainCategoryName")),
        "package_size": None,
        "package_value": None,
        "package_unit": None,
        "price": _price_num(it.get("price")),
        "comparison_value": None,  # inget jämförpris i ICA-söket
        "comparison_unit": None,
    }


async def _search_ica(client, q, limit):
    token = await ica_token.get_token(client)
    acct = (db.ica_resolve_accounts() or [None])[0]
    if not acct:
        return []
    r = await client.post(
        "https://apimgw-pub.ica.se/sverige/digx/globalsearch/v1/search/quicksearch",
        json={"queryString": q, "take": limit, "offset": 0,
              "accountNumber": acct, "searchDomain": "All", "sessionId": "catalog"},
        headers={"User-Agent": UA, "Authorization": f"Bearer {token}",
                 "Content-Type": "application/json"}, timeout=_CHAIN_TIMEOUT,
    )
    r.raise_for_status()
    docs = (r.json().get("products") or {}).get("documents") or []
    return [_norm_ica(it) for it in docs if it.get("gtin")]


# --- Axfood (willys.se/hemkop.se /search, ingen auth; EAN via ean_cache) ------------------
def _norm_axfood(it, chain, ean):
    img = (it.get("image") or {})
    return {
        "chain": chain,
        "ean": ean,
        "name": it.get("name"),
        "brand": (it.get("manufacturer") or "").strip() or None,
        "origin": None,
        "image": img.get("url") if isinstance(img, dict) else None,
        "category": categories.category_for(chain, it.get("googleAnalyticsCategory") or None),
        "package_size": it.get("productLine2") or None,
        "package_value": None,
        "package_unit": None,
        "price": _price_num(it.get("priceValue") if it.get("priceValue") is not None else it.get("price")),
        "comparison_value": _price_num(it.get("comparePrice")),
        "comparison_unit": (it.get("comparePriceUnit") or None),
    }


async def _search_axfood(client, chain, q, limit):
    domain = {"willys": "www.willys.se", "hemkop": "www.hemkop.se"}[chain]
    r = await client.get(
        f"https://{domain}/search", params={"q": q, "page": 0, "size": limit},
        headers={"User-Agent": UA, "Accept": "application/json"}, timeout=_CHAIN_TIMEOUT,
    )
    r.raise_for_status()
    items = r.json().get("results") or []
    codes = [it.get("code") for it in items if it.get("code")]
    code_eans = db.get_cached_eans(codes)  # warmad ur offers; många katalogkoder saknas -> None
    out = []
    for it in items:
        ean = matching.normalize_ean(code_eans.get(it.get("code")))
        out.append(_norm_axfood(it, chain, ean))
    return out


# --- Fan-out + gruppering -----------------------------------------------------------------
_SEARCHERS = {
    "citygross": lambda cl, q, n: _search_citygross(cl, q, n),
    "coop": lambda cl, q, n: _search_coop(cl, q, n),
    "ica": lambda cl, q, n: _search_ica(cl, q, n),
    "willys": lambda cl, q, n: _search_axfood(cl, "willys", q, n),
    "hemkop": lambda cl, q, n: _search_axfood(cl, "hemkop", q, n),
}


async def _safe(chain, coro):
    try:
        return chain, await asyncio.wait_for(coro, timeout=_CHAIN_TIMEOUT + 3)
    except Exception as e:  # noqa: BLE001
        log.warning("katalog-sök %s misslyckades: %s", chain, e)
        return chain, []


async def catalog_search(client, q, per_chain=20, limit=60):
    """Live fan-out mot kedjornas katalog-sök -> grupperade CatalogProduct (EAN cross-chain).
    Delresultat om en kedja fallerar/timeoutar. per_chain = träffar per kedja att hämta."""
    results = await asyncio.gather(*(
        _safe(ch, fn(client, q, per_chain)) for ch, fn in _SEARCHERS.items()
    ))
    items = [it for _, lst in results for it in lst if it and it.get("name")]
    groups = {}
    for it in items:
        key = it["ean"] or f"{it['chain']}:{(it['name'] or '').lower()}"
        groups.setdefault(key, []).append(it)
    products = [_build_product(g) for g in groups.values()]
    # Flest kedjor först, sedan billigast, sedan namn.
    products.sort(key=lambda p: (-len(p["chains"]), p["price_min"] if p["price_min"] is not None else 9e9,
                                 (p["name"] or "").lower()))
    return products[:limit]


def _build_product(group):
    """Slå ihop en EAN-grupp (en eller flera kedjor) till CatalogProduct-formen."""
    rep = group[0]
    prices = [{"chain": g["chain"], "price": g["price"],
               "comparison_value": g["comparison_value"], "comparison_unit": g["comparison_unit"]}
              for g in group if g.get("price") is not None]
    pv = [p["price"] for p in prices]
    # Representativa fält: föredra ett item som har det satt.
    def pick(field):
        return next((g[field] for g in group if g.get(field)), None)
    cats = [g["category"] for g in group if g.get("category") and g["category"] != "ovrigt"]
    return {
        "ean": rep["ean"],
        "name": pick("name"),
        "brand": pick("brand"),
        "origin": pick("origin"),
        "image": pick("image"),
        "category": cats[0] if cats else "ovrigt",
        "package_size": pick("package_size"),
        "package_value": pick("package_value"),
        "package_unit": pick("package_unit"),
        "chains": sorted({g["chain"] for g in group}),
        "prices": sorted(prices, key=lambda p: p["price"]),
        "price_min": min(pv) if pv else None,
        "price_max": max(pv) if pv else None,
    }
