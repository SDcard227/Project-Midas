"""
MIDAS — accounts: SQLite-backed user store (register / login / tiers).

Phase-2 platform foundation. SQLite for dev (zero-config); swap to Postgres for
prod. Stores ONLY: email, password hash, tier, created_at. No money and no
brokerage keys live here — per-user brokerage comes later via OAuth. Midas stays
software (a cockpit on a real brokerage), never a bank or custodian.
"""
import os
import re
import secrets
import sqlite3
from datetime import datetime, timezone

from werkzeug.security import generate_password_hash, check_password_hash

from brain import db

_DB = os.getenv("DB_PATH") or os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "midas_users.db")
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_NAME_RE  = re.compile(r"^[A-Za-z][A-Za-z'\-. ]{0,39}$")   # real names only, no handles
_NICK_RE  = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._'\-]{0,23}$")  # optional display nickname
_HANDLE_RE = re.compile(r"^[a-z0-9_]{2,20}$")   # unique @handle for mentions
_VALID_TIERS = {"free", "pro", "premium"}


def _slugify(s):
    s = re.sub(r"[^a-z0-9_]", "", (s or "").lower())[:20]
    return s or "user"


def _unique_handle(c, base):
    base = _slugify(base)
    h, n = base, 1
    while c.execute("SELECT 1 AS x FROM users WHERE handle=?", (h,)).fetchone():
        n += 1
        h = (base + str(n))[:20]
    return h


def _conn():
    return db.get_conn()


def init_db():
    with _conn() as c:
        c.execute("""CREATE TABLE IF NOT EXISTS users (
            id                 INTEGER PRIMARY KEY AUTOINCREMENT,
            email              TEXT UNIQUE NOT NULL,
            pw_hash            TEXT NOT NULL,
            first_name         TEXT,
            last_name          TEXT,
            tier               TEXT NOT NULL DEFAULT 'free',
            stripe_customer_id TEXT,
            created_at         TEXT NOT NULL
        )""")
        # migrate older DBs
        cols = [r["name"] for r in c.execute("PRAGMA table_info(users)").fetchall()]
        for col in ("stripe_customer_id", "first_name", "last_name", "country", "state", "verify_token", "nickname", "bio", "dm_privacy", "handle"):
            if col not in cols:
                c.execute(f"ALTER TABLE users ADD COLUMN {col} TEXT")
        if "verified" not in cols:
            c.execute("ALTER TABLE users ADD COLUMN verified INTEGER DEFAULT 0")


def _row_to_user(r):
    if not r:
        return None
    fn = (r["first_name"] or "").strip()
    ln = (r["last_name"] or "").strip()
    keys = r.keys()
    nick = (r["nickname"].strip() if "nickname" in keys and r["nickname"] else "")
    # verified real name = the identity anchor (full name, shown on the profile)
    real_name = (fn + ((" " + ln) if ln else "")).strip() or r["email"].split("@")[0]
    # post/display name = always "F. Lastname" (first initial + last). The nickname
    # is kept and shown on the profile as a "goes by", but never replaces the post name.
    display = (((fn[0] + ".") if fn else "") + ((" " + ln) if ln else "")).strip() or real_name
    return {"id": r["id"], "email": r["email"], "first_name": fn, "last_name": ln,
            "real_name": real_name, "nickname": nick, "name": display,
            "tier": r["tier"], "created_at": r["created_at"],
            "country": (r["country"] or "").strip(), "state": (r["state"] or "").strip(),
            "bio": (r["bio"].strip() if "bio" in keys and r["bio"] else ""),
            "dm_privacy": (r["dm_privacy"] if "dm_privacy" in keys and r["dm_privacy"] else "open"),
            "handle": (r["handle"] if "handle" in keys and r["handle"] else ""),
            "verified": bool(r["verified"]) if "verified" in keys else False}


def create_user(email, password, first_name="", last_name="", country="", state="", nickname=""):
    """Register a new user. Real names are required (the verified anchor); an optional
    nickname displays over the real name, which stays attached. {'user':..}/{'error':..}."""
    email = (email or "").strip().lower()
    first_name = (first_name or "").strip()
    last_name  = (last_name or "").strip()
    nickname   = (nickname or "").strip()
    if not _EMAIL_RE.match(email):
        return {"error": "Enter a valid email."}
    if len(password or "") < 8:
        return {"error": "Password must be at least 8 characters."}
    if not _NAME_RE.match(first_name):
        return {"error": "Enter your real first name (letters only)."}
    if not _NAME_RE.match(last_name):
        return {"error": "Enter your real last name (letters only)."}
    if nickname and not _NICK_RE.match(nickname):
        return {"error": "Nickname: up to 24 letters/numbers/spaces."}
    init_db()
    try:
        with _conn() as c:
            cur = c.execute(
                "INSERT INTO users (email, pw_hash, first_name, last_name, country, state, nickname, tier, verify_token, created_at)"
                " VALUES (?,?,?,?,?,?,?,?,?,?)",
                (email, generate_password_hash(password), first_name, last_name,
                 (country or "").strip()[:40], (state or "").strip()[:40], nickname[:24], "free",
                 secrets.token_urlsafe(24), datetime.now(timezone.utc).isoformat()))
            uid = cur.lastrowid
        try:                                    # auto-assign a unique @handle
            with _conn() as c:
                h = _unique_handle(c, nickname or first_name or email.split("@")[0])
                c.execute("UPDATE users SET handle=? WHERE id=?", (h, uid))
        except Exception:
            pass
        return {"user": get_user(uid)}
    except db.IntegrityError:
        return {"error": "That email is already registered."}


