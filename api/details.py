"""Lazy rik produktinfo per EAN (ingredienser, näring, ursprung, allergener),
normaliserad och sammanslagen över källor (`_merge`). Cachas EAN-nyckat i product_info.

Källor: Axfood (`/axfood/rest/p/{code}`, code via ean_cache; har näringsvärden) +
Coop personalization-API (EAN-DB; ingredienser/ursprung, täcker branded varor i alla
kedjor inkl. ICA vars ehandel är bot-skyddad). Coop hämtas bara när Axfood saknas/är
ofullständig. Allergener plockas ur de VERSALA orden i ingredienslistan.
"""

import json
import logging
import re

from . import config, database as db
from .adapters import keys
from .adapters.axfood_offers import DOMAIN, UA

log = logging.getLogger("matbutiker")
_AXFOOD = ("willys", "hemkop")

_coop_key = None  # skrapad personalization-nyckel (cache), scrape-on-401

# Allergen-vokabulär: kanonisk allergen -> indikator-termer (matchas skiftlägesokänsligt
# som delsträng i ingredienslistan). Ersätter den gamla "alla VERSALA ord"-heuristiken som
# gav skräp (trunkeringar "LK"/"TTER", icke-allergener KRAV/BCAA, främmande språk). Kurerad
# lista över EU:s 14 allergengrupper; över-varnar hellre än missar (t.ex. växtdrycker med
# "mjölk" i namnet) - säker riktning för allergi.
_ALLERGENS = {
    "Gluten": ("vete", "råg", "havre", "spelt", "dinkel", "durum", "gluten", "mannagryn", "semolina", "couscous", "bulgur"),
    "Mjölk": ("mjölk", "grädde", "ost", "vassle", "laktos", "kasein", "kvarg", "kärnmjölk", "smörfett"),
    "Ägg": ("ägg", "albumin"),
    "Soja": ("soja", "soya", "tofu", "edamame"),
    "Jordnötter": ("jordnöt",),
    "Nötter": ("mandel", "hasselnöt", "valnöt", "cashew", "pekannöt", "paranöt", "pistasch", "pistage", "macadamia", "nötter"),
    "Fisk": ("fisk", "lax", "torsk", "sill", "tonfisk", "ansjovis", "makrill"),
    "Skaldjur": ("räk", "krabb", "hummer", "kräftdjur", "skaldjur", "languster", "scampi"),
    "Blötdjur": ("blötdjur", "mussl", "ostron", "bläckfisk", "snäck", "pilgrimsmuss"),
    "Sesam": ("sesam",),
    "Senap": ("senap",),
    "Selleri": ("selleri",),
    "Lupin": ("lupin",),
    "Sulfit": ("sulfit", "svaveldioxid"),
}


def extract_allergens(ingredients):
    """Kanoniska allergener ur ingredienslistan via vokabulär-match (i kanonisk ordning)."""
    t = (ingredients or "").lower()
    if not t:
        return []
    return [name for name, terms in _ALLERGENS.items() if any(term in t for term in terms)]


# Näringsdeklaration: kanonisk etikett-form + standardordning + enhetsförkortningar.
_NUT_ORDER = [
    "Energi", "Fett", "Varav mättat fett", "Varav enkelomättat fett", "Varav fleromättat fett",
    "Kolhydrat", "Varav sockerarter", "Varav polyoler", "Fiber", "Protein", "Salt",
    "Vitamin A", "Vitamin D", "Vitamin E", "Vitamin C", "Tiamin", "Riboflavin", "Niacin",
    "Vitamin B6", "Folsyra", "Vitamin B12", "Biotin", "Kalcium", "Kalium", "Natrium",
    "Magnesium", "Selen", "Jod",
]
_NUT_ORDER_IX = {n.lower(): i for i, n in enumerate(_NUT_ORDER)}
_NUT_CANON = {  # lowercased variant -> kanonisk (de flesta är redan kanoniska)
    "energi": "Energi",
    "kolhydrat": "Kolhydrat", "kolhydrater": "Kolhydrat",
    "varav socker": "Varav sockerarter", "varav sockerarter": "Varav sockerarter",
    "mättat fett": "Varav mättat fett",
    "fibrer": "Fiber", "kostfiber": "Fiber",
}
_NUT_UNIT = {"kilojoule": "kJ", "kilokalori": "kcal", "gram": "g", "milligram": "mg", "mikrogram": "µg"}


def _normalize_nutrition(nutrition):
    out = []
    for n in nutrition or []:
        lbl = (n.get("label") or "").strip()
        out.append({
            "label": _NUT_CANON.get(lbl.lower(), lbl),
            "value": n.get("value"),
            "unit": _NUT_UNIT.get((n.get("unit") or "").strip().lower(), n.get("unit")),
        })
    out.sort(key=lambda n: _NUT_ORDER_IX.get((n["label"] or "").lower(), 999))
    return out


def normalize_info(info):
    """Read-time-normalisering av produktinfo: kanonisk + ordnad näring och allergener ur
    vokabulär. Idempotent. Täcker även gamla cachade rader (raw label/value/unit)."""
    if not info:
        return info
    info = dict(info)
    info["nutrition"] = _normalize_nutrition(info.get("nutrition"))
    info["allergens"] = extract_allergens(info.get("ingredients"))
    return info


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
        "source": chain,
        "category_raw": (d.get("googleAnalyticsCategory") or "").strip() or None,
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


async def _coop_post(client, eans, key):
    return await client.post(
        "https://external.api.coop.se/personalization/search/entities/by-id",
        params={"api-version": "v1", "store": config.COOP_DETAIL_STORE,
                "groups": "CUSTOMER_PRIVATE", "direct": "false"},
        headers={
            "Ocp-Apim-Subscription-Key": key, "Content-Type": "application/json",
            "Origin": "https://www.coop.se", "Accept": "application/json", "User-Agent": UA,
        },
        content=json.dumps([str(e) for e in eans]), timeout=20,
    )


