"""Server-side matkasse per app-användare (`basket`-tabellen): EAN + antal. CRUD; matkasse-
jämförelsen (`zone.basket_compare`) läser listan. Speglar favorit-mönstret (inloggad-bara)."""
from sqlalchemy import text

from ._conn import _now, get_conn
from ..matching import normalize_ean


def list_basket(user_id):
    """Användarens matkasse: [{ean, qty}], senast tillagd först."""
    conn = get_conn()
    rows = conn.execute(text("SELECT ean, qty FROM basket WHERE user_id=:uid ORDER BY added_at DESC, ean"),
                        {"uid": user_id}).fetchall()
    conn.close()
    return [{"ean": r["ean"], "qty": r["qty"]} for r in rows]


def set_basket_item(user_id, ean, qty=1):
    """Lägg/uppdatera en vara i matkassen (qty >= 1). Returnerar normaliserad EAN, eller None om ogiltig."""
    e = normalize_ean(ean)
    if not e:
        return None
    qty = max(1, int(qty or 1))
    conn = get_conn()
    conn.execute(
        text("INSERT INTO basket (user_id, ean, qty, added_at) VALUES (:uid, :ean, :qty, :now) "
             "ON CONFLICT (user_id, ean) DO UPDATE SET qty=excluded.qty"),
        {"uid": user_id, "ean": e, "qty": qty, "now": _now()})
    conn.commit()
    conn.close()
    return e


def remove_basket_item(user_id, ean):
    """Ta bort en vara ur matkassen."""
    conn = get_conn()
    conn.execute(text("DELETE FROM basket WHERE user_id=:uid AND ean=:ean"),
                 {"uid": user_id, "ean": normalize_ean(ean) or ean})
    conn.commit()
    conn.close()


def clear_basket(user_id):
    """Töm hela matkassen."""
    conn = get_conn()
    conn.execute(text("DELETE FROM basket WHERE user_id=:uid"), {"uid": user_id})
    conn.commit()
    conn.close()
