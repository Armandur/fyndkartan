"""Lazy rik produktinfo per EAN (ingredienser, näring, ursprung, allergener),
normaliserad och sammanslagen över källor (`_merge`). Cachas EAN-nyckat i product_info.

Källor: Axfood (`/axfood/rest/p/{code}`, code via ean_cache; har näringsvärden) +
Coop personalization-API (EAN-DB; ingredienser/ursprung, täcker branded varor i alla
kedjor) + ICA (handla.ica.se SSR-produktdetalj, WAF-förbi med browser-headers; EAN->
consumerItemId via globalsearch). Coop hämtas när Axfood saknas/är ofullständig; ICA
för ICA:s egna märken (ICA-intern EAN som de andra saknar) + som sista fallback.
Allergener härleds ur ingredienslistan via vokabulär (`extract_allergens`).
"""

import html as _html
import json
import logging
import re

from sqlalchemy import text

from . import config, countries, database as db
from .adapters import ica_token, keys
from .adapters.axfood_offers import DOMAIN, UA

log = logging.getLogger("matbutiker")
_AXFOOD = ("willys", "hemkop")

# ICA-detaljsidan (handla.ica.se) är server-renderad men AWS-WAF-skyddad mot header-lösa
# anrop; ett riktigt browser-headerset (Sec-Fetch-*) släpps igenom. EAN->consumerItemId
# resolvas via ICA:s globalsearch (butiks-scopat, se database.ica_resolve_accounts).
ICA_SEARCH = "https://apimgw-pub.ica.se/sverige/digx/globalsearch/v1/search/quicksearch"
_ICA_BROWSER_HDRS = {
    "User-Agent": UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "sv-SE,sv;q=0.9",
    "Sec-Fetch-Dest": "document", "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none", "Sec-Fetch-User": "?1",
    "Upgrade-Insecure-Requests": "1",
}

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


# Guards mot kända falskpositiv: "mjölk" i växtbaserade sammansättningar (kokosmjölk,
# havremjölk ...) är inte mejeri-allergen; "ost" i "ostron" är blötdjur, inte mejeri.
_ALLERGEN_GUARDS = {
    "mjölk": re.compile(r"(?<!kokos)(?<!havre)(?<!soja)(?<!mandel)(?<!cashew)(?<!hampa)(?<!ris)(?<!ärt)mjölk"),
    "ost": re.compile(r"ost(?!ron)"),
}


def _allergen_hit(term, text):
    g = _ALLERGEN_GUARDS.get(term)
    return bool(g.search(text)) if g else term in text


def extract_allergens(ingredients):
    """Kanoniska allergener ur ingredienslistan via vokabulär-match (i kanonisk ordning)."""
    t = (ingredients or "").lower()
    if not t:
        return []
    return [name for name, terms in _ALLERGENS.items() if any(_allergen_hit(term, t) for term in terms)]


# Kost-klassificering ur ingredienser -> fristående api/diet.py (delas med bläddra-filtret).
from .diet import classify_diet  # noqa: E402


# Näringsdeklaration: kanonisk etikett-form + standardordning + enhetsförkortningar.
_NUT_ORDER = [
    "Energi", "Fett", "Varav mättat fett", "Varav enkelomättat fett", "Varav fleromättat fett",
    "Omega-3", "Övriga omega-3",
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
    # GS1/Axfood-koder som ibland läcker igenom rått (omega-3-fettsyror, t.ex. på fet fisk)
    "fan3": "Omega-3", "x_omega_3_other": "Övriga omega-3",
}
_NUT_UNIT = {"kilojoule": "kJ", "kilokalori": "kcal", "gram": "g", "milligram": "mg", "mikrogram": "µg"}


def _normalize_nutrition(nutrition):
    # API:t exponerar energi som två separata rader (kJ + kcal); app-visningen slår ihop dem.
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