async def _coop_items(client, eans):
    """POST EAN-array -> produktentiteter (scrape-on-401). [] vid fel/tomt."""
    key = await _resolve_coop_key(client)
    r = await _coop_post(client, eans, key)
    if r.status_code in (401, 403):
        log.info("Coop detalj: %s, skrapar ny personalization-nyckel", r.status_code)
        key = await _resolve_coop_key(client, force=True)
        r = await _coop_post(client, eans, key)
    if r.status_code != 200:
        return []
    return ((r.json().get("results") or {}).get("items")) or []


def _parse_coop_item(p):
    """Coop personalization-entitet -> normaliserad info-del (en källa)."""
    s = lambda v: (v or "").strip() or None
    origin = ", ".join(x.get("value") for x in (p.get("countryOfOriginCodes") or []) if x.get("value")) or None
    storage = (p.get("consumerInstructions") or {}).get("storageInstructions")
    # Näringsvärdena ligger i nutrientLinks (description/amount/unit), inte nutrientInformation.
    nutrition = [
        {"label": n.get("description"), "value": (n.get("amount") or [None])[0], "unit": n.get("unit")}
        for n in (p.get("nutrientLinks") or []) if (n.get("amount") or [None])[0]
    ]
    basis_q = (p.get("nutrientBasis") or {}).get("quantity")
    # navCategories är en hierarki leaf -> topp; ta toppnamnet som kategori-nyckel.
    nav, cat_raw = p.get("navCategories") or [], None
    if nav:
        n = nav[0]
        while n.get("superCategories"):
            n = n["superCategories"][0]
        cat_raw = n.get("name")
    return {
        "description": s(p.get("description")),
        "ingredients": s(p.get("listOfIngredients")),
        "origin": origin,
        "province": None,
        "storage": s(storage),
        "nutrition": nutrition,
        "nutrition_basis": {"value": basis_q, "unit": "g"} if nutrition and basis_q else None,
        "labels": [],
        "source": "coop",
        "category_raw": cat_raw,
    }


async def _fetch_coop(client, ean):
    """Coop personalization-API: POST med EAN-array -> produktentitet med ingredienser,
    ursprung och förvaring. Produktdata är butiksoberoende (fast store-param)."""
    items = await _coop_items(client, [ean])
    return _parse_coop_item(items[0]) if items else None


async def fetch_coop_batch(client, eans):
    """Batch-hämta Coop-produktinfo för förvärmning: {ean: merged_info} (samma form som
    `fetch_for_ean` sparar). Tar bara med entiteter som har ingredienser, beskrivning
    eller kategori. Använder personalization-API:ts array-stöd (en POST per batch)."""
    out = {}
    for p in await _coop_items(client, eans):
        e = str(p.get("ean") or "")
        part = _parse_coop_item(p)
        if e and (part.get("ingredients") or part.get("description") or part.get("category_raw")):
            out[e] = _merge([part])
    return out


def _merge(parts):
    """Slå ihop produktinfo från flera källor per fält (näring från Axfood +
    ingredienser/ursprung från Coop osv). Textfält: längsta icke-tomma. `sources`
    listar bidragande källor. Allergener härleds ur (sammanslagna) ingredienserna."""
    merged = {"sources": [p["source"] for p in parts if p.get("source")]}
    for f in ("description", "ingredients", "origin", "province", "storage"):
        vals = [p.get(f) for p in parts if p.get(f)]
        merged[f] = max(vals, key=len) if vals else None
    best = max((p for p in parts if p.get("nutrition")), key=lambda p: len(p["nutrition"]), default=None)
    merged["nutrition"] = best["nutrition"] if best else []
    merged["nutrition_basis"] = best.get("nutrition_basis") if best else None
    labels = []
    for p in parts:
        for lbl in p.get("labels") or []:
            if lbl not in labels:
                labels.append(lbl)
    merged["labels"] = labels
    merged["allergens"] = extract_allergens(merged.get("ingredients"))
    # Kategori: föredra Axfood (pipe-path resolvas via befintlig mappning), annars Coop.
    catpart = (next((p for p in parts if p.get("source") in _AXFOOD and p.get("category_raw")), None)
               or next((p for p in parts if p.get("category_raw")), None))
    if catpart:
        merged["category_raw"] = catpart["category_raw"]
        merged["category_source"] = catpart["source"]
    return merged


async def fetch_for_ean(client, ean, prefer_chain=None):
    """Produktinfo för en EAN (EAN-global), normaliserad + sammanslagen över källor.
    Axfood-native (rikast, har näring) hämtas om EAN finns i ean_cache; Coop (EAN-DB,
    täcker branded varor i alla kedjor) hämtas om Axfood saknas/är ofullständig.
    Resultatet mergas fält-för-fält. `sources` anger bidragande källor."""
    ax_chain = prefer_chain if prefer_chain in _AXFOOD else "willys"
    ax = await _fetch_axfood(client, ax_chain, ean)  # no-op (ingen HTTP) om EAN ej i ean_cache
    # Hämta Coop när Axfood saknas, saknar ingredienser, eller har GLES näring (Axfood
    # ger ofta bara energi; Coops nutrientLinks är fylligare).
    need_more = not ax or not ax.get("ingredients") or len(ax.get("nutrition") or []) < 4
    co = await _fetch_coop(client, ean) if need_more else None
    parts = [p for p in (ax, co) if p and (p.get("ingredients") or p.get("description"))]
    if not parts:
        return None
    return _merge(parts)
