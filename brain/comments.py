"""
MIDAS — comments: crowd discussion under each wire story.

Each comment is scanned for sentiment (bullish / bearish / neutral). The NET
crowd lean becomes a MICRO signal: it can nudge a story's confidence by at most
~1% (a whisper of the crowd, deliberately tiny so it can't be brigaded/gamed —
the real signal is corroborated sources, the crowd is just a feather on the scale).

Sentiment is a fast keyword scan for now (free, instant, no API). It can be
upgraded to the Claude scanner later. Stored in the same SQLite DB as accounts.
"""
import os
import re
import sqlite3
from datetime import datetime, timezone

_DB = os.getenv("DB_PATH") or os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "midas_users.db")
_MAX_NUDGE = 1.0      # max +/- confidence points the crowd can move a story (~1%)
_FULL_WEIGHT_AT = 10  # this many comments = full (still tiny) weight

_BULL = {"buy", "bull", "bullish", "up", "moon", "rocket", "long", "calls",
         "pump", "rally", "breakout", "green", "gains", "gain", "undervalued",
         "strong", "beat", "surge", "soar", "rip", "send", "hold", "accumulate"}
_BEAR = {"sell", "bear", "bearish", "down", "short", "puts", "dump", "crash",
         "drop", "red", "weak", "miss", "overvalued", "scam", "avoid", "fall",
         "tank", "bag", "rug", "dead", "fraud", "bubble", "sinking"}


def _conn():
    from brain import db
    return db.get_conn()


def init_db():
    with _conn() as c:
        c.execute("""CREATE TABLE IF NOT EXISTS comments (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            event_id   TEXT,
            ticker     TEXT,
            user_id    INTEGER,
            author     TEXT,
            text       TEXT NOT NULL,
            sentiment  TEXT DEFAULT 'neutral',
            upvotes    INTEGER DEFAULT 0,
            created_at TEXT NOT NULL
        )""")
        cols = [r["name"] for r in c.execute("PRAGMA table_info(comments)").fetchall()]
        for col, ddl in [("upvotes", "INTEGER DEFAULT 0"),
                         ("downvotes", "INTEGER DEFAULT 0"),
                         ("parent_id", "INTEGER"),
                         ("edited", "INTEGER DEFAULT 0")]:
            if col not in cols:
                c.execute(f"ALTER TABLE comments ADD COLUMN {col} {ddl}")
        c.execute("""CREATE TABLE IF NOT EXISTS comment_votes (
            comment_id INTEGER,
            user_id    INTEGER,
            value      INTEGER DEFAULT 1,
            UNIQUE(comment_id, user_id)
        )""")
        vcols = [r["name"] for r in c.execute("PRAGMA table_info(comment_votes)").fetchall()]
        if "value" not in vcols:
            c.execute("ALTER TABLE comment_votes ADD COLUMN value INTEGER DEFAULT 1")


def _reps_for(user_ids):
    """Batch, Alpaca-free reputation lookup (for coloring names). Neutral default."""
    from brain import reputation
    reputation.init_db()
    ids = list({u for u in user_ids if u})
    out = {}
    if ids:
        qs = ",".join("?" * len(ids))
        with _conn() as c:
            for r in c.execute(f"SELECT * FROM user_rep WHERE user_id IN ({qs})", ids).fetchall():
                out[r["user_id"]] = {"score": round(r["score"], 1),
                                     "hue": reputation.hue(r["score"]), "total": r["total"]}
    for uid in ids:
        out.setdefault(uid, {"score": 50.0, "hue": "#9a9088", "total": 0})
    return out


def _scan_sentiment(text):
    """Fast keyword sentiment. Returns 'bullish' | 'bearish' | 'neutral'."""
    words = set(re.findall(r"[a-z']+", (text or "").lower()))
    b = len(words & _BULL)
    s = len(words & _BEAR)
    if b > s:
        return "bullish"
    if s > b:
        return "bearish"
    return "neutral"


def add_comment(user, event_id, ticker, text, parent_id=None):
    text = (text or "").strip()[:500]
    if len(text) < 2:
        return {"error": "Say a little more."}
    init_db()
    sent = _scan_sentiment(text)
    author = user.get("name") or (user.get("email") or "user").split("@")[0]
    with _conn() as c:
        cur = c.execute(
            "INSERT INTO comments (event_id,ticker,user_id,author,text,sentiment,parent_id,created_at)"
            " VALUES (?,?,?,?,?,?,?,?)",
            (event_id or "", (ticker or "").upper(), user["id"], author, text, sent,
             parent_id, datetime.now(timezone.utc).isoformat()))
    return {"ok": True, "id": cur.lastrowid, "sentiment": sent,
            "parent_id": parent_id, "nudge": crowd_nudge(event_id)}


