import asyncio
import logging
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from croniter import croniter

from . import apilog, config, details
from .adapters import axfood_offers, citygross, coop, hemkop, ica, lidl, willys
from .database import (
    axfood_offer_codes,
    codes_missing_category,
    coop_offer_eans,
    get_conn,
    get_product_categories,
    ica_offer_eans,
    product_info_eans,
    replace_chain,
    save_ean_meta,
    save_product_info,
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


async def _resolve_axfood_codes(client, chain, codes):
    """Resolva code->{EAN, kategori, ursprung} för de koder som saknar kategori (`/p/{code}`),
    batchat, och spara i ean_cache. Returnerar antal nya kategori-uppslag."""
    missing = codes_missing_category(codes)  # saknar kategori (-> även EAN hämtas)
    resolved = 0
    for i in range(0, len(missing), 200):
        meta = await axfood_offers.fetch_p_meta(client, chain, missing[i : i + 200])
        save_ean_meta(meta)
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


async def run_scheduler(cron_expr, tz_name="Europe/Stockholm", job=None, label="synk"):
    """Kör ett jobb enligt ett cron-uttryck (tomt/'off' = av). `job` är en async-callable
    (default `sync_and_warm`); `label` används i loggarna. Används för både butikssynken och
    erbjudande-sweepen.

    Cron ger både intervall ('0 */6 * * *') och bestämd tid ('0 4 * * *').
    Resilient: ett jobbfel dödar inte loopen. Uppstartskörning hanteras separat."""
    job = job or sync_and_warm
    expr = (cron_expr or "").strip()
    if not expr or expr.lower() in ("off", "disabled", "none"):
        log.info("Schemalagd %s avstängd (tomt cron-uttryck)", label)
        return
    if not croniter.is_valid(expr):
        log.error("Ogiltigt cron-uttryck '%s' - schemalagd %s avstängd", expr, label)
        return
    try:
        tz = ZoneInfo(tz_name)
    except Exception:  # noqa: BLE001
        log.warning("Okänd tidszon '%s', faller tillbaka på Europe/Stockholm", tz_name)
        tz = ZoneInfo("Europe/Stockholm")

    log.info("Schemalagd %s aktiv: cron '%s' (%s)", label, expr, tz_name)
    while True:
        now = datetime.now(tz)
        nxt = croniter(expr, now).get_next(datetime)
        delay = max(1.0, (nxt - now).total_seconds())
        log.info("Nästa schemalagda %s: %s (om %.0f min)", label, nxt.strftime("%Y-%m-%d %H:%M"), delay / 60)
        await asyncio.sleep(delay)
        try:
            log.info("Schemalagd %s startar", label)
            await job()
        except Exception:  # noqa: BLE001
            log.exception("Schemalagd %s misslyckades", label)
