"""ICA e-handels-crawler (handlaprivatkund.ica.se) - per-butik-HYLLPRIS via den nya ecom-plattformen.

Bakgrund: ICA tog bort pris ur globalsearch/quicksearch (~2026-06-16), så per-butik-pris (Steg 6) för ICA
måste hämtas här i stället. Se `ICA-ECOM-API.md` för endpoint-referensen. Körs PARALLELLT med den gamla
quicksearch-baserade per-butik-crawlen (`store_crawl._ica_fetch_store`, produkt-närvaro/kategori) tills vi
litar på ecom-täckningen.

Metod (ren async-httpx, INGEN browser/WAF - GET fungerar server-side med Firefox-UA + ecom-request-source):
  1. GET v1/categories -> kategoriträd -> löv-categoryId.
  2. Per löv: GET v6/product-pages?categoryId=X (hög maxPageSize -> undvik den statslöst-trasiga pageTokenen)
     -> produkter med pris/jämförpris/erbjudanden/retailerProductId.
  3. Mappa retailerProductId (== quicksearchens consumerItemId) -> gtin (bygg-lager, ej i denna fil).

Svaren bär INGEN gtin/EAN - bara retailerProductId. EAN-bryggan görs av anroparen.
"""
import asyncio
import logging

from . import apilog

log = logging.getLogger("matbutiker")

_BASE = "https://handlaprivatkund.ica.se/stores/{acct}/api"
_UA = "Mozilla/5.0 (X11; Linux x86_64; rv:152.0) Gecko/20100101 Firefox/152.0"
_HDRS = {"User-Agent": _UA, "Accept": "application/json; charset=utf-8",
         "Accept-Language": "sv-SE,sv;q=0.9,en;q=0.7", "ecom-request-source": "web"}
_PAGE_SIZE = 1000       # hög -> ryms i EN sida (pageToken paginerar inte statslöst, se ICA-ECOM-API.md)
_PAGE_PACE = 0.25       # paus mellan kategori-anrop (snäll mot ICA)


def _num(v):
    """{'amount': '27.35'} eller '27.35' -> float, annars None."""
    if isinstance(v, dict):
        v = v.get("amount")
    try:
        return float(str(v).replace(",", ".")) if v is not None else None
    except (ValueError, TypeError):
        return None


async def fetch_categories(client, acct):
    """Hämta butikens kategoriträd MED produkt-antal (`decoration=true`; `false` ger productCount=0).
    Returnerar topp-noderna (rekursiv `childCategories` behålls) - walken nedan väljer browse-nivå."""
    url = f"{_BASE.format(acct=acct)}/webproductpagews/v1/categories"
    r = await client.get(url, params={"decoration": "true", "categoryDepth": 5}, headers=_HDRS, timeout=30)
    r.raise_for_status()
    return r.json()


def _browse_plan(nodes, page_size):
    """Rekursivt: välj den kategori-nivå att browsa där productCount ryms i EN sida (pageToken paginerar
    inte statslöst). En topp-browse returnerar HELA subträdet, så vi browsar den grövsta nod som får plats;
    bara grenar > page_size descendas. Yield:ar (categoryId, name, productCount)."""
    for n in nodes or []:
        pc = n.get("productCount") or 0
        if pc == 0:
            continue
        kids = n.get("childCategories")
        if pc <= page_size or not kids:
            yield n.get("categoryId"), n.get("name"), pc
        else:
            yield from _browse_plan(kids, page_size)


def _norm_product(p):
    """decoratedProduct -> normaliserad rad. retailerProductId (== consumerItemId) är EAN-bryggan.
    Bär både ordinariepris och ev. reapris (erbjudande)."""
    up = (p.get("unitPrice") or {})
    pup = (p.get("promoPrice") or {})
    promos = p.get("promotions") or []
    return {
        "retailer_product_id": str(p.get("retailerProductId") or ""),
        "name": p.get("name"),
        "brand": p.get("brand"),
        "package": p.get("packSizeDescription"),
        "price": _num(p.get("price")),
        "comparison_value": _num(up.get("price")),
        "comparison_unit": up.get("unitName"),
        "promo_price": _num(pup) or None,
        "promo_text": (promos[0].get("description") if promos else None),
        "required_qty": (promos[0].get("requiredProductQuantity") if promos else None),
        "category_path": p.get("categoryPath") or [],
        "image": ((p.get("image") or {}).get("src")),
        "available": p.get("available"),
    }


async def fetch_category_products(client, acct, category_id, page_size=None):
    """Hämta en kategoris produkter (dekorerade med pris). Hög maxPageSize -> en sida (se pageToken-gotcha).
    Returnerar lista av normaliserade rader (deduplicerade på retailer_product_id inom kategorin)."""
    size = page_size or _PAGE_SIZE
    url = f"{_BASE.format(acct=acct)}/webproductpagews/v6/product-pages"
    params = {"categoryId": category_id, "maxPageSize": size, "maxProductsToDecorate": size,
              "tag": ["web", "category-item"]}
    r = await client.get(url, params=params, headers=_HDRS, timeout=30)
    r.raise_for_status()
    data = r.json()
    seen, out = set(), []
    for grp in data.get("productGroups") or []:
        for p in grp.get("decoratedProducts") or []:
            rid = str(p.get("retailerProductId") or "")
            if rid and rid not in seen:
                seen.add(rid)
                out.append(_norm_product(p))
    # pageToken-varning: om svaret cappades finns fler produkter vi inte får statslöst
    meta = data.get("metadata") or {}
    api_info = data.get("additionalPageInfo") or {}
    total = meta.get("totalHits") or api_info.get("totalProductCount")
    return out, total


async def fetch_store_products(client, acct, pace=None):
    """Async generator: yield:a (category_name, rows, capped) per browsad kategori för en butik.
    Browsar den grövsta kategori-nivå som ryms i en sida (se `_browse_plan`). `capped` = True om noden
    ändå gav färre än productCount (då finns produkter vi inte når statslöst)."""
    pace = _PAGE_PACE if pace is None else pace
    tree = await fetch_categories(client, acct)
    for cat_id, name, pc in _browse_plan(tree, _PAGE_SIZE):
        if not cat_id:
            continue
        try:
            rows, _total = await fetch_category_products(client, acct, cat_id)
        except Exception as e:  # noqa: BLE001
            log.warning("ica_ecom: kategori %s (%s) fel: %s", name, acct, e)
            raise
        yield name, rows, len(rows) < pc
        await asyncio.sleep(pace)
