"""Test the Pit endpoints + seed demo chatter into the rooms so it looks alive."""
import os, sqlite3, random, time, requests
from brain import chat, accounts

B = "http://localhost:5050"
random.seed(11)

# 1) endpoint smoke test: register -> post -> poll
s = requests.Session()
t = int(time.time())
s.post(B + "/api/register", json={"email": "pit_%d@midas.fake" % t, "password": "testpass123",
                                  "first_name": "Pit", "last_name": "Tester"})
rooms = s.get(B + "/api/rooms").json()["rooms"]
rid = rooms[0]["id"]
s.post(B + "/api/rooms/%d/messages" % rid, json={"text": "testing the pit $NVDA"})
got = s.get(B + "/api/rooms/%d/messages?after=0" % rid).json().get("messages", [])
print("  endpoint post+poll:", "PASS" if any("testing the pit" in m["text"] for m in got) else "FAIL")

# 2) seed demo chatter
con = sqlite3.connect(os.path.join(os.path.dirname(os.path.abspath(__file__)), "midas_users.db"))
con.row_factory = sqlite3.Row
demo = [accounts.get_user(r["id"]) for r in con.execute(
    "SELECT id FROM users WHERE email LIKE 'demo_%@midas.fake'").fetchall()]
con.close()
demo = [u for u in demo if u]

LINES = {
 "Market Open": ["futures green, watching $NVDA into the open", "CPI tomorrow, keeping it light",
   "gap up on $SPY, fading the first 30 min", "$AMD looking strong premarket", "vol is low, careful chasing",
   "anyone watching $TSLA? coiling up", "bell in 10, plan your levels"],
 "Earnings Season": ["$NFLX beat but guidance soft, IV crush incoming", "$TSLA after the bell, straddle looks pricey",
   "$AMD print strong, data center number is the tell", "selling the rip on the beat", "waiting for the call before I touch it",
   "$META whisper number is high"],
 "Long-Term Investing": ["DCA into the dip, not timing it", "10yr horizon, daily noise doesn't matter",
   "adding to my core every paycheck", "boring wins, just keep buying the index", "reinvesting the dividends, compounding does the work"],
 "Crypto Corner": ["$BTC holding 60k support for now", "$ETH looking heavy vs btc", "alt season not yet imo",
   "coinbase volume picking up", "funding flipped negative, watch for a squeeze"],
}
GENERIC = ["good read", "agreed", "watching this too", "nice setup", "careful here"]

if not demo:
    print("  no demo users, run seed_demo.py first")
else:
    n = 0
    for room in chat.list_rooms():
        pool = LINES.get(room["name"], GENERIC)
        for ln in random.sample(pool, min(len(pool), random.randint(3, 6))):
            chat.post_message(random.choice(demo), room["id"], ln)
            n += 1
    print("  seeded %d demo messages across %d rooms" % (n, len(chat.list_rooms())))
