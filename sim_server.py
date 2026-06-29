"""
MIDAS — Simulation Game Server

Run:  py sim_server.py
Open: http://localhost:5050
"""
import os, sys, io, contextlib

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    from flask import Flask, jsonify, request, send_from_directory, session
except ImportError:
    print("\n  Flask not installed. Run:  pip install flask\n")
    sys.exit(1)

from brain.backtest import Backtest

app  = Flask(__name__)
BASE = os.path.dirname(os.path.abspath(__file__))
# Session secret: env value in prod; else a random per-process key (NOT a known
# string) so sessions can't be forged if SECRET_KEY is unset.
import secrets as _secrets
app.secret_key = os.getenv("SECRET_KEY") or _secrets.token_hex(32)
if not os.getenv("SECRET_KEY"):
    print("  WARNING: SECRET_KEY not set — using a random key (sessions reset on restart). Set it in prod.")
# Harden the session cookie. SameSite=Lax blocks CSRF on cross-site POSTs;
# Secure only on a real HTTPS deploy (Render sets RENDER) so local HTTP still works.
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=bool(os.getenv("RENDER")),
    MAX_CONTENT_LENGTH=6 * 1024 * 1024,   # 6 MB cap on uploads (comics/art)
)
try:
    from brain import accounts as _accounts
    _accounts.init_db()
except Exception as _e:
    print(f"  accounts db init skipped: {_e}")


@app.route("/")
def index():
    return send_from_directory(BASE, "index.html")

_STATIC_OK = {".html", ".js", ".css", ".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico",
              ".svg", ".json", ".txt", ".woff", ".woff2", ".webmanifest", ".map"}


@app.route("/<path:filename>")
def static_files(filename):
    # Allowlist asset extensions only — never serve .db, .env, .py, .log, etc. from the repo root.
    if os.path.splitext(filename)[1].lower() not in _STATIC_OK:
        return ("Not found", 404)
    return send_from_directory(BASE, filename)


@app.route("/api/simulate")
def simulate():
    mode           = request.args.get("mode", "CLIMB")
    freq           = request.args.get("freq", "NORMAL")
    year           = int(request.args.get("year", 2023))
    balance        = float(request.args.get("balance", 1000))
    tax_rate       = float(request.args.get("tax", 22)) / 100.0
    floor_pct      = float(request.args.get("floor_pct", 80)) / 100.0
    floor_mode     = request.args.get("floor_mode", "FIXED")
    trail_pct      = float(request.args.get("trail_pct", 15)) / 100.0
    deploy_frac    = float(request.args.get("deploy", 10)) / 100.0
    reinvest_pct   = float(request.args.get("reinvest", 75)) / 100.0
    watcher_exp    = int(request.args.get("watcher", 60))
    max_trades     = request.args.get("max_trades")
    max_trades     = int(max_trades) if max_trades and max_trades != "0" else None
    conf_min       = float(request.args.get("conf_min", 0.5))
    conf_max       = float(request.args.get("conf_max", 2.0))

    bt = Backtest(
        watchlist            = ["AAPL", "MSFT", "NVDA", "SPY"],
        start_date           = f"{year}-01-01",
        end_date             = f"{year+1}-01-01",
        starting_balance     = balance,
        floor_amount         = round(balance * floor_pct, 2),
        entry_point          = round(balance * 0.10, 2),
        deploy_fraction      = deploy_frac,
        reinvest_percent     = reinvest_pct,
        reentry_dip_percent  = 0.04,
        watcher_expiry_days  = watcher_exp,
        trading_mode         = mode,
        trade_frequency      = freq,
        plateau_days         = 10,
        plateau_range_pct    = 0.02,
        slump_reentry_pct    = 0.08,
        confidence_min_scale = conf_min,
        confidence_max_scale = conf_max,
        max_trades_month     = max_trades,
        allocation_mode      = "FREE_RANGE",
        allocations          = {},
        floor_mode           = floor_mode,
        trailing_floor_pct   = trail_pct,
        commission           = 0.0,
        spread_pct           = 0.0003,
        tax_rate             = tax_rate,
    )

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        bt.run()

    def fix(v):
        if hasattr(v, "isoformat"):
            return str(v)
        if isinstance(v, list):
            return [str(x) if hasattr(x, "isoformat") else x for x in v]
        return v

    trades    = [{k: fix(v) for k, v in t.items()} for t in bt.results["trade_log"]]
    snapshots = [{k: fix(v) for k, v in s.items()} for s in bt.results["daily_snapshots"]]

    return jsonify({
        "floor_mode":              floor_mode,
        "floor_amount":            round(balance * floor_pct, 2),
        "starting_balance":        bt.results["starting_balance"],
        "final_value":             bt.results["final_value"],
        "total_return":            bt.results["total_return"],
        "total_return_pct":        bt.results["total_return_pct"],
        "total_transaction_costs": bt.results["total_transaction_costs"],
        "total_tax_paid":          bt.results["total_tax_paid"],
        "total_trades":            bt.results["total_trades"],
        "buys":                    bt.results["buys"],
        "sells":                   bt.results["sells"],
        "win_rate_pct":            bt.results["win_rate_pct"],
        "best_trade":              bt.results["best_trade"],
        "worst_trade":             bt.results["worst_trade"],
        "floor_triggered":         bt.results["floor_triggered"],
        "trades":                  trades,
        "snapshots":               snapshots,
        "timeline":                bt.results.get("signal_timeline", []),
    })


# ── Intelligence & Search ────────────────────────────────────────────────────
import time as _time
import threading as _threading
import logging as _logging
import pandas as _pd
from datetime import datetime as _dt, timedelta as _timedelta
from dotenv import load_dotenv as _load_dotenv
from alpaca.data.historical import StockHistoricalDataClient as _StockClient
from alpaca.data.requests import StockBarsRequest as _BarsReq
from alpaca.data.timeframe import TimeFrame as _TF
from brain.indicators import ema_crossover as _ema_x, rsi as _rsi
from brain.politician import PoliticianTracker as _PolTracker

_load_dotenv()
_log        = _logging.getLogger("Midas.Server")
_WATCHLIST  = ["AAPL","MSFT","NVDA","SPY","TSLA","AMD","META","AMZN","PLTR","SOFI","GME","SNDL","GOOGL","NFLX","COIN"]
_intel_data = None
_intel_ts   = 0.0
_intel_lock = _threading.Lock()
_pol        = _PolTracker(lookback_days=90)


_STOOQ_CACHE = {}

