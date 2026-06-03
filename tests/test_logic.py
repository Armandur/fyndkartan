"""Enhetstester för affärslogiken som Steg 5 (och refaktoreringen i REVIEW.md) lutar sig mot.

Rena tester (normalize_ean, category_from_name, archive_offers-dedup) körs alltid; de som
behöver verklig data (stores_with_offer, offers_for_eans, price_history) hoppas om stores.db
saknar offers. Kör: `.venv/bin/python tests/test_logic.py`.
"""

import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from api import database, matching  # noqa: E402
from api.categories import category_from_name  # noqa: E402


def test_normalize_ean():
    cases = {
        "7350068291210": "7350068291210",        # 13 ren
        "07350068291210": "7350068291210",        # GTIN-14 med ledande 0 -> 13
        "  7350068291210 ": "7350068291210",      # skräptecken rensas
        "2090183100008": None,                    # 2-prefix (viktvara) rejektas
        "123": None,                              # ogiltig längd
        "": None,
        None: None,
    }
    for raw, want in cases.items():
        got = matching.normalize_ean(raw)
        assert got == want, f"normalize_ean({raw!r}) = {got!r}, väntade {want!r}"
    return len(cases)


def test_category_from_name():
    assert category_from_name("Gurka") == "frukt_gront"
    assert category_from_name("Banan Eko 1 kg") == "frukt_gront"   # helord bland skräp
    assert category_from_name("Salladslök Knippe") == "frukt_gront"
    assert category_from_name("Tomatketchup") is None              # delsträng, inte helord
    assert category_from_name("Krossade Tomater") is None          # 'tomater' ej i vokabulären
    assert category_from_name("") is None
    assert category_from_name(None) is None
    return 6


def test_archive_offers_dedup():
    """archive_offers ska skriva en observation vid prisändring och INGEN vid oförändrat pris."""
    conn = database.get_conn()
    conn.execute("DELETE FROM offer_observations WHERE chain='_test'")
    conn.commit()
    conn.close()

    def count():
        c = database.get_conn()
        n = c.execute("SELECT COUNT(*) FROM offer_observations WHERE chain='_test'").fetchone()[0]
        c.close()
        return n

    o = {"offer_id": "x1", "eans": ["7350068291210"], "name": "Test", "price": 20.0,
         "comparison_value": 20.0, "comparison_unit": "kg", "savings": 0, "member_price": 0,
         "valid_to": "2026-06-07"}
    database.archive_offers("_test", "s1", [o])
    assert count() == 1, "första arkiveringen ska ge 1 observation"
    database.archive_offers("_test", "s1", [o])
    assert count() == 1, "oförändrat pris ska INTE ge ny observation"
    o2 = dict(o, price=18.0)
    database.archive_offers("_test", "s1", [o2])
    assert count() == 2, "prisändring ska ge ny observation"

    conn = database.get_conn()
    conn.execute("DELETE FROM offer_observations WHERE chain='_test'")
    conn.commit()
    conn.close()
    return 3


def _has_offers():
    conn = database.get_conn()
    n = conn.execute("SELECT COUNT(*) FROM offers").fetchone()[0]
    conn.close()
    return n > 0


def test_stores_with_offer_invariants():
    """Varje returnerad butik ska finnas i stores-tabellen (nyckeln frontend filtrerar på) och
    bara en rad per butik (billigaste)."""
    if not _has_offers():
        return 0
    conn = sqlite3.connect(database.DB_PATH)
    conn.row_factory = sqlite3.Row
    eans = [r["ean"] for r in conn.execute(
        "SELECT ean FROM offer_eans GROUP BY ean ORDER BY COUNT(*) DESC LIMIT 5")]
    n = 0
    for ean in eans:
        res = database.stores_with_offer(ean)
        keys = [(s["chain"], s["store_id"]) for s in res]
        assert len(keys) == len(set(keys)), f"dubblett-butik för {ean}"
        for s in res[:10]:
            row = conn.execute("SELECT 1 FROM stores WHERE chain=? AND store_id=?",
                               (s["chain"], s["store_id"])).fetchone()
            assert row, f"{s['chain']}:{s['store_id']} saknas i stores"
        n += 1
    conn.close()
    return n


def test_price_history_axfood():
    """Prishistorik ska komma åt Axfood-observationer via ean_cache-reverse (annars tomt)."""
    if not _has_offers():
        return 0
    conn = sqlite3.connect(database.DB_PATH)
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT e.ean FROM ean_cache e JOIN offer_observations o ON o.offer_id=e.code "
        "AND o.chain IN ('willys','hemkop') WHERE e.ean LIKE '73%' GROUP BY e.ean LIMIT 1").fetchone()
    conn.close()
    if not row:
        return 0
    h = database.price_history(row["ean"])
    axfood = [c for c in h["chains"] if c["chain"] in ("willys", "hemkop")]
    assert axfood and any(c["points"] for c in axfood), "Axfood-historik ska komma med"
    return 1


if __name__ == "__main__":
    print(f"OK: normalize_ean ({test_normalize_ean()} fall)")
    print(f"OK: category_from_name ({test_category_from_name()} fall)")
    print(f"OK: archive_offers dedup ({test_archive_offers_dedup()} steg)")
    print(f"OK: stores_with_offer invarianter ({test_stores_with_offer_invariants()} EAN)")
    print(f"OK: price_history Axfood ({test_price_history_axfood()})")
