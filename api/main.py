import asyncio
import json
import logging
import re
import secrets
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import Body, Depends, FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text
from starlette.middleware.sessions import SessionMiddleware

from . import apilog, auth, brands, catalog_crawl, categories, config, database, deps, ica_ecom, images, manufacturers, schemas, settings, store_crawl, store_measure, tags
from .routes import admin_vocab, compare as compare_routes, products as products_routes, stores as stores_routes
from .database import (
    get_conn,
    init_db,
)
from .sync import (
    CATALOG_EAN_STATE,
    PARTIAL_UPGRADE_STATE,
    STATE,
    run_scheduler,
    run_sync,
    sync_and_warm,
    upgrade_sparse_partials,
    warm_axfood_catalog_eans,
    warm_axfood_eans,
    warm_coop_categories,
    warm_ica_categories,
)

from .offers import (  # erbjudande-domänen utbruten (REVIEW Fynd 2)
    SUPPORTED_OFFER_CHAINS, SWEEP_STATE, sweep_offers,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("matbutiker")

# Frontend (statisk) ligger i web/ - separat från api/-paketet, samma repo.
WEB_DIR = config.BASE_DIR / "web"


def _next_cron(expr):
    """Nästa körningstid för ett cron-uttryck (i effektiv tidszon) som 'YYYY-MM-DD HH:MM',
    None om tomt/ogiltigt/avstängt ('off')."""
    try:
        from croniter import croniter
        from zoneinfo import ZoneInfo

        e = (expr or "").strip()
        if e and e.lower() not in ("off", "disabled", "none") and croniter.is_valid(e):
            now = datetime.now(ZoneInfo(settings.get("sync_tz")))
            return croniter(e, now).get_next(datetime).strftime("%Y-%m-%d %H:%M")
    except Exception:  # noqa: BLE001
        pass
    return None


def ensure_admin():
    """Skapa konsol-admin-kontot (admin_users, skilt från app-konton) vid uppstart.
    Lösenord från ADMIN_PASSWORD, annars genereras ett som loggas en gång."""
    email = config.ADMIN_EMAIL.strip().lower()
    if not email:
        return
    if database.get_admin_by_email(email):
        return
    pw = config.ADMIN_PASSWORD or secrets.token_urlsafe(12)
    database.create_admin(email, auth.hash_password(pw))
    if config.ADMIN_PASSWORD:
        log.info("Skapade konsol-admin %s (lösenord från ADMIN_PASSWORD).", email)
    else:
        log.warning(
            "Skapade konsol-admin %s med genererat lösenord: %s  (logga in och byt det)",
            email, pw,
        )


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    ensure_admin()
    tags.set_map(database.load_tag_map())
    tags.set_types(database.load_tag_types())
    tags.set_providers(database.load_providers())
    tags.set_provider_map(database.load_provider_map())
    categories.set_map(database.load_category_map())
    manufacturers.set_map(database.load_manufacturer_map())
    conn = get_conn()
    n = conn.execute(text("SELECT COUNT(*) AS c FROM stores")).fetchone()["c"]
    conn.close()
    if n == 0:
        log.info("Cachen tom - startar synk + EAN-förvärmning i bakgrunden.")
        asyncio.create_task(sync_and_warm())
    else:
        # Värm cacharna vid uppstart (idempotenta; snabba när redan varma).
        asyncio.create_task(warm_axfood_eans())
        asyncio.create_task(warm_coop_categories())
        asyncio.create_task(warm_ica_categories())
        # Katalog-grupperingscachen (~700ms blockerande läsning) -> tråd, blockar inte loopen.
        asyncio.create_task(asyncio.to_thread(database.warm_catalog_cache))
    # Schemaläggarna får callables (settings.get) -> cron/tz resolvas varje varv, så ändringar i
    # konsolens Inställningar-flik slår igenom utan omstart. tz delas av alla tre.
    _tz = lambda: settings.get("sync_tz")  # noqa: E731
    scheduler = asyncio.create_task(
        run_scheduler(lambda: settings.get("sync_cron"), _tz, sync_and_warm, "butikssynk"))
    # Erbjudande-sweepen har egen (tätare) cadence. Ingen kall sweep vid uppstart -
    # den första fyllningen triggas manuellt från konsolen (skonar kedjornas API:er).
    offers_scheduler = asyncio.create_task(
        run_scheduler(lambda: settings.get("offers_sweep_cron"), _tz, sweep_offers, "erbjudande-sweep"))
    # Fulla sortiment-crawlen (tung) har egen gles cadence (default veckovis). Tomt cron = av.
    # Schemalägger BÅDE nationell sortiment-crawl (Axfood/CG) OCH per-butik-pris för ICA/Coop (vars pris
    # är butiksspecifikt -> egen crawler) på SAMMA cadence/inställning.
    crawl_scheduler = asyncio.create_task(
        run_scheduler(lambda: settings.get("catalog_crawl_cron"), _tz, scheduled_crawl, "pris-crawl"))
    # Riktad uppgradering av glesa partial-rader till full merge (egen, strypt cadence). Tomt cron = av.
    partial_scheduler = asyncio.create_task(
        run_scheduler(lambda: settings.get("partial_upgrade_cron"), _tz, upgrade_sparse_partials, "partial-uppgradering"))
    yield
    scheduler.cancel()
    offers_scheduler.cancel()
    crawl_scheduler.cancel()
    partial_scheduler.cancel()


app = FastAPI(
    title="Fyndkartan API",
    version="0.1.0",
    description=(
        "Unified store & offers-API för sex svenska matbutikskedjor (ICA, Coop, Willys, "
        "Hemköp, Lidl, City Gross). Butiker med normaliserade veckoöppettider/taggar, lazy-cachade "
        "erbjudanden med kanonisk kategori + deal-typ, cross-chain prisjämförelse på EAN, "
        "EAN-global produktinfo, och fulla sortiment (crawlad katalog med hyllpris). "
        "Hela /v1 kräver inloggning eller `X-API-Key`."
    ),
    lifespan=lifespan,
)


# OpenAPI-kurering: gruppera endpoints i /docs på path-prefix (i stället för en platt
# lista) utan att tagga varje route manuellt.
def _openapi_tag(path):
    if path.startswith(("/v1/auth", "/v1/console/auth")):
        return "Auth & konto"
    if (path.startswith("/v1/admin") or path.startswith("/v1/sync")
            or path.startswith("/v1/tags") or path.startswith("/v1/offers/sweep")):
        return "Admin / konsol"
    if path.startswith("/v1/products"):
        return "Produkter"
    if path.startswith("/v1/stores"):
        return "Butiker"
    if path.startswith("/v1/compare"):
        return "Jämförelse"
    if path.startswith("/v1/favorites"):
        return "Favoriter"
    if path.startswith("/v1/categories") or path.startswith("/v1/chains"):
        return "Metadata"
    return "Övrigt"


def _custom_openapi():
    if app.openapi_schema:
        return app.openapi_schema
    from fastapi.openapi.utils import get_openapi

    schema = get_openapi(
        title=app.title, version=app.version, description=app.description, routes=app.routes,
    )
    for path, methods in schema.get("paths", {}).items():
        tag = _openapi_tag(path)
        for op in methods.values():
            if isinstance(op, dict):
                op["tags"] = [tag]
    app.openapi_schema = schema
    return schema


app.openapi = _custom_openapi

# Session-secret löses HÄR (vid import, före add_middleware): env eller DB-persisterad
# (settings-tabellen). DB-värdet ligger på den persistenta volymen -> sessioner
# överlever omstart. https_only av i normalfallet (lokal Unraid över http).
_SESSION_SECRET = config.SESSION_SECRET or database.get_or_create_setting(
    "session_secret", lambda: secrets.token_urlsafe(32)
)
app.add_middleware(
    SessionMiddleware,
    secret_key=_SESSION_SECRET,
    same_site="lax",
    https_only=config.SESSION_HTTPS_ONLY,
    max_age=60 * 60 * 24 * 30,
)
# CORS bara om en allowlist är satt (default ingen -> oförändrat same-origin). Explicita
# origins krävs eftersom vi kör med credentials (cookies) - aldrig "*".
if config.CORS_ORIGINS:
    from fastapi.middleware.cors import CORSMiddleware

    app.add_middleware(
        CORSMiddleware,
        allow_origins=config.CORS_ORIGINS,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
app.mount("/static", StaticFiles(directory=str(WEB_DIR)), name="static")
app.include_router(brands.router)  # märkesvaru-paring (/v1/admin/brands|private-products|matches...)
app.include_router(admin_vocab.router)  # vokabulär-admin (/v1/admin/categories|manufacturers, /v1/tags*, /v1/providers*)
app.include_router(stores_routes.router)  # butiks-endpoints (/v1/stores*)
app.include_router(compare_routes.router)  # prisjämförelse + favoriternas erbjudanden (/v1/compare*, /v1/favorites/offers)
app.include_router(products_routes.router)  # produkt-endpoints (/v1/products*, /v1/admin/products/{ean}/*, /v1/categories, /v1/chains)


@app.middleware("http")
async def log_incoming(request, call_next):
    """Logga inkommande anrop mot vårt eget /v1-API (källa 'egen') i anropsloggen.
    Hoppar över anropslogg-pollern själv (skulle annars flooda)."""
    path = request.url.path
    if not path.startswith("/v1/") or path == "/v1/admin/calls":
        return await call_next(request)
    t0 = time.perf_counter()
    resp = await call_next(request)
    apilog.record_incoming(request.method, path, resp.status_code, round((time.perf_counter() - t0) * 1000, 1))
    return resp


@app.middleware("http")
async def api_key_gate(request, call_next):
    """Valfri integratörs-autentisering: skickas `X-API-Key` valideras den (ogiltig/
    återkallad -> 401). Saknas nyckeln släpps anropet igenom (öppna läs-endpoints
    förblir öppna) - detta gatar alltså inte, det möjliggör en autentiserad tier."""
    key = request.headers.get("X-API-Key")
    if key:
        rec = database.api_key_active(auth.hash_token(key))
        if not rec:
            return JSONResponse({"detail": "Ogiltig eller återkallad API-nyckel."}, status_code=401)
        request.state.api_key = rec
    return await call_next(request)


# Cache-busting: stämpla /static/*.js|css-referenser med filens mtime så att en ändrad fil
# ger ny URL och webbläsaren hämtar om automatiskt (HTML:en serveras färsk varje navigering).
_STATIC_REF_RX = re.compile(r"/static/[\w./-]+\.(?:js|css)")


def _html_versioned(filename):
    html = (WEB_DIR / filename).read_text(encoding="utf-8")

    def stamp(m):
        ref = m.group(0)
        try:
            v = int((WEB_DIR / ref[len("/static/"):]).stat().st_mtime)
        except OSError:
            return ref
        return f"{ref}?v={v}"

    # no-cache: webbläsaren måste revalidera HTML:en varje gång -> plockar alltid upp nya
    # ?v=-asset-URL:er (annars kan en cachad HTML peka på gamla assets).
    return HTMLResponse(_STATIC_REF_RX.sub(stamp, html), headers={"Cache-Control": "no-cache"})


@app.get("/", response_class=HTMLResponse)
async def index():
    return _html_versioned("index.html")


@app.get("/healthz")
async def healthz():
    return {"status": "ok"}


# ---- Konton ----
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


@app.post("/v1/auth/register")
async def register(request: Request, payload: dict = Body(...)):
    email = (payload.get("email") or "").strip().lower()
    password = payload.get("password") or ""
    if not _EMAIL_RE.match(email) or len(password) < 8:
        return JSONResponse(
            {"detail": "Ogiltig e-post eller för kort lösenord (minst 8 tecken)."}, status_code=400
        )
    if database.get_user_by_email(email):
        return JSONResponse({"detail": "E-posten är redan registrerad."}, status_code=409)
    uid = database.create_user(email, auth.hash_password(password))
    request.session["uid"] = uid
    return auth.public_user(database.get_user_by_id(uid))


@app.post("/v1/auth/login")
async def login(request: Request, payload: dict = Body(...)):
    email = (payload.get("email") or "").strip().lower()
    password = payload.get("password") or ""
    user = database.get_user_by_email(email)
    if not user or not auth.verify_password(password, user["password_hash"]):
        return JSONResponse({"detail": "Fel e-post eller lösenord."}, status_code=401)
    request.session["uid"] = user["id"]
    return auth.public_user(user)


@app.post("/v1/auth/logout")
async def logout(request: Request):
    request.session.pop("uid", None)  # rör inte ev. admin_uid (skild session)
    return {"ok": True}


@app.get("/v1/auth/me")
async def me(user=Depends(auth.current_user)):
    return auth.public_user(user)


@app.post("/v1/auth/password")
async def change_password(payload: dict = Body(...), user=Depends(auth.current_user)):
    if not user:
        return JSONResponse({"detail": "Inte inloggad."}, status_code=401)
    current = payload.get("current_password") or ""
    new = payload.get("new_password") or ""
    if not auth.verify_password(current, user["password_hash"]):
        return JSONResponse({"detail": "Fel nuvarande lösenord."}, status_code=403)
    if len(new) < 8:
        return JSONResponse({"detail": "Nytt lösenord för kort (minst 8 tecken)."}, status_code=400)
    database.update_password(user["id"], auth.hash_password(new))
    return {"ok": True}


# ---- Slutanvändar-tokens (opaka bearer, för icke-webb-klienter) ----
@app.post("/v1/auth/token")
async def issue_token(payload: dict = Body(...)):
    """Byt e-post+lösenord mot en opak bearer-token. Använd som Authorization: Bearer <token>."""
    email = (payload.get("email") or "").strip().lower()
    password = payload.get("password") or ""
    user = database.get_user_by_email(email)
    if not user or not auth.verify_password(password, user["password_hash"]):
        return JSONResponse({"detail": "Fel e-post eller lösenord."}, status_code=401)
    raw = secrets.token_urlsafe(32)
    database.create_user_token(user["id"], auth.hash_token(raw), (payload.get("label") or "api"))
    return {"token": raw, "token_type": "bearer"}


@app.get("/v1/auth/tokens")
async def list_tokens(user=Depends(auth.current_user)):
    if not user:
        return JSONResponse({"detail": "Inte inloggad."}, status_code=401)
    return {"tokens": database.list_user_tokens(user["id"])}


@app.delete("/v1/auth/tokens/{token_id}")
async def revoke_token(token_id: int, user=Depends(auth.current_user)):
    if not user:
        return JSONResponse({"detail": "Inte inloggad."}, status_code=401)
    database.revoke_user_token(user["id"], token_id)
    return {"removed": True}


# ---- Favoriter (kräver inloggning) ----
@app.get("/v1/favorites")
async def get_favorites(user=Depends(auth.current_user)):
    if not user:
        return JSONResponse({"detail": "Inte inloggad."}, status_code=401)
    return {"favorites": database.list_favorites(user["id"])}


@app.post("/v1/favorites")
async def add_fav(payload: dict = Body(...), user=Depends(auth.current_user)):
    if not user:
        return JSONResponse({"detail": "Inte inloggad."}, status_code=401)
    chain = (payload.get("chain") or "").strip()
    store_id = str(payload.get("store_id") or "").strip()
    if not chain or not store_id:
        return JSONResponse({"detail": "chain + store_id krävs."}, status_code=400)
    database.add_favorite(user["id"], chain, store_id)
    return {"favorites": database.list_favorites(user["id"])}


@app.delete("/v1/favorites/{chain}/{store_id}")
async def del_fav(chain: str, store_id: str, user=Depends(auth.current_user)):
    if not user:
        return JSONResponse({"detail": "Inte inloggad."}, status_code=401)
    database.remove_favorite(user["id"], chain, store_id)
    return {"favorites": database.list_favorites(user["id"])}


# ---- Matkassar (server-side, namngivna per inloggad användare) + matkasse-jämförelse ----
def _basket_items_with_names(user_id, basket_id):
    """En matkasses varor berikade med namn + `paired` (private-label-parning -> kan substitueras).
    None om kassen inte ägs av användaren."""
    items = database.get_basket_items(user_id, basket_id)
    if items is None:
        return None
    if not items:
        return []
    names = database.catalog_names_for_eans([i["ean"] for i in items])
    paired = {m["ean"] for m in database.load_match_members()}
    return [{**i, "name": names.get(i["ean"]), "paired": i["ean"] in paired} for i in items]


def _require_user(user):
    return None if user else JSONResponse({"detail": "Inte inloggad."}, status_code=401)


@app.get("/v1/baskets")
async def get_baskets(user=Depends(auth.current_user)):
    return _require_user(user) or {"baskets": database.list_baskets(user["id"])}


@app.post("/v1/baskets")
async def create_basket_route(payload: dict = Body(...), user=Depends(auth.current_user)):
    err = _require_user(user)
    if err:
        return err
    b = database.create_basket(user["id"], payload.get("name"))
    return {"basket": b, "baskets": database.list_baskets(user["id"])}


@app.post("/v1/baskets/{basket_id}/rename")
async def rename_basket_route(basket_id: int, payload: dict = Body(...), user=Depends(auth.current_user)):
    err = _require_user(user)
    if err:
        return err
    if not database.rename_basket(user["id"], basket_id, payload.get("name")):
        return JSONResponse({"detail": "Matkassen hittades inte eller ogiltigt namn."}, status_code=400)
    return {"baskets": database.list_baskets(user["id"])}


@app.delete("/v1/baskets/{basket_id}")
async def delete_basket_route(basket_id: int, user=Depends(auth.current_user)):
    err = _require_user(user)
    if err:
        return err
    database.delete_basket(user["id"], basket_id)
    return {"baskets": database.list_baskets(user["id"])}


@app.get("/v1/baskets/{basket_id}")
async def get_basket_route(basket_id: int, user=Depends(auth.current_user)):
    err = _require_user(user)
    if err:
        return err
    items = _basket_items_with_names(user["id"], basket_id)
    if items is None:
        return JSONResponse({"detail": "Matkassen hittades inte."}, status_code=404)
    return {"id": basket_id, "items": items}


@app.post("/v1/baskets/{basket_id}/items")
async def add_basket_item_route(basket_id: int, payload: dict = Body(...), user=Depends(auth.current_user)):
    err = _require_user(user)
    if err:
        return err
    e = database.set_basket_item(user["id"], basket_id, payload.get("ean"), payload.get("qty", 1),
                                 exact=payload.get("exact"))  # None = bevara befintlig exact-flagga
    if not e:
        return JSONResponse({"detail": "Ogiltig EAN eller matkasse."}, status_code=400)
    return {"items": _basket_items_with_names(user["id"], basket_id)}


@app.delete("/v1/baskets/{basket_id}/items")
async def clear_basket_route(basket_id: int, user=Depends(auth.current_user)):
    err = _require_user(user)
    if err:
        return err
    database.clear_basket_items(user["id"], basket_id)
    return {"items": []}


@app.delete("/v1/baskets/{basket_id}/items/{ean}")
async def del_basket_item_route(basket_id: int, ean: str, user=Depends(auth.current_user)):
    err = _require_user(user)
    if err:
        return err
    database.remove_basket_item(user["id"], basket_id, ean)
    return {"items": _basket_items_with_names(user["id"], basket_id)}


@app.get("/v1/baskets/{basket_id}/compare", responses={200: {"model": schemas.BasketCompareResponse}})
async def basket_compare_route(basket_id: int, lat: float | None = None, lng: float | None = None,
                               radius: float = 10.0, favorites: bool = False,
                               user=Depends(auth.current_user)):
    """Jämför en av användarens matkassar över ett butiksurval. Scope: `favorites=true` = användarens
    favoritbutiker, annars geo-zon (`lat`/`lng`/`radius`). Per butik: hyllpris-total + erbjudande-
    överlagrad total + täckning. ICA/Coop per butik, Willys/Hemköp/CG nationellt, Lidl saknar pris.
    Private-label-parningar substitueras (om inte varan är exact-flaggad). Full täckning + billigast först."""
    err = _require_user(user)
    if err:
        return err
    items = database.get_basket_items(user["id"], basket_id)
    if items is None:
        return JSONResponse({"detail": "Matkassen hittades inte."}, status_code=404)
    if favorites:
        favs = [tuple(tok.split(":", 1)) for tok in database.list_favorites(user["id"]) if ":" in tok]
        return database.basket_compare(items, pairs=favs)
    if lat is None or lng is None:
        return JSONResponse({"detail": "lat+lng krävs (eller favorites=true)."}, status_code=400)
    return database.basket_compare(items, lat=lat, lng=lng, radius_km=radius)


# ---- API-konsol (egen admin-auth, skild från app-konton) ----
require_admin = deps.require_admin  # bor i auth.py, re-exporteras via deps; alias för befintliga Depends nedan


@app.get("/admin", response_class=HTMLResponse)
async def admin_page():
    # Konsolen har egen inloggningsruta; data-endpoints är gatade (403 tills inloggad).
    return _html_versioned("admin.html")


@app.post("/v1/console/auth/login")
async def console_login(request: Request, payload: dict = Body(...)):
    email = (payload.get("email") or "").strip().lower()
    password = payload.get("password") or ""
    admin = database.get_admin_by_email(email)
    if not admin or not auth.verify_password(password, admin["password_hash"]):
        return JSONResponse({"detail": "Fel e-post eller lösenord."}, status_code=401)
    request.session["admin_uid"] = admin["id"]
    return auth.public_admin(admin)


@app.post("/v1/console/auth/logout")
async def console_logout(request: Request):
    request.session.pop("admin_uid", None)  # rör inte ev. app-session (uid)
    return {"ok": True}


@app.get("/v1/console/auth/me")
async def console_me(admin=Depends(auth.current_admin)):
    return auth.public_admin(admin)


@app.post("/v1/console/auth/password")
async def console_change_password(payload: dict = Body(...), admin=Depends(auth.current_admin)):
    if not admin:
        return JSONResponse({"detail": "Inte inloggad."}, status_code=401)
    current = payload.get("current_password") or ""
    new = payload.get("new_password") or ""
    if not auth.verify_password(current, admin["password_hash"]):
        return JSONResponse({"detail": "Fel nuvarande lösenord."}, status_code=403)
    if len(new) < 8:
        return JSONResponse({"detail": "Nytt lösenord för kort (minst 8 tecken)."}, status_code=400)
    database.update_admin_password(admin["id"], auth.hash_password(new))
    return {"ok": True}


# Tunga DB-/filsystem-stats i overview (ean_stats ~3s UNION-distinct, catalog_stats, storage-scan m.m.)
# cachas kort - de ändras långsamt och behöver inte räknas om varje laddning (Kedjor-/Sweep-flikarna
# pollar samma endpoint). Live in-memory-state (synk-status, crawl/sweep-räknare) läggs på FÄRSK utanför.
_OVERVIEW_CACHE = {"data": None, "ts": 0.0, "ver": -1}
_OVERVIEW_TTL = 300.0  # backstop; data-skrivningar invaliderar via stats_version (crawl/sweep/sync)


def _overview_stats():
    """Cachad bundle av de dyra stats-queries:na. Invalideras av `database.stats_version()` (bumpas
    vid crawl/sweep/sync-slut -> färska siffror direkt) ELLER `_OVERVIEW_TTL` (backstop för konsument-
    driven drift, t.ex. storage-scan/observation-stats). De memoiserade aggregaten (ean/catalog/partial)
    delar samma version, så de räknas om en gång och återanvänds av både overview och catalog-status."""
    now = time.monotonic()
    ver = database.stats_version()
    if (_OVERVIEW_CACHE["data"] is not None and _OVERVIEW_CACHE["ver"] == ver
            and now - _OVERVIEW_CACHE["ts"] < _OVERVIEW_TTL):
        return _OVERVIEW_CACHE["data"]
    conn = get_conn()
    store_counts = {
        r["chain"]: r["c"]
        for r in conn.execute(text("SELECT chain, COUNT(*) c FROM stores GROUP BY chain"))
    }
    offers_rows = conn.execute(text("SELECT COUNT(*) c FROM offers")).fetchone()["c"]
    offers_stores = conn.execute(text(
        "SELECT COUNT(*) c FROM (SELECT 1 FROM offers GROUP BY chain, store_id) sub"
    )).fetchone()["c"]
    conn.close()

    def _file_size(p):
        try:
            return p.stat().st_size
        except OSError:
            return 0

    # Databasstorlek dialekt-medvetet: Postgres -> faktisk DB-storlek (pg_database_size); SQLite ->
    # filsumman (+ WAL/SHM). Efter PG-cutovern är stores.db-filen en fryst snapshot, så fil-summan
    # vore missvisande.
    if database.dialect_name() == "postgresql":
        dconn = get_conn()
        db_bytes = dconn.execute(text("SELECT pg_database_size(current_database())")).fetchone()[0] or 0
        dconn.close()
    else:
        db_bytes = sum(_file_size(config.DB_PATH.with_name(config.DB_PATH.name + suf))
                       for suf in ("", "-wal", "-shm"))  # inkl. WAL/SHM-sidofiler
    img_bytes = img_count = 0
    if images.IMG_DIR.exists():
        for f in images.IMG_DIR.iterdir():
            if f.is_file():
                img_bytes += _file_size(f)
                img_count += 1
    data = {
        "store_counts": store_counts,
        "offers": {"rows": offers_rows, "stores_cached": offers_stores},
        "catalog": database.catalog_stats(),  # fulla sortiment per kedja (steg 5)
        "ean_stats": database.ean_stats(),
        "price_history": database.offer_observations_stats(),
        "info_history": database.product_info_observations_stats(),
        "store_prices_stats": database.store_prices_stats(),
        "partial_counts": database.partial_info_counts(),
        "offers_coverage": database.offers_coverage(),
        "storage": {"db_bytes": db_bytes, "image_bytes": img_bytes,
                    "image_count": img_count, "total_bytes": db_bytes + img_bytes},
    }
    _OVERVIEW_CACHE.update(data=data, ts=now, ver=ver)
    return data


@app.get("/v1/admin/overview")
async def admin_overview(_=Depends(require_admin)):
    s = _overview_stats()  # cachade tunga stats
    store_counts = s["store_counts"]
    next_run = _next_cron(settings.get("sync_cron"))
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
        "offers": s["offers"],
        "catalog": s["catalog"],  # fulla sortiment per kedja (steg 5)
        "storage": s["storage"],
        "ean_stats": s["ean_stats"],
        "price_history": s["price_history"],
        "info_history": s["info_history"],
        "syncing": STATE["running"],
        "scheduler": {"cron": settings.get("sync_cron"), "tz": settings.get("sync_tz"), "next_run": next_run},
        "catalog_crawl": {"cron": settings.get("catalog_crawl_cron"),
                          "next_run": _next_cron(settings.get("catalog_crawl_cron"))},
        "store_prices": {  # steg 6: per-butik-prisinsamling (ICA/Coop) - stats cachade, running live
            "stats": s["store_prices_stats"],
            "running": any(c.get("running") for c in store_crawl.STORE_PRICE_STATE["chains"].values()),
        },
        "partial_upgrade": {
            **PARTIAL_UPGRADE_STATE,  # live in-memory (pågående jobb)
            "cron": settings.get("partial_upgrade_cron"),
            "next_run": _next_cron(settings.get("partial_upgrade_cron")),
            "counts": s["partial_counts"],  # {partial, sparse} (cachad)
        },
        "offers_sweep": {
            **SWEEP_STATE,  # live in-memory (pågående sweep)
            "cron": settings.get("offers_sweep_cron"),
            "next_run": _next_cron(settings.get("offers_sweep_cron")),
            "supported_chains": list(SUPPORTED_OFFER_CHAINS),
            "coverage": s["offers_coverage"],  # nuvarande cachade erbjudanden per kedja (cachad)
            "store_counts": {c: store_counts.get(c, 0) for c in SUPPORTED_OFFER_CHAINS},
        },
    }


def _settings_payload():
    """Effektiva schemaläggnings-inställningar för konsolen: värde, env/kod-default, om override är
    satt, samt nästa körning per cron (i effektiv tidszon)."""
    out = {}
    for k in settings.KEYS:
        item = {"value": settings.get(k), "default": settings.default(k),
                "overridden": settings.is_overridden(k)}
        if k in settings.CRON_KEYS:
            item["next_run"] = _next_cron(settings.get(k))
        out[k] = item
    return {"settings": out}


@app.get("/v1/admin/settings")
async def get_settings(_=Depends(require_admin)):
    return _settings_payload()


@app.post("/v1/admin/settings")
async def set_settings(payload: dict = Body(...), _=Depends(require_admin)):
    """Sätt/återställ en schemaläggnings-inställning. `reset:true` tar bort overriden (env-default).
    Cron: tomt/'off' = pausad (giltigt), annars valideras mot croniter. Tidszon valideras mot zoneinfo."""
    key = (payload.get("key") or "").strip()
    if key not in settings.KEYS:
        return JSONResponse({"detail": "Okänd inställning."}, status_code=400)
    if payload.get("reset"):
        settings.clear_override(key)
        return _settings_payload()
    value = (payload.get("value") or "").strip()
    if key in settings.CRON_KEYS:
        from croniter import croniter
        if value and value.lower() not in ("off", "disabled", "none") and not croniter.is_valid(value):
            return JSONResponse({"detail": "Ogiltigt cron-uttryck."}, status_code=400)
    else:  # sync_tz
        from zoneinfo import ZoneInfo
        try:
            ZoneInfo(value)
        except Exception:  # noqa: BLE001
            return JSONResponse({"detail": "Okänd tidszon."}, status_code=400)
    settings.set_override(key, value)
    return _settings_payload()


@app.get("/v1/admin/settings/cron-preview")
async def cron_preview(cron: str = "", _=Depends(require_admin)):
    """Live-validering/förhandsvisning av ett cron-uttryck (för Inställningar-fliken)."""
    e = (cron or "").strip()
    if not e or e.lower() in ("off", "disabled", "none"):
        return {"valid": True, "disabled": True, "next_run": None}
    from croniter import croniter
    if not croniter.is_valid(e):
        return {"valid": False, "disabled": False, "next_run": None}
    return {"valid": True, "disabled": False, "next_run": _next_cron(e)}


@app.get("/v1/admin/calls")
async def admin_calls(_=Depends(require_admin)):
    return {"stats": apilog.stats(), "recent": apilog.recent()}


@app.get("/v1/admin/sources")
async def admin_sources(_=Depends(require_admin)):
    return {"sources": config.DATA_SOURCES, "own_apis": config.OWN_APIS}


# ---- API-nycklar för externa integratörer (konsol-utfärdade) ----
@app.get("/v1/admin/api-keys")
async def list_api_keys(_=Depends(require_admin)):
    return {"keys": database.list_api_keys()}


@app.post("/v1/admin/api-keys")
async def create_api_key(payload: dict = Body(...), _=Depends(require_admin)):
    label = (payload.get("label") or "").strip() or "namnlös"
    raw = "fk_" + secrets.token_urlsafe(32)  # visas EN gång, lagras hashad
    database.create_api_key(auth.hash_token(raw), raw[:11], label)
    return {"key": raw, "label": label}


@app.delete("/v1/admin/api-keys/{key_id}")
async def delete_api_key(key_id: int, _=Depends(require_admin)):
    database.revoke_api_key(key_id)
    return {"removed": True}


_PROXY_HOSTS = {
    "apim-pub.gw.ica.se", "www.ica.se", "proxy.api.coop.se", "external.api.coop.se",
    "www.willys.se", "www.hemkop.se", "live.api.schwarz",
}
_PROXY_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
             "(KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36")


