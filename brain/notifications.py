"""
MIDAS — notifications: in-app pings (no email needed). A nav bell shows the unread
count; the feed lists recent events. Pushed when someone DMs you, replies to your take,
@-mentions you, or your bet settles. Stored in the shared DB (SQLite now / Postgres later).
"""
from datetime import datetime, timezone

from brain import db


def _conn():
    return db.get_conn()


def _now():
    return datetime.now(timezone.utc).isoformat()


def init_db():
    with _conn() as c:
        c.execute("""CREATE TABLE IF NOT EXISTS notifications (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id    INTEGER NOT NULL,
            kind       TEXT,
            text       TEXT,
            link       TEXT,
            read       INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL
        )""")


def push(user_id, kind, text, link=""):
    """Drop a notification for a user. No-op on a missing user. Best-effort: never
    let a notification failure break the action that triggered it."""
    if not user_id:
        return
    try:
        init_db()
        with _conn() as c:
            c.execute("INSERT INTO notifications (user_id, kind, text, link, created_at)"
                      " VALUES (?,?,?,?,?)",
                      (user_id, (kind or "")[:24], (text or "")[:240], (link or "")[:160], _now()))
    except Exception:
        pass


def listing(user_id, limit=30):
    init_db()
    with _conn() as c:
        rows = c.execute("SELECT * FROM notifications WHERE user_id=? ORDER BY id DESC LIMIT ?",
                         (user_id, limit)).fetchall()
        unread = c.execute("SELECT COUNT(*) AS n FROM notifications WHERE user_id=? AND read=0",
                           (user_id,)).fetchone()["n"]
    return {"unread": unread,
            "items": [{"id": r["id"], "kind": r["kind"], "text": r["text"], "link": r["link"],
                       "read": bool(r["read"]), "created_at": r["created_at"]} for r in rows]}


def unread_count(user_id):
    init_db()
    with _conn() as c:
        return c.execute("SELECT COUNT(*) AS n FROM notifications WHERE user_id=? AND read=0",
                         (user_id,)).fetchone()["n"]


def mark_read(user_id):
    """Mark all of a user's notifications read (called when they open the bell)."""
    init_db()
    with _conn() as c:
        c.execute("UPDATE notifications SET read=1 WHERE user_id=?", (user_id,))
    return {"ok": True}
