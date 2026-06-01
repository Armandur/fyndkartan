import json
import sqlite3

from .config import DB_PATH


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
    # ALTER TABLE-guards för nya kolumner (ingen Alembic).
    _ensure_column(conn, "offers", "member_price", "INTEGER")
    _ensure_column(conn, "offers", "savings", "REAL")
    conn.commit()
    conn.close()


def _ensure_column(conn, table, col, coltype):
    cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
    if col not in cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {coltype}")


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
        "tags": json.loads(r["tags"]) if r["tags"] else [],
        "native": json.loads(r["native"]) if r["native"] else None,
        "source": {"method": r["method"], "fetched_at": r["fetched_at"]},
    }
