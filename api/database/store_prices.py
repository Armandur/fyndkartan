"""Steg 6: per-butik-priser. Data-access för `store_crawl` (crawl-styrning + admin-valt omfång) och
`catalog_store_prices` (senaste hyllpris per butik; historiken ligger i `catalog_price_observations`
med `store` satt). Fas 1: seeding ur stores + admin-läsning; rotations-crawlern fyller resten."""
import json

from sqlalchemy import bindparam, text

from ._conn import _now, get_conn
from ..geo import haversine


def seed_store_crawl():
    """Seeda `store_crawl` ur `stores.native` för de butiksprissatta kedjorna (Coop ledger, ICA account).
    Idempotent på queryable/enabled/priority (ON CONFLICT DO NOTHING), men UPPDATERAR alltid denormaliserat
    namn/ort/antal (re-seed håller dem färska). Flera Coop-butiker kan dela en ledger -> kollapsas på PK
    (chain, store), `store_count` räknar dem. Returnerar antal nya rader. Coop: `native.ledgerAccountNumber`;
    ICA: `native.accountNumber`."""
    conn = get_conn()
    rows = conn.execute(text(
        "SELECT chain, name, city, native FROM stores WHERE chain IN ('coop','ica') AND native IS NOT NULL"
    )).fetchall()
    agg = {}  # (chain, store) -> [name, city, count]  (representativt namn/ort = första sedda)
    for r in rows:
        try:
            nat = json.loads(r["native"])
        except (ValueError, TypeError):
            continue
        store = nat.get("ledgerAccountNumber") if r["chain"] == "coop" else nat.get("accountNumber")
        if not store:
            continue
        a = agg.setdefault((r["chain"], str(store)), [r["name"], r["city"], 0])
        a[2] += 1
    n = 0
    for (chain, store), (name, city, count) in agg.items():
        n += conn.execute(
            text("INSERT INTO store_crawl (chain, store) VALUES (:chain, :store) "
                 "ON CONFLICT (chain, store) DO NOTHING"), {"chain": chain, "store": store}
        ).rowcount
        conn.execute(text("UPDATE store_crawl SET name=:name, city=:city, store_count=:count "
                          "WHERE chain=:chain AND store=:store"),
                     {"name": name, "city": city, "count": count, "chain": chain, "store": store})
    conn.commit()
    conn.close()
    return n


def stores_to_measure(chain=None, recheck=False, cap=None):
    """(chain, store)-rader som ska queryability-mätas. `recheck=False` -> bara omätta (queryable IS NULL);
    `recheck=True` -> alla (periodisk om-mätning, fångar butiker som börjat erbjuda e-handel). `chain`
    scopar, `cap` begränsar. Ordnar omätta först, sedan äldst kontrollerade (rättvis rotation)."""
    conn = get_conn()
    sql = "SELECT chain, store FROM store_crawl WHERE 1=1"
    args = {}
    if not recheck:
        sql += " AND queryable IS NULL"
    if chain:
        sql += " AND chain=:chain"
        args["chain"] = chain
    sql += " ORDER BY (checked_at IS NOT NULL), checked_at"
    if cap:
        sql += " LIMIT :cap"
        args["cap"] = int(cap)
    rows = conn.execute(text(sql), args).fetchall()
    conn.close()
    return [(r["chain"], r["store"]) for r in rows]


def set_store_queryability(chain, store, queryable, product_count, status):
    """Skriv mät-resultatet för en butik: queryable (True/False, eller None = lämna omätt vid transient fel
    så re-körningen försöker igen), product_count (eller None) + status + tidsstämpel."""
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    q = None if queryable is None else (1 if queryable else 0)
    conn = get_conn()
    conn.execute(
        text("UPDATE store_crawl SET queryable=:q, product_count=:pc, status=:status, checked_at=:now "
             "WHERE chain=:chain AND store=:store"),
        {"q": q, "pc": product_count, "status": status, "now": now, "chain": chain, "store": str(store)},
    )
    conn.commit()
    conn.close()


