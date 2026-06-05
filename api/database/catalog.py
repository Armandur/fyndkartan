"""Fulla sortiment (steg 5): persistent produktkatalog per kedja (`catalog_products`).
Crawlen (api/catalog_crawl.py) upsertar hit; `catalog_browse` läser EAN-grupperat cross-chain."""
import json

from ._conn import _now, get_conn
from .ean import get_axfood_origins
from .offers import eans_on_offer_at_stores, norm_origin, normalized_package, offers_for_eans, on_offer_eans
from .products import get_product_origins, get_product_diets
from ..categories import category_for, category_from_detail
from ..matching import _norm_unit, normalize_ean
from .. import countries, manufacturers

_CAT_COLS = ("product_id", "ean", "name", "brand", "image", "origin", "price",
             "comparison_value", "comparison_unit", "package_size", "package_value",
             "package_unit", "category_raw")

_BROWSE_SQL = ("SELECT chain, product_id, ean, name, brand, image, origin, price, comparison_value, "
               "comparison_unit, package_size, package_value, package_unit, category_raw, store, "
               "price_min, price_max, price_stores "  # Steg 6: per-butik-prisaggregat (ICA/Coop-intervall)
               "FROM catalog_products")
_CATALOG_VER = 0                            # bumpas vid varje skrivning till catalog_products (crawl)
_BROWSE_IDX = {"ver": -1, "groups": None}   # cachad EAN-/namn-gruppering (map-oberoende)


def bump_catalog_version():
    """Invalidera browse-/summary-cachen efter en extern catalog_products-skrivning (t.ex.
    store_prices.recompute_store_aggregates som inte går via catalog_upsert)."""
    global _CATALOG_VER
    _CATALOG_VER += 1


def _group_rows(rows):
    """EAN-nyckel (cross-chain) -> lista medlems-dicts. När EAN saknas (oresolvad Axfood-kod):
    nyckel = (kedja, product_id) så varje distinkt katalograd blir egen produkt - INTE (kedja, namn),
    som felaktigt slog ihop olika produkter med samma namn (t.ex. flera 'Sourcream Onion Chips' i
    olika storlekar/märken)."""
    groups = {}
    for r in rows:
        key = r["ean"] or f"{r['chain']}:{r['product_id']}"
        groups.setdefault(key, []).append(r)
    return groups


def _browse_groups():
    """Cachad EAN-/namn-gruppering av HELA katalogen (available=1). Den dyra biten (~74k rader +
    dict + gruppering, ~700ms) byggs EN gång och återanvänds tills katalogen ändras (crawlen bumpar
    _CATALOG_VER). Grupperingen är kategori-map-oberoende -> map-ändringar slår igenom direkt (kategori
    härleds vid läs-tid). Returnerar {key: [member-dicts]}; delas mellan anrop -> medlemmarna muteras
    ALDRIG (catalog_browse/summary bygger egna output-dicts)."""
    global _BROWSE_IDX
    if _BROWSE_IDX["ver"] != _CATALOG_VER:
        conn = get_conn()
        rows = [dict(r) for r in conn.execute(_BROWSE_SQL + " WHERE available=1")]
        conn.close()
        _BROWSE_IDX = {"ver": _CATALOG_VER, "groups": _group_rows(rows)}
    return _BROWSE_IDX["groups"]


def warm_catalog_cache():
    """Förvärm browse-/summary-cachen (anropas i lifespan) så första bläddringen slipper
    kallstarten (~700ms). Idempotent och snabb när redan varm."""
    _browse_groups()


def catalog_names_for_codes(chain, codes):
    """{product_id: name} för en kedjas katalogkoder (för EAN-resolvningens feed-visning)."""
    if not codes:
        return {}
    conn = get_conn()
    ph = ",".join("?" * len(codes))
    rows = conn.execute(
        f"SELECT product_id, name FROM catalog_products WHERE chain=? AND product_id IN ({ph})",
        (chain, *codes)).fetchall()
    conn.close()
    return {r["product_id"]: r["name"] for r in rows}


_PC_SORT = {
    "recent": "id DESC",
    "abs_desc": "ABS(price - prev_price) DESC",   # största ändring (oavsett riktning)
    "abs_asc": "ABS(price - prev_price) ASC",      # minsta ändring
    "inc": "(price - prev_price) DESC",            # största höjning
    "dec": "(price - prev_price) ASC",             # största sänkning
}


