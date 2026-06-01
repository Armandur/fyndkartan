"""Instrumentering av utgående HTTP-anrop (mot kedjornas API:er).

En httpx-event-hook loggar varje anrop till en ring-buffer i minnet + aggregerad
statistik per host. Används av admin-dashboarden. Återställs vid omstart."""

import time
from collections import defaultdict, deque

import httpx

_MAX_CALLS = 500
CALLS = deque(maxlen=_MAX_CALLS)
STATS = defaultdict(lambda: {"count": 0, "errors": 0, "total_ms": 0.0, "chain": "other"})
_starts = {}

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
]


def classify_source(host):
    h = host or ""
    for frag, chain in _SOURCE_HOSTS:
        if frag in h:
            return chain
    return "other"


async def _on_request(request):
    _starts[id(request)] = time.perf_counter()
    if len(_starts) > 2000:  # läckage-skydd om svar uteblir
        _starts.clear()


async def _on_response(resp):
    try:
        start = _starts.pop(id(resp.request), None)
        ms = round((time.perf_counter() - start) * 1000, 1) if start else None
        host = resp.request.url.host
        chain = classify_source(host)
        CALLS.appendleft(
            {
                "ts": time.time(),
                "method": resp.request.method,
                "host": host,
                "path": resp.request.url.path,
                "status": resp.status_code,
                "ms": ms,
                "chain": chain,
            }
        )
        s = STATS[host]
        s["count"] += 1
        s["chain"] = chain
        if resp.status_code >= 400:
            s["errors"] += 1
        if ms:
            s["total_ms"] += ms
    except Exception:  # noqa: BLE001 - loggning får aldrig fälla anropet
        pass


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
    return list(CALLS)[:limit]


def stats():
    out = [
        {
            "host": host,
            "chain": s["chain"],
            "count": s["count"],
            "errors": s["errors"],
            "avg_ms": round(s["total_ms"] / s["count"], 1) if s["count"] else None,
        }
        for host, s in STATS.items()
    ]
    out.sort(key=lambda x: -x["count"])
    return out
