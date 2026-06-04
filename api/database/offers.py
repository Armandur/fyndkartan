import json
import re

from ._conn import _now, get_conn
from .ean import get_axfood_categories, get_axfood_origins, get_cached_eans
from .products import get_product_categories
from ..categories import category_for, category_from_detail, category_from_name, raw_key
from ..config import AXFOOD_CHAINS, ORIGIN_COUNTRIES
from .. import countries
from ..matching import _norm_unit


_OFFER_COLS = (
    "chain,store_id,offer_id,name,brand,package,price,price_text,comparison_price,"
    "comparison_value,comparison_unit,category_raw,category_id,mechanic_type,valid_to,"
    "eans,image,member_price,savings,fetched_at"
)
_OFFER_PH = ",".join(f":{c}" for c in _OFFER_COLS.split(","))


def archive_offers(chain, store_id, offers):
    """Prishistorik: skriv en observation per offer NÄR (price, comparison_value, savings,
    valid_to) ändrats sedan senaste observationen för (chain, store_id, offer_id). `savings`
    låter ordinarie pris (≈ price + savings för flat) spåras. Append-only, deduppat -> upprepade
    synkar med oförändrade priser ger inga nya rader."""
    if not offers:
        return
    # Axfood-offers bär ingen inline-EAN (eans=[]) -> resolva code->EAN ur ean_cache så
    # observationen blir EAN-nyckad (annars går prishistoriken inte att slå upp på EAN för
    # Willys/Hemköp). Koder som ännu inte warmats fångas i stället read-time i price_history.
    code_eans = (get_cached_eans([str(o.get("offer_id")) for o in offers])
                 if chain in AXFOOD_CHAINS else {})
    conn = get_conn()
    try:
        latest = {
            r["offer_id"]: (r["price"], r["comparison_value"], r["savings"], r["valid_to"])
            for r in conn.execute(
                "SELECT offer_id, price, comparison_value, savings, valid_to FROM offer_observations "
                "WHERE chain=? AND store_id=? AND id IN (SELECT MAX(id) FROM offer_observations "
                "WHERE chain=? AND store_id=? GROUP BY offer_id)",
                (chain, str(store_id), chain, str(store_id)),
            )
        }
        now = _now()
        rows = []
        for o in offers:
            oid = str(o.get("offer_id"))
            cur = (o.get("price"), o.get("comparison_value"), o.get("savings"), o.get("valid_to"))
            if latest.get(oid) == cur:
                continue
            eans = o.get("eans") or []
            ean = eans[0] if eans else (code_eans.get(oid) or None)
            rows.append((chain, str(store_id), oid, ean, o.get("name"),
                         o.get("price"), o.get("comparison_value"), o.get("comparison_unit"),
                         o.get("savings"), o.get("member_price"), o.get("valid_to"), now))
        if rows:
            conn.executemany(
                "INSERT INTO offer_observations (chain, store_id, offer_id, ean, name, price, "
                "comparison_value, comparison_unit, savings, member_price, valid_to, observed_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                rows,
            )
            conn.commit()
    finally:
        conn.close()


def offer_observations_stats():
    """(antal rader, distinkta produkter, äldsta observation) för prishistorik-tabellen."""
    conn = get_conn()
    r = conn.execute(
        "SELECT COUNT(*) c, COUNT(DISTINCT chain||store_id||offer_id) p, MIN(observed_at) o "
        "FROM offer_observations"
    ).fetchone()
    conn.close()
    return {"rows": r["c"], "products": r["p"], "since": r["o"]}


