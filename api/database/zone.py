"""Steg 6 / Fas C: geo-first zon-browse. Bläddra sortimentet inom en geografisk zon (punkt + radie):
union av varor i zonens butiker, per vara billigast-i-zonen + intervall + antal butiker.

Prissemantik per kedja: ICA/Coop = butiksspecifikt pris -> zon-aggregat ur `catalog_store_prices`
(billigast/intervall/antal butiker bland zonens butiker); Willys/Hemköp/City Gross = NATIONELLT pris
(samma i hela landet, bekräftat) -> tas med OM en sådan butik finns i zonen; Lidl saknar prisdata.
Återanvänder `catalog._browse_groups` (cachad EAN-gruppering) för visningsfält + samma CatalogProduct-
form som `catalog_browse` (frontend delar `catalogCard`). Begränsning: ~0,25% av per-butik-priserna
saknar master-rad i `catalog_products` -> de saknar visningsfält och faller bort (försumbart)."""
import json

from sqlalchemy import bindparam, text

from ._conn import get_conn
from ..geo import haversine
from .. import manufacturers
from .catalog import (_BROWSE_SORTS, _NONFOOD_DIET, _browse_groups, _cat_canonical, _cat_pick,
                      _normalize_catalog_page, _parse_origin)
from .products import get_product_diets

_PER_STORE = ("ica", "coop")                            # butiksspecifikt pris (zon-aggregat)
_PRICED_NATIONAL = ("willys", "hemkop", "citygross")    # nationellt hyllpris (tas med vid zon-närvaro)
ZONE_MAX_RADIUS_KM = 50.0                                # serverside-cap (perf skalar med antal butiker)


def zone_stores(lat, lng, radius_km):
    """Fysiska butiker (alla kedjor) inom radien (haversine). Returnerar dict:
    `pairs` {"ica":[ledgers], "coop":[ledgers]} (dedupade, för per-butik-prisaggregatet);
    `national` set av nationellt prissatta kedjor med >=1 butik i zonen; `stores` lista
    [{chain, store_id, name, city, lat, lng, distance_km}] närmast först (kart-visning)."""
    conn = get_conn()
    rows = conn.execute(text(
        "SELECT chain, store_id, name, city, lat, lng, native FROM stores "
        "WHERE chain IN ('ica','coop','willys','hemkop','citygross','lidl') "
        "AND lat IS NOT NULL AND lng IS NOT NULL")).fetchall()
    conn.close()
    pairs = {"ica": set(), "coop": set()}
    national = set()
    stores = []
    for r in rows:
        if not r["lat"] or not r["lng"]:
            continue
        dist = haversine(lat, lng, r["lat"], r["lng"])
        if dist > radius_km:
            continue
        stores.append({"chain": r["chain"], "store_id": r["store_id"], "name": r["name"],
                       "city": r["city"], "lat": r["lat"], "lng": r["lng"], "distance_km": round(dist, 1)})
        if r["chain"] in _PER_STORE:
            nat = json.loads(r["native"]) if r["native"] else {}
            led = nat.get("ledgerAccountNumber") if r["chain"] == "coop" else nat.get("accountNumber")
            if led:
                pairs[r["chain"]].add(str(led))
        elif r["chain"] in _PRICED_NATIONAL:
            national.add(r["chain"])
    stores.sort(key=lambda s: s["distance_km"])
    return {"pairs": {k: list(v) for k, v in pairs.items()}, "national": national, "stores": stores}


def _zone_aggregate(pairs):
    """{(chain, product_id): (pmin, pmax, n_stores)} ur catalog_store_prices, scopat till zonens
    ICA/Coop-butiker. `COUNT(*)` = antal butiker (PK chain/product_id/store -> en rad per butik, ingen
    DISTINCT-sort). En OR-grenad fråga -> idx_csp_cover (chain,store,...)-prefix per kedja."""
    ica, coop = pairs.get("ica") or [], pairs.get("coop") or []
    if not ica and not coop:
        return {}
    conn = get_conn()
    sql = text(
        "SELECT chain, product_id, MIN(price) pmin, MAX(price) pmax, COUNT(*) ns "
        "FROM catalog_store_prices WHERE price > 0 AND "  # 0 = saknat/skräp-pris (~0,4%), ej 'billigast'
        "((chain='ica' AND store IN :ica) OR (chain='coop' AND store IN :coop)) "
        "GROUP BY chain, product_id"
    ).bindparams(bindparam("ica", expanding=True), bindparam("coop", expanding=True))
    rows = conn.execute(sql, {"ica": ica or [""], "coop": coop or [""]}).fetchall()
    conn.close()
    return {(r["chain"], r["product_id"]): (r["pmin"], r["pmax"], r["ns"]) for r in rows}


