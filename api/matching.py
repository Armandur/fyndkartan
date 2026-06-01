"""Cross-chain produktmatchning via EAN.

Endast exakt EAN-matchning (nivå 2). Strikt normalisering så att butiksinterna
koder (2-prefix/rörlig vikt) och ogiltiga längder inte ger falska matchningar.

VIKTIGT om pris: råpriset per erbjudande är INTE jämförbart mellan kedjor
(olika förpackningsstorlek, multibuy som "2 för 129", medlemspris). Jämförelsen
sker därför på **enhetspris** (jämförpris, kr/kg|liter|st) när det finns för alla
butiker i gruppen; annars faller vi tillbaka på råpris och flaggar det med
`compare_by`. Råpris + `price_text` ("2 för 129 kr") visas alltid för kontext.
"""

_OFFER_KEYS = (
    "chain",
    "store_id",
    "store_name",
    "distance_km",
    "price",
    "price_text",
    "comparison_value",
    "comparison_unit",
    "member_price",
    "mechanic_type",
    "valid_to",
)


def normalize_ean(raw):
    """Giltig, globalt unik GTIN-sträng, annars None.

    - bara siffror, längd 8/12/13/14
    - GS1 '2'-prefix = butiksintern/rörlig vikt, ej globalt unik -> None
    """
    if raw is None:
        return None
    s = "".join(ch for ch in str(raw) if ch.isdigit())
    if len(s) not in (8, 12, 13, 14):
        return None
    if s[0] == "2":
        return None
    return s


def _norm_unit(u):
    return (u or "").strip().rstrip(".").lower() or None


def _metric(o):
    """Sorterings-/dedupvärde per butik: enhetspris om det finns, annars råpris."""
    v = o.get("comparison_value")
    if v is None:
        v = o.get("price")
    return v if v is not None else float("inf")


def build_comparisons(entries, min_chains=2, manual_groups=None):
    """Produktgrupper som finns hos >= min_chains olika kedjor, sorterade på
    prisspridning (störst besparing först). Grupperas per EAN; offers vars
    (chain, ean) tillhör samma manuella märkesvaru-grupp slås ihop trots olika EAN.

    manual_groups: {ean: group_id}. EAN-nycklad så en paring täcker varje kedja som
    bär samma EAN (t.ex. Willys+Hemköp som delar Axfood-EAN). Tom = ren EAN-matchning."""
    manual_groups = manual_groups or {}
    groups = {}
    meta = {}  # key -> (None, ean) eller (group_id, None)
    for e in entries:
        for ean in {normalize_ean(x) for x in (e.get("eans") or [])}:
            if not ean:
                continue
            gid = manual_groups.get(ean)
            key = f"m{gid}" if gid is not None else ean
            groups.setdefault(key, []).append(e)
            meta[key] = (gid, None) if gid is not None else (None, ean)

    out = []
    for key, offs in groups.items():
        group_id, ean = meta[key]
        # En post per butik (lägst enhetspris vid dubbletter).
        by_store = {}
        for o in offs:
            k = (o["chain"], o["store_id"])
            cur = by_store.get(k)
            if cur is None or _metric(o) < _metric(cur):
                by_store[k] = o
        stores = list(by_store.values())
        if len({o["chain"] for o in stores}) < min_chains:
            continue

        # Jämför på enhetspris om alla butiker har det + samma enhet, annars råpris.
        units = {_norm_unit(o.get("comparison_unit")) for o in stores if o.get("comparison_value") is not None}
        uvals = [o["comparison_value"] for o in stores if o.get("comparison_value") is not None]
        if len(uvals) == len(stores) and len(units) == 1 and None not in units:
            compare_by, unit, vals = "unit_price", units.pop(), uvals
        else:
            compare_by, unit = "price", "kr"
            vals = [o["price"] for o in stores if o.get("price") is not None]
        if len(vals) < 2:
            continue

        key = "comparison_value" if compare_by == "unit_price" else "price"
        stores.sort(key=lambda o: (o.get(key) is None, o.get(key) or 0, o.get("distance_km") or 0))
        named = next((o for o in stores if o.get("name")), stores[0])
        out.append(
            {
                "ean": ean,
                "match_group": group_id,
                "manual": group_id is not None,
                "name": named.get("name"),
                "brand": named.get("brand"),
                "image": named.get("image"),
                "category": named.get("category_raw"),
                "compare_by": compare_by,
                "unit": unit,
                "chains": len({o["chain"] for o in stores}),
                "stores": len(stores),
                "min": round(min(vals), 2),
                "max": round(max(vals), 2),
                "spread": round(max(vals) - min(vals), 2),
                "offers": [{k: o.get(k) for k in _OFFER_KEYS} for o in stores],
            }
        )

    return _merge_same_deal(out)


def _r(v):
    return round(v, 2) if isinstance(v, (int, float)) else None


def _merge_same_deal(groups):
    """Slå ihop grupper med identisk erbjudande-uppsättning (samma kampanj som
    täcker flera varianter/EAN, t.ex. 'Zeta pasta 3 för 39' i flera former)."""
    merged = {}
    for g in groups:
        sig = tuple(
            sorted(
                (o["chain"], o["store_id"], _r(o.get("price")), _r(o.get("comparison_value")),
                 o.get("price_text"))
                for o in g["offers"]
            )
        )
        m = merged.get(sig)
        if m:
            if g["name"] and g["name"] not in m["variants"]:
                m["variants"].append(g["name"])
            if g["ean"]:
                m["eans"].append(g["ean"])
        else:
            g["variants"] = [g["name"]] if g["name"] else []
            g["eans"] = [g["ean"]] if g["ean"] else []
            merged[sig] = g
    result = list(merged.values())
    for g in result:
        g["variant_count"] = len(g["variants"])
    result.sort(key=lambda g: g["spread"], reverse=True)
    return result