def price_history(ean):
    """Prishistorik för en EAN ur `offer_observations`, grupperad per kedja och kollapsad på
    på varandra följande lika prisnivå (butiker med samma pris/period vid samma synk -> EN punkt,
    `stores` räknar dem). Varje punkt: pris + jämförpris + medlemspris-flagga + `valid_to`
    (när erbjudandet går ut, för stegfunktion/gap-rendering klient-sida). Tidsordnat per kedja.

    Axfood-observationer (Willys/Hemköp) saknar inline-EAN och nyckas på Axfood-koden (offer_id);
    vi reverse-resolvar därför koderna för denna EAN ur ean_cache och tar med dem - så historiken
    blir komplett även för Axfood (inkl. äldre rader arkiverade innan koden warmades)."""
    conn = get_conn()
    codes = [r["code"] for r in conn.execute("SELECT code FROM ean_cache WHERE ean=?", (ean,)).fetchall()]
    where, params = "ean=?", [ean]
    if codes:
        where += f" OR (chain IN ('willys','hemkop') AND offer_id IN ({','.join('?' * len(codes))}))"
        params.extend(codes)
    rows = conn.execute(
        "SELECT chain, store_id, name, price, comparison_value, comparison_unit, member_price, "
        f"valid_to, observed_at FROM offer_observations WHERE {where} "
        "ORDER BY chain, observed_at, store_id",
        params,
    ).fetchall()
    conn.close()
    name = None
    by_chain = {}
    for r in rows:
        name = name or r["name"]
        by_chain.setdefault(r["chain"], []).append(r)
    out = []
    for chain, obs in by_chain.items():
        pts = []
        for o in obs:
            p = o["price"]
            last = pts[-1] if pts else None
            same = (last and last["valid_to"] == o["valid_to"]
                    and ((last["price"] is None and p is None)
                         or (last["price"] is not None and p is not None
                             and abs(last["price"] - p) < 0.005)))
            if same:
                last["stores"] += 1
                last["member_price"] = last["member_price"] or bool(o["member_price"])
                continue
            pts.append({
                "observed_at": o["observed_at"], "price": p,
                "comparison_value": o["comparison_value"], "comparison_unit": o["comparison_unit"],
                "member_price": bool(o["member_price"]), "valid_to": o["valid_to"], "stores": 1,
            })
        out.append({"chain": chain, "points": pts})
    return {"ean": ean, "name": name, "chains": out}


def stores_with_offer(ean):
    """Butiker (chain, store_id) som just nu har ett erbjudande på EAN:en, med billigaste
    erbjudandet per butik (pris/jämförpris/valid_to/medlemspris). Slår upp i det normaliserade
    `offer_eans`-indexet (inline + Axfood redan resolvat). OBS: bara butiker med ERBJUDANDE -
    inte hyllsortiment."""
    conn = get_conn()
    rows = conn.execute(
        "SELECT o.chain, o.store_id, o.name, o.price, o.price_text, o.package, o.savings, "
        "o.comparison_value, o.comparison_unit, o.valid_to, o.member_price FROM offer_eans oe "
        "JOIN offers o ON oe.chain=o.chain AND oe.store_id=o.store_id AND oe.offer_id=o.offer_id "
        "WHERE oe.ean=?",
        (ean,),
    ).fetchall()
    conn.close()
    best = {}
    for r in rows:
        key = (r["chain"], r["store_id"])
        cur = best.get(key)
        if cur is None or (r["price"] is not None and (cur["price"] is None or r["price"] < cur["price"])):
            dt, mq = _deal_type(r["price_text"])
            best[key] = {"chain": r["chain"], "store_id": r["store_id"], "name": r["name"],
                         "price": r["price"], "price_text": r["price_text"], "savings": r["savings"],
                         "package": normalized_package(r["package"]), "deal_type": dt, "multibuy_qty": mq,
                         "comparison_value": r["comparison_value"], "comparison_unit": _norm_unit(r["comparison_unit"]),
                         "valid_to": r["valid_to"], "member_price": bool(r["member_price"])}
    return list(best.values())


def on_offer_eans():
    """Mängd EAN som har minst ett aktuellt erbjudande (DISTINCT ur normaliserade `offer_eans`,
    JOIN offers). Samma 'on offer'-definition som `offers_for_eans`/`_enrich_with_offers` (inkl.
    Axfood redan resolvat) -> låter katalog-bläddringen filtrera 'bara erbjudanden' server-sida
    utan IN-vargräns."""
    conn = get_conn()
    rows = conn.execute(
        "SELECT DISTINCT oe.ean FROM offer_eans oe "
        "JOIN offers o ON oe.chain=o.chain AND oe.store_id=o.store_id AND oe.offer_id=o.offer_id"
    ).fetchall()
    conn.close()
    return {r["ean"] for r in rows}


