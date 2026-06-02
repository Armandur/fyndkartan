"""Märkesvaru-paring: hitta kedjornas private-label-produkter (ur offers) och
para ihop motsvarigheter cross-chain manuellt. Egna märkesvaror har kedjeinterna
EAN och matchar därför aldrig automatiskt - här bygger admin en stabil, EAN-nycklad
mappning (`product_matches`) som `matching.build_comparisons` sedan slår ihop på.

EAN-centrerat: en produkt = en EAN. Samma EAN i flera kedjor (Willys+Hemköp delar
Axfood-EAN) kollapsas till EN post taggad med alla kedjor - de matchar redan
automatiskt och behöver aldrig paras. Paring sker bara över olika private labels.

Endast offers-data (v1): listan = private-label-produkter som synts i erbjudanden.
"""

import json
import re

from fastapi import APIRouter, Body, Depends, Query
from fastapi.responses import JSONResponse

from . import auth, config, database as db, embeddings
from .matching import normalize_ean

router = APIRouter(prefix="/v1/admin", dependencies=[Depends(auth.require_admin)])

_AXFOOD = ("willys", "hemkop")

# ---- Textanalys för matchningsförslag ----
_UNITS = [("kg", 1000.0, "mass"), ("gram", 1.0, "mass"), ("g", 1.0, "mass"),
          ("liter", 1000.0, "vol"), ("dl", 100.0, "vol"), ("cl", 10.0, "vol"),
          ("ml", 1.0, "vol"), ("l", 1000.0, "vol")]
_STOP = {"med", "och", "av", "den", "det", "ekologisk", "eko", "ny", "färsk", "svensk"}


def parse_qty(text):
    """(normaliserat värde, 'mass'|'vol'|'count') ur en text, annars None.
    Hanterar '500 g', '500 Gram', 'GARANT, 500g', '1l', '5-pack'."""
    if not text:
        return None
    t = text.lower().replace(",", ".")
    m = re.search(r"(\d+(?:\.\d+)?)\s*(kg|gram|g|liter|dl|cl|ml|l)\b", t)
    if m:
        val = float(m.group(1))
        for name, factor, kind in _UNITS:
            if m.group(2) == name:
                return (val * factor, kind)
    m = re.search(r"(\d+)\s*[-]?\s*(?:pack|st|x)\b", t)
    if m:
        return (float(m.group(1)), "count")
    return None


def name_tokens(name):
    t = re.sub(r"[^a-zåäö ]", " ", (name or "").lower())
    return {w for w in t.split() if len(w) > 2 and w not in _STOP}


def _package_bonus(a, b):
    """Bonus för matchande förpacknings-storlek: +0.3 exakt, +0.15 inom 10%, annars 0."""
    qa = parse_qty(a.get("package")) or parse_qty(a.get("name"))
    qb = parse_qty(b.get("package")) or parse_qty(b.get("name"))
    if qa and qb and qa[1] == qb[1]:
        if abs(qa[0] - qb[0]) < 1e-6:
            return 0.3
        if abs(qa[0] - qb[0]) / max(qa[0], qb[0]) < 0.1:
            return 0.15
    return 0.0


def score(a, b):
    """Lexikal likhet (fallback när embeddings saknas): namn-tokenöverlapp + förpacknings-bonus."""
    ta, tb = name_tokens(a["name"]), name_tokens(b["name"])
    if not ta or not tb:
        return 0.0
    jac = len(ta & tb) / len(ta | tb)
    return round(jac + _package_bonus(a, b), 3)


# Minsta cosine för att en kandidat ska tas med (semantisk grind; förpacknings-bonus
# adderas bara för rankning, inte för att passera grinden). Verifierat: samma-vara ~0.66-0.73,
# orelaterat ~0.1-0.27 -> 0.45 separerar väl.
_SEM_FLOOR = 0.45


