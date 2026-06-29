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
from datetime import datetime, timezone, timedelta

from brain import db

START_CREDITS = 1000          # everyone's opening play-money balance
MIN_STAKE     = 10
MAX_STAKE     = 1000
_AUTO_RULES   = ("close_green", "week_up_3", "above_price", "below_price", "beats")  # price-settleable


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
            threshold  REAL,
            ticker2    TEXT,
            closes_at  TEXT,
            status     TEXT NOT NULL DEFAULT 'open',
            outcome    TEXT,
            created_by INTEGER,
            created_at TEXT NOT NULL
        )""")
        # migrate older Parlor DBs to the price-threshold + head-to-head columns
        mcols = [r["name"] for r in c.execute("PRAGMA table_info(parlor_markets)").fetchall()]
        if "threshold" not in mcols:
            c.execute("ALTER TABLE parlor_markets ADD COLUMN threshold REAL")
        if "ticker2" not in mcols:
            c.execute("ALTER TABLE parlor_markets ADD COLUMN ticker2 TEXT")
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
    keys = m.keys()
    return {
        "id": m["id"], "question": m["question"], "category": m["category"] or "Markets",
        "ticker": m["ticker"] or "", "closes_at": m["closes_at"] or "",
        "ticker2": (m["ticker2"] if "ticker2" in keys and m["ticker2"] else ""),
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


def _default_close(rule):
    """Default settle time for an auto-rule market: ~next US close for the daily rules,
    a week out for the weekly ones, none for manual."""
    now = datetime.now(timezone.utc)
    if rule in ("close_green", "above_price", "below_price"):
        eod = now.replace(hour=21, minute=30, second=0, microsecond=0)
        if eod <= now:
            eod = eod + timedelta(days=1)
        return eod.isoformat()
    if rule in ("week_up_3", "beats"):
        return (now + timedelta(days=7)).isoformat()
    return ""


def create_market(question, ticker="", rule="", closes_at="", category="",
                  created_by=None, threshold=None, ticker2=""):
    question = (question or "").strip()
    if len(question) < 8:
        return {"error": "Question is too short."}
    rule = (rule or "").lower()
    try:
        threshold = float(threshold) if threshold not in (None, "") else None
    except (TypeError, ValueError):
        threshold = None
    closes_at = (closes_at or "") or _default_close(rule)   # auto close time for auto-rules
    if rule in ("above_price", "below_price") and threshold is None:
        return {"error": "An above/below-price market needs a price threshold."}
    if rule == "beats" and not (ticker2 or "").strip():
        return {"error": "A head-to-head race needs a second ticker."}
    if closes_at and closes_at <= _now():
        return {"error": "Close time must be in the future."}
    init_db()
    with _conn() as c:
        cur = c.execute(
            "INSERT INTO parlor_markets (question, category, ticker, rule, threshold, ticker2,"
            " closes_at, status, created_by, created_at) VALUES (?,?,?,?,?,?,?,'open',?,?)",
            (question[:200], (category or "Markets")[:40], (ticker or "").upper()[:6],
             rule[:40], threshold, (ticker2 or "").upper()[:6], (closes_at or "")[:40],
             created_by, _now()))
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
        opp = "no" if side == "yes" else "yes"
        if c.execute("SELECT 1 AS x FROM parlor_bets WHERE market_id=? AND user_id=? AND side=?",
                     (market_id, user_id, opp)).fetchone():
            return {"error": "You already backed the other side — no hedging both ways."}
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
        m = c.execute("SELECT status, question FROM parlor_markets WHERE id=?", (market_id,)).fetchone()
        if not m:
            return {"error": "No such market."}
        if m["status"] == "resolved":
            return {"error": "Market already resolved."}
        question = m["question"]
        deltas = {}   # user_id -> net Bucks change (for settle notifications)
        # claim the market atomically so a double-click or the autoresolver racing the
        # admin can't both pay out (the WHERE status='open' makes only one win the claim)
        claim = c.execute("UPDATE parlor_markets SET status='resolved', outcome=? WHERE id=? AND status='open'",
                          (outcome, market_id))
        if getattr(claim, "rowcount", 1) == 0:
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
            wins_b = [b for b in bets if b["side"] == outcome]
            n, running = len(wins_b), 0
            for i, b in enumerate(wins_b):
                # floor each share; the last winner gets the exact remainder, so the
                # pool is conserved (no Bucks minted or destroyed by rounding)
                share = (total - running) if i == n - 1 else \
                        (int(b["stake"] / win_pool * total) if win_pool else b["stake"])
                running += share
                _adjust(c, b["user_id"], share)
                c.execute("UPDATE parlor_bets SET payout=?, settled=1 WHERE id=?", (share, b["id"]))
                paid += share
                deltas[b["user_id"]] = deltas.get(b["user_id"], 0) + (share - b["stake"])
            for b in bets:
                if b["side"] != outcome:
                    c.execute("UPDATE parlor_bets SET payout=0, settled=1 WHERE id=?", (b["id"],))
                    deltas[b["user_id"]] = deltas.get(b["user_id"], 0) - b["stake"]
        # market was already claimed/marked resolved at the top of the transaction
    return {"market_id": market_id, "outcome": outcome, "total_pool": total,
            "winners": len(winners), "paid": paid, "voided": voided,
            "question": question,
            "bettors": [{"user_id": uid, "delta": d} for uid, d in deltas.items()]}


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


def leaderboard(limit=20, sort="rich"):
    """Rank players. sort='rich' (by Bucks) or 'sharp' (win-rate, min 3 decided bets) —
    the Sharpest board is the skill signal, not just who started with the most left."""
    init_db()
    with _conn() as c:
        wallets = c.execute("SELECT user_id, credits FROM parlor_wallet").fetchall()
        out = []
        for w in wallets:
            settled = c.execute("SELECT payout, stake FROM parlor_bets WHERE user_id=? AND settled=1",
                                (w["user_id"],)).fetchall()
            wins   = sum(1 for b in settled if (b["payout"] or 0) > b["stake"])
            losses = sum(1 for b in settled if (b["payout"] or 0) < b["stake"])
            decided = wins + losses
            out.append({"user_id": w["user_id"], "credits": w["credits"], "bets": len(settled),
                        "wins": wins, "decided": decided,
                        "win_rate": round(wins / decided * 100) if decided else None,
                        "net": w["credits"] - START_CREDITS})
    if sort == "sharp":
        out = [r for r in out if r["decided"] >= 3]
        out.sort(key=lambda x: (x["win_rate"] or 0, x["net"]), reverse=True)
    else:
        out.sort(key=lambda x: x["credits"], reverse=True)
    return out[:limit]


def record(user_id):
    """A user's Parlor track record (settled bets only) — their credibility signal."""
    init_db()
    with _conn() as c:
        rows = c.execute("SELECT stake, payout FROM parlor_bets WHERE user_id=? AND settled=1",
                         (user_id,)).fetchall()
        w = c.execute("SELECT credits FROM parlor_wallet WHERE user_id=?", (user_id,)).fetchone()
    wins   = sum(1 for r in rows if (r["payout"] or 0) > r["stake"])
    losses = sum(1 for r in rows if (r["payout"] or 0) < r["stake"])
    voids  = sum(1 for r in rows if (r["payout"] or 0) == r["stake"])
    decided = wins + losses
    credits = w["credits"] if w else START_CREDITS
    return {"bets": len(rows), "wins": wins, "losses": losses, "voids": voids,
            "win_rate": round(wins / decided * 100) if decided else None,
            "net": credits - START_CREDITS, "credits": credits}


