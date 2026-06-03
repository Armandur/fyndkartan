"""Fulla sortiment-crawl (steg 5): walk:ar kedjornas kategoriträd och persistar HELA sortimentet
i `catalog_products` (ej bara offers). Proaktiv, rate-limitad, inkrementell - `CRAWL_STATE`
uppdateras per sida så konsolen kan visa produkter strömma in live.

Just nu: City Gross (Loop54). Övriga kedjor kommer (Coop/ICA/Axfood); Lidl saknar EAN -> utesluts.
City Gross: enumerera kategorier via /api/v1/Navigation (Matvaror-barn med categoryPageId),
paginera category/{id}/products (totalCount/totalPages), normalisera, upserta batchvis.
"""
import asyncio
import logging
from datetime import datetime, timezone

from . import apilog, catalog, config, database, matching
from .adapters import ica_token

log = logging.getLogger("matbutiker")


def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

CATALOG_CHAINS = ("citygross", "coop", "ica", "willys", "hemkop")
_IMPLEMENTED = ("citygross", "ica")
_RECENT_MAX = 14  # live-feed: senast ingestade produkter


def _blank_chain():
    return {"status": "idle", "categories_done": 0, "categories_total": 0, "total": 0,
            "products": 0, "new": 0, "known": 0, "changed": 0, "errors": 0,
            "current_category": None, "last_errors": []}


CRAWL_STATE = {
    "running": False, "started_at": None, "finished_at": None, "recent": [],
    "chains": {c: _blank_chain() for c in CATALOG_CHAINS},
}

_UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
       "Chrome/124 Safari/537.36")
_H = {"User-Agent": _UA, "Accept": "application/json"}
_CG_BASE = "https://www.citygross.se/api/v1"
_CG_UNITS = {"KGM": "kg", "LTR": "l", "GRM": "g", "PCE": "st", "MTR": "m"}
_CG_IMG = "https://www.citygross.se/images/products/"


async def _get_json(client, url, params):
    """GET med retry + exponentiell back-off."""
    for attempt in range(config.CATALOG_CRAWL_RETRIES):
        try:
            r = await client.get(url, params=params, headers=_H, timeout=30)
            r.raise_for_status()
            return r.json()
        except Exception:  # noqa: BLE001
            if attempt + 1 >= config.CATALOG_CRAWL_RETRIES:
                raise
            await asyncio.sleep(config.CATALOG_CRAWL_BACKOFF * (2 ** attempt))


def _find(node, name):
    if isinstance(node, dict):
        if node.get("name") == name:
            return node
        for ch in node.get("children") or []:
            hit = _find(ch, name)
            if hit:
                return hit
    return None


def _cg_row(it):
    cur = ((it.get("productStoreDetails") or {}).get("prices") or {}).get("currentPrice") or {}
    imgs = it.get("images") or []
    origin = it.get("countryOfOrigin") or it.get("originStatement")
    return {
        "product_id": it.get("id"),
        "ean": matching.normalize_ean(it.get("gtin")),
        "name": it.get("name"),
        "brand": (it.get("brand") or "").strip().title() or None,
        "origin": [origin] if origin else None,
        "image": (_CG_IMG + imgs[0]["url"]) if imgs and imgs[0].get("url") else None,
        "category_raw": it.get("superCategory"),
        "package_size": it.get("descriptiveSize"),
        "package_value": None, "package_unit": None,
        "price": cur.get("price"),
        "comparison_value": cur.get("comparativePrice"),
        "comparison_unit": _CG_UNITS.get(cur.get("comparativePriceUnit")),
    }


async def _cg_categories(client):
    """Topp-matvarukategorier (Matvaror-barn med categoryPageId) -> [(id, namn)]."""
    j = await _get_json(client, f"{_CG_BASE}/Navigation", {})
    matv = _find(j.get("data", {}).get("tree", {}), "Matvaror") or {}
    out = []
    for ch in matv.get("children") or []:
        link = ch.get("link") or {}
        if ch.get("type") == "ProductCategoryPage" and link.get("categoryPageId") and ch.get("visible", True):
            out.append((link["categoryPageId"], ch.get("name")))
    return out


def _feed(chain, rows):
    """Lägg senaste ingestade produkter överst i live-feeden."""
    items = [{"chain": chain, "name": r["name"], "ean": r["ean"]} for r in rows if r.get("name")]
    CRAWL_STATE["recent"] = (items[::-1] + CRAWL_STATE["recent"])[:_RECENT_MAX]


async def _cg_crawl_category(client, cid, st, seen):
    """`seen` = produkt-id:n redan processade DENNA körning (kampanjkategorier överlappar
    departments) -> dubbletter hoppas så räknaren = distinkta produkter, inte rader."""
    skip = 0
    while True:
        j = await _get_json(client, f"{_CG_BASE}/Loop54/category/{cid}/products",
                            {"skip": skip, "take": config.CATALOG_CRAWL_PAGE})
        items = j.get("items") or []
        total = j.get("totalCount") or 0
        if not items:
            break
        rows = []
        for it in items:
            pid = str(it.get("id") or "")
            if pid and pid not in seen:
                seen.add(pid)
                rows.append(_cg_row(it))
        if rows:
            new, known, changed = database.catalog_upsert("citygross", rows)
            st["new"] += new
            st["known"] += known
            st["changed"] += changed
            st["products"] += len(rows)
            _feed("citygross", rows)
        skip += len(items)
        await asyncio.sleep(config.CATALOG_CRAWL_PACE)
        if skip >= total:
            break


