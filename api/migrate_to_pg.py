"""Engångs-migrering SQLite -> Postgres (Fas B). Bulk-kopierar alla tabeller och nollställer
SERIAL-sekvenserna. Datan är regenererbar UTOM tidsserie-historiken (catalog_price_observations,
offer_observations, product_info_observations) som byggs upp över tid -> kopiera ALLT.

Kör:  DATABASE_URL=postgresql+psycopg://... .venv/bin/python -m api.migrate_to_pg
Käll-SQLite tas ur config.DB_PATH. Dest-PG ur DATABASE_URL (env). Skapar schemat (create_all)
på dest om det saknas. Idempotent per tabell endast om dest-tabellen är tom (annars PK-krockar)
-> kör mot tom PG."""
import os
import sys
import time

from sqlalchemy import URL, create_engine, text

from .config import DB_PATH
from .database.tables import SERIAL_TABLES, metadata

CHUNK = 5000


def _src_engine():
    return create_engine(URL.create("sqlite", database=str(DB_PATH)),
                         connect_args={"check_same_thread": False})


def _dst_engine():
    url = os.environ.get("DATABASE_URL")
    if not url or url.startswith("sqlite"):
        sys.exit("Sätt DATABASE_URL till Postgres-URL:en (postgresql+psycopg://...).")
    return create_engine(url, pool_pre_ping=True)


def _copy_table(src, dst, table):
    cols = [c.name for c in table.columns]
    collist = ", ".join(cols)
    placeholders = ", ".join(f":{c}" for c in cols)
    ins = text(f"INSERT INTO {table.name} ({collist}) VALUES ({placeholders})")
    total = src.exec_driver_sql(f"SELECT COUNT(*) FROM {table.name}").fetchone()[0]
    if not total:
        return 0
    result = src.exec_driver_sql(f"SELECT {collist} FROM {table.name}")
    copied = 0
    while True:
        batch = result.fetchmany(CHUNK)
        if not batch:
            break
        rows = [dict(r._mapping) for r in batch]
        try:
            dst.execute(ins, rows)
            dst.commit()
        except Exception as e:  # noqa: BLE001
            dst.rollback()
            sys.exit(f"FEL i {table.name} efter {copied} rader: {type(e).__name__}: {e}")
        copied += len(rows)
        print(f"  {table.name}: {copied}/{total}", end="\r", flush=True)
    print(f"  {table.name}: {copied}/{total} klart")
    return copied


def _reset_sequences(dst):
    """Nollställ SERIAL-sekvenserna så nästa insert inte krockar med migrerade explicit-id:n."""
    for t in SERIAL_TABLES:
        dst.execute(text(
            f"SELECT setval(pg_get_serial_sequence('{t}', 'id'), "
            f"COALESCE((SELECT MAX(id) FROM {t}), 1))"))
    dst.commit()


def main():
    src_eng, dst_eng = _src_engine(), _dst_engine()
    print(f"Skapar schema på dest ({dst_eng.url.host}/{dst_eng.url.database})...")
    metadata.create_all(dst_eng)
    t0 = time.time()
    grand = 0
    with src_eng.connect() as src, dst_eng.connect() as dst:
        for table in metadata.sorted_tables:  # ingen FK -> ordning oviktig
            grand += _copy_table(src, dst, table)
        print("Nollställer SERIAL-sekvenser...")
        _reset_sequences(dst)
        print("ANALYZE (planerar-statistik; annars kall/seq-scan tills autovacuum hinner)...")
        dst.exec_driver_sql("ANALYZE")
        dst.commit()
    print(f"KLART: {grand} rader på {time.time() - t0:.0f}s")


if __name__ == "__main__":
    main()