def catalog_zone_browse(lat, lng, radius_km=10.0, q=None, category=None, manufacturer=None,
                        diet=None, sort=None, limit=60, offset=0):
    """Bläddra zonens sortiment (union av varor i zonens butiker). Per vara: billigast-i-zonen +
    intervall + antal butiker (ICA/Coop) resp. nationellt pris (Willys/Hemköp/CG vid zon-närvaro).
    Filter: `q` (namn, grupp-vis), `category` (kanonisk), `manufacturer` (normaliserad nyckel),
    `diet` (vegan/vegetarian). `sort`: price|spread|name (annars flest kedjor -> billigast -> namn).
    Returnerar `(sida, total, categories, zone_meta)`: categories = räknare per kategori över HELA
    zonen (filter-chips, speglar diet men ej category-filtret); zone_meta = zonens butiker/kedjor."""
    radius_km = max(0.5, min(float(radius_km), ZONE_MAX_RADIUS_KM))
    z = zone_stores(lat, lng, radius_km)
    agg = _zone_aggregate(z["pairs"])
    national = z["national"]
    ql = (q or "").strip().lower()
    mkey = manufacturers.manufacturer_key(manufacturer) if manufacturer else None
    dmap = okdiet = None
    if diet in ("vegan", "vegetarian"):
        dmap = get_product_diets()
        okdiet = {"vegan"} if diet == "vegan" else {"vegan", "vegetarian"}
    out, cats = [], {}
    for g in _browse_groups().values():
        # Pris-i-zonen FÖRST (billiga dict-lookups) -> hoppa varor utanför zonen innan de dyra
        # kategori-/tillverkar-härledningarna. ICA/Coop zon-aggregat; nationellt vid zon-närvaro.
        prices = []
        for m in g:
            ch = m["chain"]
            if ch in _PER_STORE:
                a = agg.get((ch, m["product_id"]))
                if a and a[0] is not None:
                    prices.append({"chain": ch, "price": a[0], "price_min": a[0], "price_max": a[1],
                                   "price_stores": a[2], "comparison_value": m["comparison_value"],
                                   "comparison_unit": m["comparison_unit"], "comparison_derived": False,
                                   "store": None})
            elif ch in national and m["price"] is not None:
                prices.append({"chain": ch, "price": m["price"], "price_min": None, "price_max": None,
                               "price_stores": None, "comparison_value": m["comparison_value"],
                               "comparison_unit": m["comparison_unit"], "comparison_derived": False,
                               "store": None})
        if not prices:
            continue  # varan finns inte i zonen
        rep = next((m for m in g if m.get("name")), g[0])
        if ql and ql not in (rep["name"] or "").lower() and not any(ql in (m["name"] or "").lower() for m in g):
            continue  # grupp-vis: någon kedjas namn matchar (olika ordordning ICA/Coop)
        brand = _cat_pick(g, "brand")
        if mkey and manufacturers.manufacturer_key(brand) != mkey:
            continue
        cat = _cat_canonical(g)
        if dmap is not None and (dmap.get(rep["ean"]) not in okdiet or cat in _NONFOOD_DIET):
            continue
        cats[cat] = cats.get(cat, 0) + 1  # kategori-räknare över HELA zonen (före category-filtret)
        if category and cat != category:
            continue
        pv = [p["price"] for p in prices]
        pvmax = [p["price_max"] if p.get("price_max") is not None else p["price"] for p in prices]
        out.append({
            "ean": rep["ean"], "name": rep["name"], "brand": brand, "manufacturer": None,  # canonical sätts på sidan
            "origin": _parse_origin(_cat_pick(g, "origin")),
            "image": _cat_pick(g, "image"), "category": cat,
            "package_size": _cat_pick(g, "package_size"), "package_value": rep["package_value"],
            "package_unit": rep["package_unit"],
            "chains": sorted({p["chain"] for p in prices}),
            "prices": sorted(prices, key=lambda p: p["price"]),
            "price_min": min(pv) if pv else None, "price_max": max(pvmax) if pvmax else None,
            "_ax": [m["product_id"] for m in g if m["chain"] in ("willys", "hemkop") and m.get("product_id")],
        })
    out.sort(key=_BROWSE_SORTS.get(sort) or (lambda p: (
        -len(p["chains"]), p["price_min"] if p["price_min"] is not None else 9e9, (p["name"] or "").lower())))
    total = len(out)
    page = out[offset:offset + limit]
    for p in page:  # normaliserad tillverkare bara på sidan (dyrt -> skjut upp från huvudloopen)
        p["manufacturer"] = manufacturers.canonical(p["brand"])
    _normalize_catalog_page(page)  # derive-at-read: bara sidan (förpackning/jämförenhet/ursprung)
    chains_priced = sorted({c for c in _PER_STORE if z["pairs"].get(c)} | national)
    zone_meta = {"lat": lat, "lng": lng, "radius_km": radius_km, "store_count": len(z["stores"]),
                 "chains_priced": chains_priced,
                 "lidl_in_zone": any(s["chain"] == "lidl" for s in z["stores"]),
                 "stores": z["stores"]}
    return page, total, dict(sorted(cats.items(), key=lambda kv: -kv[1])), zone_meta