def catalog_price_history(ean):
    """Hyllpris-tidsserie för en EAN ur catalog_price_observations, per kedja, kollapsad på lika pris
    i rad (en punkt per ändring + baslinje). Coop/ICA är butiksscopat (se `catalog_products.store`),
    Axfood/CG nationellt. För konsument-modalens graf, sammanslagen med offer-historiken."""
    conn = get_conn()
    rows = conn.execute(
        "SELECT chain, price, comparison_value, comparison_unit, observed_at "
        "FROM catalog_price_observations WHERE ean=? ORDER BY chain, observed_at", (str(ean),)).fetchall()
    conn.close()
    by_chain = {}
    for r in rows:
        by_chain.setdefault(r["chain"], []).append(r)
    out = []
    for chain, obs in by_chain.items():
        pts = []
        for o in obs:
            last = pts[-1] if pts else None
            if last and last["price"] is not None and o["price"] is not None and abs(last["price"] - o["price"]) < 0.005:
                continue  # oförändrat pris -> kollapsa
            pts.append({"observed_at": o["observed_at"], "price": o["price"],
                        "comparison_value": o["comparison_value"], "comparison_unit": o["comparison_unit"]})
        if pts:
            out.append({"chain": chain, "points": pts})
    return out


def catalog_price_changes(chain=None, q=None, sort="recent", limit=500):
    """Hyllpris-ÄNDRINGAR (föregående -> nytt) ur catalog_price_observations, med produktnamn.
    Beständig per kedja (append-only obs); rensas aldrig. Filtrerbar på kedja och namn (`q`),
    sorterbar (`sort`: recent/abs_desc/abs_asc/inc/dec). Bara faktiska ändringar (föregående
    observation finns, annat pris). LAG-fönster för föregående pris (ett pass, sorterbart på diffen)."""
    where, params = [], []
    if chain:
        where.append("o.chain=?")
        params.append(chain)
    if q and len(q.strip()) >= 2:
        where.append("cp.name LIKE ?")
        params.append(f"%{q.strip()}%")
    cond = (" WHERE " + " AND ".join(where)) if where else ""
    order = _PC_SORT.get(sort, _PC_SORT["recent"])
    conn = get_conn()
    rows = conn.execute(
        f"""SELECT chain, product_id, ean, name, prev_price, price, comparison_value, comparison_unit, observed_at
            FROM (
              SELECT o.id, o.chain, o.product_id, o.ean, o.price, o.comparison_value, o.comparison_unit,
                     o.observed_at, cp.name,
                     LAG(o.price) OVER (PARTITION BY o.chain, o.product_id ORDER BY o.id) AS prev_price
              FROM catalog_price_observations o
              LEFT JOIN catalog_products cp ON cp.chain=o.chain AND cp.product_id=o.product_id
              {cond}
            )
            WHERE prev_price IS NOT NULL AND prev_price != price
            ORDER BY {order} LIMIT ?""",
        (*params, int(limit))).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def catalog_names_for_eans(eans):
    """{ean: name} ur katalogen (för partial-uppgraderingens feed-visning). Första namn per EAN."""
    eans = [str(e) for e in eans if e]
    if not eans:
        return {}
    conn = get_conn()
    rows = conn.execute(
        f"SELECT ean, name FROM catalog_products WHERE ean IN ({','.join('?' * len(eans))})",
        eans).fetchall()
    conn.close()
    out = {}
    for r in rows:
        out.setdefault(r["ean"], r["name"])
    return out


def catalog_axfood_codes_missing_ean():
    """{chain: [product_id]} för Willys/Hemköp-katalograder som saknar EAN (koder att resolva)."""
    conn = get_conn()
    out = {}
    for chain in ("willys", "hemkop"):
        codes = [r["product_id"] for r in conn.execute(
            "SELECT DISTINCT product_id FROM catalog_products WHERE chain=? AND available=1 "
            "AND (ean IS NULL OR ean='')", (chain,))]
        if codes:
            out[chain] = codes
    conn.close()
    return out


