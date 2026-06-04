import asyncio
import logging
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from croniter import croniter

from . import apilog, config, details
from .adapters import axfood_offers, citygross, coop, hemkop, ica, lidl, willys
from .matching import normalize_ean
from .database import (
    axfood_offer_codes,
    backfill_catalog_eans,
    catalog_axfood_codes_missing_ean,
    catalog_names_for_codes,
    codes_missing_category,
    coop_offer_eans,
    get_conn,
    get_product_categories,
    ica_offer_eans,
    product_info_eans,
    replace_chain,
    save_ean_meta,
    save_product_info,
    product_info_fresh_set,
)
from .geo import grid

log = logging.getLogger("matbutiker")


def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


STATE = {
    "running": False,
    "started_at": None,
    "finished_at": None,
    "chains": {
        c: {"status": "idle", "count": 0, "error": None, "last_sync": None}
        for c in config.CHAINS
    },
}


async def _run_one(chain, coro):
    STATE["chains"][chain].update(status="running", error=None)
    try:
        stores = await coro
        replace_chain(chain, stores)
        STATE["chains"][chain].update(
            status="ok", count=len(stores), error=None, last_sync=_now()
        )
        log.info("sync %s: %d butiker", chain, len(stores))
    except Exception as e:  # noqa: BLE001 - logga, svälj inte
        STATE["chains"][chain].update(status="error", error=str(e))
        log.exception("sync %s misslyckades", chain)


async def run_sync():
    if STATE["running"]:
        return STATE
    STATE["running"] = True
    STATE["started_at"] = _now()
    STATE["finished_at"] = None
    boxes = grid(config.SWEDEN_BOUNDS, config.LIDL_BOX_DLAT, config.LIDL_BOX_DLNG)
    try:
        async with apilog.make_client(follow_redirects=True) as client:
            await asyncio.gather(
                _run_one("ica", ica.fetch_all(client, config.ICA_TOKEN)),
                _run_one("coop", coop.fetch_all(client, config.COOP_KEY)),
                _run_one("willys", willys.fetch_all(client)),
                _run_one("hemkop", hemkop.fetch_all(client)),
                _run_one(
                    "lidl",
                    lidl.fetch_all(client, config.LIDL_KEY, boxes, config.LIDL_SLEEP),
                ),
                _run_one("citygross", citygross.fetch_all(client)),
            )
    finally:
        STATE["running"] = False
        STATE["finished_at"] = _now()
    return STATE


# Antal butiker per Axfood-kedja att samla koder från vid förvärmning. Kampanjerna
# är i stort nationella, så ett urval täcker nästan hela kodmängden.
WARM_SAMPLE = 15


def _piggyback_axfood_info(meta):
    """Spara product_info (partial) ur /p/{code}-svaren vi ändå hämtade i EAN-warmingen - gratis
    näring/ingredienser för Willys/Hemköp. Skip-if-fresh (batchat); on-demand-öppning uppgraderar
    sedan till full korsskällig merge."""
    cand = {e: m["info"] for m in meta.values()
            if m.get("info") and (e := normalize_ean(m.get("ean")))}
    if not cand:
        return
    fresh = product_info_fresh_set(cand.keys())
    for ean, info in cand.items():
        if ean not in fresh:
            save_product_info(ean, details._merge([info]), partial=True)


async def _resolve_axfood_codes(client, chain, codes):
    """Resolva code->{EAN, kategori, ursprung} för de koder som saknar kategori (`/p/{code}`),
    batchat, och spara i ean_cache. Returnerar antal nya kategori-uppslag."""
    missing = codes_missing_category(codes)  # saknar kategori (-> även EAN hämtas)
    resolved = 0
    for i in range(0, len(missing), 200):
        meta = await axfood_offers.fetch_p_meta(client, chain, missing[i : i + 200])
        save_ean_meta(meta)
        _piggyback_axfood_info(meta)
        resolved += sum(1 for m in meta.values() if m.get("category"))
    log.info("EAN/kategori-förvärmning %s: %d koder, %d nya uppslag", chain, len(codes), len(missing))
    return resolved


