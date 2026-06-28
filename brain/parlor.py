"""
MIDAS — The Parlor: play-money prediction markets (Phase 0 of the Kalshi-rival path).

No real money. Users stake play-credits ("Bucks") on YES/NO outcomes, parimutuel
style: all YES stakes + all NO stakes form one pool, and the winning side splits the
whole pool in proportion to their stake (the same mechanic as a horse-race tote board,
so the crowd sets the odds, no bookmaker needed). Markets settle from the price data
when they're ticker-backed, or by an admin.

This is the practice mode + proof-of-concept; the identical UX later rides Kalshi's
regulated API for real-money event trading. No gambling license is needed here because
play-credits are NOT cashable, which makes this a skill contest, not a wager.
"""
from datetime import datetime, timezone

from brain import db

START_CREDITS = 1000          # everyone's opening play-money balance
MIN_STAKE     = 10
MAX_STAKE     = 1000


def _conn():
    return db.get_conn()


def _now():
    return datetime.now(timezone.utc).isoformat()


def init_db():
    with _conn() as c:
        c.execute("""CREATE TABLE IF NOT EXISTS parlor_wallet (
            user_id  INTEGER PRIMARY KEY,
            credits  INTEGER NOT NULL DEFAULT 1000,
            updated  TEXT
        )""")
        c.execute("""CREATE TABLE IF NOT EXISTS parlor_markets (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            question   TEXT NOT NULL,
            category   TEXT,
            ticker     TEXT,
            rule       TEXT,
            closes_at  TEXT,
            status     TEXT NOT NULL DEFAULT 'open',
            outcome    TEXT,
            created_by INTEGER,
            created_at TEXT NOT NULL
        )""")
        c.execute("""CREATE TABLE IF NOT EXISTS parlor_bets (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            market_id  INTEGER NOT NULL,
            user_id    INTEGER NOT NULL,
            side       TEXT NOT NULL,
            stake      INTEGER NOT NULL,
            payout     INTEGER,
            settled    INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL
        )""")


# ── wallet ───────────────────────────────────────────────────────────────────

def get_balance(user_id):
    """Return a user's play-credit balance, lazily granting the opening stake."""
    init_db()
    with _conn() as c:
        r = c.execute("SELECT credits FROM parlor_wallet WHERE user_id=?", (user_id,)).fetchone()
        if r is None:
            c.execute("INSERT INTO parlor_wallet (user_id, credits, updated) VALUES (?,?,?)",
                      (user_id, START_CREDITS, _now()))
            return START_CREDITS
        return r["credits"]


def _adjust(c, user_id, delta):
    c.execute("UPDATE parlor_wallet SET credits = credits + ?, updated=? WHERE user_id=?",
              (delta, _now(), user_id))


# ── markets ──────────────────────────────────────────────────────────────────

def _pools(c, market_id):
    bets = c.execute("SELECT side, stake FROM parlor_bets WHERE market_id=?", (market_id,)).fetchall()
    yes_pool = sum(b["stake"] for b in bets if b["side"] == "yes")
    no_pool  = sum(b["stake"] for b in bets if b["side"] == "no")
    return yes_pool, no_pool, len(bets)


def _market_view(c, m):
    yes_pool, no_pool, n = _pools(c, m["id"])
    total = yes_pool + no_pool
    return {
        "id": m["id"], "question": m["question"], "category": m["category"] or "Markets",
        "ticker": m["ticker"] or "", "closes_at": m["closes_at"] or "",
        "status": m["status"], "outcome": m["outcome"],
        "yes_pool": yes_pool, "no_pool": no_pool, "total": total, "bets": n,
        # crowd-implied probability + the parimutuel multiplier each side pays
        "yes_pct": round(yes_pool / total * 100) if total else 50,
        "no_pct":  round(no_pool / total * 100) if total else 50,
        "yes_odds": round(total / yes_pool, 2) if yes_pool else None,
        "no_odds":  round(total / no_pool, 2) if no_pool else None,
    }


def list_markets(status="open"):
    init_db()
    with _conn() as c:
        if status:
            rows = c.execute("SELECT * FROM parlor_markets WHERE status=? ORDER BY id DESC",
                             (status,)).fetchall()
        else:
            rows = c.execute("SELECT * FROM parlor_markets ORDER BY id DESC").fetchall()
        return [_market_view(c, m) for m in rows]


def get_market(market_id):
    init_db()
    with _conn() as c:
        m = c.execute("SELECT * FROM parlor_markets WHERE id=?", (market_id,)).fetchone()
        return _market_view(c, m) if m else None


def create_market(question, ticker="", rule="", closes_at="", category="", created_by=None):
    question = (question or "").strip()
    if len(question) < 8:
        return {"error": "Question is too short."}
    init_db()
    with _conn() as c:
        cur = c.execute(
            "INSERT INTO parlor_markets (question, category, ticker, rule, closes_at, status, created_by, created_at)"
            " VALUES (?,?,?,?,?,'open',?,?)",
            (question[:200], (category or "Markets")[:40], (ticker or "").upper()[:6],
             (rule or "")[:40], (closes_at or "")[:40], created_by, _now()))
        return {"id": cur.lastrowid}


# ── betting ──────────────────────────────────────────────────────────────────

