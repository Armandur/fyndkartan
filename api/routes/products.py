"""Produkt-endpoints: sök/bläddra (offers-cache + persisterad katalog + live katalog-sök),
EAN-global produktinfo/bild (lazy, delade resolvers), prishistorik, butiker-med-erbjudande,
samt metadata (/v1/categories, /v1/chains). Utbrutet ur main.py (REVIEW Fynd 2, pass 3).

VIKTIGT - registreringsordning: `/v1/products/{ean}` är en girig enkel-segment-matchare och
MÅSTE registreras EFTER literalerna (`search`, `by-category`, `catalog`), annars skuggar den
dem (`/v1/products/catalog` skulle träffa {ean}-handlern med ean='catalog'). Ordningen nedan
bevaras därför exakt som i main.py.

Plain APIRouter med per-route gating verbatim: konsument-routerna kräver `require_consumer`,
admin-speglarna (`/v1/admin/products/{ean}/info|image`, samma resolver - decoupling) kräver
`require_admin`. Fulla paths -> identiska URL:er."""
import logging

from fastapi import APIRouter, Depends, Query
from fastapi.responses import FileResponse, JSONResponse

from .. import apilog, catalog, categories, config, database, details, images, matching, schemas
from ..database import get_conn
from ..deps import require_admin, require_consumer
from ..sync import STATE

log = logging.getLogger("matbutiker")

router = APIRouter()


@router.get("/v1/products/search", responses={200: {"model": schemas.ProductSearchResponse}})
async def products_search(
    q: str = Query(..., min_length=2, description="Söktext mot produktnamn"),
    limit: int = 40,
    chain: str | None = None,
    _auth=Depends(require_consumer),
):
    """Sök produkter på namn ur cachade erbjudanden. Distinkta produkter (EAN-grupperade,
    cross-chain), med normaliserade fält, kedjor, prisintervall och antal erbjudanden.
    OBS: bara produkter som finns i offers-cachen (butiker vars erbjudanden hämtats)."""
    products = database.list_products(q=q, chain=chain, limit=max(1, min(limit, 100)))
    return {"query": q, "count": len(products), "products": products}


@router.get("/v1/products/by-category", responses={200: {"model": schemas.ProductCategoryResponse}})
async def products_by_category(
    category: str = Query(..., description="Kanonisk kategori-nyckel (se /v1/categories)"),
    limit: int = 60,
    chain: str | None = None,
    _auth=Depends(require_consumer),
):
    """Bläddra produkter i en kanonisk kategori (ur cachade erbjudanden). Samma
    produktform som /v1/products/search. Sorterat på flest kedjor/erbjudanden."""
    if category not in {c["key"] for c in categories.CANONICAL}:
        return JSONResponse({"detail": "Okänd kategori."}, status_code=400)
    products = database.list_products(category=category, chain=chain, limit=max(1, min(limit, 200)))
    return {"category": category, "count": len(products), "products": products}


@router.get("/v1/products/catalog", responses={200: {"model": schemas.CatalogSearchResponse}})
async def products_catalog(
    q: str = Query(..., min_length=2, description="Söktext mot kedjornas katalog-sök"),
    limit: int = 60,
    _auth=Depends(require_consumer),
):
    """Live katalog-sök mot kedjornas NATIVA sök-API:er - **hela sortimentet**, nationellt/
    representativt **hyllpris** (ej butikslokalt, ej erbjudanden), grupperat på EAN cross-chain.
    Skilt från /v1/products/search (offers-cachen = butikslokala deals). Lidl saknas (ingen EAN
    i deras sök). Delresultat om en kedja är seg/nere; Axfood-EAN resolvas via ean_cache så
    okända katalog-koder blir fristående poster (ingen cross-chain-matchning)."""
    async with apilog.make_client(follow_redirects=True) as client:
        products = await catalog.catalog_search(client, q.strip(), limit=max(1, min(limit, 100)))
    return {"query": q.strip(), "count": len(products), "products": products}


