"""Steg 6: per-butik-priser. Data-access för `store_crawl` (crawl-styrning + admin-valt omfång) och
`catalog_store_prices` (senaste hyllpris per butik; historiken ligger i `catalog_price_observations`
med `store` satt). Fas 1: seeding ur stores + admin-läsning; rotations-crawlern fyller resten."""
import json

from ._conn import get_conn


def seed_store_crawl():
    """Seeda `store_crawl` ur `stores.native` för de butiksprissatta kedjorna (Coop ledger, ICA account).
    Idempotent: `INSERT OR IGNORE` rör inte queryable/enabled/priority på redan seedade rader. Flera Coop-
    butiker kan dela en ledger (= en pris-entitet) -> kollapsas på PK (chain, store). Returnerar antal nya
    rader. Coop: `native.ledgerAccountNumber`; ICA: `native.accountNumber`."""
    conn = get_conn()
    rows = conn.execute(
        "SELECT chain, native FROM stores WHERE chain IN ('coop','ica') AND native IS NOT NULL"
    ).fetchall()
    n = 0
    for r in rows:
        try:
            nat = json.loads(r["native"])
        except (ValueError, TypeError):
            continue
        store = nat.get("ledgerAccountNumber") if r["chain"] == "coop" else nat.get("accountNumber")
        if not store:
            continue
        n += conn.execute(
            "INSERT OR IGNORE INTO store_crawl (chain, store) VALUES (?,?)", (r["chain"], str(store))
        ).rowcount
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
