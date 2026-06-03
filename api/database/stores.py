import json

from ._conn import get_conn
from ..config import ORIGIN_COUNTRIES
from ..tags import build_tag


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