@router.get("/v1/products/catalog/browse", responses={200: {"model": schemas.CatalogSearchResponse}})
async def products_catalog_browse(
    q: str | None = Query(None, min_length=2, description="Namn-filter (min 2 tecken)"),
    category: str | None = None,
    chain: str | None = None,
    limit: int = 60,
    offset: int = 0,
    only_offers: bool = False,
    sort: str | None = Query(None, description="price|spread|name|savings (annars default-ordning)"),
    deal: str | None = Query(None, description="multibuy|by_weight|flat - filtrera på erbjudande-typ"),
    favorites: bool = Query(False, description="bara produkter på rea hos inloggad användares favoritbutiker"),
    diet: str | None = Query(None, description="vegan|vegetarian - härledd kost (vegan ⊂ vegetarian); okänt faller bort"),
    manufacturer: str | None = Query(None, description="Tillverkar-nyckel (se /catalog/manufacturers); tål även fritt namn"),
    user=Depends(require_consumer),
):
    """Sök/bläddra den PERSISTERADE katalogen (`catalog_products`, fylld av crawlen) - hela
    sortimentet med hyllpris, EAN-grupperat cross-chain, + aktuella erbjudanden överlagrade.
    Snabbare än live-`/catalog` (ingen fan-out) och täcker crawlade kedjor. q ELLER category krävs.
    `offset` paginerar; `only_offers` filtrerar; `sort` ordnar (inkl. `savings` = störst besparing);
    `deal` filtrerar på erbjudande-typ (begränsar till rea-produkter); `manufacturer` filtrerar på
    normaliserad tillverkare. Server-side före paginering. `total` = antal matchande produkter."""
    fav_stores = None
    if favorites and user:  # inloggad användares favoritbutiker (server-side, ej från klient)
        fav_stores = [tok.split(":", 1) for tok in database.list_favorites(user["id"]) if ":" in tok]
    products, total = database.catalog_browse(q=q, category=category, chain=chain,
                                               limit=max(1, min(limit, 100)), offset=max(0, offset),
                                               only_offers=only_offers, sort=sort, deal=deal,
                                               fav_stores=fav_stores, diet=diet, manufacturer=manufacturer)
    catalog._enrich_with_offers(products)  # överlagra aktuella erbjudanden (samma som live-söket)
    return {"query": q or category or "", "count": len(products), "total": total, "products": products}


@router.get("/v1/products/catalog/summary")
async def products_catalog_summary(chain: str | None = None, only_offers: bool = False,
                                   favorites: bool = False, diet: str | None = None,
                                   user=Depends(require_consumer)):
    """Översikt av den persisterade katalogen: antal distinkta produkter per kanonisk kategori,
    total, samt produktantal per kedja. Driver bläddra-vyns kategori-räknare och totaler.
    `only_offers`/`favorites`/`diet` speglar bläddra-vyns filter (rea globalt resp. hos favoriter,
    härledd kost)."""
    fav_stores = None
    if favorites and user:
        fav_stores = [tok.split(":", 1) for tok in database.list_favorites(user["id"]) if ":" in tok]
    return database.catalog_summary(chain=chain, only_offers=only_offers, fav_stores=fav_stores, diet=diet)


@router.get("/v1/products/catalog/manufacturers", responses={200: {"model": schemas.CatalogManufacturersResponse}})
async def products_catalog_manufacturers(chain: str | None = None, q: str | None = None,
                                         limit: int = 200, _auth=Depends(require_consumer)):
    """Tillverkar-aggregat ur den persisterade katalogen: distinkta produkter per normaliserad
    tillverkare (`key` = stabil nyckel som matar `/catalog/browse?manufacturer=`, `name` = display-
    namn), flest först. `chain` scopar, `q` filtrerar på namnet. För tillverkar-katalog/filter."""
    return database.catalog_manufacturers(chain=chain, q=q, limit=max(1, min(limit, 1000)))


async def _resolve_product_info(ean: str, prefer_chain: str | None = None):
    """Delad produktinfo-resolver (cached-or-fetch + partial-uppgradering). Returnerar svars-dicten,
    eller None vid ogiltig EAN. Delas av konsument-endpointen och konsolens admin-endpoint så att
    admin-UI:t inte behöver anropa konsument-ytan (decoupling inför en framtida api/app/admin-split)."""
    e = matching.normalize_ean(ean)
    if not e:
        return None
    present, cached, fetched_at = database.product_info_cached(e)
    # Partial = EN-källa-piggyback (Coop/Axfood ur crawl/warm) -> uppgradera till full korsskällig
    # merge nu när någon faktiskt vill se detaljerna (annars servera cache-träffen direkt).
    if present and not (cached and cached.get("partial")):
        return {"ean": e, "found": cached is not None,
                "info": details.normalize_info(cached), "fetched_at": fetched_at}
    errored = False
    info = None
    try:
        async with apilog.make_client(follow_redirects=True) as client:
            info = await details.fetch_for_ean(client, e, prefer_chain=prefer_chain)
    except Exception as ex:  # noqa: BLE001
        log.warning("produktinfo %s misslyckades: %s", e, ex)
        errored = True
    # Spara även negativt (info=None -> data=null) så upprepade öppningar blir omedelbara och
    # inte re-hämtar från källorna. Vid fel cachas inte (kan vara transient -> nytt försök).
    fetched_at = database.save_product_info(e, info) if not errored else None
    return {"ean": e, "found": info is not None,
            "info": details.normalize_info(info), "fetched_at": fetched_at}


@router.get("/v1/products/{ean}", responses={200: {"model": schemas.ProductInfoResponse}})
async def product_info(ean: str, prefer_chain: str | None = None, _auth=Depends(require_consumer)):
    """EAN-global produktinfo (ingredienser/näring/ursprung), lazy + EAN-cachad. prefer_chain hintar
    rikare native-källa (Axfood har näring); annars Coops EAN-DB. `source` i svaret."""
    r = await _resolve_product_info(ean, prefer_chain)
    return r if r is not None else JSONResponse({"detail": "Ogiltig EAN."}, status_code=400)