def eans_on_offer_at_stores(pairs):
    """Mängd EAN som har ett aktuellt erbjudande hos NÅGON av butikerna (lista (chain, store_id)).
    Exakt per (kedja, butik) - inte cross-produkt. För 'på rea hos mina favoriter'-filtret."""
    pairs = [(c, str(s)) for c, s in pairs if c and s is not None]
    if not pairs:
        return set()
    out = set()
    conn = get_conn()
    for i in range(0, len(pairs), 400):  # chunka (2 params per par -> < ~999)
        chunk = pairs[i:i + 400]
        where = " OR ".join("(chain=? AND store_id=?)" for _ in chunk)
        params = [x for pr in chunk for x in pr]
        for r in conn.execute(f"SELECT DISTINCT ean FROM offer_eans WHERE {where}", params):
            out.add(r["ean"])
    conn.close()
    return out


def offers_for_eans(eans):
    """Bästa (lägsta) aktuella erbjudandepris per (EAN, kedja) ur offers-cachen, för en lista EAN.
    {ean: {chain: {price, comparison_value, comparison_unit, valid_to, member_price}}}. Slår upp i
    det normaliserade `offer_eans`-indexet (inline + Axfood redan resolvat). Används för att
    överlagra aktuella erbjudanden på katalog-sökets nationella hyllpriser."""
    eans = list({e for e in eans if e})
    if not eans:
        return {}
    out = {}
    conn = get_conn()
    for i in range(0, len(eans), 900):  # chunka -> klarar hela kategorier (SQLite-vargräns ~999)
        chunk = eans[i:i + 900]
        ph = ",".join("?" * len(chunk))
        for r in conn.execute(
            f"SELECT oe.ean, o.chain, o.price, o.price_text, o.comparison_value, o.comparison_unit, "
            f"o.valid_to, o.member_price FROM offer_eans oe "
            f"JOIN offers o ON oe.chain=o.chain AND oe.store_id=o.store_id AND oe.offer_id=o.offer_id "
            f"WHERE oe.ean IN ({ph})",
            chunk,
        ):
            slot = out.setdefault(r["ean"], {})
            cur = slot.get(r["chain"])
            if cur is None or (r["price"] is not None and (cur["price"] is None or r["price"] < cur["price"])):
                dt, mq = _deal_type(r["price_text"])
                slot[r["chain"]] = {"price": r["price"], "price_text": r["price_text"], "deal_type": dt,
                                    "multibuy_qty": mq, "comparison_value": r["comparison_value"],
                                    "comparison_unit": _norm_unit(r["comparison_unit"]), "valid_to": r["valid_to"],
                                    "member_price": bool(r["member_price"])}
    conn.close()
    return out


def replace_store_offers(chain, store_id, offers):
    """Ersätt en butiks erbjudanden transaktionellt. `eans` serialiseras till JSON.
    Arkiverar prisförändringar (prishistorik) innan replace."""
    archive_offers(chain, store_id, offers)
    rows = []
    for o in offers:
        r = dict(o)
        r["eans"] = json.dumps(o.get("eans") or [], ensure_ascii=False)
        rows.append(r)
    # offer_eans-index: inline-EAN + Axfood-kod resolvat ur ean_cache (NU; ev. ej-warmade koder
    # fylls vid nästa replace när ean_cache hunnit fyllas - självläkande över sweepar).
    code_eans = (get_cached_eans([str(o.get("offer_id")) for o in offers])
                 if chain in AXFOOD_CHAINS else {})
    oe_rows = []
    for o in offers:
        oid = str(o.get("offer_id"))
        eans = o.get("eans") or []
        if not eans and code_eans.get(oid):
            eans = [code_eans[oid]]
        for e in eans:
            if e:
                oe_rows.append((chain, str(store_id), oid, e))
    conn = get_conn()
    try:
        conn.execute("DELETE FROM offers WHERE chain=? AND store_id=?", (chain, str(store_id)))
        conn.execute("DELETE FROM offer_eans WHERE chain=? AND store_id=?", (chain, str(store_id)))
        if rows:
            conn.executemany(
                f"INSERT OR REPLACE INTO offers ({_OFFER_COLS}) VALUES ({_OFFER_PH})", rows
            )
        if oe_rows:
            conn.executemany("INSERT OR IGNORE INTO offer_eans VALUES (?,?,?,?)", oe_rows)
        conn.commit()
    finally:
        conn.close()


