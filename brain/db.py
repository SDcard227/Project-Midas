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


# Tables whose primary key is NOT a column named `id`, so `RETURNING id` is invalid.
_NO_ID_TABLES = {"user_rep", "parlor_wallet", "comment_votes", "follows", "dm_blocks",
                 "exch_holdings", "exch_stats", "exch_prices"}
# For `INSERT OR REPLACE`, the conflict column to upsert on (Postgres ON CONFLICT).
_UPSERT_CONFLICT = {"user_rep": "user_id"}


def _insert_table(s):
    m = re.match(r"\s*INSERT(?:\s+OR\s+\w+)?\s+INTO\s+(\w+)", s, re.I)
    return m.group(1).lower() if m else ""


def _pg_upsert(s):
    """'INSERT OR REPLACE/IGNORE INTO t (cols) ...' -> Postgres ON CONFLICT upsert.
    Returns the rewritten SQL, or None if `s` isn't an OR REPLACE/IGNORE insert."""
    m = re.match(r"\s*INSERT\s+OR\s+(REPLACE|IGNORE)\s+INTO\s+(\w+)\s*\(([^)]*)\)(.*)",
                 s, re.I | re.S)
    if not m:
        return None
    kind, table, cols, rest = m.group(1).upper(), m.group(2), m.group(3), m.group(4)
    base = f"INSERT INTO {table} ({cols}){rest}".rstrip().rstrip(";")
    conflict = _UPSERT_CONFLICT.get(table.lower())
    if kind == "IGNORE":
        return base + (f" ON CONFLICT ({conflict}) DO NOTHING" if conflict else " ON CONFLICT DO NOTHING")
    if not conflict:
        return None
    sets = ", ".join(f"{c.strip()}=EXCLUDED.{c.strip()}"
                     for c in cols.split(",") if c.strip() != conflict)
    return base + f" ON CONFLICT ({conflict}) DO UPDATE SET {sets}"


def translate(sql):
    """SQLite SQL -> Postgres SQL. Pure function so it can be unit-tested."""
    m = re.match(r"\s*PRAGMA\s+table_info\((\w+)\)\s*;?\s*$", sql, re.I)
    if m:
        return ("SELECT column_name AS name FROM information_schema.columns "
                "WHERE table_name = %s", m.group(1).lower())
    s = sql.replace("INTEGER PRIMARY KEY AUTOINCREMENT", "SERIAL PRIMARY KEY")
    s = s.replace("AUTOINCREMENT", "")
    up = _pg_upsert(s)                 # INSERT OR REPLACE/IGNORE -> ON CONFLICT
    if up is not None:
        s = up
    s = s.replace("?", "%s")
    # `RETURNING id` only for INSERTs into tables that actually have an `id` column,
    # and never on an upsert.
    if (s.lstrip()[:6].upper() == "INSERT" and "RETURNING" not in s.upper()
            and "ON CONFLICT" not in s.upper() and _insert_table(s) not in _NO_ID_TABLES):
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
    # timeout + WAL fix "database is locked" under gunicorn's threads + the daemons:
    # WAL lets readers run during a write, and busy_timeout makes a writer wait for the
    # lock instead of erroring instantly. check_same_thread=False is safe here because a
    # fresh connection is opened per call (and GC may close it on another thread).
    c = sqlite3.connect(_SQLITE_PATH, timeout=15, check_same_thread=False)
    c.row_factory = sqlite3.Row
    try:
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA busy_timeout=15000")
        c.execute("PRAGMA synchronous=NORMAL")
    except Exception:
        pass
    return c