def list_store_crawl(chain=None, q=None, queryable=None, enabled=None, limit=200, offset=0):
    """Admin-urvalstabellen: store_crawl-rader med denormaliserat namn/ort (seedat). Filter: `chain`,
    `queryable` (0/1/None), `enabled` (0/1/None), `q` (namn/ort-sök). Returnerar (sida, total)."""
    conn = get_conn()
    where = "WHERE 1=1"
    args = {}
    if chain:
        where += " AND chain=:chain"
        args["chain"] = chain
    if queryable is not None:
        where += " AND queryable=:queryable"
        args["queryable"] = queryable
    if enabled is not None:
        where += " AND enabled=:enabled"
        args["enabled"] = enabled
    if q:
        where += " AND (LOWER(name) LIKE :like OR LOWER(city) LIKE :like)"
        args["like"] = f"%{q.lower()}%"
    total = conn.execute(text(f"SELECT COUNT(*) FROM store_crawl {where}"), args).fetchone()[0]
    rows = [dict(r) for r in conn.execute(text(
        f"SELECT chain, store, queryable, enabled, product_count, last_crawled, status, checked_at, "
        f"name, city, store_count FROM store_crawl {where} ORDER BY (name IS NULL), LOWER(name), store "
        f"LIMIT :limit OFFSET :offset"), {**args, "limit": limit, "offset": offset}).fetchall()]
    conn.close()
    return rows, total


def set_stores_enabled(items, enabled):
    """Sätt enabled (0/1) för en lista (chain, store)-par. Returnerar antal ändrade rader."""
    conn = get_conn()
    n = 0
    for chain, store in items:
        n += conn.execute(text("UPDATE store_crawl SET enabled=:enabled WHERE chain=:chain AND store=:store"),
                          {"enabled": 1 if enabled else 0, "chain": chain, "store": str(store)}).rowcount
    conn.commit()
    conn.close()
    return n


def set_all_queryable_enabled(enabled, chain=None):
    """Bulk: sätt enabled för ALLA frågbara butiker (queryable=1), ev. chain-scopat. Returnerar antal."""
    conn = get_conn()
    sql = "UPDATE store_crawl SET enabled=:enabled WHERE queryable=1"
    args = {"enabled": 1 if enabled else 0}
    if chain:
        sql += " AND chain=:chain"
        args["chain"] = chain
    n = conn.execute(text(sql), args).rowcount
    conn.commit()
    conn.close()
    return n


def _pdiff(a, b):
    """True om två priser skiljer sig (tål float-brus + None)."""
    if a is None or b is None:
        return a is not b
    return abs(a - b) >= 0.005


def stores_to_crawl(chain=None, cap=None, max_age_hours=None):
    """(chain, store)-par som ska per-butik-pris-crawlas: enabled=1 OCH queryable=1. Äldst crawlad först
    (rättvis rotation; aldrig-crawlad = först). `chain` scopar, `cap` begränsar. `max_age_hours` > 0 ->
    HOPPA butiker crawlade nyligare än så (bara NULL eller äldre) -> 'lägg till + crawla' kör bara de nya;
    daglig rotation refreshar det som blivit gammalt. None/0 = crawla alla enabled (full om-crawl)."""
    conn = get_conn()
    sql = "SELECT chain, store FROM store_crawl WHERE enabled=1 AND queryable=1"
    args = {}
    if chain:
        sql += " AND chain=:chain"
        args["chain"] = chain
    if max_age_hours and max_age_hours > 0:
        from datetime import datetime, timedelta, timezone
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=max_age_hours)).strftime("%Y-%m-%dT%H:%M:%SZ")
        sql += " AND (last_crawled IS NULL OR last_crawled < :cutoff)"
        args["cutoff"] = cutoff
    sql += " ORDER BY (last_crawled IS NOT NULL), last_crawled"
    if cap:
        sql += " LIMIT :cap"
        args["cap"] = int(cap)
    rows = conn.execute(text(sql), args).fetchall()
    conn.close()
    return [(r["chain"], r["store"]) for r in rows]


def mark_store_crawled(chain, store, product_count):
    """Stämpla last_crawled=nu + product_count efter en per-butik-crawl."""
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    conn = get_conn()
    conn.execute(text("UPDATE store_crawl SET last_crawled=:now, product_count=:pc "
                      "WHERE chain=:chain AND store=:store"),
                 {"now": now, "pc": product_count, "chain": chain, "store": str(store)})
    conn.commit()
    conn.close()


