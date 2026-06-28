"""MIDAS — full functionality audit. Hits every page + endpoint + a full user
flow and prints a PASS/FAIL matrix. Run while sim_server.py is up:  python qa_check.py"""
import sqlite3, random, sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import requests

B = "http://localhost:5050"
results = []

def check(name, fn):
    try:
        ok, detail = fn()
    except Exception as e:
        ok, detail = False, "EXC: " + str(e)[:70]
    results.append(ok)
    print(("  [PASS] " if ok else "  [FAIL] ") + name + (("  " + detail) if detail else ""))
    return ok

def get(path, timeout=30):
    r = requests.get(B + path, timeout=timeout)
    return r

print("\n=== PAGES (serve 200) ===")
for p in ["index","stocks","penny","intelligence","whispers","gossip","practice",
          "trade","dashboard","account","survey","leaderboard"]:
    check(p + ".html", lambda p=p: ((lambda r: (r.status_code == 200, "HTTP %d" % r.status_code))(get("/%s.html" % p, 12))))

print("\n=== SEARCH ENGINE (/api/find) ===")
for q in ["NVDA", "Apple", "Tesla", "oil", "GOOGL", "Microsoft"]:
    def f(q=q):
        r = get("/api/find?q=" + q, 40).json()
        return (("news" in r and "whispers" in r),
                'q="%s" -> ticker=%s, news=%d, whisper=%s, related=%d'
                % (q, r.get("ticker"), len(r.get("news", [])), bool(r.get("whispers")), len(r.get("related", []))))
    check("find", f)

print("\n=== CORE GET ENDPOINTS ===")
GETS = [
    ("/api/whispers", lambda j: "whispers" in j, 90),
    ("/api/news", lambda j: "articles" in j, 30),
    ("/api/comments", lambda j: "comments" in j, 20),
    ("/api/leaderboard?scope=world", lambda j: "leaders" in j, 60),
    ("/api/suggestions", lambda j: "suggestions" in j, 90),
    ("/api/health", lambda j: "anthropic" in j, 10),
    ("/api/billing/status", lambda j: "configured" in j, 10),
    ("/api/ticker/AAPL", lambda j: "ticker" in j, 20),
    ("/api/search?ticker=AAPL", lambda j: ("price" in j or "error" in j), 30),
    ("/api/monitor", lambda j: isinstance(j, dict), 30),
    ("/api/intelligence", lambda j: isinstance(j, dict), 30),
    ("/api/daynews?date=2026-06-20", lambda j: "news" in j, 30),
    ("/api/account", lambda j: isinstance(j, dict), 20),
    ("/api/simulate?year=2023&mode=CLIMB&freq=ACTIVE&balance=1000&floor_pct=80&floor_mode=FIXED&trail_pct=15&deploy=10&reinvest=75&watcher=60&max_trades=0&conf_min=0.5&conf_max=2&tax=22",
     lambda j: ("timeline" in j or "error" in j), 90),
]
for path, validate, to in GETS:
    def f(path=path, validate=validate, to=to):
        r = get(path, to)
        try:
            j = r.json()
        except Exception:
            return (False, "non-JSON HTTP %d" % r.status_code)
        return (r.status_code == 200 and validate(j), "HTTP %d" % r.status_code)
    check(path.split("?")[0], f)

print("\n=== FULL USER FLOW (register -> me -> comment -> vote -> profile) ===")
em = "qa_%d@midas.fake" % random.randint(1000, 9999)
s = requests.Session()
def flow():
    r = s.post(B + "/api/register", json={"email": em, "password": "testpass123",
              "first_name": "Qa", "last_name": "Tester", "country": "USA", "state": "Illinois"}, timeout=20).json()
    if r.get("error"):
        return (False, "register: " + r["error"])
    uid = r["user"]["id"]
    me = s.get(B + "/api/me", timeout=10).json()
    if not me.get("user"):
        return (False, "me returned no user")
    wd = s.get(B + "/api/whispers", timeout=90).json()
    rows = (wd.get("haulers", []) or []) + (wd.get("whispers", []) or [])
    eid = rows[0]["id"] if rows else "qa:test"
    c = s.post(B + "/api/comments", json={"event_id": eid, "ticker": "QA", "text": "qa bullish test, big buy"}, timeout=15).json()
    if c.get("error"):
        return (False, "comment: " + c["error"])
    v = s.post(B + "/api/comments/vote", json={"comment_id": c.get("id")}, timeout=15).json()
    prof = s.get(B + "/api/profile/%d" % uid, timeout=60).json()
    ok = bool(me.get("user")) and not c.get("error") and ("ok" in v) and ("reputation" in prof) and ("portfolio" in prof)
    return (ok, "uid=%d, voted=%s, profile_has_rep+portfolio=%s" % (uid, v.get("ok"), ("reputation" in prof and "portfolio" in prof)))
check("full social flow", flow)

# cleanup qa user
try:
    cx = sqlite3.connect("midas_users.db")
    cx.execute("DELETE FROM comments WHERE ticker='QA'")
    cx.execute("DELETE FROM users WHERE email=?", (em,))
    cx.commit()
    print("  (qa test user cleaned)")
except Exception as e:
    print("  cleanup warn:", e)

p = sum(1 for x in results if x)
print("\n=== RESULT: %d/%d passed ===" % (p, len(results)))
if p < len(results):
    print("  ^ see [FAIL] lines above")