async def _proxy_auth_headers(client, kind):
    from .adapters import ica_token, keys
    if kind == "ica":
        return {"Authorization": f"Bearer {await ica_token.get_token(client)}"}
    if kind == "coop_store":
        return {"Ocp-Apim-Subscription-Key": config.COOP_KEY or await keys.scrape_coop_key(client)}
    if kind == "coop_dke":
        return {"Ocp-Apim-Subscription-Key": config.COOP_OFFERS_KEY or await keys.scrape_coop_offers_key(client)}
    if kind == "coop_perso":
        return {"Ocp-Apim-Subscription-Key": config.COOP_PERSO_KEY or await keys.scrape_coop_perso_key(client)}
    if kind == "lidl":
        return {"x-apikey": config.LIDL_KEY or await keys.scrape_lidl_key(client)}
    return {}


@app.post("/v1/admin/proxy")
async def admin_proxy(payload: dict = Body(...), _=Depends(require_admin)):
    """Testa kedjornas upstream-API:er från konsolen (rätt nyckel/token läggs på
    server-side). GET eller POST (med body). Endast whitelistade kedje-hostar."""
    from urllib.parse import urlparse

    url = payload.get("url") or ""
    auth_kind = payload.get("auth_kind") or "none"
    method = (payload.get("method") or "GET").upper()
    req_body = payload.get("body")
    host = (urlparse(url).hostname or "").lower()
    if host not in _PROXY_HOSTS:
        return JSONResponse({"detail": f"Host ej tillåten: {host or '(tom)'}"}, status_code=400)
    try:
        async with apilog.make_client(follow_redirects=True) as client:
            headers = {"User-Agent": _PROXY_UA, "Accept": "application/json",
                       **await _proxy_auth_headers(client, auth_kind)}
            if method == "POST":
                headers["Content-Type"] = "application/json"
                r = await client.post(url, headers=headers, content=req_body or "", timeout=25)
            else:
                r = await client.get(url, headers=headers, timeout=25)
        ct = r.headers.get("content-type", "")
        body = r.json() if "application/json" in ct else r.text[:4000]
        return {"status": r.status_code, "content_type": ct, "body": body}
    except Exception as e:  # noqa: BLE001
        log.warning("proxy %s misslyckades: %s", url, e)
        return JSONResponse({"detail": str(e)}, status_code=502)


