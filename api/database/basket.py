"""Namngivna matkassar per app-användare (`baskets` + `basket_items`): t.ex. veckohandling eller
ett recept. CRUD; matkasse-jämförelsen (`zone.basket_compare`) läser en kasses varor. Alla
operationer ägar-kontrolleras (basket_id kommer från klienten) - en användare når bara sina egna."""
from sqlalchemy import text

from ._conn import _now, get_conn
from ..matching import normalize_ean


def list_baskets(user_id):
    """Användarens matkassar: [{id, name, item_count, updated_at}], senast ändrad först."""
    conn = get_conn()
    rows = conn.execute(text(
        "SELECT b.id, b.name, b.updated_at, COUNT(i.ean) AS item_count "
        "FROM baskets b LEFT JOIN basket_items i ON i.basket_id=b.id "
        "WHERE b.user_id=:uid GROUP BY b.id, b.name, b.updated_at "
        "ORDER BY b.updated_at DESC, b.id DESC"), {"uid": user_id}).fetchall()
    conn.close()
    return [{"id": r["id"], "name": r["name"], "item_count": r["item_count"], "updated_at": r["updated_at"]}
            for r in rows]


def create_basket(user_id, name):
    """Skapa en ny matkasse. Returnerar {id, name}."""
    name = (name or "").strip() or "Ny matkasse"
    now = _now()
    conn = get_conn()
    bid = conn.execute(
        text("INSERT INTO baskets (user_id, name, created_at, updated_at) "
             "VALUES (:uid, :name, :now, :now) RETURNING id"),
        {"uid": user_id, "name": name, "now": now}).fetchone()[0]
    conn.commit()
    conn.close()
    return {"id": bid, "name": name}


def _owns(conn, user_id, basket_id):
    r = conn.execute(text("SELECT 1 FROM baskets WHERE id=:id AND user_id=:uid"),
                     {"id": basket_id, "uid": user_id}).fetchone()
    return r is not None


def _touch(conn, basket_id):
    conn.execute(text("UPDATE baskets SET updated_at=:now WHERE id=:id"), {"now": _now(), "id": basket_id})


def rename_basket(user_id, basket_id, name):
    """Byt namn på en matkasse. Returnerar True om den ägs av användaren."""
    name = (name or "").strip()
    if not name:
        return False
    conn = get_conn()
    if not _owns(conn, user_id, basket_id):
        conn.close()
        return False
    conn.execute(text("UPDATE baskets SET name=:name, updated_at=:now WHERE id=:id"),
                 {"name": name, "now": _now(), "id": basket_id})
    conn.commit()
    conn.close()
    return True


def delete_basket(user_id, basket_id):
    """Ta bort en matkasse + dess varor. Returnerar True om den ägdes av användaren."""
    conn = get_conn()
    if not _owns(conn, user_id, basket_id):
        conn.close()
        return False
    conn.execute(text("DELETE FROM basket_items WHERE basket_id=:id"), {"id": basket_id})
    conn.execute(text("DELETE FROM baskets WHERE id=:id"), {"id": basket_id})
    conn.commit()
    conn.close()
    return True


def get_basket_items(user_id, basket_id):
    """En matkasses varor [{ean, qty, exact}], senast tillagd först. None om kassen inte ägs av användaren."""
    conn = get_conn()
    if not _owns(conn, user_id, basket_id):
        conn.close()
        return None
    rows = conn.execute(text("SELECT ean, qty, exact FROM basket_items WHERE basket_id=:id "
                             "ORDER BY added_at DESC, ean"), {"id": basket_id}).fetchall()
    conn.close()
    return [{"ean": r["ean"], "qty": r["qty"], "exact": bool(r["exact"])} for r in rows]


def set_basket_item(user_id, basket_id, ean, qty=1, exact=None):
    """Lägg/uppdatera en vara i en matkasse (qty >= 1). `exact` (bool) styr private-label-substitution;
    None = bevara befintlig flagga (default 0 för ny rad). Returnerar normaliserad EAN, eller None om
    ogiltig EAN ELLER kassen inte ägs av användaren."""
    e = normalize_ean(ean)
    if not e:
        return None
    qty = max(1, int(qty or 1))
    conn = get_conn()
    if not _owns(conn, user_id, basket_id):
        conn.close()
        return None
    if exact is None:  # bevara befintlig exact-flagga vid ren qty-uppdatering
        conn.execute(
            text("INSERT INTO basket_items (basket_id, ean, qty, exact, added_at) VALUES (:id, :ean, :qty, 0, :now) "
                 "ON CONFLICT (basket_id, ean) DO UPDATE SET qty=excluded.qty"),
            {"id": basket_id, "ean": e, "qty": qty, "now": _now()})
    else:
        conn.execute(
            text("INSERT INTO basket_items (basket_id, ean, qty, exact, added_at) VALUES (:id, :ean, :qty, :ex, :now) "
                 "ON CONFLICT (basket_id, ean) DO UPDATE SET qty=excluded.qty, exact=excluded.exact"),
            {"id": basket_id, "ean": e, "qty": qty, "ex": 1 if exact else 0, "now": _now()})
    _touch(conn, basket_id)
    conn.commit()
    conn.close()
    return e


def remove_basket_item(user_id, basket_id, ean):
    """Ta bort en vara ur en matkasse (ägar-kontrollerat)."""
    conn = get_conn()
    if not _owns(conn, user_id, basket_id):
        conn.close()
        return
    conn.execute(text("DELETE FROM basket_items WHERE basket_id=:id AND ean=:ean"),
                 {"id": basket_id, "ean": normalize_ean(ean) or ean})
    _touch(conn, basket_id)
    conn.commit()
    conn.close()


def clear_basket_items(user_id, basket_id):
    """Töm en matkasse (ägar-kontrollerat)."""
    conn = get_conn()
    if not _owns(conn, user_id, basket_id):
        conn.close()
        return
    conn.execute(text("DELETE FROM basket_items WHERE basket_id=:id"), {"id": basket_id})
    _touch(conn, basket_id)
    conn.commit()
    conn.close()
