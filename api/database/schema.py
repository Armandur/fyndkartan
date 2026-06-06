"""Schema-init: DB-oberoende via SQLAlchemy Core (tabelldefinitioner i tables.py).

`create_schema()` = `metadata.create_all()` (idempotent, bygger tabeller + index på SQLite/PG).
`seed()` = idempotenta default-vokabulärer (ON CONFLICT DO NOTHING). `init_db()` = båda (normal
uppstart / fresh deploy). Migrerings-skriptet kör `create_schema()` + bulk-kopia (utan seed -
datan bär med sig vokabulären). De gamla ALTER-guards / DROP-migreringar / engångsbackfills var
SQLite-in-place-migrerings-ställningar och är borttagna: create_all bygger hela nuvarande schemat
i ett svep, och migrerad/befintlig data har redan backfillen."""
from sqlalchemy import text

from ._conn import dialect_name, get_conn, get_engine
from .tables import metadata
from ..config import (
    BUILTIN_TAG_TYPES, DB_PATH, DEFAULT_CATEGORY_MAP, DEFAULT_PRIVATE_BRANDS,
    DEFAULT_PROVIDERS, DEFAULT_TAG_TYPES,
)


def create_schema():
    """Bygg schemat (tabeller + index) på måldialekten. Idempotent (skippar befintliga tabeller)."""
    eng = get_engine()
    metadata.create_all(eng)
    if dialect_name() == "sqlite":
        with eng.connect() as conn:
            conn.exec_driver_sql("PRAGMA journal_mode=WAL")


def seed():
    """Idempotenta default-vokabulärer (kör alltid; ON CONFLICT DO NOTHING -> nya seed-nycklar läggs
    till vid uppgradering utan att skriva över admin-ändringar)."""
    conn = get_conn()
    # Kategori-mappning: alltid (nya seed-nycklar t.ex. coop_nav läggs till).
    conn.executemany(
        text("INSERT INTO category_map (chain_key, raw_key, canonical) VALUES (:ck, :rk, :canon) "
             "ON CONFLICT DO NOTHING"),
        [{"ck": ck, "rk": rk, "canon": canon} for (ck, rk), canon in DEFAULT_CATEGORY_MAP.items()])
    # Tagg-typer: seeda default-listan om tom; säkerställ sedan inbyggda (utom användar-borttagna).
    removed = {r[0] for r in conn.execute(text("SELECT type FROM tag_types_removed"))}
    if not conn.execute(text("SELECT 1 FROM tag_types LIMIT 1")).fetchone():
        conn.executemany(text("INSERT INTO tag_types (type) VALUES (:t) ON CONFLICT DO NOTHING"),
                         [{"t": t} for t in DEFAULT_TAG_TYPES])
    conn.executemany(text("INSERT INTO tag_types (type) VALUES (:t) ON CONFLICT DO NOTHING"),
                     [{"t": t} for t in BUILTIN_TAG_TYPES if t not in removed])
    # Speditörer: seeda en gång.
    if not conn.execute(text("SELECT 1 FROM providers LIMIT 1")).fetchone():
        conn.executemany(text("INSERT INTO providers (name) VALUES (:n) ON CONFLICT DO NOTHING"),
                         [{"n": p} for p in DEFAULT_PROVIDERS])
    # Private-label-vokabulär: seeda en gång.
    if not conn.execute(text("SELECT 1 FROM private_brands LIMIT 1")).fetchone():
        conn.executemany(text("INSERT INTO private_brands (chain, brand) VALUES (:c, :b) "
                              "ON CONFLICT DO NOTHING"),
                         [{"c": ch, "b": b} for ch, bs in DEFAULT_PRIVATE_BRANDS.items() for b in bs])
    conn.commit()
    conn.close()


def init_db():
    if dialect_name() == "sqlite":
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    create_schema()
    seed()