@app.post("/v1/sync")
async def trigger_sync(_=Depends(require_admin)):
    if STATE["running"]:
        return {"status": "running", "detail": "Synk pågår redan."}
    asyncio.create_task(run_sync())
    return {"status": "started"}


@app.get("/v1/sync/status")
async def sync_status(_=Depends(require_admin)):
    return STATE


@app.post("/v1/offers/sweep")
async def trigger_offers_sweep(force: bool = False, _=Depends(require_admin)):
    """Starta en bulk-förhämtning av erbjudanden för alla offer-stödda butiker (bakgrund).
    `force=true` ignorerar färskhets-cachen och hämtar om allt (annars hoppas färska butiker)."""
    if SWEEP_STATE["running"]:
        return {"status": "running", "detail": "En sweep pågår redan."}
    asyncio.create_task(sweep_offers(force=force))
    return {"status": "started", "force": force}


@app.get("/v1/offers/sweep/status")
async def offers_sweep_status(_=Depends(require_admin)):
    return SWEEP_STATE


@app.post("/v1/admin/partials/upgrade")
async def trigger_partial_upgrade(cap: int | None = None, _=Depends(require_admin)):
    """Starta en riktad uppgradering av glesa partial-rader (piggyback) till full korsskällig merge
    (bakgrund). `cap` = max EAN denna körning (default PARTIAL_UPGRADE_CAP)."""
    if PARTIAL_UPGRADE_STATE["running"]:
        return {"status": "running", "detail": "En uppgradering pågår redan."}
    asyncio.create_task(upgrade_sparse_partials(cap=cap))
    return {"status": "started", "cap": cap or config.PARTIAL_UPGRADE_CAP}


