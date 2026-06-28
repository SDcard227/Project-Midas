"""Seed replies + up/down votes onto existing Floor comments so threads look alive.
Idempotent-ish: safe to run once after the demo seed. Uses brain modules directly."""
import os, sqlite3, random
from brain import comments, accounts

random.seed(7)
DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "midas_users.db")

con = sqlite3.connect(DB); con.row_factory = sqlite3.Row
demo = [dict(r) for r in con.execute(
    "SELECT id FROM users WHERE email LIKE 'demo_%@midas.fake'").fetchall()]
con.close()
users = [accounts.get_user(u["id"]) for u in demo]
users = [u for u in users if u]
if not users:
    print("No demo users — run seed_demo.py first."); raise SystemExit

REPLIES = [
    "agreed, watching this one too", "nah, i think you're early here",
    "what's your stop?", "this aged well lol", "source? genuinely curious",
    "been in since last week, riding it", "the volume backs this up",
    "careful, earnings thursday", "100% this", "chart says otherwise tbh",
    "good call honestly", "i'm fading this one", "macro doesn't support it",
    "insiders were buying, noted", "this is the play", "too crowded for me now",
]

feed = comments.recent_all(limit=80)["comments"]
n_rep = n_dv = n_uv = 0
for c in feed:
    # ~55% of takes spark a thread
    if random.random() < 0.55:
        for _ in range(random.randint(1, 4)):
            u = random.choice(users)
            comments.add_comment(u, c.get("event_id") or "gossip",
                                 c.get("ticker") or "", random.choice(REPLIES),
                                 parent_id=c["id"])
            n_rep += 1
    # spread of down-votes (dissent) and extra up-votes
    for u in random.sample(users, random.randint(0, 3)):
        comments.vote(u["id"], c["id"], "down"); n_dv += 1
    for u in random.sample(users, random.randint(0, 6)):
        comments.vote(u["id"], c["id"], "up"); n_uv += 1

print("seeded %d replies, %d down-votes, %d up-votes across %d takes"
      % (n_rep, n_dv, n_uv, len(feed)))
