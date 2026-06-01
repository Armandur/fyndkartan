import json
import sqlite3

from .config import BUILTIN_TAG_TYPES, DB_PATH, DEFAULT_PRIVATE_BRANDS, DEFAULT_TAG_TYPES
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
    # Editerbar kanonisk vokabulär; seedas med default-listan första gången.
    conn.execute("CREATE TABLE IF NOT EXISTS tag_types (type TEXT PRIMARY KEY)")
    if not conn.execute("SELECT 1 FROM tag_types LIMIT 1").fetchone():
        conn.executemany("INSERT INTO tag_types (type) VALUES (?)", [(t,) for t in DEFAULT_TAG_TYPES])
    # Säkerställ att inbyggda (seed-producerade) typer alltid finns.
    conn.executemany(
        "INSERT OR IGNORE INTO tag_types (type) VALUES (?)", [(t,) for t in BUILTIN_TAG_TYPES]
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
    # ALTER TABLE-guards för nya kolumner (ingen Alembic).
    _ensure_column(conn, "offers", "member_price", "INTEGER")
    _ensure_column(conn, "offers", "savings", "REAL")
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


def load_tag_types():
    conn = get_conn()
    rows = conn.execute("SELECT type FROM tag_types ORDER BY rowid").fetchall()
    conn.close()
    return [r["type"] for r in rows]


def add_tag_type(type_):
    conn = get_conn()
    conn.execute("INSERT OR IGNORE INTO tag_types (type) VALUES (?)", (type_,))
    conn.commit()
    conn.close()


def remove_tag_type(type_):
    conn = get_conn()
    conn.execute("DELETE FROM tag_types WHERE type=?", (type_,))
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
        out.append(d)
    return out


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
        "native": json.dumps(s.get("native"), ensure_ascii=False) if s.get("native") else None,
        "method": (s.get("source") or {}).get("method"),
        "fetched_at": (s.get("source") or {}).get("fetched_at"),
    }


_COLS = (
    "chain,store_id,name,brand,street,postal_code,city,lat,lng,phone,email,"
    "oh_today,open_now,link_store,link_offers,link_online,tags,raw,native,method,fetched_at"
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