# Cert-/kvalitetsmarkörer som inte är produktidentitet - droppas före embedding så de inte
# dominerar korta namn (annars matchar "Bryggkaffe Eko" mot "Pommes Frites Eko"). Smakord
# (Naturell/Vanilj/Jordgubb...) BEHÅLLS - de är identitet för paring.
_EMBED_DROP = {"eko", "ekologisk", "ekologiskt", "ekologiska", "krav", "fairtrade", "organic"}


def _clean_for_embed(name, brand=None):
    """Produktbeskrivande kärna för embedding: bort med procent, storlek, cert-markörer och
    märkesord; behåll resten (inkl. smak)."""
    t = re.sub(r"\d+[.,]?\d*\s*%", " ", (name or "").lower())
    t = re.sub(r"\b\d+[.,]?\d*\s*(g|kg|ml|cl|l|st|p|pack|x)\b", " ", t)
    t = re.sub(r"[^a-zåäö ]", " ", t)
    drop = set(_EMBED_DROP)
    if brand:
        drop |= {w for w in re.sub(r"[^a-zåäö ]", " ", brand.lower()).split() if len(w) > 1}
    toks = [w for w in t.split() if len(w) > 1 and w not in drop]
    return " ".join(toks) or (name or "").lower().strip()


def rank_candidates(src, cands):
    """Ranka paringskandidater. Semantisk namn-likhet (rensade namn-embeddings) + förpacknings-
    bonus; faller tillbaka på lexikal `score` om embeddings ej tillgängliga. Returnerar
    (rankad_lista, metod)."""
    sims = embeddings.name_cosines(
        _clean_for_embed(src.get("name"), src.get("brand")),
        [_clean_for_embed(c.get("name"), c.get("brand")) for c in cands],
    )
    if sims is None:  # embeddings ej tillgängliga -> lexikalt
        ranked = sorted(({**c, "score": score(src, c)} for c in cands),
                        key=lambda p: p["score"], reverse=True)
        return [p for p in ranked if p["score"] > 0], "lexical"
    scored = []
    for c, sem in zip(cands, sims):
        if sem >= _SEM_FLOOR:
            scored.append({**c, "score": round(sem + _package_bonus(src, c), 3)})
    scored.sort(key=lambda p: p["score"], reverse=True)
    return scored, "embeddings"


# ---- Hämta private-label-produkter ur offers (kollapsade per EAN) ----
def _is_private(brand, roots):
    b = (brand or "").lower()
    return bool(b) and any(b.startswith(r) for r in roots)


def _products(conn, brands, chain=None, q=None):
    """Distinkta private-label-produkter per EAN ur offers (samma EAN i flera kedjor
    blir en post med `chains`-lista). Axfood-EAN via ean_cache (offer_id = code)."""
    members = {m["ean"]: m["group_id"] for m in db.load_match_members()}
    out = {}
    for ch in config.CHAINS:
        roots = [r.lower() for r in brands.get(ch, [])]
        if not roots:
            continue
        if ch in _AXFOOD:
            rows = conn.execute(
                "SELECT o.name, o.brand, o.package, o.comparison_value, o.comparison_unit, "
                "o.category_raw, o.image, e.ean FROM offers o JOIN ean_cache e ON e.code=o.offer_id "
                "WHERE o.chain=? AND e.ean!=''", (ch,)
            ).fetchall()
            inline = False
        else:
            rows = conn.execute(
                "SELECT name, brand, package, comparison_value, comparison_unit, category_raw, image, eans "
                "FROM offers WHERE chain=?", (ch,)
            ).fetchall()
            inline = True
        for r in rows:
            if not _is_private(r["brand"], roots):
                continue
            if inline:
                ean = next((e for e in (normalize_ean(x) for x in json.loads(r["eans"] or "[]")) if e), None)
            else:
                ean = normalize_ean(r["ean"])
            if not ean:
                continue
            e = out.get(ean)
            if e is None:
                out[ean] = {
                    "ean": ean, "chains": [ch], "name": r["name"], "brand": r["brand"],
                    "package": r["package"], "comparison_value": r["comparison_value"],
                    "comparison_unit": r["comparison_unit"], "category": r["category_raw"],
                    "image": r["image"], "group_id": members.get(ean),
                }
            elif ch not in e["chains"]:
                e["chains"].append(ch)
    items = list(out.values())
    if chain:
        items = [p for p in items if chain in p["chains"]]
    if q:
        ql = q.lower()
        items = [p for p in items if ql in (p["name"] or "").lower()]
    return items