# ── auto-resolution (price-backed rules) ─────────────────────────────────────

def eval_rule(rule, closes, threshold=None, closes2=None):
    """Evaluate an auto-rule against recent daily closes (oldest..newest). Pure +
    testable; the caller supplies the price series. -> 'yes' / 'no' / None.
      close_green  today's close > yesterday's
      week_up_3    > +3% over ~5 sessions
      above_price  latest close > threshold
      below_price  latest close < threshold
      beats        ticker's week move > ticker2's (closes2) week move"""
    try:
        closes = [float(x) for x in closes]
    except (TypeError, ValueError):
        return None
    if len(closes) < 2:
        return None
    rule = (rule or "").lower()
    if rule == "close_green":
        return "yes" if closes[-1] > closes[-2] else "no"
    if rule == "week_up_3":
        if len(closes) < 6:
            return None
        chg = (closes[-1] / closes[-6] - 1) * 100 if closes[-6] else 0
        return "yes" if chg > 3 else "no"
    if rule in ("above_price", "below_price"):
        try:
            threshold = float(threshold)
        except (TypeError, ValueError):
            return None
        if rule == "above_price":
            return "yes" if closes[-1] > threshold else "no"
        return "yes" if closes[-1] < threshold else "no"
    if rule == "beats":
        try:
            closes2 = [float(x) for x in (closes2 or [])]
        except (TypeError, ValueError):
            return None
        if len(closes) < 6 or len(closes2) < 6 or not closes[-6] or not closes2[-6]:
            return None
        a = closes[-1] / closes[-6] - 1
        b = closes2[-1] / closes2[-6] - 1
        return "yes" if a > b else "no"
    return None


