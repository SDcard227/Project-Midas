"""
DB layer: SQLite by default, Postgres when DATABASE_URL is set.

WHY: SQLite lives on Render's ephemeral disk (wiped every deploy). For real users
the data must persist — either a persistent disk (set DB_PATH) or a Postgres
(set DATABASE_URL). This module routes connections to the right one.

DESIGN: `get_conn()` returns a connection that behaves like sqlite3 either way, so
the brain modules don't change their query style. For Postgres a thin wrapper:
  - translates `?` placeholders to `%s`
  - translates SQLite DDL (`INTEGER PRIMARY KEY AUTOINCREMENT` -> `SERIAL PRIMARY KEY`)
  - translates `PRAGMA table_info(t)` -> information_schema (for the column migrations)
  - appends `RETURNING id` to INSERTs so `.lastrowid` works
  - returns dict rows (so `row["col"]` works like sqlite3.Row)
  - commits + closes on `with` exit (no connection leaks)

SAFETY: with no DATABASE_URL this is plain sqlite3, byte-for-byte as before. The
Postgres path is written to the SQL-dialect rules and its translation is unit-tested,
but EXECUTE IT AGAINST A REAL POSTGRES IN STAGING before trusting prod — psycopg2
behavior can only be fully confirmed against a live database.
"""
import os
import re
import sqlite3

_SQLITE_PATH = os.getenv("DB_PATH") or os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "midas_users.db")


def _is_pg():
    return os.getenv("DATABASE_URL", "").startswith(("postgres://", "postgresql://"))


# IntegrityError as a tuple so `except db.IntegrityError` catches either backend.
IntegrityError = (sqlite3.IntegrityError,)
try:
    import psycopg2 as _pg
    IntegrityError = IntegrityError + (_pg.IntegrityError,)
except Exception:
    _pg = None


def translate(sql):
    """SQLite SQL -> Postgres SQL. Pure function so it can be unit-tested."""
    m = re.match(r"\s*PRAGMA\s+table_info\((\w+)\)\s*;?\s*$", sql, re.I)
    if m:
        return ("SELECT column_name AS name FROM information_schema.columns "
                "WHERE table_name = %s", m.group(1).lower())
    s = sql.replace("INTEGER PRIMARY KEY AUTOINCREMENT", "SERIAL PRIMARY KEY")
    s = s.replace("AUTOINCREMENT", "")
    s = s.replace("?", "%s")
    if s.lstrip()[:6].upper() == "INSERT" and "RETURNING" not in s.upper():
        s = s.rstrip().rstrip(";") + " RETURNING id"
    return (s, None)


class _PgCursor:
    def __init__(self, cur, lastid):
        self._cur = cur
        self.lastrowid = lastid

    def fetchone(self):
        return self._cur.fetchone()

    def fetchall(self):
        return self._cur.fetchall()

    @property
    def rowcount(self):
        return self._cur.rowcount


class _PgConn:
    """Wraps a psycopg2 connection to look enough like sqlite3 for the modules."""
    def __init__(self, raw):
        self._raw = raw

    def __enter__(self):
        return self

    def __exit__(self, et, ev, tb):
        if et:
            self._raw.rollback()
        else:
            self._raw.commit()
        self._raw.close()
        return False

    def execute(self, sql, params=()):
        import psycopg2.extras
        cur = self._raw.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        s, pragma_param = translate(sql)
        if pragma_param is not None:                  # PRAGMA -> information_schema
            cur.execute(s, (pragma_param,))
            return _PgCursor(cur, None)
        is_insert = s.lstrip()[:6].upper() == "INSERT" and s.rstrip().upper().endswith("RETURNING ID")
        cur.execute(s, params)
        lastid = None
        if is_insert:
            try:
                lastid = cur.fetchone()["id"]
            except Exception:
                lastid = None
        return _PgCursor(cur, lastid)

    def cursor(self):
        import psycopg2.extras
        return self._raw.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    def commit(self):
        self._raw.commit()

    def close(self):
        self._raw.close()


def get_conn():
    """A DB connection. Postgres if DATABASE_URL is set, else SQLite (unchanged)."""
    if _is_pg():
        import psycopg2
        url = os.getenv("DATABASE_URL", "").replace("postgres://", "postgresql://", 1)
        return _PgConn(psycopg2.connect(url))
    c = sqlite3.connect(_SQLITE_PATH)
    c.row_factory = sqlite3.Row
    return c