def backfill_catalog_eans():
    """Fyll catalog_products.ean ur ean_cache för Axfood-rader utan EAN (kod = product_id),
    NORMALISERAT (`normalize_ean`) så strängen matchar övriga kedjors form -> cross-chain-merge.
    Bumpar katalog-versionen (browse-cachen byggs om). Returnerar antal uppdaterade rader."""
    conn = get_conn()
    rows = conn.execute(
        "SELECT cp.chain, cp.product_id, ec.ean FROM catalog_products cp "
        "JOIN ean_cache ec ON ec.code = cp.product_id "
        "WHERE cp.chain IN ('willys','hemkop') AND (cp.ean IS NULL OR cp.ean='') "
        "AND ec.ean IS NOT NULL AND ec.ean != ''").fetchall()
    updates = [(e, r["chain"], r["product_id"]) for r in rows if (e := normalize_ean(r["ean"]))]
    if updates:
        conn.executemany("UPDATE catalog_products SET ean=? WHERE chain=? AND product_id=?", updates)
        conn.commit()
    conn.close()
    global _CATALOG_VER
    if updates:
        _CATALOG_VER += 1
    return len(updates)


def _diff(a, b):
    """True om två pris/jämförvärden skiljer sig (tolerant; None hanteras)."""
    if (a is None) != (b is None):
        return True
    return a is not None and abs(a - b) > 0.005


def catalog_upsert(chain, rows):
    """Upserta en batch katalog-rader för en kedja. `origin` (lista) serialiseras till JSON.
    Sätter last_seen/fetched_at=nu + available=1; first_seen bevaras. Returnerar (nya, befintliga,
    ändrade): 'befintliga' = product_id fanns redan, 'ändrade' = befintliga vars pris/jämförpris
    skiljer sig (-> en hyllpris-observation skrivs; nya får sin första). Hyllpris-historik append-only."""
    rows = [r for r in rows if r.get("product_id")]
    if not rows:
        return 0, 0, 0
    now = _now()
    conn = get_conn()
    try:
        ids = [str(r["product_id"]) for r in rows]
        ph = ",".join("?" * len(ids))
        existing = {r["product_id"]: (r["price"], r["comparison_value"]) for r in conn.execute(
            f"SELECT product_id, price, comparison_value FROM catalog_products "
            f"WHERE chain=? AND product_id IN ({ph})", (chain, *ids))}
        new = changed = 0
        obs = []  # hyllpris-observationer: nya (första pris) + prisändringar
        for r in rows:
            pid, price, cv = str(r["product_id"]), r.get("price"), r.get("comparison_value")
            if pid not in existing:
                new += 1
                if price is not None:
                    obs.append((chain, pid, r.get("ean"), price, cv, r.get("comparison_unit"), now))
            else:
                op, ocv = existing[pid]
                if price is not None and (_diff(op, price) or _diff(ocv, cv)):
                    changed += 1
                    obs.append((chain, pid, r.get("ean"), price, cv, r.get("comparison_unit"), now))
        params = []
        for r in rows:
            params.append((
                chain, str(r["product_id"]), r.get("ean"), r.get("name"), r.get("brand"),
                r.get("image"), json.dumps(r.get("origin") or None, ensure_ascii=False) if r.get("origin") else None,
                r.get("price"), r.get("comparison_value"), r.get("comparison_unit"),
                r.get("package_size"), r.get("package_value"), r.get("package_unit"),
                r.get("category_raw"), r.get("store"), now, now, now,
            ))
        conn.executemany(
            "INSERT INTO catalog_products "
            "(chain, product_id, ean, name, brand, image, origin, price, comparison_value, "
            "comparison_unit, package_size, package_value, package_unit, category_raw, store, "
            "first_seen, last_seen, fetched_at, available) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1) "
            "ON CONFLICT(chain, product_id) DO UPDATE SET "
            "ean=excluded.ean, name=excluded.name, brand=excluded.brand, image=excluded.image, "
            "origin=excluded.origin, price=excluded.price, comparison_value=excluded.comparison_value, "
            "comparison_unit=excluded.comparison_unit, package_size=excluded.package_size, "
            "package_value=excluded.package_value, package_unit=excluded.package_unit, "
            "category_raw=excluded.category_raw, store=excluded.store, last_seen=excluded.last_seen, "
            "fetched_at=excluded.fetched_at, available=1",
            params,
        )
        if obs:
            conn.executemany(
                "INSERT INTO catalog_price_observations "
                "(chain, product_id, ean, price, comparison_value, comparison_unit, observed_at) "
                "VALUES (?,?,?,?,?,?,?)", obs)
        conn.commit()
    finally:
        conn.close()
    global _CATALOG_VER
    _CATALOG_VER += 1  # invalidera browse-/summary-cachen (efter commit; no-rows-fallet bumpar ej)
    return new, len(ids) - new, changed