def _get_one(conn, brands, ean):
    for p in _products(conn, brands):
        if p["ean"] == ean:
            return p
    return None


# ---- Endpoints ----
@router.get("/brands")
async def get_brands():
    return {"chains": config.CHAINS, "brands": db.load_private_brands()}


@router.post("/brands")
async def add_brand(payload: dict = Body(...)):
    chain = (payload.get("chain") or "").strip()
    brand = (payload.get("brand") or "").strip()
    if chain not in config.CHAINS or not brand:
        return JSONResponse({"detail": "Ogiltig kedja eller brand."}, status_code=400)
    db.add_private_brand(chain, brand)
    return {"chain": chain, "brand": brand, "brands": db.load_private_brands()}


@router.delete("/brands/{chain}/{brand:path}")
async def del_brand(chain: str, brand: str):
    db.remove_private_brand(chain, brand)
    return {"removed": True, "brands": db.load_private_brands()}


@router.get("/private-products")
async def private_products(chain: str | None = None, q: str | None = None):
    conn = db.get_conn()
    try:
        items = _products(conn, db.load_private_brands(), chain=chain, q=q)
    finally:
        conn.close()
    items.sort(key=lambda p: (p["group_id"] is not None, (p["name"] or "").lower()))
    return {"count": len(items), "products": items}


@router.get("/match/suggestions")
async def suggestions(ean: str = Query(...), limit: int = 8):
    conn = db.get_conn()
    try:
        brands = db.load_private_brands()
        src = _get_one(conn, brands, normalize_ean(ean))
        if not src:
            return JSONResponse({"detail": "Produkten hittades inte."}, status_code=404)
        srcchains = set(src["chains"])
        # Kandidater i andra kedjor (delar ingen kedja med källan = annan private label).
        cand = [p for p in _products(conn, brands) if not (set(p["chains"]) & srcchains)]
    finally:
        conn.close()
    ranked, method = rank_candidates(src, cand)
    return {"source": src, "suggestions": ranked[:limit], "method": method}


@router.get("/matches")
async def list_matches():
    groups = {}
    for m in db.load_match_members():
        groups.setdefault(m["group_id"], []).append(m)
    out = [
        {"group_id": gid, "members": sorted(ms, key=lambda x: x["chain"])}
        for gid, ms in sorted(groups.items())
    ]
    return {"count": len(out), "groups": out}


@router.post("/matches")
async def create_match(payload: dict = Body(...)):
    clean, seen = [], set()
    for m in payload.get("members") or []:
        ean = normalize_ean(m.get("ean"))
        if m.get("chain") in config.CHAINS and ean and ean not in seen:
            seen.add(ean)
            clean.append({"chain": m["chain"], "ean": ean, "name": m.get("name"),
                          "brand": m.get("brand"), "package": m.get("package")})
    if len(clean) < 2:
        return JSONResponse({"detail": "Para ihop minst två produkter."}, status_code=400)
    gid = db.link_products(clean)
    return {"group_id": gid}


@router.delete("/matches/{group_id}")
async def delete_match(group_id: int):
    db.delete_match_group(group_id)
    return {"removed": True}


@router.delete("/matches/{chain}/{ean}")
async def unlink_match_member(chain: str, ean: str):
    db.unlink_member(chain, ean)
    return {"removed": True}
