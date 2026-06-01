import asyncio
import json
import logging
import re
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

from fastapi import Body, FastAPI, Query
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import apilog, config, database, matching, tags
from .adapters import axfood_offers, coop_offers, ica_offers
from .database import (
    get_cached_eans,
    get_conn,
    get_store_offers,
    init_db,
    offers_fetched_at,
    replace_store_offers,
    row_to_store,
    save_eans,
)
from .geo import haversine
from .sync import STATE, run_scheduler, run_sync, sync_and_warm, warm_axfood_eans

OFFERS_TTL = timedelta(hours=6)  # erbjudanden uppdateras veckovis; 6h cache räcker gott

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("matbutiker")

# Frontend (statisk) ligger i web/ - separat från api/-paketet, samma repo.
WEB_DIR = config.BASE_DIR / "web"


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    tags.set_map(database.load_tag_map())
    tags.set_types(database.load_tag_types())
    conn = get_conn()
    n = conn.execute("SELECT COUNT(*) AS c FROM stores").fetchone()["c"]
    conn.close()
    if n == 0:
        log.info("Cachen tom - startar synk + EAN-förvärmning i bakgrunden.")
        asyncio.create_task(sync_and_warm())
    else:
        # Värm EAN-cachen vid uppstart (idempotent; snabbt när redan varm).
        asyncio.create_task(warm_axfood_eans())
    scheduler = asyncio.create_task(run_scheduler(config.SYNC_CRON, config.SYNC_TZ))
    yield
    scheduler.cancel()


app = FastAPI(title="Fyndkartan API", version="0.1.0", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=str(WEB_DIR)), name="static")


def _last_sync():
    times = [c["last_sync"] for c in STATE["chains"].values() if c["last_sync"]]
    return max(times) if times else None


def _query_stores(chain=None, city=None, q=None, brand=None, features=None, has_offers=False):
    sql = "SELECT * FROM stores WHERE 1=1"
    args = []
    if chain:
        chains = [c.strip() for c in chain.split(",") if c.strip()]
        sql += f" AND chain IN ({','.join('?' * len(chains))})"
        args += chains
    if brand:
        brands = [b.strip() for b in brand.split(",") if b.strip()]
        sql += f" AND brand IN ({','.join('?' * len(brands))})"
        args += brands
    if city:
        sql += " AND lower(city) = lower(?)"
        args.append(city)
    if q:
        sql += " AND (lower(name) LIKE ? OR lower(street) LIKE ? OR lower(city) LIKE ?)"
        like = f"%{q.lower()}%"
        args += [like, like, like]
    if has_offers:
        sql += " AND link_offers IS NOT NULL"
    conn = get_conn()
    rows = conn.execute(sql, args).fetchall()
    conn.close()
    stores = [row_to_store(r) for r in rows]
    if features:
        wanted = {f.strip() for f in features.split(",") if f.strip()}
        stores = [
            s for s in stores if wanted <= {ty for t in s["tags"] for ty in t["types"]}
        ]
    return stores


@app.get("/", response_class=FileResponse)
async def index():
    return FileResponse(WEB_DIR / "index.html")


@app.get("/healthz")
async def healthz():
    return {"status": "ok"}


# ---- Admin-dashboard ----
@app.get("/admin", response_class=FileResponse)
async def admin_page():
    return FileResponse(WEB_DIR / "admin.html")