# Deal-typen ligger i price_text, INTE i mechanic_type (som är opålitlig och kedje-
# specifik: ICA "Standard" blandar platt+multibuy, Axfood "MixMatch" är platt pris osv).
_MB_BUY_PAY = re.compile(r"k[öo]p\s*(\d+)\s*betala", re.I)   # "Köp 3 betala för 2"
_MB_N_FOR = re.compile(r"\b(\d+)\s*f[öo]r\b", re.I)          # "3 för 95 kr"
_BY_WEIGHT = re.compile(r"/\s*(kg|hg|g|l|liter)\b", re.I)    # "74,90 kr/kg"


def _deal_type(price_text):
    """Normaliserad deal-typ + ev. multibuy-antal, härledd ur price_text."""
    t = price_text or ""
    m = _MB_BUY_PAY.search(t)
    if m:
        return "multibuy", int(m.group(1))
    m = _MB_N_FOR.search(t)
    if m:
        return "multibuy", int(m.group(1))
    if _BY_WEIGHT.search(t):
        return "by_weight", None
    return "flat", None


# package skrivs olika: Axfood "BRAND, [ca: ]storlek", Coop ordenheter ("900 Gram"),
# ICA ren storlek med ranges/multipack ("350-500 g", "12 x 33 cl"). Normalisera till
# en ren storlekssträng + (value, unit) för det enkla "N enhet"-fallet + approx-flagga.
_PKG_SIMPLE = re.compile(r"\s*(\d+(?:[.,]\d+)?)\s*(kg|hg|g|l|dl|cl|ml|st|p|pack)\s*", re.I)
_PKG_WORD = ((re.compile(r"\bGram\b", re.I), "g"), (re.compile(r"\bMilliliter\b", re.I), "ml"),
             (re.compile(r"\bST\b"), "st"))


def _clean_package(pkg):
    """(storlekssträng, value, unit, approx) ur ett rått package-fält."""
    s = (pkg or "").strip()
    if not s:
        return None, None, None, False
    # Axfood-brandprefix: text före ', ' som inte börjar med siffra (ICA:s komma-separerade
    # storlekar börjar med siffra och ska behållas).
    if ", " in s:
        head, _, tail = s.partition(", ")
        if head and not head[0].isdigit():
            s = tail.strip()
    approx = bool(re.match(r"ca[:\s]", s, re.I))
    s = re.sub(r"^ca[:\s]+", "", s, flags=re.I).strip()
    for rx, repl in _PKG_WORD:
        s = rx.sub(repl, s)
    s = s.strip()
    value, unit = None, None
    m = _PKG_SIMPLE.fullmatch(s)
    if m:
        value = float(m.group(1).replace(",", "."))
        unit = m.group(2).lower()
        if unit == "pack":
            unit = "p"
    return s or None, value, unit, approx


# Förpackningsenhet -> (jämför-basenhet, faktor till basen). För härlett jämförpris.
_PKG_TO_BASE = {
    "g": ("kg", 0.001), "kg": ("kg", 1.0), "hg": ("kg", 0.1),
    "ml": ("l", 0.001), "cl": ("l", 0.01), "dl": ("l", 0.1), "l": ("l", 1.0),
    "st": ("st", 1.0), "p": ("st", 1.0),
}


def derived_comparison(price, value, unit):
    """(jämförvärde, basenhet) ur pris/storlek (basenhet kg/l/st), annars (None, None).
    UNGEFÄRLIGT - använd bara som fallback för flat-pris när kedjans jämförpris saknas; kedjan
    räknar ofta på avrunnen vikt/faktiskt innehåll, så detta kan skilja 10-30%."""
    conv = _PKG_TO_BASE.get((unit or "").lower())
    if not (price and value and conv) or value <= 0:
        return None, None
    base, fac = conv
    size = value * fac
    return (round(price / size, 2), base) if size > 0 else (None, None)


