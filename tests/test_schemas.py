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


if __name__ == "__main__":
    n, chains, deals = test_product_matches_model()
    print(f"OK: {n} produkter validerade mot Product | kedjor={chains} | deal_types={deals}")