def markets_due():
    """Open, ticker-backed, auto-rule markets whose close time has passed. The server
    settles these off the price feed. -> [{id, ticker, rule}]."""
    init_db()
    now = _now()
    with _conn() as c:
        rows = c.execute(
            "SELECT id, ticker, rule, threshold, ticker2, closes_at FROM parlor_markets"
            " WHERE status='open'").fetchall()
    return [{"id": m["id"], "ticker": m["ticker"], "rule": m["rule"],
             "threshold": m["threshold"], "ticker2": m["ticker2"] or ""}
            for m in rows
            if m["rule"] in _AUTO_RULES and (m["ticker"] or "")
            and (m["closes_at"] or "") and m["closes_at"] <= now]


# ── seed ─────────────────────────────────────────────────────────────────────

_DEFAULT_MARKETS = [
    {"question": "Will the S&P 500 (SPY) close GREEN today?", "ticker": "SPY",
     "rule": "close_green", "category": "The Market"},
    {"question": "Will NVDA finish the week up more than 3%?", "ticker": "NVDA",
     "rule": "week_up_3", "category": "The Market"},
    {"question": "Will NVDA close above $170 today?", "ticker": "NVDA",
     "rule": "above_price", "threshold": 170, "category": "The Market"},
    {"question": "The Race: will NVDA outrun AMD this week?", "ticker": "NVDA",
     "ticker2": "AMD", "rule": "beats", "category": "The Races"},
    {"question": "Will any megacap (AAPL/MSFT/NVDA/AMZN/GOOGL) pop 5%+ today?", "ticker": "",
     "rule": "manual", "category": "The Races"},
    {"question": "Will Bitcoin trade above its monthly open at the close?", "ticker": "",
     "rule": "manual", "category": "Crypto"},
    {"question": "Will the Fed CUT rates at the next FOMC meeting?", "ticker": "",
     "rule": "manual", "category": "Macro"},
    {"question": "Will the reigning champion repeat this season?", "ticker": "",
     "rule": "manual", "category": "Sports"},
    {"question": "Will a major bill clear Congress this month?", "ticker": "",
     "rule": "manual", "category": "Politics"},
]


def seed_if_empty():
    """Lay down a starter card of markets the first time the Parlor is opened.
    create_market() stamps auto-rule markets with a close time on its own."""
    init_db()
    with _conn() as c:
        n = c.execute("SELECT COUNT(*) AS n FROM parlor_markets").fetchone()["n"]
    if n:
        return
    for mk in _DEFAULT_MARKETS:
        create_market(**mk)


# ── culture markets (bet on taste, not just tickers) ─────────────────────────
# Kalshi/Polymarket own finance + politics; nobody owns TASTE. These are 'manual'
# markets (no price feed for culture) — the Pit Boss settles them by hand, and later
# oracles (Box Office Mojo, OpenCritic, Spotify, Kickstarter) can auto-settle some.
_CULTURE_CATEGORIES = ("Film", "Art", "Music", "Games", "Design", "Drops", "Studios")

_CULTURE_MARKETS = [
    {"question": "Will the year's biggest film open above $100M domestic?", "rule": "manual", "category": "Film"},
    {"question": "Will an A24 release crack 90+ on the critics' aggregators this year?", "rule": "manual", "category": "Film"},
    {"question": "Will this month's most-hyped gallery drop sell out within 24 hours?", "rule": "manual", "category": "Art"},
    {"question": "Will a debut artist pass 1M monthly listeners this quarter?", "rule": "manual", "category": "Music"},
    {"question": "Will the year's most-anticipated indie game score 90+ on OpenCritic?", "rule": "manual", "category": "Games"},
    {"question": "Will the next flagship sneaker drop resell above retail within 30 days?", "rule": "manual", "category": "Drops"},
    {"question": "Will a major streetwear collab sell out on launch day?", "rule": "manual", "category": "Design"},
    {"question": "Will a small studio's next crowdfunding campaign hit its funding goal?", "rule": "manual", "category": "Studios"},
]


def seed_culture():
    """Idempotent top-up: add the culture starter card if the Parlor has none in the
    culture categories yet (so an already-seeded board still gets the new categories)."""
    init_db()
    with _conn() as c:
        qs = ",".join("?" * len(_CULTURE_CATEGORIES))
        has = c.execute(f"SELECT 1 AS x FROM parlor_markets WHERE category IN ({qs}) LIMIT 1",
                        _CULTURE_CATEGORIES).fetchone()
    if has:
        return
    for mk in _CULTURE_MARKETS:
        create_market(**mk)
