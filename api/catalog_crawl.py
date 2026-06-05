"""Fulla sortiment-crawl (steg 5): walk:ar kedjornas kategoriträd och persistar HELA sortimentet
i `catalog_products` (ej bara offers). Proaktiv, rate-limitad, inkrementell - `CRAWL_STATE`
uppdateras per sida så konsolen kan visa produkter strömma in live.

Implementerat: City Gross (Loop54 kategoriträd), ICA (wildcard '*' + offset), Coop (by-attribute
+ harvestade departement-rötter), Willys/Hemköp (Axfood /c/<slug> + leftMenu/categorytree; EAN
ur ean_cache, NULL annars). Lidl saknar EAN -> utesluts. Kedjorna crawlas PARALLELLT (egen host
var -> ingen rate-limit-konflikt). ALLA EAN-bärande kedjor nu täckta.
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
_IMPLEMENTED = ("citygross", "ica", "coop", "willys", "hemkop")
_RECENT_MAX = 60  # live-feed-buffert: senaste ingestade produkter (klient-kön tömmer en i taget)
_RECENT_PER_CHAIN = 40  # per-kedje-buffert (för round-robin-rättvis feed)
_RECENT_BY_CHAIN = {}  # {chain: [items]} - modulnivå, skickas EJ till klienten


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


def _round_robin_feed():
    """Interleava per-kedje-buffertarna (rank för rank) -> rättvis feed, ingen kedja dominerar."""
    lists = [v for v in _RECENT_BY_CHAIN.values() if v]
    out, idx = [], 0
    while lists and len(out) < _RECENT_MAX:
        progressed = False
        for lst in lists:
            if idx < len(lst):
                out.append(lst[idx])
                progressed = True
                if len(out) >= _RECENT_MAX:
                    break
        if not progressed:
            break
        idx += 1
    return out


def _feed(chain, rows):
    """Lägg senaste ingestade produkter i kedjans buffert; bygg den gemensamma feeden round-robin."""
    items = [{"chain": chain, "name": r["name"], "ean": r["ean"]} for r in rows if r.get("name")]
    if not items:
        return
    buf = _RECENT_BY_CHAIN.setdefault(chain, [])
    buf[:0] = items[::-1]          # nyast först
    del buf[_RECENT_PER_CHAIN:]    # capa per kedja
    CRAWL_STATE["recent"] = _round_robin_feed()


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


def _ica_row(doc, acct):
    # Återanvänd katalog-sökets normalisering; lägg product_id (gtin) + category_raw (mainCategoryName).
    # ICA-pris är butiksspecifikt (per accountNumber) -> tagga med butiksprofilen vi crawlade med.
    return {**catalog._norm_ica(doc), "product_id": str(doc.get("gtin") or ""),
            "category_raw": doc.get("mainCategoryName"), "store": acct}


# ICA globalsearch cappar offset HÅRT vid 20000 -> '*' når bara de första 20k av ev. ~45k. Vägen förbi:
# paginera även per huvudkategori (queryString=kategorinamn -> eget offset-rum < 20k), unionen dedupas på
# gtin. Summan av kategori-counts > totalHits (överlapp) -> full täckning. Kategorinamnen matchar ICA:s
# mainCategoryName (text-relevans, inte strikt filter - därför union med '*' för det som ingen kategori fångar).
_ICA_CATEGORIES = (
    "Frukt & grönt", "Mejeri", "Ost", "Kött & fågel", "Chark", "Fisk & skaldjur", "Bröd & kakor",
    "Skafferi", "Dryck", "Fryst", "Glass", "Godis & snacks", "Färdigmat", "Vegetariskt", "Bak",
    "Blommor & växter", "Hem & hushåll", "Barn", "Djur", "Hälsa & skönhet",
)


async def _ica_fetch_store(client, acct, token, limit_pages=None, pace=None, deep=True):
    """Async generator: paginerar en ICA-butiks katalog och yield:ar (rows, store_total, page) per sida.
    rows = _ica_row-normaliserade, deduplicerade på gtin ÖVER hela walken. `deep=True` walkar '*' + varje
    huvudkategori (förbi 20000-offset-cappen, ~hela sortimentet); `deep=False` bara '*' (snabbt, max 20k).
    DELAD walk - master-crawlen (-> catalog_products) och per-butik-crawlern (-> catalog_store_prices) ger
    samma walk olika write-target. `store_total` = '*'-totalHits (progress-nämnare). Höjer vid HTTP-fel."""
    seen, page, size = set(), 0, config.CATALOG_CRAWL_PAGE
    pace = config.CATALOG_CRAWL_PACE if pace is None else pace
    store_total = 0
    queries = ["*"] + (list(_ICA_CATEGORIES) if deep else [])
    for qs in queries:
        offset = 0
        while True:
            r = await client.post(_ICA_URL, json={
                "queryString": qs, "take": size, "offset": offset, "accountNumber": acct,
                "searchDomain": "All", "sessionId": "catalog-crawl"},
                headers={"User-Agent": _UA, "Authorization": f"Bearer {token}",
                         "Content-Type": "application/json"}, timeout=30)
            r.raise_for_status()
            prods = r.json().get("products") or {}
            docs = prods.get("documents") or []
            qtotal = (prods.get("stats") or {}).get("totalHits") or 0
            if qs == "*":
                store_total = qtotal
            if not docs:
                break
            rows = []
            for d in docs:
                pid = str(d.get("gtin") or "")
                if pid and pid not in seen:
                    seen.add(pid)
                    rows.append(_ica_row(d, acct))
            yield rows, store_total, page
            offset += len(docs)
            page += 1
            await asyncio.sleep(pace)
            if offset >= qtotal or (limit_pages and page >= limit_pages):
                break
        if limit_pages and page >= limit_pages:
            break


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
    try:
        async for rows, total, page in _ica_fetch_store(client, acct, token, limit_pages):
            st["total"] = total
            pages_full = max(1, -(-total // config.CATALOG_CRAWL_PAGE))  # antal sidor för hela katalogen
            st["categories_total"] = min(limit_pages, pages_full) if limit_pages else pages_full
            if rows:
                new, known, changed = database.catalog_upsert("ica", rows)
                st["new"] += new; st["known"] += known; st["changed"] += changed
                st["products"] += len(rows)
                _feed("ica", rows)
            st["categories_done"] = page + 1
            st["current_category"] = f"{st['products']} / {total} produkter"
    except Exception as e:  # noqa: BLE001
        st["errors"] += 1
        if len(st["last_errors"]) < 8:
            st["last_errors"].append(str(e))
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
    # Coop-pris/sortiment är butiksspecifikt (perso-API:t scopar på ledger) -> tagga med butiken.
    return {**base, "product_id": str(base.get("ean") or ""), "category_raw": raw, "store": config.COOP_DETAIL_STORE}


def _piggyback_coop_info(items):
    """Spara product_info (partial) ur Coop-items vi ändå hämtade i crawlen - gratis ingredienser/
    näring. Skip-if-fresh (batchat); on-demand-öppning uppgraderar till full korsskällig merge."""
    cand = {}
    for it in items:
        ean = matching.normalize_ean(it.get("ean"))
        if not ean:
            continue
        part = details._parse_coop_item(it)
        if part.get("ingredients") or part.get("description"):
            cand[ean] = part
    if not cand:
        return
    database.archive_product_info(cand.items())  # innehållshistorik (append-on-change)
    fresh = database.product_info_fresh_set(cand.keys())
    for ean, part in cand.items():
        if ean not in fresh:
            database.save_product_info(ean, details._merge([part]), partial=True)


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
        _piggyback_coop_info(items)  # gratis product_info (partial) ur samma Coop-svar
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


# --- Axfood (Willys/Hemköp): /c/<slug>-paginering + leftMenu/categorytree -----------------
# Olika API-prefix och kategori-id/slugs per sajt (delar backend men ej taxonomi). EAN ej inline
# -> slås upp ur ean_cache (gratis; NULL annars, fylls av warm_axfood_eans över tid).
_AXFOOD_BASE = {"willys": "https://www.willys.se/axfood/rest/v1", "hemkop": "https://www.hemkop.se"}
_AXFOOD_TREE = {
    "willys": ("https://www.willys.se/axfood/rest/v1/leftMenu/categorytree",
               {"storeId": "2110", "deviceType": "OTHER"}),
    "hemkop": ("https://www.hemkop.se/leftMenu/categorytree", {}),
}


async def _axfood_categories(client, chain):
    """Topp-avdelningarna (rotens direkta barn) -> [(slug, titel)]."""
    url, params = _AXFOOD_TREE[chain]
    j = await _get_json(client, url, params)
    root = j[0] if isinstance(j, list) else j
    out = []
    for ch in root.get("children") or []:
        if ch.get("url") and ch.get("valid", True):
            out.append((ch["url"].strip("/").split("/")[-1], ch.get("title") or ch["url"]))
    return out


def _axfood_row(it, ean, cat_fallback):
    img = it.get("image") or {}
    return {
        "product_id": str(it.get("code") or ""),
        "ean": ean,
        "name": it.get("name"),
        "brand": (it.get("manufacturer") or "").strip() or None,
        "origin": None,
        "image": img.get("url") if isinstance(img, dict) else None,
        "category_raw": it.get("googleAnalyticsCategory") or cat_fallback,
        "package_size": it.get("productLine2") or None,
        "package_value": None, "package_unit": None,
        "price": catalog._price_num(it.get("priceValue") if it.get("priceValue") is not None else it.get("price")),
        "comparison_value": catalog._price_num(it.get("comparePrice")),
        "comparison_unit": (it.get("comparePriceUnit") or "").lower() or None,
    }


async def _axfood_browse(client, chain, slug, title, st, seen, max_pages):
    base, page, size = _AXFOOD_BASE[chain], 0, config.CATALOG_CRAWL_PAGE
    while True:
        j = await _get_json(client, f"{base}/c/{slug}", {"page": page, "size": size, "sort": ""})
        items = j.get("results") or []
        npages = (j.get("pagination") or {}).get("numberOfPages") or 0
        if not items:
            break
        codes = [str(it.get("code")) for it in items if it.get("code")]
        code_eans = database.get_cached_eans(codes)  # gratis ean_cache-uppslag
        rows = []
        for it in items:
            code = str(it.get("code") or "")
            if not code or code in seen:
                continue
            seen.add(code)
            rows.append(_axfood_row(it, code_eans.get(code) or None, title))
        if rows:
            new, known, changed = database.catalog_upsert(chain, rows)
            st["new"] += new; st["known"] += known; st["changed"] += changed
            st["products"] += len(rows)
            _feed(chain, rows)
        page += 1
        await asyncio.sleep(config.CATALOG_CRAWL_PACE)
        if page >= npages or (max_pages and page >= max_pages):
            break


async def _crawl_axfood(client, limit_categories, chain):
    st = CRAWL_STATE["chains"][chain]
    started = _now()
    st.update(status="running", started_at=started, finished_at=None, limited=bool(limit_categories))
    cats = await _axfood_categories(client, chain)
    if limit_categories:
        cats = cats[:limit_categories]
    st["categories_total"] = len(cats)
    seen = set()
    max_pages = 1 if limit_categories else None
    for slug, title in cats:
        st["current_category"] = title
        try:
            await _axfood_browse(client, chain, slug, title, st, seen, max_pages)
        except Exception as e:  # noqa: BLE001
            st["errors"] += 1
            if len(st["last_errors"]) < 8:
                st["last_errors"].append(f"{title}: {e}")
            log.warning("katalog-crawl %s/%s misslyckades: %s", chain, title, e)
        st["categories_done"] += 1
    st["current_category"] = None
    if not limit_categories:
        database.catalog_mark_unseen(chain, started)
    st["status"] = "ok" if not st["errors"] else "ok_med_fel"
    st["finished_at"] = _now()


async def _crawl_willys(client, limit):
    await _crawl_axfood(client, limit, "willys")


async def _crawl_hemkop(client, limit):
    await _crawl_axfood(client, limit, "hemkop")


_CRAWLERS = {"citygross": _crawl_citygross, "ica": _crawl_ica, "coop": _crawl_coop,
             "willys": _crawl_willys, "hemkop": _crawl_hemkop}


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
    _RECENT_BY_CHAIN.clear()
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
