"""Fulla sortiment (steg 5): persistent produktkatalog per kedja (`catalog_products`).
Crawlen (api/catalog_crawl.py) upsertar hit; läsvägen grupperar på EAN cross-chain (kommer)."""
import json

from ._conn import _now, get_conn

_CAT_COLS = ("product_id", "ean", "name", "brand", "image", "origin", "price",
             "comparison_value", "comparison_unit", "package_size", "package_value",
             "package_unit", "category_raw")


def catalog_upsert(chain, rows):
    """Upserta en batch katalog-rader för en kedja. `origin` (lista) serialiseras till JSON.
    Sätter last_seen/fetched_at=nu + available=1; first_seen bevaras. Returnerar (nya, befintliga)
    där 'befintliga' = product_id fanns redan (raden skrevs om/omcachades - INTE nödvändigtvis
    ändrad data; vi jämför inte värden)."""
    rows = [r for r in rows if r.get("product_id")]
    if not rows:
        return 0, 0
    now = _now()
    conn = get_conn()
    try:
        ids = [str(r["product_id"]) for r in rows]
        ph = ",".join("?" * len(ids))
        existing = {r["product_id"] for r in conn.execute(
            f"SELECT product_id FROM catalog_products WHERE chain=? AND product_id IN ({ph})",
            (chain, *ids))}
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
        conn.commit()
    finally:
        conn.close()
    new = sum(1 for i in ids if i not in existing)
    return new, len(ids) - new


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