def upsert_store_prices(chain, store, rows):
    """Upserta per-butik-priser (`catalog_store_prices`, PK chain/product_id/store) + append-on-change-
    historik (`catalog_price_observations` med `store`) NÄR pris/jämförvärde ändrats sedan förra crawlen.
    `rows` = normaliserade katalog-rader (product_id/ean/price/comparison_value/comparison_unit). Batchat
    (en SELECT + två executemany). Returnerar (nya, ändrade)."""
    rows = [r for r in rows if r.get("product_id")]
    if not rows:
        return 0, 0
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    store = str(store)
    conn = get_conn()
    try:
        ids = [str(r["product_id"]) for r in rows]
        existing = {r["product_id"]: (r["price"], r["comparison_value"]) for r in conn.execute(
            text("SELECT product_id, price, comparison_value FROM catalog_store_prices "
                 "WHERE chain=:chain AND store=:store AND product_id IN :ids").bindparams(
                bindparam("ids", expanding=True)),
            {"chain": chain, "store": store, "ids": ids})}
        new = changed = 0
        obs = []  # dict-rader vid nytt/ändrat pris
        for r in rows:
            pid, price, cv = str(r["product_id"]), r.get("price"), r.get("comparison_value")
            o = {"chain": chain, "product_id": pid, "store": store, "ean": r.get("ean"),
                 "price": price, "comparison_value": cv,
                 "comparison_unit": r.get("comparison_unit"), "observed_at": now, "prev_price": None}
            if pid not in existing:
                new += 1
                if price is not None:
                    obs.append(o)
            else:
                op, ocv = existing[pid]
                if price is not None and (_pdiff(op, price) or _pdiff(ocv, cv)):
                    changed += 1
                    o["prev_price"] = op  # föregående pris lagras -> läsqueryn slipper LAG-fönster
                    obs.append(o)
        conn.executemany(
            text("INSERT INTO catalog_store_prices (chain, product_id, store, ean, price, comparison_value, "
                 "comparison_unit, available, first_seen, last_seen) VALUES "
                 "(:chain, :product_id, :store, :ean, :price, :comparison_value, :comparison_unit, 1, "
                 ":first_seen, :last_seen) "
                 "ON CONFLICT (chain, product_id, store) DO UPDATE SET ean=excluded.ean, price=excluded.price, "
                 "comparison_value=excluded.comparison_value, comparison_unit=excluded.comparison_unit, "
                 "available=1, last_seen=excluded.last_seen"),
            [{"chain": chain, "product_id": str(r["product_id"]), "store": store, "ean": r.get("ean"),
              "price": r.get("price"), "comparison_value": r.get("comparison_value"),
              "comparison_unit": r.get("comparison_unit"), "first_seen": now, "last_seen": now} for r in rows])
        if obs:
            conn.executemany(
                text("INSERT INTO catalog_price_observations (chain, product_id, store, ean, price, "
                     "prev_price, comparison_value, comparison_unit, observed_at) VALUES "
                     "(:chain, :product_id, :store, :ean, :price, :prev_price, :comparison_value, "
                     ":comparison_unit, :observed_at)"), obs)
        conn.commit()
    finally:
        conn.close()
    return new, changed