@router.get("/v1/admin/products/{ean}/info", responses={200: {"model": schemas.ProductInfoResponse}})
async def admin_product_info(ean: str, prefer_chain: str | None = None, _=Depends(require_admin)):
    """Produktinfo för konsolen (admin-gated, samma resolver som konsument-endpointen). Egen route
    så konsol-UI:t bara talar med /v1/admin/* - håller admin frikopplat från konsument-ytan inför
    en framtida api/app/admin-split."""
    r = await _resolve_product_info(ean, prefer_chain)
    return r if r is not None else JSONResponse({"detail": "Ogiltig EAN."}, status_code=400)


async def _resolve_product_image(ean: str, size: str):
    """Delad bild-resolver (proxa + cacha -> CDN-oberoende). Delas av konsument- och admin-route."""
    e = matching.normalize_ean(ean)
    if not e:
        return JSONResponse({"detail": "Ogiltig EAN."}, status_code=400)
    if size not in images.SIZES:
        size = "default"
    res = await images.get_cached_image(e, size)
    if not res:
        return JSONResponse({"detail": "Ingen bild hittades."}, status_code=404)
    path, ct = res
    return FileResponse(path, media_type=ct, headers={"Cache-Control": "public, max-age=86400"})


@router.get("/v1/products/{ean}/image")
async def product_image(ean: str, size: str = "default", _auth=Depends(require_consumer)):
    """Lokalt cachad produktbild för EAN:en (proxas + cachas -> CDN-oberoende).
    `size` = thumb|default|full (cachas separat). Same-origin <img> skickar cookie."""
    return await _resolve_product_image(ean, size)


@router.get("/v1/admin/products/{ean}/image")
async def admin_product_image(ean: str, size: str = "default", _=Depends(require_admin)):
    """Produktbild för konsolen (admin-gated, samma resolver). Egen route så konsol-UI:t bara
    talar med /v1/admin/* (decoupling inför en framtida api/app/admin-split)."""
    return await _resolve_product_image(ean, size)


@router.get("/v1/products/{ean}/history", responses={200: {"model": schemas.PriceHistoryResponse}})
async def product_price_history(ean: str, _auth=Depends(require_consumer)):
    """Prishistorik (tidsserie) för en EAN ur arkiverade erbjudande-observationer
    (`offer_observations`). Grupperad per kedja, kollapsad på lika prisnivå (butiker med samma
    pris -> en punkt, `stores` räknar dem). Erbjudande-data = fyndspårning: en produkt syns
    bara när den varit nedsatt, så serien har luckor (offer utgår vid `valid_to`)."""
    e = matching.normalize_ean(ean)
    if not e:
        return JSONResponse({"detail": "Ogiltig EAN."}, status_code=400)
    hist = database.price_history(e)
    hist["shelf"] = database.catalog_price_history(e)  # hyllpris-serie (ordinarie) sammanslagen i grafen
    return hist


@router.get("/v1/products/{ean}/stores", responses={200: {"model": schemas.ProductStoresResponse}})
async def product_stores(ean: str, _auth=Depends(require_consumer)):
    """Butiker som just nu har ett ERBJUDANDE på EAN:en (billigaste per butik), för kartfilter.
    OBS: bygger på erbjudande-cachen - visar butiker med ett erbjudande, inte hyllsortiment.
    EAN matchas inline (ICA/Coop/CG) eller via Axfood-koden (Willys/Hemköp, reverse-resolvat)."""
    e = matching.normalize_ean(ean)
    if not e:
        return JSONResponse({"detail": "Ogiltig EAN."}, status_code=400)
    stores = database.stores_with_offer(e)
    return {"ean": e, "count": len(stores), "stores": stores}


@router.get("/v1/products/{ean}/store-prices")
async def product_store_prices(ean: str, _auth=Depends(require_consumer)):
    """Per-butik-HYLLPRISER för en EAN (Steg 6): ICA/Coop varierar per butik -> alla crawlade butikers
    pris + namn/ort, billigast först. Driver bläddra-vyns intervall-modal. Tom för nationellt prissatta
    kedjor (Willys/Hemköp/CG) som inte har per-butik-data."""
    e = matching.normalize_ean(ean)
    if not e:
        return JSONResponse({"detail": "Ogiltig EAN."}, status_code=400)
    d = database.store_prices_for_ean(e)
    return {"ean": e, "total_stores": d["total_stores"], "levels": d["levels"]}


@router.get("/v1/categories", responses={200: {"model": schemas.CategoriesResponse}})
async def categories_list(_auth=Depends(require_consumer)):
    """Kanonisk kategori-vokabulär (för filtrering i erbjudande-vyer)."""
    return {"categories": categories.CANONICAL}


@router.get("/v1/chains", responses={200: {"model": schemas.ChainsResponse}})
async def chains(_auth=Depends(require_consumer)):
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
