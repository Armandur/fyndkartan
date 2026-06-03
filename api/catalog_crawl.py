"""Fulla sortiment-crawl (steg 5): walk:ar kedjornas kategoriträd och persistar HELA sortimentet
i `catalog_products` (ej bara offers). Proaktiv, rate-limitad, inkrementell - `CRAWL_STATE`
uppdateras per sida så konsolen kan visa produkter strömma in live.

Just nu: City Gross (Loop54). Övriga kedjor kommer (Coop/ICA/Axfood); Lidl saknar EAN -> utesluts.
City Gross: enumerera kategorier via /api/v1/Navigation (Matvaror-barn med categoryPageId),
paginera category/{id}/products (totalCount/totalPages), normalisera, upserta batchvis.
"""
import asyncio
import json
import logging
from datetime import datetime, timezone

from . import apilog, catalog, config, database, details, matching
from .adapters import ica_token

log = logging.getLogger("matbutiker")


def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

CATALOG_CHAINS = ("citygross", "coop", "ica", "willys", "hemkop")
_IMPLEMENTED = ("citygross", "ica", "coop")
_RECENT_MAX = 14  # live-feed: senast ingestade produkter


def _blank_chain():
    return {"status": "idle", "categories_done": 0, "categories_total": 0, "total": 0,
            "products": 0, "new": 0, "known": 0, "changed": 0, "errors": 0,
            "current_category": None, "last_errors": [],
            "started_at": None, "finished_at": None, "limited": False}


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
    started = _now()
    st.update(status="running", started_at=started, finished_at=None, limited=bool(limit_categories))
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
    st["finished_at"] = _now()


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
    started = _now()
    st.update(status="running", started_at=started, finished_at=None, limited=bool(limit_pages))
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
        pages_full = max(1, -(-total // size))  # antal sidor för hela katalogen
        st["categories_total"] = min(limit_pages, pages_full) if limit_pages else pages_full
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
    st["finished_at"] = _now()


# --- Coop (personalization by-attribute; departement-rötter harvestas ur navCategories) ------
_COOP_BY_ATTR = "https://external.api.coop.se/personalization/search/entities/by-attribute"
_COOP_SEARCH = "https://external.api.coop.se/personalization/search/global"
# Breda sök-termer för att harvesta departement-rötterna (kod = navCategories-rot, tom superCategories).
_COOP_HARVEST_Q = ["mjölk", "ost", "kött", "kyckling", "fisk", "bröd", "äpple", "godis", "öl",
                   "vatten", "kaffe", "pasta", "schampo", "tvättmedel", "blöja", "hundmat",
                   "vitamin", "glass", "ägg", "chips", "blomma", "tandkräm", "toapapper", "lök",
                   "yoghurt", "ris", "smör", "juice", "korv", "sylt"]
_COOP_ROOTS = {}  # cachad {kod: namn} departement-lista (harvestas en gång, återanvänds)



def _coop_params():
    return {"api-version": "v1", "store": config.COOP_DETAIL_STORE, "groups": "CUSTOMER_PRIVATE",
            "device": "desktop", "direct": "false"}


async def _coop_post(client, url, params, payload):
    """POST mot Coop perso-API med skanner-säker nyckel (cachad; force-skrapas vid 401/403)."""
    key = await details._resolve_coop_key(client)
    H = {"Ocp-Apim-Subscription-Key": key, "Content-Type": "application/json",
         "Origin": "https://www.coop.se", "Accept": "application/json", "User-Agent": _UA}
    body = json.dumps(payload)
    r = await client.post(url, params=params, headers=H, content=body, timeout=30)
    if r.status_code in (401, 403):
        H["Ocp-Apim-Subscription-Key"] = await details._resolve_coop_key(client, force=True)
        r = await client.post(url, params=params, headers=H, content=body, timeout=30)
    r.raise_for_status()
    return r.json()


def _coop_root(navcats):
    for nc in navcats or []:
        node = nc
        while node.get("superCategories"):
            node = node["superCategories"][0]
        if node.get("code"):
            yield node["code"], node.get("name") or node["code"]


async def _coop_harvest_roots(client):
    """Departement-rötter ur produkternas navCategories via breda sökningar (cachas)."""
    if _COOP_ROOTS:
        return _COOP_ROOTS
    for q in _COOP_HARVEST_Q:
        try:
            j = await _coop_post(client, _COOP_SEARCH, {k: v for k, v in _coop_params().items() if k != "device"},
                                 {"query": q, "resultsOptions": {"skip": 0, "take": 25}})
            for it in (j.get("results") or {}).get("items") or []:
                for code, name in _coop_root(it.get("navCategories")):
                    _COOP_ROOTS[code] = name
        except Exception as e:  # noqa: BLE001
            log.warning("Coop root-harvest q=%s misslyckades: %s", q, e)
    return _COOP_ROOTS


def _coop_row(it):
    base = catalog._norm_coop(it)
    raw = details._parse_coop_item(it).get("category_raw")
    return {**base, "product_id": str(base.get("ean") or ""), "category_raw": raw}


async def _coop_browse(client, code, st, seen, max_pages):
    skip, page, size = 0, 0, config.CATALOG_CRAWL_PAGE
    while True:
        j = await _coop_post(client, _COOP_BY_ATTR, _coop_params(), {
            "attribute": {"name": "categoryIds", "value": str(code)},
            "resultsOptions": {"skip": skip, "take": size, "sortBy": [], "facets": []},
            "customData": {"getEntitiesByAttributeABTest": False, "consent": False}})
        res = j.get("results") or {}
        items = res.get("items") or []
        total = res.get("count") or 0
        if not items:
            break
        rows = []
        for it in items:
            ean = matching.normalize_ean(it.get("ean"))
            if ean and ean not in seen:
                seen.add(ean)
                rows.append(_coop_row(it))
        if rows:
            new, known, changed = database.catalog_upsert("coop", rows)
            st["new"] += new; st["known"] += known; st["changed"] += changed
            st["products"] += len(rows)
            _feed("coop", rows)
        skip += len(items)
        page += 1
        await asyncio.sleep(config.CATALOG_CRAWL_PACE)
        if skip >= total or (max_pages and page >= max_pages):
            break


async def _crawl_coop(client, limit_categories):
    """Coop: harvesta departement-rötter (cachat), browsa varje via by-attribute + skip/take.
    `limit_categories` = max antal departement (test cappar dessutom till 1 sida/departement)."""
    st = CRAWL_STATE["chains"]["coop"]
    started = _now()
    st.update(status="running", started_at=started, finished_at=None, limited=bool(limit_categories))
    roots = await _coop_harvest_roots(client)
    codes = list(roots)
    if limit_categories:
        codes = codes[:limit_categories]
    st["categories_total"] = len(codes)
    seen = set()
    max_pages = 1 if limit_categories else None  # test: 1 sida/departement för snabbhet
    for code in codes:
        st["current_category"] = roots.get(code)
        try:
            await _coop_browse(client, code, st, seen, max_pages)
        except Exception as e:  # noqa: BLE001
            st["errors"] += 1
            if len(st["last_errors"]) < 8:
                st["last_errors"].append(f"{roots.get(code)}: {e}")
            log.warning("katalog-crawl coop/%s misslyckades: %s", roots.get(code), e)
        st["categories_done"] += 1
    st["current_category"] = None
    if not limit_categories:
        database.catalog_mark_unseen("coop", started)
    st["status"] = "ok" if not st["errors"] else "ok_med_fel"
    st["finished_at"] = _now()


_CRAWLERS = {"citygross": _crawl_citygross, "ica": _crawl_ica, "coop": _crawl_coop}


async def crawl_all(limit_categories=None, chains=None):
    """Crawla implementerade kedjor PARALLELLT (varje kedja egen host -> ingen rate-limit-konflikt;
    total tid = långsammaste kedjan, ej summan). `chains` = delmängd (default alla implementerade).
    `limit_categories` cappar antal kategorier/sidor per kedja (snabbtest av visualiseringen)."""
    if CRAWL_STATE["running"]:
        return CRAWL_STATE
    targets = [c for c in _IMPLEMENTED if not chains or c in chains]
    if not targets:
        return CRAWL_STATE
    CRAWL_STATE.update(running=True, started_at=_now(), finished_at=None, recent=[])
    for c in targets:
        CRAWL_STATE["chains"][c] = _blank_chain()
    try:
        async with apilog.make_client(follow_redirects=True) as client:
            # Parallellt: olika hostar, så ingen kedja hamras hårdare; DB-upserts serialiseras
            # ändå av event-loopen (synkrona) -> ingen write-contention.
            await asyncio.gather(*(_CRAWLERS[c](client, limit_categories) for c in targets))
    finally:
        CRAWL_STATE.update(running=False, finished_at=_now())
    log.info("Katalog-crawl klar: %s", {c: {k: CRAWL_STATE["chains"][c][k]
             for k in ("products", "new", "known", "changed", "errors")} for c in targets})
    return CRAWL_STATE