@app.get("/v1/admin/partials/upgrade/status")
async def partial_upgrade_status(_=Depends(require_admin)):
    return PARTIAL_UPGRADE_STATE


@app.post("/v1/admin/store-prices/measure")
async def trigger_store_measure(chain: str | None = None, recheck: bool = False,
                                cap: int | None = None, _=Depends(require_admin)):
    """Starta queryability-mätningen per butik (Steg 6 Fas 1) i bakgrunden. `chain`=coop|ica (annars
    båda), `recheck`=om-mät ALLA (annars bara omätta; fångar butiker som börjat erbjuda e-handel),
    `cap`=max butiker/kedja. Fyller store_crawl.queryable + product_count (ICA)."""
    if store_measure.MEASURE_STATE["running"]:
        return {"status": "running", "detail": "En mätning pågår redan."}
    asyncio.create_task(store_measure.measure_queryability(chain=chain, recheck=recheck, cap=cap))
    return {"status": "started", "chain": chain, "recheck": recheck, "cap": cap}


@app.get("/v1/admin/store-prices/measure/status")
async def store_measure_status(_=Depends(require_admin)):
    return {**store_measure.MEASURE_STATE, "stats": database.store_crawl_stats()}


@app.get("/v1/admin/store-prices/stores")
async def store_prices_list(chain: str | None = None, q: str | None = None,
                            queryable: int | None = None, enabled: int | None = None,
                            limit: int = 200, offset: int = 0, _=Depends(require_admin)):
    """Urvalstabellen (Steg 6 Fas 2): store_crawl-rader (kedja/namn/ort/frågbar/vald/produktantal),
    filtrerbar på chain/queryable/enabled/namn-sök. Seedar store_crawl om tomt (fresh deploy)."""
    if not database.store_crawl_stats():
        database.seed_store_crawl()
    rows, total = database.list_store_crawl(chain=chain, q=q, queryable=queryable, enabled=enabled,
                                            limit=max(1, min(limit, 2000)), offset=max(0, offset))
    return {"stores": rows, "total": total, "stats": database.store_crawl_stats()}