# Axfood-märknings-koder (produktinfons `labels`) -> läsbar svensk etikett. `environmental_facet`
# är en vag intern facet och droppas; okända koder humaniseras ('_' -> mellanslag, versal).
_LABELS = {
    "keyhole": "Nyckelhålsmärkt",
    "ecological": "Ekologisk",
    "eu_ecological": "EU-ekologisk",
    "krav": "KRAV",
    "swedish_flag": "Svenskt",
    "from_sweden": "Från Sverige",
    "meat_from_sweden": "Kött från Sverige",
    "swedish_bird": "Svensk fågel",
    "rainforest_alliance": "Rainforest Alliance",
    "msc_fish": "MSC-märkt",
    "asc_fish": "ASC-märkt",
    "glutenfree": "Glutenfri",
    "laktosfree": "Laktosfri",
    "frozen": "Fryst",
}
_LABELS_DROP = {"environmental_facet"}


def _normalize_labels(labels):
    out = []
    for raw in labels or []:
        key = str(raw).strip()
        if not key or key.lower() in _LABELS_DROP:
            continue
        disp = _LABELS.get(key.lower(), key.replace("_", " ").capitalize())
        if disp not in out:
            out.append(disp)
    return out


def normalize_info(info):
    """Read-time-normalisering av produktinfo: kanonisk + ordnad näring, allergener och
    märkningar ur vokabulär. Idempotent. Täcker även gamla cachade rader (raw koder)."""
    if not info:
        return info
    info = dict(info)
    info.pop("partial", None)  # intern piggyback-flagga, exponeras inte i API:t
    info["nutrition"] = _normalize_nutrition(info.get("nutrition"))
    info["allergens"] = extract_allergens(info.get("ingredients"))
    info["diet"] = classify_diet(info.get("ingredients"))  # härledd vegan/vegetarian/none
    info["labels"] = _normalize_labels(info.get("labels"))
    # Ursprung: normalisera till svenskt CLDR-namn ("Sweden"->"Sverige") + ISO-koder (-> flaggor i
    # appen). Hanterar fleruländer ("Sverige, Norge"); okända delar (fiskeområden) lämnas utan kod.
    norm_origin, codes = countries.split_origins(info.get("origin"))
    info["origin"] = norm_origin
    info["origin_codes"] = codes
    return info


def _axfood_code(ean):
    conn = db.get_conn()
    row = conn.execute(text("SELECT code FROM ean_cache WHERE ean=:ean LIMIT 1"),
                       {"ean": str(ean)}).fetchone()
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
    from .adapters.axfood_offers import parse_axfood_detail
    return parse_axfood_detail(r.json(), chain)


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


def _micro(h, ip):
    """Värdet i `<meta itemprop="ip" content="...">` (HTML-avkodat)."""
    m = re.search(r'itemprop="%s"\s+content="([^"]*)"' % ip, h)
    return _html.unescape(m.group(1)) if m else None


def _ica_section(h, heading):
    """De-taggad text efter `<h2>heading</h2>` fram till nästa `<h2>`. None om saknas/tom."""
    m = re.search(r"<h2[^>]*>\s*" + re.escape(heading) + r"\s*</h2>", h)
    if not m:
        return None
    seg = h[m.end():m.end() + 2000].split("<h2")[0]
    txt = _html.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", seg))).strip()
    return txt or None


_ICA_BASIS_UNIT = {"gram": "g", "milliliter": "ml", "millilitre": "ml"}


