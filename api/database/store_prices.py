"""Steg 6: per-butik-priser. Data-access för `store_crawl` (crawl-styrning + admin-valt omfång) och
`catalog_store_prices` (senaste hyllpris per butik; historiken ligger i `catalog_price_observations`
med `store` satt). Fas 1: seeding ur stores + admin-läsning; rotations-crawlern fyller resten."""
import json

from ._conn import get_conn


def seed_store_crawl():
    """Seeda `store_crawl` ur `stores.native` för de butiksprissatta kedjorna (Coop ledger, ICA account).
    Idempotent på queryable/enabled/priority (`INSERT OR IGNORE`), men UPPDATERAR alltid denormaliserat
    namn/ort/antal (re-seed håller dem färska). Flera Coop-butiker kan dela en ledger -> kollapsas på PK
    (chain, store), `store_count` räknar dem. Returnerar antal nya rader. Coop: `native.ledgerAccountNumber`;
    ICA: `native.accountNumber`."""
    conn = get_conn()
    rows = conn.execute(
        "SELECT chain, name, city, native FROM stores WHERE chain IN ('coop','ica') AND native IS NOT NULL"
    ).fetchall()
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
            "INSERT OR IGNORE INTO store_crawl (chain, store) VALUES (?,?)", (chain, store)
        ).rowcount
        conn.execute("UPDATE store_crawl SET name=?, city=?, store_count=? WHERE chain=? AND store=?",
                     (name, city, count, chain, store))
    conn.commit()
    conn.close()
    return n


def stores_to_measure(chain=None, recheck=False, cap=None):
    """(chain, store)-rader som ska queryability-mätas. `recheck=False` -> bara omätta (queryable IS NULL);
    `recheck=True` -> alla (periodisk om-mätning, fångar butiker som börjat erbjuda e-handel). `chain`
    scopar, `cap` begränsar. Ordnar omätta först, sedan äldst kontrollerade (rättvis rotation)."""
    conn = get_conn()
    sql = "SELECT chain, store FROM store_crawl WHERE 1=1"
    args = []
    if not recheck:
        sql += " AND queryable IS NULL"
    if chain:
        sql += " AND chain=?"
        args.append(chain)
    sql += " ORDER BY (checked_at IS NOT NULL), checked_at"
    if cap:
        sql += f" LIMIT {int(cap)}"
    rows = conn.execute(sql, args).fetchall()
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
        "UPDATE store_crawl SET queryable=?, product_count=?, status=?, checked_at=? WHERE chain=? AND store=?",
        (q, product_count, status, now, chain, str(store)),
    )
    conn.commit()
    conn.close()


def list_store_crawl(chain=None, q=None, queryable=None, enabled=None, limit=200, offset=0):
    """Admin-urvalstabellen: store_crawl-rader med denormaliserat namn/ort (seedat). Filter: `chain`,
    `queryable` (0/1/None), `enabled` (0/1/None), `q` (namn/ort-sök). Returnerar (sida, total)."""
    conn = get_conn()
    where = "WHERE 1=1"
    args = []
    if chain:
        where += " AND chain=?"
        args.append(chain)
    if queryable is not None:
        where += " AND queryable=?"
        args.append(queryable)
    if enabled is not None:
        where += " AND enabled=?"
        args.append(enabled)
    if q:
        where += " AND (LOWER(name) LIKE ? OR LOWER(city) LIKE ?)"
        like = f"%{q.lower()}%"
        args += [like, like]
    total = conn.execute(f"SELECT COUNT(*) FROM store_crawl {where}", args).fetchone()[0]
    rows = [dict(r) for r in conn.execute(
        f"SELECT chain, store, queryable, enabled, product_count, last_crawled, status, checked_at, "
        f"name, city, store_count FROM store_crawl {where} ORDER BY (name IS NULL), LOWER(name), store "
        f"LIMIT ? OFFSET ?", (*args, limit, offset)).fetchall()]
    conn.close()
    return rows, total


def set_stores_enabled(items, enabled):
    """Sätt enabled (0/1) för en lista (chain, store)-par. Returnerar antal ändrade rader."""
    conn = get_conn()
    n = 0
    for chain, store in items:
        n += conn.execute("UPDATE store_crawl SET enabled=? WHERE chain=? AND store=?",
                          (1 if enabled else 0, chain, str(store))).rowcount
    conn.commit()
    conn.close()
    return n


def set_all_queryable_enabled(enabled, chain=None):
    """Bulk: sätt enabled för ALLA frågbara butiker (queryable=1), ev. chain-scopat. Returnerar antal."""
    conn = get_conn()
    sql = "UPDATE store_crawl SET enabled=? WHERE queryable=1"
    args = [1 if enabled else 0]
    if chain:
        sql += " AND chain=?"
        args.append(chain)
    n = conn.execute(sql, args).rowcount
    conn.commit()
    conn.close()
    return n


def store_crawl_stats():
    """Översikt för admin/konsol: antal butiker (ledgers/accounts) i store_crawl per kedja, samt hur många
    som är frågbara (queryable=1), omätta (NULL), ej frågbara (0) och valda (enabled=1)."""
    conn = get_conn()
    rows = conn.execute(
        "SELECT chain, COUNT(*) total, "
        "SUM(CASE WHEN queryable=1 THEN 1 ELSE 0 END) queryable, "
        "SUM(CASE WHEN queryable IS NULL THEN 1 ELSE 0 END) unmeasured, "
        "SUM(CASE WHEN queryable=0 THEN 1 ELSE 0 END) not_queryable, "
        "SUM(CASE WHEN enabled=1 THEN 1 ELSE 0 END) enabled "
        "FROM store_crawl GROUP BY chain"
    ).fetchall()
    conn.close()
    return {r["chain"]: {k: r[k] for k in ("total", "queryable", "unmeasured", "not_queryable", "enabled")}
            for r in rows}