@app.post("/v1/admin/store-prices/crawl")
async def trigger_store_price_crawl(chain: str = "ica", cap: int | None = None,
                                    concurrency: int | None = None, max_age_hours: int = 20,
                                    _=Depends(require_admin)):
    """Starta per-butik-pris-crawlen (Steg 6 Fas 3) i bakgrunden för de enabled+frågbara butikerna i
    `chain` (rotation, äldst först). `cap` = max butiker denna körning. Samtidigheten ADAPTIVT auto-tunad
    (AIMD; `concurrency` = valfri manuell sänkning av taket). `max_age_hours` (default 20) HOPPAR butiker
    crawlade nyligare än så -> 'lägg till + crawla' kör bara de nya; 0 = full om-crawl av alla valda.
    `chain` = ica|coop|both (both kör ICA+Coop PARALLELLT). Skriver catalog_store_prices + per-butik-historik."""
    want = ["ica", "coop"] if chain == "both" else ([chain] if chain in ("ica", "coop") else [])
    if want and all(store_crawl.STORE_PRICE_STATE["chains"].get(c, {}).get("running") for c in want):
        return {"status": "running", "detail": "Pågår redan för den/de kedjorna."}
    asyncio.create_task(store_crawl.crawl_store_prices(chain=chain, cap=cap, concurrency=concurrency,
                                                       max_age_hours=max_age_hours))
    return {"status": "started", "chain": chain, "cap": cap, "max_age_hours": max_age_hours}


