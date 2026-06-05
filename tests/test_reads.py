"""Invariant-tester för de tyngsta/nybyggda LÄS-funktionerna (read-only mot stores.db).

Kompletterar test_schemas.py (som validerar SHAPE) med BETEENDE: filter-monotoni, paginering,
sorterings-stabilitet, normaliserings-grupper, kost-klassificering, ursprung. Robusta mot
data-variation (delmängds-/ordningskontroller, inte exakta antal).

Kör: `.venv/bin/python tests/test_reads.py` (eller pytest).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from api import countries, database, diet, manufacturers  # noqa: E402

_eans = lambda items: {p["ean"] for p in items if p.get("ean")}


def test_classify_diet():
    """Kost-klassificeringen ska klara skiftläge/kompositer (kokosmjölk != mjölk) och hierarkin."""
    cases = {
        "Vatten, socker, vetemjöl": "vegan",
        "Kokosmjölk, havredryck": "vegan",          # växt-mjölk ska inte träffa "mjölk"
        "Mjölkchoklad (mjölkpulver)": "vegetarian",
        "Honung, mandel": "vegetarian",
        "Fläskkött 80%, salt": "none",
        "Lax, salt": "none",
    }
    for ing, exp in cases.items():
        got = diet.classify_diet(ing)
        assert got == exp, f"classify_diet({ing!r}) = {got}, väntat {exp}"
    assert diet.classify_diet(None) is None and diet.classify_diet("") is None
    return len(cases)


def test_catalog_browse_pagination_and_filters():
    """Paginering: sida <= limit, total >= sida. Diet-filter: per-produkt korrekt + hierarki på
    TOTAL-antalet (vegan-total <= vegetariskt-total <= alla; robust mot paginering). only_offers:
    varje produkt on-offer. Store-tagg bara på Coop/ICA-priser. manufacturer = kanonisk av brand."""
    allp, total = database.catalog_browse(q="choklad", limit=300)
    assert len(allp) <= 300 and total >= len(allp)
    dmap = database.get_product_diets()
    vegan, _ = database.catalog_browse(q="choklad", diet="vegan", limit=300)
    veg, _ = database.catalog_browse(q="choklad", diet="vegetarian", limit=300)
    for p in vegan:
        assert dmap.get(p["ean"]) == "vegan", "vegan-filtret släppte in icke-vegan"
    for p in veg:
        assert dmap.get(p["ean"]) in ("vegan", "vegetarian"), "veg-filtret släppte in none/okänt"
    # hierarki på total-antalet (catalog_browse returnerar total FÖRE paginering)
    _, t_all = database.catalog_browse(q="choklad", limit=1)
    _, t_veg = database.catalog_browse(q="choklad", diet="vegetarian", limit=1)
    _, t_vegan = database.catalog_browse(q="choklad", diet="vegan", limit=1)
    assert t_vegan <= t_veg <= t_all, f"hierarki bruten: vegan={t_vegan} veg={t_veg} alla={t_all}"
    # only_offers: varje produkt är on-offer
    oset = database.on_offer_eans()
    off, _ = database.catalog_browse(q="choklad", only_offers=True, limit=300)
    for p in off:
        assert p["ean"] in oset, "only_offers släppte in produkt utan erbjudande"
    # store-tagg + manufacturer (per produkt, robust mot paginering)
    for p in allp:
        for pr in p.get("prices", []):
            if pr["chain"] in ("coop", "ica"):
                assert pr.get("store"), f"{pr['chain']}-pris saknar store-tagg"
            else:
                assert pr.get("store") is None, f"{pr['chain']}-pris har oväntad store-tagg"
        assert p.get("manufacturer") == manufacturers.canonical(p.get("brand"))
    return total


def test_catalog_browse_sort_preserves_set():
    """Icke-filtrerande sort (price/name/spread) ändrar inte TOTAL-antalet, bara ordningen.
    Set-jämförelse görs bara på en smal query där helmängden ryms i limit (annars är top-N olika)."""
    _, total = database.catalog_browse(q="kaffe", limit=1)
    for s in ("price", "name", "spread"):
        _, t = database.catalog_browse(q="kaffe", sort=s, limit=1)
        assert t == total, f"sort={s} ändrade total ({t} != {total})"
    base, tot = database.catalog_browse(q="bregott", limit=500)
    if tot <= 500:  # hela mängden ryms -> set ska vara identisk oavsett ordning
        for s in ("price", "name"):
            srt, _ = database.catalog_browse(q="bregott", sort=s, limit=500)
            assert _eans(srt) == _eans(base), f"sort={s} ändrade mängden"
    return total


def test_price_changes_real_changes_and_sort():
    """Prisändrings-loggen: varje rad är en FAKTISK ändring (prev != price). Sort-lägen ger
    samma mängd, annan ordning. Chain-filter respekteras."""
    rows = database.catalog_price_changes(limit=200)
    for r in rows:
        assert r["prev_price"] is not None and r["prev_price"] != r["price"]
    # abs_desc verkligen fallande |diff|, abs_asc stigande (ordningskontroll, ej set under paginering)
    a = database.catalog_price_changes(sort="abs_desc", limit=200)
    diffs = [abs(r["price"] - r["prev_price"]) for r in a]
    assert diffs == sorted(diffs, reverse=True), "abs_desc inte fallande"
    b = database.catalog_price_changes(sort="abs_asc", limit=200)
    bdiffs = [abs(r["price"] - r["prev_price"]) for r in b]
    assert bdiffs == sorted(bdiffs), "abs_asc inte stigande"
    ica = database.catalog_price_changes(chain="ica", limit=50)
    assert all(r["chain"] == "ica" for r in ica)
    return len(rows)


def test_price_history_ordering():
    """offer- + hyllpris-historik: punkter tidsordnade per kedja, hyllpris kollapsad på lika pris."""
    conn = database.get_conn()
    row = conn.execute(
        "SELECT ean FROM catalog_price_observations WHERE ean IS NOT NULL AND ean!='' "
        "GROUP BY ean HAVING COUNT(*)>1 LIMIT 1").fetchone()
    conn.close()
    if not row:
        return 0
    ean = row[0]
    shelf = database.catalog_price_history(ean)
    for c in shelf:
        ts = [p["observed_at"] for p in c["points"]]
        assert ts == sorted(ts), "hyllpris-punkter inte tidsordnade"
        prices = [p["price"] for p in c["points"]]
        for i in range(1, len(prices)):
            assert prices[i] != prices[i - 1], "lika pris i rad ej kollapsat"
    hist = database.price_history(ean)
    assert set(hist) >= {"ean", "name", "chains"}
    return len(shelf)


def test_manufacturer_normalization():
    """Varianter (skiftläge/legal-suffix) ska få SAMMA nyckel; canonical idempotent på sin output."""
    same = [
        ("Itigo AB", "ITIGO AB", "Itigo"),
        ("Dr. Oetker", "Dr.Oetker", "Dr Oetker", "Dr. Oetker Sverige AB"),
        ("Head & Shoulders", "HEAD & SHOULDERS", "Head&Shoulders"),
    ]
    for grp in same:
        keys = {manufacturers.manufacturer_key(b) for b in grp}
        assert len(keys) == 1, f"{grp} -> flera nycklar {keys}"
    # "Foods"/"Group" ska INTE strippas (del av namnet)
    assert manufacturers.manufacturer_key("Dava Foods") != manufacturers.manufacturer_key("Dava")
    # canonical idempotent: canonical(canonical(x)) stabil
    for b in ("Arla Foods AB", "ITIGO AB", "Garant"):
        c1 = manufacturers.canonical(b)
        assert manufacturers.canonical(c1) == c1, f"canonical ej idempotent för {b!r}"
    return len(same)


def test_split_origins():
    """Ursprungsnormalisering: EN->SV, fleruländer, skräp/fiskeområde -> None, idempotent."""
    assert countries.split_origins("Sweden") == ("Sverige", ["SE"])
    assert countries.split_origins("Sverige, Norge") == ("Sverige, Norge", ["SE", "NO"])
    name, codes = countries.split_origins("EU/Marocko")
    assert codes == ["EU", "MA"]
    assert countries.split_origins("Nordostatlanten") == ("Nordostatlanten", [])
    assert countries.split_origins(None) == (None, [])
    # skräp (för långt) -> None
    assert countries.split_origins("Kina " + "x" * 80)[0] is None
    # idempotent på normaliserat namn
    n1, _ = countries.split_origins("Sweden")
    assert countries.split_origins(n1)[0] == "Sverige"
    return True


def test_get_product_diets_and_origins():
    """Batch-derivering: diets ger giltiga värden; origins ger (namnlista, koder)-par."""
    dmap = database.get_product_diets()
    assert dmap, "inga diets - är product_info populerad?"
    assert set(dmap.values()) <= {"vegan", "vegetarian", "none"}
    eans = list(dmap)[:50]
    omap = database.get_product_origins(eans)
    for names, codes in omap.values():
        assert isinstance(names, list) and isinstance(codes, list) and codes
    return len(dmap), len(omap)


if __name__ == "__main__":
    print(f"OK: classify_diet ({test_classify_diet()} fall)")
    print(f"OK: catalog_browse paginering/filter (total={test_catalog_browse_pagination_and_filters()})")
    print(f"OK: catalog_browse sort bevarar mängd ({test_catalog_browse_sort_preserves_set()} produkter)")
    print(f"OK: prisändringar faktiska + sort ({test_price_changes_real_changes_and_sort()} rader)")
    print(f"OK: prishistorik-ordning ({test_price_history_ordering()} hyllpris-serier)")
    print(f"OK: tillverkar-normalisering ({test_manufacturer_normalization()} grupper)")
    print(f"OK: split_origins ({test_split_origins()})")
    d, o = test_get_product_diets_and_origins()
    print(f"OK: get_product_diets/origins ({d} diets, {o} origins)")