def catalog_upsert_metadata(chain, rows):
    """Upsert per-butik-crawlens produkt-METADATA till catalog_products (union: produkter som syns i
    crawlade ICA/Coop-butiker -> bläddra-vyn behåller dem när master-crawlen pensioneras). Skriver INTE
    pris/store/jämförvärde - de hör till per-butik-aggregatet (price_min/max). Vid konflikt uppdateras BARA
    metadata; det befintliga representativpriset BEVARAS (fallback i browse tills ett intervall finns).
    Inga prisobservationer. Returnerar antal nya rader."""
    rows = [r for r in rows if r.get("product_id")]
    if not rows:
        return 0
    now = _now()
    conn = get_conn()
    try:
        ids = [str(r["product_id"]) for r in rows]
        ph = ",".join("?" * len(ids))
        existing = {r["product_id"] for r in conn.execute(
            f"SELECT product_id FROM catalog_products WHERE chain=? AND product_id IN ({ph})", (chain, *ids))}
        new = sum(1 for r in rows if str(r["product_id"]) not in existing)
        params = [(
            chain, str(r["product_id"]), r.get("ean"), r.get("name"), r.get("brand"), r.get("image"),
            json.dumps(r.get("origin") or None, ensure_ascii=False) if r.get("origin") else None,
            r.get("package_size"), r.get("package_value"), r.get("package_unit"), r.get("category_raw"),
            now, now, now,
        ) for r in rows]
        conn.executemany(
            "INSERT INTO catalog_products "
            "(chain, product_id, ean, name, brand, image, origin, package_size, package_value, "
            "package_unit, category_raw, first_seen, last_seen, fetched_at, available) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,1) "
            "ON CONFLICT(chain, product_id) DO UPDATE SET "
            "ean=excluded.ean, name=excluded.name, brand=excluded.brand, image=excluded.image, "
            "origin=excluded.origin, package_size=excluded.package_size, package_value=excluded.package_value, "
            "package_unit=excluded.package_unit, category_raw=excluded.category_raw, "
            "last_seen=excluded.last_seen, fetched_at=excluded.fetched_at, available=1",
            params)
        conn.commit()
    finally:
        conn.close()
    global _CATALOG_VER
    _CATALOG_VER += 1
    return new


def catalog_mark_unseen(chain, before):
    """Sätt available=0 för kedjans rader som inte setts sedan `before` (utgångna varor; behålls)."""
    conn = get_conn()
    conn.execute("UPDATE catalog_products SET available=0 WHERE chain=? AND last_seen < ?",
                 (chain, before))
    conn.commit()
    conn.close()
    global _CATALOG_VER
    _CATALOG_VER += 1  # invalidera browse-/summary-cachen


def catalog_stats():
    """Per kedja: antal produkter, varav tillgängliga, distinkta EAN, hur många som SAKNAR EAN
    (tillgängliga rader utan EAN -> kan ej slås ihop cross-chain), senaste crawl (fetched_at)."""
    conn = get_conn()
    rows = conn.execute(
        "SELECT chain, COUNT(*) total, SUM(available) avail, COUNT(DISTINCT ean) eans, "
        "SUM(CASE WHEN available=1 AND (ean IS NULL OR ean='') THEN 1 ELSE 0 END) missing_ean, "
        "MAX(fetched_at) last FROM catalog_products GROUP BY chain"
    ).fetchall()
    conn.close()
    return {r["chain"]: {"total": r["total"], "available": r["avail"] or 0, "eans": r["eans"],
                         "missing_ean": r["missing_ean"] or 0, "last_crawl": r["last"]} for r in rows}


# Icke-livsmedelskategorier som ändå kan bära ingredienslista (kosmetika/tvål, rengöring, djurmat)
# -> exkluderas ur kost-filtret så de inte felklassas som "vegan". `barn` (kan vara barnmat) och
# `ovrigt` (okänt) lämnas kvar för att inte tappa riktig mat.
_NONFOOD_DIET = {"halsa_skonhet", "hem_hushall", "djur"}