@app.get("/v1/admin/store-prices/crawl/status")
async def store_price_crawl_status(_=Depends(require_admin)):
    # + DURABLE last_runs ur crawl_runs så korten visar "ändringar sedan senaste" även efter omstart
    # (in-memory STORE_PRICE_STATE nollställs då).
    runs = database.last_crawl_runs(kind="store_prices")
    # Delar cadence med sortiment-crawlen (catalog_crawl_cron) -> visa samma schema så konsolen speglar
    # att ICA/Coop nu är schemalagda.
    return {**store_crawl.STORE_PRICE_STATE,
            "cron": settings.get("catalog_crawl_cron"),
            "next_run": _next_cron(settings.get("catalog_crawl_cron")),
            "last_runs": {c: runs.get(("store_prices", c)) for c in ("ica", "coop")}}


@app.post("/v1/admin/store-prices/crawl-ecom")
async def trigger_ica_ecom_crawl(cap: int | None = None, concurrency: int | None = None,
                                 max_age_hours: int | None = None, _=Depends(require_admin)):
    """Starta ICA ecom-pris-crawlen (handlaprivatkund) i bakgrunden -> ica_ecom_prices (separat tabell,
    parallell-fasen). `cap` = max butiker. `max_age_hours` None/0 = alla valda ICA-butiker. Kör parallellt
    med quicksearch-crawlen; rör inte dess rotation. EAN-mappning fylls av quicksearch (ica_cid_ean)."""
    if ica_ecom.ECOM_STATE["running"]:
        return {"status": "running", "detail": "ICA ecom-crawl pågår redan."}
    asyncio.create_task(ica_ecom.crawl_all_ecom(cap=cap, concurrency=concurrency, max_age_hours=max_age_hours))
    return {"status": "started", "cap": cap, "max_age_hours": max_age_hours}


