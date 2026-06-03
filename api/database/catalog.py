"""Fulla sortiment (steg 5): persistent produktkatalog per kedja (`catalog_products`).
Crawlen (api/catalog_crawl.py) upsertar hit; `catalog_browse` läser EAN-grupperat cross-chain."""
import json

from ._conn import _now, get_conn
from ..categories import category_for, category_from_detail

_CAT_COLS = ("product_id", "ean", "name", "brand", "image", "origin", "price",
             "comparison_value", "comparison_unit", "package_size", "package_value",
             "package_unit", "category_raw")


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
                r.get("category_raw"), now, now, now,
            ))
        conn.executemany(
            "INSERT INTO catalog_products "
            "(chain, product_id, ean, name, brand, image, origin, price, comparison_value, "
            "comparison_unit, package_size, package_value, package_unit, category_raw, "
            "first_seen, last_seen, fetched_at, available) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1) "
            "ON CONFLICT(chain, product_id) DO UPDATE SET "
            "ean=excluded.ean, name=excluded.name, brand=excluded.brand, image=excluded.image, "
            "origin=excluded.origin, price=excluded.price, comparison_value=excluded.comparison_value, "
            "comparison_unit=excluded.comparison_unit, package_size=excluded.package_size, "
            "package_value=excluded.package_value, package_unit=excluded.package_unit, "
            "category_raw=excluded.category_raw, last_seen=excluded.last_seen, "
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
    return new, len(ids) - new, changed


def catalog_mark_unseen(chain, before):
    """Sätt available=0 för kedjans rader som inte setts sedan `before` (utgångna varor; behålls)."""
    conn = get_conn()
    conn.execute("UPDATE catalog_products SET available=0 WHERE chain=? AND last_seen < ?",
                 (chain, before))
    conn.commit()
    conn.close()


def catalog_stats():
    """Per kedja: antal produkter, varav tillgängliga, distinkta EAN, senaste crawl (fetched_at)."""
    conn = get_conn()
    rows = conn.execute(
        "SELECT chain, COUNT(*) total, SUM(available) avail, COUNT(DISTINCT ean) eans, "
        "MAX(fetched_at) last FROM catalog_products GROUP BY chain"
    ).fetchall()
    conn.close()
    return {r["chain"]: {"total": r["total"], "available": r["avail"] or 0,
                         "eans": r["eans"], "last_crawl": r["last"]} for r in rows}


def catalog_summary(chain=None):
    """Översikt av den persisterade katalogen (available=1): antal distinkta produkter
    (EAN-grupperat cross-chain) per kanonisk kategori, totalsumma, samt råa produktantal
    per kedja (cross-chain-delade EAN räknas i varje kedja -> summan kan vara > total)."""
    conn = get_conn()
    sql = "SELECT chain, ean, name, category_raw FROM catalog_products WHERE available=1"
    params = []
    if chain:
        sql += " AND chain=?"
        params.append(chain)
    rows = [dict(r) for r in conn.execute(sql, params).fetchall()]
    conn.close()
    by_chain = {}
    groups = {}
    for r in rows:
        by_chain[r["chain"]] = by_chain.get(r["chain"], 0) + 1
        key = r["ean"] or f"{r['chain']}:{(r['name'] or '').lower()}"
        groups.setdefault(key, []).append(r)
    cats = {}
    for g in groups.values():
        cat = _cat_canonical(g)
        cats[cat] = cats.get(cat, 0) + 1
    return {"categories": cats, "total": len(groups),
            "by_chain": dict(sorted(by_chain.items(), key=lambda kv: -kv[1]))}


def _cat_canonical(members):
    """Kanonisk kategori (derive-at-read) ur medlemmarnas råkategorier; första mappbara, annars
    'ovrigt'. Coop/ICA via category_from_detail (nav-namn), övriga via category_for."""
    for m in members:
        raw, ch = m["category_raw"], m["chain"]
        if not raw:
            continue
        c = category_from_detail(ch, raw) if ch in ("coop", "ica") else category_for(ch, raw)
        if c and c != "ovrigt":
            return c
    return "ovrigt"


def _cat_pick(members, field):
    return next((m[field] for m in members if m.get(field)), None)


def catalog_browse(q=None, category=None, chain=None, limit=60):
    """Distinkta produkter ur den persisterade katalogen (`catalog_products`, available=1),
    grupperade på EAN cross-chain (annars (kedja, namn)). Per produkt: representativ metadata,
    kanonisk kategori, kedjor och per-kedje-hyllpris (CatalogProduct-form, samma som live-söket -
    frontend återanvänder catalogCard). Namn-filter `q` (SQL LIKE), `category` (kanonisk), `chain`."""
    ql = (q or "").strip()
    if q is not None and len(ql) < 2:
        return []
    conn = get_conn()
    sql = ("SELECT chain, ean, name, brand, image, origin, price, comparison_value, comparison_unit, "
           "package_size, package_value, package_unit, category_raw FROM catalog_products WHERE available=1")
    params = []
    if chain:
        sql += " AND chain=?"
        params.append(chain)
    if ql:
        sql += " AND name LIKE ?"
        params.append(f"%{ql}%")
    rows = [dict(r) for r in conn.execute(sql, params).fetchall()]
    conn.close()
    groups = {}
    for r in rows:
        key = r["ean"] or f"{r['chain']}:{(r['name'] or '').lower()}"
        groups.setdefault(key, []).append(r)
    out = []
    for g in groups.values():
        cat = _cat_canonical(g)
        if category and cat != category:
            continue
        rep = next((m for m in g if m.get("name")), g[0])
        prices = [{"chain": m["chain"], "price": m["price"], "comparison_value": m["comparison_value"],
                   "comparison_unit": m["comparison_unit"], "comparison_derived": False}
                  for m in g if m["price"] is not None]
        pv = [p["price"] for p in prices]
        origin = None
        if rep.get("origin"):
            try:
                origin = json.loads(rep["origin"])
            except (json.JSONDecodeError, TypeError):
                origin = None
        out.append({
            "ean": rep["ean"], "name": rep["name"], "brand": _cat_pick(g, "brand"),
            "origin": origin, "image": _cat_pick(g, "image"), "category": cat,
            "package_size": _cat_pick(g, "package_size"), "package_value": rep["package_value"],
            "package_unit": rep["package_unit"], "chains": sorted({m["chain"] for m in g}),
            "prices": sorted(prices, key=lambda p: p["price"]),
            "price_min": min(pv) if pv else None, "price_max": max(pv) if pv else None,
        })
    out.sort(key=lambda p: (-len(p["chains"]), p["price_min"] if p["price_min"] is not None else 9e9,
                            (p["name"] or "").lower()))
    return out[:limit]
