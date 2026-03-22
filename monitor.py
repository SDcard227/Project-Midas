import os
import sys
import time
import pandas as pd
import yfinance as yf
from datetime import datetime
from zoneinfo import ZoneInfo
from brain.indicators import ema_crossover, rsi as calc_rsi

# ─────────────────────────────────────────────
# WORLD CLOCKS
# Format: (City, timezone, market_open, market_close)
# market_open/close in local 24h time, None = no major exchange
# ─────────────────────────────────────────────

WORLD_CLOCKS = [
    ("Chicago",   "America/Chicago",    "8:30",  "15:00"),
    ("New York",  "America/New_York",   "9:30",  "16:00"),
    ("London",    "Europe/London",      "8:00",  "16:30"),
    ("Frankfurt", "Europe/Berlin",      "9:00",  "17:30"),
    ("Dubai",     "Asia/Dubai",         "10:00", "14:00"),
    ("Tokyo",     "Asia/Tokyo",         "9:00",  "15:30"),
    ("Hong Kong", "Asia/Hong_Kong",     "9:30",  "16:00"),
    ("Sydney",    "Australia/Sydney",   "10:00", "16:00"),
    ("LA",        "America/Los_Angeles","6:30",  "13:00"),
]


def market_status(open_str: str, close_str: str, local_now: datetime) -> bool:
    """Returns True if market is currently open."""
    def to_minutes(t: str) -> int:
        h, m = t.split(":")
        return int(h) * 60 + int(m)
    if local_now.weekday() >= 5:
        return False
    now_min   = local_now.hour * 60 + local_now.minute
    return to_minutes(open_str) <= now_min < to_minutes(close_str)


def world_clock_lines() -> list:
    """Build world clock display lines, 3 cities per row."""
    entries = []
    for city, tz, mopen, mclose in WORLD_CLOCKS:
        local  = datetime.now(ZoneInfo(tz))
        is_open = market_status(mopen, mclose, local)
        dot    = "●" if is_open else "○"
        entries.append(f"{dot} {city:<10} {local.strftime('%H:%M')}")

    # 3 per row
    lines = []
    for i in range(0, len(entries), 3):
        row  = entries[i:i+3]
        line = "  │  " + "    ".join(f"{e:<18}" for e in row)
        line = line.ljust(73) + "│"
        lines.append(line)
    return lines

# ─────────────────────────────────────────────
# MONITOR SETTINGS
# ─────────────────────────────────────────────

WATCHLIST        = ["AAPL", "MSFT", "NVDA", "SPY"]
REFRESH_INTERVAL = 60    # seconds between refreshes
EMA_FAST         = 12
EMA_SLOW         = 26

# ─────────────────────────────────────────────


def fetch(ticker: str) -> dict:
    """Download recent history and compute indicators for one ticker."""
    df = yf.download(ticker, period="60d", interval="1d", auto_adjust=True, progress=False)
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    if df.empty or len(df) < 30:
        return None

    prices = df["Close"].squeeze()
    current = float(prices.iloc[-1])
    prev    = float(prices.iloc[-2])
    change  = (current - prev) / prev * 100

    trend      = ema_crossover(prices, fast=EMA_FAST, slow=EMA_SLOW)
    rsi_series = calc_rsi(prices)
    current_rsi = float(rsi_series.iloc[-1])

    # Confidence — same formula as backtest (no sentiment layer in monitor)
    rsi_conf    = max(0.0, min(100.0, (65.0 - current_rsi) / 35.0 * 100.0))
    spread_pct  = max(0.0, (trend["fast_ema"] - trend["slow_ema"]) / trend["slow_ema"] * 100.0) if trend["slow_ema"] > 0 else 0.0
    spread_conf = min(100.0, spread_pct * 50.0)
    confidence  = round(rsi_conf * 0.5 + spread_conf * 0.5, 1)

    # Signal
    bullish = trend["bullish_crossover"] and current_rsi <= 60
    bearish = trend["bearish_crossunder"] or current_rsi > 70
    signal  = "BUY" if bullish else "SELL" if bearish else "HOLD"

    return {
        "ticker":     ticker,
        "price":      current,
        "change":     change,
        "trend":      trend["trend"],
        "rsi":        round(current_rsi, 1),
        "confidence": confidence,
        "signal":     signal,
    }


def bar(score: float, width: int = 10) -> str:
    filled = round(score / 100 * width)
    return "█" * filled + "░" * (width - filled)


def run():
    # py monitor.py AAPL TSLA          — custom watchlist
    # py monitor.py --top 5            — top N by confidence
    args = sys.argv[1:]
    top = None
    filtered_args = []
    i = 0
    while i < len(args):
        if args[i] == "--top" and i + 1 < len(args) and args[i + 1].isdigit():
            top = int(args[i + 1])
            i += 2
        else:
            filtered_args.append(args[i])
            i += 1
    watchlist = filtered_args if filtered_args else WATCHLIST

    W = 68
    while True:
        os.system("cls" if os.name == "nt" else "clear")
        now = datetime.now().strftime("%Y-%m-%d  %H:%M:%S")

        print()
        print(f"  ┌{'─'*W}┐")
        print(f"  │  MIDAS  ·  Market Monitor{' ' * (W - 28 - len(now))}{now}  │")
        print(f"  ├{'─'*W}┤")
        print(f"  │  {'● open  ○ closed':^{W-2}}│")
        for line in world_clock_lines():
            print(line)
        print(f"  ├{'─'*W}┤")
        print(f"  │  {'Ticker':<7}  {'Price':>9}  {'Chg':>8}  {'T':^3}  {'RSI':>5}  {'Signal':<4}  {'Confidence':<21}│")
        print(f"  ├{'─'*W}┤")

        results = []
        for ticker in watchlist:
            try:
                data = fetch(ticker)
                if data:
                    results.append(data)
            except Exception as e:
                results.append({"ticker": ticker, "error": str(e)})

        results.sort(key=lambda x: x.get("confidence", -1), reverse=True)
        if top:
            results = results[:top]

        for d in results:
            if "error" in d:
                print(f"  │  {d['ticker']:<7}  Error: {d['error'][:50]:<50} │")
                continue

            arrow   = "▲" if d["change"] >= 0 else "▼"
            chg_str = f"{arrow}{abs(d['change']):>5.2f}%"
            trend   = "↑" if d["trend"] == "up" else "↓"
            sig     = d["signal"]
            cbar    = bar(d["confidence"])

            print(
                f"  │  {d['ticker']:<7}  ${d['price']:>8.2f}  {chg_str:>8}  {trend:^3}  "
                f"{d['rsi']:>5.1f}  {sig:<4}  {d['confidence']:>4.0f}%  {cbar} │"
            )

        footer = f"  Refresh: {REFRESH_INTERVAL}s  ·  Ctrl+C to exit"
        if top:
            footer += f"  ·  Top {top} shown"
        print(f"  ├{'─'*W}┤")
        print(f"  │{footer:<{W}}│")
        print(f"  └{'─'*W}┘")
        print()

        time.sleep(REFRESH_INTERVAL)


if __name__ == "__main__":
    try:
        run()
    except KeyboardInterrupt:
        print("\n  Monitor stopped.")
