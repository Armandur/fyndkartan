"""Steg 6 Fas 3: per-butik-pris-crawler. Roterar över de admin-valda (enabled) frågbara butikerna och
skriver catalog_store_prices + per-butik append-on-change-historik. Återanvänder katalog-crawlens walk
(catalog_crawl._ica_fetch_store) - samma walk, per-butik write-target. Admin-triggat bakgrundsjobb (ingen
auto-körning vid uppstart), rate-limitat + circuit-breaker.

STEG 1: ICA (skippar masterbutik - ingen catalog_products-skrivning; per-butik-pris är sanningskällan,
allmänt jämförpris härleds senare ur catalog_store_prices). Coop:s department-walk följer i nästa pass."""
import asyncio
import logging
import time
from datetime import datetime, timezone

import httpx

from . import apilog, catalog_crawl, database
from .adapters import ica_token

log = logging.getLogger("matbutiker")

_PAGE_PACE = 0.35    # paus mellan sidor i EN butik (varje parallell butik throttlas separat)
_CONCURRENCY = 4     # default-tak för adaptiv samtidighet (rampar upp mot detta)
_MIN_CONC = 1
_RAMP_AFTER = 4      # additiv ökning (+1 mål) efter så här många butiker i rad utan WAF
_WAF_COOLDOWN = 30   # sek paus efter WAF innan nya butiker startas
_BREAKER = 8         # totalt antal WAF/fel i rad -> avbryt hela körningen

STORE_PRICE_STATE = {
    "running": False, "chain": None, "done": 0, "total": 0, "stores_ok": 0,
    "rows": 0, "changed": 0, "errors": 0, "last_error": None, "current": None,
    "target": 0, "active": 0, "cooldown": False,  # adaptiv samtidighet (synlig i konsolen)
    "started_at": None, "finished_at": None,
}


def _is_waf(e):
    """True om felet ser ut som WAF/rate-limit (429/403) eller anslutnings-/timeout-strul."""
    if isinstance(e, httpx.HTTPStatusError):
        return e.response.status_code in (429, 403, 503)
    return isinstance(e, (httpx.ConnectError, httpx.ReadTimeout, httpx.RemoteProtocolError))


def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


async def _crawl_one_ica(client, token, acct):
    """Crawla en ICA-butiks hela katalog -> catalog_store_prices + historik. Returnerar antal produkter."""
    total_rows = 0
    async for rows, total, _page in catalog_crawl._ica_fetch_store(client, acct, token, pace=_PAGE_PACE):
        if rows:
            _new, changed = database.upsert_store_prices("ica", acct, rows)
            total_rows += len(rows)
            STORE_PRICE_STATE["rows"] += len(rows)
            STORE_PRICE_STATE["changed"] += changed
        STORE_PRICE_STATE["current"] = f"ICA {acct}: {total_rows}/{total}"
    database.mark_store_crawled("ica", acct, total_rows)
    return total_rows


async def crawl_store_prices(chain="ica", cap=None, concurrency=_CONCURRENCY):
    """Crawla per-butik-priser för enabled+frågbara butiker i `chain` (rotation, äldst crawlad först, cap).
    Butiker körs parallellt med ADAPTIV samtidighet (AIMD): börjar lågt, +1 mål efter `_RAMP_AFTER` butiker
    utan WAF (upp till taket `concurrency`), halverar målet + `_WAF_COOLDOWN`s paus vid WAF (429/403/503).
    Inom en butik är pagineringen sekventiell. Global circuit-breaker (`_BREAKER` WAF i rad -> avbryt).
    STEG 1: bara ICA. Bakgrund. Dispatcher fyller på upp till målet allt eftersom butiker blir klara."""
    if STORE_PRICE_STATE["running"]:
        return {"status": "running"}
    if chain != "ica":
        return {"status": "error", "detail": "Steg 1 stödjer bara ICA än."}
    queue = [a for _, a in database.stores_to_crawl(chain="ica", cap=cap)]
    ceiling = max(_MIN_CONC, min(concurrency, 10))
    STORE_PRICE_STATE.update(running=True, chain=chain, done=0, total=len(queue), stores_ok=0,
                             rows=0, changed=0, errors=0, last_error=None, current=None,
                             target=min(2, ceiling), active=0, cooldown=False,
                             started_at=_now(), finished_at=None)
    ctl = {"target": min(2, ceiling), "active": 0, "ok_streak": 0, "waf_streak": 0,
           "cooldown_until": 0.0, "abort": False}

    async def _run_one(client, acct):
        ctl["active"] += 1
        STORE_PRICE_STATE["active"] = ctl["active"]
        try:
            token = await ica_token.get_token(client)
            await _crawl_one_ica(client, token, acct)
            STORE_PRICE_STATE["stores_ok"] += 1
            ctl["waf_streak"] = 0
            ctl["ok_streak"] += 1
            if ctl["ok_streak"] >= _RAMP_AFTER and ctl["target"] < ceiling:  # additiv ökning
                ctl["target"] += 1
                ctl["ok_streak"] = 0
        except Exception as e:  # noqa: BLE001
            STORE_PRICE_STATE["errors"] += 1
            STORE_PRICE_STATE["last_error"] = str(e)[:200]
            ctl["ok_streak"] = 0
            if _is_waf(e):
                ctl["waf_streak"] += 1
                ctl["target"] = max(_MIN_CONC, ctl["target"] // 2)  # multiplikativ minskning
                ctl["cooldown_until"] = time.monotonic() + _WAF_COOLDOWN
                if ctl["waf_streak"] >= _BREAKER:
                    ctl["abort"] = True
                    log.warning("store_crawl: circuit-breaker (%d WAF i rad) - avbryter", _BREAKER)
        finally:
            ctl["active"] -= 1
            STORE_PRICE_STATE.update(done=STORE_PRICE_STATE["done"] + 1, active=ctl["active"],
                                     target=ctl["target"])

    try:
        async with apilog.make_client(follow_redirects=True) as client:
            tasks = set()
            while (queue or tasks) and not ctl["abort"]:
                now = time.monotonic()
                cooling = now < ctl["cooldown_until"]
                STORE_PRICE_STATE["cooldown"] = cooling
                while queue and ctl["active"] < ctl["target"] and not cooling and not ctl["abort"]:
                    tasks.add(asyncio.create_task(_run_one(client, queue.pop(0))))
                if not tasks:
                    await asyncio.sleep(0.5)  # i cooldown utan aktiva -> vänta ut den
                    continue
                _done, tasks = await asyncio.wait(tasks, timeout=0.5, return_when=asyncio.FIRST_COMPLETED)
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)  # låt pågående bli klara
    finally:
        STORE_PRICE_STATE.update(running=False, finished_at=_now(), current=None, active=0, cooldown=False)
    return {"status": "done", "stores_ok": STORE_PRICE_STATE["stores_ok"], "rows": STORE_PRICE_STATE["rows"]}