def _ica_nutrition(h):
    """Näringsdeklarationen -> ([{label,value,unit}], basis). ICA renderar två varianter:
    en `<table>` (rad = `<td>label</td><td>value unit</td>`) eller en kommaseparerad `<p>`
    ("Energi (kcal) 20 kcal, Fett 0.5 g, ..."). '(kcal)/(kJ)'-suffix strippas ur etiketten."""
    m = re.search(r"Näringsdeklaration.*?<table>(.*?)</table>", h, re.S)
    if m:
        table = m.group(1)
        # Rad-/cell-baserad parsning (tål blanksteg mellan taggar - ICA serverar både
        # minifierad och pretty-printad HTML; header-raden använder <th> -> 0 <td> -> hoppas).
        out = []
        for row in re.findall(r"<tr>(.*?)</tr>", table, re.S):
            cells = [re.sub(r"<[^>]+>", "", _html.unescape(td)).strip()
                     for td in re.findall(r"<td[^>]*>(.*?)</td>", row, re.S)]
            if len(cells) < 2 or not cells[0] or not cells[1]:
                continue
            label = re.sub(r"\s*\(.*?\)\s*$", "", cells[0])
            vu = re.match(r"\s*([\d.,]+)\s*(\S+)?", cells[1])
            if label and vu:
                out.append({"label": label, "value": vu.group(1), "unit": vu.group(2)})
        basis = None
        hrow = re.search(r"<tr>(.*?)</tr>", table, re.S)  # header: <th>Näringsvärde</th><th>100 Gram</th>
        ths = re.findall(r"<th[^>]*>(.*?)</th>", hrow.group(1), re.S) if hrow else []
        if len(ths) >= 2:
            bm = re.match(r"\s*([\d.,]+)\s*(\S+)?",
                          re.sub(r"<[^>]+>", "", _html.unescape(ths[1])).strip())
            if bm:
                u = (bm.group(2) or "").strip()
                basis = {"value": bm.group(1), "unit": _ICA_BASIS_UNIT.get(u.lower(), u) or None}
        return out, (basis if out else None)
    seg = _ica_section(h, "Näringsdeklaration")  # kommaseparerad variant (basis okänd -> None)
    if not seg:
        return [], None
    out = []
    for part in seg.split(","):
        pm = re.match(r"\s*(.+?)\s+([\d.,]+)\s+(\S+)\s*$", part)
        if pm:
            label = re.sub(r"\s*\(.*?\)\s*$", "", pm.group(1).strip())
            out.append({"label": label, "value": pm.group(2), "unit": pm.group(3)})
    return out, None


# Inline ursprungsmarkörer i ingredienslistan (egna märken saknar Ursprungsland-sektionen):
# "*Ursprung Sverige", "*Ursprung: Sverige" (kolon), "*Odlade i Italien", "Producerad i ...".
# Markörordet skiftlägesokänsligt (?i:...); landet måste börja versalt (fångar ett ord).
_ICA_ORIGIN_RX = re.compile(
    r"\*?\s*\b(?i:ursprungsland|ursprung|"
    r"(?:odla|producera|tillverka|framställ|fånga|fiska|skörda)\w*\s+i\b)"
    r"\s*:?\s*([A-ZÅÄÖ][\wåäöÅÄÖ-]+(?:\s*/\s*[A-ZÅÄÖ][\wåäöÅÄÖ-]+)*)"  # tål "Polen/Litauen"
)


def _ica_origin(h, ingredients):
    """Ursprungsland-sektionen; för egna märken ligger ursprunget inline i ingredienserna."""
    o = _ica_section(h, "Ursprungsland")
    if o:
        return o
    if ingredients:
        m = _ICA_ORIGIN_RX.search(ingredients)
        if m:
            return m.group(1)
    return None


def _ica_image(h):
    """Produktbildens URL (og:image) - en resizebar cloudinary `/image/upload/`-URL."""
    m = re.search(r'<meta\s+property="og:image"\s+content="([^"]+)"', h)
    return _html.unescape(m.group(1)) if m else None


def _parse_ica_detail(h):
    """handla.ica.se produktdetalj (SSR-microdata + sektioner) -> normaliserad info-del."""
    ingredients = _ica_section(h, "Ingredienser")
    if ingredients:
        ingredients = re.sub(r"^INGREDIENSER:\s*", "", ingredients, flags=re.I).strip() or None
    nutrition, basis = _ica_nutrition(h)
    desc = _micro(h, "description")
    if desc:
        desc = re.sub(r"\s+", " ", _html.unescape(re.sub(r"<[^>]+>", " ", desc))).strip()
        desc = re.sub(r"^Information från leverantör\s*", "", desc).strip() or None
    if not (ingredients or desc or nutrition):
        return None
    cats = _micro(h, "categories")
    try:
        cats = json.loads(cats) if cats else []
    except (ValueError, TypeError):
        cats = []
    return {
        "description": desc,
        "ingredients": ingredients,
        "origin": _ica_origin(h, ingredients),
        "province": None,
        "storage": _ica_section(h, "Förvaring"),
        "nutrition": nutrition,
        "nutrition_basis": basis,
        "labels": [],
        "source": "ica",
        "category_raw": cats[0] if cats else None,
        "image": _ica_image(h),
    }