def edit_comment(user, comment_id, new_text):
    """Owner edits their own comment: updates text, re-scans sentiment, marks it edited."""
    new_text = (new_text or "").strip()[:500]
    if len(new_text) < 2:
        return {"error": "Say a little more."}
    init_db()
    with _conn() as c:
        r = c.execute("SELECT user_id FROM comments WHERE id=?", (comment_id,)).fetchone()
        if not r:
            return {"error": "Comment not found."}
        if r["user_id"] != user["id"]:
            return {"error": "You can only edit your own comments."}
        sent = _scan_sentiment(new_text)
        c.execute("UPDATE comments SET text=?, sentiment=?, edited=1 WHERE id=?",
                  (new_text, sent, comment_id))
    return {"ok": True, "id": comment_id, "text": new_text, "sentiment": sent, "edited": True}


def delete_comment(user, comment_id):
    """Owner deletes their own comment (and, if it's a top-level take, its replies + votes)."""
    init_db()
    with _conn() as c:
        r = c.execute("SELECT user_id, parent_id FROM comments WHERE id=?",
                      (comment_id,)).fetchone()
        if not r:
            return {"error": "Comment not found."}
        if r["user_id"] != user["id"]:
            return {"error": "You can only delete your own comments."}
        ids = [comment_id]
        if r["parent_id"] is None:   # a top-level take takes its replies with it
            ids += [x["id"] for x in
                    c.execute("SELECT id FROM comments WHERE parent_id=?", (comment_id,)).fetchall()]
        qs = ",".join("?" * len(ids))
        c.execute(f"DELETE FROM comment_votes WHERE comment_id IN ({qs})", ids)
        c.execute(f"DELETE FROM comments WHERE id IN ({qs})", ids)
    return {"ok": True, "deleted": ids}


def author_of(comment_id):
    """The author user_id (+ a short snippet) of a comment, for reply notifications."""
    init_db()
    with _conn() as c:
        r = c.execute("SELECT user_id, text FROM comments WHERE id=?", (comment_id,)).fetchone()
    return {"user_id": r["user_id"], "text": (r["text"] or "")[:60]} if r else None


def vote(user_id, comment_id, direction="up"):
    """Up OR down vote. One vote per user per comment; voting the same way again
    toggles it off, the other way flips it. Counts are recomputed from the vote
    table (the source of truth) so they can never drift."""
    init_db()
    val = -1 if str(direction).lower().startswith("d") else 1
    with _conn() as c:
        ex = c.execute("SELECT value FROM comment_votes WHERE comment_id=? AND user_id=?",
                       (comment_id, user_id)).fetchone()
        if ex is None:
            c.execute("INSERT INTO comment_votes (comment_id,user_id,value) VALUES (?,?,?)",
                      (comment_id, user_id, val))
        elif ex["value"] == val:
            c.execute("DELETE FROM comment_votes WHERE comment_id=? AND user_id=?",
                      (comment_id, user_id))
        else:
            c.execute("UPDATE comment_votes SET value=? WHERE comment_id=? AND user_id=?",
                      (val, comment_id, user_id))
        up = c.execute("SELECT COUNT(*) n FROM comment_votes WHERE comment_id=? AND value=1",
                       (comment_id,)).fetchone()["n"]
        down = c.execute("SELECT COUNT(*) n FROM comment_votes WHERE comment_id=? AND value=-1",
                         (comment_id,)).fetchone()["n"]
        c.execute("UPDATE comments SET upvotes=?, downvotes=? WHERE id=?", (up, down, comment_id))
    return {"ok": True, "upvotes": up, "downvotes": down}


def upvote(user_id, comment_id):
    """Back-compat shim — an upvote is just vote(up)."""
    return vote(user_id, comment_id, "up")


def crowd_nudge(event_id):
    """Net crowd lean as a confidence delta, capped at +/- _MAX_NUDGE (~1%).
    Each comment is weighted by its upvotes — the more a take gets upvoted, the
    more attention the system gives it. Still capped tiny so it can't be brigaded."""
    if not event_id:
        return 0.0
    init_db()
    with _conn() as c:
        rows = c.execute("SELECT sentiment, upvotes FROM comments WHERE event_id=?",
                         (event_id,)).fetchall()
    if not rows:
        return 0.0
    num = den = 0.0
    for r in rows:
        w = 1.0 + min(r["upvotes"] or 0, 20)       # upvotes add weight (capped)
        sign = 1 if r["sentiment"] == "bullish" else -1 if r["sentiment"] == "bearish" else 0
        num += sign * w
        den += w
    if den == 0:
        return 0.0
    net = num / den                                # -1 .. 1 (upvote-weighted)
    weight = min(1.0, len(rows) / _FULL_WEIGHT_AT)  # more comments -> closer to full
    return round(max(-_MAX_NUDGE, min(_MAX_NUDGE, net * _MAX_NUDGE * weight)), 2)


