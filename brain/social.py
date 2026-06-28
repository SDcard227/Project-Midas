"""
MIDAS — social graph: follows + mutuals. "Mutual" = you follow each other. Mutuals
gate the mutuals-only DM setting and show as a badge on profiles. Shared DB.
"""
from datetime import datetime, timezone

from brain import db


def _conn():
    return db.get_conn()


def _now():
    return datetime.now(timezone.utc).isoformat()


def init_db():
    with _conn() as c:
        c.execute("""CREATE TABLE IF NOT EXISTS follows (
            follower_id INTEGER NOT NULL,
            followee_id INTEGER NOT NULL,
            created_at  TEXT,
            UNIQUE(follower_id, followee_id)
        )""")


def is_following(a, b):
    init_db()
    with _conn() as c:
        r = c.execute("SELECT 1 AS x FROM follows WHERE follower_id=? AND followee_id=?",
                      (a, b)).fetchone()
    return bool(r)


def is_mutual(a, b):
    return is_following(a, b) and is_following(b, a)


def follow(a, b):
    try:
        b = int(b)
    except (TypeError, ValueError):
        return {"error": "Bad user."}
    if a == b:
        return {"error": "You can't follow yourself."}
    init_db()
    if not is_following(a, b):
        try:
            with _conn() as c:
                c.execute("INSERT INTO follows (follower_id, followee_id, created_at) VALUES (?,?,?)",
                          (a, b, _now()))
        except db.IntegrityError:
            pass   # raced another follow; the row already exists, which is the goal
    return {"ok": True, "following": True, "mutual": is_mutual(a, b)}


def unfollow(a, b):
    init_db()
    try:
        b = int(b)
    except (TypeError, ValueError):
        return {"error": "Bad user."}
    with _conn() as c:
        c.execute("DELETE FROM follows WHERE follower_id=? AND followee_id=?", (a, b))
    return {"ok": True, "following": False, "mutual": False}


def counts(user_id):
    init_db()
    with _conn() as c:
        followers = c.execute("SELECT COUNT(*) AS n FROM follows WHERE followee_id=?",
                              (user_id,)).fetchone()["n"]
        following = c.execute("SELECT COUNT(*) AS n FROM follows WHERE follower_id=?",
                              (user_id,)).fetchone()["n"]
    return {"followers": followers, "following": following}