async def _resolve_ica_cid(client, ean):
    """EAN(13) -> ICA consumerItemId via globalsearch (butiks-scopat, provar flera profiler).
    Cachas; cid='' = försökt utan träff. None vid fel (cachas ej -> nytt försök senare)."""
    cached = db.get_ica_cid(ean)
    if cached is not None:
        return cached or None
    target = str(ean).lstrip("0")
    pad = str(ean).zfill(14)  # söket kräver 14-siffrig gtin
    try:
        token = await ica_token.get_token(client)
    except Exception as ex:  # noqa: BLE001
        log.warning("ICA detalj: token-hämtning misslyckades: %s", ex)
        return None
    cid = None
    for acct in db.ica_resolve_accounts():
        try:
            r = await client.post(
                ICA_SEARCH,
                json={"queryString": pad, "take": 5, "offset": 0,
                      "accountNumber": acct, "searchDomain": "All", "sessionId": "x"},
                headers={"User-Agent": UA, "Authorization": f"Bearer {token}",
                         "Content-Type": "application/json"},
                timeout=15,
            )
        except Exception:  # noqa: BLE001
            continue
        if r.status_code != 200:
            continue
        docs = (r.json().get("products") or {}).get("documents") or []
        m = next((d for d in docs if str(d.get("gtin") or "").lstrip("0") == target), None)
        if m and m.get("consumerItemId"):
            cid = str(m["consumerItemId"])
            break
    db.save_ica_cid(ean, cid or "")
    return cid


async def _fetch_ica(client, ean):
    """ICA produktdetalj (handla.ica.se SSR, WAF-förbi med browser-headers). Täcker ICA:s
    egna märken (ICA-intern EAN) som Coop/Axfood saknar. EAN->cid resolvas + cachas."""
    cid = await _resolve_ica_cid(client, ean)
    if not cid:
        return None
    try:
        r = await client.get(f"https://handla.ica.se/produkt/{cid}",
                             headers=_ICA_BROWSER_HDRS, timeout=15)
    except Exception as ex:  # noqa: BLE001
        log.warning("ICA detalj %s (cid %s) misslyckades: %s", ean, cid, ex)
        return None
    return _parse_ica_detail(r.text) if r.status_code == 200 else None


async def fetch_ica_only(client, ean):
    """Bara ICA-källan, normaliserad - för förvärmning av ICA:s egna märken (Coop/Axfood
    saknar dem ändå, så fetch_for_ean:s Coop-anrop vore bortkastat). None om inget."""
    ic = await _fetch_ica(client, ean)
    return _merge([ic]) if ic and (ic.get("ingredients") or ic.get("description")) else None


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
    imgs = [p.get("image") for p in parts if p.get("image")]
    merged["image"] = imgs[0] if imgs else None  # ICA-detaljbild (resizebar) -> bild-resolvern
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
    # ICA-detalj: enda källan för ICA:s egna märken (ICA-intern EAN, prefix 731869) som
    # Axfood/Coop saknar; fallback när de andra gett tomt; OCH när näringen fortfarande är
    # gles (Axfood ger ofta bara energi och Coop saknade varan) - ICA bär full näringsdeklaration.
    nut_best = max((len(p.get("nutrition") or []) for p in parts), default=0)
    if str(ean).lstrip("0").startswith("731869") or not parts or nut_best < 4:
        ic = await _fetch_ica(client, ean)
        if ic and (ic.get("ingredients") or ic.get("description")):
            parts.append(ic)
    if not parts:
        return None
    db.archive_product_info([(ean, p) for p in parts])  # innehållshistorik per källa (append-on-change)
    return _merge(parts)