def _free_closes_fetch(tkey):
    """One raw attempt at free closes: Yahoo's chart API (JSON, datacenter-friendly),
    then stooq (CSV). No cache. Returns a pandas Series or None."""
    import urllib.request
    try:                                              # Yahoo chart API, no key
        import json as _json
        url = ("https://query1.finance.yahoo.com/v8/finance/chart/%s"
               "?range=2y&interval=1d" % tkey.upper())
        rq = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(rq, timeout=8) as resp:
            d = _json.loads(resp.read().decode("utf-8", "replace"))
        q = d["chart"]["result"][0]["indicators"]["quote"][0]["close"]
        vals = [float(x) for x in q if x is not None]
        if len(vals) >= 15:
            return _pd.Series(vals).dropna()
    except Exception as e:
        _log.debug(f"yahoo closes {tkey}: {e}")
    try:                                              # stooq CSV fallback, no key
        rq = urllib.request.Request("https://stooq.com/q/d/l/?s=%s.us&i=d" % tkey.lower(),
                                    headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(rq, timeout=8) as resp:
            text = resp.read().decode("utf-8", "replace")
        lines = [ln for ln in text.splitlines() if ln.strip()]
        if len(lines) >= 16 and lines[0].lower().startswith("date"):
            vals = []
            for ln in lines[1:]:
                p = ln.split(",")
                if len(p) >= 5:
                    try:
                        vals.append(float(p[4]))      # Close column
                    except ValueError:
                        pass
            if len(vals) >= 15:
                return _pd.Series(vals).dropna()
    except Exception as e:
        _log.debug(f"stooq closes {tkey}: {e}")
    return None


def _stooq_closes(ticker, days=370):
    """Free daily closes (NO API key): Yahoo then stooq, cached 30 min (even misses) so
    we don't hammer either. The fallback so Signals + the Parlor's price markets work
    with zero keys. Returns a pandas Series (most recent `days`) or None."""
    tkey = (ticker or "").strip()
    if not tkey:
        return None
    ck = tkey.upper()
    hit = _STOOQ_CACHE.get(ck)
    if not (hit and _time.time() - hit[0] < 1800):
        _STOOQ_CACHE[ck] = (_time.time(), _free_closes_fetch(tkey))
        hit = _STOOQ_CACHE[ck]
    s = hit[1]
    return s.iloc[-days:] if (s is not None and len(s)) else None


def _alpaca_closes(ticker, days=370):
    """Daily closes for the past `days`. Alpaca when keys are set, else a free stooq
    fallback (so Signals + Parlor auto-resolve work with no keys). pandas Series or None."""
    key = os.getenv("ALPACA_API_KEY")
    sec = os.getenv("ALPACA_SECRET_KEY")
    if not (key and sec):
        return _stooq_closes(ticker, days)             # free fallback, no key needed
    try:
        client = _StockClient(key, sec)
        req = _BarsReq(symbol_or_symbols=ticker, timeframe=_TF.Day,
                       start=_dt.now() - _timedelta(days=days), end=_dt.now())
        df = getattr(client.get_stock_bars(req), "df", None)
    except Exception as e:
        _log.debug(f"Alpaca closes {ticker}: {e}")
        return _stooq_closes(ticker, days)
    if df is None or df.empty:
        return _stooq_closes(ticker, days)
    df = df.reset_index()
    if "symbol" in df.columns:
        df = df[df["symbol"] == ticker]
    if df.empty or "close" not in df.columns:
        return _stooq_closes(ticker, days)
    return _pd.Series(df["close"].astype(float).values).dropna()


@app.after_request
def _security_headers(response):
    # Same-origin app, so no wildcard CORS. Add basic hardening headers instead.
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "SAMEORIGIN"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    return response


_RL = {}
def _rate_ok(name, limit, per_sec=60):
    """In-memory per-IP fixed-window rate limit (single-worker scope, fine on Render free)."""
    try:
        ip = (request.headers.get("X-Forwarded-For", "") or request.remote_addr or "?").split(",")[0].strip()
    except Exception:
        ip = "?"
    now = _time.time()
    cnt, start = _RL.get((ip, name), (0, now))
    if now - start > per_sec:
        cnt, start = 0, now
    cnt += 1
    _RL[(ip, name)] = (cnt, start)
    return cnt <= limit


@app.before_request
def _rate_guard():
    # Blanket abuse protection: tight on auth (brute-force/signup spam), looser on
    # general activity (comments/chat/votes). Stops bots and AI-cost-bombs.
    if request.method == "POST" and request.path.startswith("/api/"):
        if request.path in ("/api/register", "/api/login"):
            if not _rate_ok("auth", 8, 60):
                return jsonify({"error": "Too many attempts, wait a minute."}), 429
        elif not _rate_ok("post", 60, 60):
            return jsonify({"error": "Going too fast, slow down a moment."}), 429


@app.route("/intelligence")
def intelligence_page():
    return send_from_directory(BASE, "intelligence.html")


def _ticker_stats(ticker):
    closes = _alpaca_closes(ticker, days=370)
    if closes is None or len(closes) < 15:
        return None

    trend   = _ema_x(closes, fast=5, slow=13)
    rsi_s   = _rsi(closes)
    cur_rsi = float(rsi_s.iloc[-1])
    cur_px  = float(closes.iloc[-1])
    prev_px = float(closes.iloc[-2]) if len(closes) > 1 else cur_px
    chg_pct = round((cur_px - prev_px) / prev_px * 100, 2)

    if trend["bullish_crossover"] and cur_rsi <= 60:
        signal = "BUY"
    elif trend["bearish_crossunder"] or cur_rsi > 70:
        signal = "SELL"
    else:
        signal = "HOLD"

    rsi_conf = max(0.0, min(100.0, (65.0 - cur_rsi) / 35.0 * 100.0))
    spr      = (max(0.0, (trend["fast_ema"] - trend["slow_ema"]) / trend["slow_ema"] * 100.0)
                if trend["slow_ema"] > 0 else 0.0)
    spr_conf = min(100.0, spr * 50.0)
    pol      = _pol.get_signal(ticker)
    pol_conf = 100.0 if pol["score"] == 1 else 50.0 if pol["score"] == 0 else 0.0
    conf     = round(rsi_conf * 0.40 + spr_conf * 0.40 + pol_conf * 0.20, 1)

    # 52-week high/low from the same ~1y Alpaca series
    h52 = round(float(closes.max()), 2)
    l52 = round(float(closes.min()), 2)

    return {
        "ticker":             ticker.upper(),
        "price":              round(cur_px, 2),
        "chg_pct":            chg_pct,
        "signal":             signal,
        "confidence":         conf,
        "trend":              trend["trend"],
        "fast_ema":           round(trend["fast_ema"], 2),
        "slow_ema":           round(trend["slow_ema"], 2),
        "rsi":                round(cur_rsi, 1),
        "bullish_crossover":  trend["bullish_crossover"],
        "bearish_crossunder": trend["bearish_crossunder"],
        "high_52w":           h52,
        "low_52w":            l52,
        "pol_signal":         pol["signal"],
        "pol_score":          pol["score"],
        "pol_buys":           pol["buys"],
        "pol_sells":          pol["sells"],
        "pol_recent":         pol.get("recent", [])[:5],
    }


def _build_intel():
    results = []
    for ticker in _WATCHLIST:
        try:
            s = _ticker_stats(ticker)
            if s:
                results.append(s)
        except Exception as e:
            _log.warning(f"Intel: {ticker} — {e}")
    results.sort(key=lambda x: (
        0 if x["signal"] == "BUY" else 1 if x["signal"] == "HOLD" else 2,
        -x["confidence"],
    ))
    return {"updated": _dt.now().isoformat(), "tickers": results}


@app.route("/api/intelligence")
def api_intelligence():
    global _intel_data, _intel_ts
    if _intel_data is None or _time.time() - _intel_ts > 300:
        with _intel_lock:
            if _intel_data is None or _time.time() - _intel_ts > 300:
                _intel_data = _build_intel()
                _intel_ts   = _time.time()
    return jsonify(_intel_data)


@app.route("/api/search")
def api_search():
    ticker = request.args.get("ticker", "").strip().upper()
    if not ticker or not all(c.isalpha() or c in ".-" for c in ticker) or len(ticker) > 6:
        return jsonify({"error": "Invalid ticker symbol"}), 400
    try:
        data = _ticker_stats(ticker)
        if data is None:
            return jsonify({"error": f"No data found for {ticker}"}), 404
        return jsonify(data)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ── NEWS FEED ──────────────────────────────────────────────────────────────
import xml.etree.ElementTree as _ET

_NEWS_FEEDS = [
    ("Al Jazeera",       "MIDDLE EAST", "https://www.aljazeera.com/xml/rss/all.xml"),
    ("BBC Business",     "EUROPE",      "https://feeds.bbci.co.uk/news/business/rss.xml"),
    ("Reuters",          "GLOBAL",      "https://feeds.reuters.com/reuters/businessNews"),
    ("CNBC",             "AMERICAS",    "https://www.cnbc.com/id/100003114/device/rss/rss.html"),
    ("MarketWatch",      "AMERICAS",    "https://feeds.marketwatch.com/marketwatch/topstories"),
    ("Yahoo Finance",    "GLOBAL",      "https://finance.yahoo.com/news/rssindex"),
    ("Investopedia",     "GLOBAL",      "https://www.investopedia.com/feedbuilder/feed/getfeed/?feedName=rss_headline"),
    ("Forbes Investing", "AMERICAS",    "https://www.forbes.com/investing/feed/"),
    # underground / alt / early-signal
    ("Zerohedge",        "GLOBAL",      "https://feeds.feedburner.com/zerohedge/feed"),
    ("Benzinga",         "AMERICAS",    "https://www.benzinga.com/feed"),
    ("Seeking Alpha",    "GLOBAL",      "https://seekingalpha.com/market_currents.xml"),
    ("WSB",              "SOCIAL",      "https://www.reddit.com/r/wallstreetbets/.rss"),
    ("r/stocks",         "SOCIAL",      "https://www.reddit.com/r/stocks/.rss"),
    ("r/pennystocks",    "SOCIAL",      "https://www.reddit.com/r/pennystocks/.rss"),
    ("GlobeNewswire",    "AMERICAS",    "https://www.globenewswire.com/RssFeed/orgclass/1/feedTitle/GlobeNewswire"),
    # commodities / metals / minerals / energy / crypto (for category browsing)
    ("Mining.com",       "COMMODITIES", "https://www.mining.com/feed/"),
    ("OilPrice",         "COMMODITIES", "https://oilprice.com/rss/main"),
    ("Kitco Metals",     "COMMODITIES", "https://www.kitco.com/rss/KitcoNews.xml"),
    ("CoinDesk",         "CRYPTO",      "https://www.coindesk.com/arc/outboundfeeds/rss/"),
    ("CoinTelegraph",    "CRYPTO",      "https://cointelegraph.com/rss"),
]

_news_data = None
_news_ts   = 0.0
_news_lock = _threading.Lock()

# Sort each story into a browsable category. A topical keyword (gold, oil, bitcoin)
# wins over the source's default, so a CNBC gold story lands under Metals, not Markets.
_CAT_SOURCE = {
    "Mining.com": "Metals & Minerals", "Kitco Metals": "Metals & Minerals",
    "OilPrice": "Energy & Commodities",
    "CoinDesk": "Crypto", "CoinTelegraph": "Crypto",
    "WSB": "Underground", "r/stocks": "Underground", "r/pennystocks": "Underground",
    "Zerohedge": "Underground", "Seeking Alpha": "Underground", "Benzinga": "Underground",
    "Al Jazeera": "Macro & World", "BBC Business": "Macro & World", "Reuters": "Macro & World",
}
_CAT_KW = [
    ("Crypto", ("bitcoin", "crypto", "ethereum", " btc", " eth ", "blockchain", "stablecoin", "defi", "altcoin", "coinbase")),
    ("Metals & Minerals", ("gold", "silver", "copper", "lithium", "mining", "mineral", "rare earth", "cobalt", "nickel", "uranium", "platinum", "palladium", "iron ore")),
    ("Energy & Commodities", ("oil", "crude", "natural gas", "opec", " energy", "barrel", "gasoline", "coal", "wheat", "corn", "commodit", "soybean", "natgas")),
]

def _categorize(source, title, desc=""):
    t = (" " + (title or "") + " " + (desc or "")).lower()
    for cat, kws in _CAT_KW:
        if any(k in t for k in kws):
            return cat
    return _CAT_SOURCE.get(source, "Markets")

def _fetch_rss(name, region, url):
    """Fetch one RSS feed; return list of article dicts."""
    import requests as _req, re as _re
    headers = {"User-Agent": "Mozilla/5.0 (compatible; MidasBot/1.0)"}
    try:
        r = _req.get(url, timeout=8, headers=headers)
        if not r.ok:
            return []
        root = _ET.fromstring(r.content)
        ns   = {"atom": "http://www.w3.org/2005/Atom"}
        items = root.findall(".//item") or root.findall(".//atom:entry", ns)
        articles = []
        for item in items[:6]:
            def _text(tag):
                el = item.find(tag)
                return el.text.strip() if el is not None and el.text else ""
            title   = _text("title")
            link    = _text("link") or _text("url")
            pubdate = _text("pubDate") or _text("published") or _text("updated")
            desc    = _re.sub(r"<[^>]+>", "", _text("description") or _text("summary"))[:200]
            if not title:
                continue
            articles.append({
                "source":   name,
                "region":   region,
                "category": _categorize(name, title, desc),
                "title":    title,
                "link":     link,
                "pubdate":  pubdate,
                "desc":     desc,
            })
        return articles
    except Exception as exc:
        _log.debug(f"News feed {name}: {exc}")
        return []


def _build_news():
    all_articles = []
    for name, region, url in _NEWS_FEEDS:
        all_articles.extend(_fetch_rss(name, region, url))
    return {"updated": _dt.now().isoformat(), "articles": all_articles}


# ── MEDIA FEED (replaces YouTube RSS which is blocked server-side) ───────────
# Live stream embeds still use YouTube iframes (client-side, no API needed).
# Recent content is fetched from Bloomberg/CNBC/Reuters RSS which have thumbnails.
_MEDIA_FEEDS = [
    ("Bloomberg Markets",  "https://feeds.bloomberg.com/markets/news.rss"),
    ("Bloomberg Tech",     "https://feeds.bloomberg.com/technology/news.rss"),
    ("CNBC Markets",       "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=15839069"),
    ("Yahoo Finance",      "https://finance.yahoo.com/news/rssindex"),
]
_YT_LIVE_STREAMS = [
    {"channel": "Bloomberg",      "channelId": "UCIALMKvObZNtJ6AmdCLP7Lg", "label": "Bloomberg"},
    {"channel": "CNBC TV",        "channelId": "UCvJJ_dzjViJCoLf5uKUTwoA", "label": "CNBC TV"},
    {"channel": "Al Jazeera",     "channelId": "UCfiwzLy-8yKzIbsmZTzxDgw", "label": "Al Jazeera"},
    {"channel": "Sky News",       "channelId": "UCkFclpi8U9VJjfxLYoms7Aw", "label": "Sky News"},
    {"channel": "France 24",      "channelId": "UCCCPCZNChQdGa9EkATeye4g", "label": "France 24"},
    {"channel": "TRT World",      "channelId": "UCnyCrv8b7bu0oWFXGyHaPzg", "label": "TRT World"},
]
_yt_data = None
_yt_ts   = 0.0
_yt_lock = _threading.Lock()

def _fetch_media_feed(name, url):
    import requests as _req, re as _re
    headers = {"User-Agent": "Mozilla/5.0 (compatible; MidasBot/1.0)"}
    ns = {"media": "http://search.yahoo.com/mrss/"}
    try:
        r = _req.get(url, timeout=8, headers=headers)
        if not r.ok:
            return []
        root = _ET.fromstring(r.content)
        items = root.findall(".//item")
        articles = []
        for item in items[:6]:
            def _text(tag):
                el = item.find(tag)
                return el.text.strip() if el is not None and el.text else ""
            title   = _text("title")
            link    = _text("link")
            pubdate = _text("pubDate")
            desc    = _re.sub(r"<[^>]+>", "", _text("description"))[:200]
            if not title:
                continue
            # Extract thumbnail from media:content (Bloomberg has these)
            thumb = ""
            mc = item.find("media:content", ns)
            if mc is not None:
                thumb = mc.get("url", "")
            articles.append({
                "channel": name,
                "title":   title,
                "link":    link,
                "pubdate": pubdate,
                "desc":    desc,
                "thumb":   thumb,
            })
        return articles
    except Exception as exc:
        _log.debug(f"Media feed {name}: {exc}")
        return []


def _build_yt():
    all_items = []
    for name, url in _MEDIA_FEEDS:
        all_items.extend(_fetch_media_feed(name, url))
    return {
        "updated":    _dt.now().isoformat(),
        "videos":     all_items,
        "livestreams": _YT_LIVE_STREAMS,
    }


# ── WORLD MONITOR ──────────────────────────────────────────────────────────
_MONITOR_CLOCKS = [
    ("New York",    "AMERICAS",    "America/New_York",    "9:30",  "16:00"),
    ("London",      "EUROPE",      "Europe/London",       "8:00",  "16:30"),
    ("Frankfurt",   "EUROPE",      "Europe/Berlin",       "9:00",  "17:30"),
    ("Dubai",       "MIDDLE EAST", "Asia/Dubai",          "10:00", "14:00"),
    ("Tokyo",       "ASIA",        "Asia/Tokyo",          "9:00",  "15:30"),
    ("Hong Kong",   "ASIA",        "Asia/Hong_Kong",      "9:30",  "16:00"),
    ("Sydney",      "ASIA",        "Australia/Sydney",    "10:00", "16:00"),
    ("Chicago",     "AMERICAS",    "America/Chicago",     "8:30",  "15:00"),
    ("Los Angeles", "AMERICAS",    "America/Los_Angeles", "6:30",  "13:00"),
]

def _clock_data():
    from zoneinfo import ZoneInfo
    clocks = []
    for city, region, tz, mopen, mclose in _MONITOR_CLOCKS:
        local = _dt.now(ZoneInfo(tz))
        def _to_min(t):
            h, m = t.split(":")
            return int(h)*60+int(m)
        is_weekend = local.weekday() >= 5
        now_min = local.hour*60+local.minute
        is_open = (not is_weekend) and _to_min(mopen) <= now_min < _to_min(mclose)
        clocks.append({
            "city": city, "region": region, "tz": tz,
            "time": local.strftime("%H:%M"),
            "date": local.strftime("%a %b %d"),
            "open": is_open,
            "market_hours": f"{mopen}–{mclose}",
        })
    return clocks

_monitor_data = None
_monitor_ts   = 0.0
_monitor_lock = _threading.Lock()

def _build_monitor():
    clocks = _clock_data()
    tickers = []
    for ticker in _WATCHLIST:
        try:
            s = _ticker_stats(ticker)
            if s:
                tickers.append(s)
        except Exception:
            pass
    tickers.sort(key=lambda x: (
        0 if x["signal"]=="BUY" else 1 if x["signal"]=="HOLD" else 2,
        -x["confidence"],
    ))
    return {"updated": _dt.now().isoformat(), "clocks": clocks, "tickers": tickers}


@app.route("/api/monitor")
def api_monitor():
    global _monitor_data, _monitor_ts
    # Piggyback on intel cache — if fresh intel exists reuse it for tickers
    if _monitor_data is None or _time.time() - _monitor_ts > 60:
        with _monitor_lock:
            if _monitor_data is None or _time.time() - _monitor_ts > 60:
                # Always refresh clocks; reuse cached ticker data if fresh enough
                clocks = _clock_data()
                if _intel_data and _time.time() - _intel_ts < 300:
                    tickers = _intel_data["tickers"]
                else:
                    tickers = []
                    for t in _WATCHLIST:
                        try:
                            s = _ticker_stats(t)
                            if s:
                                tickers.append(s)
                        except Exception:
                            pass
                    tickers.sort(key=lambda x:(0 if x["signal"]=="BUY" else 1 if x["signal"]=="HOLD" else 2,-x["confidence"]))
                _monitor_data = {"updated": _dt.now().isoformat(), "clocks": clocks, "tickers": tickers}
                _monitor_ts = _time.time()
    return jsonify(_monitor_data)


@app.route("/api/youtube")
def api_youtube():
    global _yt_data, _yt_ts
    if _yt_data is None or _time.time() - _yt_ts > 1800:
        with _yt_lock:
            if _yt_data is None or _time.time() - _yt_ts > 1800:
                _yt_data = _build_yt()
                _yt_ts   = _time.time()
    return jsonify(_yt_data)


@app.route("/api/news")
def api_news():
    global _news_data, _news_ts
    if _news_data is None or _time.time() - _news_ts > 600:
        with _news_lock:
            if _news_data is None or _time.time() - _news_ts > 600:
                _news_data = _build_news()
                _news_ts   = _time.time()
    return jsonify(_news_data)


# ── WHISPERS — AI news edge (feeds → Claude filter → corroboration ledger) ────
_whisper_data = None
_whisper_ts   = 0.0
_whisper_lock = _threading.Lock()


# ── Tier gating: Free sees a limited slice of the wire; Pro/Premium see it all ─
FREE_WHISPER_LIMIT = 6


def _current_tier():
    u = _current_user()
    return (u.get("tier") if u else "free") or "free"


def _gate_whispers(data):
    """Apply the free-tier limit based on the logged-in user (or anonymous=free)."""
    if not data:
        return data
    tier = _current_tier()
    out = dict(data)
    if tier in ("pro", "premium"):
        out.update(tier=tier, gated=False, locked=0)
        return out
    h = list(data.get("haulers", []) or [])
    w = list(data.get("whispers", []) or [])
    total = len(h) + len(w)
    keep_h = h[:FREE_WHISPER_LIMIT]
    keep_w = w[:max(0, FREE_WHISPER_LIMIT - len(keep_h))]
    out["haulers"]  = keep_h
    out["whispers"] = keep_w
    out.update(tier="free", gated=True,
               locked=max(0, total - (len(keep_h) + len(keep_w))))
    return out


@app.route("/api/whispers")
def api_whispers():
    """Early-signal feed: rising news events not yet mainstream. Cached 10 min
    because each refresh runs Claude over the latest headlines."""
    global _whisper_data, _whisper_ts
    if _whisper_data is None or _time.time() - _whisper_ts > 600:
        with _whisper_lock:
            if _whisper_data is None or _time.time() - _whisper_ts > 600:
                try:
                    from brain.news_pipeline import run_scan
                    _whisper_data = run_scan()
                except Exception as e:
                    _log.warning(f"Whisper scan failed: {e}")
                    _whisper_data = {"updated": _dt.now().isoformat(),
                                     "whispers": [], "top": [], "error": str(e)}
                _whisper_ts = _time.time()
    # The Wire is FREE (no gating). Subscriptions gate the TRADING PROGRAMS
    # (semi-auto / full-auto), not the social/content layer. _gate_whispers kept
    # for reference but no longer applied.
    return jsonify(_whisper_data)


@app.route("/api/suggestions")
def api_suggestions():
    """Semi-auto: the top Wire signals as approve-able trade suggestions."""
    global _whisper_data, _whisper_ts
    if _whisper_data is None or _time.time() - _whisper_ts > 600:
        with _whisper_lock:
            if _whisper_data is None or _time.time() - _whisper_ts > 600:
                try:
                    from brain.news_pipeline import run_scan
                    _whisper_data = run_scan()
                except Exception as e:
                    _log.warning(f"suggestions scan failed: {e}")
                    _whisper_data = {"whispers": [], "haulers": []}
                _whisper_ts = _time.time()
    rows = (_whisper_data.get("haulers", []) or []) + (_whisper_data.get("whispers", []) or [])
    rows = sorted(rows, key=lambda x: x.get("confidence", 0), reverse=True)[:8]
    sugg = [{"ticker": w.get("ticker"), "direction": w.get("direction"),
             "confidence": round(w.get("confidence", 0)), "stage": w.get("stage"),
             "amount": 200} for w in rows if w.get("ticker")]
    return jsonify({"suggestions": sugg, "tier": _current_tier()})


@app.route("/api/ticker/<symbol>")
def api_ticker(symbol):
    """Confidence + clickable source feed for one ticker. Powers the user flow:
    search a ticker -> see its confidence -> drill into the live source feed."""
    try:
        from brain.signal_ledger import SignalLedger
        return jsonify(SignalLedger().ticker_feed(symbol))
    except Exception as e:
        _log.warning(f"Ticker feed failed: {e}")
        return jsonify({"ticker": symbol.upper(), "confidence": 0.0,
                        "direction": "neutral", "events": [], "error": str(e)})


# ── SEARCH: "type anything -> news + whispers on it" ─────────────────────────
_NAME_TO_TICKER = {
    "apple":"AAPL","microsoft":"MSFT","nvidia":"NVDA","amazon":"AMZN","alphabet":"GOOGL",
    "google":"GOOGL","meta":"META","facebook":"META","tesla":"TSLA","netflix":"NFLX",
    "amd":"AMD","intel":"INTC","broadcom":"AVGO","oracle":"ORCL","salesforce":"CRM",
    "adobe":"ADBE","qualcomm":"QCOM","cisco":"CSCO","ibm":"IBM","palantir":"PLTR",
    "micron":"MU","super micro":"SMCI","supermicro":"SMCI","arm":"ARM","snowflake":"SNOW",
    "jpmorgan":"JPM","jp morgan":"JPM","bank of america":"BAC","wells fargo":"WFC",
    "goldman sachs":"GS","morgan stanley":"MS","visa":"V","mastercard":"MA","paypal":"PYPL",
    "berkshire":"BRK.B","exxon":"XOM","chevron":"CVX","shell":"SHEL","conocophillips":"COP",
    "occidental":"OXY","pfizer":"PFE","moderna":"MRNA","johnson & johnson":"JNJ","merck":"MRK",
    "eli lilly":"LLY","abbvie":"ABBV","unitedhealth":"UNH","cvs":"CVS","walmart":"WMT",
    "costco":"COST","target":"TGT","home depot":"HD","nike":"NKE","mcdonalds":"MCD",
    "mcdonald's":"MCD","starbucks":"SBUX","coca-cola":"KO","coca cola":"KO","pepsi":"PEP",
    "procter & gamble":"PG","disney":"DIS","ford":"F","general motors":"GM","gm":"GM",
    "rivian":"RIVN","lucid":"LCID","boeing":"BA","caterpillar":"CAT","general electric":"GE",
    "ge":"GE","3m":"MMM","lockheed":"LMT","coinbase":"COIN","robinhood":"HOOD","block":"SQ",
    "square":"SQ","uber":"UBER","lyft":"LYFT","airbnb":"ABNB","spotify":"SPOT","snap":"SNAP",
    "pinterest":"PINS","reddit":"RDDT","draftkings":"DKNG","wendys":"WEN","wendy's":"WEN",
    "chipotle":"CMG","gamestop":"GME",
}
_TICKER_TO_NAME = {}
for _nm, _tk in _NAME_TO_TICKER.items():
    _TICKER_TO_NAME.setdefault(_tk, _nm)


def _news_cached():
    """Return the cached news bundle (same 10-min cache as /api/news)."""
    global _news_data, _news_ts
    if _news_data is None or _time.time() - _news_ts > 600:
        with _news_lock:
            if _news_data is None or _time.time() - _news_ts > 600:
                _news_data = _build_news()
                _news_ts = _time.time()
    return _news_data or {"articles": []}


def _resolve_ticker(q):
    """Best-effort: company name -> ticker, or a bare symbol -> itself."""
    import re as _re
    ql = q.strip().lower()
    if ql in _NAME_TO_TICKER:
        return _NAME_TO_TICKER[ql]
    qu = q.strip().upper()
    if _re.fullmatch(r"[A-Z]{1,5}(\.[A-Z])?", qu):
        return qu
    return None


def _search_terms(q, ticker):
    """The set of lowercased strings we match against news/ledger."""
    terms = set()
    ql = q.strip().lower()
    if len(ql) >= 2:
        terms.add(ql)
    if ticker:
        if len(ticker) >= 2:
            terms.add(ticker.lower())
        nm = _TICKER_TO_NAME.get(ticker)
        if nm and len(nm) >= 3:
            terms.add(nm)
    return terms


def _finnhub_news(ticker, days=14, limit=12):
    """Recent company-specific news from Finnhub (far richer than the thin RSS)."""
    key = os.getenv("FINNHUB_API_KEY")
    if not key or not ticker:
        return []
    import requests as _req
    to  = _dt.now().date().isoformat()
    frm = (_dt.now() - _timedelta(days=days)).date().isoformat()
    try:
        r = _req.get("https://finnhub.io/api/v1/company-news",
                     params={"symbol": ticker, "from": frm, "to": to, "token": key}, timeout=8)
        items = r.json() if r.ok else []
        return [{"title": a.get("headline"), "link": a.get("url"), "source": a.get("source"),
                 "desc": (a.get("summary") or "")[:180], "region": ""}
                for a in items[:limit] if a.get("headline")]
    except Exception:
        return []


def _news_search(terms, limit=24):
    """Rank cached headlines by term hits (title weighted over description)."""
    arts = _news_cached().get("articles", [])
    hits = []
    for a in arts:
        t = (a.get("title") or "").lower()
        d = (a.get("desc") or "").lower()
        score = 0
        for term in terms:
            if term in t:   score += 2
            elif term in d: score += 1
        if score:
            hits.append((score, a))
    hits.sort(key=lambda x: x[0], reverse=True)
    return [a for _s, a in hits[:limit]]


def _ledger_related(led, terms, exclude=None):
    """Other tickers whose signal/headlines mention the query (deduped, top by conf)."""
    best = {}
    try:
        for ev in led.state["events"].values():
            tk = (ev.get("ticker") or "").upper()
            if not tk or (exclude and tk == exclude.upper()):
                continue
            hay = (tk + " " + " ".join(
                (m.get("title") or "") for m in ev.get("mentions_log", []))).lower()
            if any(term in hay for term in terms):
                conf = round(led.confidence(ev), 1)
                if tk not in best or conf > best[tk]["confidence"]:
                    best[tk] = {"ticker": tk, "confidence": conf,
                                "direction": led.direction(ev), "stage": led.stage(ev),
                                "source_count": len(ev.get("sources", []))}
    except Exception as exc:
        _log.debug(f"ledger related search: {exc}")
    return sorted(best.values(), key=lambda r: r["confidence"], reverse=True)[:8]


@app.route("/api/find")
def api_find():
    """Search anything (ticker, company name, or topic) -> news + whispers on it."""
    q = (request.args.get("q") or "").strip()
    if not q:
        return jsonify({"query": "", "ticker": None, "whispers": None,
                        "related": [], "news": []})
    ticker  = _resolve_ticker(q)
    terms   = _search_terms(q, ticker)
    whispers = None
    related  = []
    try:
        from brain.signal_ledger import SignalLedger
        led = SignalLedger()
        if ticker:
            tf = led.ticker_feed(ticker)
            if tf.get("events"):
                whispers = tf
        related = _ledger_related(led, terms, exclude=ticker)
    except Exception as exc:
        _log.warning(f"find whispers failed: {exc}")
    news = _news_search(terms) if terms else []
    if ticker:   # richer per-company news from Finnhub, prepended + deduped
        seen = {n.get("title") for n in news}
        news = [a for a in _finnhub_news(ticker)
                if a.get("title") and a.get("title") not in seen] + news
    return jsonify({"query": q, "ticker": ticker, "whispers": whispers,
                    "related": related, "news": news})


# ── ACCOUNTS — register / login / session (Phase 2 platform foundation) ──────
# SQLite-backed. Stores only email + password hash + tier. No money or brokerage
# keys here — per-user brokerage comes later via OAuth. Stay software, not a bank.
def _current_user():
    from brain import accounts
    return accounts.get_user(session.get("uid"))


@app.route("/api/register", methods=["POST"])
def api_register():
    from brain import accounts
    data = request.get_json(silent=True) or {}
    res = accounts.create_user(data.get("email"), data.get("password"),
                               data.get("first_name"), data.get("last_name"),
                               data.get("country"), data.get("state"),
                               data.get("nickname"))
    if res.get("error"):
        return jsonify(res), 400
    session["uid"] = res["user"]["id"]
    return jsonify({"user": res["user"]})


@app.route("/api/login", methods=["POST"])
def api_login():
    from brain import accounts
    data = request.get_json(silent=True) or {}
    user = accounts.verify_user(data.get("email"), data.get("password"))
    if not user:
        return jsonify({"error": "Wrong email or password."}), 401
    session["uid"] = user["id"]
    return jsonify({"user": user})


@app.route("/api/logout", methods=["POST"])
def api_logout():
    session.pop("uid", None)
    return jsonify({"ok": True})


@app.route("/api/me")
def api_me():
    u = _current_user()
    if u:
        u = dict(u)
        u["is_admin"] = _is_admin(u)
    return jsonify({"user": u})


@app.route("/api/set-nickname", methods=["POST"])
def api_set_nickname():
    from brain import accounts
    user = _current_user()
    if not user:
        return jsonify({"error": "Log in first."}), 401
    data = request.get_json(silent=True) or {}
    res = accounts.set_nickname(user["id"], data.get("nickname", ""))
    return jsonify(res), (400 if res.get("error") else 200)


@app.route("/api/rooms", methods=["GET", "POST"])
def api_rooms():
    from brain import chat
    if request.method == "POST":
        user = _current_user()
        if not user:
            return jsonify({"error": "Log in to create a room."}), 401
        data = request.get_json(silent=True) or {}
        res = chat.create_room(user, data.get("name"), data.get("topic"))
        return jsonify(res), (400 if res.get("error") else 200)
    return jsonify({"rooms": chat.list_rooms()})


@app.route("/api/rooms/<int:room_id>/messages", methods=["GET", "POST"])
def api_room_messages(room_id):
    from brain import chat
    if request.method == "POST":
        user = _current_user()
        if not user:
            return jsonify({"error": "Log in to chat."}), 401
        data = request.get_json(silent=True) or {}
        res = chat.post_message(user, room_id, data.get("text"))
        if not res.get("error"):
            try:
                from brain import notifications, accounts
                for uid in accounts.resolve_handles(data.get("text", "")):
                    if uid != user["id"]:
                        notifications.push(uid, "mention",
                                           f"{user['name']} mentioned you in the Pit", "pit.html")
            except Exception:
                pass
        return jsonify(res), (400 if res.get("error") else 200)
    after = int(request.args.get("after", 0) or 0)
    return jsonify(chat.list_messages(room_id, after))


@app.route("/api/rooms/<int:room_id>/plays", methods=["GET", "POST"])
def api_room_plays(room_id):
    from brain import chat
    if request.method == "POST":
        user = _current_user()
        if not user:
            return jsonify({"error": "Log in to add a play."}), 401
        data = request.get_json(silent=True) or {}
        res = chat.add_play(user, room_id, data.get("ticker"), data.get("note"))
        return jsonify(res), (400 if res.get("error") else 200)
    return jsonify({"plays": chat.list_plays(room_id)})


@app.route("/api/rooms/<int:room_id>/plays/<int:play_id>", methods=["DELETE"])
def api_room_play_del(room_id, play_id):
    from brain import chat
    if not _current_user():
        return jsonify({"error": "Log in."}), 401
    return jsonify(chat.remove_play(room_id, play_id))


# ── BILLING — Stripe subscriptions (graceful if unconfigured) ────────────────
@app.route("/api/billing/status")
def api_billing_status():
    from brain import billing
    return jsonify(billing.status())


@app.route("/api/billing/checkout", methods=["POST"])
def api_billing_checkout():
    from brain import billing
    user = _current_user()
    if not user:
        return jsonify({"error": "Log in first."}), 401
    data = request.get_json(silent=True) or {}
    res  = billing.create_checkout(user, data.get("tier"), request.host_url)
    return jsonify(res), (400 if res.get("error") else 200)


@app.route("/api/billing/portal", methods=["POST"])
def api_billing_portal():
    from brain import billing
    user = _current_user()
    if not user:
        return jsonify({"error": "Log in first."}), 401
    res = billing.create_portal_session(user, request.host_url)
    return jsonify(res), (400 if res.get("error") else 200)


@app.route("/api/billing/webhook", methods=["POST"])
def api_billing_webhook():
    from brain import billing, accounts
    res = billing.handle_webhook(request.get_data(),
                                 request.headers.get("Stripe-Signature", ""))
    act = res.get("action")
    try:
        if act == "set_tier" and res.get("uid"):
            accounts.set_tier(int(res["uid"]), res.get("tier", "pro"))
            if res.get("customer"):
                accounts.set_stripe_customer(int(res["uid"]), res["customer"])
        elif act == "set_tier_customer" and res.get("customer"):
            accounts.set_tier_by_customer(res["customer"], res.get("tier", "pro"))
        elif act == "downgrade" and res.get("customer"):
            accounts.set_tier_by_customer(res["customer"], "free")
    except Exception as e:
        _log.warning(f"billing webhook apply failed: {e}")
    return jsonify({"received": True})


# ── COMMENTS — crowd discussion under wire stories (micro signal, ~1% nudge) ──
@app.route("/api/comments", methods=["GET", "POST"])
def api_comments():
    from brain import comments
    if request.method == "POST":
        user = _current_user()
        if not user:
            return jsonify({"error": "Log in to comment."}), 401
        data = request.get_json(silent=True) or {}
        res = comments.add_comment(user, data.get("event_id"), data.get("ticker"),
                                   data.get("text"), parent_id=data.get("parent_id"))
        if res.get("ok"):
            try:
                from brain import notifications, accounts
                notified = set()
                if data.get("parent_id"):
                    pa = comments.author_of(int(data["parent_id"]))
                    if pa and pa.get("user_id") and pa["user_id"] != user["id"]:
                        notifications.push(pa["user_id"], "reply",
                                           f"{user['name']} replied to your take", "gossip.html")
                        notified.add(pa["user_id"])
                for uid in accounts.resolve_handles(data.get("text", "")):
                    if uid != user["id"] and uid not in notified:
                        notifications.push(uid, "mention", f"{user['name']} mentioned you", "gossip.html")
            except Exception:
                pass
        return jsonify(res), (400 if res.get("error") else 200)
    eid = request.args.get("event_id")
    tk  = request.args.get("ticker")
    if not eid and not tk:
        return jsonify(comments.recent_all())   # global feed: talk across all stories
    return jsonify(comments.list_comments(eid, tk))


@app.route("/api/comments/vote", methods=["POST"])
def api_comments_vote():
    from brain import comments
    user = _current_user()
    if not user:
        return jsonify({"error": "Log in to vote."}), 401
    data = request.get_json(silent=True) or {}
    cid = data.get("comment_id")
    if not cid:
        return jsonify({"error": "comment_id required"}), 400
    return jsonify(comments.vote(user["id"], int(cid), data.get("dir", "up")))


@app.route("/api/comments/thread/<int:comment_id>")
def api_comments_thread(comment_id):
    from brain import comments
    return jsonify(comments.thread(comment_id))


def _send_email(to, subject, body):
    """Send via SMTP if configured (SMTP_HOST/USER/PASS env); else False (dev mode
    shows the link in the UI). Keeps Midas runnable with no email service set up."""
    host = os.getenv("SMTP_HOST")
    if not host:
        return False
    try:
        import smtplib
        from email.mime.text import MIMEText
        msg = MIMEText(body)
        msg["Subject"] = subject
        msg["From"] = os.getenv("SMTP_FROM", os.getenv("SMTP_USER", "no-reply@midas.app"))
        msg["To"] = to
        s = smtplib.SMTP(host, int(os.getenv("SMTP_PORT", "587")))
        s.starttls()
        if os.getenv("SMTP_USER"):
            s.login(os.getenv("SMTP_USER"), os.getenv("SMTP_PASS", ""))
        s.send_message(msg)
        s.quit()
        return True
    except Exception:
        return False


@app.route("/api/verify-email")
def api_verify_email():
    from brain import accounts
    u = accounts.verify_email(request.args.get("token", ""))
    msg = ("Email verified — you're all set."
           if u else "This link is invalid or already used.")
    return ("<html><body style='font-family:system-ui,sans-serif;background:#f5f0e8;color:#1e1a14;"
            "text-align:center;padding:80px 20px'><h2 style='font-weight:600'>" + msg + "</h2>"
            "<p><a href='/settings.html' style='color:#1a7080'>← Back to Midas settings</a></p>"
            "</body></html>")


@app.route("/api/resend-verify", methods=["POST"])
def api_resend_verify():
    from brain import accounts
    user = _current_user()
    if not user:
        return jsonify({"error": "Log in first."}), 401
    if user.get("verified"):
        return jsonify({"ok": True, "already": True})
    tok = accounts.get_verify_token(user["id"])
    link = request.host_url.rstrip("/") + "/api/verify-email?token=" + (tok or "")
    sent = _send_email(user["email"], "Verify your Midas email",
                       "Confirm your email for Project Midas:\n\n" + link +
                       "\n\nIf you didn't sign up, ignore this.")
    return jsonify({"ok": True, "sent": sent, "link": (None if sent else link)})


@app.route("/api/profile/<int:user_id>")
def api_profile(user_id):
    """A person's public profile: real name + reputation + their actual takes."""
    from brain import accounts, reputation, comments
    u = accounts.get_user(user_id)
    if not u:
        return jsonify({"error": "No such user."}), 404
    from brain import parlor, social, exchange
    rep = reputation.compute(user_id)   # scores their calls vs real price moves
    port = reputation.portfolio(user_id)
    takes = comments.by_user(user_id, 12)   # their most-upvoted posts
    me = _current_user()
    is_me = bool(me and me["id"] == user_id)
    sc = social.counts(user_id)
    return jsonify({"id": u["id"], "name": u["name"], "real_name": u.get("real_name"),
                    "nickname": u.get("nickname"), "verified": u.get("verified"),
                    "bio": u.get("bio", ""), "country": u.get("country", ""),
                    "state": u.get("state", ""), "tier": u["tier"], "joined": u.get("created_at"),
                    "reputation": rep, "portfolio": port, "takes": takes,
                    "parlor": parlor.record(user_id), "is_me": is_me,
                    "followers": sc["followers"], "following": sc["following"],
                    "i_follow": bool(me and not is_me and social.is_following(me["id"], user_id)),
                    "mutual": bool(me and not is_me and social.is_mutual(me["id"], user_id)),
                    "dm_privacy": (u.get("dm_privacy") or "open") if is_me else None,
                    "handle": u.get("handle", ""), "exchange": exchange.scout(user_id)})


@app.route("/api/leaderboard")
def api_leaderboard():
    """World / country / state ranking by reputation (scored from calls)."""
    from brain import reputation
    return jsonify(reputation.leaderboard(
        request.args.get("scope", "world"),
        request.args.get("country", ""), request.args.get("state", "")))


@app.route("/api/daynews")
def api_daynews():
    """Historical news around one date for the Replay 'what hit the market' view.
    Pulls Finnhub company-news for a basket and merges. Graceful w/o Finnhub key."""
    import requests as _req
    date = (request.args.get("date") or "").strip()[:10]
    tickers = (request.args.get("tickers") or "SPY,AAPL,MSFT,NVDA").upper().split(",")[:5]
    key = os.getenv("FINNHUB_API_KEY")
    if not date:
        return jsonify({"date": date, "news": []})
    if not key:
        return jsonify({"date": date, "news": [], "note": "Finnhub key not set"})
    try:
        d0 = _dt.fromisoformat(date)
    except Exception:
        return jsonify({"date": date, "news": [], "error": "bad date"})
    frm = (d0 - _timedelta(days=1)).date().isoformat()
    to  = (d0 + _timedelta(days=1)).date().isoformat()
    seen, news = set(), []
    for tk in tickers:
        tk = tk.strip()
        if not tk:
            continue
        try:
            r = _req.get("https://finnhub.io/api/v1/company-news",
                         params={"symbol": tk, "from": frm, "to": to, "token": key}, timeout=8)
            for a in (r.json() if r.ok else []):
                h = a.get("headline", "")
                if h and h not in seen:
                    seen.add(h)
                    news.append({"headline": h, "source": a.get("source"),
                                 "url": a.get("url"), "summary": (a.get("summary") or "")[:180],
                                 "ticker": tk})
        except Exception:
            continue
    return jsonify({"date": date, "news": news[:20]})


@app.route("/api/fundamentals/<ticker>")
def api_fundamentals(ticker):
    """Insider transactions + last earnings surprise (Finnhub). New signal layers."""
    import requests as _req
    key = os.getenv("FINNHUB_API_KEY")
    tk = (ticker or "").upper().strip()
    out = {"ticker": tk, "insider": None, "earnings": None}
    if not key or not tk:
        return jsonify(out)
    try:
        frm = (_dt.now() - _timedelta(days=90)).date().isoformat()
        to  = _dt.now().date().isoformat()
        r = _req.get("https://finnhub.io/api/v1/stock/insider-transactions",
                     params={"symbol": tk, "from": frm, "to": to, "token": key}, timeout=8)
        data = ((r.json() or {}).get("data") if r.ok else []) or []
        if data:
            buys  = sum(1 for d in data if (d.get("change") or 0) > 0)
            sells = sum(1 for d in data if (d.get("change") or 0) < 0)
            net   = sum((d.get("change") or 0) for d in data)
            out["insider"] = {"buys": buys, "sells": sells, "net_shares": int(net), "count": len(data)}
    except Exception:
        pass
    try:
        r = _req.get("https://finnhub.io/api/v1/stock/earnings",
                     params={"symbol": tk, "token": key}, timeout=8)
        e = (r.json() if r.ok else []) or []
        if e:
            last = e[0]
            out["earnings"] = {"period": last.get("period"), "actual": last.get("actual"),
                               "estimate": last.get("estimate"), "surprise": last.get("surprise"),
                               "surprise_pct": last.get("surprisePercent")}
    except Exception:
        pass
    return jsonify(out)


@app.route("/api/history/<ticker>")
def api_history(ticker):
    """Price history for the company dossier. range: 1D | 1W | 1M | 1Y | max."""
    tk = (ticker or "").upper().strip()
    rng = request.args.get("range", "1M")
    key, sec = os.getenv("ALPACA_API_KEY"), os.getenv("ALPACA_SECRET_KEY")
    if not tk or not key:
        return jsonify({"ticker": tk, "points": []})
    try:
        from alpaca.data.historical import StockHistoricalDataClient
        from alpaca.data.requests import StockBarsRequest
        from alpaca.data.timeframe import TimeFrame
        client = StockHistoricalDataClient(key, sec)
        cfg = {"1D": (_timedelta(days=5), TimeFrame.Hour),
               "1W": (_timedelta(days=8), TimeFrame.Day),
               "1M": (_timedelta(days=32), TimeFrame.Day),
               "1Y": (_timedelta(days=366), TimeFrame.Day),
               "max": (_timedelta(days=365 * 5), TimeFrame.Day)}.get(rng, (_timedelta(days=32), TimeFrame.Day))
        start, tf = cfg
        req = StockBarsRequest(symbol_or_symbols=tk, timeframe=tf, start=_dt.now() - start)
        bars = client.get_stock_bars(req)
        data = bars.data.get(tk, []) if hasattr(bars, "data") else []
        pts = [{"t": b.timestamp.isoformat(), "c": round(float(b.close), 2)} for b in data]
        if rng == "1D" and pts:
            last_day = pts[-1]["t"][:10]
            pts = [p for p in pts if p["t"][:10] == last_day]
        chg = round((pts[-1]["c"] - pts[0]["c"]) / pts[0]["c"] * 100, 2) if len(pts) > 1 and pts[0]["c"] else 0
        return jsonify({"ticker": tk, "range": rng, "points": pts, "change_pct": chg,
                        "first": pts[0]["c"] if pts else None, "last": pts[-1]["c"] if pts else None})
    except Exception as e:
        return jsonify({"ticker": tk, "points": [], "error": str(e)})


@app.route("/api/ownership/<ticker>")
def api_ownership(ticker):
    """Who holds what — institutional holders + shares outstanding (Finnhub)."""
    import requests as _req
    key = os.getenv("FINNHUB_API_KEY")
    tk = (ticker or "").upper().strip()
    out = {"ticker": tk, "name": None, "shares_outstanding": None,
           "holders": [], "institutional_pct": None}
    if not key or not tk:
        return jsonify(out)
    so = None
    try:
        p = _req.get("https://finnhub.io/api/v1/stock/profile2",
                     params={"symbol": tk, "token": key}, timeout=8).json() or {}
        so = p.get("shareOutstanding")
        out["shares_outstanding"] = so
        out["name"] = p.get("name")
    except Exception:
        pass
    try:
        r = _req.get("https://finnhub.io/api/v1/stock/ownership",
                     params={"symbol": tk, "limit": 15, "token": key}, timeout=8)
        data = ((r.json() or {}).get("ownership") if r.ok else []) or []
        out["holders"] = [{"name": h.get("name"), "shares": h.get("share"),
                           "change": h.get("change")} for h in data[:12]]
        if so:
            inst = sum((h.get("share") or 0) for h in data)
            so_units = so * 1_000_000
            if so_units:
                out["institutional_pct"] = round(min(100.0, inst / so_units * 100), 1)
    except Exception:
        pass
    # Finnhub ownership is premium; fall back to Financial Modeling Prep (free tier)
    if not out["holders"]:
        fmp = os.getenv("FMP_API_KEY")
        if fmp:
            try:
                r = _req.get(f"https://financialmodelingprep.com/api/v3/institutional-holder/{tk}",
                             params={"apikey": fmp}, timeout=10)
                data = (r.json() if r.ok else []) or []
                out["holders"] = [{"name": h.get("holder"), "shares": h.get("shares"),
                                   "change": h.get("change")} for h in data[:12] if h.get("holder")]
                if so and data:
                    inst = sum((h.get("shares") or 0) for h in data)
                    so_units = so * 1_000_000
                    if so_units:
                        out["institutional_pct"] = round(min(100.0, inst / so_units * 100), 1)
                out["source"] = "fmp"
            except Exception:
                pass
    return jsonify(out)


_funnies_cache = None
_funnies_ts = 0.0
_FUNNY_SUBS = [
    ("r/wallstreetbets", "https://www.reddit.com/r/wallstreetbets/hot/.rss?limit=25"),
    ("r/financememes",   "https://www.reddit.com/r/financememes/hot/.rss?limit=25"),
]

def _fetch_funnies():
    import requests as _req, re as _re2
    headers = {"User-Agent": "python:project-midas:1.0 (funnies)"}
    ns = {"atom": "http://www.w3.org/2005/Atom"}
    out = []
    for src, url in _FUNNY_SUBS:
        try:
            r = _req.get(url, headers=headers, timeout=8)
            if not r.ok:
                continue
            root = _ET.fromstring(r.content)
            for e in (root.findall(".//atom:entry", ns) or [])[:15]:
                te = e.find("atom:title", ns)
                le = e.find("atom:link", ns)
                ce = e.find("atom:content", ns)
                title = (te.text or "").strip() if te is not None else ""
                link = le.get("href") if le is not None else ""
                thumb = ""
                if ce is not None and ce.text:
                    mm = _re2.search(r'<img[^>]+src="([^"]+)"', ce.text)
                    if mm:
                        thumb = mm.group(1).replace("&amp;", "&")
                if title and link:
                    out.append({"title": title, "link": link, "source": src, "thumb": thumb})
        except Exception:
            continue
    return out


@app.route("/api/funnies")
def api_funnies():
    """Live market humor from the wild (cached 30 min, graceful if reddit is grumpy)."""
    global _funnies_cache, _funnies_ts
    if _funnies_cache is None or _time.time() - _funnies_ts > 1800:
        try:
            _funnies_cache = {"items": _fetch_funnies(), "updated": _dt.now().isoformat()}
        except Exception:
            _funnies_cache = {"items": [], "updated": _dt.now().isoformat()}
        _funnies_ts = _time.time()
    return jsonify(_funnies_cache)


_ALLOWED_IMG = {".png", ".jpg", ".jpeg", ".gif", ".webp"}


def _is_admin(user):
    em = os.getenv("ADMIN_EMAIL", "").strip().lower()
    return bool(user and em and (user.get("email") or "").lower() == em)


@app.route("/api/funnies/submit", methods=["POST"])
def api_funnies_submit():
    """Submit a comic/artwork — goes to a pending queue, never auto-published."""
    from brain import funnies
    user = _current_user()
    if not user:
        return jsonify({"error": "Log in to submit."}), 401
    f = request.files.get("image")
    if not f or not f.filename:
        return jsonify({"error": "Pick an image (your comic or artwork)."}), 400
    ext = os.path.splitext(f.filename)[1].lower()
    if ext not in _ALLOWED_IMG:
        return jsonify({"error": "Images only: png, jpg, gif, or webp."}), 400
    try:
        os.makedirs(funnies.UPLOAD_DIR, exist_ok=True)
        name = _secrets.token_hex(16) + ext          # random name, never trust user filenames
        f.save(os.path.join(funnies.UPLOAD_DIR, name))
    except Exception:
        return jsonify({"error": "Upload failed."}), 500
    return jsonify(funnies.submit(user, request.form.get("caption", ""), name))


@app.route("/api/funnies/featured")
def api_funnies_featured():
    from brain import funnies
    return jsonify({"items": funnies.list_featured()})


@app.route("/uploads/<path:filename>")
def serve_upload(filename):
    from brain import funnies
    return send_from_directory(funnies.UPLOAD_DIR, filename)


@app.route("/api/funnies/pending")
def api_funnies_pending():
    from brain import funnies
    if not _is_admin(_current_user()):
        return jsonify({"error": "Admin only."}), 403
    return jsonify({"items": funnies.list_pending()})


@app.route("/api/funnies/moderate", methods=["POST"])
def api_funnies_moderate():
    from brain import funnies
    if not _is_admin(_current_user()):
        return jsonify({"error": "Admin only."}), 403
    data = request.get_json(silent=True) or {}
    if not data.get("id"):
        return jsonify({"error": "id required"}), 400
    return jsonify(funnies.moderate(int(data["id"]), data.get("action")))


# ── THE PARLOR — play-money prediction markets (Phase 0 of the Kalshi path) ──
@app.route("/parlor")
def parlor_page():
    return send_from_directory(BASE, "parlor.html")


@app.route("/api/parlor/markets")
def api_parlor_markets():
    from brain import parlor
    parlor.seed_if_empty()
    parlor.seed_culture()      # top-up: culture categories on an already-seeded board
    out = {"markets": parlor.list_markets(request.args.get("status", "open"))}
    u = _current_user()
    if u:
        out["balance"] = parlor.get_balance(u["id"])
    return jsonify(out)


@app.route("/api/parlor/me")
def api_parlor_me():
    from brain import parlor
    u = _current_user()
    if not u:
        return jsonify({"error": "Log in to play."}), 401
    return jsonify({"balance": parlor.get_balance(u["id"]), "bets": parlor.user_bets(u["id"]),
                    "record": parlor.record(u["id"])})


@app.route("/api/parlor/bet", methods=["POST"])
def api_parlor_bet():
    from brain import parlor
    u = _current_user()
    if not u:
        return jsonify({"error": "Log in to place a bet."}), 401
    data = request.get_json(silent=True) or {}
    res = parlor.place_bet(u["id"], data.get("market_id"), data.get("side"), data.get("stake"))
    return jsonify(res), (400 if res.get("error") else 200)


@app.route("/api/parlor/leaderboard")
def api_parlor_leaderboard():
    from brain import parlor, accounts
    rows = parlor.leaderboard(sort=request.args.get("sort", "rich"))
    for r in rows:
        u = accounts.get_user(r["user_id"])
        r["name"] = u["name"] if u else "Anon"
    return jsonify({"leaders": rows})


@app.route("/api/parlor/market", methods=["POST"])
def api_parlor_create():
    from brain import parlor
    u = _current_user()
    if not _is_admin(u):
        return jsonify({"error": "Admin only."}), 403
    data = request.get_json(silent=True) or {}
    res = parlor.create_market(data.get("question", ""), data.get("ticker", ""),
                               data.get("rule", ""), data.get("closes_at", ""),
                               data.get("category", ""), u["id"],
                               threshold=data.get("threshold"), ticker2=data.get("ticker2", ""))
    return jsonify(res), (400 if res.get("error") else 200)


@app.route("/api/parlor/resolve", methods=["POST"])
def api_parlor_resolve():
    from brain import parlor
    if not _is_admin(_current_user()):
        return jsonify({"error": "Admin only."}), 403
    data = request.get_json(silent=True) or {}
    res = parlor.resolve_market(int(data.get("market_id", 0)), data.get("outcome"))
    if not res.get("error"):
        _notify_bet_settled(res)
    return jsonify(res), (400 if res.get("error") else 200)


# ── THE EXCHANGE — trade anything like a stock (play-money hype market) ───────
@app.route("/exchange")
def exchange_page():
    return send_from_directory(BASE, "exchange.html")


@app.route("/api/exchange/assets")
def api_exchange_assets():
    from brain import exchange, parlor
    exchange.seed_if_empty()
    out = {"assets": exchange.list_assets(request.args.get("category") or None)}
    u = _current_user()
    if u:
        out["balance"] = parlor.get_balance(u["id"])
        out["is_admin"] = _is_admin(u)
        if _is_admin(u):
            out["pending"] = exchange.list_pending()
    return jsonify(out)


@app.route("/api/exchange/asset/<ticker>")
def api_exchange_asset(ticker):
    from brain import exchange
    a = exchange.get_asset(ticker)
    if not a:
        return jsonify({"error": "No such asset."}), 404
    return jsonify(a)


@app.route("/api/exchange/portfolio")
def api_exchange_portfolio():
    from brain import exchange
    u = _current_user()
    if not u:
        return jsonify({"error": "Log in first."}), 401
    return jsonify(exchange.portfolio(u["id"]))


@app.route("/api/exchange/buy", methods=["POST"])
def api_exchange_buy():
    from brain import exchange
    u = _current_user()
    if not u:
        return jsonify({"error": "Log in to trade."}), 401
    data = request.get_json(silent=True) or {}
    res = exchange.buy(u["id"], data.get("ticker", ""), data.get("bucks"))
    return jsonify(res), (400 if res.get("error") else 200)


@app.route("/api/exchange/sell", methods=["POST"])
def api_exchange_sell():
    from brain import exchange
    u = _current_user()
    if not u:
        return jsonify({"error": "Log in to trade."}), 401
    data = request.get_json(silent=True) or {}
    res = exchange.sell(u["id"], data.get("ticker", ""), data.get("shares"))
    return jsonify(res), (400 if res.get("error") else 200)


@app.route("/api/exchange/submit", methods=["POST"])
def api_exchange_submit():
    from brain import exchange
    u = _current_user()
    if not u:
        return jsonify({"error": "Log in to submit a listing."}), 401
    data = request.get_json(silent=True) or {}
    res = exchange.create_asset(
        data.get("name", ""), data.get("category", ""), data.get("blurb", ""),
        data.get("ticker", ""), created_by=u["id"], link=data.get("link", ""),
        image=data.get("image", ""), proof=data.get("proof", ""), status="pending")
    return jsonify(res), (400 if res.get("error") else 200)


@app.route("/api/exchange/moderate", methods=["POST"])
def api_exchange_moderate():
    from brain import exchange, notifications
    if not _is_admin(_current_user()):
        return jsonify({"error": "Admin only."}), 403
    data = request.get_json(silent=True) or {}
    res = exchange.moderate(int(data.get("asset_id", 0)), data.get("action"))
    if res.get("ok") and res.get("created_by"):
        try:
            verb = "approved and is now LIVE" if res["status"] == "listed" else "was not approved"
            notifications.push(res["created_by"], "listing",
                               f"Your listing {res['name']} {verb}", "exchange.html")
        except Exception:
            pass
    return jsonify(res), (400 if res.get("error") else 200)


@app.route("/api/exchange/scout")
def api_exchange_scout():
    from brain import exchange, accounts
    board = exchange.scout_leaderboard(20)
    for r in board:
        u = accounts.get_user(r["user_id"])
        r["name"] = u["name"] if u else "—"
        r["handle"] = (u.get("handle", "") if u else "")
    return jsonify({"scouts": board})


@app.route("/api/bucks/send", methods=["POST"])
def api_bucks_send():
    """Send play-money Bucks to another user (gifting a game score, not money transmission)."""
    from brain import parlor, accounts, notifications
    u = _current_user()
    if not u:
        return jsonify({"error": "Log in first."}), 401
    data = request.get_json(silent=True) or {}
    to_id = int(data.get("to", 0) or 0)
    if not accounts.get_user(to_id):
        return jsonify({"error": "No such user."}), 404
    res = parlor.transfer(u["id"], to_id, data.get("amount"))
    if res.get("ok"):
        try:
            notifications.push(to_id, "bucks",
                               f"{u['name']} sent you {res['amount']} Bucks", "profile.html")
        except Exception:
            pass
    return jsonify(res), (400 if res.get("error") else 200)


# ── PROFILE — bio + all your Midas info in one place ─────────────────────────
@app.route("/profile")
def profile_page():
    return send_from_directory(BASE, "profile.html")


@app.route("/api/profile/bio", methods=["POST"])
def api_profile_bio():
    from brain import accounts
    u = _current_user()
    if not u:
        return jsonify({"error": "Log in first."}), 401
    data = request.get_json(silent=True) or {}
    return jsonify(accounts.set_bio(u["id"], data.get("bio", "")))


@app.route("/api/profile/handle", methods=["POST"])
def api_profile_handle():
    from brain import accounts
    u = _current_user()
    if not u:
        return jsonify({"error": "Log in first."}), 401
    data = request.get_json(silent=True) or {}
    res = accounts.set_handle(u["id"], data.get("handle", ""))
    return jsonify(res), (400 if res.get("error") else 200)


@app.route("/api/handle/<handle>")
def api_handle(handle):
    from brain import accounts
    u = accounts.get_by_handle(handle)
    if not u:
        return jsonify({"error": "No such handle."}), 404
    return jsonify({"id": u["id"], "name": u["name"], "handle": u["handle"]})


# ── DMs ──────────────────────────────────────────────────────────────────────
@app.route("/messages")
def messages_page():
    return send_from_directory(BASE, "messages.html")


@app.route("/welcome")
def welcome_page():
    return send_from_directory(BASE, "welcome.html")


@app.route("/api/dm/inbox")
def api_dm_inbox():
    from brain import messages, accounts
    u = _current_user()
    if not u:
        return jsonify({"error": "Log in to see messages."}), 401
    convos = messages.inbox(u["id"])
    for cv in convos:
        ou = accounts.get_user(cv["other_id"])
        cv["name"] = ou["name"] if ou else "Unknown"
    return jsonify({"conversations": convos})


@app.route("/api/dm/thread/<int:other_id>")
def api_dm_thread(other_id):
    from brain import messages, accounts
    u = _current_user()
    if not u:
        return jsonify({"error": "Log in to see messages."}), 401
    ou = accounts.get_user(other_id)
    return jsonify({"other": {"id": other_id, "name": ou["name"] if ou else "Unknown"},
                    "messages": messages.thread(u["id"], other_id)})


@app.route("/api/dm/send", methods=["POST"])
def api_dm_send():
    from brain import messages, notifications
    u = _current_user()
    if not u:
        return jsonify({"error": "Log in to send messages."}), 401
    data = request.get_json(silent=True) or {}
    res = messages.send(u["id"], data.get("to_id"), data.get("body"))
    if res.get("ok"):
        notifications.push(res["to_id"], "dm", f"{u['name']} sent you a message",
                           f"messages.html?u={u['id']}")
    return jsonify(res), (400 if res.get("error") else 200)


@app.route("/api/dm/block", methods=["POST"])
def api_dm_block():
    from brain import messages
    u = _current_user()
    if not u:
        return jsonify({"error": "Log in first."}), 401
    data = request.get_json(silent=True) or {}
    return jsonify(messages.block(u["id"], data.get("user_id")))


# ── FOLLOWS + DM PRIVACY ─────────────────────────────────────────────────────
@app.route("/api/follow", methods=["POST"])
def api_follow():
    from brain import social
    u = _current_user()
    if not u:
        return jsonify({"error": "Log in first."}), 401
    data = request.get_json(silent=True) or {}
    if (data.get("action") or "follow").lower() == "unfollow":
        res = social.unfollow(u["id"], data.get("user_id"))
    else:
        res = social.follow(u["id"], data.get("user_id"))
    return jsonify(res), (400 if res.get("error") else 200)


@app.route("/api/dm-privacy", methods=["POST"])
def api_dm_privacy():
    from brain import accounts
    u = _current_user()
    if not u:
        return jsonify({"error": "Log in first."}), 401
    data = request.get_json(silent=True) or {}
    return jsonify(accounts.set_dm_privacy(u["id"], data.get("value")))


# ── NOTIFICATIONS ────────────────────────────────────────────────────────────
@app.route("/api/notifications")
def api_notifications():
    from brain import notifications
    u = _current_user()
    if not u:
        return jsonify({"unread": 0, "items": []})
    return jsonify(notifications.listing(u["id"]))


@app.route("/api/notifications/read", methods=["POST"])
def api_notifications_read():
    from brain import notifications
    u = _current_user()
    if not u:
        return jsonify({"error": "Log in first."}), 401
    return jsonify(notifications.mark_read(u["id"]))


@app.route("/api/health")
def api_health():
    """Which API keys the server can see (presence only, never values).
    Confirms whether env vars are set in production."""
    from brain import db
    out = {
        "alpaca_key":    bool(os.getenv("ALPACA_API_KEY")),
        "alpaca_secret": bool(os.getenv("ALPACA_SECRET_KEY")),
        "finnhub":       bool(os.getenv("FINNHUB_API_KEY")),
        "anthropic":     bool(os.getenv("ANTHROPIC_API_KEY")),
        "db_backend":    "postgres" if db._is_pg() else "sqlite",
        "has_database_url": bool(os.getenv("DATABASE_URL")),
    }
    try:
        from brain import accounts
        accounts.init_db()
        with db.get_conn() as c:
            out["users_count"] = c.execute("SELECT COUNT(*) AS n FROM users").fetchone()["n"]
        out["users_ok"] = True
    except Exception as e:
        out["users_ok"] = False
        out["users_error"] = f"{type(e).__name__}: {str(e)[:240]}"
    return jsonify(out)


@app.route("/api/rate", methods=["POST"])
def api_rate():
    """Crowd review: a user rates a source reliable or not. Feeds the trust model.
    Body: {"source": "r/wallstreetbets", "helpful": true}"""
    if not _current_user():
        return jsonify({"error": "Log in to rate sources."}), 401
    data = request.get_json(silent=True) or {}
    source = (data.get("source") or "").strip()
    if not source:
        return jsonify({"error": "source required"}), 400
    try:
        from brain.signal_ledger import SignalLedger
        led = SignalLedger()
        led.record_rating(source, bool(data.get("helpful")))
        return jsonify({"ok": True, "source": source, "trust": led.source_trust(source)})
    except Exception as e:
        _log.warning(f"Rate failed: {e}")
        return jsonify({"error": str(e)}), 500


# ── TRADING (paper mode for safety) ──────────────────────────────────────────
# Single-account demo using the server's Alpaca keys. paper=True = fake money.
# DO NOT expose publicly with real keys / no auth -- anyone could place orders.
from brain.trader import Trader as _Trader


def _trader():
    key = os.getenv("ALPACA_API_KEY")
    sec = os.getenv("ALPACA_SECRET_KEY")
    if not (key and sec):
        return None
    return _Trader(key, sec, paper=True)


@app.route("/api/account")
def api_account():
    t = _trader()
    if t is None:
        return jsonify({"error": "Alpaca keys not set"})
    try:
        s = t.sync_status()
        return jsonify({"portfolio_value": s["portfolio_value"], "cash": s["cash"],
                        "buying_power": s["buying_power"], "positions": s["positions"],
                        "paper": True})
    except Exception as e:
        _log.warning(f"Account failed: {e}")
        return jsonify({"error": str(e)})


def _trading_guard():
    """Trade endpoints run paper-mode on the OPERATOR's keys, so they must never be
    open to the public (anyone could place trades on the account). Off unless
    ENABLE_TRADING=1 and the caller is logged in."""
    if os.getenv("ENABLE_TRADING", "0") != "1":
        return jsonify({"error": "Trading is disabled on this deployment."}), 403
    if not _current_user():
        return jsonify({"error": "Log in to trade."}), 401
    return None


@app.route("/api/buy", methods=["POST"])
def api_buy():
    g = _trading_guard()
    if g:
        return g
    d = request.get_json(silent=True) or {}
    ticker = (d.get("ticker") or "").strip().upper()
    try:
        amount = float(d.get("amount") or 0)
    except (TypeError, ValueError):
        amount = 0
    if not ticker or amount < 1:
        return jsonify({"error": "ticker and amount (>= $1) required"}), 400
    t = _trader()
    if t is None:
        return jsonify({"error": "Alpaca keys not set"}), 500
    try:
        return jsonify(t.buy(ticker, amount))
    except Exception as e:
        _log.warning(f"Buy failed: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/sell", methods=["POST"])
def api_sell():
    g = _trading_guard()
    if g:
        return g
    d = request.get_json(silent=True) or {}
    ticker = (d.get("ticker") or "").strip().upper()
    if not ticker:
        return jsonify({"error": "ticker required"}), 400
    t = _trader()
    if t is None:
        return jsonify({"error": "Alpaca keys not set"}), 500
    try:
        return jsonify(t.sell_all(ticker))
    except Exception as e:
        _log.warning(f"Sell failed: {e}")
        return jsonify({"error": str(e)}), 500


def _prewarm():
    """Warm the whisper + news caches at boot so the first visitor doesn't wait."""
    global _whisper_data, _whisper_ts, _news_data, _news_ts
    try:
        from brain.news_pipeline import run_scan
        _whisper_data = run_scan()
        _whisper_ts = _time.time()
        _log.info("prewarm: whisper cache ready")
    except Exception as e:
        _log.warning(f"prewarm whispers failed: {e}")
    try:
        _news_data = _build_news()
        _news_ts = _time.time()
        _log.info("prewarm: news cache ready")
    except Exception as e:
        _log.warning(f"prewarm news failed: {e}")


def _rep_rescore_loop():
    """Keep cached reputation rows fresh in the background so name hue colors stay
    current without anyone opening a profile. Every ~30 min it recomputes up to
    ~20 already-cached users (oldest first), bounded to respect Alpaca rate limits.
    Fully guarded so it can never take the server down."""
    while True:
        try:
            from brain import reputation
            try:
                uids = reputation.cached_user_ids(stale_only=True, limit=20)
            except Exception as e:
                _log.debug(f"rep rescore: id fetch failed: {e}")
                uids = []
            done = 0
            for uid in uids:
                try:
                    reputation.compute(uid)
                    done += 1
                except Exception as e:
                    _log.debug(f"rep rescore: user {uid} failed: {e}")
                _time.sleep(1)   # gentle on Alpaca between users
            if done:
                _log.info(f"rep rescore: refreshed {done} cached user(s)")
        except Exception as e:
            _log.warning(f"rep rescore loop error: {e}")
        _time.sleep(1800)   # ~30 minutes between cycles


def _move_since(ticker, since_iso):
    """Percent price move for `ticker` from around `since_iso` to the latest close."""
    key, sec = os.getenv("ALPACA_API_KEY"), os.getenv("ALPACA_SECRET_KEY")
    if not (key and sec):
        return None
    try:
        from alpaca.data.historical import StockHistoricalDataClient
        from alpaca.data.requests import StockBarsRequest
        from alpaca.data.timeframe import TimeFrame
        start = _dt.fromisoformat(str(since_iso).replace("Z", "").split("+")[0])
        client = StockHistoricalDataClient(key, sec)
        req = StockBarsRequest(symbol_or_symbols=ticker, timeframe=TimeFrame.Day, start=start)
        data = client.get_stock_bars(req).data.get(ticker, [])
        if len(data) < 2:
            return None
        base, last = float(data[0].close), float(data[-1].close)
        return round((last - base) / base * 100, 2) if base else None
    except Exception:
        return None


def _score_outcomes(max_n=25, min_age_hours=24):
    """Grade aged whispers against the real move so the ledger learns which sources
    were right (their next whispers then carry more confidence). Returns count scored."""
    try:
        from brain.signal_ledger import SignalLedger
        led = SignalLedger()
        scored = 0
        for eid, ticker, first_seen in led.scoreable(min_age_hours=min_age_hours, limit=max_n):
            mv = _move_since(ticker, first_seen)
            if mv is None:
                continue
            led.record_outcome(eid, mv)
            scored += 1
        if scored:
            _log.info(f"outcome loop: scored {scored} past whisper(s)")
        return scored
    except Exception as e:
        _log.warning(f"outcome loop error: {e}")
        return 0


def _score_outcomes_loop():
    while True:
        try:
            _score_outcomes()
        except Exception:
            pass
        _time.sleep(3600)   # hourly — outcomes don't change fast


@app.route("/api/score-outcomes", methods=["POST"])
def api_score_outcomes():
    """Manual trigger for the confidence outcome-loop (also runs hourly in the background)."""
    return jsonify({"scored": _score_outcomes(min_age_hours=float(request.args.get("min_age", 24)))})


def _notify_bet_settled(res):
    """Ping each bettor when their Parlor market settles (skip voids/refunds)."""
    if not res or res.get("voided"):
        return
    try:
        from brain import notifications
        q = (res.get("question") or "your market")[:64]
        for b in res.get("bettors", []):
            d = b.get("delta", 0)
            verb = "won" if d > 0 else ("lost" if d < 0 else "pushed")
            sign = "+" if d >= 0 else ""
            notifications.push(b["user_id"], "parlor",
                               f"Your bet on “{q}” {verb}: {sign}{d} ₿", "parlor.html")
    except Exception:
        pass


def _parlor_autoresolve():
    """Settle price-backed Parlor markets whose close time has passed, off the Alpaca
    feed. Graceful: no key / no data -> the market just waits for the house."""
    from brain import parlor
    done = 0
    for m in parlor.markets_due():
        try:
            closes = _alpaca_closes(m["ticker"], days=15)
            if closes is None or len(closes) < 2:
                continue
            closes2 = None
            if m.get("rule") == "beats" and m.get("ticker2"):
                c2 = _alpaca_closes(m["ticker2"], days=15)
                if c2 is None or len(c2) < 2:
                    continue            # need both legs to call the race
                closes2 = list(c2)
            outcome = parlor.eval_rule(m["rule"], list(closes),
                                       threshold=m.get("threshold"), closes2=closes2)
            if outcome in ("yes", "no"):
                _notify_bet_settled(parlor.resolve_market(m["id"], outcome))
                done += 1
        except Exception as e:
            _log.warning(f"Parlor auto-resolve {m.get('id')}: {e}")
    return done


def _parlor_autoresolve_loop():
    while True:
        try:
            _parlor_autoresolve()
        except Exception:
            pass
        _time.sleep(900)   # every 15 min — settle markets past their bell


@app.route("/api/parlor/autoresolve", methods=["POST"])
def api_parlor_autoresolve():
    """Admin trigger for the Parlor price-settler (also runs every 15 min)."""
    if not _is_admin(_current_user()):
        return jsonify({"error": "Admin only."}), 403
    return jsonify({"resolved": _parlor_autoresolve()})


try:
    _threading.Thread(target=_prewarm, daemon=True).start()
except Exception as _e:
    pass


try:
    _threading.Thread(target=_rep_rescore_loop, daemon=True).start()
except Exception as _e:
    pass


try:
    _threading.Thread(target=_score_outcomes_loop, daemon=True).start()
except Exception as _e:
    pass


try:
    _threading.Thread(target=_parlor_autoresolve_loop, daemon=True).start()
except Exception as _e:
    pass


if __name__ == "__main__":
    print("\n  MIDAS Simulation Server")
    print("  -----------------------------")
    print("  Open:  http://localhost:5050\n")
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "5050")), debug=False)
