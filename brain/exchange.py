"""
MIDAS — The Exchange: a play-money HYPE market. Trade anything like a stock.

Every entity (an artist, a film, a game, a restaurant, a sneaker, a studio, a person,
an idea) can be LISTED as an asset with a ticker. Its price rides a bonding curve:
buying mints shares and pushes the price UP, selling burns shares and pulls it DOWN.
So early backers profit when a name runs — "I was on it before it blew up," with a
number attached. It is the meme-coin / hype mechanic, on non-cashable Bucks (a skill
and taste game, NOT real securities and NOT real crypto). Shares the Parlor's wallet.

PRICE: a linear bonding curve, price(s) = BASE + SLOPE*s  (s = shares outstanding).
Buy cost (s -> s+n) and sell value (s -> s-n) are the exact integrals of that line,
so the curve is fully reversible: the maker never mints or destroys Bucks by itself.
There is no settle and no oracle — value is pure crowd demand, you exit by selling.
"""
import math
import re
from datetime import datetime, timezone

from brain import db
from brain import parlor          # shared Bucks wallet (parlor_wallet)

BASE    = 1.0     # price of the very first share, in Bucks
SLOPE   = 0.02    # how hard the price pumps per share bought
MIN_BUY = 10      # minimum Bucks per buy
_TICKER_RE = re.compile(r"^[A-Z0-9]{2,8}$")


def _conn():
    return db.get_conn()


def _now():
    return datetime.now(timezone.utc).isoformat()


def init_db():
    with _conn() as c:
        c.execute("""CREATE TABLE IF NOT EXISTS exch_assets (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            name       TEXT NOT NULL,
            ticker     TEXT UNIQUE NOT NULL,
            category   TEXT,
            blurb      TEXT,
            link       TEXT,
            image      TEXT,
            proof      TEXT,
            status     TEXT NOT NULL DEFAULT 'pending',
            shares     REAL NOT NULL DEFAULT 0,
            created_by INTEGER,
            created_at TEXT NOT NULL
        )""")
        acols = [r["name"] for r in c.execute("PRAGMA table_info(exch_assets)").fetchall()]
        for col in ("link", "image", "proof", "status"):
            if col not in acols:
                c.execute(f"ALTER TABLE exch_assets ADD COLUMN {col} TEXT")
        c.execute("""CREATE TABLE IF NOT EXISTS exch_holdings (
            user_id  INTEGER NOT NULL,
            asset_id INTEGER NOT NULL,
            shares   REAL NOT NULL DEFAULT 0,
            cost     INTEGER NOT NULL DEFAULT 0,
            UNIQUE(user_id, asset_id)
        )""")
        c.execute("""CREATE TABLE IF NOT EXISTS exch_stats (
            user_id  INTEGER PRIMARY KEY,
            realized INTEGER NOT NULL DEFAULT 0,
            wins     INTEGER NOT NULL DEFAULT 0,
            losses   INTEGER NOT NULL DEFAULT 0
        )""")


# ── bonding curve (pure, testable) ───────────────────────────────────────────

def price(shares):
    return BASE + SLOPE * max(0.0, shares)


def _buy_cost(s, n):
    """Exact Bucks to buy n shares starting at supply s (integral of the price line)."""
    return BASE * n + SLOPE * (n * s + n * n / 2.0)


def _shares_for(s, bucks):
    """Shares that `bucks` buys at supply s. Solves (SLOPE/2)n^2 + (BASE+SLOPE*s)n - bucks = 0."""
    a = SLOPE / 2.0
    b = BASE + SLOPE * s
    if a <= 0:
        return bucks / b
    disc = b * b + 4 * a * bucks
    return (-b + math.sqrt(disc)) / (2 * a)


def _sell_value(s, n):
    """Exact Bucks returned selling n shares from supply s (integral s-n .. s)."""
    n = min(n, s)
    return BASE * n + SLOPE * (n * s - n * n / 2.0)


def market_cap(s):
    return _buy_cost(0.0, s)      # total Bucks the curve has absorbed at supply s


# ── wallet (shared with the Parlor) ──────────────────────────────────────────

def _adjust(c, user_id, delta):
    c.execute("UPDATE parlor_wallet SET credits = credits + ?, updated=? WHERE user_id=?",
              (int(delta), _now(), user_id))


# ── listing ──────────────────────────────────────────────────────────────────

def _slug_ticker(name):
    t = re.sub(r"[^A-Z0-9]", "", (name or "").upper())[:6]
    return t or "ASSET"


