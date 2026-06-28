"""
MIDAS — The Pit: group chat rooms for live discussion + group investing.

Pillar 5 scaffold. Polling-based for now (no WebSockets yet) — the frontend long-ish
polls /api/rooms/<id>/messages?after=<last_id>. Same SQLite DB and real-name model as
comments: you post under your verified "F. Lastname", names carry the reputation hue.
Reading is open to everyone; posting needs an account.
"""
import os
import sqlite3
from datetime import datetime, timezone

_DB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   "midas_users.db")


def _conn():
    c = sqlite3.connect(_DB)
    c.row_factory = sqlite3.Row
    return c


def init_db():
    with _conn() as c:
        c.execute("""CREATE TABLE IF NOT EXISTS rooms (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            name       TEXT NOT NULL,
            topic      TEXT,
            creator    TEXT,
            created_at TEXT NOT NULL
        )""")
        c.execute("""CREATE TABLE IF NOT EXISTS room_messages (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            room_id    INTEGER NOT NULL,
            user_id    INTEGER,
            author     TEXT,
            text       TEXT NOT NULL,
            created_at TEXT NOT NULL
        )""")
        if not c.execute("SELECT COUNT(*) AS n FROM rooms").fetchone()["n"]:
            for nm, tp in [("Market Open", "Pre-bell game plan + overnight news"),
                           ("Earnings Season", "Reactions + setups around prints"),
                           ("Long-Term Investing", "Theses, DCA, and conviction holds"),
                           ("Crypto Corner", "BTC, ETH, and the rest")]:
                c.execute("INSERT INTO rooms (name, topic, creator, created_at) VALUES (?,?,?,?)",
                          (nm, tp, "Midas", datetime.now(timezone.utc).isoformat()))


def list_rooms():
    init_db()
    with _conn() as c:
        rows = c.execute(
            """SELECT r.*,
                 (SELECT COUNT(*) FROM room_messages m WHERE m.room_id=r.id) AS msg_count,
                 (SELECT MAX(created_at) FROM room_messages m WHERE m.room_id=r.id) AS last_at
               FROM rooms r
               ORDER BY (last_at IS NULL), last_at DESC, r.id ASC""").fetchall()
    return [{"id": r["id"], "name": r["name"], "topic": r["topic"], "creator": r["creator"],
             "msg_count": r["msg_count"] or 0, "last_at": r["last_at"]} for r in rows]


def create_room(user, name, topic=""):
    name = (name or "").strip()[:60]
    if len(name) < 2:
        return {"error": "Name your room (2+ characters)."}
    init_db()
    with _conn() as c:
        cur = c.execute("INSERT INTO rooms (name, topic, creator, created_at) VALUES (?,?,?,?)",
                        (name, (topic or "").strip()[:140], user.get("name") or "user",
                         datetime.now(timezone.utc).isoformat()))
    return {"ok": True, "id": cur.lastrowid}


def post_message(user, room_id, text):
    text = (text or "").strip()[:600]
    if len(text) < 1:
        return {"error": "Say something."}
    init_db()
    author = user.get("name") or (user.get("email") or "user").split("@")[0]
    with _conn() as c:
        cur = c.execute(
            "INSERT INTO room_messages (room_id, user_id, author, text, created_at) VALUES (?,?,?,?,?)",
            (room_id, user["id"], author, text, datetime.now(timezone.utc).isoformat()))
    return {"ok": True, "id": cur.lastrowid}


def list_messages(room_id, after=0, limit=120):
    """Messages in a room with id > after (for incremental polling), names rep-colored."""
    init_db()
    with _conn() as c:
        rows = c.execute(
            "SELECT * FROM room_messages WHERE room_id=? AND id>? ORDER BY id ASC LIMIT ?",
            (room_id, after, limit)).fetchall()
    try:
        from brain.comments import _reps_for
        reps = _reps_for([r["user_id"] for r in rows])
    except Exception:
        reps = {}
    out = [{"id": r["id"], "author": r["author"], "text": r["text"],
            "user_id": r["user_id"], "rep": reps.get(r["user_id"]),
            "created_at": r["created_at"]} for r in rows]
    return {"messages": out, "last_id": (out[-1]["id"] if out else after)}