def catalog_summary(chain=None, only_offers=False, fav_stores=None, diet=None):
    """Översikt av den persisterade katalogen (available=1): antal distinkta produkter
    (EAN-grupperat cross-chain) per kanonisk kategori, totalsumma, samt råa produktantal
    per kedja. `only_offers`/`fav_stores`/`diet` begränsar (rea globalt resp. hos favoritbutiker,
    härledd kost) -> kategori-siffrorna speglar samma filter som bläddra-vyn (catalog_browse)."""
    oset = None  # EAN-restriktion (None = ingen)
    if fav_stores:
        oset = eans_on_offer_at_stores(fav_stores)
    elif only_offers:
        oset = on_offer_eans()
    dmap = okdiet = None  # diet-restriktion (None = ingen); spegl-ar catalog_browse
    if diet in ("vegan", "vegetarian"):
        dmap = get_product_diets()
        okdiet = {"vegan"} if diet == "vegan" else {"vegan", "vegetarian"}
    by_chain = {}
    cats = {}
    total = 0
    for g in _browse_groups().values():  # samma cachade gruppering som catalog_browse
        members = [m for m in g if m["chain"] == chain] if chain else g
        if not members:
            continue
        if oset is not None and (members[0]["ean"] not in oset):  # gruppen delar en EAN
            continue
        cat = _cat_canonical(members)
        if dmap is not None and (dmap.get(members[0]["ean"]) not in okdiet or cat in _NONFOOD_DIET):
            continue  # okänt diet eller icke-livsmedel faller bort
        total += 1
        for m in members:
            by_chain[m["chain"]] = by_chain.get(m["chain"], 0) + 1
        cats[cat] = cats.get(cat, 0) + 1
    return {"categories": cats, "total": total,
            "by_chain": dict(sorted(by_chain.items(), key=lambda kv: -kv[1]))}


def _cat_canonical(members):
    """Kanonisk kategori (derive-at-read): MAJORITETSröstning över medlemmarnas mappbara råkategorier
    (robust mot att en enskild kedja felklassar en produkt). Oavgjort -> föredra detalj-baserad
    ICA/Coop-nav-kategori om den är bland vinnarna, annars första-sedda. Inga mappbara -> 'ovrigt'.
    Coop/ICA via category_from_detail (nav-namn), övriga via category_for."""
    votes = {}          # kanonisk -> röster (dict bevarar insättningsordning = först-sedd)
    detail_winner = None  # första detalj-baserade (ica/coop) mappbara, för tie-break
    for m in members:
        raw, ch = m["category_raw"], m["chain"]
        if not raw:
            continue
        c = category_from_detail(ch, raw) if ch in ("coop", "ica") else category_for(ch, raw)
        if not c or c == "ovrigt":
            continue
        votes[c] = votes.get(c, 0) + 1
        if detail_winner is None and ch in ("coop", "ica"):
            detail_winner = c
    if not votes:
        return "ovrigt"
    top = max(votes.values())
    winners = [c for c in votes if votes[c] == top]
    if len(winners) == 1:
        return winners[0]
    return detail_winner if detail_winner in winners else winners[0]


def _cat_pick(members, field):
    return next((m[field] for m in members if m.get(field)), None)


_BROWSE_SORTS = {
    "price": lambda p: (p["price_min"] is None, p["price_min"] or 0, (p["name"] or "").lower()),
    "spread": lambda p: (-((p["price_max"] or 0) - (p["price_min"] or 0)), (p["name"] or "").lower()),
    "name": lambda p: (p["name"] or "").lower(),
}


