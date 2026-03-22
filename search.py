"""
Midas Search — standalone signal scanner (no API key needed, uses yfinance)

Usage:
  py search.py AAPL                   — quick signal for one ticker
  py search.py AAPL MSFT NVDA         — multi-ticker signal check
  py search.py --screen               — scan 30 popular tickers, rank by confidence
  py search.py --screen --top 10      — show top 10 from screener
  py search.py --history              — full trade history
  py search.py --history AAPL         — trades for a specific ticker
  py search.py --news AAPL            — live YouTube news coverage for a ticker
"""

import os
import sys
import json
from pathlib import Path
from datetime import datetime

import pandas as pd
import yfinance as yf
from dotenv import load_dotenv
from brain.indicators import ema_crossover, rsi as calc_rsi
from brain.youtube import YouTubeTracker

load_dotenv()

# ─────────────────────────────────────────────
# SCREENER LIST
# Tickers scanned in --screen mode (no API key needed)
# ─────────────────────────────────────────────

SCREENER_LIST = [
    "AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "TSLA", "SPY", "QQQ",
    "AMD",  "INTC", "NFLX", "JPM",  "BAC",   "V",    "MA",   "UNH", "JNJ",
    "XOM",  "CVX",  "WMT",  "HD",   "PG",    "KO",   "DIS",  "CRM", "ORCL",
    "ADBE", "PYPL", "COIN",
]

STATE_FILE = "fund_state.json"
EMA_FAST   = 12
EMA_SLOW   = 26


# ─────────────────────────────────────────────
# Signal fetch (yfinance — no API key)
# ─────────────────────────────────────────────

def fetch(ticker: str) -> dict:
    df = yf.download(ticker, period="60d", interval="1d", auto_adjust=True, progress=False)
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    if df.empty or len(df) < 30:
        return None

    prices  = df["Close"].squeeze()
    current = float(prices.iloc[-1])
    prev    = float(prices.iloc[-2])
    change  = (current - prev) / prev * 100

    trend       = ema_crossover(prices, fast=EMA_FAST, slow=EMA_SLOW)
    rsi_series  = calc_rsi(prices)
    current_rsi = float(rsi_series.iloc[-1])

    rsi_conf    = max(0.0, min(100.0, (65.0 - current_rsi) / 35.0 * 100.0))
    spread_pct  = max(0.0, (trend["fast_ema"] - trend["slow_ema"]) / trend["slow_ema"] * 100.0) if trend["slow_ema"] > 0 else 0.0
    spread_conf = min(100.0, spread_pct * 50.0)
    confidence  = round(rsi_conf * 0.5 + spread_conf * 0.5, 1)

    bullish = trend["bullish_crossover"] and current_rsi <= 60
    bearish = trend["bearish_crossunder"] or current_rsi > 70
    signal  = "BUY" if bullish else "SELL" if bearish else "HOLD"

    return {
        "ticker":     ticker.upper(),
        "price":      current,
        "change":     change,
        "trend":      trend["trend"],
        "rsi":        round(current_rsi, 1),
        "confidence": confidence,
        "signal":     signal,
    }


# ─────────────────────────────────────────────
# Display helpers
# ─────────────────────────────────────────────

W = 68


def bar(score: float, width: int = 10) -> str:
    filled = round(score / 100 * width)
    return "█" * filled + "░" * (width - filled)


def print_signal_header(title: str):
    print()
    print(f"  ┌{'─'*W}┐")
    print(f"  │  {title:<{W-2}}│")
    print(f"  ├{'─'*W}┤")
    print(f"  │  {'Ticker':<7}  {'Price':>9}  {'Chg':>8}  {'T':^3}  {'RSI':>5}  {'Signal':<4}  {'Confidence':<21}│")
    print(f"  ├{'─'*W}┤")