def verify_user(email, password):
    """Return the user dict on correct credentials, else None."""
    email = (email or "").strip().lower()
    init_db()
    with _conn() as c:
        r = c.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
    if r and check_password_hash(r["pw_hash"], password or ""):
        return _row_to_user(r)
    return None


def get_user(user_id):
    if not user_id:
        return None
    init_db()
    with _conn() as c:
        r = c.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
    return _row_to_user(r)


def set_tier(user_id, tier):
    """Set a user's subscription tier (called by the Stripe webhook later)."""
    tier = (tier or "free").lower()
    if tier not in _VALID_TIERS:
        tier = "free"
    init_db()
    with _conn() as c:
        c.execute("UPDATE users SET tier=? WHERE id=?", (tier, user_id))
    return get_user(user_id)


def set_stripe_customer(user_id, customer_id):
    init_db()
    with _conn() as c:
        c.execute("UPDATE users SET stripe_customer_id=? WHERE id=?",
                  (customer_id, user_id))


def get_stripe_customer(user_id):
    init_db()
    with _conn() as c:
        r = c.execute("SELECT stripe_customer_id FROM users WHERE id=?", (user_id,)).fetchone()
    return (r["stripe_customer_id"] if r else None)


def set_tier_by_customer(customer_id, tier):
    """Used by the Stripe webhook on subscription create/cancel."""
    tier = (tier or "free").lower()
    if tier not in _VALID_TIERS:
        tier = "free"
    init_db()
    with _conn() as c:
        c.execute("UPDATE users SET tier=? WHERE stripe_customer_id=?",
                  (tier, customer_id))


def count_users():
    init_db()
    with _conn() as c:
        return c.execute("SELECT COUNT(*) AS n FROM users").fetchone()["n"]


def set_nickname(user_id, nickname):
    """Set/clear a user's display nickname. The verified real name stays attached."""
    nickname = (nickname or "").strip()
    if nickname and not _NICK_RE.match(nickname):
        return {"error": "Nickname: up to 24 letters/numbers/spaces."}
    init_db()
    with _conn() as c:
        c.execute("UPDATE users SET nickname=? WHERE id=?", (nickname[:24], user_id))
    return {"user": get_user(user_id)}


def set_bio(user_id, bio):
    """Set/clear a user's profile bio (plain text, capped at 400 chars)."""
    bio = (bio or "").strip()
    init_db()
    with _conn() as c:
        c.execute("UPDATE users SET bio=? WHERE id=?", (bio[:400], user_id))
    return {"user": get_user(user_id)}


def set_dm_privacy(user_id, value):
    """Who can DM this user: 'open' (anyone) or 'mutuals' (only mutual follows)."""
    value = "mutuals" if (value or "").lower() == "mutuals" else "open"
    init_db()
    with _conn() as c:
        c.execute("UPDATE users SET dm_privacy=? WHERE id=?", (value, user_id))
    return {"user": get_user(user_id)}


def set_handle(user_id, handle):
    """Set a unique @handle (2-20 lowercase letters/numbers/underscore)."""
    handle = (handle or "").strip().lower().lstrip("@")
    if not _HANDLE_RE.match(handle):
        return {"error": "Handle: 2-20 lowercase letters, numbers, or underscore."}
    init_db()
    with _conn() as c:
        if c.execute("SELECT 1 AS x FROM users WHERE handle=? AND id<>?", (handle, user_id)).fetchone():
            return {"error": "That handle is taken."}
        c.execute("UPDATE users SET handle=? WHERE id=?", (handle, user_id))
    return {"user": get_user(user_id)}


def get_by_handle(handle):
    handle = (handle or "").strip().lower().lstrip("@")
    if not handle:
        return None
    init_db()
    with _conn() as c:
        r = c.execute("SELECT * FROM users WHERE handle=?", (handle,)).fetchone()
    return _row_to_user(r)


def resolve_handles(text):
    """Find @handles in text -> deduped list of user_ids (for @mention notifications)."""
    if not text:
        return []
    handles = set(m.lower() for m in re.findall(r"@([A-Za-z0-9_]{2,20})", text))
    if not handles:
        return []
    init_db()
    out = []
    with _conn() as c:
        for h in handles:
            r = c.execute("SELECT id FROM users WHERE handle=?", (h,)).fetchone()
            if r:
                out.append(r["id"])
    return out


def get_verify_token(user_id):
    """Return (creating if missing) the email-verification token. None if already verified."""
    init_db()
    with _conn() as c:
        r = c.execute("SELECT verify_token, verified FROM users WHERE id=?", (user_id,)).fetchone()
        if not r or r["verified"]:
            return None
        tok = r["verify_token"]
        if not tok:
            tok = secrets.token_urlsafe(24)
            c.execute("UPDATE users SET verify_token=? WHERE id=?", (tok, user_id))
    return tok


def verify_email(token):
    """Consume a verification token -> mark the user verified. Returns the user or None."""
    if not token:
        return None
    init_db()
    with _conn() as c:
        r = c.execute("SELECT id FROM users WHERE verify_token=?", (token,)).fetchone()
        if not r:
            return None
        c.execute("UPDATE users SET verified=1, verify_token=NULL WHERE id=?", (r["id"],))
    return get_user(r["id"])