def normalized_package(pkg):
    """Ren, skal-normaliserad förpacknings-sträng för visning: brand-prefix bort, ordenheter
    -> symbol (`_clean_package`), och ml->l / g->kg när det blir ett helt tal ('1000 Milliliter'
    -> '1 l', 'ELDORADO, 1l' -> '1 l'). Range/multipack (utan enkel value+unit) lämnas städad."""
    s, value, unit, approx = _clean_package(pkg)
    if value is not None and unit in ("ml", "g") and value >= 1000 and value % 1000 == 0:
        value, unit = value / 1000, {"ml": "l", "g": "kg"}[unit]
    if value is not None and unit:
        num = f"{value:g}".replace(".", ",")  # svensk decimal: 1.5 -> 1,5
        return ("ca " if approx else "") + f"{num} {_norm_unit(unit) or unit}"
    return s


def norm_origin(items):
    """Title-case en lista ursprungsländer för visning ('SVERIGE' -> 'Sverige'). Delad
    visnings-normalisering för offers OCH katalog (samma svenska CLDR-landnamn, olika versalisering)."""
    return [str(x).strip().title() for x in (items or []) if str(x).strip()] or None


def _origin_list(s):
    return [t.strip() for t in s.split("/") if t.strip()] or None


def _split_brand_origin(chain, brand):
    """Dela offers.brand i (brand, origin-lista). ICA: 'BRAND. [Ursprung] LAND' (brand först,
    landet validerat mot ORIGIN_COUNTRIES så 'Dr. Oetker' inte splittas fel). Coop:
    'LAND/.../BRAND' (ledande land-tokens = ursprung, resten varumärke). Axfood: redan rent.
    origin blir en lista av länder (`Spanien/Marocko` -> `['Spanien','Marocko']`) eller None."""
    s = (brand or "").strip()
    if not s:
        return None, None
    if chain == "ica":
        if s.lower().startswith("ursprung "):
            return None, _origin_list(s[9:])
        if "." in s:
            left, _, right = s.partition(".")
            right = re.sub(r"^\s*ursprung\s+", "", right.strip(), flags=re.I).strip()
            if right and right.split("/")[0].strip().lower() in ORIGIN_COUNTRIES:
                return (left.strip() or None), _origin_list(right)
            return s, None
        # Bart ursprung utan brand: flera länder slash-separerat ("Colombia/Peru/Sydafrika").
        toks = [t.strip() for t in s.split("/")]
        if len(toks) > 1 and all(t.lower() in ORIGIN_COUNTRIES for t in toks):
            return None, _origin_list(s)
        return s, None
    if chain == "coop" and "/" in s:
        parts = [p.strip() for p in s.split("/")]
        i = 0
        while i < len(parts) and parts[i].lower() in ORIGIN_COUNTRIES:
            i += 1
        return ("/".join(parts[i:]) or None), (parts[:i] or None)
    return s, None


