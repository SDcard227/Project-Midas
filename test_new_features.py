"""Quick end-to-end check of the new social + account features."""
import requests, time, sys

B = "http://localhost:5050"
s = requests.Session()
ok = lambda c, m: print(("  PASS " if c else "  FAIL ") + m)

# wait for server
for _ in range(40):
    try:
        if s.get(B + "/api/health", timeout=3).ok:
            break
    except Exception:
        time.sleep(1)

print("== ACCOUNTS + EMAIL VERIFY ==")
email = "settest_%d@midas.fake" % int(time.time())
r = s.post(B + "/api/register", json={"email": email, "password": "testpass123",
           "first_name": "Sam", "last_name": "Test", "country": "USA", "state": "CA"}).json()
ok(not r.get("error"), "register: %s" % (r.get("error") or "created"))
me = s.get(B + "/api/me").json().get("user") or {}
ok(me.get("verified") is False, "new user starts UNverified")
rv = s.post(B + "/api/resend-verify").json()
link = rv.get("link")
ok(bool(link), "resend-verify returns a dev link")
if link:
    s.get(link)  # hit the verify page
    me2 = s.get(B + "/api/me").json().get("user") or {}
    ok(me2.get("verified") is True, "email now VERIFIED after clicking link")

print("== THE FLOOR: down-vote + replies + threads ==")
# post a top-level comment
c = s.post(B + "/api/comments", json={"event_id": "gossip", "ticker": "TEST",
                                      "text": "testing the new thread system $TEST"}).json()
cid = c.get("id")
ok(bool(cid), "posted a comment id=%s" % cid)
# down-vote it
dv = s.post(B + "/api/comments/vote", json={"comment_id": cid, "dir": "down"}).json()
ok(dv.get("downvotes") == 1, "down-vote registered (downvotes=%s)" % dv.get("downvotes"))
# switch to up-vote (should flip: down->0, up->1)
uv = s.post(B + "/api/comments/vote", json={"comment_id": cid, "dir": "up"}).json()
ok(uv.get("upvotes") == 1 and uv.get("downvotes") == 0, "vote flips down->up (%s up / %s down)" % (uv.get("upvotes"), uv.get("downvotes")))
# reply to it
rp = s.post(B + "/api/comments", json={"event_id": "gossip", "ticker": "TEST",
            "text": "replying in the thread!", "parent_id": cid}).json()
ok(rp.get("parent_id") == cid, "reply posted with parent_id=%s" % rp.get("parent_id"))
# fetch thread
th = s.get(B + "/api/comments/thread/%d" % cid).json()
ok(th.get("count") == 1 and th.get("root", {}).get("id") == cid, "thread shows root + %s reply" % th.get("count"))
# recent_all should carry the new fields + top-level only
ra = s.get(B + "/api/comments").json()
top = next((x for x in ra.get("comments", []) if x.get("id") == cid), None)
ok(top and "downvotes" in top and top.get("reply_count") == 1, "feed row has downvotes + reply_count=1")
ok(all(x.get("id") != rp.get("id") for x in ra.get("comments", [])), "replies are hidden from the top feed")

print("== PAGES SERVE ==")
for p in ["settings.html", "company.html", "gossip.html", "midas-theme.js"]:
    ok(s.get(B + "/" + p, timeout=8).status_code == 200, p)

print("done.")
