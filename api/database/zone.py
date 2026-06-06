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
from ..matching import normalize_ean
from .. import manufacturers
from .catalog import (_BROWSE_SORTS, _NONFOOD_DIET, _browse_groups, _cat_canonical, _cat_pick,
                      _normalize_catalog_page, _parse_origin, catalog_names_for_eans)
from .offers import _deal_type
from .meta import load_match_members
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
        led = None
        if r["chain"] in _PER_STORE:
            nat = json.loads(r["native"]) if r["native"] else {}
            led = nat.get("ledgerAccountNumber") if r["chain"] == "coop" else nat.get("accountNumber")
            if led:
                led = str(led)
                pairs[r["chain"]].add(led)
        elif r["chain"] in _PRICED_NATIONAL:
            national.add(r["chain"])
        stores.append({"chain": r["chain"], "store_id": r["store_id"], "name": r["name"],
                       "city": r["city"], "lat": r["lat"], "lng": r["lng"],
                       "distance_km": round(dist, 1), "ledger": led})
    stores.sort(key=lambda s: s["distance_km"])
    return {"pairs": {k: list(v) for k, v in pairs.items()}, "national": national, "stores": stores}


def _scope_from_pairs(store_pairs):
    """Samma scope-struktur som `zone_stores` men för en EXPLICIT butikslista (favoriter):
    [(chain, store_id)] -> {pairs, national, stores}. Inget avstånd (ingen mittpunkt)."""
    want = {(c, str(s)) for c, s in store_pairs if c and s is not None}
    pairs = {"ica": set(), "coop": set()}
    national = set()
    stores = []
    if not want:
        return {"pairs": {"ica": [], "coop": []}, "national": national, "stores": stores}
    conn = get_conn()
    rows = conn.execute(text(
        "SELECT chain, store_id, name, city, lat, lng, native FROM stores "
        "WHERE chain IN :chains").bindparams(bindparam("chains", expanding=True)),
        {"chains": list({c for c, _ in want})}).fetchall()
    conn.close()
    for r in rows:
        if (r["chain"], str(r["store_id"])) not in want:
            continue
        led = None
        if r["chain"] in _PER_STORE:
            nat = json.loads(r["native"]) if r["native"] else {}
            led = nat.get("ledgerAccountNumber") if r["chain"] == "coop" else nat.get("accountNumber")
            if led:
                led = str(led)
                pairs[r["chain"]].add(led)
        elif r["chain"] in _PRICED_NATIONAL:
            national.add(r["chain"])
        stores.append({"chain": r["chain"], "store_id": r["store_id"], "name": r["name"],
                       "city": r["city"], "lat": r["lat"], "lng": r["lng"],
                       "distance_km": None, "ledger": led})
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
    return page, total, dict(sorted(cats.items(), key=lambda kv: -kv[1])), _zone_meta(z, lat, lng, radius_km)


def _zone_meta(z, lat, lng, radius_km, scope="zone"):
    """Zonens/urvalets metadata-block (delas av zon-browse + matkasse-jämförelse). scope=zone|favorites."""
    chains_priced = sorted({c for c in _PER_STORE if z["pairs"].get(c)} | z["national"])
    return {"lat": lat, "lng": lng, "radius_km": radius_km, "scope": scope, "store_count": len(z["stores"]),
            "chains_priced": chains_priced,
            "lidl_in_zone": any(s["chain"] == "lidl" for s in z["stores"]),
            "stores": z["stores"]}