def create_asset(name, category="", blurb="", ticker="", created_by=None,
                 link="", image="", proof="", status="pending"):
    """Submit a new asset. Users submit (status='pending') -> the team vets the proof of
    concept -> approve flips it to 'listed' and tradeable. Seeds/admin pass status='listed'."""
    name = (name or "").strip()
    if len(name) < 2:
        return {"error": "Give it a name (2+ chars)."}
    ticker = (ticker or "").strip().upper() or _slug_ticker(name)
    if not _TICKER_RE.match(ticker):
        return {"error": "Ticker: 2-8 letters or numbers."}
    status = status if status in ("pending", "listed", "rejected") else "pending"
    init_db()
    with _conn() as c:
        base, t, k = ticker[:6], ticker, 1
        while c.execute("SELECT 1 AS x FROM exch_assets WHERE ticker=?", (t,)).fetchone():
            k += 1
            t = (base + str(k))[:8]
        cur = c.execute(
            "INSERT INTO exch_assets (name,ticker,category,blurb,link,image,proof,status,"
            "shares,created_by,created_at) VALUES (?,?,?,?,?,?,?,?,0,?,?)",
            (name[:80], t, (category or "Culture")[:40], (blurb or "")[:240],
             (link or "")[:300], (image or "")[:300], (proof or "")[:600], status,
             created_by, _now()))
        return {"id": cur.lastrowid, "ticker": t, "status": status}


# ── trading ──────────────────────────────────────────────────────────────────

def buy(user_id, ticker, bucks):
    try:
        bucks = int(bucks)
    except (TypeError, ValueError):
        return {"error": "Enter a whole number of Bucks."}
    if bucks < MIN_BUY:
        return {"error": f"Minimum buy is {MIN_BUY} Bucks."}
    bal = parlor.get_balance(user_id)        # ensures the wallet row exists
    if bal < bucks:
        return {"error": "Not enough Bucks."}
    init_db()
    with _conn() as c:
        a = c.execute("SELECT * FROM exch_assets WHERE ticker=?", ((ticker or "").upper(),)).fetchone()
        if not a:
            return {"error": "No such asset."}
        if (a["status"] or "pending") != "listed":
            return {"error": "This listing isn't live for trading yet."}
        s = a["shares"] or 0.0
        n = _shares_for(s, bucks)
        if n <= 0:
            return {"error": "Buy too small."}
        _adjust(c, user_id, -bucks)
        c.execute("UPDATE exch_assets SET shares=? WHERE id=?", (s + n, a["id"]))
        h = c.execute("SELECT shares,cost FROM exch_holdings WHERE user_id=? AND asset_id=?",
                      (user_id, a["id"])).fetchone()
        if h:
            c.execute("UPDATE exch_holdings SET shares=?, cost=? WHERE user_id=? AND asset_id=?",
                      (h["shares"] + n, h["cost"] + bucks, user_id, a["id"]))
        else:
            c.execute("INSERT INTO exch_holdings (user_id,asset_id,shares,cost) VALUES (?,?,?,?)",
                      (user_id, a["id"], n, bucks))
    return {"ok": True, "shares": round(n, 4), "avg_price": round(bucks / n, 4),
            "new_price": round(price(s + n), 4), "balance": parlor.get_balance(user_id)}


def sell(user_id, ticker, shares):
    try:
        shares = float(shares)
    except (TypeError, ValueError):
        return {"error": "Enter a number of shares."}
    if shares <= 0:
        return {"error": "Nothing to sell."}
    init_db()
    with _conn() as c:
        a = c.execute("SELECT * FROM exch_assets WHERE ticker=?", ((ticker or "").upper(),)).fetchone()
        if not a:
            return {"error": "No such asset."}
        h = c.execute("SELECT shares,cost FROM exch_holdings WHERE user_id=? AND asset_id=?",
                      (user_id, a["id"])).fetchone()
        if not h or h["shares"] <= 0:
            return {"error": "You hold none of this."}
        sell_n = min(shares, h["shares"])
        s = a["shares"] or 0.0
        value = int(round(_sell_value(s, sell_n)))
        frac = sell_n / h["shares"] if h["shares"] else 1.0
        cost_out = int(round(h["cost"] * frac))
        new_shares = h["shares"] - sell_n
        _adjust(c, user_id, value)
        c.execute("UPDATE exch_assets SET shares=? WHERE id=?", (max(0.0, s - sell_n), a["id"]))
        if new_shares <= 1e-9:
            c.execute("DELETE FROM exch_holdings WHERE user_id=? AND asset_id=?", (user_id, a["id"]))
        else:
            c.execute("UPDATE exch_holdings SET shares=?, cost=? WHERE user_id=? AND asset_id=?",
                      (new_shares, max(0, h["cost"] - cost_out), user_id, a["id"]))
        pnl = value - cost_out                       # scout track record: realized P&L on this exit
        if c.execute("SELECT 1 AS x FROM exch_stats WHERE user_id=?", (user_id,)).fetchone():
            c.execute("UPDATE exch_stats SET realized=realized+?, wins=wins+?, losses=losses+? WHERE user_id=?",
                      (pnl, 1 if pnl > 0 else 0, 1 if pnl < 0 else 0, user_id))
        else:
            c.execute("INSERT INTO exch_stats (user_id, realized, wins, losses) VALUES (?,?,?,?)",
                      (user_id, pnl, 1 if pnl > 0 else 0, 1 if pnl < 0 else 0))
    return {"ok": True, "sold": round(sell_n, 4), "value": value, "pnl": value - cost_out,
            "new_price": round(price(max(0.0, s - sell_n)), 4), "balance": parlor.get_balance(user_id)}


