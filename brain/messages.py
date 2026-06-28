"""
MIDAS — messages: private 1-on-1 DMs. Inbox = your conversations; a thread = the
messages between you and one other user. Block stops someone from DMing you. Stored
in the shared DB. Light by design; moderation is block + report.
"""
from datetime import datetime, timezone

from brain import db


def _conn():
    return db.get_conn()


def _now():
    return datetime.now(timezone.utc).isoformat()


def init_db():
    with _conn() as c:
        c.execute("""CREATE TABLE IF NOT EXISTS dm_messages (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            from_id    INTEGER NOT NULL,
            to_id      INTEGER NOT NULL,
            body       TEXT NOT NULL,
            read       INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL
        )""")
        c.execute("""CREATE TABLE IF NOT EXISTS dm_blocks (
            blocker_id INTEGER NOT NULL,
            blocked_id INTEGER NOT NULL,
            created_at TEXT
        )""")


def is_blocked(blocker_id, blocked_id):
    init_db()
    with _conn() as c:
        r = c.execute("SELECT 1 AS x FROM dm_blocks WHERE blocker_id=? AND blocked_id=?",
                      (blocker_id, blocked_id)).fetchone()
    return bool(r)


def send(from_id, to_id, body):
    """Send a DM. Returns {'ok':True,'to_id':..} or {'error':..}. Blocked by the
    recipient -> refused. The caller pushes the recipient's notification."""
    body = (body or "").strip()
    if not body:
        return {"error": "Write something first."}
    if len(body) > 2000:
        return {"error": "That message is too long."}
    try:
        to_id = int(to_id)
    except (TypeError, ValueError):
        return {"error": "Bad recipient."}
    if to_id == from_id:
        return {"error": "You can't message yourself."}
    init_db()
    if is_blocked(to_id, from_id):
        return {"error": "You can't message this user."}
    with _conn() as c:
        c.execute("INSERT INTO dm_messages (from_id, to_id, body, created_at) VALUES (?,?,?,?)",
                  (from_id, to_id, body[:2000], _now()))
    return {"ok": True, "to_id": to_id}


def thread(user_id, other_id, limit=200):
    """Messages between user_id and other_id (oldest..newest), marking inbound read."""
    init_db()
    with _conn() as c:
        rows = c.execute(
            "SELECT * FROM dm_messages WHERE (from_id=? AND to_id=?) OR (from_id=? AND to_id=?)"
            " ORDER BY id ASC LIMIT ?", (user_id, other_id, other_id, user_id, limit)).fetchall()
        c.execute("UPDATE dm_messages SET read=1 WHERE to_id=? AND from_id=?", (user_id, other_id))
    return [{"id": r["id"], "from_id": r["from_id"], "mine": r["from_id"] == user_id,
             "body": r["body"], "created_at": r["created_at"]} for r in rows]


def inbox(user_id):
    """Conversation list: the other user + last message + unread count, newest first."""
    init_db()
    with _conn() as c:
        rows = c.execute("SELECT * FROM dm_messages WHERE from_id=? OR to_id=? ORDER BY id DESC",
                         (user_id, user_id)).fetchall()
    convos = {}
    for r in rows:
        other = r["to_id"] if r["from_id"] == user_id else r["from_id"]
        if other not in convos:
            convos[other] = {"other_id": other, "last": r["body"],
                             "created_at": r["created_at"], "unread": 0}
        if r["to_id"] == user_id and not r["read"]:
            convos[other]["unread"] += 1
    return list(convos.values())


def unread_count(user_id):
    init_db()
    with _conn() as c:
        return c.execute("SELECT COUNT(*) AS n FROM dm_messages WHERE to_id=? AND read=0",
                         (user_id,)).fetchone()["n"]


def block(blocker_id, blocked_id):
    init_db()
    try:
        blocked_id = int(blocked_id)
    except (TypeError, ValueError):
        return {"error": "Bad user."}
    if not is_blocked(blocker_id, blocked_id):
        with _conn() as c:
            c.execute("INSERT INTO dm_blocks (blocker_id, blocked_id, created_at) VALUES (?,?,?)",
                      (blocker_id, blocked_id, _now()))
    return {"ok": True}
