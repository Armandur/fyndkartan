import json

from ._conn import _now, get_conn
from ..categories import category_from_detail


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