def recompute_store_aggregates(chain=None):
    """Materialisera per-butik-prisaggregatet: MIN/MAX/antal-distinkta-butiker per produkt ur
    catalog_store_prices -> catalog_products.price_min/max/price_stores (för bläddra-vyns INTERVALL).
    Korrelerad UPDATE (idx_csp_chain_product) över befintliga ICA/Coop-master-rader. `chain` scopar.
    Returnerar antal uppdaterade rader. (Union-produkter som ännu saknas i master skapas vid cutover-flippen.)"""
    conn = get_conn()
    sub = ("(SELECT {agg} FROM catalog_store_prices sp WHERE sp.chain=cp.chain AND sp.product_id=cp.product_id)")
    sql = (f"UPDATE catalog_products AS cp SET "
           f"price_min={sub.format(agg='MIN(price)')}, "
           f"price_max={sub.format(agg='MAX(price)')}, "
           f"price_stores={sub.format(agg='COUNT(DISTINCT store)')} "
           f"WHERE cp.chain IN ('ica','coop')")
    args = {}
    if chain:
        sql += " AND cp.chain=:chain"
        args["chain"] = chain
    cur = conn.execute(text(sql), args)
    n = cur.rowcount
    # Materialisera radantalet (catalog_store_prices är för stor för COUNT(*) per overview-laddning).
    # Här (slutet av en crawl) är COUNT:en försumbar mot resten av jobbet. Scopar på chain om satt.
    vol_sql = "SELECT chain, COUNT(*) rows, COUNT(DISTINCT store) stores FROM catalog_store_prices"
    if chain:
        vol_sql += " WHERE chain=:chain"
    vol_sql += " GROUP BY chain"
    for r in conn.execute(text(vol_sql), args).fetchall():
        conn.execute(text(
            "INSERT INTO store_price_volume (chain, price_rows, price_stores, updated) "
            "VALUES (:chain, :price_rows, :price_stores, :updated) "
            "ON CONFLICT (chain) DO UPDATE SET price_rows=excluded.price_rows, "
            "price_stores=excluded.price_stores, updated=excluded.updated"),
            {"chain": r["chain"], "price_rows": r["rows"], "price_stores": r["stores"], "updated": _now()})
    conn.commit()
    conn.close()
    from .catalog import bump_catalog_version  # lokal import (undvik cirkulär) - intervallen ska synas i browse
    bump_catalog_version()
    return n


def store_prices_for_ean(ean):
    """Per-butik-priser för en EAN, GRUPPERADE på (kedja, pris) - en populär vara i hundratals butiker
    har oftast få distinkta prisnivåer (många delar exakt samma pris). Returnerar prisnivåer billigast
    först: {chain, price, comparison_value, comparison_unit, store_count, stores:[{name,city}]}. Driver
    bläddra-vyns intervall-modal (kort + smal lista). `total_stores` = antal butiker totalt."""
    conn = get_conn()
    rows = conn.execute(text(
        "SELECT sp.chain, sp.store, sp.price, sp.comparison_value, sp.comparison_unit, "
        "sc.name, sc.city FROM catalog_store_prices sp "
        "LEFT JOIN store_crawl sc ON sc.chain=sp.chain AND sc.store=sp.store "
        "WHERE sp.ean=:ean AND sp.price IS NOT NULL ORDER BY sp.price"), {"ean": str(ean)}).fetchall()
    conn.close()
    groups, total = {}, 0
    for r in rows:
        total += 1
        key = (r["chain"], round(r["price"], 2))
        g = groups.get(key)
        if g is None:
            g = {"chain": r["chain"], "price": round(r["price"], 2), "comparison_value": r["comparison_value"],
                 "comparison_unit": r["comparison_unit"], "store_count": 0, "stores": []}
            groups[key] = g
        g["store_count"] += 1
        g["stores"].append({"name": r["name"] or r["store"], "city": r["city"]})
    out = sorted(groups.values(), key=lambda g: g["price"])
    return {"levels": out, "total_stores": total}


def store_prices_geo(ean, lat=None, lng=None, radius_km=10.0, pairs=None):
    """Per-FYSISK-butik hyllpris för en EAN, scopat geografiskt eller till specifika butiker (favoriter).
    Mappar fysisk butik (`stores`.lat/lng + native) -> ledger/account -> `catalog_store_prices`. `pairs` =
    [(chain, store_id)] (favoriter/explicit); annars `lat`/`lng`/`radius_km` (närmaste). Billigast först;
    vid geo-scope tas bara prissatta butiker med, vid favorit/explicit tas alla med (pris kan vara null ->
    'inget data för den butiken'). Bara ICA/Coop (butiksprissatta)."""
    conn = get_conn()
    if pairs:
        srows = []
        for c, sid in pairs:
            if c in ("ica", "coop"):
                r = conn.execute(text("SELECT chain, store_id, name, city, lat, lng, native FROM stores "
                                      "WHERE chain=:chain AND store_id=:store_id"),
                                 {"chain": c, "store_id": str(sid)}).fetchone()
                if r:
                    srows.append(r)
    else:
        srows = conn.execute(text("SELECT chain, store_id, name, city, lat, lng, native FROM stores "
                                  "WHERE chain IN ('ica','coop') AND native IS NOT NULL")).fetchall()
    prices = {(r["chain"], str(r["store"])): r for r in conn.execute(
        text("SELECT chain, store, price, comparison_value, comparison_unit FROM catalog_store_prices "
             "WHERE ean=:ean AND price IS NOT NULL"), {"ean": str(ean)})}
    conn.close()
    near = lat is not None and lng is not None
    out = []
    for r in srows:
        nat = json.loads(r["native"]) if r["native"] else {}
        ledger = nat.get("ledgerAccountNumber") if r["chain"] == "coop" else nat.get("accountNumber")
        if not ledger:
            continue
        dist = None
        if near:
            if not r["lat"] or not r["lng"]:
                continue
            dist = haversine(lat, lng, r["lat"], r["lng"])
            if dist > radius_km:
                continue
        pr = prices.get((r["chain"], str(ledger)))
        if near and pr is None:  # "nära mig" = prissatta butiker
            continue
        out.append({"chain": r["chain"], "store_id": r["store_id"], "name": r["name"], "city": r["city"],
                    "lat": r["lat"], "lng": r["lng"],
                    "distance_km": round(dist, 1) if dist is not None else None,
                    "price": pr["price"] if pr else None,
                    "comparison_value": pr["comparison_value"] if pr else None,
                    "comparison_unit": pr["comparison_unit"] if pr else None})
    out.sort(key=lambda x: (x["price"] is None, x["price"] or 0,
                            x["distance_km"] if x["distance_km"] is not None else 1e9))
    return out


