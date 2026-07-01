"""
Postgres-path verification for db.translate().

Guards the SQLite -> Postgres translation so a new query can't silently break the live
Postgres backend (Render). The translate() unit cases always run; the dialect sweep
(parses every static statement in the codebase as Postgres) runs only if sqlglot is
installed, so it's a no-op in prod but a real check in dev/CI.

Run:  python brain/test_pg_translate.py     (or pytest brain/test_pg_translate.py)
"""
import ast
import glob
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import brain.db as db


# ── translate() unit cases (no external deps) ────────────────────────────────
def test_placeholders():
    s, _ = db.translate("SELECT * FROM users WHERE id=?")
    assert s == "SELECT * FROM users WHERE id=%s", s


def test_autoincrement_to_serial():
    s, _ = db.translate("CREATE TABLE t (id INTEGER PRIMARY KEY AUTOINCREMENT, x TEXT)")
    assert "SERIAL PRIMARY KEY" in s and "AUTOINCREMENT" not in s, s


def test_pragma_to_information_schema():
    s, p = db.translate("PRAGMA table_info(users)")
    assert "information_schema.columns" in s and p == "users", (s, p)


def test_returning_id_on_plain_insert():
    s, _ = db.translate("INSERT INTO users (email) VALUES (?)")
    assert s.strip().upper().endswith("RETURNING ID"), s


def test_no_returning_on_no_id_tables():
    s, _ = db.translate("INSERT INTO follows (follower_id,followee_id) VALUES (?,?)")
    assert "RETURNING" not in s.upper(), s


def test_insert_or_replace_becomes_upsert():
    s, _ = db.translate("INSERT OR REPLACE INTO user_rep (user_id,score) VALUES (?,?)")
    assert "ON CONFLICT (user_id) DO UPDATE SET" in s and "OR REPLACE" not in s.upper(), s
    assert "RETURNING" not in s.upper(), s   # never RETURNING on an upsert


def test_insert_or_ignore_becomes_do_nothing():
    s, _ = db.translate("INSERT OR IGNORE INTO follows (a,b) VALUES (?,?)")
    assert "ON CONFLICT" in s and "DO NOTHING" in s, s


# ── dialect sweep: every static statement must parse as Postgres ──────────────
def _static_statements():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    files = sorted(glob.glob(os.path.join(root, "brain", "*.py")))
    server = os.path.join(root, "sim_server.py")
    if os.path.exists(server):
        files.append(server)
    kw = ("SELECT", "INSERT", "UPDATE", "DELETE", "CREATE", "ALTER", "PRAGMA")
    for f in files:
        try:
            tree = ast.parse(open(f, encoding="utf-8").read())
        except Exception:
            continue
        for n in ast.walk(tree):
            if (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                    and n.func.attr == "execute" and n.args):
                a = n.args[0]
                if isinstance(a, ast.Constant) and isinstance(a.value, str):   # pure-literal only
                    if any(k in a.value.upper() for k in kw):
                        yield os.path.basename(f), a.value


def test_static_sql_is_valid_postgres():
    try:
        import sqlglot
    except ImportError:
        return  # optional dev sweep
    bad = []
    for fname, sql in _static_statements():
        s, _ = db.translate(sql)
        if s.strip().upper().startswith("PRAGMA"):
            continue  # SQLite-only session config, never sent to Postgres
        try:
            sqlglot.parse_one(s.replace("%s", "NULL"), read="postgres")
        except Exception as e:
            bad.append((fname, sql[:60], str(e).splitlines()[0]))
    assert not bad, f"{len(bad)} statement(s) not valid Postgres: {bad}"


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    passed = 0
    for fn in fns:
        fn()
        passed += 1
        print(f"  ok  {fn.__name__}")
    n = sum(1 for _ in _static_statements())
    print(f"\n{passed}/{len(fns)} checks passed; dialect sweep covered {n} static statements.")
