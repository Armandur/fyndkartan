import json

from ._conn import _now, get_conn
from ..categories import raw_key
from ..config import (
    BUILTIN_TAG_TYPES, DEFAULT_CATEGORY_MAP, DEFAULT_PRIVATE_BRANDS, DEFAULT_PROVIDERS,
    DEFAULT_TAG_TYPES,
)


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
