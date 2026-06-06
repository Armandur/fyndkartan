"""Beständig crawl-körningshistorik (`crawl_runs`). Skrivs vid varje crawls slut för båda systemen:
kind='store_prices' (per-butik ICA/Coop) och kind='catalog' (master nationella). Driver historik-vyn
i konsolen + DURABLE "ändringar sedan senaste körningen" (överlever omstart, till skillnad från den
in-memory CRAWL_STATE/STORE_PRICE_STATE som nollställs)."""
import json

from sqlalchemy import text

from ._conn import get_conn

_COLS = ("id", "kind", "chain", "started", "finished", "status", "rows", "changed",
         "errors", "stores_ok", "stores_total", "last_error", "error_summary")


def _rowdict(r):
    """Rad -> dict med error_summary parsad ur JSON ({feltyp: antal})."""
    d = dict(r)
    raw = d.pop("error_summary", None)
    try:
        d["errors_by_type"] = json.loads(raw) if raw else {}
    except (ValueError, TypeError):
        d["errors_by_type"] = {}
    return d


def record_crawl_run(kind, chain, started=None, finished=None, status=None, rows=0, changed=0,
                     errors=0, stores_ok=None, stores_total=None, last_error=None, error_summary=None):
    """Spara en avslutad crawl-körning. `error_summary` = dict {feltyp: antal}. Returnerar rad-id."""
    conn = get_conn()
    cur = conn.execute(
        text("INSERT INTO crawl_runs (kind, chain, started, finished, status, rows, changed, errors, "
             "stores_ok, stores_total, last_error, error_summary) VALUES "
             "(:kind, :chain, :started, :finished, :status, :rows, :changed, :errors, "
             ":stores_ok, :stores_total, :last_error, :error_summary) RETURNING id"),
        {"kind": kind, "chain": chain, "started": started, "finished": finished, "status": status,
         "rows": rows or 0, "changed": changed or 0, "errors": errors or 0,
         "stores_ok": stores_ok, "stores_total": stores_total, "last_error": last_error,
         "error_summary": json.dumps(error_summary, ensure_ascii=False) if error_summary else None})
    rid = cur.fetchone()[0]
    conn.commit()
    conn.close()
    return rid


def recent_crawl_runs(limit=50, kind=None, chain=None):
    """Senaste körningarna (nyast först), valfritt filtrerat på kind/chain."""
    sql = f"SELECT {', '.join(_COLS)} FROM crawl_runs"
    where, args = [], {"limit": limit}
    if kind:
        where.append("kind=:kind"); args["kind"] = kind
    if chain:
        where.append("chain=:chain"); args["chain"] = chain
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY id DESC LIMIT :limit"
    conn = get_conn()
    rows = [_rowdict(r) for r in conn.execute(text(sql), args).fetchall()]
    conn.close()
    return rows


def last_crawl_runs(kind=None):
    """Senaste körningen PER (kind, chain) -> {(kind, chain): rad}. För durable last-run i korten."""
    sql = ("SELECT cr.* FROM crawl_runs cr JOIN (SELECT kind, chain, MAX(id) mid FROM crawl_runs "
           + ("WHERE kind=:kind " if kind else "") + "GROUP BY kind, chain) m "
           "ON cr.id=m.mid")
    conn = get_conn()
    rows = conn.execute(text(sql), ({"kind": kind} if kind else {})).fetchall()
    conn.close()
    return {(r["kind"], r["chain"]): _rowdict(r) for r in rows}
