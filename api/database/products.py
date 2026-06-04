import json

from ._conn import _now, get_conn
from ..categories import category_from_detail
from .. import countries


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


def get_product_origins(eans):
    """{ean: (origin-namn-lista, ISO-koder)} ur produktdetalj-cachen (Axfood/Coop/ICA-detalj).
    Rikare ursprung än offers brand-parsning (som bara fångar ICA/Coop). Bara EAN där minst
    ett land kunde resolvas; råname normaliseras till svenska via countries.split_origins."""
    eans = [str(e) for e in eans if e]
    if not eans:
        return {}
    conn = get_conn()
    rows = conn.execute(
        f"SELECT ean, json_extract(data,'$.origin') AS origin FROM product_info "
        f"WHERE ean IN ({','.join('?' * len(eans))})",
        eans,
    ).fetchall()
    conn.close()
    out = {}
    for r in rows:
        if not r["origin"]:
            continue
        _, codes = countries.split_origins(r["origin"])
        if codes:
            out[r["ean"]] = ([countries.sv_name(c) for c in codes], codes)
    return out


def save_product_info(ean, data, partial=False):
    """Cacha produktinfo. partial=True markerar en EN-källa-piggyback (Coop/Axfood ur crawl/warm)
    som on-demand-endpointen senare uppgraderar till full korsskällig merge (fetch_for_ean)."""
    from datetime import datetime, timezone

    if partial and data is not None:
        data = {**data, "partial": True}
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    conn = get_conn()
    conn.execute(
        "INSERT OR REPLACE INTO product_info (ean, data, fetched_at) VALUES (?,?,?)",
        (str(ean), json.dumps(data, ensure_ascii=False), now),
    )
    conn.commit()
    conn.close()
    return now


def _info_sig(ingredients, nutrition, origin):
    """Normaliserad signatur för ändringsdetektering (skiftläge/whitespace-tålig, näring ordnad)."""
    ing = " ".join((ingredients or "").lower().split())
    nut = ";".join(sorted(
        f"{(n.get('label') or '').lower()}={n.get('value')}{(n.get('unit') or '').lower()}"
        for n in (nutrition or [])))
    return f"{ing}|{nut}|{(origin or '').lower().strip()}"


def archive_product_info(items):
    """Append-on-change-historik per (ean, source) för produktinnehåll (recept-/närings-/ursprungs-
    ändringar). `items` = iterabel av (ean, part) där part är enkällsform (source satt). Skriver bara
    rader vars signatur skiljer sig från senaste observationen för (ean, source) -> kompakt
    ändringslogg. Batchat (en SELECT + en INSERT). Inget UI än (se ROADMAP)."""
    cand = []  # (ean, source, ingredients, nutrition, origin, sig)
    for ean, part in items:
        source = part.get("source")
        ing, nut, orig = part.get("ingredients"), part.get("nutrition") or [], part.get("origin")
        if not ean or not source or (not ing and not nut and not orig):
            continue
        cand.append((str(ean), source, ing, nut, orig, _info_sig(ing, nut, orig)))
    if not cand:
        return
    conn = get_conn()
    eans = list({c[0] for c in cand})
    last = {}  # (ean, source) -> senaste signatur (id-ordnat -> sista vinner)
    for r in conn.execute(
        f"SELECT ean, source, ingredients, nutrition, origin FROM product_info_observations "
        f"WHERE ean IN ({','.join('?' * len(eans))}) ORDER BY id", eans,
    ):
        last[(r["ean"], r["source"])] = _info_sig(
            r["ingredients"], json.loads(r["nutrition"]) if r["nutrition"] else [], r["origin"])
    now = _now()
    rows = [(c[0], c[1], c[2], json.dumps(c[3], ensure_ascii=False) if c[3] else None, c[4], now)
            for c in cand if last.get((c[0], c[1])) != c[5]]
    if rows:
        conn.executemany(
            "INSERT INTO product_info_observations (ean, source, ingredients, nutrition, origin, observed_at) "
            "VALUES (?,?,?,?,?,?)", rows)
        conn.commit()
    conn.close()


def sparse_partial_eans(limit=None):
    """EAN för partial-rader (piggyback) med GLES näring (< 4 värden) - kandidater för full
    korsskällig merge-uppgradering. Uppgraderade rader tappar partial-flaggan och faller ur mängden."""
    conn = get_conn()
    sql = ("SELECT ean FROM product_info WHERE json_extract(data,'$.partial')=1 "
           "AND COALESCE(json_array_length(json_extract(data,'$.nutrition')),0) < 4")
    if limit:
        sql += f" LIMIT {int(limit)}"
    rows = conn.execute(sql).fetchall()
    conn.close()
    return [r["ean"] for r in rows]


def product_info_observations_stats():
    """(antal rader, distinkta produkter (ean), äldsta observation) för innehållshistoriken
    (recept-/närings-/ursprungsändringar, product_info_observations)."""
    conn = get_conn()
    r = conn.execute(
        "SELECT COUNT(*) c, COUNT(DISTINCT ean) p, MIN(observed_at) o FROM product_info_observations"
    ).fetchone()
    conn.close()
    return {"rows": r["c"], "products": r["p"], "since": r["o"]}


def partial_info_counts():
    """{partial: antal partial-rader, sparse: antal med gles näring (<4, uppgraderingskandidater)}."""
    conn = get_conn()
    total = conn.execute("SELECT COUNT(*) FROM product_info WHERE json_extract(data,'$.partial')=1").fetchone()[0]
    sparse = conn.execute(
        "SELECT COUNT(*) FROM product_info WHERE json_extract(data,'$.partial')=1 "
        "AND COALESCE(json_array_length(json_extract(data,'$.nutrition')),0) < 4").fetchone()[0]
    conn.close()
    return {"partial": total, "sparse": sparse}


def product_info_fresh_set(eans):
    """Mängd EAN som har en EJ utgången product_info-rad (full/partial/negativ). För piggyback-
    skrivningarnas skip-if-fresh - utgångna återfylls av nästa crawl/warm."""
    eans = [str(e) for e in eans if e]
    if not eans:
        return set()
    conn = get_conn()
    rows = conn.execute(
        f"SELECT ean, data, fetched_at FROM product_info WHERE ean IN ({','.join('?' * len(eans))})",
        eans,
    ).fetchall()
    conn.close()
    out = set()
    for r in rows:
        ttl = _NEG_TTL_DAYS if json.loads(r["data"]) is None else _POS_TTL_DAYS
        if not _info_expired(r["fetched_at"], ttl):
            out.add(r["ean"])
    return out


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
