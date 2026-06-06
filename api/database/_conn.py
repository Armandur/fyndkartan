"""DB-anslutning via SQLAlchemy Core (DB-oberoende brygga mot Postgres-bytet).

`get_conn()` ger en tunn shim ovanpå en SQLAlchemy-Connection som beter sig som den
gamla `sqlite3`-anslutningen: `execute(sql, params)` med BÅDE qmark (`?`-tupler) och
namngivna (`:name`-dictar) placeholders via `exec_driver_sql` (pysqlite native paramstyle),
rader som indexeras med både `r[0]` och `r["kol"]`, `.lastrowid`/`.rowcount`, `commit()`/
`close()`. Rå SQL körs alltså oförändrad genom shimmen modul för modul tills den
Core-ifieras. URL ur env `DATABASE_URL` (default lokal sqlite-fil)."""

import os

from sqlalchemy import URL, create_engine, event
from sqlalchemy.pool import NullPool

from ..config import DB_PATH

DATABASE_URL = os.getenv("DATABASE_URL") or URL.create("sqlite", database=str(DB_PATH))

_engine = None


def get_engine():
    global _engine
    if _engine is None:
        url = DATABASE_URL
        is_sqlite = str(url).startswith("sqlite")
        kwargs = {}
        if is_sqlite:
            # NullPool = ny connection per get_conn(), stängs vid close() - exakt som gamla
            # sqlite3-mönstret (ingen delad pool-connection över trådar, ingen pool-svält i crawl).
            kwargs["connect_args"] = {"check_same_thread": False}
            kwargs["poolclass"] = NullPool
        _engine = create_engine(url, **kwargs)
        if is_sqlite:

            @event.listens_for(_engine, "connect")
            def _set_pragma(dbapi_conn, _rec):  # noqa: ANN001
                cur = dbapi_conn.cursor()
                cur.execute("PRAGMA busy_timeout=3000")  # vänta i st.f. att fela vid samtidig skrivning
                cur.close()

    return _engine


class _Row:
    """Rad som stödjer både r[0] (positionell), r["kol"] (nyckel) och dict(r)."""

    __slots__ = ("_r",)

    def __init__(self, row):
        self._r = row

    def __getitem__(self, k):
        if isinstance(k, (int, slice)):
            return self._r[k]
        return self._r._mapping[k]

    def __contains__(self, k):
        return k in self._r._mapping

    def keys(self):
        return list(self._r._mapping.keys())

    def __iter__(self):
        return iter(self._r)  # sqlite3.Row-semantik: iterera över värden

    def __len__(self):
        return len(self._r)


class _Result:
    __slots__ = ("_res",)

    def __init__(self, res):
        self._res = res

    def fetchone(self):
        row = self._res.fetchone()
        return _Row(row) if row is not None else None

    def fetchall(self):
        return [_Row(r) for r in self._res.fetchall()]

    def fetchmany(self, size=None):
        rows = self._res.fetchmany() if size is None else self._res.fetchmany(size)
        return [_Row(r) for r in rows]

    def __iter__(self):
        for r in self._res:
            yield _Row(r)

    @property
    def lastrowid(self):
        return self._res.lastrowid

    @property
    def rowcount(self):
        return self._res.rowcount


class _NoResult:
    """Tom resultat-stand-in för no-op executemany (tom sekvens)."""

    lastrowid = None
    rowcount = 0

    def fetchone(self):
        return None

    def fetchall(self):
        return []

    def fetchmany(self, size=None):
        return []

    def __iter__(self):
        return iter(())


class _Conn:
    """sqlite3-liknande connection-shim ovanpå en SQLAlchemy-Connection."""

    __slots__ = ("_c",)

    def __init__(self, conn):
        self._c = conn

    def execute(self, sql, params=None):
        # Konverterade moduler skickar text()/Core-uttryck (named params -> dialekt-portabelt).
        if not isinstance(sql, str):
            return _Result(self._c.execute(sql, params or {}))
        # Transitionell väg: rå SQL-sträng körs oförändrad via pysqlite native paramstyle.
        if params is None:
            return _Result(self._c.exec_driver_sql(sql))
        # list-params (t.ex. IN (...)-koder) = EN qmark-uppsättning, inte executemany.
        # exec_driver_sql tolkar topp-nivå-list som multiparams -> coerce till tuple.
        if isinstance(params, list):
            params = tuple(params)
        return _Result(self._c.exec_driver_sql(sql, params))

    def executemany(self, sql, seq):
        seq = list(seq)
        if not seq:
            return _NoResult()
        return _Result(self._c.exec_driver_sql(sql, seq))

    def commit(self):
        self._c.commit()

    def rollback(self):
        self._c.rollback()

    def close(self):
        self._c.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def get_conn():
    return _Conn(get_engine().connect())


def _ensure_column(conn, table, col, coltype):
    cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
    if col not in cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {coltype}")


def _now():
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
