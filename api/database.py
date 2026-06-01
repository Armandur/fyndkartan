import json
import re
import sqlite3

from .config import (
    BUILTIN_TAG_TYPES, DB_PATH, DEFAULT_CATEGORY_MAP, DEFAULT_PRIVATE_BRANDS, DEFAULT_TAG_TYPES,
    ORIGIN_COUNTRIES,
)
from .categories import category_for, category_from_detail, raw_key
from .tags import build_tag


def get_conn():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = get_conn()
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS stores (
            chain       TEXT NOT NULL,
            store_id    TEXT NOT NULL,
            name        TEXT,
            brand       TEXT,
            street      TEXT,
            postal_code TEXT,
            city        TEXT,
            lat         REAL,
            lng         REAL,
            phone       TEXT,
            email       TEXT,
            oh_today    TEXT,
            open_now    INTEGER,
            link_store  TEXT,
            link_offers TEXT,
            link_online TEXT,
            tags        TEXT,
            raw         TEXT,
            native      TEXT,
            method      TEXT,
            fetched_at  TEXT,
            PRIMARY KEY (chain, store_id)
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_stores_chain ON stores(chain)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_stores_city ON stores(city)")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS offers (
            chain            TEXT NOT NULL,
            store_id         TEXT NOT NULL,
            offer_id         TEXT NOT NULL,
            name             TEXT,
            brand            TEXT,
            package          TEXT,
            price            REAL,
            price_text       TEXT,
            comparison_price TEXT,
            comparison_value REAL,
            comparison_unit  TEXT,
            category_raw     TEXT,
            category_id      INTEGER,
            mechanic_type    TEXT,
            valid_to         TEXT,
            eans             TEXT,
            image            TEXT,
            member_price     INTEGER,
            savings          REAL,
            fetched_at       TEXT,
            PRIMARY KEY (chain, store_id, offer_id)
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_offers_store ON offers(chain, store_id)")
    # Axfood code -> EAN, butiksoberoende och persistent (överlever offers-refresh).
    # ean = "" markerar "resolvad, ingen EAN" så vi slipper hämta om.
    conn.execute(
        "CREATE TABLE IF NOT EXISTS ean_cache (code TEXT PRIMARY KEY, ean TEXT, fetched_at TEXT)"
    )
    # Editerbar mappning råetikett -> lista av kanoniska typer (JSON, admin-override).
    _cols = {r[1] for r in conn.execute("PRAGMA table_info(tag_map)")}
    if _cols and "types" not in _cols:  # migrera bort gammalt enkel-typ-schema
        conn.execute("DROP TABLE tag_map")
    conn.execute("CREATE TABLE IF NOT EXISTS tag_map (label TEXT PRIMARY KEY, types TEXT)")
    # Kategori-mappning (chain_key, raw_key) -> kanonisk; seedas första gången.
    conn.execute(
        "CREATE TABLE IF NOT EXISTS category_map (chain_key TEXT, raw_key TEXT, canonical TEXT, "
        "PRIMARY KEY (chain_key, raw_key))"
    )
    # Alltid INSERT OR IGNORE -> nya seed-nycklar (t.ex. coop_nav) läggs till vid
    # uppgradering utan att skriva över admin-ändringar.
    conn.executemany(
        "INSERT OR IGNORE INTO category_map (chain_key, raw_key, canonical) VALUES (?,?,?)",
        [(ck, rk, canon) for (ck, rk), canon in DEFAULT_CATEGORY_MAP.items()],
    )
    # Editerbar kanonisk vokabulär; seedas med default-listan första gången.
    conn.execute("CREATE TABLE IF NOT EXISTS tag_types (type TEXT PRIMARY KEY)")
    # Tombstone: typer användaren tagit bort. Hindrar att inbyggda återskapas vid omstart.
    conn.execute("CREATE TABLE IF NOT EXISTS tag_types_removed (type TEXT PRIMARY KEY)")
    if not conn.execute("SELECT 1 FROM tag_types LIMIT 1").fetchone():
        conn.executemany("INSERT INTO tag_types (type) VALUES (?)", [(t,) for t in DEFAULT_TAG_TYPES])
    # Säkerställ att inbyggda (seed-producerade) typer finns - utom de användaren tagit bort.
    _removed = {r[0] for r in conn.execute("SELECT type FROM tag_types_removed")}
    conn.executemany(
        "INSERT OR IGNORE INTO tag_types (type) VALUES (?)",
        [(t,) for t in BUILTIN_TAG_TYPES if t not in _removed],
    )
    # Konton + favoriter + nyckel/värde-settings.
    conn.execute("CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)")
    conn.execute(
        """CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            is_admin INTEGER DEFAULT 0,
            created_at TEXT
        )"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS favorites (
            user_id INTEGER NOT NULL,
            chain TEXT NOT NULL,
            store_id TEXT NOT NULL,
            PRIMARY KEY (user_id, chain, store_id)
        )"""
    )
    # Admin-/konsolkonton är helt skilda från app-konton (users): egen tabell,
    # egen session (admin_uid). En app-användare har aldrig admin-behörighet.
    conn.execute(
        """CREATE TABLE IF NOT EXISTS admin_users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            created_at TEXT
        )"""
    )
    # Editerbar private-label-vokabulär (brand-rötter per kedja); seedas en gång.
    conn.execute("CREATE TABLE IF NOT EXISTS private_brands (chain TEXT, brand TEXT, PRIMARY KEY (chain, brand))")
    if not conn.execute("SELECT 1 FROM private_brands LIMIT 1").fetchone():
        conn.executemany(
            "INSERT OR IGNORE INTO private_brands (chain, brand) VALUES (?,?)",
            [(ch, b) for ch, bs in DEFAULT_PRIVATE_BRANDS.items() for b in bs],
        )
    # Manuell cross-chain-paring av märkesvaror. EAN-nycklad (stabil, överlever att
    # offers uppdateras). Snapshot av namn/brand/pkg så posten kan visas även när
    # erbjudandet försvunnit. En (chain, ean) tillhör som mest en grupp.
    conn.execute(
        """CREATE TABLE IF NOT EXISTS product_matches (
            group_id INTEGER NOT NULL,
            chain TEXT NOT NULL,
            ean TEXT NOT NULL,
            name TEXT, brand TEXT, package TEXT,
            PRIMARY KEY (chain, ean)
        )"""
    )
    # Produktinfo per EAN (EAN-global: ingredienser/näring/ursprung), lazy-cachad.
    # Källan står i datan (`source`). Regenererbar -> gamla per-kedje-cachen släpps.
    conn.execute("DROP TABLE IF EXISTS product_details")
    conn.execute(
        "CREATE TABLE IF NOT EXISTS product_info (ean TEXT PRIMARY KEY, data TEXT, fetched_at TEXT)"
    )
    # Lokalt cachade produktbilder per (ean, storlek) (bytes på disk, metadata här) -
    # CDN-oberoende + snabbare. Migrera bort gammalt ean-PK-schema (cache regenererbar).
    _icols = {r[1] for r in conn.execute("PRAGMA table_info(product_images)")}
    if _icols and "size" not in _icols:
        conn.execute("DROP TABLE product_images")
    conn.execute(
        "CREATE TABLE IF NOT EXISTS product_images (ean TEXT, size TEXT, content_type TEXT, "
        "source_url TEXT, fetched_at TEXT, PRIMARY KEY (ean, size))"
    )
    # Opaka bearer-tokens för slutanvändare (icke-webb-klienter). Lagras hashade.
    conn.execute(
        """CREATE TABLE IF NOT EXISTS user_tokens (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            token_hash TEXT UNIQUE NOT NULL,
            user_id INTEGER NOT NULL,
            label TEXT, created_at TEXT, last_used TEXT
        )"""
    )
    # API-nycklar för externa integratörer (utfärdas i konsolen). Lagras hashade;
    # validering är valfri (gatar inte de öppna läs-endpoints) - ogiltig nyckel nekas dock.
    conn.execute(
        """CREATE TABLE IF NOT EXISTS api_keys (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            key_hash TEXT UNIQUE NOT NULL,
            prefix TEXT, label TEXT, created_at TEXT,
            revoked INTEGER DEFAULT 0, last_used TEXT
        )"""
    )
    # ALTER TABLE-guards för nya kolumner (ingen Alembic).
    _ensure_column(conn, "offers", "member_price", "INTEGER")
    _ensure_column(conn, "offers", "savings", "REAL")
    _ensure_column(conn, "ean_cache", "category", "TEXT")  # Axfood googleAnalyticsCategory (förvärmd)
    _ensure_column(conn, "stores", "hours", "TEXT")  # JSON {week, exceptions} - normaliserad veckoöppettid
    conn.commit()
    conn.close()


def _ensure_column(conn, table, col, coltype):
    cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
    if col not in cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {coltype}")


def load_tag_map():
    conn = get_conn()
    rows = conn.execute("SELECT label, types FROM tag_map").fetchall()
    conn.close()
    return {r["label"]: json.loads(r["types"]) for r in rows}


def set_tag_map(label, types):
    conn = get_conn()
    conn.execute(
        "INSERT OR REPLACE INTO tag_map (label, types) VALUES (?,?)",
        (label, json.dumps(list(types), ensure_ascii=False)),
    )
    conn.commit()
    conn.close()


def delete_tag_map(label):
    conn = get_conn()
    conn.execute("DELETE FROM tag_map WHERE label=?", (label,))
    conn.commit()
    conn.close()


def category_label_counts():
    """Distinkta (chain_key, raw_key) ur offers + förvärmd ean_cache, med antal och
    nuvarande kanonisk mappning. För admin-fliken. Omappade först."""
    conn = get_conn()
    counts = {}
    for r in conn.execute(
        "SELECT chain, category_raw, COUNT(*) c FROM offers "
        "WHERE category_raw IS NOT NULL AND category_raw != '' GROUP BY chain, category_raw"
    ):
        ck, rk = raw_key(r["chain"], r["category_raw"])
        if rk:
            counts[(ck, rk)] = counts.get((ck, rk), 0) + r["c"]
    for r in conn.execute(
        "SELECT category, COUNT(*) c FROM ean_cache WHERE category IS NOT NULL AND category != '' GROUP BY category"
    ):
        rk = r["category"].split("|")[0]
        counts[("axfood", rk)] = counts.get(("axfood", rk), 0) + r["c"]
    mapping = {
        (r["chain_key"], r["raw_key"]): r["canonical"]
        for r in conn.execute("SELECT chain_key, raw_key, canonical FROM category_map")
    }
    conn.close()
    for k in mapping:
        counts.setdefault(k, 0)
    items = [
        {"chain_key": ck, "raw_key": rk, "count": n, "canonical": mapping.get((ck, rk))}
        for (ck, rk), n in counts.items()
    ]
    items.sort(key=lambda x: (x["canonical"] is not None, -x["count"]))
    return items


def load_category_map():
    conn = get_conn()
    rows = conn.execute("SELECT chain_key, raw_key, canonical FROM category_map").fetchall()
    conn.close()
    return {(r["chain_key"], r["raw_key"]): r["canonical"] for r in rows}


def set_category_map(chain_key, raw_key, canonical):
    conn = get_conn()
    conn.execute(
        "INSERT OR REPLACE INTO category_map (chain_key, raw_key, canonical) VALUES (?,?,?)",
        (chain_key, raw_key, canonical),
    )
    conn.commit()
    conn.close()


def delete_category_map(chain_key, raw_key):
    conn = get_conn()
    conn.execute("DELETE FROM category_map WHERE chain_key=? AND raw_key=?", (chain_key, raw_key))
    conn.commit()
    conn.close()


def load_tag_types():
    conn = get_conn()
    rows = conn.execute("SELECT type FROM tag_types ORDER BY rowid").fetchall()
    conn.close()
    return [r["type"] for r in rows]


def add_tag_type(type_):
    conn = get_conn()
    conn.execute("INSERT OR IGNORE INTO tag_types (type) VALUES (?)", (type_,))
    conn.execute("DELETE FROM tag_types_removed WHERE type=?", (type_,))  # un-tombstone vid återskapande
    conn.commit()
    conn.close()


def remove_tag_type(type_):
    conn = get_conn()
    conn.execute("DELETE FROM tag_types WHERE type=?", (type_,))
    conn.execute("INSERT OR IGNORE INTO tag_types_removed (type) VALUES (?)", (type_,))  # överlever omstart
    conn.commit()
    conn.close()


def tag_type_in_use(type_):
    """True om någon tag_map-rad använder typen."""
    conn = get_conn()
    rows = conn.execute("SELECT types FROM tag_map").fetchall()
    conn.close()
    return any(type_ in json.loads(r["types"]) for r in rows)


def tag_label_counts():
    """Distinkta råetiketter över alla butikers tags: antal butiker + vilka kedjor."""
    conn = get_conn()
    rows = conn.execute(
        "SELECT chain, tags FROM stores WHERE tags IS NOT NULL AND tags != '[]'"
    ).fetchall()
    conn.close()
    out = {}
    for r in rows:
        for t in json.loads(r["tags"]):
            lbl = t.get("label")
            if not lbl:
                continue
            e = out.setdefault(lbl, {"count": 0, "chains": set()})
            e["count"] += 1
            e["chains"].add(r["chain"])
    return out


def get_cached_eans(codes):
    codes = list(codes)
    if not codes:
        return {}
    conn = get_conn()
    rows = conn.execute(
        f"SELECT code, ean FROM ean_cache WHERE code IN ({','.join('?' * len(codes))})", codes
    ).fetchall()
    conn.close()
    return {r["code"]: r["ean"] for r in rows}


def save_eans(mapping):
    if not mapping:
        return
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    conn = get_conn()
    conn.executemany(
        "INSERT OR REPLACE INTO ean_cache (code, ean, fetched_at) VALUES (?,?,?)",
        [(c, e or "", now) for c, e in mapping.items()],
    )
    conn.commit()
    conn.close()


def save_ean_meta(mapping):
    """Förvärm code -> {ean, category} (Axfood /p/{code}). category = googleAnalyticsCategory."""
    if not mapping:
        return
    conn = get_conn()
    conn.executemany(
        "INSERT OR REPLACE INTO ean_cache (code, ean, category, fetched_at) VALUES (?,?,?,?)",
        [(c, m.get("ean") or "", m.get("category") or None, _now()) for c, m in mapping.items()],
    )
    conn.commit()
    conn.close()


def codes_missing_category(codes):
    """Vilka av koderna saknar category i ean_cache (ej warmade än)."""
    codes = list(codes)
    if not codes:
        return []
    conn = get_conn()
    rows = conn.execute(
        f"SELECT code FROM ean_cache WHERE code IN ({','.join('?' * len(codes))}) "
        f"AND category IS NOT NULL AND category != ''",
        codes,
    ).fetchall()
    conn.close()
    have = {r["code"] for r in rows}
    return [c for c in codes if c not in have]


def get_axfood_categories(codes):
    """{code: category_raw} ur ean_cache för Axfood-koder (förvärmd kategori)."""
    codes = list(codes)
    if not codes:
        return {}
    conn = get_conn()
    rows = conn.execute(
        f"SELECT code, category FROM ean_cache WHERE category IS NOT NULL AND category != '' "
        f"AND code IN ({','.join('?' * len(codes))})",
        codes,
    ).fetchall()
    conn.close()
    return {r["code"]: r["category"] for r in rows}


def coop_offer_eans():
    """Distinkta EAN ur Coop-erbjudanden (för kategori-förvärmning av product_info)."""
    conn = get_conn()
    rows = conn.execute("SELECT DISTINCT eans FROM offers WHERE chain='coop' AND eans NOT IN ('','[]')").fetchall()
    conn.close()
    out = set()
    for r in rows:
        try:
            out.update(json.loads(r["eans"]))
        except (json.JSONDecodeError, TypeError):
            pass
    return [e for e in out if e]


_OFFER_COLS = (
    "chain,store_id,offer_id,name,brand,package,price,price_text,comparison_price,"
    "comparison_value,comparison_unit,category_raw,category_id,mechanic_type,valid_to,"
    "eans,image,member_price,savings,fetched_at"
)
_OFFER_PH = ",".join(f":{c}" for c in _OFFER_COLS.split(","))


def replace_store_offers(chain, store_id, offers):
    """Ersätt en butiks erbjudanden transaktionellt. `eans` serialiseras till JSON."""
    rows = []
    for o in offers:
        r = dict(o)
        r["eans"] = json.dumps(o.get("eans") or [], ensure_ascii=False)
        rows.append(r)
    conn = get_conn()
    try:
        conn.execute("DELETE FROM offers WHERE chain=? AND store_id=?", (chain, str(store_id)))
        if rows:
            conn.executemany(
                f"INSERT OR REPLACE INTO offers ({_OFFER_COLS}) VALUES ({_OFFER_PH})", rows
            )
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
        d["package_size"], d["package_value"], d["package_unit"], d["package_approx"] = _clean_package(d.get("package"))
        d["brand"], d["origin"] = _split_brand_origin(chain, d.get("brand"))
        out.append(d)
    # Axfood: fyll saknad offer-kategori (särskilt Willys) från förvärmad ean_cache
    # (googleAnalyticsCategory per code). category_for hanterar pipe-pathens första segment.
    if chain in ("willys", "hemkop"):
        axc = get_axfood_categories([o["offer_id"] for o in out if not o.get("category_raw")])
        for o in out:
            if not o.get("category_raw") and axc.get(o["offer_id"]):
                o["category"] = category_for(chain, axc[o["offer_id"]])
    # Berika: föredra produktdetalj-kategori per EAN där den finns (rikast; cross-chain).
    # Axfood-offers har ean via ean_cache (offer_id).
    code_eans = get_cached_eans([o["offer_id"] for o in out if not o["eans"]])
    for o in out:
        o["_ean"] = o["eans"][0] if o["eans"] else code_eans.get(o["offer_id"])
    pc = get_product_categories([o["_ean"] for o in out if o.get("_ean")])
    for o in out:
        if o.get("_ean") and pc.get(o["_ean"]):
            o["category"] = pc[o["_ean"]]
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
        [r["offer_id"] for r in reps.values() if r["chain"] in ("willys", "hemkop") and not r.get("category_raw")]
    )
    pc = get_product_categories([g["ean"] for g in groups.values() if g["ean"]])
    out = []
    for key, g in groups.items():
        rep = g["offs"][0]
        ch = rep["chain"]
        cat = category_for(ch, rep.get("category_raw"))
        if ch in ("willys", "hemkop") and not rep.get("category_raw") and axc.get(rep["offer_id"]):
            cat = category_for(ch, axc[rep["offer_id"]])
        if g["ean"] and pc.get(g["ean"]):
            cat = pc[g["ean"]]
        brand, origin = _split_brand_origin(ch, rep.get("brand"))
        psize, pval, punit, _ = _clean_package(rep.get("package"))
        dt, mb = _deal_type(rep.get("price_text"))
        prices = [o["price"] for o in g["offs"] if o.get("price") is not None]
        out.append({
            "ean": g["ean"],
            "name": rep.get("name"),
            "brand": brand,
            "origin": origin,
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


def _to_row(s):
    loc = s.get("location") or {}
    addr = s.get("address") or {}
    oh = s.get("opening_hours") or {}
    links = s.get("links") or {}
    contact = s.get("contact") or {}
    open_now = oh.get("open_now")
    raw = oh.get("raw")
    hours = {"week": oh.get("week"), "exceptions": oh.get("exceptions")}
    hours = hours if (hours["week"] or hours["exceptions"]) else None
    return {
        "chain": s["chain"],
        "store_id": str(s["store_id"]),
        "name": s.get("name"),
        "brand": s.get("brand"),
        "street": addr.get("street"),
        "postal_code": addr.get("postal_code"),
        "city": addr.get("city"),
        "lat": loc.get("lat") if s.get("location") else None,
        "lng": loc.get("lng") if s.get("location") else None,
        "phone": contact.get("phone"),
        "email": contact.get("email"),
        "oh_today": oh.get("today"),
        "open_now": None if open_now is None else int(bool(open_now)),
        "link_store": links.get("store_page"),
        "link_offers": links.get("offers"),
        "link_online": links.get("online_shopping"),
        "tags": json.dumps(s.get("tags") or [], ensure_ascii=False),
        "raw": json.dumps(raw, ensure_ascii=False) if raw is not None else None,
        "hours": json.dumps(hours, ensure_ascii=False) if hours is not None else None,
        "native": json.dumps(s.get("native"), ensure_ascii=False) if s.get("native") else None,
        "method": (s.get("source") or {}).get("method"),
        "fetched_at": (s.get("source") or {}).get("fetched_at"),
    }


_COLS = (
    "chain,store_id,name,brand,street,postal_code,city,lat,lng,phone,email,"
    "oh_today,open_now,link_store,link_offers,link_online,tags,raw,hours,native,method,fetched_at"
)
_PLACEHOLDERS = ",".join(f":{c}" for c in _COLS.split(","))


def replace_chain(chain, stores):
    """Ersätt hela en kedjas bestånd transaktionellt."""
    rows = [_to_row(s) for s in stores]
    conn = get_conn()
    try:
        conn.execute("DELETE FROM stores WHERE chain=?", (chain,))
        if rows:
            conn.executemany(
                f"INSERT OR REPLACE INTO stores ({_COLS}) VALUES ({_PLACEHOLDERS})", rows
            )
        conn.commit()
    finally:
        conn.close()


def row_to_store(r):
    return {
        "chain": r["chain"],
        "store_id": r["store_id"],
        "name": r["name"],
        "brand": r["brand"],
        "address": {"street": r["street"], "postal_code": r["postal_code"], "city": r["city"]},
        "location": {"lat": r["lat"], "lng": r["lng"]} if r["lat"] is not None else None,
        "contact": {"phone": r["phone"], "email": r["email"]},
        "opening_hours": {
            "today": r["oh_today"],
            "open_now": None if r["open_now"] is None else bool(r["open_now"]),
            "week": (json.loads(r["hours"]).get("week") if r["hours"] else None),
            "exceptions": (json.loads(r["hours"]).get("exceptions") if r["hours"] else None),
            "raw": json.loads(r["raw"]) if r["raw"] else None,
        },
        "links": {
            "store_page": r["link_store"],
            "offers": r["link_offers"],
            "online_shopping": r["link_online"],
        },
        # Typerna härleds vid läsning (label = sanning), så admin-mappningen slår igenom direkt.
        "tags": [build_tag(t.get("label")) for t in (json.loads(r["tags"]) if r["tags"] else [])],
        "native": json.loads(r["native"]) if r["native"] else None,
        "source": {"method": r["method"], "fetched_at": r["fetched_at"]},
    }


# ---- Settings / konton / favoriter ----
def get_or_create_setting(key, default_factory):
    """Läs ett settings-värde, skapa det (persistent) om det saknas. Självständig
    (skapar tabellen) så den kan köras vid import innan init_db()."""
    conn = get_conn()
    conn.execute("CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)")
    row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    if row:
        conn.close()
        return row["value"]
    value = default_factory()
    conn.execute("INSERT OR IGNORE INTO settings (key, value) VALUES (?,?)", (key, value))
    conn.commit()
    row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    conn.close()
    return row["value"]


def create_user(email, password_hash):
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    conn = get_conn()
    cur = conn.execute(
        "INSERT INTO users (email, password_hash, created_at) VALUES (?,?,?)",
        (email, password_hash, now),
    )
    conn.commit()
    uid = cur.lastrowid
    conn.close()
    return uid


def update_password(user_id, password_hash):
    conn = get_conn()
    conn.execute("UPDATE users SET password_hash=? WHERE id=?", (password_hash, user_id))
    conn.commit()
    conn.close()


# ---- Admin-/konsolkonton (skilda från app-konton) ----
def create_admin(email, password_hash):
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    conn = get_conn()
    cur = conn.execute(
        "INSERT INTO admin_users (email, password_hash, created_at) VALUES (?,?,?)",
        (email, password_hash, now),
    )
    conn.commit()
    aid = cur.lastrowid
    conn.close()
    return aid


def get_admin_by_email(email):
    conn = get_conn()
    row = conn.execute("SELECT * FROM admin_users WHERE email=?", (email,)).fetchone()
    conn.close()
    return dict(row) if row else None


def get_admin_by_id(aid):
    conn = get_conn()
    row = conn.execute("SELECT * FROM admin_users WHERE id=?", (aid,)).fetchone()
    conn.close()
    return dict(row) if row else None


def update_admin_password(aid, password_hash):
    conn = get_conn()
    conn.execute("UPDATE admin_users SET password_hash=? WHERE id=?", (password_hash, aid))
    conn.commit()
    conn.close()


# ---- Private-label-vokabulär + märkesvaru-paring ----
def load_private_brands():
    conn = get_conn()
    rows = conn.execute("SELECT chain, brand FROM private_brands ORDER BY chain, brand").fetchall()
    conn.close()
    out = {}
    for r in rows:
        out.setdefault(r["chain"], []).append(r["brand"])
    return out


def add_private_brand(chain, brand):
    conn = get_conn()
    conn.execute("INSERT OR IGNORE INTO private_brands (chain, brand) VALUES (?,?)", (chain, brand))
    conn.commit()
    conn.close()


def remove_private_brand(chain, brand):
    conn = get_conn()
    conn.execute("DELETE FROM private_brands WHERE chain=? AND brand=?", (chain, brand))
    conn.commit()
    conn.close()


def load_match_members():
    """Alla parade medlemmar som lista av dict (för admin-vy + compare-map)."""
    conn = get_conn()
    rows = conn.execute(
        "SELECT group_id, chain, ean, name, brand, package FROM product_matches"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def group_for(chain, ean):
    conn = get_conn()
    row = conn.execute(
        "SELECT group_id FROM product_matches WHERE chain=? AND ean=?", (chain, str(ean))
    ).fetchone()
    conn.close()
    return row["group_id"] if row else None


def link_products(members):
    """Knyt ihop medlemmar ({chain, ean, name, brand, package}) till en grupp. Om
    någon redan tillhör en grupp återanvänds det group_id, annars skapas ett nytt."""
    conn = get_conn()
    try:
        gid = None
        for m in members:
            row = conn.execute(
                "SELECT group_id FROM product_matches WHERE chain=? AND ean=?",
                (m["chain"], str(m["ean"])),
            ).fetchone()
            if row:
                gid = row["group_id"]
                break
        if gid is None:
            gid = conn.execute("SELECT COALESCE(MAX(group_id), 0) + 1 AS g FROM product_matches").fetchone()["g"]
        conn.executemany(
            "INSERT OR REPLACE INTO product_matches (group_id, chain, ean, name, brand, package) VALUES (?,?,?,?,?,?)",
            [(gid, m["chain"], str(m["ean"]), m.get("name"), m.get("brand"), m.get("package")) for m in members],
        )
        conn.commit()
    finally:
        conn.close()
    return gid


def unlink_member(chain, ean):
    conn = get_conn()
    conn.execute("DELETE FROM product_matches WHERE chain=? AND ean=?", (chain, str(ean)))
    conn.commit()
    conn.close()


def delete_match_group(group_id):
    conn = get_conn()
    conn.execute("DELETE FROM product_matches WHERE group_id=?", (group_id,))
    conn.commit()
    conn.close()


def get_product_info(ean):
    conn = get_conn()
    row = conn.execute("SELECT data FROM product_info WHERE ean=?", (str(ean),)).fetchone()
    conn.close()
    return json.loads(row["data"]) if row else None


def get_product_categories(eans):
    """{ean: kanonisk kategori} ur produktdetalj-cachen (rikare än offer-nivån).
    Resolverar category_raw+source -> kanonisk; bara de som mappar."""
    eans = [str(e) for e in eans if e]
    if not eans:
        return {}
    conn = get_conn()
    rows = conn.execute(
        f"SELECT ean, json_extract(data,'$.category_raw') AS raw, "
        f"json_extract(data,'$.category_source') AS src FROM product_info "
        f"WHERE ean IN ({','.join('?' * len(eans))})",
        eans,
    ).fetchall()
    conn.close()
    out = {}
    for r in rows:
        canon = category_from_detail(r["src"], r["raw"]) if r["raw"] else None
        if canon:
            out[r["ean"]] = canon
    return out


def save_product_info(ean, data):
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    conn = get_conn()
    conn.execute(
        "INSERT OR REPLACE INTO product_info (ean, data, fetched_at) VALUES (?,?,?)",
        (str(ean), json.dumps(data, ensure_ascii=False), now),
    )
    conn.commit()
    conn.close()


def _now():
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---- Produktbilds-cache (metadata; bytes ligger på disk) ----
def get_image_meta(ean, size):
    conn = get_conn()
    row = conn.execute(
        "SELECT content_type, source_url FROM product_images WHERE ean=? AND size=?", (str(ean), size)
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def save_image_meta(ean, size, content_type, source_url):
    conn = get_conn()
    conn.execute(
        "INSERT OR REPLACE INTO product_images (ean, size, content_type, source_url, fetched_at) VALUES (?,?,?,?,?)",
        (str(ean), size, content_type, source_url, _now()),
    )
    conn.commit()
    conn.close()


# ---- Slutanvändar-tokens (opaka bearer, för icke-webb-klienter) ----
def create_user_token(user_id, token_hash, label):
    conn = get_conn()
    cur = conn.execute(
        "INSERT INTO user_tokens (token_hash, user_id, label, created_at) VALUES (?,?,?,?)",
        (token_hash, user_id, label, _now()),
    )
    conn.commit()
    tid = cur.lastrowid
    conn.close()
    return tid


def user_id_for_token(token_hash):
    conn = get_conn()
    row = conn.execute("SELECT user_id FROM user_tokens WHERE token_hash=?", (token_hash,)).fetchone()
    if row:
        conn.execute("UPDATE user_tokens SET last_used=? WHERE token_hash=?", (_now(), token_hash))
        conn.commit()
    conn.close()
    return row["user_id"] if row else None


def list_user_tokens(user_id):
    conn = get_conn()
    rows = conn.execute(
        "SELECT id, label, created_at, last_used FROM user_tokens WHERE user_id=? ORDER BY id DESC",
        (user_id,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def revoke_user_token(user_id, token_id):
    conn = get_conn()
    conn.execute("DELETE FROM user_tokens WHERE id=? AND user_id=?", (token_id, user_id))
    conn.commit()
    conn.close()


# ---- API-nycklar (externa integratörer, konsol-utfärdade) ----
def create_api_key(key_hash, prefix, label):
    conn = get_conn()
    cur = conn.execute(
        "INSERT INTO api_keys (key_hash, prefix, label, created_at) VALUES (?,?,?,?)",
        (key_hash, prefix, label, _now()),
    )
    conn.commit()
    kid = cur.lastrowid
    conn.close()
    return kid


def list_api_keys():
    conn = get_conn()
    rows = conn.execute(
        "SELECT id, prefix, label, created_at, revoked, last_used FROM api_keys ORDER BY id DESC"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def api_key_active(key_hash):
    """Returnera nyckelraden om giltig (ej återkallad) + uppdatera last_used, annars None."""
    conn = get_conn()
    row = conn.execute(
        "SELECT id, label, revoked FROM api_keys WHERE key_hash=?", (key_hash,)
    ).fetchone()
    if not row or row["revoked"]:
        conn.close()
        return None
    conn.execute("UPDATE api_keys SET last_used=? WHERE key_hash=?", (_now(), key_hash))
    conn.commit()
    conn.close()
    return {"id": row["id"], "label": row["label"]}


def revoke_api_key(key_id):
    conn = get_conn()
    conn.execute("UPDATE api_keys SET revoked=1 WHERE id=?", (key_id,))
    conn.commit()
    conn.close()


def get_user_by_email(email):
    conn = get_conn()
    row = conn.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
    conn.close()
    return dict(row) if row else None


def get_user_by_id(uid):
    conn = get_conn()
    row = conn.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
    conn.close()
    return dict(row) if row else None


def list_favorites(user_id):
    conn = get_conn()
    rows = conn.execute(
        "SELECT chain, store_id FROM favorites WHERE user_id=?", (user_id,)
    ).fetchall()
    conn.close()
    return [f"{r['chain']}:{r['store_id']}" for r in rows]


def add_favorite(user_id, chain, store_id):
    conn = get_conn()
    conn.execute(
        "INSERT OR IGNORE INTO favorites (user_id, chain, store_id) VALUES (?,?,?)",
        (user_id, chain, str(store_id)),
    )
    conn.commit()
    conn.close()


def remove_favorite(user_id, chain, store_id):
    conn = get_conn()
    conn.execute(
        "DELETE FROM favorites WHERE user_id=? AND chain=? AND store_id=?",
        (user_id, chain, str(store_id)),
    )
    conn.commit()
    conn.close()
