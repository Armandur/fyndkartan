"""Invariant-tester för cross-chain-jämförelsen (matching.build_comparisons).

Rena funktions-tester med SYNTETISKA offer-entries (ingen DB) -> deterministiska, snabba.
Täcker grupperingen, min_chains/min_stores-grindarna, per-butik-dedup, unit_price-vs-price och
manual_groups-sammanslagningen (märkesvaror med olika EAN).

Kör: `.venv/bin/python tests/test_compare.py` (eller pytest).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from api import matching  # noqa: E402

# Två giltiga 13-siffriga EAN (icke 2-prefix) som normalize_ean accepterar.
E1, E2 = "5019503048353", "7311041000017"


def _offer(chain, store, ean, price, cv=None, unit=None, name="Vara"):
    return {"chain": chain, "store_id": store, "eans": [ean], "price": price,
            "comparison_value": cv, "comparison_unit": unit, "name": name}


def test_eans_valid():
    assert matching.normalize_ean(E1) and matching.normalize_ean(E2), "test-EAN normaliserar till None"
    return True


def test_min_chains_gate():
    """Produkt hos bara EN kedja (även flera butiker) faller bort när min_chains=2; tas med vid 1."""
    one_chain = [_offer("ica", "s1", E1, 10), _offer("ica", "s2", E1, 12)]
    assert build_count(one_chain, min_chains=2) == 0
    assert build_count(one_chain, min_chains=1, min_stores=2) == 1  # 1 kedja, 2 butiker
    two_chains = [_offer("ica", "s1", E1, 10), _offer("coop", "s2", E1, 12)]
    out = matching.build_comparisons(two_chains, min_chains=2)
    assert len(out) == 1 and out[0]["chains"] == 2
    return True


def test_spread_and_minmax():
    """spread = max - min på det jämförda värdet; min/max korrekt."""
    entries = [_offer("ica", "s1", E1, 10), _offer("coop", "s2", E1, 15), _offer("willys", "s3", E1, 12)]
    out = matching.build_comparisons(entries, min_chains=2)[0]
    assert out["min"] == 10 and out["max"] == 15 and round(out["spread"], 2) == 5.0
    assert out["compare_by"] == "price" and out["chains"] == 3 and out["stores"] == 3
    return True


def test_per_store_dedup_cheapest():
    """Samma butik två gånger -> en post (lägsta pris). Påverkar inte butiks-/kedjeräkningen fel."""
    entries = [_offer("ica", "s1", E1, 20), _offer("ica", "s1", E1, 9),  # samma butik, billigast 9
               _offer("coop", "s2", E1, 15)]
    out = matching.build_comparisons(entries, min_chains=2)[0]
    assert out["stores"] == 2 and out["min"] == 9
    return True


def test_unit_price_vs_price():
    """Jämför på enhetspris när ALLA har comparison_value + samma enhet, annars råpris."""
    unit_all = [_offer("ica", "s1", E1, 20, cv=80, unit="kg"), _offer("coop", "s2", E1, 25, cv=100, unit="kg")]
    assert matching.build_comparisons(unit_all, min_chains=2)[0]["compare_by"] == "unit_price"
    mixed = [_offer("ica", "s1", E1, 20, cv=80, unit="kg"), _offer("coop", "s2", E1, 25)]  # en saknar cv
    assert matching.build_comparisons(mixed, min_chains=2)[0]["compare_by"] == "price"
    diff_unit = [_offer("ica", "s1", E1, 20, cv=80, unit="kg"), _offer("coop", "s2", E1, 25, cv=5, unit="st")]
    assert matching.build_comparisons(diff_unit, min_chains=2)[0]["compare_by"] == "price"
    return True


def test_manual_group_merges_different_eans():
    """Märkesvaror med OLIKA EAN i samma manuella grupp slås ihop till EN jämförelse (manual=True)."""
    entries = [_offer("ica", "s1", E1, 10, name="ICA Krossade Tomater"),
               _offer("coop", "s2", E2, 12, name="Änglamark Tomatkross")]
    # utan manual: olika EAN -> två separata grupper, var och en bara 1 kedja -> inget med min_chains=2
    assert build_count(entries, min_chains=2) == 0
    e1n, e2n = matching.normalize_ean(E1), matching.normalize_ean(E2)
    out = matching.build_comparisons(entries, min_chains=2, manual_groups={e1n: 7, e2n: 7})
    assert len(out) == 1 and out[0]["manual"] is True and out[0]["match_group"] == 7
    assert out[0]["chains"] == 2
    return True


def build_count(entries, **kw):
    return len(matching.build_comparisons(entries, **kw))


if __name__ == "__main__":
    test_eans_valid()
    for fn in (test_min_chains_gate, test_spread_and_minmax, test_per_store_dedup_cheapest,
               test_unit_price_vs_price, test_manual_group_merges_different_eans):
        fn()
        print(f"OK: {fn.__name__}")