async def warm_axfood_eans():
    """Förvärm code->{EAN, kategori}-cachen för Willys/Hemköp (`/p/{code}`) via ett butiks-urval.

    Ger snabb compare (EAN) OCH fyller Willys saknade offer-kategori (Willys-kampanjer
    bär ingen kategori; produktdetaljen gör det). Cachen är global -> värma en gång
    gynnar alla områden/butiker. Idempotent: bara koder utan kategori hämtas."""
    conn = get_conn()
    samples = {}
    for chain in ("willys", "hemkop"):
        rows = conn.execute(
            "SELECT store_id FROM stores WHERE chain=? ORDER BY RANDOM() LIMIT ?",
            (chain, WARM_SAMPLE),
        ).fetchall()
        samples[chain] = [r["store_id"] for r in rows]
    conn.close()

    resolved = 0
    async with apilog.make_client(follow_redirects=True) as client:
        for chain, ids in samples.items():
            if not ids:
                continue
            sem = asyncio.Semaphore(6)

            async def grab(sid):
                async with sem:
                    try:
                        offers = await axfood_offers.fetch_offers(client, chain, sid)
                        return [o["offer_id"] for o in offers]
                    except Exception as e:  # noqa: BLE001
                        log.warning("förvärmning %s/%s misslyckades: %s", chain, sid, e)
                        return []

            lists = await asyncio.gather(*(grab(s) for s in ids))
            codes = {c for lst in lists for c in lst}
            resolved += await _resolve_axfood_codes(client, chain, codes)
    log.info("EAN/kategori-förvärmning klar (%d nya kategorier cachade)", resolved)


# Progress för Axfood-katalog-EAN-warmingen (visas i konsolens Sortiment-flik).
CATALOG_EAN_STATE = {"running": False, "total": 0, "done": 0, "resolved": 0, "empty": 0,
                     "blocked": 0, "updated": 0, "current_chain": None, "cooldown": False,
                     "skipped_chains": [], "started_at": None, "finished_at": None,
                     "error": None, "recent": []}
_EAN_FEED_MAX = 60


def _ean_feed(chain, meta):
    """Lägg nyss resolvade produkter (med EAN) i CATALOG_EAN_STATE['recent'] för feed-visningen
    (samma {chain, name, ean}-form som crawlens feed)."""
    got = {code: m["ean"] for code, m in meta.items() if m.get("ean")}
    if not got:
        return
    names = catalog_names_for_codes(chain, list(got))
    rows = [{"chain": chain, "name": names[code], "ean": e}
            for code, raw in got.items() if names.get(code) and (e := normalize_ean(raw))]
    buf = CATALOG_EAN_STATE["recent"]
    buf[:0] = rows[::-1]  # nyast först
    del buf[_EAN_FEED_MAX:]


