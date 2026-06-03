import json
import re
import sqlite3

from .config import (
    AXFOOD_CHAINS, BUILTIN_TAG_TYPES, DB_PATH, DEFAULT_CATEGORY_MAP, DEFAULT_PRIVATE_BRANDS,
    DEFAULT_PROVIDERS, DEFAULT_TAG_TYPES, ORIGIN_COUNTRIES,
)
from .categories import category_for, category_from_detail, category_from_name, raw_key
from .tags import build_tag


def get_conn():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=3000")  # vänta i st. f. att fela vid samtidig skrivning (apilog)
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
    # Prishistorik (steg 4): append-only observationer av offers. offers churnar vid varje synk
    # (replace), så historiken måste skrivas separat. En rad per offer NÄR priset/jämförpriset/
    # valid_to ändrats sedan senaste observationen (dedup) -> en kompakt prisförändrings-logg.
    conn.execute(
        """CREATE TABLE IF NOT EXISTS offer_observations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chain TEXT NOT NULL, store_id TEXT NOT NULL, offer_id TEXT NOT NULL,
            ean TEXT, name TEXT, price REAL, comparison_value REAL, comparison_unit TEXT,
            savings REAL, member_price INTEGER, valid_to TEXT, observed_at TEXT
        )"""
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_obs_offer ON offer_observations(chain, store_id, offer_id, id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_obs_ean ON offer_observations(ean)")
    # Axfood code -> EAN, butiksoberoende och persistent (överlever offers-refresh).
    # ean = "" markerar "resolvad, ingen EAN" så vi slipper hämta om.
    conn.execute(
        "CREATE TABLE IF NOT EXISTS ean_cache (code TEXT PRIMARY KEY, ean TEXT, fetched_at TEXT)"
    )
    # Normaliserat offer -> EAN-index (inline för ICA/Coop/CG, Axfood-kod resolvat ur ean_cache).
    # Fylls write-path i replace_store_offers; ersätter json_each-scans + Axfood-reverse-resolve i
    # läsvägarna (stores_with_offer/offers_for_eans). Indexerat på ean.
    conn.execute(
        "CREATE TABLE IF NOT EXISTS offer_eans (chain TEXT, store_id TEXT, offer_id TEXT, ean TEXT, "
        "PRIMARY KEY (chain, store_id, offer_id, ean))"
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_offer_eans_ean ON offer_eans(ean)")
    # Engångs-backfill ur befintliga offers (inline + Axfood via ean_cache) om tomt.
    if conn.execute("SELECT COUNT(*) FROM offer_eans").fetchone()[0] == 0:
        conn.execute(
            "INSERT OR IGNORE INTO offer_eans SELECT o.chain, o.store_id, o.offer_id, je.value "
            "FROM offers o, json_each(o.eans) je WHERE o.eans NOT IN ('','[]')"
        )
        conn.execute(
            "INSERT OR IGNORE INTO offer_eans SELECT o.chain, o.store_id, o.offer_id, e.ean "
            "FROM offers o JOIN ean_cache e ON o.offer_id=e.code "
            "WHERE o.chain IN ('willys','hemkop') AND e.ean!=''"
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
    # Editerbar speditör-vokabulär (seedas en gång) + override-mappning label -> speditör.
    conn.execute("CREATE TABLE IF NOT EXISTS providers (name TEXT PRIMARY KEY)")
    if not conn.execute("SELECT 1 FROM providers LIMIT 1").fetchone():
        conn.executemany("INSERT INTO providers (name) VALUES (?)", [(p,) for p in DEFAULT_PROVIDERS])
    conn.execute("CREATE TABLE IF NOT EXISTS provider_map (label TEXT PRIMARY KEY, provider TEXT)")
    # Persistent anropslogg: ring-buffer (feed, beskärs) + kumulativ statistik per host.
    conn.execute(
        "CREATE TABLE IF NOT EXISTS api_calls (id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, "
        "method TEXT, host TEXT, path TEXT, status INTEGER, ms REAL, chain TEXT)"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS api_call_stats (host TEXT PRIMARY KEY, chain TEXT, "
        "count INTEGER DEFAULT 0, errors INTEGER DEFAULT 0, total_ms REAL DEFAULT 0)"
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
    # EAN(13) -> ICA consumerItemId (detalj-URL:en). Söket scopar på butikssortiment, så
    # resolvern provar flera butiker; cid='' = försökt utan träff (negativ cache).
    conn.execute(
        "CREATE TABLE IF NOT EXISTS ica_item_map (ean TEXT PRIMARY KEY, cid TEXT, fetched_at TEXT)"
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
    _ensure_column(conn, "ean_cache", "origin", "TEXT")  # Axfood ursprungsland (svenska, förvärmt)
    _ensure_column(conn, "offer_observations", "savings", "REAL")  # för att spåra ordinarie pris
    _ensure_column(conn, "offer_observations", "member_price", "INTEGER")
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


# ---- Speditörer (vokabulär + label-override), speglar tagg-typer/tag_map ----
def load_providers():
    conn = get_conn()
    rows = conn.execute("SELECT name FROM providers ORDER BY rowid").fetchall()
    conn.close()
    return [r["name"] for r in rows]


def add_provider(name):
    conn = get_conn()
    conn.execute("INSERT OR IGNORE INTO providers (name) VALUES (?)", (name,))
    conn.commit()
    conn.close()


def remove_provider(name):
    conn = get_conn()
    conn.execute("DELETE FROM providers WHERE name=?", (name,))
    conn.commit()
    conn.close()


def provider_in_use(name):
    """True om någon provider_map-rad pekar på speditören."""
    conn = get_conn()
    row = conn.execute("SELECT 1 FROM provider_map WHERE provider=? LIMIT 1", (name,)).fetchone()
    conn.close()
    return bool(row)


def load_provider_map():
    conn = get_conn()
    rows = conn.execute("SELECT label, provider FROM provider_map").fetchall()
    conn.close()
    return {r["label"]: r["provider"] for r in rows}


def set_provider_map(label, provider):
    conn = get_conn()
    conn.execute("INSERT OR REPLACE INTO provider_map (label, provider) VALUES (?,?)", (label, provider))
    conn.commit()
    conn.close()


def delete_provider_map(label):
    conn = get_conn()
    conn.execute("DELETE FROM provider_map WHERE label=?", (label,))
    conn.commit()
    conn.close()


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
    """Förvärm code -> {ean, category, origin} (Axfood /p/{code}). category =
    googleAnalyticsCategory; origin = ursprungsland (svenska)."""
    if not mapping:
        return
    conn = get_conn()
    conn.executemany(
        "INSERT OR REPLACE INTO ean_cache (code, ean, category, origin, fetched_at) VALUES (?,?,?,?,?)",
        [(c, m.get("ean") or "", m.get("category") or None, m.get("origin") or None, _now())
         for c, m in mapping.items()],
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


def get_axfood_origins(codes):
    """{code: origin} ur ean_cache för Axfood-koder (förvärmt ursprungsland, svenska)."""
    codes = list(codes)
    if not codes:
        return {}
    conn = get_conn()
    rows = conn.execute(
        f"SELECT code, origin FROM ean_cache WHERE origin IS NOT NULL AND origin != '' "
        f"AND code IN ({','.join('?' * len(codes))})",
        codes,
    ).fetchall()
    conn.close()
    return {r["code"]: r["origin"] for r in rows}


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


def ica_offer_eans():
    """Distinkta 13-siffriga EAN ur ICA-erbjudanden (för ICA-kategori-förvärmning)."""
    conn = get_conn()
    rows = conn.execute("SELECT DISTINCT eans FROM offers WHERE chain='ica' AND eans NOT IN ('','[]')").fetchall()
    conn.close()
    out = set()
    for r in rows:
        try:
            out.update(json.loads(r["eans"]))
        except (json.JSONDecodeError, TypeError):
            pass
    return [e for e in out if e and len(str(e)) == 13]


def axfood_offer_codes():
    """Distinkta Axfood-artikelkoder (offer_id) ur cachade Willys/Hemköp-erbjudanden, per kedja.
    Efter en sweep är offers-cachen komplett -> hela kodmängden (inkl. ev. regionala koder som
    15-butikers-samplingen i warm_axfood_eans missar). {chain: [codes]}."""
    conn = get_conn()
    rows = conn.execute(
        "SELECT chain, offer_id FROM offers WHERE chain IN ('willys','hemkop') GROUP BY chain, offer_id"
    ).fetchall()
    conn.close()
    out = {}
    for r in rows:
        out.setdefault(r["chain"], []).append(r["offer_id"])
    return out


def product_info_eans():
    """RÅ mängd EAN i product_info (positiva som negativa rader, oavsett TTL). Används som
    'redan försökt'-filter i förvärmning - utgångna negativa ska INTE re-warmas (TTL-vägen
    är den lazy route:n), annars äter döda EAN upp förvärmnings-capen i all evighet."""
    conn = get_conn()
    rows = conn.execute("SELECT ean FROM product_info").fetchall()
    conn.close()
    return {r["ean"] for r in rows}


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
                 if chain in ("willys", "hemkop") else {})
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
        "SELECT o.chain, o.store_id, o.name, o.price, o.comparison_value, o.comparison_unit, "
        "o.valid_to, o.member_price FROM offer_eans oe "
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
            best[key] = {"chain": r["chain"], "store_id": r["store_id"], "name": r["name"],
                         "price": r["price"], "comparison_value": r["comparison_value"],
                         "comparison_unit": r["comparison_unit"], "valid_to": r["valid_to"],
                         "member_price": bool(r["member_price"])}
    return list(best.values())


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
    ph = ",".join("?" * len(eans))
    for r in conn.execute(
        f"SELECT oe.ean, o.chain, o.price, o.comparison_value, o.comparison_unit, o.valid_to, "
        f"o.member_price FROM offer_eans oe "
        f"JOIN offers o ON oe.chain=o.chain AND oe.store_id=o.store_id AND oe.offer_id=o.offer_id "
        f"WHERE oe.ean IN ({ph})",
        eans,
    ):
        slot = out.setdefault(r["ean"], {})
        cur = slot.get(r["chain"])
        if cur is None or (r["price"] is not None and (cur["price"] is None or r["price"] < cur["price"])):
            slot[r["chain"]] = {"price": r["price"], "comparison_value": r["comparison_value"],
                                "comparison_unit": r["comparison_unit"], "valid_to": r["valid_to"],
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
        return ("ca " if approx else "") + f"{value:g} {unit}"
    return s


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
        if cat == "ovrigt":
            cat = category_from_name(rep.get("name")) or "ovrigt"
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


def add_match_member(group_id, member):
    """Lägg en produkt i en befintlig grupp. PK (chain, ean) -> INSERT OR REPLACE flyttar den
    om den redan låg i en annan grupp."""
    conn = get_conn()
    conn.execute(
        "INSERT OR REPLACE INTO product_matches (group_id, chain, ean, name, brand, package) VALUES (?,?,?,?,?,?)",
        (group_id, member["chain"], str(member["ean"]), member.get("name"),
         member.get("brand"), member.get("package")),
    )
    conn.commit()
    conn.close()


def match_group_exists(group_id):
    conn = get_conn()
    row = conn.execute("SELECT 1 FROM product_matches WHERE group_id=? LIMIT 1", (group_id,)).fetchone()
    conn.close()
    return row is not None


def member_group(chain, ean):
    """group_id för en medlem (chain, ean), eller None."""
    conn = get_conn()
    row = conn.execute(
        "SELECT group_id FROM product_matches WHERE chain=? AND ean=?", (chain, str(ean))
    ).fetchone()
    conn.close()
    return row["group_id"] if row else None


def match_group_size(group_id):
    conn = get_conn()
    n = conn.execute(
        "SELECT COUNT(*) AS c FROM product_matches WHERE group_id=?", (group_id,)
    ).fetchone()["c"]
    conn.close()
    return n


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


# Produktinfo-cachen har TTL åt båda håll (lazy re-hämtning vid åtkomst efter utgång):
# - positiv (faktisk info): ingredienser/näring/ursprung kan ändras vid receptändringar (ICA:s
#   egen sida brasklappar om det), så vi håller den färsk men inte aggressivt - produktdata
#   ändras långsamt.
# - negativ (data=null, "hämtat, inget fanns"): kortare, så säsongs-/omlagervaror som får en
#   detaljsida kan dyka upp igen utan att vänta lika länge.
_POS_TTL_DAYS = 30
_NEG_TTL_DAYS = 14


def _info_expired(fetched_at, ttl_days):
    from datetime import datetime, timedelta, timezone

    try:
        t = datetime.strptime(fetched_at, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return True
    return datetime.now(timezone.utc) - t > timedelta(days=ttl_days)


def product_info_cached(ean):
    """(finns_i_cache, info, fetched_at). info=None med finns=True = negativ cache (ej utgången).
    Utgången cache (positiv som negativ) rapporteras som ej-cachad -> route hämtar om."""
    conn = get_conn()
    row = conn.execute(
        "SELECT data, fetched_at FROM product_info WHERE ean=?", (str(ean),)
    ).fetchone()
    conn.close()
    if not row:
        return False, None, None
    data = json.loads(row["data"])
    ttl = _NEG_TTL_DAYS if data is None else _POS_TTL_DAYS
    if _info_expired(row["fetched_at"], ttl):
        return False, None, None
    return True, data, row["fetched_at"]


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
    return now


def get_ica_cid(ean):
    """ICA consumerItemId för en EAN. None = ej försökt; '' = försökt utan träff; annars cid."""
    conn = get_conn()
    row = conn.execute("SELECT cid FROM ica_item_map WHERE ean=?", (str(ean),)).fetchone()
    conn.close()
    return row["cid"] if row else None


def save_ica_cid(ean, cid):
    conn = get_conn()
    conn.execute(
        "INSERT OR REPLACE INTO ica_item_map (ean, cid, fetched_at) VALUES (?,?,?)",
        (str(ean), cid or "", _now()),
    )
    conn.commit()
    conn.close()


def ica_resolve_accounts(limit=4):
    """Upp till `limit` ICA-accountNumber, ett per butiksprofil (störst format först), för
    butiks-scopad EAN->consumerItemId-resolv. Söket returnerar bara butikens sortiment, så
    en handfull profiler (Maxi/Kvantum/Supermarket/Nära) täcker betydligt fler EAN än en."""
    order = {"Maxi": 0, "Kvantum": 1, "Supermarket": 2, "Nära": 3}
    conn = get_conn()
    rows = conn.execute(
        "SELECT native FROM stores WHERE chain='ica' AND native IS NOT NULL"
    ).fetchall()
    conn.close()
    seen, picks = set(), []
    for r in rows:
        try:
            n = json.loads(r["native"])
        except (ValueError, TypeError):
            continue
        prof, acct = n.get("profile"), n.get("accountNumber")
        if acct and prof not in seen:
            seen.add(prof)
            picks.append((order.get(prof, 9), acct))
    picks.sort()
    return [a for _, a in picks][:limit]


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
