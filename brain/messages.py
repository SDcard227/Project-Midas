"""
MIDAS — messages: private 1-on-1 DMs. Inbox = your conversations; a thread = the
messages between you and one other user. Block stops someone from DMing you. Stored
in the shared DB. Light by design; moderation is block + report.
"""
import os
from datetime import datetime, timezone

from brain import db


def _conn():
    return db.get_conn()


# ── encryption at rest ───────────────────────────────────────────────────────
# DM bodies are stored encrypted IF SECRET_KEY (or MIDAS_MSG_KEY) is set, so a raw DB
# leak exposes nothing. With no key it's plaintext (no behaviour change). The server can
# still read messages in memory, so moderation + the mutuals gate keep working. (This is
# encryption-at-rest, not end-to-end; full E2EE would block those features.)
_FERNET_CACHE = "uninit"
_ENC_MARK = "enc1:"


def _fernet():
    global _FERNET_CACHE
    if _FERNET_CACHE != "uninit":
        return _FERNET_CACHE
    sk = os.getenv("SECRET_KEY") or os.getenv("MIDAS_MSG_KEY")
    if not sk:
        _FERNET_CACHE = None
        return None
    try:
        import hashlib, base64
        from cryptography.fernet import Fernet
        key = base64.urlsafe_b64encode(hashlib.sha256(("midas-dm::" + sk).encode()).digest())
        _FERNET_CACHE = Fernet(key)
    except Exception:
        _FERNET_CACHE = None
    return _FERNET_CACHE


def _enc(text):
    f = _fernet()
    if not f or not text:
        return text
    try:
        return _ENC_MARK + f.encrypt(text.encode("utf-8")).decode("ascii")
    except Exception:
        return text


def _dec(text):
    if not text or not text.startswith(_ENC_MARK):
        return text                       # plaintext / legacy message
    f = _fernet()
    if not f:
        return "[encrypted, set SECRET_KEY to read]"
    try:
        return f.decrypt(text[len(_ENC_MARK):].encode("ascii")).decode("utf-8")
    except Exception:
        return "[encrypted]"


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


def _has_history(a, b):
    """True if a and b already have a DM between them (an open conversation)."""
    init_db()
    with _conn() as c:
        r = c.execute("SELECT 1 AS x FROM dm_messages WHERE (from_id=? AND to_id=?)"
                      " OR (from_id=? AND to_id=?) LIMIT 1", (a, b, b, a)).fetchone()
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
    # mutuals-only privacy: only mutual follows can open a NEW conversation
    from brain import accounts, social
    recip = accounts.get_user(to_id)
    if (recip and recip.get("dm_privacy") == "mutuals"
            and not social.is_mutual(from_id, to_id) and not _has_history(from_id, to_id)):
        return {"error": "They only take DMs from mutuals , you both have to follow each other."}
    with _conn() as c:
        c.execute("INSERT INTO dm_messages (from_id, to_id, body, created_at) VALUES (?,?,?,?)",
                  (from_id, to_id, _enc(body[:2000]), _now()))
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
             "body": _dec(r["body"]), "created_at": r["created_at"]} for r in rows]


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
            convos[other] = {"other_id": other, "last": _dec(r["body"]),
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
