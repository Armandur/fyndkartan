"""Drift-test för responsmodellerna (api/schemas.py).

Modellerna kopplas DOKUMENTERANDE till routes (`responses={200: {"model": M}}`) och
enforcar därför inte i runtime. Det här testet validerar verkliga svar mot modellerna
så att drift mellan modell och faktisk data fångas. Kräver en populerad stores.db.

Kör: `.venv/bin/python tests/test_schemas.py` (eller via pytest om det installeras).
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from api import database, schemas  # noqa: E402


def test_product_matches_model():
    """list_products-poster ska ha exakt Product-modellens fält (båda riktningar) och
    validera mot modellen, sett över flera kedjor och deal-typer."""
    model_fields = set(schemas.Product.model_fields)
    seen_chains, seen_deals, n = set(), set(), 0
    for q in ("mjölk", "ost", "kyckling", "kaffe", "choklad", "bröd"):
        for p in database.list_products(q=q, limit=80):
            schemas.Product.model_validate(p)  # kastar vid typ-/saknadsdrift
            assert set(p.keys()) == model_fields, (
                f"Fält-drift: svar={sorted(p.keys())} modell={sorted(model_fields)}"
            )
            seen_chains.update(p["chains"])
            seen_deals.add(p["deal_type"])
            n += 1
    assert n > 0, "Inga produkter att validera - är stores.db populerad?"
    assert {"ica", "coop", "willys", "hemkop"} & seen_chains, f"Få kedjor täckta: {seen_chains}"
    assert {"flat", "multibuy", "by_weight"} & seen_deals, f"Få deal-typer täckta: {seen_deals}"
    return n, sorted(seen_chains), sorted(seen_deals)


def _validate_all(items, model):
    """Validera varje post mot modellen + säkerställ att inget modellfält är ett
    fantomfält (måste synas i minst en verklig post). Returnerar antal validerade."""
    seen, n = set(), 0
    for it in items:
        model.model_validate(it)
        seen |= set(it.keys())
        n += 1
    phantom = set(model.model_fields) - seen
    assert not phantom, f"{model.__name__}: modellfält som aldrig syns i data: {sorted(phantom)}"
    return n


def test_store_matches_model():
    """Butiks-svar (row_to_store) ska validera mot Store, sett över alla kedjor."""
    conn = database.get_conn()
    rows = []
    for ch in ("ica", "coop", "willys", "hemkop", "lidl"):
        rows += conn.execute("SELECT * FROM stores WHERE chain=? LIMIT 40", (ch,)).fetchall()
    conn.close()
    stores = [database.row_to_store(r) for r in rows]
    n = _validate_all(stores, schemas.Store)
    assert n > 0, "Inga butiker - är stores.db populerad?"
    return n


def test_offer_matches_model():
    """Erbjudande-svar (get_store_offers) ska validera mot Offer, sett över kedjor."""
    conn = database.get_conn()
    pairs = conn.execute(
        "SELECT chain, store_id, COUNT(*) c FROM offers GROUP BY chain, store_id "
        "ORDER BY c DESC LIMIT 8"
    ).fetchall()
    conn.close()
    offers = []
    for p in pairs:
        offers += database.get_store_offers(p["chain"], p["store_id"])
    n = _validate_all(offers, schemas.Offer)
    assert n > 0, "Inga erbjudanden i cachen att validera."
    return n


def test_price_history_matches_model():
    """Prishistorik-svar (price_history) ska validera mot PriceHistoryResponse, för EAN:er
    med flest observationer. Tom om inga arkiverats än - då hoppas testet."""
    conn = database.get_conn()
    eans = [r["ean"] for r in conn.execute(
        "SELECT ean FROM offer_observations WHERE ean IS NOT NULL "
        "GROUP BY ean ORDER BY COUNT(*) DESC LIMIT 10"
    ).fetchall()]
    conn.close()
    if not eans:
        return 0
    for e in eans:
        schemas.PriceHistoryResponse.model_validate(database.price_history(e))
    return len(eans)


def test_product_stores_matches_model():
    """stores_with_offer-svar ska validera mot ProductStoresResponse, för EAN:er som har
    erbjudanden i cachen. Tom om inga offers -> hoppas."""
    conn = database.get_conn()
    eans = [r["ean"] for r in conn.execute(
        "SELECT je.value AS ean FROM offers, json_each(offers.eans) je "
        "WHERE offers.eans NOT IN ('','[]') GROUP BY je.value LIMIT 5"
    ).fetchall()]
    conn.close()
    if not eans:
        return 0
    for e in eans:
        stores = database.stores_with_offer(e)
        schemas.ProductStoresResponse.model_validate({"ean": e, "count": len(stores), "stores": stores})
    return len(eans)


if __name__ == "__main__":
    n, chains, deals = test_product_matches_model()
    print(f"OK: {n} produkter validerade mot Product | kedjor={chains} | deal_types={deals}")
    print(f"OK: {test_store_matches_model()} butiker validerade mot Store")
    print(f"OK: {test_offer_matches_model()} erbjudanden validerade mot Offer")
    print(f"OK: {test_price_history_matches_model()} prishistorik-EAN validerade mot PriceHistoryResponse")
    print(f"OK: {test_product_stores_matches_model()} EAN validerade mot ProductStoresResponse")