async def warm_axfood_catalog_eans(cap=None, chain=None):
    """Resolva Axfood-KATALOGkoder (catalog_products utan EAN) till EAN via `/p/{code}`, fyll
    ean_cache och backfilla `catalog_products.ean` (normaliserat) -> Willys/Hemköp slås ihop
    cross-chain med kedjor som redan har EAN. `cap` = max koder/kedja (None = alla, engångs-bulk),
    `chain` = bara en kedja (willys|hemkop, None = båda). Hämtar bara ej-cachade koder
    (`codes_missing_category`). Progress i CATALOG_EAN_STATE."""
    if CATALOG_EAN_STATE["running"]:
        return 0
    src = catalog_axfood_codes_missing_ean()
    if chain:
        src = {chain: src[chain]} if chain in src else {}
    to_fetch = {}
    for ch, codes in src.items():
        miss = codes_missing_category(codes)
        if cap:
            miss = miss[:cap]
        if miss:
            to_fetch[ch] = miss
    CATALOG_EAN_STATE.update(running=True, total=sum(len(v) for v in to_fetch.values()), done=0,
                             resolved=0, empty=0, blocked=0, updated=0, current_chain=None,
                             cooldown=False, skipped_chains=[], started_at=_now(), finished_at=None,
                             error=None, recent=[])
    try:
        async with apilog.make_client(follow_redirects=True) as client:
            for chain, codes in to_fetch.items():
                CATALOG_EAN_STATE["current_chain"] = chain
                i, block_streak = 0, 0
                while i < len(codes):
                    batch = codes[i:i + 200]
                    meta = await axfood_offers.fetch_p_meta(client, chain, batch)
                    nblock = sum(1 for m in meta.values() if m.get("blocked"))
                    if nblock > len(batch) // 2:  # WAF aktiv -> circuit-breaker (cooldown + om-försök)
                        block_streak += 1
                        if block_streak > config.CATALOG_EAN_MAX_BLOCKS:
                            log.warning("Axfood-EAN: %s fortsatt blockerad -> hoppar resten (%d koder)",
                                        chain, len(codes) - i)
                            CATALOG_EAN_STATE["skipped_chains"].append(chain)
                            break
                        CATALOG_EAN_STATE["cooldown"] = True
                        await asyncio.sleep(config.CATALOG_EAN_COOLDOWN)
                        CATALOG_EAN_STATE["cooldown"] = False
                        continue  # samma batch igen (i oförändrat)
                    block_streak = 0
                    ok = {c: m for c, m in meta.items() if not m.get("blocked")}
                    save_ean_meta(ok)  # cacha ej blockerade
                    _piggyback_axfood_info(ok)  # gratis product_info (partial) ur samma /p/{code}-svar
                    _ean_feed(chain, meta)  # mata in resolvade i feeden
                    CATALOG_EAN_STATE["done"] += len(batch)
                    CATALOG_EAN_STATE["resolved"] += sum(1 for m in meta.values() if m.get("ean"))
                    CATALOG_EAN_STATE["empty"] += sum(1 for m in meta.values() if not m.get("ean") and not m.get("blocked"))
                    CATALOG_EAN_STATE["blocked"] += nblock
                    i += 200
                    if config.CATALOG_EAN_PACE and i < len(codes):
                        await asyncio.sleep(config.CATALOG_EAN_PACE)  # skonsam takt mellan batchar
        CATALOG_EAN_STATE["updated"] = backfill_catalog_eans()
        log.info("Axfood-katalog-EAN klar: %d resolved, %d empty, %d blockerade, %d backfilllade%s",
                 CATALOG_EAN_STATE["resolved"], CATALOG_EAN_STATE["empty"], CATALOG_EAN_STATE["blocked"],
                 CATALOG_EAN_STATE["updated"],
                 f" (hoppade: {','.join(CATALOG_EAN_STATE['skipped_chains'])})" if CATALOG_EAN_STATE["skipped_chains"] else "")
        return CATALOG_EAN_STATE["updated"]
    except Exception as e:  # noqa: BLE001
        CATALOG_EAN_STATE["error"] = str(e)
        log.exception("Axfood-katalog-EAN-warming misslyckades")
        return 0
    finally:
        CATALOG_EAN_STATE.update(running=False, current_chain=None, cooldown=False, finished_at=_now())


async def warm_axfood_eans_cached():
    """Förvärm Axfood code->EAN/kategori ur de REDAN cachade offers (efter en sweep, då offers-
    cachen täcker alla butiker -> hela kodmängden, inkl. ev. regionala koder som butiks-urvalet i
    warm_axfood_eans missar). Inget campaigns-refetch: koderna läses ur offers-tabellen."""
    by_chain = axfood_offer_codes()
    if not by_chain:
        return
    resolved = 0
    async with apilog.make_client(follow_redirects=True) as client:
        for chain, codes in by_chain.items():
            resolved += await _resolve_axfood_codes(client, chain, codes)
    log.info("Axfood-EAN-förvärmning ur cache klar (%d nya kategorier cachade)", resolved)


