"""Instrumentering av HTTP-anrop (utgående mot kedjornas API:er + inkommande mot vårt
eget /v1). En httpx-event-hook + middleware loggar varje anrop till SQLite: en ring-
buffer (`api_calls`, beskärs) för feeden och kumulativ statistik per host
(`api_call_stats`). Persistent - överlever omstart. Loggning får aldrig fälla anropet."""

import time

import httpx
from sqlalchemy import text

from .database import get_conn

_MAX_CALLS = 2000  # ring-storlek för feeden (statistiken är kumulativ, beskärs ej)
_starts = {}
_since_prune = 0

# host-fragment -> vilken kedja/källa anropet hör till
_SOURCE_HOSTS = [
    ("apim-pub.gw.ica.se", "ica"),
    ("apimgw-pub.ica.se", "ica"),
    ("icanet.se", "ica"),
    ("ica.se", "ica"),
    ("proxy.api.coop.se", "coop"),
    ("external.api.coop.se", "coop"),
    ("dr.coop.se", "coop"),
    ("coop.se", "coop"),
    ("willys.se", "willys"),
    ("hemkop.se", "hemkop"),
    ("schwarz", "lidl"),
    ("lidl.se", "lidl"),
    ("citygross.se", "citygross"),
]


def classify_source(host):
    h = host or ""
    for frag, chain in _SOURCE_HOSTS:
        if frag in h:
            return chain
    return "other"


def _record(ts, method, host, path, status, ms, chain):
    """Skriv ett anrop till feeden + uppdatera kumulativ statistik. Sväljer fel.
    Egen kort transaktion per anrop (commit direkt) = samma effekt som gamla autocommit-loggern."""
    global _since_prune
    try:
        c = get_conn()
        c.execute(
            text("INSERT INTO api_calls (ts, method, host, path, status, ms, chain) "
                 "VALUES (:ts, :method, :host, :path, :status, :ms, :chain)"),
            {"ts": ts, "method": method, "host": host, "path": path,
             "status": status, "ms": ms, "chain": chain},
        )
        c.execute(
            text("INSERT INTO api_call_stats (host, chain, count, errors, total_ms) "
                 "VALUES (:host, :chain, 1, :errors, :ms) "
                 "ON CONFLICT (host) DO UPDATE SET count=count+1, chain=excluded.chain, "
                 "errors=errors+excluded.errors, total_ms=total_ms+excluded.total_ms"),
            {"host": host, "chain": chain,
             "errors": 1 if status and status >= 400 else 0, "ms": ms or 0},
        )
        _since_prune += 1
        if _since_prune >= 250:  # beskär feeden då och då (behåll de _MAX_CALLS senaste)
            _since_prune = 0
            c.execute(text("DELETE FROM api_calls WHERE id <= "
                           "(SELECT MAX(id) FROM api_calls) - :max"), {"max": _MAX_CALLS})
        c.commit()
        c.close()
    except Exception:  # noqa: BLE001 - loggning får aldrig fälla anropet
        pass


async def _on_request(request):
    _starts[id(request)] = time.perf_counter()
    if len(_starts) > 2000:  # läckage-skydd om svar uteblir
        _starts.clear()


async def _on_response(resp):
    try:
        start = _starts.pop(id(resp.request), None)
        ms = round((time.perf_counter() - start) * 1000, 1) if start else None
        host = resp.request.url.host
        _record(time.time(), resp.request.method, host, resp.request.url.path,
                resp.status_code, ms, classify_source(host))
    except Exception:  # noqa: BLE001
        pass


def record_incoming(method, path, status, ms):
    """Logga ett inkommande anrop mot vårt EGET API (källa 'egen') i samma feed."""
    _record(time.time(), method, "(egen)", path, status, ms, "egen")


def make_client(**kwargs):
    """httpx.AsyncClient med anropsloggning inkopplad."""
    hooks = kwargs.pop("event_hooks", {}) or {}
    return httpx.AsyncClient(
        event_hooks={
            "request": list(hooks.get("request", [])) + [_on_request],
            "response": list(hooks.get("response", [])) + [_on_response],
        },
        **kwargs,
    )


def recent(limit=120):
    try:
        conn = get_conn()
        rows = conn.execute(
            text("SELECT ts, method, host, path, status, ms, chain FROM api_calls "
                 "ORDER BY id DESC LIMIT :limit"),
            {"limit": limit},
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except Exception:  # noqa: BLE001
        return []


def stats():
    try:
        conn = get_conn()
        rows = conn.execute(text(
            "SELECT host, chain, count, errors, total_ms FROM api_call_stats")).fetchall()
        conn.close()
    except Exception:  # noqa: BLE001
        return []
    out = [
        {
            "host": r["host"],
            "chain": r["chain"],
            "count": r["count"],
            "errors": r["errors"],
            "avg_ms": round(r["total_ms"] / r["count"], 1) if r["count"] else None,
        }
        for r in rows
    ]
    out.sort(key=lambda x: -x["count"])
    return out
