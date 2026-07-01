import json

from sqlalchemy import bindparam, text

from ._conn import _now, get_conn, json_array_len, json_get, json_is_true, stats_memo
from ..categories import category_from_detail
from .. import countries, diet


def get_product_info(ean):
    conn = get_conn()
    row = conn.execute(text("SELECT data FROM product_info WHERE ean=:ean"),
                       {"ean": str(ean)}).fetchone()
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
        text("SELECT data, fetched_at FROM product_info WHERE ean=:ean"), {"ean": str(ean)}
    ).fetchone()
    conn.close()
    if not row:
        return False, None, None
    data = json.loads(row["data"])
    ttl = _NEG_TTL_DAYS if data is None else _POS_TTL_DAYS
    if _info_expired(row["fetched_at"], ttl):
        return False, None, None
    return True, data, row["fetched_at"]


def _product_info_fields(eans, select_exprs):
    """Batchad läsning ur product_info för en EAN-mängd: `SELECT ean, <select_exprs> WHERE ean IN (...)`.
    Delad loader för get_product_categories/origins + product_info_fresh_set (REVIEW Fynd C) - samlar
    EAN-normalisering, tom-koll, IN-klausul och conn-hantering på ett ställe. Returnerar Row-lista
    (tom om inga giltiga EAN)."""
    eans = [str(e) for e in eans if e]
    if not eans:
        return []
    conn = get_conn()
    rows = conn.execute(
        text(f"SELECT ean, {', '.join(select_exprs)} FROM product_info "
             "WHERE ean IN :eans").bindparams(bindparam("eans", expanding=True)),
        {"eans": eans},
    ).fetchall()
    conn.close()
    return rows


def get_product_categories(eans):
    """{ean: kanonisk kategori} ur produktdetalj-cachen (rikare än offer-nivån).
    Resolverar category_raw+source -> kanonisk; bara de som mappar."""
    rows = _product_info_fields(eans, [
        f"{json_get('data', 'category_raw')} AS raw",
        f"{json_get('data', 'category_source')} AS src",
    ])
    out = {}
    for r in rows:
        canon = category_from_detail(r["src"], r["raw"]) if r["raw"] else None
        if canon:
            out[r["ean"]] = canon
    return out


_DIET_CACHE = None  # {ean: diet}; modulnivå-cache, invalideras vid varje product_info-skrivning


def get_product_diets():
    """{ean: diet} (vegan/vegetarian/none) härledd ur cachade ingredienser för bläddra-filtret.
    Derive-at-read (alltid aktuell vokabulär); bara EAN med ingredienslista. Hela mängden (~11k) -
    catalog_browse anropar bara när diet-filtret är aktivt. Modulnivå-cachad (REVIEW Fynd D): hela
    klassificeringen är ~50-100ms, så vid interaktiv filtrering återanvänds resultatet; cachen nollas
    i save_product_info (enda product_info-skrivaren) när ingredienser kan ha ändrats."""
    global _DIET_CACHE
    if _DIET_CACHE is not None:
        return _DIET_CACHE
    conn = get_conn()
    rows = conn.execute(text(
        f"SELECT ean, {json_get('data', 'ingredients')} AS ing FROM product_info "
        f"WHERE {json_get('data', 'ingredients')} IS NOT NULL"
    )).fetchall()
    conn.close()
    out = {}
    for r in rows:
        d = diet.classify_diet(r["ing"])
        if d:
            out[r["ean"]] = d
    _DIET_CACHE = out
    return out


