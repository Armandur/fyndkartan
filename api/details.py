"""Lazy rik produktdetalj (ingredienser, näring, ursprung) per (chain, ean) för
paringsvyn. Normaliserat schema per kedja; cachas i product_details.

Axfood (Willys/Hemköp) byggt: `/axfood/rest/p/{code}` (code slås upp via ean_cache).
ICA/Coop: ej byggt än (kräver egen produkt-API-research) -> None.
"""

import json
import logging

from . import config, database as db
from .adapters import keys
from .adapters.axfood_offers import DOMAIN, UA

log = logging.getLogger("matbutiker")
_AXFOOD = ("willys", "hemkop")

_coop_key = None  # skrapad personalization-nyckel (cache), scrape-on-401


def _axfood_code(ean):
    conn = db.get_conn()
    row = conn.execute("SELECT code FROM ean_cache WHERE ean=? LIMIT 1", (str(ean),)).fetchone()
    conn.close()
    return row["code"] if row else None


async def _fetch_axfood(client, chain, ean):
    code = _axfood_code(ean)
    domain = DOMAIN.get(chain)
    if not code or not domain:
        return None
    r = await client.get(
        f"https://{domain}/axfood/rest/p/{code}",
        headers={"Accept": "application/json", "User-Agent": UA}, timeout=15,
    )
    if r.status_code != 200:
        return None
    d = r.json()
    nutrition = [
        {"label": n.get("typeCode"), "value": n.get("value"), "unit": n.get("unitCode")}
        for n in (d.get("nutritionsFactList") or []) if n.get("value")
    ]
    basis = (d.get("nutrientHeaders") or [{}])[0]
    s = lambda k: (d.get(k) or "").strip() or None
    return {
        "description": s("description"),
        "ingredients": s("ingredients"),
        "origin": s("tradeItemCountryOfOrigin"),
        "province": s("provinceStatement"),
        "storage": s("consumerStorageInstructions"),
        "nutrition": nutrition,
        "nutrition_basis": {
            "value": basis.get("nutrientBasisQuantity"),
            "unit": basis.get("nutrientBasisQuantityMeasurementUnitCode"),
        } if nutrition else None,
        "labels": d.get("labels") or [],
    }


async def _resolve_coop_key(client, force=False):
    """Env-nyckel om satt, annars skrapad personalization-nyckel (cache)."""
    global _coop_key
    if config.COOP_PERSO_KEY and not force:
        return config.COOP_PERSO_KEY
    if _coop_key and not force:
        return _coop_key
    _coop_key = await keys.scrape_coop_perso_key(client)
    return _coop_key


async def _coop_post(client, ean, key):
    return await client.post(
        "https://external.api.coop.se/personalization/search/entities/by-id",
        params={"api-version": "v1", "store": config.COOP_DETAIL_STORE,
                "groups": "CUSTOMER_PRIVATE", "direct": "false"},
        headers={
            "Ocp-Apim-Subscription-Key": key, "Content-Type": "application/json",
            "Origin": "https://www.coop.se", "Accept": "application/json", "User-Agent": UA,
        },
        content=json.dumps([str(ean)]), timeout=15,
    )


async def _fetch_coop(client, ean):
    """Coop personalization-API: POST med EAN-array -> produktentitet med ingredienser,
    ursprung och förvaring. Produktdata är butiksoberoende (fast store-param)."""
    key = await _resolve_coop_key(client)
    r = await _coop_post(client, ean, key)
    if r.status_code in (401, 403):
        log.info("Coop detalj: %s, skrapar ny personalization-nyckel", r.status_code)
        key = await _resolve_coop_key(client, force=True)
        r = await _coop_post(client, ean, key)
    if r.status_code != 200:
        return None
    items = ((r.json().get("results") or {}).get("items")) or []
    if not items:
        return None
    p = items[0]
    s = lambda v: (v or "").strip() or None
    origin = ", ".join(x.get("value") for x in (p.get("countryOfOriginCodes") or []) if x.get("value")) or None
    storage = (p.get("consumerInstructions") or {}).get("storageInstructions")
    return {
        "description": s(p.get("description")),
        "ingredients": s(p.get("listOfIngredients")),
        "origin": origin,
        "province": None,
        "storage": s(storage),
        "nutrition": [],  # näringsvärden saknas oftast i denna respons; ej parsad i v1
        "nutrition_basis": None,
        "labels": [],
    }


async def fetch_for_ean(client, ean, prefer_chain=None):
    """Produktinfo för en EAN (EAN-global). Axfood-native först om EAN finns i
    ean_cache (rikare - har näring), annars Coops EAN-DB (täcker branded varor i alla
    kedjor; egna märkesvaror finns bara i sin kedja). `source` = varifrån datan kom.
    First-hit vinner (cachen är EAN-nyckad; senare berikning kan skriva över)."""
    ax_chain = prefer_chain if prefer_chain in _AXFOOD else "willys"
    native = await _fetch_axfood(client, ax_chain, ean)  # no-op (ingen HTTP) om EAN ej i ean_cache
    if native and native.get("ingredients"):
        native["source"] = ax_chain
        return native
    fb = await _fetch_coop(client, ean)
    if fb and (fb.get("ingredients") or fb.get("description")):
        fb["source"] = "coop"
        return fb
    if native:
        native["source"] = ax_chain
        return native
    return None