# EAN per batch till Coops personalization-API (POST tar en array).
COOP_WARM_BATCH = 40


async def warm_coop_categories():
    """Förvärm product_info-kategori för Coop-EAN via personalization-API (batchat).

    Coops offer-nivå (Färsk/Kolonial/Nonfood) är för grov och delvis felklassad;
    produktdetaljens navCategories ger rätt kategori. Cachen är EAN-global (butiks-
    oberoende). Idempotent: bara EAN utan redan mappbar kategori hämtas. Lagrar full
    product_info (gynnar även lazy detalj-vyn)."""
    eans = coop_offer_eans()
    if not eans:
        return
    have = get_product_categories(eans)  # EAN med redan mappbar kategori
    todo = [e for e in eans if e not in have]
    if not todo:
        log.info("Coop-kategoriförvärmning: inget att göra (%d EAN klara)", len(eans))
        return
    saved = 0
    async with apilog.make_client(follow_redirects=True) as client:
        for i in range(0, len(todo), COOP_WARM_BATCH):
            batch = todo[i : i + COOP_WARM_BATCH]
            try:
                infos = await details.fetch_coop_batch(client, batch)
            except Exception as e:  # noqa: BLE001
                log.warning("Coop-kategoriförvärmning batch misslyckades: %s", e)
                continue
            for ean, info in infos.items():
                save_product_info(ean, info)
                saved += 1
    log.info("Coop-kategoriförvärmning klar: %d EAN att hämta, %d cachade", len(todo), saved)


# ICA-detalj saknar batch-API (sök + sida per EAN) -> capa per synk, warma inkrementellt.
# Låg parallellism (handla.ica.se throttlar burst; en throttle blir tyst negativ-cachad).
ICA_WARM_CAP = 40
ICA_WARM_CONCURRENCY = 2


async def warm_ica_categories(cap=ICA_WARM_CAP):
    """Förvärm product_info för ICA-offer-EAN som saknar mappbar kategori (ICA:s offer-nivå
    är grov). Capad + egna märken (731869) först (de har bara ICA som källa); inkrementell
    över flera synkar. Hoppar EAN som redan finns i product_info (RÅ membership - utgångna
    negativa ska inte re-warmas, lazy route:n sköter retry via TTL). Egna märken hämtas via
    ICA-bara (Coop/Axfood saknar dem); branded via fulla fetch_for_ean."""
    eans = ica_offer_eans()
    if not eans:
        return
    have = get_product_categories(eans)          # redan mappbar kategori
    tried = product_info_eans()                  # redan hämtat (positivt el. negativt)
    todo = [e for e in eans if e not in have and e not in tried]
    if not todo:
        log.info("ICA-kategoriförvärmning: inget att göra (%d EAN)", len(eans))
        return
    todo.sort(key=lambda e: 0 if str(e).lstrip("0").startswith("731869") else 1)
    todo = todo[:cap]
    sem = asyncio.Semaphore(ICA_WARM_CONCURRENCY)
    saved = 0

    async def warm(ean, client):
        nonlocal saved
        async with sem:
            try:
                if str(ean).lstrip("0").startswith("731869"):
                    info = await details.fetch_ica_only(client, ean)
                else:
                    info = await details.fetch_for_ean(client, ean)
            except Exception as e:  # noqa: BLE001
                log.warning("ICA-kategoriförvärmning %s misslyckades: %s", ean, e)
                return
            save_product_info(ean, info)  # även None -> negativ cache (undviker re-warm)
            if info:
                saved += 1

    async with apilog.make_client(follow_redirects=True) as client:
        await asyncio.gather(*(warm(e, client) for e in todo))
    log.info("ICA-kategoriförvärmning klar: %d EAN hämtade, %d med info", len(todo), saved)


