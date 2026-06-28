"""Full-site audit for Project Midas: serving, endpoints, links, dead buttons,
stubs, and integration/key status. ASCII-only output (Windows console safe)."""
import requests, glob, re, os, time

B = "http://localhost:5050"
s = requests.Session()
R = {"FAIL": [], "WARN": [], "PASS": [], "INFO": []}

def add(tag, m): R[tag].append(m)

for _ in range(25):
    try:
        if s.get(B + "/api/health", timeout=3).ok:
            break
    except Exception:
        time.sleep(1)

pages = sorted(os.path.basename(f) for f in glob.glob("*.html"))

# 1) every page serves
for p in pages:
    try:
        c = s.get(B + "/" + p, timeout=12).status_code
        add("PASS" if c == 200 else "FAIL", "page %-20s %d" % (p, c))
    except Exception as e:
        add("FAIL", "page %-20s %s" % (p, e))

# 2) API endpoints respond
eps = ["/api/health", "/api/whispers", "/api/news", "/api/suggestions", "/api/comments",
       "/api/leaderboard", "/api/me", "/api/billing/status", "/api/search?ticker=AAPL",
       "/api/find?q=AAPL", "/api/history/AAPL", "/api/ownership/AAPL",
       "/api/fundamentals/AAPL", "/api/profile/1", "/api/comments/thread/1", "/api/daynews"]
for ep in eps:
    try:
        c = s.get(B + ep, timeout=35).status_code
        add("PASS" if c in (200, 404) else "FAIL", "GET %-34s %d" % (ep, c))
    except Exception as e:
        add("FAIL", "GET %-34s %s" % (ep, str(e)[:40]))

# 3) internal link integrity
existing = set(pages)
linkre = re.compile(r'href="([a-zA-Z0-9_\-]+\.html)(?:[#?][^"]*)?"')
for f in pages:
    for tgt in sorted(set(linkre.findall(open(f, encoding="utf-8").read()))):
        if tgt not in existing:
            add("FAIL", "broken link  %s -> %s" % (f, tgt))

# 4) dead buttons: onclick="fn(" with no local definition
SHARED = {"midasToggleTheme", "midasBack", "midasIsDark", "history", "location",
          "document", "window", "event", "return", "alert", "if", "console"}
for f in pages:
    txt = open(f, encoding="utf-8").read()
    for fn in sorted(set(re.findall(r'onclick="([a-zA-Z_][\w]*)\(', txt))):
        if fn in SHARED:
            continue
        if not any(p in txt for p in ("function " + fn, fn + "=", fn + " =",
                                      "window." + fn, "const " + fn, "let " + fn)):
            add("WARN", "%s: onclick %s() not defined locally" % (f, fn))

# 5) stub / placeholder markers
stub = re.compile(r"TODO|FIXME|coming soon|not implemented|lorem ipsum", re.I)
for f in pages:
    for i, line in enumerate(open(f, encoding="utf-8"), 1):
        if stub.search(line):
            add("WARN", "%s:%d stub marker" % (f, i))
for f in pages + ["sim_server.py"]:
    if os.path.exists(f):
        n = len(re.findall(r"\balert\(", open(f, encoding="utf-8").read()))
        if n:
            add("INFO", "%s uses alert() x%d" % (f, n))

# 6) integration / key status (what's blocked)
env = {}
if os.path.exists(".env"):
    for line in open(".env", encoding="utf-8"):
        line = line.strip()
        if "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            env[k.strip()] = bool(v.strip())
for key, what in [("ALPACA_API_KEY", "price data + paper trades"),
                  ("FINNHUB_API_KEY", "company news + insider/earnings"),
                  ("FMP_API_KEY", "ownership pie + Congress trades"),
                  ("ANTHROPIC_API_KEY", "AI news classification"),
                  ("STRIPE_SECRET_KEY", "subscriptions go live"),
                  ("SMTP_HOST", "real verification emails")]:
    add("INFO" if env.get(key) else "WARN",
        "%-18s %-7s -> %s" % (key, "SET" if env.get(key) else "MISSING", what))

for tag in ["FAIL", "WARN", "INFO", "PASS"]:
    print("\n=== %s (%d) ===" % (tag, len(R[tag])))
    for m in R[tag]:
        print("  " + m)
print("\nSUMMARY: %d pass, %d fail, %d warn" % (len(R["PASS"]), len(R["FAIL"]), len(R["WARN"])))
