"""Erbjudande-domänen: lazy hämtning/färskhet + bulk-förhämtning (sweep).

Utbrutet ur main.py (REVIEW Fynd 2). main.py importerar härifrån. Inga FastAPI-routes här -
bara logiken; routes och schemaläggning bor kvar i main.py/lifespan.
"""

import asyncio
import json
import logging
import random
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import text

from . import apilog, config, database
from .adapters import axfood_offers, citygross_offers, coop_offers, ica_offers
from .database import get_conn, get_store_offers, offers_fetched_at, replace_store_offers
from .sync import warm_after_sweep

log = logging.getLogger("matbutiker")

OFFERS_TTL = timedelta(hours=6)  # erbjudanden uppdateras veckovis; 6h cache räcker gott
OFFERS_MIN_REFRESH = timedelta(minutes=30)  # golv för validitets-driven tidig refresh
SUPPORTED_OFFER_CHAINS = ("ica", "willys", "hemkop", "coop", "citygross")


def _offers_expired(chain, store_id):
    """True om någon cachad offer har valid_to i det förflutna -> set:et är inte längre
    aktuellt. valid_to är ISO-datum (YYYY-MM-DD), så strängjämförelse räcker."""
    today = datetime.now(ZoneInfo(config.SYNC_TZ)).date().isoformat()
    conn = get_conn()
    row = conn.execute(
        text("SELECT MIN(valid_to) AS m FROM offers WHERE chain=:chain AND store_id=:store "
             "AND valid_to IS NOT NULL AND valid_to != ''"),
        {"chain": chain, "store": str(store_id)},
    ).fetchone()
    conn.close()
    return bool(row and row["m"]) and row["m"] < today


def _offers_fresh(chain, store_id):
    ts = offers_fetched_at(chain, store_id)
    if not ts:
        return False
    try:
        fetched = datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return False
    age = datetime.now(timezone.utc) - fetched
    if age >= OFFERS_TTL:
        return False
    # Tidigare refresh än 6h om validitetstiden passerat - men inte oftare än golvet
    # (annars loop om källan fortsatt listar en utgången offer).
    if age >= OFFERS_MIN_REFRESH and _offers_expired(chain, store_id):
        return False
    return True


async def _fetch_offers_for(client, chain, store_id, link_offers, native_json):
    if chain == "ica":
        return await ica_offers.fetch_offers(client, link_offers, store_id)
    if chain == "coop":
        native = json.loads(native_json) if native_json else {}
        return await coop_offers.fetch_offers(
            client, store_id, native.get("ledgerAccountNumber"), config.COOP_OFFERS_KEY
        )
    if chain == "citygross":
        return await citygross_offers.fetch_offers(client, store_id)
    return await axfood_offers.fetch_offers(client, chain, store_id)  # willys / hemkop


async def _ensure_offers(client, chain, store_id, link_offers, native_json, refresh=False):
    """Returnera butikens erbjudanden ur cache; hämta live om saknas/för gammalt."""
    if not refresh and _offers_fresh(chain, store_id):
        return get_store_offers(chain, store_id)
    if chain not in SUPPORTED_OFFER_CHAINS:
        return get_store_offers(chain, store_id)
    offers = await _fetch_offers_for(client, chain, store_id, link_offers, native_json)
    replace_store_offers(chain, store_id, offers)
    return get_store_offers(chain, store_id)


# ---- Bulk-förhämtning av erbjudanden (sweep) ----
# Proaktiv motsats till lazy-hämtningen: går igenom ALLA offer-stödda butiker och hämtar de
# som inte är färska (_offers_fresh, som redan är valid_to-medveten). Efter en kall fyllning
# refetchas alltså bara butiker vars offers gått ut. Rate-limitad per kedja (semafor + paus),
# back-off med retry per butik, och en circuit breaker som pausar en kedja vars API:t spottar fel.
SWEEP_ERROR_SAMPLE = 8  # antal sparade fel-detaljer per kedja (för konsolen)
SWEEP_STATE = {
    "running": False, "started_at": None, "finished_at": None, "force": False,
    "chains": {c: {"status": "idle", "total": 0, "fetched": 0, "skipped": 0, "errors": 0, "last_errors": []}
               for c in SUPPORTED_OFFER_CHAINS},
}


