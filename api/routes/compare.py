"""Cross-chain prisjämförelse + favoriternas erbjudanden. Utbrutet ur main.py (REVIEW Fynd 2,
pass 3). Plain APIRouter med per-route gating verbatim: compare-routerna kräver `require_consumer`,
`/v1/favorites/offers` kräver inloggad app-användare (`auth.current_user`, returnerar egen 401).
Fulla paths -> identiska URL:er.

`/v1/favorites/offers` bor här (inte hos övriga /v1/favorites i main.py) för att den delar
`_compare_rows`/`_resolve_axfood_eans` - närmare jämförelse-domänen än favorit-CRUD:en."""
import asyncio
import logging

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse
from sqlalchemy import bindparam, text

from .. import apilog, auth, database, matching, schemas
from ..adapters import axfood_offers
from ..database import get_cached_eans, get_conn, save_eans
from ..deps import require_consumer
from ..geo import haversine
from ..offers import _ensure_offers

log = logging.getLogger("matbutiker")

router = APIRouter()

COMPARE_CHAINS = ("ica", "coop", "willys", "hemkop", "citygross")
COMPARE_MAX_STORES = 12
# Tak på antal nya Axfood code->EAN-uppslag per compare-anrop (cachen warmar över tid).
EAN_RESOLVE_CAP = 150


async def _resolve_axfood_eans(client, entries):
    """Fyll i EAN för Willys/Hemköp-erbjudanden (saknas i listan) via code->EAN-cache.

    Bunden: hämtar högst EAN_RESOLVE_CAP nya uppslag per anrop; resten täcks av
    cachen som warmar över tid."""
    by_chain = {}
    for e in entries:
        if e["chain"] in ("willys", "hemkop") and not e.get("eans"):
            by_chain.setdefault(e["chain"], set()).add(e["offer_id"])
    if not by_chain:
        return

    ean_map = {}
    budget = EAN_RESOLVE_CAP
    for chain, codes in by_chain.items():
        cached = get_cached_eans(codes)
        ean_map.update({c: v for c, v in cached.items() if v})
        missing = [c for c in codes if c not in cached]
        if budget > 0 and missing:
            take = missing[:budget]
            budget -= len(take)
            fetched = await axfood_offers.fetch_eans(client, chain, take)
            save_eans(fetched)
            ean_map.update({c: v for c, v in fetched.items() if v})

    for e in entries:
        if e["chain"] in ("willys", "hemkop") and not e.get("eans"):
            ean = ean_map.get(e["offer_id"])
            if ean:
                e["eans"] = [ean]


async def _compare_rows(client, rows_with_dist, min_chains):
    """Ladda offers för butikerna, resolva Axfood-EAN, returnera matchade produkter.

    rows_with_dist: lista av (avstånd_km eller None, butiksrad)."""
    results = await asyncio.gather(
        *(
            _ensure_offers(client, r["chain"], r["store_id"], r["link_offers"], r["native"])
            for _, r in rows_with_dist
        ),
        return_exceptions=True,
    )
    entries = []
    for (d, r), offs in zip(rows_with_dist, results):
        if isinstance(offs, Exception):
            log.warning("compare: %s/%s misslyckades: %s", r["chain"], r["store_id"], offs)
            continue
        for o in offs:
            e = dict(o)
            e["store_name"] = r["name"]
            e["distance_km"] = round(d, 2) if d is not None else None
            entries.append(e)
    await _resolve_axfood_eans(client, entries)
    # Berika ursprung ur produktdetalj-cachen för offers som saknar det (brand-parsning fångar
    # bara ICA/Coop; nu har EAN resolvats så även Axfood/CG kan få ursprung + flagga).
    po = database.get_product_origins(
        [matching.normalize_ean(e["eans"][0]) for e in entries if e.get("eans")]
    )
    for e in entries:
        ne = matching.normalize_ean(e["eans"][0]) if e.get("eans") else None
        if ne and not e.get("origin_codes") and po.get(ne):
            e["origin"], e["origin_codes"] = po[ne]
    manual = {m["ean"]: m["group_id"] for m in database.load_match_members()}
    return matching.build_comparisons(entries, min_chains=min_chains, manual_groups=manual)


_FAV_OFFER_FIELDS = (
    "chain", "store_id", "store_name", "name", "brand", "package", "price", "price_text",
    "comparison_value", "comparison_unit", "member_price", "image", "valid_to",
    "category_raw", "eans", "savings",
)


