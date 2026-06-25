"""
MIDAS — Simulation Game Server

Run:  py sim_server.py
Open: http://localhost:5050
"""
import os, sys, io, contextlib

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    from flask import Flask, jsonify, request, send_from_directory
except ImportError:
    print("\n  Flask not installed. Run:  pip install flask\n")
    sys.exit(1)

from brain.backtest import Backtest

app  = Flask(__name__)
BASE = os.path.dirname(os.path.abspath(__file__))


@app.route("/")
def index():
    return send_from_directory(BASE, "index.html")

@app.route("/<path:filename>")
def static_files(filename):
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


def _alpaca_closes(ticker, days=370):
    """Daily closes for the past `days` from Alpaca (replaces dead yfinance).
    Returns a pandas Series of floats, or None if unavailable / no keys set."""
    key = os.getenv("ALPACA_API_KEY")
    sec = os.getenv("ALPACA_SECRET_KEY")
    if not (key and sec):
        _log.warning("Live data off — set ALPACA_API_KEY/SECRET in .env.")
        return None
    try:
        client = _StockClient(key, sec)
        req = _BarsReq(symbol_or_symbols=ticker, timeframe=_TF.Day,
                       start=_dt.now() - _timedelta(days=days), end=_dt.now())
        df = getattr(client.get_stock_bars(req), "df", None)
    except Exception as e:
        _log.debug(f"Alpaca closes {ticker}: {e}")
        return None
    if df is None or df.empty:
        return None
    df = df.reset_index()
    if "symbol" in df.columns:
        df = df[df["symbol"] == ticker]
    if df.empty or "close" not in df.columns:
        return None
    return _pd.Series(df["close"].astype(float).values).dropna()


@app.after_request
def _add_cors(response):
    response.headers["Access-Control-Allow-Origin"] = "*"
    return response


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
]

_news_data = None
_news_ts   = 0.0
_news_lock = _threading.Lock()

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
                "source":  name,
                "region":  region,
                "title":   title,
                "link":    link,
                "pubdate": pubdate,
                "desc":    desc,
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
    return jsonify(_whisper_data)


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


@app.route("/api/rate", methods=["POST"])
def api_rate():
    """Crowd review: a user rates a source reliable or not. Feeds the trust model.
    Body: {"source": "r/wallstreetbets", "helpful": true}"""
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


if __name__ == "__main__":
    print("\n  MIDAS Simulation Server")
    print("  -----------------------------")
    print("  Open:  http://localhost:5050\n")
    app.run(host="0.0.0.0", port=5050, debug=False)