@app.get("/v1/admin/overview")
async def admin_overview():
    conn = get_conn()
    store_counts = {
        r["chain"]: r["c"] for r in conn.execute("SELECT chain, COUNT(*) c FROM stores GROUP BY chain")
    }
    offers_rows = conn.execute("SELECT COUNT(*) c FROM offers").fetchone()["c"]
    offers_stores = conn.execute(
        "SELECT COUNT(*) c FROM (SELECT 1 FROM offers GROUP BY chain, store_id)"
    ).fetchone()["c"]
    ean_n = conn.execute("SELECT COUNT(*) c FROM ean_cache WHERE ean!=''").fetchone()["c"]
    conn.close()

    next_run = None
    try:
        from croniter import croniter
        from zoneinfo import ZoneInfo

        if config.SYNC_CRON.strip():
            now = datetime.now(ZoneInfo(config.SYNC_TZ))
            next_run = croniter(config.SYNC_CRON, now).get_next(datetime).strftime("%Y-%m-%d %H:%M")
    except Exception:  # noqa: BLE001
        pass

    return {
        "chains": [
            {
                "chain": c,
                "store_count": store_counts.get(c, 0),
                "status": STATE["chains"][c]["status"],
                "last_sync": STATE["chains"][c]["last_sync"],
                "error": STATE["chains"][c]["error"],
            }
            for c in config.CHAINS
        ],
        "offers": {"rows": offers_rows, "stores_cached": offers_stores},
        "ean_cache": ean_n,
        "syncing": STATE["running"],
        "scheduler": {"cron": config.SYNC_CRON, "tz": config.SYNC_TZ, "next_run": next_run},
    }


@app.get("/v1/admin/calls")
async def admin_calls():
    return {"stats": apilog.stats(), "recent": apilog.recent()}


@app.get("/v1/admin/sources")
async def admin_sources():
    return {"sources": config.DATA_SOURCES}


@app.get("/v1/tags")
async def list_tags():
    from .adapters.base import classify_provider

    items = []
    for label, info in database.tag_label_counts().items():
        types = tags.effective_types(label)
        overridden = label in tags.TAG_MAP
        items.append(
            {
                "label": label,
                "count": info["count"],
                "chains": sorted(info["chains"]),
                "types": types,
                "provider": classify_provider(label),
                "overridden": overridden,
            }
        )
    # Behöver-uppmärksamhet (ej override och bara "other") först, sedan på antal.
    items.sort(key=lambda x: (x["overridden"] or x["types"] != ["other"], -x["count"]))
    return {"types": tags.CANONICAL, "tags": items}


@app.post("/v1/tags/map")
async def set_tag(payload: dict = Body(...)):
    label = (payload.get("label") or "").strip()
    types = [t for t in (payload.get("types") or []) if tags.valid_type(t)]
    if not label or not types:
        return JSONResponse({"detail": "Ogiltig label eller typer."}, status_code=400)
    database.set_tag_map(label, types)
    tags.put(label, types)
    return {"label": label, "types": types}


# ---- Kanonisk vokabulär (typer) ----
@app.get("/v1/tags/types")
async def list_types():
    return {"types": tags.CANONICAL, "builtin": sorted(config.BUILTIN_TAG_TYPES)}


@app.post("/v1/tags/types")
async def add_type(payload: dict = Body(...)):
    raw = (payload.get("type") or "").strip().lower()
    for a, b in (("å", "a"), ("ä", "a"), ("ö", "o"), ("é", "e"), ("ü", "u")):
        raw = raw.replace(a, b)
    type_ = re.sub(r"[^a-z0-9_]+", "_", raw).strip("_")
    if not type_:
        return JSONResponse({"detail": "Ogiltig typ."}, status_code=400)
    if type_ not in tags.CANONICAL:
        database.add_tag_type(type_)
        tags.set_types(database.load_tag_types())
    return {"type": type_, "types": tags.CANONICAL}


@app.delete("/v1/tags/types/{type_}")
async def remove_type(type_: str):
    if type_ in config.BUILTIN_TAG_TYPES:
        return JSONResponse({"detail": "Inbyggd typ kan inte tas bort."}, status_code=400)
    if database.tag_type_in_use(type_):
        return JSONResponse({"detail": "Typen används i en mappning."}, status_code=400)
    database.remove_tag_type(type_)
    tags.set_types(database.load_tag_types())
    return {"type": type_, "removed": True, "types": tags.CANONICAL}


@app.delete("/v1/tags/map/{label:path}")
async def del_tag(label: str):
    database.delete_tag_map(label)
    tags.remove(label)
    return {"label": label, "removed": True}


@app.get("/v1/stores")
async def list_stores(
    chain: str | None = None,
    city: str | None = None,
    q: str | None = None,
    brand: str | None = None,
    features: str | None = None,
    has_offers: bool = False,
):
    stores = _query_stores(chain, city, q, brand, features, has_offers)
    return {"count": len(stores), "generated_at": _last_sync(), "stores": stores}