# ── views ────────────────────────────────────────────────────────────────────

def list_assets(category=None, limit=300):
    init_db()
    out = []
    with _conn() as c:
        rows = c.execute("SELECT * FROM exch_assets WHERE status='listed' ORDER BY shares DESC, id DESC").fetchall()
        for a in rows:
            if category and (a["category"] or "Culture") != category:
                continue
            s = a["shares"] or 0.0
            holders = c.execute(
                "SELECT COUNT(*) AS n FROM exch_holdings WHERE asset_id=? AND shares>0",
                (a["id"],)).fetchone()["n"]
            out.append({"id": a["id"], "name": a["name"], "ticker": a["ticker"],
                        "category": a["category"] or "Culture", "blurb": a["blurb"] or "",
                        "price": round(price(s), 4), "shares": round(s, 2),
                        "mcap": int(round(market_cap(s))), "holders": holders})
    return out[:limit]


def get_asset(ticker):
    init_db()
    with _conn() as c:
        a = c.execute("SELECT * FROM exch_assets WHERE ticker=?", ((ticker or "").upper(),)).fetchone()
        if not a:
            return None
        s = a["shares"] or 0.0
        holders = c.execute("SELECT COUNT(*) AS n FROM exch_holdings WHERE asset_id=? AND shares>0",
                            (a["id"],)).fetchone()["n"]
        keys = a.keys()
        g = lambda k: (a[k] if k in keys and a[k] else "")
        owner = None
        if a["created_by"]:
            try:
                from brain import accounts
                u = accounts.get_user(a["created_by"])
                if u:
                    owner = {"id": u["id"], "name": u["name"], "handle": u.get("handle", "")}
            except Exception:
                owner = None
        return {"id": a["id"], "name": a["name"], "ticker": a["ticker"],
                "category": a["category"] or "Culture", "blurb": g("blurb"),
                "link": g("link"), "image": g("image"), "proof": g("proof"),
                "status": a["status"] or "pending", "owner": owner,
                "price": round(price(s), 4), "shares": round(s, 2),
                "mcap": int(round(market_cap(s))), "holders": holders}


def portfolio(user_id):
    init_db()
    out = []
    with _conn() as c:
        hs = c.execute(
            "SELECT h.shares, h.cost, a.name, a.ticker, a.category, a.shares AS supply"
            " FROM exch_holdings h JOIN exch_assets a ON a.id=h.asset_id"
            " WHERE h.user_id=? AND h.shares>0 ORDER BY h.cost DESC", (user_id,)).fetchall()
        for h in hs:
            val = int(round(_sell_value(h["supply"] or 0.0, h["shares"])))
            out.append({"ticker": h["ticker"], "name": h["name"],
                        "category": h["category"] or "Culture", "shares": round(h["shares"], 3),
                        "cost": h["cost"], "value": val, "pnl": val - h["cost"],
                        "pnl_pct": round((val - h["cost"]) / h["cost"] * 100, 1) if h["cost"] else 0.0,
                        "price": round(price(h["supply"] or 0.0), 4)})
    invested = sum(r["cost"] for r in out)
    value = sum(r["value"] for r in out)
    return {"holdings": out, "invested": invested, "value": value,
            "pnl": value - invested, "balance": parlor.get_balance(user_id)}


# ── scout reputation (credibility for backing winners early) ─────────────────