def print_signal_row(d: dict):
    arrow   = "▲" if d["change"] >= 0 else "▼"
    chg_str = f"{arrow}{abs(d['change']):>5.2f}%"
    trend   = "↑" if d["trend"] == "up" else "↓"
    sig     = d["signal"]
    cbar    = bar(d["confidence"])
    print(
        f"  │  {d['ticker']:<7}  ${d['price']:>8.2f}  {chg_str:>8}  {trend:^3}  "
        f"{d['rsi']:>5.1f}  {sig:<4}  {d['confidence']:>4.0f}%  {cbar} │"
    )


def print_signal_footer():
    print(f"  └{'─'*W}┘")
    print()


# ─────────────────────────────────────────────
# Commands
# ─────────────────────────────────────────────

def cmd_lookup(tickers: list):
    """Quick signal check for one or more tickers."""
    title = f"Midas Lookup — {datetime.now().strftime('%Y-%m-%d  %H:%M:%S')}"
    print_signal_header(title)
    results = []
    for ticker in tickers:
        try:
            d = fetch(ticker.upper())
            if d:
                results.append(d)
            else:
                print(f"  │  {ticker.upper():<7}  No data or insufficient history{'':>33}│")
        except Exception as e:
            print(f"  │  {ticker.upper():<7}  Error: {str(e)[:48]:<48}│")
    results.sort(key=lambda x: x["confidence"], reverse=True)
    for d in results:
        print_signal_row(d)
    print_signal_footer()


def cmd_screen(top: int = None):
    """Scan the screener list and rank all tickers by confidence."""
    n = top or len(SCREENER_LIST)
    print(f"\n  Scanning {len(SCREENER_LIST)} tickers — please wait...\n")
    results = []
    for ticker in SCREENER_LIST:
        try:
            d = fetch(ticker)
            if d:
                results.append(d)
        except Exception:
            pass
    results.sort(key=lambda x: x["confidence"], reverse=True)
    results = results[:n]
    title = f"Midas Screener — Top {len(results)} by Confidence  ({datetime.now().strftime('%Y-%m-%d  %H:%M')})"
    print_signal_header(title)
    for d in results:
        print_signal_row(d)
    print_signal_footer()


def cmd_history(ticker_filter: str = None):
    """Show trade history from fund_state.json."""
    state_path = Path(STATE_FILE)
    if not state_path.exists():
        print("\n  No trade history found (fund_state.json not found — run py main.py first).\n")
        return

    with open(state_path) as f:
        state = json.load(f)

    trade_log = state.get("trade_log", [])
    if not trade_log:
        print("\n  No trades recorded yet.\n")
        return

    if ticker_filter:
        trade_log = [t for t in trade_log if t.get("ticker", "").upper() == ticker_filter.upper()]
        if not trade_log:
            print(f"\n  No trades found for {ticker_filter.upper()}.\n")
            return

    HW = 66
    print()
    print(f"  ┌{'─'*HW}┐")
    hist_hdr = f"Trade History{f'  —  {ticker_filter.upper()}' if ticker_filter else ''}"
    print(f"  │  {hist_hdr:<{HW-2}}│")
    print(f"  ├{'─'*HW}┤")
    print(f"  │  {'Date':<11}  {'Ticker':<6}  {'Action':<4}  {'Amount':>10}  {'Gain / Loss':>11}  {'Note':<12}│")
    print(f"  ├{'─'*HW}┤")

    for t in reversed(trade_log):
        if t.get("gain") is not None:
            sign = "+" if t["gain"] >= 0 else ""
            gain_str = f"${sign}{t['gain']:.2f}"
        else:
            gain_str = "—"
        note = (t.get("notes") or "")[:12]
        print(
            f"  │  {t['date']:<11}  {t['ticker']:<6}  {t['action']:<4}  "
            f"${t['amount']:>9.2f}  {gain_str:>11}  {note:<12}│"
        )

    total_gain = sum(t.get("gain") or 0 for t in trade_log)
    buys  = sum(1 for t in trade_log if t["action"] == "BUY")
    sells = sum(1 for t in trade_log if t["action"] == "SELL")
    sign  = "+" if total_gain >= 0 else ""
    print(f"  ├{'─'*HW}┤")
    summary = f"{buys} buys  ·  {sells} sells  ·  total  ${sign}{total_gain:.2f}"
    print(f"  │  {summary:<{HW-2}}│")
    print(f"  └{'─'*HW}┘")
    print()

    # ── Current trends for tickers in this history ──
    unique_tickers = list(dict.fromkeys(
        t["ticker"].upper() for t in trade_log if t.get("ticker")
    ))
    if unique_tickers:
        print(f"  Fetching current trends for {', '.join(unique_tickers)}...")
        results = []
        for ticker in unique_tickers:
            try:
                d = fetch(ticker)
                if d:
                    results.append(d)
            except Exception:
                pass
        if results:
            results.sort(key=lambda x: x["confidence"], reverse=True)
            print_signal_header("Current Trends — Tickers in Your History")
            for d in results:
                print_signal_row(d)
            print_signal_footer()