@app.get("/v1/stores/near")
async def stores_near(
    lat: float = Query(...),
    lng: float = Query(...),
    radius_km: float = 10.0,
    chain: str | None = None,
    features: str | None = None,
    has_offers: bool = False,
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


@app.get("/v1/stores/{chain}/{store_id}")
async def get_store(chain: str, store_id: str):
    conn = get_conn()
    row = conn.execute(
        "SELECT * FROM stores WHERE chain=? AND store_id=?", (chain, store_id)
    ).fetchone()
    conn.close()
    if not row:
        return JSONResponse({"detail": "Butiken hittades inte."}, status_code=404)
    return row_to_store(row)


def _offers_fresh(chain, store_id):
    ts = offers_fetched_at(chain, store_id)
    if not ts:
        return False
    try:
        fetched = datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return False
    return datetime.now(timezone.utc) - fetched < OFFERS_TTL


SUPPORTED_OFFER_CHAINS = ("ica", "willys", "hemkop", "coop")
COMPARE_CHAINS = ("ica", "coop", "willys", "hemkop")
COMPARE_MAX_STORES = 12
# Tak på antal nya Axfood code->EAN-uppslag per compare-anrop (cachen warmar över tid).
EAN_RESOLVE_CAP = 150


async def _fetch_offers_for(client, chain, store_id, link_offers, native_json):
    if chain == "ica":
        return await ica_offers.fetch_offers(client, link_offers, store_id)
    if chain == "coop":
        native = json.loads(native_json) if native_json else {}
        return await coop_offers.fetch_offers(
            client, store_id, native.get("ledgerAccountNumber"), config.COOP_OFFERS_KEY
        )
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


@app.get("/v1/stores/{chain}/{store_id}/offers")
async def store_offers(chain: str, store_id: str, refresh: bool = False):
    conn = get_conn()
    row = conn.execute(
        "SELECT chain, link_offers, native FROM stores WHERE chain=? AND store_id=?",
        (chain, store_id),
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
    return matching.build_comparisons(entries, min_chains=min_chains)


@app.get("/v1/compare/near")
async def compare_near(
    lat: float = Query(...),
    lng: float = Query(...),
    radius_km: float = 5.0,
    min_chains: int = 2,
    chains: str | None = None,
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
        f"SELECT chain, store_id, name, lat, lng, link_offers, native FROM stores "
        f"WHERE chain IN ({','.join('?' * len(allowed))}) AND lat IS NOT NULL",
        allowed,
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


@app.get("/v1/compare/stores")
async def compare_stores(stores: str = Query(...), min_chains: int = 2):
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
            "SELECT chain, store_id, name, link_offers, native FROM stores WHERE chain=? AND store_id=?",
            (c, sid),
        ).fetchone()
        if r:
            rows.append(r)
    conn.close()

    async with apilog.make_client(follow_redirects=True) as client:
        products = await _compare_rows(client, [(None, r) for r in rows], min_chains)
    return {"count": len(products), "stores_compared": len(rows), "products": products}


@app.get("/v1/chains")
async def chains():
    conn = get_conn()
    counts = {
        r["chain"]: r["c"]
        for r in conn.execute("SELECT chain, COUNT(*) AS c FROM stores GROUP BY chain")
    }
    conn.close()
    out = []
    for c in config.CHAINS:
        meta = config.CHAIN_META[c]
        st = STATE["chains"][c]
        out.append(
            {
                "chain": c,
                "label": meta["label"],
                "color": meta["color"],
                "auth": meta["auth"],
                "offers_supported": meta["offers"],
                "store_count": counts.get(c, 0),
                "sync_status": st["status"],
                "last_sync": st["last_sync"],
                "error": st["error"],
            }
        )
    return {"chains": out}


@app.post("/v1/sync")
async def trigger_sync():
    if STATE["running"]:
        return {"status": "running", "detail": "Synk pågår redan."}
    asyncio.create_task(run_sync())
    return {"status": "started"}


@app.get("/v1/sync/status")
async def sync_status():
    return STATE