@app.get("/v1/admin/store-prices/crawl-ecom/status")
async def ica_ecom_crawl_status(_=Depends(require_admin)):
    runs = database.last_crawl_runs(kind="ecom_prices")
    return {**ica_ecom.ECOM_STATE, "coverage": database.ica_ecom_coverage(),
            "last_run": runs.get(("ecom_prices", "ica"))}


@app.get("/v1/admin/crawl-history")
async def crawl_history(kind: str | None = None, chain: str | None = None, limit: int = 50,
                        _=Depends(require_admin)):
    """Beständig crawl-körningshistorik (alla kedjor, båda systemen). Nyast först."""
    return {"runs": database.recent_crawl_runs(limit=min(limit, 200), kind=kind, chain=chain)}


@app.post("/v1/admin/store-prices/stores/enable")
async def store_prices_enable(payload: dict = Body(...), _=Depends(require_admin)):
    """Sätt `enabled` (urval för crawl). Antingen `all_queryable=true` (bulk på alla frågbara, ev.
    `chain`-scopat) eller en lista `stores` av "chain:store"-nycklar. `enabled` (bool) styr på/av."""
    enabled = bool(payload.get("enabled"))
    if payload.get("all_queryable"):
        n = database.set_all_queryable_enabled(enabled, chain=payload.get("chain"))
    else:
        items = [tuple(s.split(":", 1)) for s in (payload.get("stores") or []) if ":" in s]
        n = database.set_stores_enabled(items, enabled)
    return {"changed": n, "enabled": enabled, "stats": database.store_crawl_stats()}


