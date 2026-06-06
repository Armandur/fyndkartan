"""Beständig crawl-körningshistorik (`crawl_runs`). Skrivs vid varje crawls slut för båda systemen:
kind='store_prices' (per-butik ICA/Coop) och kind='catalog' (master nationella). Driver historik-vyn
i konsolen + DURABLE "ändringar sedan senaste körningen" (överlever omstart, till skillnad från den
in-memory CRAWL_STATE/STORE_PRICE_STATE som nollställs)."""
from ._conn import get_conn

_COLS = ("id", "kind", "chain", "started", "finished", "status", "rows", "changed",
         "errors", "stores_ok", "stores_total", "last_error")


def record_crawl_run(kind, chain, started=None, finished=None, status=None, rows=0, changed=0,
                     errors=0, stores_ok=None, stores_total=None, last_error=None):
    """Spara en avslutad crawl-körning. Returnerar rad-id."""
    conn = get_conn()
    cur = conn.execute(
        "INSERT INTO crawl_runs (kind, chain, started, finished, status, rows, changed, errors, "
        "stores_ok, stores_total, last_error) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (kind, chain, started, finished, status, rows or 0, changed or 0, errors or 0,
         stores_ok, stores_total, last_error))
    conn.commit()
    rid = cur.lastrowid
    conn.close()
    return rid


def recent_crawl_runs(limit=50, kind=None, chain=None):
    """Senaste körningarna (nyast först), valfritt filtrerat på kind/chain."""
    sql = f"SELECT {', '.join(_COLS)} FROM crawl_runs"
    where, args = [], []
    if kind:
        where.append("kind=?"); args.append(kind)
    if chain:
        where.append("chain=?"); args.append(chain)
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY id DESC LIMIT ?"
    args.append(limit)
    conn = get_conn()
    rows = [dict(r) for r in conn.execute(sql, args).fetchall()]
    conn.close()
    return rows


def last_crawl_runs(kind=None):
    """Senaste körningen PER (kind, chain) -> {(kind, chain): rad}. För durable last-run i korten."""
    sql = ("SELECT cr.* FROM crawl_runs cr JOIN (SELECT kind, chain, MAX(id) mid FROM crawl_runs "
           + ("WHERE kind=? " if kind else "") + "GROUP BY kind, chain) m "
           "ON cr.id=m.mid")
    conn = get_conn()
    rows = conn.execute(sql, ([kind] if kind else [])).fetchall()
    conn.close()
    return {(r["kind"], r["chain"]): dict(r) for r in rows}
