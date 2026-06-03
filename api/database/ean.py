import json

from ._conn import _now, get_conn


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
