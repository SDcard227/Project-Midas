"""
MIDAS — demo seeder. Populates the DB with realistic fake users so you can see the
platform alive: real names + locations, reputations (scored from real calls), Floor
chatter, upvotes, and portfolios with live P&L.

Run:  python seed_demo.py          (idempotent — clears prior demo data first)
Clear: python seed_demo.py --clear
All demo users use emails demo_*@midas.fake so they're easy to wipe.
"""
import sys, os, re, json, random
from datetime import datetime, timezone, timedelta

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)

# load .env so Alpaca keys are available for price scoring
_envf = os.path.join(BASE, ".env")
if os.path.exists(_envf):
    for _ln in open(_envf, encoding="utf-8"):
        _ln = _ln.strip()
        if _ln and not _ln.startswith("#") and "=" in _ln:
            _k, _v = _ln.split("=", 1)
            os.environ.setdefault(_k.strip(), _v.strip())

import sqlite3
from brain import accounts, reputation
from brain import comments as cmt

DB = os.path.join(BASE, "midas_users.db")


def conn():
    c = sqlite3.connect(DB); c.row_factory = sqlite3.Row; return c


def clear():
    accounts.init_db(); reputation.init_db(); cmt.init_db()
    with conn() as c:
        ids = [r["id"] for r in c.execute(
            "SELECT id FROM users WHERE email LIKE 'demo_%@midas.fake'").fetchall()]
        if ids:
            qs = ",".join("?" * len(ids))
            for tbl in ("comments", "holdings", "comment_votes", "user_rep"):
                c.execute(f"DELETE FROM {tbl} WHERE user_id IN ({qs})", ids)
            c.execute("DELETE FROM users WHERE email LIKE 'demo_%@midas.fake'")
        c.commit()
    print("cleared prior demo data")


if "--clear" in sys.argv:
    clear()
    sys.exit(0)

clear()

USERS = [
    ("Marcus", "Chen", "USA", "California", "premium"),
    ("Aisha", "Khan", "USA", "New York", "pro"),
    ("Diego", "Santos", "USA", "Texas", "free"),
    ("Priya", "Patel", "USA", "Illinois", "pro"),
    ("Liam", "OBrien", "USA", "Massachusetts", "free"),
    ("Sofia", "Rossi", "Italy", "Lazio", "free"),
    ("Kenji", "Tanaka", "Japan", "Tokyo", "premium"),
    ("Emma", "Johansson", "Sweden", "Stockholm", "free"),
    ("Carlos", "Mendez", "USA", "Florida", "pro"),
    ("Fatima", "Alsayed", "UAE", "Dubai", "premium"),
    ("Noah", "Williams", "Canada", "Ontario", "free"),
    ("Mia", "Nguyen", "USA", "Washington", "pro"),
    ("Lucas", "Silva", "Brazil", "Sao Paulo", "free"),
    ("Hannah", "Cohen", "USA", "New York", "free"),
    ("Omar", "Hassan", "USA", "Georgia", "pro"),
    ("Grace", "Kim", "USA", "California", "premium"),
]
HOLD_TICKERS = ["AAPL", "MSFT", "NVDA", "TSLA", "SPY", "AMD", "GOOGL", "META", "AMZN", "NFLX", "JPM"]
TAKES = [
    "loading up here, this run is just getting started",
    "overbought imo, taking some off the table",
    "the chart looks heavy, careful up here",
    "earnings are gonna surprise to the upside",
    "institutions are accumulating, watch the volume",
    "this is a falling knife, not catching it",
    "broke resistance clean, momentum is real",
    "macro headwinds, staying cautious here",
    "best risk/reward on the board right now",
    "rotation out of this sector, I'm out",
    "thesis hasn't changed, holding",
    "dead cat bounce, fading this",
    "this gaps up tomorrow, calling it now",
    "smart money is selling into this rally",
]


def back_ts(days):
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()


# real wire-story events (filter to clean tickers so reputation can score them)
events = []
lp = os.path.join(BASE, "signal_ledger.json")
if os.path.exists(lp):
    try:
        d = json.load(open(lp, encoding="utf-8"))
        for eid, ev in (d.get("events", {}) or {}).items():
            tk = (ev.get("ticker") or "").strip()
            if re.fullmatch(r"[A-Z]{1,5}", tk):
                events.append((eid, tk))
    except Exception as e:
        print("ledger read warn:", e)