def store_name(chain, store):
    """Denormaliserat butiksnamn ur store_crawl (för crawl-feeden), annars store-id:t."""
    conn = get_conn()
    r = conn.execute(text("SELECT name FROM store_crawl WHERE chain=:chain AND store=:store"),
                     {"chain": chain, "store": str(store)}).fetchone()
    conn.close()
    return (r["name"] if r else None) or str(store)


def store_crawl_stats():
    """Översikt för admin/konsol: antal butiker (ledgers/accounts) i store_crawl per kedja, samt hur många
    som är frågbara (queryable=1), omätta (NULL), ej frågbara (0) och valda (enabled=1)."""
    conn = get_conn()
    rows = conn.execute(text(
        "SELECT chain, COUNT(*) total, "
        "SUM(CASE WHEN queryable=1 THEN 1 ELSE 0 END) queryable, "
        "SUM(CASE WHEN queryable IS NULL THEN 1 ELSE 0 END) unmeasured, "
        "SUM(CASE WHEN queryable=0 THEN 1 ELSE 0 END) not_queryable, "
        "SUM(CASE WHEN enabled=1 THEN 1 ELSE 0 END) enabled "
        "FROM store_crawl GROUP BY chain"
    )).fetchall()
    conn.close()
    return {r["chain"]: {k: r[k] for k in ("total", "queryable", "unmeasured", "not_queryable", "enabled")}
            for r in rows}


def store_prices_stats():
    """Steg 6-översikt för konsolen: per-butik-prisinsamlingens status per kedja - valda/frågbara/crawlade
    butiker + senaste crawl (ur store_crawl, litet) samt MATERIALISERAT radantal (store_price_volume,
    uppdaterat per crawl). Räknar INTE catalog_store_prices vid läsning - den tabellen växer mot ~17M rader
    (COUNT(*) ~6s och stigande) och får inte ligga i overview-laddningen."""
    conn = get_conn()
    crawl = {r["chain"]: dict(r) for r in conn.execute(text(
        "SELECT chain, COUNT(*) total, "
        "SUM(CASE WHEN enabled=1 THEN 1 ELSE 0 END) enabled, "
        "SUM(CASE WHEN queryable=1 THEN 1 ELSE 0 END) queryable, "
        "SUM(CASE WHEN last_crawled IS NOT NULL THEN 1 ELSE 0 END) crawled, "
        "MAX(last_crawled) last_crawled "
        "FROM store_crawl GROUP BY chain"
    )).fetchall()}
    vol = {r["chain"]: dict(r) for r in conn.execute(text(
        "SELECT chain, price_rows, price_stores FROM store_price_volume"
    )).fetchall()}
    conn.close()
    out = {}
    for chain in sorted(set(crawl) | set(vol)):
        c, v = crawl.get(chain, {}), vol.get(chain, {})
        out[chain] = {
            "total": c.get("total", 0), "enabled": c.get("enabled", 0),
            "queryable": c.get("queryable", 0), "crawled": c.get("crawled", 0),
            "last_crawled": c.get("last_crawled"),
            "price_rows": v.get("price_rows", 0), "price_stores": v.get("price_stores", 0),
        }
    return out


