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
from datetime import datetime, timezone

from . import apilog, database

log = logging.getLogger("matbutiker")


def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

_BASE = "https://handlaprivatkund.ica.se/stores/{acct}/api"
_UA = "Mozilla/5.0 (X11; Linux x86_64; rv:152.0) Gecko/20100101 Firefox/152.0"
_HDRS = {"User-Agent": _UA, "Accept": "application/json; charset=utf-8",
         "Accept-Language": "sv-SE,sv;q=0.9,en;q=0.7", "ecom-request-source": "web"}
_PAGE_SIZE = 1000       # hög -> ryms i EN sida (pageToken paginerar inte statslöst, se ICA-ECOM-API.md)
_PAGE_PACE = 0.4        # paus mellan kategori-anrop - ICA:s WAF är rate-baserad och trippar under last
                        # (challengar då butiker med 200 text/html i st.f. JSON), så håll takten låg


def _num(v):
    """{'amount': '27.35'} eller '27.35' -> float, annars None."""
    if isinstance(v, dict):
        v = v.get("amount")
    try:
        return float(str(v).replace(",", ".")) if v is not None else None
    except (ValueError, TypeError):
        return None


async def _get_json(client, url, params, retries=3):
    """GET -> JSON med retry/backoff. ICA:s WAF/rate-limit ger sporadiskt 403 (CloudFront-HTML) eller
    tomt svar under samtidig last -> retas i st.f. att fälla hela butiken. Höjer efter sista försöket."""
    last = None
    for attempt in range(retries + 1):
        try:
            r = await client.get(url, params=params, headers=_HDRS, timeout=30)
            if r.status_code == 200 and "json" in r.headers.get("content-type", ""):
                return r.json()
            last = f"HTTP {r.status_code} {r.headers.get('content-type', '')[:20]}"
        except Exception as e:  # noqa: BLE001 (transport/JSON)
            last = f"{type(e).__name__}"
        if attempt < retries:
            await asyncio.sleep(1.0 + attempt * 1.5)  # backoff: 1s, 2.5s, 4s
    raise RuntimeError(f"ica_ecom _get_json misslyckades ({last}): {url.split('/api/')[-1][:40]}")


async def fetch_categories(client, acct):
    """Hämta butikens kategoriträd MED produkt-antal (`decoration=true`; `false` ger productCount=0).
    Returnerar topp-noderna (rekursiv `childCategories` behålls) - walken nedan väljer browse-nivå."""
    url = f"{_BASE.format(acct=acct)}/webproductpagews/v1/categories"
    return await _get_json(client, url, {"decoration": "true", "categoryDepth": 5})


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
    data = await _get_json(client, url, params)
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


_STORE_CONC = 2     # LÅG cross-store-samtidighet: ICA:s WAF är rate-baserad och challengar butiker under
                    # last (fler samtidiga -> fler 200-text/html-challenges). _get_json retar transienta;
                    # persistent challengeade butiker (en minoritet) skippas och retas nästa körning.
_STORE_PACE = 0.8   # paus efter varje butik

# Parallell-fasens live-state för konsolen (per-process, som övriga crawl-states).
ECOM_STATE = {"running": False, "done": 0, "total": 0, "stores_ok": 0, "products": 0, "mapped": 0,
              "errors": 0, "current": None, "started_at": None, "finished_at": None, "last_error": None}


async def crawl_store(client, acct):
    """Crawla en ICA-butiks hela ecom-sortiment -> ica_ecom_prices (separat tabell). Mappar
    retailerProductId -> gtin via ica_cid_ean (quicksearch-byggd). Returnerar stats-dict."""
    products, ncat, capped = {}, 0, 0
    async for _name, rows, cap in fetch_store_products(client, acct):
        ncat += 1
        capped += 1 if cap else 0
        for r in rows:
            products[r["retailer_product_id"]] = r  # dedup över kategorier
    eanmap = await asyncio.to_thread(database.ica_ean_for_cids, list(products))
    for rid, r in products.items():
        r["ean"] = eanmap.get(rid)
    written = await asyncio.to_thread(database.upsert_ica_ecom_prices, acct, list(products.values()))
    return {"products": len(products), "categories": ncat, "capped": capped, "written": written,
            "priced": sum(1 for r in products.values() if r["price"] is not None),
            "mapped": sum(1 for r in products.values() if r["ean"]),
            "promos": sum(1 for r in products.values() if r["promo_price"])}


async def crawl_all_ecom(cap=None, max_age_hours=None, concurrency=None):
    """Kör ecom-pris-crawlen för de enabled+frågbara ICA-butikerna (bunden samtidighet). Skriver till
    ica_ecom_prices + en crawl_runs-rad (kind='ecom_prices'). Parallellt med quicksearch-crawlen; rör
    inte dess rotation/last_crawled. `max_age_hours=None/0` = alla valda butiker."""
    st = ECOM_STATE
    if st["running"]:
        return {"status": "running"}
    queue = [a for _, a in database.stores_to_crawl(chain="ica", cap=cap, max_age_hours=max_age_hours)]
    st.update(running=True, done=0, total=len(queue), stores_ok=0, products=0, mapped=0, errors=0,
              current=None, last_error=None, started_at=_now(), finished_at=None)
    sem = asyncio.Semaphore(concurrency or _STORE_CONC)
    try:
        async with apilog.make_client(follow_redirects=True) as client:
            async def one(acct):
                async with sem:
                    try:
                        r = await crawl_store(client, acct)
                        st["stores_ok"] += 1
                        st["products"] += r["products"]
                        st["mapped"] += r["mapped"]
                        st["current"] = f"ICA {acct}: {r['products']} prod ({r['mapped']} mappade)"
                    except Exception as e:  # noqa: BLE001
                        st["errors"] += 1
                        st["last_error"] = f"{acct}: {type(e).__name__}: {e}"[:200]
                        log.warning("ica_ecom: butik %s fel: %s", acct, e)
                    finally:
                        st["done"] += 1
                    await asyncio.sleep(_STORE_PACE)
            await asyncio.gather(*(one(a) for a in queue), return_exceptions=True)
    finally:
        st.update(running=False, finished_at=_now(), current=None)
        await asyncio.to_thread(
            database.record_crawl_run, "ecom_prices", "ica", started=st["started_at"],
            finished=st["finished_at"], status=("ok_med_fel" if st["errors"] else "ok"),
            rows=st["products"], changed=st["mapped"], errors=st["errors"],
            stores_ok=st["stores_ok"], stores_total=st["total"], last_error=st["last_error"])
    return st