async def _crawl_citygross(client, limit_categories):
    st = CRAWL_STATE["chains"]["citygross"]
    st.update(status="running", categories_done=0, categories_total=0, products=0,
              new=0, known=0, changed=0, errors=0, current_category=None, last_errors=[])
    started = _now()
    seen = set()  # distinkta produkt-id:n denna körning (kampanjkategorier överlappar)
    cats = await _cg_categories(client)
    if limit_categories:
        cats = cats[:limit_categories]
    st["categories_total"] = len(cats)
    for cid, name in cats:
        st["current_category"] = name
        try:
            await _cg_crawl_category(client, cid, st, seen)
        except Exception as e:  # noqa: BLE001
            st["errors"] += 1
            if len(st["last_errors"]) < 8:
                st["last_errors"].append(f"{name}: {e}")
            log.warning("katalog-crawl citygross/%s misslyckades: %s", name, e)
        st["categories_done"] += 1
    st["current_category"] = None
    if not limit_categories:  # full crawl -> markera utgångna; partiell skulle felmarkera
        database.catalog_mark_unseen("citygross", started)
    st["status"] = "ok" if not st["errors"] else "ok_med_fel"


# --- ICA (globalsearch quicksearch; wildcard '*' -> hela katalogen, offset-paginering) -------
_ICA_URL = "https://apimgw-pub.ica.se/sverige/digx/globalsearch/v1/search/quicksearch"


def _ica_row(doc):
    # Återanvänd katalog-sökets normalisering; lägg product_id (gtin) + category_raw (mainCategoryName).
    return {**catalog._norm_ica(doc), "product_id": str(doc.get("gtin") or ""),
            "category_raw": doc.get("mainCategoryName")}


async def _crawl_ica(client, limit_pages):
    """ICA har inget kategoriträd som behövs - wildcard '*' + offset paginerar hela katalogen
    (~20k produkter). `limit_pages` (=limit_categories) cappar antal sidor för snabbtest."""
    st = CRAWL_STATE["chains"]["ica"]
    st["status"] = "running"
    started = _now()
    try:
        token = await ica_token.get_token(client)
    except Exception as e:  # noqa: BLE001
        st["status"] = "ok_med_fel"; st["last_errors"].append(f"token: {e}"); return
    acct = (database.ica_resolve_accounts() or [None])[0]
    if not acct:
        st["status"] = "ok_med_fel"; st["last_errors"].append("ingen ICA-butiksprofil"); return
    seen, offset, page, size = set(), 0, 0, config.CATALOG_CRAWL_PAGE
    while True:
        try:
            r = await client.post(_ICA_URL, json={
                "queryString": "*", "take": size, "offset": offset, "accountNumber": acct,
                "searchDomain": "All", "sessionId": "catalog-crawl"},
                headers={"User-Agent": _UA, "Authorization": f"Bearer {token}",
                         "Content-Type": "application/json"}, timeout=30)
            r.raise_for_status()
            prods = r.json().get("products") or {}
        except Exception as e:  # noqa: BLE001
            st["errors"] += 1
            if len(st["last_errors"]) < 8:
                st["last_errors"].append(f"offset {offset}: {e}")
            break
        docs = prods.get("documents") or []
        total = (prods.get("stats") or {}).get("totalHits") or 0
        st["total"] = total
        st["categories_total"] = max(1, -(-total // size))  # = antal sidor
        if not docs:
            break
        rows = []
        for d in docs:
            pid = str(d.get("gtin") or "")
            if pid and pid not in seen:
                seen.add(pid)
                rows.append(_ica_row(d))
        if rows:
            new, known, changed = database.catalog_upsert("ica", rows)
            st["new"] += new; st["known"] += known; st["changed"] += changed
            st["products"] += len(rows)
            _feed("ica", rows)
        offset += len(docs)
        page += 1
        st["categories_done"] = page
        st["current_category"] = f"{st['products']} / {total} produkter"
        await asyncio.sleep(config.CATALOG_CRAWL_PACE)
        if offset >= total or (limit_pages and page >= limit_pages):
            break
    st["current_category"] = None
    if not limit_pages:
        database.catalog_mark_unseen("ica", started)
    if st["status"] == "running":
        st["status"] = "ok" if not st["errors"] else "ok_med_fel"


async def crawl_all(limit_categories=None):
    """Crawla alla implementerade kedjor sekventiellt (snällt + tydlig progress). `limit_categories`
    cappar antal kategorier per kedja (för snabb test av visualiseringen)."""
    if CRAWL_STATE["running"]:
        return CRAWL_STATE
    CRAWL_STATE.update(running=True, started_at=_now(), finished_at=None, recent=[])
    for c in CATALOG_CHAINS:
        CRAWL_STATE["chains"][c] = _blank_chain()
    try:
        async with apilog.make_client(follow_redirects=True) as client:
            await _crawl_citygross(client, limit_categories)
            await _crawl_ica(client, limit_categories)
            # TODO(steg 5): Coop/Axfood-crawlers här.
    finally:
        CRAWL_STATE.update(running=False, finished_at=_now())
    log.info("Katalog-crawl klar: %s", {c: {k: CRAWL_STATE["chains"][c][k]
             for k in ("products", "new", "known", "changed", "errors")} for c in _IMPLEMENTED})
    return CRAWL_STATE