def cmd_news(tickers: list):
    """Show live YouTube news coverage for one or more tickers."""
    api_key = os.getenv("YOUTUBE_API_KEY")
    if not api_key:
        print()
        print("  YouTube API key not set.")
        print("  Add YOUTUBE_API_KEY to your .env file or run py setup.py -> [2] API Keys.")
        print()
        return

    tracker = YouTubeTracker(api_key)
    NW = 68

    for ticker in tickers:
        ticker = ticker.upper()
        print(f"\n  Fetching YouTube coverage for {ticker}...")
        result = tracker.get_signal(ticker)
        feed   = result.get("feed", [])

        print()
        print(f"  ┌{'─'*NW}┐")

        tone_label = {"positive": "mostly bullish", "negative": "mostly bearish"}.get(
            result["signal"], "mixed / neutral"
        )
        header_str = f"{ticker}  —  YouTube News  ·  {tone_label}  ·  {result['videos']} videos scanned"
        print(f"  │  {header_str:<{NW-2}}│")
        print(f"  ├{'─'*NW}┤")

        if not feed:
            print(f"  │  {'No recent videos found.':<{NW-2}}│")
        else:
            for v in feed:
                # Tone marker
                dot = "▲" if v["tone"] == "bullish" else "▼" if v["tone"] == "bearish" else "·"

                # Title (truncated to fit)
                title = v["title"]
                if len(title) > NW - 6:
                    title = title[:NW - 9] + "..."
                print(f"  │  {dot} {title:<{NW-4}}│")

                meta = f"    {v['channel']}  ·  {v['ago']}"
                print(f"  │  {meta:<{NW-2}}│")

                if v["url"]:
                    url_str = f"    {v['url']}"
                    print(f"  │  {url_str:<{NW-2}}│")

                print(f"  │  {'':<{NW-2}}│")

        print(f"  └{'─'*NW}┘")
        print()


# ─────────────────────────────────────────────

def _usage():
    print()
    print("  Midas Search")
    print()
    print("  py search.py AAPL                   — signal lookup")
    print("  py search.py AAPL MSFT NVDA         — multi-ticker lookup")
    print("  py search.py --screen               — scan screener list")
    print("  py search.py --screen --top 10      — top 10 from screener")
    print("  py search.py --history              — full trade history")
    print("  py search.py --history AAPL         — trades for one ticker")
    print("  py search.py --news AAPL            — live YouTube news")
    print("  py search.py --news AAPL MSFT       — news for multiple tickers")
    print()


if __name__ == "__main__":
    args = sys.argv[1:]

    if not args:
        _usage()
        sys.exit(0)

    if "--news" in args:
        rest = [a for a in args if a != "--news"]
        if not rest:
            print("\n  Usage: py search.py --news AAPL\n")
        else:
            cmd_news(rest)

    elif "--history" in args:
        rest = [a for a in args if a not in ("--history",)]
        cmd_history(rest[0] if rest else None)

    elif "--screen" in args:
        top = None
        if "--top" in args:
            idx = args.index("--top")
            if idx + 1 < len(args) and args[idx + 1].isdigit():
                top = int(args[idx + 1])
        cmd_screen(top)

    else:
        cmd_lookup(args)
