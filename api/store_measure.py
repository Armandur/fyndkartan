"""Steg 6, Fas 1: queryability-mätning per butik. Probe:ar varje butik (Coop ledger / ICA account) med
en bred sökning för att avgöra om den är frågbar (har e-handelspris) + ICA:s totala sortiment-antal.
Re-runnable (periodisk om-mätning fångar butiker som börjat erbjuda e-handel). Admin-triggat bakgrundsjobb -
INGEN auto-körning vid uppstart. Rate-limitat + circuit-breaker (WAF-skonsamt), idempotent/resumerbart.

Coop: perso-sök ger inte hela sortimentet i ett svar -> queryability via "mjölk" count>0, product_count NULL.
ICA: globalsearch `*` -> stats.totalHits = hela butikens sortiment (queryable + exakt antal i ett anrop)."""
import asyncio
import json
import logging

from . import apilog, database, details
from .adapters import ica_token

log = logging.getLogger("matbutiker")

_UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
       "Chrome/124.0.0.0 Safari/537.36")

# Bunden parallellism + paus mellan batchar (WAF-skonsamt); circuit-breaker vid fel i rad.
_CONCURRENCY = {"coop": 2, "ica": 4}
_PAUSE = 0.4
_BREAKER = 8  # avbryt kedjan efter så här många fel i rad (sannolikt WAF/blockad)
_TIMEOUT = 20

MEASURE_STATE = {
    "running": False,
    "chains": {c: {"done": 0, "total": 0, "queryable": 0, "errors": 0, "last_error": None}
               for c in ("coop", "ica")},
}


async def _probe_coop(client, ledger, key):
    """(queryable, product_count, status) för en Coop-ledger. Bred sökning ("mjölk"); count>0 = frågbar.
    product_count = None (perso-söket ger inte hela sortimentet). Re-keyar en gång vid 401/403."""
    url = "https://external.api.coop.se/personalization/search/global"
    params = {"api-version": "v1", "store": str(ledger), "groups": "CUSTOMER_PRIVATE", "direct": "true"}
    body = json.dumps({"query": "mjölk", "resultsOptions": {"skip": 0, "take": 1}})

    async def _req(k):
        h = {"Ocp-Apim-Subscription-Key": k, "Content-Type": "application/json",
             "Origin": "https://www.coop.se", "Accept": "application/json", "User-Agent": _UA}
        return await client.post(url, params=params, headers=h, content=body, timeout=_TIMEOUT)

    r = await _req(key)
    if r.status_code in (401, 403):
        key = await details._resolve_coop_key(client, force=True)
        r = await _req(key)
    r.raise_for_status()
    count = ((r.json().get("results") or {}).get("count")) or 0
    return count > 0, None, "ok"


async def _probe_ica(client, account, token):
    """(queryable, product_count, status) för ett ICA-account. `*` -> stats.totalHits = hela sortimentet."""
    r = await client.post(
        "https://apimgw-pub.ica.se/sverige/digx/globalsearch/v1/search/quicksearch",
        json={"queryString": "*", "take": 1, "offset": 0, "accountNumber": str(account),
              "searchDomain": "All", "sessionId": "measure"},
        headers={"User-Agent": _UA, "Authorization": f"Bearer {token}",
                 "Content-Type": "application/json"}, timeout=_TIMEOUT,
    )
    r.raise_for_status()
    total = ((r.json().get("products") or {}).get("stats") or {}).get("totalHits") or 0
    return total > 0, total, "ok"


async def _measure_chain(client, chain, targets):
    """Mät en kedjas butiker (bunden parallellism + paus + circuit-breaker). Uppdaterar MEASURE_STATE."""
    st = MEASURE_STATE["chains"][chain]
    st.update(done=0, total=len(targets), queryable=0, errors=0, last_error=None)
    if not targets:
        return
    key = await details._resolve_coop_key(client) if chain == "coop" else None
    token = await ica_token.get_token(client) if chain == "ica" else None
    sem = asyncio.Semaphore(_CONCURRENCY[chain])
    consecutive = 0
    aborted = False

    async def _one(store):
        nonlocal consecutive, aborted
        if aborted:
            return
        async with sem:
            try:
                if chain == "coop":
                    q, cnt, status = await _probe_coop(client, store, key)
                else:
                    q, cnt, status = await _probe_ica(client, store, token)
                database.set_store_queryability(chain, store, q, cnt, status)
                consecutive = 0
                if q:
                    st["queryable"] += 1
            except Exception as e:  # noqa: BLE001
                st["errors"] += 1
                st["last_error"] = str(e)[:200]
                consecutive += 1
                # Fel != ej frågbar (kan vara transient/WAF) -> queryable=None (lämna omätt, retry nästa körning).
                database.set_store_queryability(chain, store, None, None, f"error: {str(e)[:80]}")
                if consecutive >= _BREAKER:
                    aborted = True
                    log.warning("store_measure: %s circuit-breaker (%d fel i rad) - avbryter", chain, consecutive)
            finally:
                st["done"] += 1

    # Batcha så pausen bromsar takten även med parallellism.
    batch = _CONCURRENCY[chain]
    for i in range(0, len(targets), batch):
        if aborted:
            break
        await asyncio.gather(*(_one(s) for s in targets[i:i + batch]))
        await asyncio.sleep(_PAUSE)


async def measure_queryability(chain=None, recheck=False, cap=None):
    """Kör queryability-mätningen (bakgrund). `chain` = coop|ica (annars båda), `recheck` = om-mät alla
    (annars bara omätta), `cap` = max butiker/kedja. Skriver queryable/product_count i store_crawl."""
    if MEASURE_STATE["running"]:
        return {"status": "running"}
    MEASURE_STATE["running"] = True
    chains = [chain] if chain in ("coop", "ica") else ["coop", "ica"]
    try:
        async with apilog.make_client(follow_redirects=True) as client:
            for ch in chains:
                targets = database.stores_to_measure(ch, recheck=recheck, cap=cap)
                await _measure_chain(client, ch, [s for _, s in targets])
    finally:
        MEASURE_STATE["running"] = False
    return {"status": "done", "stats": database.store_crawl_stats()}