def get_product_origins(eans):
    """{ean: (origin-namn-lista, ISO-koder)} ur produktdetalj-cachen (Axfood/Coop/ICA-detalj).
    Rikare ursprung än offers brand-parsning (som bara fångar ICA/Coop). Bara EAN där minst
    ett land kunde resolvas; råname normaliseras till svenska via countries.split_origins."""
    rows = _product_info_fields(eans, [f"{json_get('data', 'origin')} AS origin"])
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
        text("INSERT INTO product_info (ean, data, fetched_at) VALUES (:ean, :data, :fetched_at) "
             "ON CONFLICT (ean) DO UPDATE SET data=excluded.data, fetched_at=excluded.fetched_at"),
        {"ean": str(ean), "data": json.dumps(data, ensure_ascii=False), "fetched_at": now},
    )
    conn.commit()
    conn.close()
    global _DIET_CACHE
    _DIET_CACHE = None  # ingredienser kan ha ändrats -> bläddra-filtrets diet-karta byggs om vid nästa anrop
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
        text("SELECT ean, source, ingredients, nutrition, origin FROM product_info_observations "
             "WHERE ean IN :eans ORDER BY id").bindparams(bindparam("eans", expanding=True)),
        {"eans": eans},
    ):
        last[(r["ean"], r["source"])] = _info_sig(
            r["ingredients"], json.loads(r["nutrition"]) if r["nutrition"] else [], r["origin"])
    now = _now()
    rows = [{"ean": c[0], "source": c[1], "ingredients": c[2],
             "nutrition": json.dumps(c[3], ensure_ascii=False) if c[3] else None,
             "origin": c[4], "observed_at": now}
            for c in cand if last.get((c[0], c[1])) != c[5]]
    if rows:
        conn.executemany(
            text("INSERT INTO product_info_observations "
                 "(ean, source, ingredients, nutrition, origin, observed_at) "
                 "VALUES (:ean, :source, :ingredients, :nutrition, :origin, :observed_at)"), rows)
        conn.commit()
    conn.close()


def sparse_partial_eans(limit=None):
    """EAN för partial-rader (piggyback) med GLES näring (< 4 värden) - kandidater för full
    korsskällig merge-uppgradering. Uppgraderade rader tappar partial-flaggan och faller ur mängden."""
    conn = get_conn()
    sql = (f"SELECT ean FROM product_info WHERE {json_is_true('data', 'partial')} "
           f"AND COALESCE({json_array_len('data', 'nutrition')}, 0) < 4")
    params = {}
    if limit:
        sql += " LIMIT :limit"
        params["limit"] = int(limit)
    rows = conn.execute(text(sql), params).fetchall()
    conn.close()
    return [r["ean"] for r in rows]


@stats_memo
def product_info_observations_stats():
    """(antal rader, distinkta produkter (ean), äldsta observation) för innehållshistoriken
    (recept-/närings-/ursprungsändringar, product_info_observations)."""
    conn = get_conn()
    r = conn.execute(text(
        "SELECT COUNT(*) c, COUNT(DISTINCT ean) p, MIN(observed_at) o FROM product_info_observations"
    )).fetchone()
    conn.close()
    return {"rows": r["c"], "products": r["p"], "since": r["o"]}


@stats_memo
def partial_info_counts():
    """{partial: antal partial-rader, sparse: antal med gles näring (<4, uppgraderingskandidater)}."""
    conn = get_conn()
    total = conn.execute(text(
        f"SELECT COUNT(*) FROM product_info WHERE {json_is_true('data', 'partial')}")).fetchone()[0]
    sparse = conn.execute(text(
        f"SELECT COUNT(*) FROM product_info WHERE {json_is_true('data', 'partial')} "
        f"AND COALESCE({json_array_len('data', 'nutrition')}, 0) < 4")).fetchone()[0]
    conn.close()
    return {"partial": total, "sparse": sparse}


def product_info_fresh_set(eans):
    """Mängd EAN som har en EJ utgången product_info-rad (full/partial/negativ). För piggyback-
    skrivningarnas skip-if-fresh - utgångna återfylls av nästa crawl/warm."""
    rows = _product_info_fields(eans, ["data", "fetched_at"])
    out = set()
    for r in rows:
        ttl = _NEG_TTL_DAYS if json.loads(r["data"]) is None else _POS_TTL_DAYS
        if not _info_expired(r["fetched_at"], ttl):
            out.add(r["ean"])
    return out