def place_bet(user_id, market_id, side, stake):
    side = (side or "").lower()
    if side not in ("yes", "no"):
        return {"error": "Pick YES or NO."}
    try:
        stake = int(stake)
    except (TypeError, ValueError):
        return {"error": "Enter a whole number of Bucks."}
    if stake < MIN_STAKE or stake > MAX_STAKE:
        return {"error": f"Stake must be between {MIN_STAKE} and {MAX_STAKE} Bucks."}
    bal = get_balance(user_id)   # ensures the wallet exists
    init_db()
    with _conn() as c:
        m = c.execute("SELECT status FROM parlor_markets WHERE id=?", (market_id,)).fetchone()
        if not m:
            return {"error": "No such market."}
        if m["status"] != "open":
            return {"error": "Betting on this market is closed."}
        if bal < stake:
            return {"error": "Not enough Bucks for that stake."}
        _adjust(c, user_id, -stake)
        c.execute("INSERT INTO parlor_bets (market_id, user_id, side, stake, created_at)"
                  " VALUES (?,?,?,?,?)", (market_id, user_id, side, stake, _now()))
    return {"ok": True, "balance": get_balance(user_id)}


def resolve_market(market_id, outcome):
    """Settle a market parimutuel-style. Winners split the whole pool pro-rata; if
    nobody called it right the market voids and every stake is refunded."""
    outcome = (outcome or "").lower()
    if outcome not in ("yes", "no"):
        return {"error": "Outcome must be yes or no."}
    init_db()
    with _conn() as c:
        m = c.execute("SELECT status FROM parlor_markets WHERE id=?", (market_id,)).fetchone()
        if not m:
            return {"error": "No such market."}
        if m["status"] == "resolved":
            return {"error": "Market already resolved."}
        bets = c.execute("SELECT * FROM parlor_bets WHERE market_id=? AND settled=0",
                         (market_id,)).fetchall()
        yes_pool = sum(b["stake"] for b in bets if b["side"] == "yes")
        no_pool  = sum(b["stake"] for b in bets if b["side"] == "no")
        total    = yes_pool + no_pool
        win_pool = yes_pool if outcome == "yes" else no_pool
        winners  = [b for b in bets if b["side"] == outcome]
        paid, voided = 0, False

        if total > 0 and (not winners or win_pool == 0):
            # nobody on the winning side -> void: refund all stakes
            voided = True
            for b in bets:
                _adjust(c, b["user_id"], b["stake"])
                c.execute("UPDATE parlor_bets SET payout=?, settled=1 WHERE id=?",
                          (b["stake"], b["id"]))
        else:
            for b in bets:
                if b["side"] == outcome:
                    share = int(round(b["stake"] / win_pool * total)) if win_pool else b["stake"]
                    _adjust(c, b["user_id"], share)
                    c.execute("UPDATE parlor_bets SET payout=?, settled=1 WHERE id=?",
                              (share, b["id"]))
                    paid += share
                else:
                    c.execute("UPDATE parlor_bets SET payout=0, settled=1 WHERE id=?", (b["id"],))
        c.execute("UPDATE parlor_markets SET status='resolved', outcome=? WHERE id=?",
                  (outcome, market_id))
    return {"market_id": market_id, "outcome": outcome, "total_pool": total,
            "winners": len(winners), "paid": paid, "voided": voided}


# ── views ────────────────────────────────────────────────────────────────────

def user_bets(user_id):
    init_db()
    with _conn() as c:
        rows = c.execute(
            "SELECT b.market_id, b.side, b.stake, b.payout, b.settled,"
            " m.question, m.status AS m_status, m.outcome AS m_outcome"
            " FROM parlor_bets b JOIN parlor_markets m ON m.id = b.market_id"
            " WHERE b.user_id=? ORDER BY b.id DESC", (user_id,)).fetchall()
        return [{"market_id": r["market_id"], "question": r["question"], "side": r["side"],
                 "stake": r["stake"], "payout": r["payout"], "settled": bool(r["settled"]),
                 "status": r["m_status"], "outcome": r["m_outcome"]} for r in rows]


def leaderboard(limit=20):
    init_db()
    with _conn() as c:
        rows = c.execute("SELECT user_id, credits FROM parlor_wallet ORDER BY credits DESC LIMIT ?",
                         (limit,)).fetchall()
        out = []
        for w in rows:
            settled = c.execute("SELECT payout, stake FROM parlor_bets WHERE user_id=? AND settled=1",
                                (w["user_id"],)).fetchall()
            wins = sum(1 for b in settled if (b["payout"] or 0) > b["stake"])
            out.append({"user_id": w["user_id"], "credits": w["credits"],
                        "bets": len(settled), "wins": wins})
    return out


# ── seed ─────────────────────────────────────────────────────────────────────

_DEFAULT_MARKETS = [
    {"question": "Will the S&P 500 (SPY) close GREEN today?", "ticker": "SPY",
     "rule": "close_green", "category": "The Market"},
    {"question": "Will NVDA finish the week up more than 3%?", "ticker": "NVDA",
     "rule": "week_up_3", "category": "The Market"},
    {"question": "Will any megacap (AAPL/MSFT/NVDA/AMZN/GOOGL) pop 5%+ today?", "ticker": "",
     "rule": "manual", "category": "The Races"},
    {"question": "Will Bitcoin trade above its monthly open at the close?", "ticker": "",
     "rule": "manual", "category": "Crypto"},
    {"question": "Will the Fed CUT rates at the next FOMC meeting?", "ticker": "",
     "rule": "manual", "category": "Macro"},
]


def seed_if_empty():
    """Lay down a starter card of markets the first time the Parlor is opened."""
    init_db()
    with _conn() as c:
        n = c.execute("SELECT COUNT(*) AS n FROM parlor_markets").fetchone()["n"]
    if n:
        return
    for mk in _DEFAULT_MARKETS:
        create_market(**mk)