def get_store_offers(chain, store_id):
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM offers WHERE chain=? AND store_id=? ORDER BY category_raw, name",
        (chain, str(store_id)),
    ).fetchall()
    conn.close()
    out = []
    for r in rows:
        d = dict(r)
        d["eans"] = json.loads(d["eans"]) if d["eans"] else []
        d["category"] = category_for(chain, d.get("category_raw"))  # offer-nivå (fallback)
        d["deal_type"], d["multibuy_qty"] = _deal_type(d.get("price_text"))
        _, d["package_value"], d["package_unit"], d["package_approx"] = _clean_package(d.get("package"))
        d["package_size"] = normalized_package(d.get("package"))  # generell visnings-normalisering
        b, orig = _split_brand_origin(chain, d.get("brand"))
        d["brand"], d["origin"] = b, norm_origin(orig)
        d["origin_codes"] = countries.codes_for(d["origin"])
        d["comparison_unit"] = _norm_unit(d.get("comparison_unit"))
        d["comparison_derived"] = False
        # Härlett jämförpris (UNGEFÄRLIGT): fyll bara när kedjans saknas, dealen är flat och
        # storleken är parsbar. Markeras så UI/compare vet att det är en uppskattning.
        if d.get("comparison_value") is None and d["deal_type"] == "flat":
            dv, du = derived_comparison(d.get("price"), d["package_value"], d["package_unit"])
            if dv is not None:
                d["comparison_value"], d["comparison_unit"], d["comparison_derived"] = dv, du, True
        out.append(d)
    # Axfood: fyll saknad offer-kategori (särskilt Willys) från förvärmad ean_cache
    # (googleAnalyticsCategory per code). category_for hanterar pipe-pathens första segment.
    if chain in AXFOOD_CHAINS:
        axc = get_axfood_categories([o["offer_id"] for o in out if not o.get("category_raw")])
        for o in out:
            if not o.get("category_raw") and axc.get(o["offer_id"]):
                o["category"] = category_for(chain, axc[o["offer_id"]])
    # Berika: föredra produktdetalj-kategori per EAN där den finns (rikast; cross-chain).
    # Axfood-offers har ean via ean_cache (offer_id).
    code_eans = get_cached_eans([o["offer_id"] for o in out if not o["eans"]])
    for o in out:
        if not o["eans"] and code_eans.get(o["offer_id"]):
            o["eans"] = [code_eans[o["offer_id"]]]  # Axfood: surfa resolvad EAN -> bild + "Visa info"
        o["_ean"] = o["eans"][0] if o["eans"] else None
    pc = get_product_categories([o["_ean"] for o in out if o.get("_ean")])
    for o in out:
        if o.get("_ean") and pc.get(o["_ean"]):
            o["category"] = pc[o["_ean"]]
        if o.get("category") == "ovrigt":
            o["category"] = category_from_name(o.get("name")) or "ovrigt"
        o.pop("_ean", None)
    return out


def list_products(q=None, category=None, chain=None, limit=40):
    """Distinkta produkter ur cachade erbjudanden, grupperade på EAN (cross-chain) -
    annars (kedja, namn) när EAN saknas. Filtrerbart på namn (`q`), kanonisk `category`
    och `chain`. Per produkt: representativ normaliserad metadata, kedjor, prisintervall
    och antal erbjudanden. Namnmatchning i Python (Unicode-skiftlägesokänsligt; SQLite
    LOWER fäller bara ASCII)."""
    ql = (q or "").strip().lower()
    if q is not None and len(ql) < 2:
        return []
    conn = get_conn()
    sql, params = "SELECT * FROM offers", []
    if chain:
        sql += " WHERE chain=?"
        params.append(chain)
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    hits = [dict(r) for r in rows if not ql or ql in (r["name"] or "").lower()]
    if not hits:
        return []
    # EAN-resolution: inline-array, annars ean_cache (Axfood code->EAN).
    code_eans = get_cached_eans([h["offer_id"] for h in hits if not h["eans"]])
    groups = {}
    for h in hits:
        eans = json.loads(h["eans"]) if h["eans"] else []
        ean = eans[0] if eans else code_eans.get(h["offer_id"])
        key = ean or f"{h['chain']}:{(h['name'] or '').lower()}"
        g = groups.setdefault(key, {"ean": ean, "chains": set(), "offs": []})
        g["chains"].add(h["chain"])
        g["offs"].append(h)
    # Kategori-berikning som get_store_offers (offer-nivå + Axfood ean_cache + product_info).
    reps = {k: g["offs"][0] for k, g in groups.items()}
    axc = get_axfood_categories(
        [r["offer_id"] for r in reps.values() if r["chain"] in AXFOOD_CHAINS and not r.get("category_raw")]
    )
    pc = get_product_categories([g["ean"] for g in groups.values() if g["ean"]])
    out = []
    for key, g in groups.items():
        rep = g["offs"][0]
        ch = rep["chain"]
        cat = category_for(ch, rep.get("category_raw"))
        if ch in AXFOOD_CHAINS and not rep.get("category_raw") and axc.get(rep["offer_id"]):
            cat = category_for(ch, axc[rep["offer_id"]])
        if g["ean"] and pc.get(g["ean"]):
            cat = pc[g["ean"]]
        if cat == "ovrigt":
            cat = category_from_name(rep.get("name")) or "ovrigt"
        brand, origin = _split_brand_origin(ch, rep.get("brand"))
        origin = norm_origin(origin)
        _, pval, punit, _ = _clean_package(rep.get("package"))
        psize = normalized_package(rep.get("package"))
        dt, mb = _deal_type(rep.get("price_text"))
        prices = [o["price"] for o in g["offs"] if o.get("price") is not None]
        out.append({
            "ean": g["ean"],
            "name": rep.get("name"),
            "brand": brand,
            "origin": origin,
            "origin_codes": countries.codes_for(origin),
            "image": rep.get("image"),
            "category": cat,
            "package_size": psize,
            "package_value": pval,
            "package_unit": punit,
            "deal_type": dt,
            "multibuy_qty": mb,
            "chains": sorted(g["chains"]),
            "offer_count": len(g["offs"]),
            "price_min": min(prices) if prices else None,
            "price_max": max(prices) if prices else None,
        })
    if category:
        out = [p for p in out if p["category"] == category]
    # Namnsök: prefix-träff först. Bläddring (utan q): flest kedjor/erbjudanden, sen namn.
    out.sort(key=lambda p: (
        bool(ql) and not (p["name"] or "").lower().startswith(ql),
        -len(p["chains"]), -p["offer_count"], (p["name"] or "").lower(),
    ))
    return out[:limit]