def scout(user_id):
    """A user's scouting track record: realized + unrealized P&L on the Exchange, hit
    rate, open positions, best call. The bonding curve already pays early backers, so
    P&L IS the earliness signal; this turns it into a public credibility score for taste."""
    init_db()
    p = portfolio(user_id)
    unrealized = p["pnl"]
    best = max(p["holdings"], key=lambda h: h["pnl"], default=None) if p["holdings"] else None
    with _conn() as c:
        st = c.execute("SELECT realized, wins, losses FROM exch_stats WHERE user_id=?",
                       (user_id,)).fetchone()
    realized = (st["realized"] if st else 0)
    wins = (st["wins"] if st else 0) + sum(1 for h in p["holdings"] if h["pnl"] > 0)
    losses = (st["losses"] if st else 0) + sum(1 for h in p["holdings"] if h["pnl"] < 0)
    decided = wins + losses
    return {"score": realized + unrealized, "realized": realized, "unrealized": unrealized,
            "open_positions": len(p["holdings"]), "wins": wins, "losses": losses,
            "hit_rate": round(wins / decided * 100) if decided else None,
            "best_call": ({"ticker": best["ticker"], "pnl": best["pnl"]}
                          if best and best["pnl"] > 0 else None)}


def scout_leaderboard(limit=20):
    """Rank scouts by total (realized + unrealized) Exchange P&L. The taste board."""
    init_db()
    with _conn() as c:
        uids = set(r["user_id"] for r in c.execute("SELECT DISTINCT user_id FROM exch_holdings").fetchall())
        uids |= set(r["user_id"] for r in c.execute("SELECT user_id FROM exch_stats").fetchall())
    out = [dict(user_id=uid, **scout(uid)) for uid in uids]
    out.sort(key=lambda x: x["score"], reverse=True)
    return out[:limit]


# ── moderation (the team vets submissions) ───────────────────────────────────

def list_pending():
    """Submissions awaiting review (for the team / Pit Boss)."""
    init_db()
    with _conn() as c:
        rows = c.execute("SELECT * FROM exch_assets WHERE status='pending' ORDER BY id DESC").fetchall()
        def g(a, k):
            return a[k] if k in a.keys() and a[k] else ""
        return [{"id": a["id"], "name": a["name"], "ticker": a["ticker"],
                 "category": a["category"] or "Culture", "blurb": g(a, "blurb"),
                 "link": g(a, "link"), "image": g(a, "image"), "proof": g(a, "proof"),
                 "created_by": a["created_by"], "created_at": a["created_at"]} for a in rows]


def moderate(asset_id, action):
    """Approve (-> listed + tradeable) or reject a submission."""
    action = (action or "").lower()
    if action not in ("approve", "reject"):
        return {"error": "Action must be approve or reject."}
    init_db()
    with _conn() as c:
        a = c.execute("SELECT status, created_by, name, ticker FROM exch_assets WHERE id=?",
                      (asset_id,)).fetchone()
        if not a:
            return {"error": "No such submission."}
        new = "listed" if action == "approve" else "rejected"
        c.execute("UPDATE exch_assets SET status=? WHERE id=?", (new, asset_id))
        return {"ok": True, "status": new, "created_by": a["created_by"],
                "name": a["name"], "ticker": a["ticker"]}


# ── seed ─────────────────────────────────────────────────────────────────────

_SEED = [
    {"name": "A24 Films",                 "ticker": "A24",    "category": "Film",   "blurb": "The indie studio everyone roots for."},
    {"name": "Hollow Knight: Silksong",   "ticker": "SILK",   "category": "Games",  "blurb": "The most-awaited indie sequel."},
    {"name": "Jordan Brand",              "ticker": "JORDAN", "category": "Drops",  "blurb": "Sneaker resale royalty."},
    {"name": "Studio Ghibli",             "ticker": "GHIBLI", "category": "Film",   "blurb": "Hand-drawn legends."},
    {"name": "The Corner Spot",           "ticker": "EATS",   "category": "Food",   "blurb": "The local-restaurant index."},
    {"name": "Rising Painter Index",      "ticker": "BRUSH",  "category": "Art",    "blurb": "Back the next big brush."},
]


def seed_if_empty():
    init_db()
    with _conn() as c:
        n = c.execute("SELECT COUNT(*) AS n FROM exch_assets").fetchone()["n"]
    if n:
        return
    for a in _SEED:
        create_asset(a["name"], a["category"], a["blurb"], a["ticker"], status="listed")
