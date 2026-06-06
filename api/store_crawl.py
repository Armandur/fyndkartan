"""Steg 6 Fas 3: per-butik-pris-crawler. Roterar över de admin-valda (enabled) frågbara butikerna och
skriver catalog_store_prices + per-butik append-on-change-historik. Återanvänder katalog-crawlens walk
(catalog_crawl._ica_fetch_store) - samma walk, per-butik write-target. Admin-triggat bakgrundsjobb (ingen
auto-körning vid uppstart), rate-limitat + circuit-breaker.

ICA (`*` + empirisk kategori-walk) och Coop (department-rötter via by-attribute) - båda parametriserade på
butik. Skippar masterbutik (ingen catalog_products-skrivning än; per-butik-pris är sanningskällan, allmänt
jämförpris härleds senare ur catalog_store_prices). Samtidigheten är adaptiv (AIMD) och kedje-agnostisk."""
import asyncio
import logging
import time
from datetime import datetime, timezone

import httpx

from . import apilog, catalog_crawl, database
from .adapters import ica_token

log = logging.getLogger("matbutiker")

_PAGE_PACE = 0.35    # paus mellan sidor i EN butik (varje parallell butik throttlas separat)
_MAX_CONC = 12       # HÅRD säkerhetsgräns (mot katastrofal overshoot). AIMD rampar mot den; det är WAF-
                     # backoffen som hittar den FAKTISKA gränsen under - inte ett handsatt tak.
_MIN_CONC = 1
_RAMP_AFTER = 4      # additiv ökning (+1 mål) efter så här många butiker i rad utan WAF
_WAF_COOLDOWN = 30   # sek paus efter WAF innan nya butiker startas
_BREAKER = 8         # totalt antal WAF/fel i rad -> avbryt hela körningen

def _blank_chain():
    return {"running": False, "done": 0, "total": 0, "stores_ok": 0, "rows": 0, "changed": 0,
            "errors": 0, "last_error": None, "current": None, "target": 0, "active": 0,
            "cooldown": False, "started_at": None, "finished_at": None}


# Per-kedja sub-state så ICA + Coop kan köra PARALLELLT (olika API:er -> ingen kontention). Delad
# `recent`-feed (båda kedjornas kategori-poster interfolierade -> visar parallelliteten i konsolen).
STORE_PRICE_STATE = {"recent": [], "chains": {"ica": _blank_chain(), "coop": _blank_chain()}}
_FEED_CAP = 60


def _is_waf(e):
    """True om felet ska trigga back-off/breaker: WAF/rate-limit (429/403/503) ELLER vilket som helst
    transport-/anslutningsfel. httpx.TransportError täcker ConnectError, ConnectTimeout, ReadError,
    ReadTimeout, WriteError, PoolTimeout, RemoteProtocolError m.fl. - dvs nät-/last-strul som vi ska
    backa av på (tidigare missades ReadError/ConnectTimeout/PoolTimeout -> breakern slog aldrig till och
    crawlen brände igenom alla butiker när nätet var mättat)."""
    if isinstance(e, httpx.HTTPStatusError):
        return e.response.status_code in (429, 403, 503)
    return isinstance(e, httpx.TransportError)


def _err_key(e):
    """Normaliserad fel-bucket för fördelnings-statistik (grupperar per typ, inte per butiks-URL):
    'HTTP 401', 'PoolTimeout', 'ReadError'..."""
    if isinstance(e, httpx.HTTPStatusError):
        return f"HTTP {e.response.status_code}"
    return type(e).__name__


def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _emit(chain, sname, cat_label, cat_total):
    """Lägg en kategori-post i crawl-feeden (butik · kategori + antal). Återanvänder feed-item-shapen
    {chain, name, ean} så konsolens befintliga flöde kan strömma dem."""
    item = {"chain": chain, "name": f"{sname} · {cat_label}",
            "ean": f"{(cat_total or 0):,}".replace(",", " ") + " st"}
    STORE_PRICE_STATE["recent"] = ([item] + STORE_PRICE_STATE["recent"])[:_FEED_CAP]


