"""
news_pipeline.py — the end-to-end loop: sources -> AI filter -> ledger -> whispers.

Ties the three pieces together so you can watch real whispers form from live
data, before the full firehose + database get built:

  1. Pull headlines from many SOURCES (RSS + SEC EDGAR filings + Reddit).
  2. Classify each with news_intelligence (Claude) — sentiment / market-moving / novelty.
  3. Fold the market-moving ones into the signal_ledger, tagged by their source.
  4. Surface the current whispers (the edge zone: rising, not yet mainstream).

Why these sources, in order of edge:
  - SEC EDGAR — PRIMARY. Outlets read these filings and THEN write articles.
    Catching an 8-K at filing time is minutes-to-hours ahead of the headline.
  - Reddit    — SOCIAL. Retail chatter often stirs before mainstream coverage.
  - RSS       — MAINSTREAM. Broad but late; useful as corroboration, not as edge.

Each source is a DISTINCT voice — that's the whole point. SEC + Reddit + CNBC all
naming NVDA = three independent sources = the confidence engine lights up.

Run a one-shot scan:
    python -m brain.news_pipeline

Honesty notes:
  - SEC 8-K titles tell you a material filing happened, not WHAT it says. Real
    edge needs to fetch the filing body — roadmap. Title-level is the first step.
  - "Independent source" is still just a distinct name; syndication/echo
    detection is roadmap. Reddit/EDGAR can rate-limit; failures are swallowed.
  - Each scan makes Claude calls (cheap on Haiku, not free). The web endpoint
    caches; the CLI runs once.
"""
import os
import re
import json
import logging
import xml.etree.ElementTree as ET

import requests

from .news_intelligence import classify_headlines
from .signal_ledger import SignalLedger

log = logging.getLogger("Midas.Pipeline")

# SEC requires a descriptive User-Agent with a real contact. Set SEC_CONTACT in
# your .env to your own email; the default is a placeholder SEC may throttle.
_SEC_CONTACT = os.getenv("SEC_CONTACT", "Project Midas research admin@example.com")
_SEC_UA = {"User-Agent": _SEC_CONTACT}
_UA = {"User-Agent": "Mozilla/5.0 (compatible; MidasBot/1.0)"}
_REDDIT_UA = {"User-Agent": "python:project-midas:1.0 (news research)"}

# ── Source 1: mainstream RSS ─────────────────────────────────────────────────
RSS_FEEDS = [
    ("CNBC",          "https://www.cnbc.com/id/100003114/device/rss/rss.html"),
    ("MarketWatch",   "https://feeds.marketwatch.com/marketwatch/topstories"),
    ("Yahoo Finance", "https://finance.yahoo.com/news/rssindex"),
    ("BBC Business",  "https://feeds.bbci.co.uk/news/business/rss.xml"),
    # ── underground / alt / early-signal (whispers live in the fringe) ──
    ("Zerohedge",     "https://feeds.feedburner.com/zerohedge/feed"),
    ("Benzinga",      "https://www.benzinga.com/feed"),
    ("Seeking Alpha", "https://seekingalpha.com/market_currents.xml"),
    ("Hacker News",   "https://hnrss.org/newest?points=100"),
    ("WSB",           "https://www.reddit.com/r/wallstreetbets/.rss"),
    ("r/stocks",      "https://www.reddit.com/r/stocks/.rss"),
    ("r/pennystocks", "https://www.reddit.com/r/pennystocks/.rss"),
    ("r/SPACs",       "https://www.reddit.com/r/SPACs/.rss"),
    ("GlobeNewswire", "https://www.globenewswire.com/RssFeed/orgclass/1/feedTitle/GlobeNewswire"),
    ("PRNewswire",    "https://www.prnewswire.com/rss/news-releases-list.rss"),
    # ── commodities / minerals / natural resources ──
    ("Mining.com",    "https://www.mining.com/feed/"),
    ("OilPrice",      "https://oilprice.com/rss/main"),
    ("Kitco Metals",  "https://www.kitco.com/rss/KitcoNews.xml"),
]

# ── Source 2: SEC EDGAR latest filings (the primary-source edge) ─────────────
# "getcurrent" = a live firehose of the most recent filings of a given type.
_SEC_TYPES = ["8-K"]   # 8-K = material events. Add "4" for insider trades later.

# ── Source 3: Reddit (retail whispers) ───────────────────────────────────────
_REDDIT_SUBS = ["wallstreetbets", "stocks", "StockMarket"]

# ── Source 4: StockTwits (social chatter) — needs a token; API 403s unauth ────
_STOCKTWITS_TOKEN = os.getenv("STOCKTWITS_TOKEN")
_ST_BASE = "https://api.stocktwits.com/api/2"