def basket_compare(items, lat=None, lng=None, radius_km=10.0, pairs=None, max_results=30):
    """Jämför en matkasse ([{ean, qty, exact}]) över ett butiksurval. Per kandidat: HYLLPRIS-total +
    ERBJUDANDE-överlagrad total (effektivt pris/vara = min(hyllpris, erbjudande/st)) + täckning
    (funna/saknade varor). ICA/Coop = fysiska butiker dedupade per ledger (närmast som representant,
    `store_count` räknar resten); Willys/Hemköp/CG = nationellt (en post per kedja i zonen). EAN-exakt
    -> hyllpriserna är per styck (ingen enhets-normalisering behövs).

    PRIVATE-LABEL-PARNING: en korg-vara som ingår i en manuell paring (`product_matches`) matchas mot
    HELA gruppens EAN:er per butik -> billigaste tillgängliga private-label-motsvarighet används som
    substitut (varje kedjas egna märke jämförs rättvist). `exact=True` på varan stänger av substitutionen
    (jämför bara exakt den EAN:en). Rankas: FULL täckning först, sedan billigaste effektiva total.

    SCOPE: `pairs` (lista (chain, store_id)) = jämför ett EXPLICIT butiksurval (favoriter); annars geo
    (`lat`/`lng`/`radius_km`)."""
    fav_scope = pairs is not None
    if not fav_scope:
        radius_km = max(0.5, min(float(radius_km), ZONE_MAX_RADIUS_KM))
    norm, seen = [], set()
    for it in items or []:
        e = normalize_ean(it.get("ean"))
        if e and e not in seen:
            seen.add(e)
            norm.append((e, max(1, int(it.get("qty") or 1)), bool(it.get("exact"))))
    z = _scope_from_pairs(pairs) if fav_scope else zone_stores(lat, lng, radius_km)
    meta = lambda: _zone_meta(z, None if fav_scope else lat, None if fav_scope else lng,
                              None if fav_scope else radius_km, "favorites" if fav_scope else "zone")
    basket_eans = [e for e, _, _ in norm]
    if not basket_eans:
        return {"zone": meta(), "basket": [], "results": [], "unavailable": []}
    # Private-label-grupper: ean -> grupp, grupp -> alla EAN:er
    group_of, group_eans = {}, {}
    for m in load_match_members():
        group_of[m["ean"]] = m["group_id"]
        group_eans.setdefault(m["group_id"], []).append(m["ean"])
    # Per korg-vara: substituerings-EAN:er (gruppens alla om parad + ej exact, annars bara varan själv)
    subs = {}
    for e, _, exact in norm:
        gid = None if exact else group_of.get(e)
        subs[e] = group_eans.get(gid, [e]) if gid is not None else [e]
    all_eans = list({s for ss in subs.values() for s in ss})
    names = catalog_names_for_eans(all_eans)
    ica, coop = z["pairs"].get("ica") or [], z["pairs"].get("coop") or []
    national = z["national"]
    conn = get_conn()
    shelf = {}  # (chain, ledger, ean) -> hyllpris
    if ica or coop:
        for r in conn.execute(text(
            "SELECT chain, store, ean, MIN(price) p FROM catalog_store_prices "
            "WHERE ean IN :eans AND price > 0 AND "
            "((chain='ica' AND store IN :ica) OR (chain='coop' AND store IN :coop)) "
            "GROUP BY chain, store, ean").bindparams(
            bindparam("eans", expanding=True), bindparam("ica", expanding=True), bindparam("coop", expanding=True)),
                {"eans": all_eans, "ica": ica or [""], "coop": coop or [""]}):
            shelf[(r["chain"], r["store"], r["ean"])] = r["p"]
    nat_shelf = {}  # (chain, ean) -> nationellt hyllpris
    if national:
        for r in conn.execute(text(
            "SELECT chain, ean, MIN(price) p FROM catalog_products "
            "WHERE ean IN :eans AND available=1 AND price > 0 AND chain IN :ch GROUP BY chain, ean").bindparams(
            bindparam("eans", expanding=True), bindparam("ch", expanding=True)),
                {"eans": all_eans, "ch": list(national)}):
            nat_shelf[(r["chain"], r["ean"])] = r["p"]
    # Erbjudanden: styckpris per (kedja, butik, ean) + bästa per (kedja, ean) (nationellt).
    store_off, chain_off = {}, {}
    for r in conn.execute(text(
        "SELECT oe.ean, o.chain, o.store_id, o.price, o.price_text, o.member_price FROM offer_eans oe "
        "JOIN offers o ON oe.chain=o.chain AND oe.store_id=o.store_id AND oe.offer_id=o.offer_id "
        "WHERE oe.ean IN :eans AND o.price IS NOT NULL").bindparams(bindparam("eans", expanding=True)),
            {"eans": all_eans}):
        _, mq = _deal_type(r["price_text"])
        per = r["price"] / (mq or 1)
        rec = (per, bool(r["member_price"]))
        sk = (r["chain"], str(r["store_id"]), r["ean"])
        if sk not in store_off or per < store_off[sk][0]:
            store_off[sk] = rec
        ck = (r["chain"], r["ean"])
        if ck not in chain_off or per < chain_off[ck][0]:
            chain_off[ck] = rec
    conn.close()

    def _candidate(kind, chain, store_id, name, city, distance, store_count, get, national=False):
        lines, ts, to, found, missing, member = [], 0.0, 0.0, 0, [], False
        for e, q, _exact in norm:
            # Välj den substituerings-EAN med lägst EFFEKTIVT pris hos butiken (private-label-parning).
            best = None  # (eff, sh, offp, member, used_ean)
            for se in subs[e]:
                sh, of = get(se)
                if sh is None:
                    continue
                offp = of[0] if of else None
                used = offp is not None and offp < sh
                eff = offp if used else sh
                if best is None or eff < best[0]:
                    best = (eff, sh, offp, bool(used and of and of[1]), se)
            if best is None:  # ingen variant fanns hos butiken
                missing.append(e)
                lines.append({"ean": e, "name": names.get(e), "qty": q, "shelf": None, "offer": None,
                              "eff": None, "used_ean": None, "used_name": None})
                continue
            eff, sh, offp, mem, used_ean = best
            found += 1
            member = member or mem
            ts += sh * q
            to += eff * q
            lines.append({"ean": e, "name": names.get(e), "qty": q, "shelf": round(sh, 2),
                          "offer": round(offp, 2) if offp is not None else None, "eff": round(eff, 2),
                          "used_ean": used_ean if used_ean != e else None,
                          "used_name": names.get(used_ean) if used_ean != e else None})
        return {"kind": kind, "chain": chain, "store_id": store_id, "name": name, "city": city,
                "distance_km": distance, "store_count": store_count, "national": national,
                "total_shelf": round(ts, 2), "total_offer": round(to, 2),
                "found": found, "missing": missing, "uses_member": member, "lines": lines}

    results = []
    by_ledger = {}  # ICA/Coop fysiska butiker dedupade per (kedja, ledger), närmast som representant
    for s in z["stores"]:
        if s["chain"] not in _PER_STORE or not s.get("ledger"):
            continue
        k = (s["chain"], s["ledger"])
        cur = by_ledger.get(k)
        if cur is None:
            by_ledger[k] = {**s, "store_count": 1}
        else:
            cur["store_count"] += 1
            if s["distance_km"] < cur["distance_km"]:
                cur.update(store_id=s["store_id"], name=s["name"], city=s["city"], distance_km=s["distance_km"])
    for (chain, ledger), s in by_ledger.items():
        sid = str(s["store_id"])
        results.append(_candidate(
            "store", chain, s["store_id"], s["name"], s["city"], s["distance_km"], s["store_count"],
            lambda e, c=chain, lg=ledger, st=sid: (shelf.get((c, lg, e)), store_off.get((c, st, e)))))
    # Nationella kedjor (Willys/Hemköp/CG): ETT kort per kedja (samma nationella pris -> visas en gång)
    # med kedjans butiker i zonen uppradade (närmast först) i `stores`. store_count = antal butiker.
    for chain in sorted(national):  # national ⊆ _PRICED_NATIONAL (prissatta nationella kedjor i urvalet)
        cstores = sorted((s for s in z["stores"] if s["chain"] == chain),
                         key=lambda s: (s["distance_km"] is None, s["distance_km"] or 0))
        cand = _candidate(
            "chain", chain, None, None, None,
            cstores[0]["distance_km"] if cstores else None, len(cstores),
            lambda e, ch=chain: (nat_shelf.get((ch, e)), chain_off.get((ch, e))), national=True)
        cand["stores"] = [{"store_id": s["store_id"], "name": s["name"], "city": s["city"],
                           "distance_km": s["distance_km"]} for s in cstores]
        results.append(cand)
    results.sort(key=lambda r: (len(r["missing"]), r["total_offer"]))  # full täckning först, sedan billigast
    available = {k[2] for k in shelf} | {k[1] for k in nat_shelf}  # EAN:er med pris någonstans i zonen
    unavailable = [{"ean": e, "name": names.get(e)} for e, _, _ in norm
                   if not any(se in available for se in subs[e])]
    return {"zone": meta(),
            "basket": [{"ean": e, "name": names.get(e), "qty": q,
                        "exact": exact, "paired": (group_of.get(e) is not None)} for e, q, exact in norm],
            "results": results[:max_results], "unavailable": unavailable}