def catalog_browse(q=None, category=None, chain=None, limit=60, offset=0, only_offers=False,
                   sort=None, deal=None, fav_stores=None, diet=None, manufacturer=None):
    """Distinkta produkter ur den persisterade katalogen (`catalog_products`, available=1),
    grupperade på EAN cross-chain (annars (kedja, namn)). Per produkt: representativ metadata,
    kanonisk kategori, kedjor och per-kedje-hyllpris (CatalogProduct-form, samma som live-söket -
    frontend återanvänder catalogCard). Namn-filter `q` (SQL LIKE), `category` (kanonisk), `chain`,
    `only_offers` (bara produkter med aktuellt erbjudande), `manufacturer` (normaliserad tillverkar-
    nyckel, se catalog_manufacturers). `sort`: price|spread|name (annars default: flest kedjor, billigast,
    namn). `offset`/`limit` paginerar (sort sker FÖRE paginering, server-side). Returnerar `(sida, total)`
    där total = antal matchande produkter före paginering."""
    ql = (q or "").strip()
    if q is not None and len(ql) < 2:
        return [], 0
    if ql or chain:
        # Filtrerad delmängd: q SQL-snabbt (LIKE narrowar), chain ~en kedja -> läs direkt, ej cache.
        conn = get_conn()
        sql = _BROWSE_SQL + " WHERE available=1"
        params = []
        if chain:
            sql += " AND chain=?"
            params.append(chain)
        if ql:
            sql += " AND name LIKE ?"
            params.append(f"%{ql}%")
        rows = [dict(r) for r in conn.execute(sql, params).fetchall()]
        conn.close()
        groups = _group_rows(rows).values()
    else:
        groups = _browse_groups().values()  # hela katalogen, cachad (map-oberoende gruppering)
    # Tillverkar-filter (normaliserad nyckel): tål både aggregatets `key` (idempotent) och fritt namn.
    mkey = manufacturers.manufacturer_key(manufacturer) if manufacturer else None
    out = []
    for g in groups:
        cat = _cat_canonical(g)
        if category and cat != category:
            continue
        brand = _cat_pick(g, "brand")
        if mkey and manufacturers.manufacturer_key(brand) != mkey:
            continue
        rep = next((m for m in g if m.get("name")), g[0])
        # Steg 6: ICA/Coop visar PER-BUTIK-INTERVALL (price_min/max ur aggregatet) när det finns; annars
        # fallback till det lagrade representativpriset (master) tills per-butik-data crawlats. National-
        # kedjor (Willys/Hemköp/CG) har enkelt nationellt pris.
        prices = []
        for m in g:
            if m["chain"] in ("ica", "coop") and m["price_min"] is not None:
                prices.append({"chain": m["chain"], "price": m["price_min"], "price_min": m["price_min"],
                               "price_max": m["price_max"], "price_stores": m["price_stores"],
                               "comparison_value": m["comparison_value"], "comparison_unit": m["comparison_unit"],
                               "comparison_derived": False, "store": None})
            elif m["price"] is not None:
                prices.append({"chain": m["chain"], "price": m["price"], "price_min": None, "price_max": None,
                               "price_stores": None, "comparison_value": m["comparison_value"],
                               "comparison_unit": m["comparison_unit"], "comparison_derived": False,
                               "store": m.get("store")})  # representativt/nationellt
        pv = [p["price"] for p in prices]
        out.append({
            "ean": rep["ean"], "name": rep["name"], "brand": brand,
            "manufacturer": manufacturers.canonical(brand),  # normaliserad tillverkare (kanonisk)
            "origin": _parse_origin(_cat_pick(g, "origin")),  # cross-chain: första medlem med origin
            "image": _cat_pick(g, "image"), "category": cat,
            "package_size": _cat_pick(g, "package_size"), "package_value": rep["package_value"],
            "package_unit": rep["package_unit"], "chains": sorted({m["chain"] for m in g}),
            "prices": sorted(prices, key=lambda p: p["price"]),
            "price_min": min(pv) if pv else None, "price_max": max(pv) if pv else None,
            "_ax": [m["product_id"] for m in g if m["chain"] in ("willys", "hemkop") and m.get("product_id")],
        })
    if fav_stores:  # bara produkter med erbjudande hos användarens favoritbutiker (per-butik-exakt)
        feans = eans_on_offer_at_stores(fav_stores)
        out = [p for p in out if p["ean"] in feans]
    elif only_offers:  # behåll bara produkter med aktuellt erbjudande (global on-offer-mängd)
        oset = on_offer_eans()
        out = [p for p in out if p["ean"] in oset]
    if diet in ("vegan", "vegetarian"):  # härledd kost ur ingredienser; vegan ⊂ vegetarian
        dmap = get_product_diets()
        ok = {"vegan"} if diet == "vegan" else {"vegan", "vegetarian"}
        # okänt (ingen ingrediens) + icke-livsmedel (kosmetika/rengöring/djurmat) faller bort
        out = [p for p in out if dmap.get(p["ean"]) in ok and p["category"] not in _NONFOOD_DIET]
    # Besparings-sort / deal-typ-filter är OFFERS-koncept -> kräver offer-enrichment av HELA mängden
    # före paginering. Restrikterar då till produkter med aktuellt erbjudande och beräknar besparing
    # (max hyllpris-rea över kedjorna) + deal-typer per produkt.
    if sort == "savings" or deal:
        omap = offers_for_eans([p["ean"] for p in out if p["ean"]])
        kept = []
        for p in out:
            offs = omap.get(p["ean"]) or {}
            if not offs:
                continue
            deals = {o["deal_type"] for o in offs.values() if o.get("deal_type")}
            if deal and deal not in deals:
                continue
            sav = 0.0
            for pr in p["prices"]:
                o = offs.get(pr["chain"])
                if o and o.get("price") is not None and pr["price"] is not None:
                    sav = max(sav, pr["price"] - o["price"])
            p["_savings"] = sav
            kept.append(p)
        out = kept
    if sort == "savings":
        out.sort(key=lambda p: (-p.get("_savings", 0), (p["name"] or "").lower()))
    else:
        out.sort(key=_BROWSE_SORTS.get(sort) or (lambda p: (
            -len(p["chains"]), p["price_min"] if p["price_min"] is not None else 9e9, (p["name"] or "").lower())))
    page = out[offset:offset + limit]
    for p in page:
        p.pop("_savings", None)
    _normalize_catalog_page(page)  # derive-at-read: bara sidan (perf + SQLite-vargräns)
    return page, len(out)


