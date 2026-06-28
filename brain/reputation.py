"""
MIDAS — reputation: credibility earned from CALLS, not claimed.

A "call" = a user's bullish/bearish comment on a ticker. We score each call
against the ACTUAL price move since they made it (via Alpaca daily bars):
  bullish + price up  = correct
  bearish + price down = correct
A user's accuracy across resolved calls -> a 0..100 reputation score -> a red→green
name hue. New/unproven users sit neutral (grey ~50) and earn color as calls resolve.

This gives everyone a real track record straight from the social layer, no real
trades required. Cached in the same SQLite DB as accounts/comments; the heavy
Alpaca work happens on-demand (profile view), name colors read the cache.
"""
import os
import sqlite3
from datetime import datetime, timezone, timedelta

_DB = os.getenv("DB_PATH") or os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "midas_users.db")
_NEUTRAL = "#9a9088"
_STALE_MIN = 30          # recompute if the cache is older than this many minutes
_SHRINK_AT = 5           # calls needed before the score uses its full red↔green range


def _conn():
    from brain import db
    return db.get_conn()


def init_db():
    with _conn() as c:
        c.execute("""CREATE TABLE IF NOT EXISTS user_rep (
            user_id    INTEGER PRIMARY KEY,
            score      REAL,
            accuracy   REAL,
            total      INTEGER,
            correct    INTEGER,
            updated_at TEXT
        )""")
        c.execute("""CREATE TABLE IF NOT EXISTS holdings (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id     INTEGER,
            ticker      TEXT,
            shares      REAL,
            entry_price REAL,
            opened_at   TEXT
        )""")


def _blend(a, b, t):
    return "#%02x%02x%02x" % tuple(round(a[i] + (b[i] - a[i]) * t) for i in range(3))


def hue(score):
    """0..100 -> red→grey→green hex for a name."""
    s = max(0.0, min(100.0, score if score is not None else 50.0))
    if s >= 50:
        return _blend((154, 144, 136), (45, 122, 79), (s - 50) / 50.0)   # grey -> green
    return _blend((154, 144, 136), (138, 48, 48), (50 - s) / 50.0)        # grey -> red


def get_cached(user_id):
    """Fast, Alpaca-free read for coloring names. Neutral if not computed yet."""
    if not user_id:
        return {"score": 50.0, "hue": _NEUTRAL, "total": 0}
    init_db()
    with _conn() as c:
        r = c.execute("SELECT * FROM user_rep WHERE user_id=?", (user_id,)).fetchone()
    if not r:
        return {"score": 50.0, "hue": _NEUTRAL, "total": 0}
    return {"score": round(r["score"], 1), "hue": hue(r["score"]),
            "accuracy": r["accuracy"], "total": r["total"], "correct": r["correct"]}


def cached_user_ids(stale_only=False, limit=None):
    """User ids that already have a cached user_rep row. Used by the background
    re-scorer so name hue colors stay fresh without anyone opening a profile.
    stale_only=True keeps only rows older than _STALE_MIN, oldest first."""
    init_db()
    with _conn() as c:
        rows = c.execute(
            "SELECT user_id, updated_at FROM user_rep ORDER BY updated_at ASC").fetchall()
    if not stale_only:
        ids = [r["user_id"] for r in rows]
        return ids[:limit] if limit else ids
    now = datetime.now(timezone.utc)
    out = []
    for r in rows:
        ts = r["updated_at"]
        stale = True
        if ts:
            try:
                stale = (now - datetime.fromisoformat(ts)).total_seconds() > _STALE_MIN * 60
            except Exception:
                stale = True
        if stale:
            out.append(r["user_id"])
    return out[:limit] if limit else out


# ── price scoring (Alpaca daily bars) ────────────────────────────────────────
def _daily_closes(ticker, start_dt):
    """{date_str: close} for ticker from start_dt to now. {} on any failure."""
    key, sec = os.getenv("ALPACA_API_KEY"), os.getenv("ALPACA_SECRET_KEY")
    if not key or not sec:
        return {}
    try:
        from alpaca.data.historical import StockHistoricalDataClient
        from alpaca.data.requests import StockBarsRequest
        from alpaca.data.timeframe import TimeFrame
        client = StockHistoricalDataClient(key, sec)
        req = StockBarsRequest(symbol_or_symbols=ticker, timeframe=TimeFrame.Day,
                               start=start_dt)
        bars = client.get_stock_bars(req)
        out = {}
        data = bars.data.get(ticker, []) if hasattr(bars, "data") else []
        for b in data:
            out[b.timestamp.date().isoformat()] = float(b.close)
        return out
    except Exception:
        return {}


def _resolve_calls(calls):
    """calls: [(ticker, direction, created_at_iso)]. Returns scored detail list."""
    by_ticker = {}
    for tk, d, ts in calls:
        by_ticker.setdefault(tk, []).append((d, ts))
    out = []
    for tk, items in by_ticker.items():
        earliest = min(ts for _d, ts in items)
        try:
            start = datetime.fromisoformat(earliest) - timedelta(days=3)
        except Exception:
            start = datetime.now(timezone.utc) - timedelta(days=120)
        closes = _daily_closes(tk, start)
        if not closes:
            for d, ts in items:
                out.append({"ticker": tk, "direction": d, "when": ts,
                            "move_pct": None, "correct": None})
            continue
        dates = sorted(closes.keys())
        latest = closes[dates[-1]]
        for d, ts in items:
            day = ts[:10]
            entry_key = next((dt for dt in dates if dt >= day), None)
            if entry_key is None:
                out.append({"ticker": tk, "direction": d, "when": ts,
                            "move_pct": None, "correct": None})
                continue
            entry = closes[entry_key]
            move = (latest - entry) / entry * 100.0 if entry else 0.0
            if entry_key == dates[-1]:
                correct = None                       # too new, no exit bar yet
            elif d == "bullish":
                correct = move > 0
            elif d == "bearish":
                correct = move < 0
            else:
                correct = None
            out.append({"ticker": tk, "direction": d, "when": ts,
                        "move_pct": round(move, 2), "correct": correct})
    out.sort(key=lambda x: x["when"], reverse=True)
    return out


