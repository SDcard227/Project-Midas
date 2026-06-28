"""
DB seam for the SQLite -> Postgres migration.

WHY THIS EXISTS
Today every brain module (accounts, comments, reputation, chat) calls sqlite3
directly against a local file. That file lives on Render's EPHEMERAL disk, so it is
WIPED on every deploy/restart — fine for a demo, fatal for real users. Before a real
public launch the data must move to a persistent Postgres.

This module is the single seam for that move:
  - DATABASE_URL set (postgres://...)  -> connections route to Postgres
  - otherwise                          -> SQLite, exactly as today

IT IS NOT YET WIRED INTO THE MODULES, on purpose, so the working SQLite path can't
break. Wiring it in is the migration task.

MIGRATION CHECKLIST (do this against a real Postgres instance):
  1. `pip install psycopg2-binary` and add it to requirements.txt.
  2. Point each brain module's `_conn()` at `get_conn()` here.
  3. SQL dialect fixes:
       - `INTEGER PRIMARY KEY AUTOINCREMENT` -> `SERIAL PRIMARY KEY` (or IDENTITY)
       - parameter placeholders `?` -> `%s`
       - `cur.lastrowid` -> `INSERT ... RETURNING id`
       - `INTEGER` booleans are fine; or use real BOOLEAN
       - `INSERT OR IGNORE` / `UNIQUE` upserts -> `ON CONFLICT ... DO ...`
  4. Run each module's CREATE TABLE statements once to init the schema.
  5. Test every flow: register/login, comments+votes, rooms+messages+plays, reputation.
  6. Optionally migrate existing rows with a one-off export/import.
"""
import os
import sqlite3

_SQLITE_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            "midas_users.db")


def is_postgres() -> bool:
    return os.getenv("DATABASE_URL", "").startswith(("postgres://", "postgresql://"))


def get_conn():
    """A DB connection. Postgres when DATABASE_URL is set, else SQLite.

    Callers still use sqlite3 directly for now; routing them through here is the
    migration step. Kept import-light so importing this never breaks the app.
    """
    url = os.getenv("DATABASE_URL", "")
    if url.startswith(("postgres://", "postgresql://")):
        import psycopg2
        import psycopg2.extras
        return psycopg2.connect(
            url.replace("postgres://", "postgresql://", 1),
            cursor_factory=psycopg2.extras.RealDictCursor,
        )
    conn = sqlite3.connect(_SQLITE_PATH)
    conn.row_factory = sqlite3.Row
    return conn
