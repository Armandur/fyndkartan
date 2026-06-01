import asyncio
import logging
from datetime import datetime, timezone

import httpx

from . import config
from .adapters import coop, hemkop, ica, lidl, willys
from .database import replace_chain
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
        async with httpx.AsyncClient(follow_redirects=True) as client:
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