def _score_from(total, correct):
    if total <= 0:
        return 50.0
    acc = correct / total
    shrink = min(1.0, total / _SHRINK_AT)            # few calls -> stay near neutral
    return round(50.0 + (acc - 0.5) * 100.0 * shrink, 1)


def compute(user_id):
    """Score the user's calls vs real price moves, cache the result, return detail."""
    init_db()
    try:
        with _conn() as c:
            rows = c.execute(
                "SELECT ticker, sentiment, created_at FROM comments "
                "WHERE user_id=? AND sentiment IN ('bullish','bearish') AND ticker NOT IN ('','GOSSIP')",
                (user_id,)).fetchall()
    except Exception:
        rows = []          # no comments table yet (fresh DB) -> nothing to score
    calls = [(r["ticker"], r["sentiment"], r["created_at"]) for r in rows]
    detail = _resolve_calls(calls) if calls else []
    resolved = [d for d in detail if d["correct"] is not None]
    total = len(resolved)
    correct = sum(1 for d in resolved if d["correct"])
    acc = (correct / total) if total else None
    score = _score_from(total, correct)
    with _conn() as c:
        c.execute("INSERT OR REPLACE INTO user_rep (user_id,score,accuracy,total,correct,updated_at)"
                  " VALUES (?,?,?,?,?,?)",
                  (user_id, score, acc, total, correct,
                   datetime.now(timezone.utc).isoformat()))
    return {"score": score, "hue": hue(score), "accuracy": acc,
            "total": total, "correct": correct, "calls": detail,
            "pending": len(detail) - total}


def leaderboard(scope="world", country="", state="", limit=50, max_compute=15):
    """Rank users by reputation. scope: world | country | state. Refreshes rep for
    users with calls (bounded per request) then ranks those with scored calls."""
    init_db()
    try:
        with _conn() as c:
            caller_ids = [r["user_id"] for r in c.execute(
                "SELECT DISTINCT user_id FROM comments WHERE sentiment IN ('bullish','bearish') "
                "AND ticker NOT IN ('','GOSSIP') AND user_id IS NOT NULL").fetchall()]
    except Exception:
        caller_ids = []
    with _conn() as c:
        rep_rows = {r["user_id"]: r for r in c.execute("SELECT * FROM user_rep").fetchall()}
    now = datetime.now(timezone.utc)
    computed = 0
    for uid in caller_ids:
        row = rep_rows.get(uid)
        stale = True
        if row and row["updated_at"]:
            try:
                stale = (now - datetime.fromisoformat(row["updated_at"])).total_seconds() > _STALE_MIN * 60
            except Exception:
                stale = True
        if stale and computed < max_compute:
            compute(uid)
            computed += 1
    with _conn() as c:
        rows = c.execute(
            "SELECT u.id, u.first_name, u.last_name, u.email, u.country, u.state, u.tier, "
            "       r.score, r.accuracy, r.total, r.correct "
            "FROM user_rep r JOIN users u ON u.id = r.user_id WHERE r.total > 0").fetchall()
    cl = (country or "").strip().lower()
    sl = (state or "").strip().lower()
    out = []
    for r in rows:
        rc = (r["country"] or "").strip().lower()
        rs = (r["state"] or "").strip().lower()
        if scope == "country" and cl and rc != cl:
            continue
        if scope == "state" and sl and rs != sl:
            continue
        fn = (r["first_name"] or "").strip()
        ln = (r["last_name"] or "").strip()
        name = (fn + ((" " + ln[0] + ".") if ln else "")).strip() or r["email"].split("@")[0]
        out.append({"id": r["id"], "name": name, "score": round(r["score"], 1),
                    "hue": hue(r["score"]), "accuracy": r["accuracy"],
                    "correct": r["correct"], "total": r["total"],
                    "country": (r["country"] or "").strip(),
                    "state": (r["state"] or "").strip(), "tier": r["tier"]})
    out.sort(key=lambda x: x["score"], reverse=True)
    return {"scope": scope, "country": country, "state": state, "leaders": out[:limit]}


def portfolio(user_id):
    """A user's holdings with live P&L (for their profile / 'how they're doing')."""
    init_db()
    with _conn() as c:
        rows = c.execute("SELECT ticker, shares, entry_price FROM holdings WHERE user_id=?",
                         (user_id,)).fetchall()
    out = []
    tv = tc = 0.0
    cache = {}
    for r in rows:
        tk = r["ticker"]; sh = r["shares"] or 0; ep = r["entry_price"] or 0
        if tk not in cache:
            closes = _daily_closes(tk, datetime.now(timezone.utc) - timedelta(days=6))
            cache[tk] = (list(closes.values())[-1] if closes else ep)
        cur = cache[tk] or ep
        val = cur * sh; cost = ep * sh
        out.append({"ticker": tk, "shares": sh, "entry": round(ep, 2), "price": round(cur, 2),
                    "value": round(val, 2), "pnl": round(val - cost, 2),
                    "pnl_pct": round((cur - ep) / ep * 100, 1) if ep else 0})
        tv += val; tc += cost
    out.sort(key=lambda x: x["value"], reverse=True)
    return {"holdings": out, "total_value": round(tv, 2), "total_pnl": round(tv - tc, 2),
            "total_pnl_pct": round((tv - tc) / tc * 100, 1) if tc else 0}