async def _sweep_one_store(client, chain, store, force):
    """Hämta en butiks erbjudanden om de inte är färska (om inte force). Retry + exponentiell
    back-off vid fel. Returnerar ('fetched'|'skipped'|'error', fel-detalj eller None)."""
    sid = str(store["store_id"])
    if not force and _offers_fresh(chain, sid):
        return "skipped", None
    for attempt in range(config.OFFERS_SWEEP_RETRIES):
        try:
            offers = await _fetch_offers_for(client, chain, sid, store["link_offers"], store["native"])
            # offloada till tråd: parallella sweep-workers landar annars sina (tunga: archive+DELETE+
            # INSERT) skrivningar tätt på event-loopen. Per-butik-rader -> ingen lås-trängsel mellan workers.
            await asyncio.to_thread(replace_store_offers, chain, sid, offers)
            return "fetched", None
        except Exception as e:  # noqa: BLE001
            if attempt + 1 >= config.OFFERS_SWEEP_RETRIES:
                log.warning("sweep %s/%s misslyckades slutgiltigt: %s", chain, sid, e)
                return "error", f"{sid}: {e}"
            await asyncio.sleep(config.OFFERS_SWEEP_BACKOFF * (3 ** attempt) + random.uniform(0, 0.5))
    return "error", f"{sid}: okänt fel"


async def _sweep_chain(client, chain, stores, force):
    st = SWEEP_STATE["chains"][chain]
    st.update(status="running", total=len(stores), fetched=0, skipped=0, errors=0, last_errors=[])
    sem = asyncio.Semaphore(config.OFFERS_SWEEP_CONCURRENCY)
    streak = 0       # fel i rad -> circuit breaker
    tripped = False

    async def worker(store):
        nonlocal streak, tripped
        async with sem:
            if tripped:
                return
            res, detail = await _sweep_one_store(client, chain, store, force)
            if res == "fetched":
                st["fetched"] += 1
                streak = 0
                await asyncio.sleep(config.OFFERS_SWEEP_PACE)  # håller sem -> sprider lasten
            elif res == "skipped":
                st["skipped"] += 1
            else:
                st["errors"] += 1
                streak += 1
                if len(st["last_errors"]) < SWEEP_ERROR_SAMPLE:
                    st["last_errors"].append(detail)
                if streak >= config.OFFERS_SWEEP_CIRCUIT:
                    tripped = True
                    log.error("sweep %s: %d fel i rad - circuit breaker, pausar kedjan", chain, streak)

    await asyncio.gather(*(worker(s) for s in stores))
    st["status"] = "tripped" if tripped else "ok"


async def sweep_offers(force=False):
    """Bulk-förhämta erbjudanden för alla offer-stödda butiker. Hoppar färska (om inte force);
    arkiverar prishistorik via replace_store_offers. Kedjorna sveps parallellt, butiker inom en
    kedja rate-limitat. Idempotent och säker att köra ofta - de flesta butiker hoppas."""
    if SWEEP_STATE["running"]:
        return SWEEP_STATE
    SWEEP_STATE.update(running=True, force=force,
                       started_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                       finished_at=None)
    by_chain = database.offer_stores(SUPPORTED_OFFER_CHAINS)
    for c in SUPPORTED_OFFER_CHAINS:
        SWEEP_STATE["chains"][c].update(status="idle", total=len(by_chain.get(c, [])),
                                        fetched=0, skipped=0, errors=0)
    try:
        async with apilog.make_client(follow_redirects=True) as client:
            await asyncio.gather(*(_sweep_chain(client, c, by_chain.get(c, []), force)
                                   for c in SUPPORTED_OFFER_CHAINS))
        # Stäng EAN/kategori-luckan för precis de offers vi just cachade (Axfood-EAN ur de nya
        # koderna, Coop+ICA-kategori). Bara om något faktiskt hämtades - annars inget nytt att warma.
        if any(SWEEP_STATE["chains"][c]["fetched"] for c in SUPPORTED_OFFER_CHAINS):
            await warm_after_sweep()
    finally:
        SWEEP_STATE.update(running=False,
                           finished_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))
    log.info("Erbjudande-sweep klar (force=%s): %s", force,
             {c: {k: SWEEP_STATE["chains"][c][k] for k in ("fetched", "skipped", "errors")}
              for c in SUPPORTED_OFFER_CHAINS})
    return SWEEP_STATE