random.shuffle(events)
print("usable wire stories:", len(events))

created = []
for (fn, ln, country, state, tier) in USERS:
    email = f"demo_{fn.lower()}.{ln.lower()}@midas.fake"
    res = accounts.create_user(email, "demopass123", fn, ln, country, state)
    if res.get("error"):
        print("  skip", email, res["error"]); continue
    uid = res["user"]["id"]
    accounts.set_tier(uid, tier)
    author = f"{fn} {ln[0]}."
    created.append((uid, author))
    with conn() as c:
        # comments on real stories: ~60% backdated (resolve -> reputation), ~40% fresh (Floor feed)
        for _ in range(random.randint(6, 10)):
            if not events:
                break
            eid, tk = random.choice(events)
            sent = random.choice(["bullish", "bearish", "bullish", "bearish", "neutral"])
            recent = random.random() < 0.4
            days = random.randint(0, 6) if recent else random.randint(20, 55)
            c.execute("INSERT INTO comments (event_id,ticker,user_id,author,text,sentiment,upvotes,created_at)"
                      " VALUES (?,?,?,?,?,?,0,?)",
                      (eid, tk, uid, author, random.choice(TAKES), sent, back_ts(days)))
        c.commit()
print("created", len(created), "demo users with calls + Floor comments")

# portfolios: 2-4 holdings each, entry = a past price so P&L is real
pcache = {}
def past_entry(tk):
    if tk not in pcache:
        cl = reputation._daily_closes(tk, datetime.now(timezone.utc) - timedelta(days=80))
        vals = list(cl.values())
        pcache[tk] = vals[len(vals) // 3] if vals else None
    return pcache[tk]

with conn() as c:
    for (uid, author) in created:
        for _ in range(random.randint(2, 4)):
            tk = random.choice(HOLD_TICKERS)
            ep = past_entry(tk)
            if not ep:
                continue
            sh = random.choice([5, 10, 15, 20, 25, 30, 50, 75])
            c.execute("INSERT INTO holdings (user_id,ticker,shares,entry_price,opened_at)"
                      " VALUES (?,?,?,?,?)", (uid, tk, sh, round(ep, 2), back_ts(random.randint(10, 60))))
    c.commit()
print("seeded portfolios")

# upvotes on Floor comments
with conn() as c:
    floor = [r["id"] for r in c.execute(
        "SELECT id FROM comments WHERE user_id IN (SELECT id FROM users WHERE email LIKE 'demo_%@midas.fake')").fetchall()]
    uids = [u[0] for u in created]
    for cid in floor:
        for voter in random.sample(uids, k=min(len(uids), random.randint(0, 6))):
            try:
                c.execute("INSERT INTO comment_votes (comment_id,user_id) VALUES (?,?)", (cid, voter))
                c.execute("UPDATE comments SET upvotes=upvotes+1 WHERE id=?", (cid,))
            except sqlite3.IntegrityError:
                pass
    c.commit()
print("seeded upvotes")

# compute reputation (so leaderboard + name colors populate)
for (uid, author) in created:
    try:
        reputation.compute(uid)
    except Exception as e:
        print("  rep fail", uid, e)
print("computed reputation for all demo users")

with conn() as c:
    nu = c.execute("SELECT COUNT(*) n FROM users WHERE email LIKE 'demo_%@midas.fake'").fetchone()["n"]
    nc = c.execute("SELECT COUNT(*) n FROM comments WHERE user_id IN (SELECT id FROM users WHERE email LIKE 'demo_%@midas.fake')").fetchone()["n"]
    nh = c.execute("SELECT COUNT(*) n FROM holdings").fetchone()["n"]
    top = c.execute("SELECT u.first_name, u.last_name, r.score FROM user_rep r JOIN users u ON u.id=r.user_id "
                    "WHERE u.email LIKE 'demo_%@midas.fake' AND r.total>0 ORDER BY r.score DESC LIMIT 3").fetchall()
print(f"\nDONE: {nu} users, {nc} comments, {nh} holdings")
print("top reputations:", [f"{r['first_name']} {r['last_name'][0]}. ({round(r['score'])})" for r in top])