def get_ica_cid(ean):
    """ICA consumerItemId för en EAN. None = ej försökt; '' = försökt utan träff; annars cid."""
    conn = get_conn()
    row = conn.execute(text("SELECT cid FROM ica_item_map WHERE ean=:ean"),
                       {"ean": str(ean)}).fetchone()
    conn.close()
    return row["cid"] if row else None


def save_ica_cid(ean, cid):
    conn = get_conn()
    conn.execute(
        text("INSERT INTO ica_item_map (ean, cid, fetched_at) VALUES (:ean, :cid, :fetched_at) "
             "ON CONFLICT (ean) DO UPDATE SET cid=excluded.cid, fetched_at=excluded.fetched_at"),
        {"ean": str(ean), "cid": cid or "", "fetched_at": _now()},
    )
    conn.commit()
    conn.close()


def save_ica_cid_eans(pairs):
    """Batch-upsert consumerItemId -> gtin (`ica_cid_ean`). `pairs` = iterable av (cid, ean). Byggs av
    quicksearch-crawlen; ecom-pris-crawlen (api/ica_ecom.py) joinar retailerProductId(==cid) -> ean."""
    rows = [{"cid": str(c), "ean": str(e), "fetched_at": _now()}
            for c, e in pairs if c and e]
    if not rows:
        return 0
    conn = get_conn()
    conn.execute(
        text("INSERT INTO ica_cid_ean (cid, ean, fetched_at) VALUES (:cid, :ean, :fetched_at) "
             "ON CONFLICT (cid) DO UPDATE SET ean=excluded.ean, fetched_at=excluded.fetched_at"),
        rows)
    conn.commit()
    conn.close()
    return len(rows)


def ica_ean_for_cids(cids):
    """{cid: ean} för givna consumerItemId/retailerProductId ur `ica_cid_ean` (fallback: `ica_item_map`)."""
    ids = [str(c) for c in cids if c]
    if not ids:
        return {}
    conn = get_conn()
    q1 = text("SELECT cid, ean FROM ica_cid_ean WHERE cid IN :ids").bindparams(
        bindparam("ids", expanding=True))
    out = {r["cid"]: r["ean"] for r in conn.execute(q1, {"ids": ids})}
    # komplettera ur ica_item_map (detalj-hämtade) för cids vi ännu inte sett i quicksearch-crawlen
    missing = [c for c in ids if c not in out]
    if missing:
        q2 = text("SELECT cid, ean FROM ica_item_map WHERE cid IN :ids AND cid != ''").bindparams(
            bindparam("ids", expanding=True))
        for r in conn.execute(q2, {"ids": missing}):
            out.setdefault(r["cid"], r["ean"])
    conn.close()
    return out


def ica_resolve_accounts(limit=4):
    """Upp till `limit` ICA-accountNumber, ett per butiksprofil (störst format först), för
    butiks-scopad EAN->consumerItemId-resolv. Söket returnerar bara butikens sortiment, så
    en handfull profiler (Maxi/Kvantum/Supermarket/Nära) täcker betydligt fler EAN än en."""
    order = {"Maxi": 0, "Kvantum": 1, "Supermarket": 2, "Nära": 3}
    conn = get_conn()
    rows = conn.execute(text(
        "SELECT native FROM stores WHERE chain='ica' AND native IS NOT NULL"
    )).fetchall()
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
        text("SELECT content_type, source_url FROM product_images WHERE ean=:ean AND size=:size"),
        {"ean": str(ean), "size": size}
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def save_image_meta(ean, size, content_type, source_url):
    conn = get_conn()
    conn.execute(
        text("INSERT INTO product_images (ean, size, content_type, source_url, fetched_at) "
             "VALUES (:ean, :size, :content_type, :source_url, :fetched_at) "
             "ON CONFLICT (ean, size) DO UPDATE SET content_type=excluded.content_type, "
             "source_url=excluded.source_url, fetched_at=excluded.fetched_at"),
        {"ean": str(ean), "size": size, "content_type": content_type,
         "source_url": source_url, "fetched_at": _now()},
    )
    conn.commit()
    conn.close()


# ---- Slutanvändar-tokens (opaka bearer, för icke-webb-klienter) ----