def upsert_ica_ecom_prices(store, rows):
    """Batch-upsert ICA ecom-pris-crawlens rader till `ica_ecom_prices` (separat tabell, parallell-fasen).
    `rows` = ica_ecom-normaliserade dicts (retailer_product_id, name, brand, price, comparison_value/unit,
    promo_price/text, available) + `ean` (gtin, ev. None). Nyckel (store, retailer_product_id). Returnerar
    antal skrivna rader."""
    rows = [r for r in rows if r.get("retailer_product_id")]
    if not rows:
        return 0
    now = _now()
    payload = [{"store": str(store), "rid": str(r["retailer_product_id"]), "ean": r.get("ean"),
                "name": r.get("name"), "brand": r.get("brand"), "price": r.get("price"),
                "cv": r.get("comparison_value"), "cu": r.get("comparison_unit"),
                "pp": r.get("promo_price"), "pt": r.get("promo_text"),
                "av": 1 if r.get("available") else 0, "now": now} for r in rows]
    conn = get_conn()
    conn.execute(
        text("INSERT INTO ica_ecom_prices (store, retailer_product_id, ean, name, brand, price, "
             "comparison_value, comparison_unit, promo_price, promo_text, available, fetched_at) VALUES "
             "(:store, :rid, :ean, :name, :brand, :price, :cv, :cu, :pp, :pt, :av, :now) "
             "ON CONFLICT (store, retailer_product_id) DO UPDATE SET ean=excluded.ean, name=excluded.name, "
             "brand=excluded.brand, price=excluded.price, comparison_value=excluded.comparison_value, "
             "comparison_unit=excluded.comparison_unit, promo_price=excluded.promo_price, "
             "promo_text=excluded.promo_text, available=excluded.available, fetched_at=excluded.fetched_at"),
        payload)
    conn.commit()
    conn.close()
    return len(payload)


def ica_ecom_coverage():
    """Snabböversikt för parallell-jämförelsen: rader, distinkta butiker, mappnings-grad (ean ifyllt),
    med-pris i ica_ecom_prices."""
    conn = get_conn()
    r = conn.execute(text(
        "SELECT COUNT(*) rows, COUNT(DISTINCT store) stores, "
        "COUNT(ean) mapped, COUNT(price) priced, COUNT(promo_price) promos, MAX(fetched_at) last "
        "FROM ica_ecom_prices")).fetchone()
    conn.close()
    return {"rows": r["rows"], "stores": r["stores"], "mapped": r["mapped"],
            "priced": r["priced"], "promos": r["promos"], "last": r["last"]}


def ica_ecom_stores_to_crawl(cap=None, max_age_hours=None):
    """ICA-butiker (enabled=1) att ecom-pris-crawla, ROTATION härledd ur ica_ecom_prices.fetched_at (senast
    ecom-crawlad) - egen rotation skild från quicksearchens last_crawled. Aldrig-ecom-crawlad först, sedan
    äldst. `max_age_hours` > 0 -> hoppa butiker ecom-crawlade nyligare än så. Returnerar butiks-id (accountId)."""
    conn = get_conn()
    base = ("SELECT sc.store, MAX(ep.fetched_at) AS last FROM store_crawl sc "
            "LEFT JOIN ica_ecom_prices ep ON ep.store = sc.store "
            "WHERE sc.chain='ica' AND sc.enabled=1 GROUP BY sc.store")
    sql = f"SELECT store, last FROM ({base}) s"  # wrap -> aliaset `last` går att använda i ORDER BY-uttryck
    args = {}
    if max_age_hours and max_age_hours > 0:
        from datetime import datetime, timedelta, timezone
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=max_age_hours)).strftime("%Y-%m-%dT%H:%M:%SZ")
        sql += " WHERE last IS NULL OR last < :cutoff"
        args["cutoff"] = cutoff
    sql += " ORDER BY (last IS NOT NULL), last"
    if cap:
        sql += " LIMIT :cap"
        args["cap"] = int(cap)
    rows = conn.execute(text(sql), args).fetchall()
    conn.close()
    return [r["store"] for r in rows]
