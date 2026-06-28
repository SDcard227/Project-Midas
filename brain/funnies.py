"""
The Funnies — community comic/art submissions.

People submit a comic or artwork; it lands in a PENDING queue and only shows in the
Daily Funnies after an admin approves it (never auto-publish user images — that's how
you end up hosting things you really don't want to host).

Storage: files go to UPLOAD_DIR (set it to a persistent disk in prod, or move to S3
for scale — the ephemeral disk loses uploads on deploy). Metadata rides in the same
DB as everything else, through the db abstraction.
"""
import os
from datetime import datetime, timezone

from brain import db

UPLOAD_DIR = os.getenv("UPLOAD_DIR") or os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "uploads")


def _conn():
    return db.get_conn()


def init_db():
    with _conn() as c:
        c.execute("""CREATE TABLE IF NOT EXISTS funny_submissions (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id    INTEGER,
            author     TEXT,
            caption    TEXT,
            filename   TEXT NOT NULL,
            status     TEXT DEFAULT 'pending',
            created_at TEXT NOT NULL
        )""")


def submit(user, caption, filename):
    init_db()
    author = user.get("name") or "user"
    with _conn() as c:
        cur = c.execute(
            "INSERT INTO funny_submissions (user_id,author,caption,filename,status,created_at)"
            " VALUES (?,?,?,?,?,?)",
            (user["id"], author, (caption or "").strip()[:140], filename, "pending",
             datetime.now(timezone.utc).isoformat()))
    return {"ok": True, "id": cur.lastrowid,
            "message": "Submitted! It'll show in the Daily Funnies once a human approves it."}


def _rows(status, limit):
    init_db()
    with _conn() as c:
        rows = c.execute(
            "SELECT * FROM funny_submissions WHERE status=? ORDER BY id DESC LIMIT ?",
            (status, limit)).fetchall()
    return [{"id": r["id"], "author": r["author"], "caption": r["caption"],
             "filename": r["filename"], "created_at": r["created_at"]} for r in rows]


def list_featured(limit=40):
    return _rows("approved", limit)


def list_pending(limit=60):
    return _rows("pending", limit)


def pending_count():
    init_db()
    with _conn() as c:
        r = c.execute("SELECT COUNT(*) AS n FROM funny_submissions WHERE status='pending'").fetchone()
    return r["n"] if r else 0


def moderate(sub_id, action):
    status = "approved" if action == "approve" else "rejected"
    init_db()
    with _conn() as c:
        c.execute("UPDATE funny_submissions SET status=? WHERE id=?", (status, sub_id))
    return {"ok": True, "id": sub_id, "status": status}
