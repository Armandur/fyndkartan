"""Butiks-endpoints: lista/filtrera, närhet (Haversine), enskild butik och dess (lazy-cachade)
erbjudanden. Utbrutet ur main.py (REVIEW Fynd 2, pass 3). Plain APIRouter med per-route
`require_consumer` (samma gating som tidigare); fulla paths -> identiska URL:er."""
import logging

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse
from sqlalchemy import bindparam, text

from .. import apilog, schemas
from ..database import get_conn, get_store_offers, row_to_store
from ..deps import require_consumer
from ..geo import haversine
from ..offers import SUPPORTED_OFFER_CHAINS, _ensure_offers, _offers_fresh
from ..sync import STATE

log = logging.getLogger("matbutiker")

router = APIRouter()


def _last_sync():
    times = [c["last_sync"] for c in STATE["chains"].values() if c["last_sync"]]
    return max(times) if times else None


def _query_stores(chain=None, city=None, q=None, brand=None, features=None, has_offers=False):
    sql = "SELECT * FROM stores WHERE 1=1"
    args, binds = {}, []
    if chain:
        chains = [c.strip() for c in chain.split(",") if c.strip()]
        sql += " AND chain IN :chains"
        args["chains"] = chains
        binds.append(bindparam("chains", expanding=True))
    if brand:
        brands = [b.strip() for b in brand.split(",") if b.strip()]
        sql += " AND brand IN :brands"
        args["brands"] = brands
        binds.append(bindparam("brands", expanding=True))
    if city:
        sql += " AND lower(city) = lower(:city)"
        args["city"] = city
    if q:
        sql += " AND (lower(name) LIKE :like OR lower(street) LIKE :like OR lower(city) LIKE :like)"
        args["like"] = f"%{q.lower()}%"
    if has_offers:
        sql += " AND link_offers IS NOT NULL"
    stmt = text(sql)
    if binds:
        stmt = stmt.bindparams(*binds)
    conn = get_conn()
    rows = conn.execute(stmt, args).fetchall()
    conn.close()
    stores = [row_to_store(r) for r in rows]
    if features:
        wanted = {f.strip() for f in features.split(",") if f.strip()}
        stores = [
            s for s in stores if wanted <= {ty for t in s["tags"] for ty in t["types"]}
        ]
    return stores


@router.get("/v1/stores", responses={200: {"model": schemas.StoresResponse}})
async def list_stores(
    chain: str | None = None,
    city: str | None = None,
    q: str | None = None,
    brand: str | None = None,
    features: str | None = None,
    has_offers: bool = False,
    _auth=Depends(require_consumer),
):
    stores = _query_stores(chain, city, q, brand, features, has_offers)
    return {"count": len(stores), "generated_at": _last_sync(), "stores": stores}


@router.get("/v1/stores/near", responses={200: {"model": schemas.StoresNearResponse}})
async def stores_near(
    lat: float = Query(...),
    lng: float = Query(...),
    radius_km: float = 10.0,
    chain: str | None = None,
    features: str | None = None,
    has_offers: bool = False,
    _auth=Depends(require_consumer),
):
    stores = _query_stores(chain=chain, features=features, has_offers=has_offers)
    hits = []
    for s in stores:
        loc = s.get("location")
        if not loc:
            continue
        d = haversine(lat, lng, loc["lat"], loc["lng"])
        if d <= radius_km:
            s = {**s, "distance_km": round(d, 2)}
            hits.append(s)
    hits.sort(key=lambda s: s["distance_km"])
    return {"count": len(hits), "generated_at": _last_sync(), "stores": hits}


@router.get("/v1/stores/{chain}/{store_id}", responses={200: {"model": schemas.Store}})
async def get_store(chain: str, store_id: str, _auth=Depends(require_consumer)):
    conn = get_conn()
    row = conn.execute(
        text("SELECT * FROM stores WHERE chain=:chain AND store_id=:store"),
        {"chain": chain, "store": store_id}
    ).fetchone()
    conn.close()
    if not row:
        return JSONResponse({"detail": "Butiken hittades inte."}, status_code=404)
    return row_to_store(row)


@router.get("/v1/stores/{chain}/{store_id}/offers", responses={200: {"model": schemas.StoreOffersResponse}})
async def store_offers(chain: str, store_id: str, refresh: bool = False, _auth=Depends(require_consumer)):
    conn = get_conn()
    row = conn.execute(
        text("SELECT chain, link_offers, native FROM stores WHERE chain=:chain AND store_id=:store"),
        {"chain": chain, "store": store_id},
    ).fetchone()
    conn.close()
    if not row:
        return JSONResponse({"detail": "Butiken hittades inte."}, status_code=404)

    cached = not refresh and _offers_fresh(chain, store_id)
    if not cached and chain not in SUPPORTED_OFFER_CHAINS:
        existing = get_store_offers(chain, store_id)
        return {
            "count": len(existing),
            "cached": True,
            "offers": existing,
            "note": f"Erbjudande-ingestion för {chain} är inte byggd än.",
        }
    try:
        async with apilog.make_client(follow_redirects=True) as client:
            offers = await _ensure_offers(
                client, chain, store_id, row["link_offers"], row["native"], refresh
            )
        return {"count": len(offers), "cached": cached, "offers": offers}
    except Exception as e:  # noqa: BLE001
        log.exception("Hämtning av erbjudanden misslyckades för %s/%s", chain, store_id)
        return JSONResponse(
            {"detail": "Kunde inte hämta erbjudanden just nu.", "error": str(e)}, status_code=502
        )