@app.post("/v1/admin/catalog/crawl")
async def trigger_catalog_crawl(limit_categories: int | None = None, chains: str | None = None,
                                _=Depends(require_admin)):
    """Starta en katalog-crawl (fulla sortiment) i bakgrunden. `limit_categories` cappar antal
    kategorier/sidor per kedja (snabbtest). `chains` (komma-separerad) begränsar till vissa kedjor
    (default alla implementerade)."""
    if catalog_crawl.CRAWL_STATE["running"]:
        return {"status": "running", "detail": "En crawl pågår redan."}
    chain_list = [c.strip() for c in chains.split(",")] if chains else None
    asyncio.create_task(crawl_and_warm(limit_categories=limit_categories, chains=chain_list))
    return {"status": "started", "limit_categories": limit_categories, "chains": chain_list}


async def crawl_and_warm(limit_categories=None, chains=None):
    """Katalog-crawl + inkrementell Axfood-EAN-warming (capad) efteråt -> nya koder resolvas över tid.
    Testkörningar (`limit_categories`) hoppar warmingen."""
    await catalog_crawl.crawl_all(limit_categories=limit_categories, chains=chains)
    if limit_categories:
        return
    try:
        await warm_axfood_catalog_eans(cap=config.CATALOG_EAN_WARM_CAP)
    except Exception:  # noqa: BLE001
        log.exception("Axfood-katalog-EAN-warming efter crawl misslyckades")


async def scheduled_crawl():
    """Schemalagt pris-crawl-jobb (catalog_crawl_cron): nationell sortiment-crawl (Axfood/CG via
    crawl_and_warm) FÖLJT av per-butik-pris för ICA/Coop (store_crawl, vars pris är butiksspecifikt).
    Benen körs SEKVENTIELLT (SQLite enkel-skrivare -> undvik 'database is locked' av två tunga
    skriv-jobb samtidigt) och var för sig skyddat -> ett krasch i ena benet hoppar inte över det andra.
    Endast schemaläggaren använder denna; manuella endpoints triggar respektive crawl var för sig."""
    for label, fn in (("sortiment-crawl (Axfood/CG)", crawl_and_warm),
                      ("per-butik-pris (ICA/Coop)", lambda: store_crawl.crawl_store_prices(chain="both"))):
        try:
            await fn()
        except Exception:  # noqa: BLE001
            log.exception("Schemalagd %s misslyckades", label)


@app.post("/v1/admin/catalog/warm-eans")
async def trigger_catalog_ean_warm(cap: int | None = None, chain: str | None = None,
                                   _=Depends(require_admin)):
    """Resolva Axfood-katalogkoder till EAN (cross-chain-merge) i bakgrunden. `cap` = max koder/kedja
    (default: alla = engångs-bulk). `chain` (willys|hemkop) = bara en kedja. Progress i crawl-status."""
    if CATALOG_EAN_STATE["running"]:
        return {"status": "running", "detail": "En EAN-resolvning pågår redan."}
    if catalog_crawl.CRAWL_STATE["running"]:
        return {"status": "blocked", "detail": "En crawl pågår - vänta tills den är klar."}
    asyncio.create_task(warm_axfood_catalog_eans(cap=cap, chain=chain))
    return {"status": "started", "cap": cap, "chain": chain}


@app.get("/v1/admin/catalog/crawl/status")
async def catalog_crawl_status(_=Depends(require_admin)):
    return {**catalog_crawl.CRAWL_STATE, "stats": database.catalog_stats(),
            "cron": settings.get("catalog_crawl_cron"),
            "next_run": _next_cron(settings.get("catalog_crawl_cron")),
            "ean_warm": CATALOG_EAN_STATE,
            "partial_upgrade": {**PARTIAL_UPGRADE_STATE,
                                "cron": settings.get("partial_upgrade_cron"),
                                "next_run": _next_cron(settings.get("partial_upgrade_cron")),
                                "counts": database.partial_info_counts()}}


@app.get("/v1/admin/catalog/price-changes")
async def catalog_price_changes(chain: str | None = None, q: str | None = None,
                                sort: str = "recent", limit: int = 500, _=Depends(require_admin)):
    """Hyllpris-ändringar ur katalogen (beständiga, append-only). Filtrerbart på kedja + namn,
    sorterbart (recent/abs_desc/abs_asc/inc/dec)."""
    return {"changes": database.catalog_price_changes(chain=chain, q=q, sort=sort, limit=min(limit, 2000))}