async def warm_after_sweep():
    """Förvärmning efter en erbjudande-sweep: stänger EAN/kategori-luckan för precis de offers
    sweepen nyss cachade. Axfood-EAN ur cachade koder (komplett, ej sampling), Coop+ICA-kategori
    ur cachade offer-EAN (ICA capad per körning, inkrementell). Resilient - ett fel stoppar inte."""
    for label, coro in (("Axfood-EAN", warm_axfood_eans_cached()),
                        ("Coop-kategori", warm_coop_categories()),
                        ("ICA-kategori", warm_ica_categories())):
        try:
            await coro
        except Exception:  # noqa: BLE001
            log.exception("%s-förvärmning efter sweep misslyckades", label)


async def sync_and_warm():
    """Butikssynk följt av EAN-förvärmning (används av schemaläggare + uppstart)."""
    await run_sync()
    try:
        await warm_axfood_eans()
    except Exception:  # noqa: BLE001
        log.exception("EAN-förvärmning misslyckades")
    try:
        await warm_coop_categories()
    except Exception:  # noqa: BLE001
        log.exception("Coop-kategoriförvärmning misslyckades")
    try:
        await warm_ica_categories()
    except Exception:  # noqa: BLE001
        log.exception("ICA-kategoriförvärmning misslyckades")


SCHEDULER_CHECK = 30.0  # s: hur ofta cron/tz omläses (konsol-ändringar slår igenom inom detta)


async def run_scheduler(cron_source, tz_source="Europe/Stockholm", job=None, label="synk"):
    """Kör `job` enligt ett cron-uttryck. `cron_source`/`tz_source` är en sträng ELLER en 0-arg
    callable som resolvas VARJE varv -> konsol-ändringar slår igenom utan omstart (inom
    SCHEDULER_CHECK). Tomt/'off'/ogiltigt cron = pausad (loopen lever, plockar upp giltig cron
    senare). Resilient: ett jobbfel dödar inte loopen.

    Långa väntor sker i bitar (CHECK) så omläsning märks; den SISTA väntan sover exakt fram till
    körningen och kör sedan UTAN omräkning (annars skulle get_next() efter uppvaknandet hoppa till
    nästa tillfälle och missa körningen)."""
    job = job or sync_and_warm

    def _cron():
        return ((cron_source() if callable(cron_source) else cron_source) or "").strip()

    def _tz():
        name = (tz_source() if callable(tz_source) else tz_source) or "Europe/Stockholm"
        try:
            return ZoneInfo(name), name
        except Exception:  # noqa: BLE001
            return ZoneInfo("Europe/Stockholm"), "Europe/Stockholm"

    log.info("Schemaläggare '%s' startad", label)
    last_sig = None
    while True:
        expr = _cron()
        if not expr or expr.lower() in ("off", "disabled", "none") or not croniter.is_valid(expr):
            if last_sig != "paused":
                log.info("Schemalagd %s pausad (cron '%s')", label, expr)
                last_sig = "paused"
            await asyncio.sleep(SCHEDULER_CHECK)
            continue
        tz, tz_name = _tz()
        now = datetime.now(tz)
        nxt = croniter(expr, now).get_next(datetime)
        delay = (nxt - now).total_seconds()
        sig = (expr, tz_name, nxt.isoformat())
        if sig != last_sig:
            log.info("Schemalagd %s: nästa %s (%s, cron '%s')", label,
                     nxt.strftime("%Y-%m-%d %H:%M"), tz_name, expr)
            last_sig = sig
        if delay > SCHEDULER_CHECK:
            await asyncio.sleep(SCHEDULER_CHECK)  # vänta i bitar, läs om cron/tz nästa varv
            continue
        await asyncio.sleep(max(1.0, delay))  # exakt fram till körningen - INGEN omräkning efter detta
        try:
            log.info("Schemalagd %s startar", label)
            await job()
        except Exception:  # noqa: BLE001
            log.exception("Schemalagd %s misslyckades", label)
