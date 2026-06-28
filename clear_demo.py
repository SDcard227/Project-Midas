"""Wipe all demo/seed/test data before a real public launch.

Removes every *@midas.fake account (demo_, test_, pit_, etc.) and everything they
created: comments, votes, room messages, room plays, reputation, holdings. The four
default chat rooms stay (structure); only the fake chatter is cleared.

    python clear_demo.py          # show what would be cleared
    python clear_demo.py --yes    # actually clear it
"""
import os
import sys
import sqlite3

DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "midas_users.db")
GO = "--yes" in sys.argv

con = sqlite3.connect(DB)
con.row_factory = sqlite3.Row


def has_table(t):
    return con.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?",
                       (t,)).fetchone() is not None


ids = [r[0] for r in con.execute(
    "SELECT id FROM users WHERE email LIKE '%@midas.fake'").fetchall()]
print("Fake/test accounts found: %d" % len(ids))

if not ids:
    print("Nothing to clear."); con.close(); raise SystemExit

if not GO:
    print("\nThis WOULD delete those users + their comments, votes, room messages,")
    print("room plays, reputation, and holdings. Re-run with --yes to do it.")
    con.close(); raise SystemExit

qs = ",".join("?" * len(ids))
for tbl, col in [("comments", "user_id"), ("comment_votes", "user_id"),
                 ("room_messages", "user_id"), ("room_plays", "user_id"),
                 ("user_rep", "user_id"), ("holdings", "user_id")]:
    if has_table(tbl):
        n = con.execute(f"DELETE FROM {tbl} WHERE {col} IN ({qs})", ids).rowcount
        print("  %-16s -%d" % (tbl, n))
con.execute(f"DELETE FROM users WHERE id IN ({qs})", ids)
con.commit()
con.execute("VACUUM")
con.commit()
con.close()
print("Done. Demo/test data cleared. The platform is now clean for real users.")