def list_comments(event_id=None, ticker=None, limit=50):
    init_db()
    order = "ORDER BY upvotes DESC, id DESC"        # most-upvoted floats up = more attention
    rc = "(SELECT COUNT(*) FROM comments r WHERE r.parent_id=comments.id) AS reply_count"
    with _conn() as c:
        if event_id:
            rows = c.execute(f"SELECT *, {rc} FROM comments WHERE event_id=? AND parent_id IS NULL {order} LIMIT ?",
                             (event_id, limit)).fetchall()
        elif ticker:
            rows = c.execute(f"SELECT *, {rc} FROM comments WHERE ticker=? AND parent_id IS NULL {order} LIMIT ?",
                             ((ticker or "").upper(), limit)).fetchall()
        else:
            rows = []
    reps = _reps_for([r["user_id"] for r in rows])
    out = [{"id": r["id"], "author": r["author"], "text": r["text"],
            "sentiment": r["sentiment"], "upvotes": r["upvotes"] or 0,
            "downvotes": r["downvotes"] or 0, "reply_count": r["reply_count"] or 0,
            "user_id": r["user_id"], "rep": reps.get(r["user_id"]),
            "edited": bool(r["edited"]), "created_at": r["created_at"]} for r in rows]
    bull = sum(1 for r in out if r["sentiment"] == "bullish")
    bear = sum(1 for r in out if r["sentiment"] == "bearish")
    return {"comments": out, "count": len(out), "bull": bull, "bear": bear,
            "nudge": crowd_nudge(event_id) if event_id else 0.0}


def recent_all(limit=80):
    """Global discussion feed: recent TOP-LEVEL comments across ALL stories — the
    'gossip' that forms around the official content. Replies live inside threads."""
    init_db()
    rc = "(SELECT COUNT(*) FROM comments r WHERE r.parent_id=comments.id) AS reply_count"
    with _conn() as c:
        rows = c.execute(
            f"SELECT *, {rc} FROM comments WHERE event_id IS NOT NULL AND event_id != '' "
            "AND parent_id IS NULL ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    reps = _reps_for([r["user_id"] for r in rows])
    out = [{"id": r["id"], "author": r["author"], "text": r["text"],
            "sentiment": r["sentiment"], "upvotes": r["upvotes"] or 0,
            "downvotes": r["downvotes"] or 0, "reply_count": r["reply_count"] or 0,
            "event_id": r["event_id"], "ticker": r["ticker"],
            "user_id": r["user_id"], "rep": reps.get(r["user_id"]),
            "edited": bool(r["edited"]), "created_at": r["created_at"]} for r in rows]
    bull = sum(1 for r in out if r["sentiment"] == "bullish")
    bear = sum(1 for r in out if r["sentiment"] == "bearish")
    return {"comments": out, "count": len(out), "bull": bull, "bear": bear}


def thread(comment_id):
    """A comment + its replies (one level deep) — the 'further discussion' view."""
    init_db()
    with _conn() as c:
        root = c.execute("SELECT * FROM comments WHERE id=?", (comment_id,)).fetchone()
        if not root:
            return {"error": "not found"}
        replies = c.execute("SELECT * FROM comments WHERE parent_id=? ORDER BY id ASC",
                            (comment_id,)).fetchall()
    rows = [root] + list(replies)
    reps = _reps_for([r["user_id"] for r in rows])

    def fmt(r):
        return {"id": r["id"], "author": r["author"], "text": r["text"],
                "sentiment": r["sentiment"], "upvotes": r["upvotes"] or 0,
                "downvotes": r["downvotes"] or 0, "ticker": r["ticker"],
                "event_id": r["event_id"], "user_id": r["user_id"],
                "rep": reps.get(r["user_id"]), "edited": bool(r["edited"]),
                "created_at": r["created_at"]}
    return {"root": fmt(root), "replies": [fmt(r) for r in replies], "count": len(replies)}


def by_user(user_id, limit=12):
    """A user's own takes (comments), most-upvoted first — for their profile page.
    This is their public track record of opinions, alongside their scored calls."""
    init_db()
    with _conn() as c:
        rows = c.execute(
            "SELECT * FROM comments WHERE user_id=? ORDER BY upvotes DESC, id DESC LIMIT ?",
            (user_id, limit)).fetchall()
        total = c.execute("SELECT COUNT(*) AS n FROM comments WHERE user_id=?",
                          (user_id,)).fetchone()["n"]
        ups = c.execute("SELECT COALESCE(SUM(upvotes),0) AS n FROM comments WHERE user_id=?",
                        (user_id,)).fetchone()["n"]
    out = [{"id": r["id"], "text": r["text"], "ticker": r["ticker"],
            "sentiment": r["sentiment"], "upvotes": r["upvotes"] or 0,
            "downvotes": r["downvotes"] or 0, "event_id": r["event_id"],
            "is_reply": bool(r["parent_id"]), "created_at": r["created_at"]} for r in rows]
    return {"takes": out, "total": total, "total_upvotes": ups}