def _fetch_one_rss(source, url, limit_per_feed=15):
    out = []
    try:
        r = requests.get(url, timeout=8, headers=_UA)
        if not r.ok:
            return out
        root = ET.fromstring(r.content)
        for item in (root.findall(".//item") or [])[:limit_per_feed]:
            el = item.find("title")
            title = (el.text or "").strip() if el is not None else ""
            if title:
                link_el = item.find("link")
                out.append({"source": source,
                            "title": re.sub(r"\s+", " ", title),
                            "link": (link_el.text or "").strip() if link_el is not None else ""})
    except Exception as e:
        log.debug(f"RSS {source} failed: {e}")
    return out


def _fetch_rss(limit_per_feed: int = 15) -> list:
    """Fetch all RSS feeds CONCURRENTLY (was sequential -> cold scans overran the gateway
    and /api/whispers 500'd). Returns whatever finishes within the budget."""
    from concurrent.futures import ThreadPoolExecutor, as_completed
    out = []
    ex = ThreadPoolExecutor(max_workers=12)
    futs = [ex.submit(_fetch_one_rss, s, u, limit_per_feed) for s, u in RSS_FEEDS]
    try:
        for f in as_completed(futs, timeout=15):
            try:
                out.extend(f.result() or [])
            except Exception:
                pass
    except Exception as e:
        log.debug(f"rss parallel partial: {e}")
    ex.shutdown(wait=False)
    return out


def _fetch_sec(count: int = 30) -> list:
    """Latest SEC filings via EDGAR's getcurrent Atom feed. Each entry titled
    like '8-K - APPLE INC (0000320193) (Filer)' — we reshape it into a headline
    the classifier understands and can map to a ticker."""
    out = []
    ns = {"a": "http://www.w3.org/2005/Atom"}
    for ftype in _SEC_TYPES:
        url = ("https://www.sec.gov/cgi-bin/browse-edgar?action=getcurrent"
               f"&type={ftype}&company=&dateb=&owner=include&count={count}&output=atom")
        try:
            r = requests.get(url, timeout=10, headers=_SEC_UA)
            if not r.ok:
                log.debug(f"SEC {ftype} HTTP {r.status_code}")
                continue
            root = ET.fromstring(r.content)
            for entry in root.findall(".//a:entry", ns)[:count]:
                t = entry.find("a:title", ns)
                raw = (t.text or "").strip() if t is not None else ""
                if not raw:
                    continue
                # "8-K - APPLE INC (0000320193) (Filer)" -> "Apple Inc"
                company = re.sub(r"\s*\(\d+\).*$", "", raw)
                company = re.sub(r"^\S+\s*-\s*", "", company).strip()
                link_el = entry.find("a:link", ns)
                link = link_el.get("href") if link_el is not None else ""
                out.append({"source": "SEC EDGAR",
                            "title": f"SEC {ftype} filing: {company}",
                            "link": link})
        except Exception as e:
            log.debug(f"SEC {ftype} failed: {e}")
    return out


def _fetch_reddit(limit_per_sub: int = 15) -> list:
    out = []
    for sub in _REDDIT_SUBS:
        url = f"https://www.reddit.com/r/{sub}/hot.json?limit={limit_per_sub}"
        try:
            r = requests.get(url, timeout=8, headers=_REDDIT_UA)
            if not r.ok:
                log.debug(f"Reddit r/{sub} HTTP {r.status_code}")
                continue
            children = r.json().get("data", {}).get("children", [])
            for c in children:
                d = c.get("data", {})
                title = (d.get("title") or "").strip()
                if title and not d.get("stickied"):
                    out.append({"source": f"r/{sub}",
                                "title": re.sub(r"\s+", " ", title),
                                "link": "https://reddit.com" + d.get("permalink", "")})
        except Exception as e:
            log.debug(f"Reddit r/{sub} failed: {e}")
    return out


def _fetch_stocktwits(top_symbols: int = 5, msgs_per: int = 10) -> list:
    """Pull chatter from StockTwits' TRENDING symbols — itself a whisper radar
    (what social is buzzing about right now). The public API now 403s server IPs,
    so this needs STOCKTWITS_TOKEN in .env. Degrades to [] without one."""
    if not _STOCKTWITS_TOKEN:
        log.info("StockTwits skipped - set STOCKTWITS_TOKEN in .env to enable.")
        return []
    params = {"access_token": _STOCKTWITS_TOKEN}
    try:
        r = requests.get(f"{_ST_BASE}/trending/symbols.json", params=params, timeout=10, headers=_UA)
        if not r.ok:
            log.debug(f"StockTwits trending HTTP {r.status_code}")
            return []
        symbols = [s["symbol"] for s in r.json().get("symbols", [])][:top_symbols]
    except Exception as e:
        log.debug(f"StockTwits trending failed: {e}")
        return []

    out = []
    for sym in symbols:
        try:
            r = requests.get(f"{_ST_BASE}/streams/symbol/{sym}.json", params=params, timeout=10, headers=_UA)
            if not r.ok:
                continue
            for m in r.json().get("messages", [])[:msgs_per]:
                body = re.sub(r"\s+", " ", (m.get("body") or "").strip())
                if not body:
                    continue
                out.append({"source": "StockTwits",
                            "title": f"${sym}: {body}",
                            "link": f"https://stocktwits.com/symbol/{sym}"})
        except Exception as e:
            log.debug(f"StockTwits {sym} failed: {e}")
    return out