async def _crawl_one_ica(client, token, acct, cs):
    """Crawla en ICA-butiks hela katalog -> catalog_store_prices + historik. Returnerar antal produkter."""
    sname = database.store_name("ica", acct)
    total_rows, prev = 0, None  # prev = (kategori, antal) -> emit en feed-post vid kategori-byte
    async for rows, total, _page, cat in catalog_crawl._ica_fetch_store(client, acct, token, pace=_PAGE_PACE):
        if prev and cat[0] != prev[0]:
            _emit("ica", sname, prev[0], prev[1])
        prev = cat
        if rows:
            _new, changed = database.upsert_store_prices("ica", acct, rows)
            database.catalog_upsert_metadata("ica", rows)  # union-metadata -> bläddra-vyn behåller produkten
            total_rows += len(rows)
            cs["rows"] += len(rows)
            cs["changed"] += changed
        cs["current"] = f"ICA {sname}: {total_rows}/{total}"
    if prev:
        _emit("ica", sname, prev[0], prev[1])
    database.mark_store_crawled("ica", acct, total_rows)
    return total_rows


async def _crawl_one_coop(client, ledger, cs):
    """Crawla en Coop-butiks (ledger) katalog -> catalog_store_prices + historik. Returnerar antal."""
    sname = database.store_name("coop", ledger)
    total_rows, prev = 0, None
    async for rows, _t, _p, cat in catalog_crawl._coop_fetch_store(client, ledger, pace=_PAGE_PACE):
        if prev and cat[0] != prev[0]:
            _emit("coop", sname, prev[0], prev[1])
        prev = cat
        if rows:
            _new, changed = database.upsert_store_prices("coop", ledger, rows)
            database.catalog_upsert_metadata("coop", rows)  # union-metadata -> bläddra-vyn behåller produkten
            total_rows += len(rows)
            cs["rows"] += len(rows)
            cs["changed"] += changed
        cs["current"] = f"Coop {sname}: {total_rows}"
    if prev:
        _emit("coop", sname, prev[0], prev[1])
    database.mark_store_crawled("coop", ledger, total_rows)
    return total_rows