@router.get("/v1/favorites/offers")
async def favorites_offers(user=Depends(auth.current_user)):
    """Alla erbjudanden från användarens favoritbutiker (hela listan), plus `compared`:
    produkter som finns hos >= 2 av favoritbutikerna (samma EAN, oavsett kedja), med
    pris per butik billigast först."""
    if not user:
        return JSONResponse({"detail": "Inte inloggad."}, status_code=401)
    pairs = [tok.split(":", 1) for tok in database.list_favorites(user["id"]) if ":" in tok]
    if not pairs:
        return {"stores": [], "count": 0, "offers": [], "compared": []}

    conn = get_conn()
    rows = []
    for c, sid in pairs:
        r = conn.execute(
            text("SELECT chain, store_id, name, link_offers, native FROM stores "
                 "WHERE chain=:chain AND store_id=:store"),
            {"chain": c, "store": sid},
        ).fetchone()
        if r:
            rows.append(r)
    conn.close()

    entries, store_summ = [], []
    async with apilog.make_client(follow_redirects=True) as client:
        results = await asyncio.gather(
            *(_ensure_offers(client, r["chain"], r["store_id"], r["link_offers"], r["native"]) for r in rows),
            return_exceptions=True,
        )
        for r, offs in zip(rows, results):
            n = 0
            if isinstance(offs, Exception):
                log.warning("favoriter-offers %s/%s: %s", r["chain"], r["store_id"], offs)
            else:
                for o in offs:
                    e = dict(o)
                    e["store_name"] = r["name"]
                    entries.append(e)
                    n += 1
            store_summ.append({"chain": r["chain"], "store_id": r["store_id"], "name": r["name"], "offer_count": n})
        await _resolve_axfood_eans(client, entries)

    manual = {m["ean"]: m["group_id"] for m in database.load_match_members()}
    compared = matching.build_comparisons(entries, min_chains=1, min_stores=2, manual_groups=manual)
    offers = [{k: e.get(k) for k in _FAV_OFFER_FIELDS} for e in entries]
    offers.sort(key=lambda o: (o.get("name") or "").lower())
    return {"stores": store_summ, "count": len(offers), "offers": offers, "compared": compared}


@router.get("/v1/compare/near", responses={200: {"model": schemas.CompareResponse}})
async def compare_near(
    lat: float = Query(...),
    lng: float = Query(...),
    radius_km: float = 5.0,
    min_chains: int = 2,
    chains: str | None = None,
    _auth=Depends(require_consumer),
):
    """Produkter (per EAN) som finns på erbjudande hos >= min_chains olika kedjor
    bland närliggande butiker, med pris per butik. Endast EAN-matchning."""
    allowed = COMPARE_CHAINS
    if chains:
        allowed = tuple(c for c in chains.split(",") if c in COMPARE_CHAINS)
    if not allowed:
        return JSONResponse({"detail": "Inga giltiga kedjor."}, status_code=400)

    conn = get_conn()
    rows = conn.execute(
        text("SELECT chain, store_id, name, lat, lng, link_offers, native FROM stores "
             "WHERE chain IN :chains AND lat IS NOT NULL").bindparams(
            bindparam("chains", expanding=True)),
        {"chains": list(allowed)},
    ).fetchall()
    conn.close()

    near = sorted(
        ((haversine(lat, lng, r["lat"], r["lng"]), r) for r in rows), key=lambda x: x[0]
    )
    near = [(d, r) for d, r in near if d <= radius_km][:COMPARE_MAX_STORES]
    if not near:
        return {"count": 0, "stores_compared": 0, "radius_km": radius_km, "products": []}

    async with apilog.make_client(follow_redirects=True) as client:
        products = await _compare_rows(client, near, min_chains)
    return {
        "count": len(products),
        "stores_compared": len(near),
        "radius_km": radius_km,
        "products": products,
    }


@router.get("/v1/compare/stores", responses={200: {"model": schemas.CompareResponse}})
async def compare_stores(stores: str = Query(...), min_chains: int = 2, _auth=Depends(require_consumer)):
    """Jämför erbjudanden bland specifika butiker (t.ex. favoriter).

    stores = komma-separerad lista 'chain:store_id', t.ex. 'ica:2527,coop:598'."""
    pairs = []
    for tok in stores.split(","):
        tok = tok.strip()
        if ":" in tok:
            c, sid = tok.split(":", 1)
            if c in COMPARE_CHAINS:
                pairs.append((c, sid))
    pairs = pairs[:COMPARE_MAX_STORES]
    if not pairs:
        return {"count": 0, "stores_compared": 0, "products": []}

    conn = get_conn()
    rows = []
    for c, sid in pairs:
        r = conn.execute(
            text("SELECT chain, store_id, name, link_offers, native FROM stores "
                 "WHERE chain=:chain AND store_id=:store"),
            {"chain": c, "store": sid},
        ).fetchone()
        if r:
            rows.append(r)
    conn.close()

    async with apilog.make_client(follow_redirects=True) as client:
        products = await _compare_rows(client, [(None, r) for r in rows], min_chains)
    return {"count": len(products), "stores_compared": len(rows), "products": products}