def fetch_headlines() -> list:
    """Pull from every source. Returns [{source, title, link}] — source is the
    unit of corroboration, so keep them granular and distinct."""
    from concurrent.futures import ThreadPoolExecutor
    headlines = []
    with ThreadPoolExecutor(max_workers=4) as ex:
        futs = {ex.submit(fn): fn.__name__ for fn in (_fetch_sec, _fetch_reddit, _fetch_stocktwits, _fetch_rss)}
        for fut in futs:
            try:
                headlines.extend(fut.result(timeout=20) or [])
            except Exception as e:
                log.debug(f"{futs[fut]} failed: {e}")
    log.info(f"Fetched {len(headlines)} headlines across all sources")
    return headlines


def run_scan(ledger: SignalLedger = None, model: str = None,
             review_model: str = None, fact_check: bool = True) -> dict:
    """
    One full pass: fetch -> classify -> ingest -> peer-review haulers -> report.

    Returns a JSON-serializable summary:
        {updated, scanned, market_moving, ai_enabled, sources, whispers, haulers, top}
    Each hauler carries a `review` (peer-review verdict) and a
    `reviewed_confidence` (raw confidence x the panel's trust multiplier).
    Safe with no API key — AI just stays off and the lists come back empty.
    """
    from datetime import datetime, timezone
    ledger = ledger or SignalLedger()
    articles = fetch_headlines()
    titles = [a["title"] for a in articles]

    verdicts = classify_headlines(titles, model=model)
    ai_enabled = bool(verdicts)

    now = datetime.now(timezone.utc).isoformat()
    market_moving = 0
    for article, verdict in zip(articles, verdicts):
        if not verdict.get("market_moving"):
            continue
        ticker = (verdict.get("tickers") or [None])[0]
        if not ticker:
            continue
        ledger.ingest(ticker, verdict, source=article["source"], ts=now,
                      title=article["title"], link=article.get("link"))
        market_moving += 1

    # Count how many distinct sources we pulled, for visibility.
    src_counts = {}
    for a in articles:
        src_counts[a["source"]] = src_counts.get(a["source"], 0) + 1

    # Peer-review only the haulers (few) — the firehose is too big to review.
    # Each gets a verdict + a trust-adjusted confidence.
    haulers = ledger.haulers()
    if ai_enabled and fact_check and haulers:
        from .fact_checker import review_event
        for h in haulers[:8]:                       # cost cap: review at most 8 haulers per scan
            h["review"] = review_event(h, model=review_model)
            h["reviewed_confidence"] = round(h["confidence"] * h["review"]["multiplier"], 1)

    # No AI key? Synthesize whisper cards (ticker + direction + confidence) from the raw
    # headlines so the Wire keeps its ticker/confidence view without a key — see wire_enrich.
    whispers_out = ledger.whispers()
    if not ai_enabled:
        from .wire_enrich import build_keyless_whispers
        whispers_out = build_keyless_whispers(articles)

    return {
        "updated": now,
        "scanned": len(articles),
        "market_moving": market_moving,
        "ai_enabled": ai_enabled,
        "sources": src_counts,
        "whispers": whispers_out,
        "haulers": haulers,
        "top": ledger.top_signals(5),
        # raw firehose — so the Wire shows live news even with AI off (no API key needed).
        # Newest first; capped so the payload stays small.
        "wire": [{"source": a["source"], "title": a["title"], "link": a.get("link", "")}
                 for a in articles[:60]],
    }


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    result = run_scan()
    print(f"\nScanned {result['scanned']} headlines from {len(result['sources'])} sources "
          f"| AI {'ON' if result['ai_enabled'] else 'OFF (set ANTHROPIC_API_KEY)'} "
          f"| {result['market_moving']} market-moving\n")
    print("By source:", json.dumps(result["sources"], indent=2))
    if not result["whispers"]:
        print("\nNo whispers yet - run again as more news (and sources) come in.")
    for w in result["whispers"]:
        print(f"  {w['ticker']:6s} {w['direction']:8s} conf {w['confidence']:5.1f} "
              f"| {w['source_count']} src [{w['stage']}] | {w['event_type']}")

    if result.get("haulers"):
        print("\nHAULERS (peer-reviewed):")
        for h in result["haulers"]:
            rv = h.get("review", {})
            print(f"  {h['ticker']:6s} {h['direction']:8s} conf {h['confidence']:5.1f} "
                  f"-> reviewed {h.get('reviewed_confidence', h['confidence']):5.1f} "
                  f"[{rv.get('status', 'unreviewed')}] | {h['source_count']} src")