def catalog_manufacturers(chain=None, q=None, limit=200):
    """Tillverkar-aggregat ur katalogen (available=1, EAN-grupperat): distinkta produkter per
    normaliserad tillverkare. `{key, name (kanonisk), count}`, flest produkter först. `key` är
    `manufacturer_key` (stabil, matar `catalog_browse(manufacturer=...)`); `name` är display-namnet.
    `chain` scopar till en kedjas katalog, `q` filtrerar på namnet (skiftläges-okänsligt). Okänd/tom
    tillverkare hoppas. Returnerar `{manufacturers, total}` (total = antal distinkta tillverkare)."""
    counts, names = {}, {}
    for g in _browse_groups().values():
        members = [m for m in g if m["chain"] == chain] if chain else g
        if not members:
            continue
        brand = _cat_pick(members, "brand")
        key = manufacturers.manufacturer_key(brand)
        if not key:
            continue
        counts[key] = counts.get(key, 0) + 1
        if key not in names:
            names[key] = manufacturers.canonical(brand)
    items = [{"key": k, "name": names[k], "count": c} for k, c in counts.items()]
    if q:
        ql = q.lower()
        items = [it for it in items if ql in (it["name"] or "").lower()]
    items.sort(key=lambda it: (-it["count"], (it["name"] or "").lower()))
    return {"manufacturers": items[:limit], "total": len(items)}


def _parse_origin(s):
    """Lagrat origin (JSON-lista) -> Python-lista, annars None."""
    if not s:
        return None
    try:
        v = json.loads(s)
    except (json.JSONDecodeError, TypeError):
        return None
    return v if isinstance(v, list) and v else None


def _normalize_catalog_page(page):
    """Visnings-normalisera EN sida katalogprodukter, samma hjälpare som offers-vyn:
    förpackning -> normalized_package (vikt/volym), jämförenhet -> _norm_unit, land title-case.
    Axfood-rader saknar lagrat origin -> backfill ur ean_cache (warmat svenskt ursprung); bara
    sidans koder slås upp (bunden mängd, undviker full-table + SQLite-vargränsen)."""
    need = [c for p in page if not p["origin"] for c in p.get("_ax", [])]
    ax_origin = get_axfood_origins(need) if need else {}
    po = get_product_origins([p["ean"] for p in page if p.get("ean")])  # produktdetalj-fallback
    for p in page:
        p["package_size"] = normalized_package(p["package_size"])
        for pr in p["prices"]:
            pr["comparison_unit"] = _norm_unit(pr["comparison_unit"])
        if p["origin"]:
            p["origin"] = norm_origin(p["origin"])
        else:  # Axfood-backfill: första kod i EAN-gruppen med warmat ursprung (ean_cache, sträng)
            hit = next((ax_origin[c] for c in p.get("_ax", []) if c in ax_origin), None)
            p["origin"] = norm_origin(hit.replace(",", "/").split("/")) if hit else None
        codes = countries.codes_for(p["origin"])
        if not codes and p.get("ean") and po.get(p["ean"]):  # fallback ur product_info
            p["origin"], codes = po[p["ean"]]
        p["origin_codes"] = codes
        p.pop("_ax", None)