async def _run_chain(client, chain, cap, concurrency, max_age_hours):
    """Kör per-butik-crawlen för EN kedja med adaptiv samtidighet (AIMD), på dess egna sub-state. Delas
    av den parallella wrappern - flera kedjor kör samtidigt med var sin AIMD-styrning mot sitt eget API."""
    cs = STORE_PRICE_STATE["chains"][chain]
    queue = [a for _, a in database.stores_to_crawl(chain=chain, cap=cap, max_age_hours=max_age_hours)]
    ceiling = max(_MIN_CONC, min(concurrency or _MAX_CONC, _MAX_CONC))
    cs.update(running=True, done=0, total=len(queue), stores_ok=0, rows=0, changed=0, errors=0,
              last_error=None, current=None, target=min(2, ceiling), active=0, cooldown=False,
              started_at=_now(), finished_at=None)
    ctl = {"ok_streak": 0, "waf_streak": 0, "cooldown_until": 0.0, "abort": False}
    err_counts = {}  # fel-fördelning {feltyp: antal} -> beständig i crawl_runs (se vad som gick fel)

    async def _run_one(acct):
        cs["active"] += 1
        try:
            if chain == "ica":
                token = await ica_token.get_token(client)  # cachad + auto-förnyad
                await _crawl_one_ica(client, token, acct, cs)
            else:  # coop - nyckeln resolvas i _coop_post (cachad, re-key vid 401/403)
                await _crawl_one_coop(client, acct, cs)
            cs["stores_ok"] += 1
            ctl["waf_streak"] = 0
            ctl["ok_streak"] += 1
            if ctl["ok_streak"] >= _RAMP_AFTER and cs["target"] < ceiling:  # additiv ökning
                cs["target"] += 1
                ctl["ok_streak"] = 0
        except Exception as e:  # noqa: BLE001
            cs["errors"] += 1
            cs["last_error"] = f"{type(e).__name__}: {e}"[:200]  # transport-fel har tom str(e) -> ta med typen
            err_counts[_err_key(e)] = err_counts.get(_err_key(e), 0) + 1  # fördelning per feltyp
            ctl["ok_streak"] = 0
            if _is_waf(e):
                ctl["waf_streak"] += 1
                cs["target"] = max(_MIN_CONC, cs["target"] // 2)  # multiplikativ minskning
                ctl["cooldown_until"] = time.monotonic() + _WAF_COOLDOWN
                if ctl["waf_streak"] >= _BREAKER:
                    ctl["abort"] = True
                    log.warning("store_crawl: %s circuit-breaker (%d WAF i rad) - avbryter", chain, _BREAKER)
        finally:
            cs["active"] -= 1
            cs["done"] += 1

    try:
        tasks = set()
        while (queue or tasks) and not ctl["abort"]:
            cooling = time.monotonic() < ctl["cooldown_until"]
            cs["cooldown"] = cooling
            # Gate på ANTAL schemalagda tasks (len(tasks)), INTE cs["active"]: active ökas inuti _run_one
            # som ännu inte körts efter create_task, så den står kvar tills loopen yield:ar -> annars startas
            # HELA kön på en gång (-> pool-utmattning/PoolTimeout på allt vid full skala).
            while queue and len(tasks) < cs["target"] and not cooling and not ctl["abort"]:
                tasks.add(asyncio.create_task(_run_one(queue.pop(0))))
            if not tasks:
                await asyncio.sleep(0.5)  # i cooldown utan aktiva -> vänta ut den
                continue
            _done, tasks = await asyncio.wait(tasks, timeout=0.5, return_when=asyncio.FIRST_COMPLETED)
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        database.recompute_store_aggregates(chain)  # materialisera intervall-aggregatet (price_min/max/stores)
    finally:
        cs.update(running=False, finished_at=_now(), current=None, active=0, cooldown=False)
        status = "avbruten" if ctl["abort"] else ("ok_med_fel" if cs["errors"] else "ok")
        database.record_crawl_run("store_prices", chain, started=cs["started_at"],
                                  finished=cs["finished_at"], status=status, rows=cs["rows"],
                                  changed=cs["changed"], errors=cs["errors"], stores_ok=cs["stores_ok"],
                                  stores_total=cs["total"], last_error=cs["last_error"],
                                  error_summary=err_counts or None)


async def crawl_store_prices(chain="ica", cap=None, concurrency=None, max_age_hours=20):
    """Crawla per-butik-priser för enabled+frågbara butiker. `chain` = ica | coop | both (kör ICA och Coop
    PARALLELLT - olika API:er, var sin adaptiva AIMD-styrning). Rotation äldst-först, `cap` per kedja,
    `max_age_hours` hoppar nyligen crawlade. Adaptiv samtidighet (tak = säkerhets-guardrail; `concurrency`
    = valfri manuell sänkning). Inom en butik sekventiell paginering. Bakgrund. Delad kategori-feed."""
    chains = ["ica", "coop"] if chain == "both" else ([chain] if chain in ("ica", "coop") else None)
    if chains is None:
        return {"status": "error", "detail": "Stödjer ica|coop|both."}
    to_run = [c for c in chains if not STORE_PRICE_STATE["chains"][c]["running"]]
    if not to_run:
        return {"status": "running"}
    async with apilog.make_client(follow_redirects=True) as client:
        await asyncio.gather(*(_run_chain(client, c, cap, concurrency, max_age_hours) for c in to_run))
    return {"status": "done", "chains": {c: {"stores_ok": STORE_PRICE_STATE["chains"][c]["stores_ok"],
                                             "rows": STORE_PRICE_STATE["chains"][c]["rows"]} for c in to_run}}