def offers_fetched_at(chain, store_id):
    """Senaste hämtningstidpunkt för en butiks erbjudanden, eller None."""
    conn = get_conn()
    row = conn.execute(
        "SELECT MAX(fetched_at) AS t FROM offers WHERE chain=? AND store_id=?",
        (chain, str(store_id)),
    ).fetchone()
    conn.close()
    return row["t"] if row else None


def ean_stats():
    """Distinkta EAN vi känner till, union över källorna: inline i offers (ICA/Coop/CG, via
    json_each), Axfood code->EAN-cachen, product_info och product_images. Plus delsiffror för
    Axfood-resolve-cachen och hur många som har hämtad produktinfo."""
    conn = get_conn()
    distinct = conn.execute(
        "SELECT COUNT(*) FROM ("
        "SELECT je.value AS ean FROM offers, json_each(offers.eans) je WHERE offers.eans NOT IN ('','[]') "
        "UNION SELECT ean FROM ean_cache WHERE ean!='' "
        "UNION SELECT ean FROM product_info WHERE ean IS NOT NULL "
        "UNION SELECT ean FROM product_images WHERE ean IS NOT NULL)"
    ).fetchone()[0]
    axfood = conn.execute("SELECT COUNT(*) FROM ean_cache WHERE ean!=''").fetchone()[0]
    with_info = conn.execute("SELECT COUNT(*) FROM product_info WHERE ean IS NOT NULL").fetchone()[0]
    conn.close()
    return {"distinct": distinct, "axfood_cache": axfood, "with_info": with_info}


def offers_coverage():
    """Per kedja: antal butiker med cachade erbjudanden + totalt antal cachade erbjudanden.
    Visar hur komplett offers-cachen är per kedja (det bulk-sweepen fyller)."""
    conn = get_conn()
    rows = conn.execute(
        "SELECT chain, COUNT(DISTINCT store_id) AS stores, COUNT(*) AS offers "
        "FROM offers GROUP BY chain"
    ).fetchall()
    conn.close()
    return {r["chain"]: {"stores_with_offers": r["stores"], "offers": r["offers"]} for r in rows}


def offer_stores(chains):
    """Butiker (chain, store_id, link_offers, native) för givna kedjor - för bulk-sweep av
    erbjudanden. Returnerar en dict {chain: [rader]} så sweepen kan köra kedjor parallellt."""
    qs = ",".join("?" for _ in chains)
    conn = get_conn()
    rows = conn.execute(
        f"SELECT chain, store_id, link_offers, native FROM stores WHERE chain IN ({qs}) "
        "ORDER BY chain, store_id",
        tuple(chains),
    ).fetchall()
    conn.close()
    out = {}
    for r in rows:
        out.setdefault(r["chain"], []).append(dict(r))
    return out
