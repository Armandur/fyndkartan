"""Steg 6 Fas 3: per-butik-pris-crawler. Roterar över de admin-valda (enabled) frågbara butikerna och
skriver catalog_store_prices + per-butik append-on-change-historik. Återanvänder katalog-crawlens walk
(catalog_crawl._ica_fetch_store) - samma walk, per-butik write-target. Admin-triggat bakgrundsjobb (ingen
auto-körning vid uppstart), rate-limitat + circuit-breaker.

STEG 1: ICA (skippar masterbutik - ingen catalog_products-skrivning; per-butik-pris är sanningskällan,
allmänt jämförpris härleds senare ur catalog_store_prices). Coop:s department-walk följer i nästa pass."""
import asyncio
import logging
from datetime import datetime, timezone

from . import apilog, catalog_crawl, database
from .adapters import ica_token

log = logging.getLogger("matbutiker")

_PAGE_PACE = 0.35    # paus mellan sidor i EN butik (varje parallell butik throttlas separat)
_CONCURRENCY = 4     # antal butiker parallellt (aggregerad takt ~ CONCURRENCY/PAGE_PACE req/s)
_BREAKER = 6         # butiks-fel i rad -> avbryt hela körningen (sannolikt WAF/blockad)

STORE_PRICE_STATE = {
    "running": False, "chain": None, "done": 0, "total": 0, "stores_ok": 0,
    "rows": 0, "changed": 0, "errors": 0, "last_error": None, "current": None,
    "started_at": None, "finished_at": None,
}


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
    Butiker körs PARALLELLT (bunden samtidighet `concurrency`); inom en butik är pagineringen sekventiell.
    STEG 1: bara ICA. Bakgrund, rate-limitat + global circuit-breaker (fel i rad -> avbryt, WAF-skydd)."""
    if STORE_PRICE_STATE["running"]:
        return {"status": "running"}
    if chain != "ica":
        return {"status": "error", "detail": "Steg 1 stödjer bara ICA än."}
    targets = [a for _, a in database.stores_to_crawl(chain="ica", cap=cap)]
    STORE_PRICE_STATE.update(running=True, chain=chain, done=0, total=len(targets), stores_ok=0,
                             rows=0, changed=0, errors=0, last_error=None, current=None,
                             started_at=_now(), finished_at=None)
    sem = asyncio.Semaphore(max(1, concurrency))
    abort = {"flag": False, "consecutive": 0}
    try:
        async with apilog.make_client(follow_redirects=True) as client:
            async def _worker(acct):
                if abort["flag"]:
                    return
                async with sem:
                    if abort["flag"]:
                        return
                    try:
                        token = await ica_token.get_token(client)  # cachad + auto-förnyad
                        await _crawl_one_ica(client, token, acct)
                        STORE_PRICE_STATE["stores_ok"] += 1
                        abort["consecutive"] = 0
                    except Exception as e:  # noqa: BLE001
                        STORE_PRICE_STATE["errors"] += 1
                        STORE_PRICE_STATE["last_error"] = str(e)[:200]
                        abort["consecutive"] += 1
                        if abort["consecutive"] >= _BREAKER:
                            abort["flag"] = True
                            log.warning("store_crawl: circuit-breaker (%d fel i rad) - avbryter", _BREAKER)
                    finally:
                        STORE_PRICE_STATE["done"] += 1
            await asyncio.gather(*(_worker(a) for a in targets))
    finally:
        STORE_PRICE_STATE.update(running=False, finished_at=_now(), current=None)
    return {"status": "done", "stores_ok": STORE_PRICE_STATE["stores_ok"], "rows": STORE_PRICE_STATE["rows"]}
