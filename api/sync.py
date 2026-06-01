import asyncio
import logging
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from croniter import croniter

from . import apilog, config
from .adapters import axfood_offers, coop, hemkop, ica, lidl, willys
from .database import codes_missing_category, get_conn, replace_chain, save_ean_meta
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
            )
    finally:
        STATE["running"] = False
        STATE["finished_at"] = _now()
    return STATE


# Antal butiker per Axfood-kedja att samla koder från vid förvärmning. Kampanjerna
# är i stort nationella, så ett urval täcker nästan hela kodmängden.
WARM_SAMPLE = 15


async def warm_axfood_eans():
    """Förvärm code->{EAN, kategori}-cachen för Willys/Hemköp (`/p/{code}`).

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
            missing = codes_missing_category(codes)  # saknar kategori (-> även EAN hämtas)
            for i in range(0, len(missing), 200):
                meta = await axfood_offers.fetch_p_meta(client, chain, missing[i : i + 200])
                save_ean_meta(meta)
                resolved += sum(1 for m in meta.values() if m.get("category"))
            log.info("EAN/kategori-förvärmning %s: %d koder, %d nya uppslag", chain, len(codes), len(missing))
    log.info("EAN/kategori-förvärmning klar (%d nya kategorier cachade)", resolved)


async def sync_and_warm():
    """Butikssynk följt av EAN-förvärmning (används av schemaläggare + uppstart)."""
    await run_sync()
    try:
        await warm_axfood_eans()
    except Exception:  # noqa: BLE001
        log.exception("EAN-förvärmning misslyckades")


async def run_scheduler(cron_expr, tz_name="Europe/Stockholm"):
    """Kör butikssynken enligt ett cron-uttryck (tomt/'off' = av).

    Cron ger både intervall ('0 */6 * * *') och bestämd tid ('0 4 * * *').
    Resilient: ett synkfel dödar inte loopen. Uppstartssynken hanteras separat."""
    expr = (cron_expr or "").strip()
    if not expr or expr.lower() in ("off", "disabled", "none"):
        log.info("Schemalagd synk avstängd (SYNC_CRON tomt)")
        return
    if not croniter.is_valid(expr):
        log.error("Ogiltig SYNC_CRON '%s' - schemalagd synk avstängd", expr)
        return
    try:
        tz = ZoneInfo(tz_name)
    except Exception:  # noqa: BLE001
        log.warning("Okänd SYNC_TZ '%s', faller tillbaka på Europe/Stockholm", tz_name)
        tz = ZoneInfo("Europe/Stockholm")

    log.info("Schemalagd synk aktiv: cron '%s' (%s)", expr, tz_name)
    while True:
        now = datetime.now(tz)
        nxt = croniter(expr, now).get_next(datetime)
        delay = max(1.0, (nxt - now).total_seconds())
        log.info("Nästa schemalagda synk: %s (om %.0f min)", nxt.strftime("%Y-%m-%d %H:%M"), delay / 60)
        await asyncio.sleep(delay)
        try:
            log.info("Schemalagd synk startar")
            await sync_and_warm()
        except Exception:  # noqa: BLE001
            log.exception("Schemalagd synk misslyckades")
