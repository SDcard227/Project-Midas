"""
Project Midas — Setup Wizard

Configure all settings without editing any Python files.

Usage: py setup.py
"""

import os
import re
import sys
import json
from pathlib import Path

MAIN_FILE = Path("main.py")
ENV_FILE  = Path(".env")


# ─────────────────────────────────────────────
# Display helpers
# ─────────────────────────────────────────────

def clear():
    os.system("cls" if os.name == "nt" else "clear")


def header(title: str):
    W = 58
    print()
    print(f"  {'═' * W}")
    print(f"   Project Midas — {title}")
    print(f"  {'═' * W}")
    print()


def section(title: str):
    print()
    print(f"  ── {title} {'─' * (52 - len(title))}")
    print()


def ask(prompt: str, current, validator=None, options: list = None) -> object:
    """
    Prompt user for a value.
    Press Enter to keep the current value.
    """
    if options:
        opts_str = f"  [{'/'.join(options)}]"
    else:
        opts_str = ""

    if isinstance(current, str):
        current_display = f'"{current}"'
    elif current is None:
        current_display = "None"
    else:
        current_display = str(current)

    while True:
        raw = input(f"  {prompt}{opts_str}  (now: {current_display}): ").strip()

        if raw == "":
            return current  # keep current

        if options:
            match = next((o for o in options if o.upper() == raw.upper()), None)
            if match is None:
                print(f"  Please choose one of: {', '.join(options)}")
                continue
            return match

        if validator:
            try:
                return validator(raw)
            except Exception as e:
                print(f"  Invalid value — {e}")
                continue

        return raw


def confirm(msg: str) -> bool:
    return input(f"\n  {msg} [y/n]: ").strip().lower() == "y"


def pause():
    input("\n  Press Enter to go back...")


# ─────────────────────────────────────────────
# .env read / write
# ─────────────────────────────────────────────

def read_env() -> dict:
    env = {}
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text().splitlines():
            line = line.strip()
            if line and "=" in line and not line.startswith("#"):
                k, _, v = line.partition("=")
                env[k.strip()] = v.strip()
    return env


def write_env(env: dict):
    lines = [f"{k}={v}" for k, v in env.items() if v]
    ENV_FILE.write_text("\n".join(lines) + "\n")


# ─────────────────────────────────────────────
# main.py read / write helpers
# ─────────────────────────────────────────────

def read_var(content: str, name: str):
    """Read a scalar variable from main.py content."""
    m = re.search(rf'^{name}\s*=\s*(.+?)(?:\s*#.*)?$', content, re.MULTILINE)
    if not m:
        return None
    raw = m.group(1).strip()
    if raw == "None":   return None
    if raw == "True":   return True
    if raw == "False":  return False
    if (raw.startswith('"') and raw.endswith('"')) or \
       (raw.startswith("'") and raw.endswith("'")):
        return raw[1:-1]
    try:
        return float(raw) if "." in raw else int(raw)
    except ValueError:
        return raw


def write_var(content: str, name: str, value) -> str:
    """Write a scalar variable back into main.py content."""
    if value is None:
        py_val = "None"
    elif isinstance(value, bool):
        py_val = "True" if value else "False"
    elif isinstance(value, str):
        py_val = f'"{value}"'
    elif isinstance(value, float):
        py_val = f"{value}"
    else:
        py_val = str(value)
    # Replace value while preserving any trailing inline comment
    pattern = rf'^({name}\s*=\s*)(.+?)(\s*(?:#.*)?)$'
    return re.sub(pattern, rf'\g<1>{py_val}\3', content, flags=re.MULTILINE)


def read_watchlist(content: str) -> list:
    m = re.search(r'WATCHLIST\s*=\s*\[(.*?)\]', content, re.DOTALL)
    if not m:
        return []
    return re.findall(r'"([A-Z0-9.]+)"', m.group(1))


def write_watchlist(content: str, tickers: list) -> str:
    lines = ",\n".join(f'    "{t}"' for t in tickers)
    new_block = f"WATCHLIST = [\n{lines},\n]"
    return re.sub(r'WATCHLIST\s*=\s*\[.*?\]', new_block, content, flags=re.DOTALL)


# ─────────────────────────────────────────────
# Setup sections
# ─────────────────────────────────────────────

def setup_api_keys(env: dict) -> dict:
    header("API Keys")
    print("  Get your free keys from:")
    print()
    print("    Alpaca   — alpaca.markets          (paper trading, no real money needed)")
    print("    Finnhub  — finnhub.io              (free tier, no credit card)")
    print("    YouTube  — console.cloud.google.com (optional, Layer 5)")
    print("               New Project → Enable YouTube Data API v3 → Create API Key")
    print("    Twilio   — twilio.com              (optional, SMS alerts + 2FA)")
    print()
    print("  Press Enter to keep the current value.")
    print()

    for key, label in [
        ("ALPACA_API_KEY",      "Alpaca API Key      "),
        ("ALPACA_SECRET_KEY",   "Alpaca Secret Key   "),
        ("FINNHUB_API_KEY",     "Finnhub API Key     "),
        ("YOUTUBE_API_KEY",     "YouTube API Key     "),
        ("TWILIO_ACCOUNT_SID",  "Twilio Account SID  "),
        ("TWILIO_AUTH_TOKEN",   "Twilio Auth Token   "),
        ("TWILIO_FROM_NUMBER",  "Twilio From Number  "),
        ("TWILIO_TO_NUMBER",    "Your Phone Number   "),
    ]:
        current = env.get(key, "")
        if len(current) > 6:
            display = "*" * (len(current) - 4) + current[-4:]
        else:
            display = current or "not set"
        raw = input(f"  {label} (now: {display}): ").strip()
        if raw:
            env[key] = raw

    print()
    missing = [k for k in ("ALPACA_API_KEY", "ALPACA_SECRET_KEY", "FINNHUB_API_KEY") if not env.get(k)]
    if not env.get("YOUTUBE_API_KEY"):
        print("  Note: YOUTUBE_API_KEY not set — Layer 5 (YouTube news) will be disabled.")
    if missing:
        print(f"  Note: {', '.join(missing)} still not set — Midas won't start until all three are filled in.")
    else:
        print("  All three API keys are set.")
    pause()
    return env


def setup_fund(content: str) -> str:
    header("Fund Settings")
    print("  Control how much money Midas manages and how aggressively it invests.")
    print()

    for name, prompt, typ, hint in [
        ("STARTING_BALANCE", "Total fund amount ($)",                     float, "e.g. 1000"),
        ("FLOOR_AMOUNT",     "Floor — stop trading if fund drops to ($)", float, "e.g. 800"),
        ("ENTRY_POINT",      "Min cash needed before deploying ($)",       float, "e.g. 100"),
        ("DEPLOY_FRACTION",  "Fraction to deploy per trade (0.10 = 10%)", float, "e.g. 0.10"),
    ]:
        current = read_var(content, name)
        print(f"  {hint}")
        val = ask(prompt, current, validator=typ)
        content = write_var(content, name, val)
        print()

    pause()
    return content


def setup_trading(content: str) -> str:
    header("Trading Style & Mode")

    section("Trading Style")
    print("  SWING — daily bars, holds positions for days or weeks  (recommended)")
    print("  DAY   — 5-minute bars, all positions close before market close each day")
    print("          (set CYCLE_INTERVAL = 60 when using DAY mode)")
    print()
    current = read_var(content, "TRADING_STYLE")
    val = ask("Trading style", current, options=["SWING", "DAY"])
    content = write_var(content, "TRADING_STYLE", val)

    section("Trading Mode")
    print("  CLIMB   — sell on EMA crossunder or RSI overbought  (balanced default)")
    print("  PLATEAU — also exits when price goes sideways before a drop")
    print("  SURGE   — holds longer, only exits when the peak is breaking down")
    print()
    current = read_var(content, "TRADING_MODE")
    val = ask("Trading mode", current, options=["CLIMB", "PLATEAU", "SURGE"])
    content = write_var(content, "TRADING_MODE", val)

    section("Trade Frequency")
    print("  NORMAL     — EMA crossovers only (fewer, higher-confidence trades)")
    print("  ACTIVE     — crossovers + RSI dip buying")
    print("  AGGRESSIVE — rides full EMA trend + RSI dips (most trades)")
    print()
    current = read_var(content, "TRADE_FREQUENCY")
    val = ask("Trade frequency", current, options=["NORMAL", "ACTIVE", "AGGRESSIVE"])
    content = write_var(content, "TRADE_FREQUENCY", val)

    section("Cycle Interval")
    print("  How often Midas checks the market (seconds).")
    print("  SWING: 300 recommended  |  DAY: 60 recommended")
    print()
    current = read_var(content, "CYCLE_INTERVAL")
    val = ask("Cycle interval (seconds)", current, validator=int)
    content = write_var(content, "CYCLE_INTERVAL", val)

    pause()
    return content


def setup_floor(content: str) -> str:
    header("Floor Protection Mode")
    print("  FIXED     — tripwire. Midas stops if fund drops to your floor amount.")
    print("  TRAILING  — floor rises as fund grows. Automatically locks in gains.")
    print("              (e.g. fund hits $1500 → floor rises to $1275 at 15%)")
    print("  LOCKED    — floor amount is kept in cash forever, never invested.")
    print("              Midas only trades with capital above the floor.")
    print("  PRINCIPAL — your starting balance is locked in cash forever.")
    print("              Midas only ever trades with profits. Zero risk to original capital.")
    print()
    current = read_var(content, "FLOOR_MODE")
    val = ask("Floor mode", current, options=["FIXED", "TRAILING", "LOCKED", "PRINCIPAL"])
    content = write_var(content, "FLOOR_MODE", val)

    if val == "TRAILING":
        print()
        print("  Trailing floor % — how far below the peak the floor sits.")
        print("  0.15 means the floor is always 15% below your highest fund value.")
        current_pct = read_var(content, "TRAILING_FLOOR_PCT")
        pct = ask("Trailing floor % below peak", current_pct, validator=float)
        content = write_var(content, "TRAILING_FLOOR_PCT", pct)

    pause()
    return content


def setup_watchlist(content: str) -> str:
    header("Watchlist")
    tickers = read_watchlist(content)
    print(f"  Current: {', '.join(tickers) if tickers else '(empty)'}")
    print()
    print("  [a] Add a ticker")
    print("  [r] Remove a ticker")
    print("  [c] Clear all and start fresh")
    print("  [s] Skip (keep as-is)")
    print()

    while True:
        choice = input("  Choose [a/r/c/s]: ").strip().lower()

        if choice == "s":
            break
        elif choice == "a":
            raw = input("  Ticker to add (e.g. TSLA): ").strip().upper()
            if raw and raw not in tickers:
                tickers.append(raw)
                print(f"  Added {raw}.")
            elif raw in tickers:
                print(f"  {raw} is already in the watchlist.")
        elif choice == "r":
            raw = input("  Ticker to remove: ").strip().upper()
            if raw in tickers:
                tickers.remove(raw)
                print(f"  Removed {raw}.")
            else:
                print(f"  {raw} not found in watchlist.")
        elif choice == "c":
            tickers = []
            print("  Watchlist cleared.")
        else:
            print("  Use a / r / c / s.")

        print(f"  Current: {', '.join(tickers) if tickers else '(empty)'}")
        print()
        if input("  Make another change? [y/n]: ").strip().lower() != "y":
            break

    content = write_watchlist(content, tickers)
    print(f"\n  Final watchlist: {', '.join(tickers)}")
    pause()
    return content


def setup_paper(content: str) -> str:
    header("Paper vs Live Trading")
    print("  PAPER — fake money, real market data. Safe for testing.")
    print("  LIVE  — real money. Only switch when you are ready and have tested in paper mode.")
    print()
    current = read_var(content, "PAPER_TRADING")
    current_label = "PAPER" if current else "LIVE"
    choice = ask("Trading mode", current_label, options=["PAPER", "LIVE"])
    content = write_var(content, "PAPER_TRADING", choice == "PAPER")

    if choice == "LIVE":
        print()
        print("  *** WARNING: LIVE mode uses real money. ***")
        print("      Make sure you have run paper trading first and are comfortable")
        print("      with the risks before switching to live.")
    pause()
    return content


def setup_notifications(content: str) -> str:
    header("Email Notifications")
    print("  Midas can email you when the floor is hit and all positions are closed.")
    print("  Uses Gmail — requires a Gmail App Password (not your regular password).")
    print()
    print("  How to get a Gmail App Password:")
    print("    Google Account → Security → 2-Step Verification → App Passwords")
    print()
    print("  Leave the email blank to disable notifications.")
    print()

    current_email = read_var(content, "NOTIFY_EMAIL")
    email = input(f"  Gmail address (now: {current_email or 'disabled'}): ").strip()

    if email:
        content = write_var(content, "NOTIFY_EMAIL", email)
        current_pw = read_var(content, "NOTIFY_EMAIL_PASSWORD")
        pw_display = "set" if current_pw else "not set"
        pw = input(f"  Gmail App Password (now: {pw_display}, press Enter to keep): ").strip()
        if pw:
            content = write_var(content, "NOTIFY_EMAIL_PASSWORD", pw)
        print("  Notifications enabled.")
    elif current_email and confirm("Disable notifications?"):
        content = write_var(content, "NOTIFY_EMAIL", None)
        content = write_var(content, "NOTIFY_EMAIL_PASSWORD", None)
        print("  Notifications disabled.")

    pause()
    return content


def show_summary(content: str, env: dict):
    header("Current Configuration")

    def val(name):
        v = read_var(content, name)
        return str(v) if v is not None else "—"

    api_ok  = all(env.get(k) for k in ("ALPACA_API_KEY", "ALPACA_SECRET_KEY", "FINNHUB_API_KEY"))
    yt_ok   = bool(env.get("YOUTUBE_API_KEY"))

    print(f"  API Keys         {'All set' if api_ok else 'INCOMPLETE — run option 1'}")
    print(f"  YouTube (Layer 5) {'enabled' if yt_ok else 'disabled (no YOUTUBE_API_KEY)'}")
    print(f"  Paper Trading    {val('PAPER_TRADING')}")
    print()
    print(f"  Starting Balance ${val('STARTING_BALANCE')}")
    print(f"  Floor Amount     ${val('FLOOR_AMOUNT')}  [{val('FLOOR_MODE')} mode]")
    print(f"  Deploy Fraction  {val('DEPLOY_FRACTION')}")
    print()
    print(f"  Trading Style    {val('TRADING_STYLE')}")
    print(f"  Trading Mode     {val('TRADING_MODE')}")
    print(f"  Trade Frequency  {val('TRADE_FREQUENCY')}")
    print(f"  Cycle Interval   {val('CYCLE_INTERVAL')}s")
    print()
    print(f"  Watchlist        {', '.join(read_watchlist(content))}")
    print()
    notify = read_var(content, "NOTIFY_EMAIL")
    print(f"  Notifications    {notify if notify else 'disabled'}")
    print()
    pause()


def setup_profile(env: dict) -> dict:
    header("Profile & Personal Settings")

    section("Identity")
    print("  Your name and timezone — shown in logs and alerts.")
    print()
    for key, label in [
        ("USER_NAME",     "Your name          "),
        ("USER_TIMEZONE", "Your timezone       "),
    ]:
        current = env.get(key, "")
        raw = input(f"  {label} (now: {current or 'not set'}): ").strip()
        if raw:
            env[key] = raw

    section("Phone Number (SMS alerts + 2FA)")
    print("  Requires Twilio — twilio.com (free trial).")
    print()
    for key, label in [
        ("TWILIO_ACCOUNT_SID",  "Twilio Account SID  "),
        ("TWILIO_AUTH_TOKEN",   "Twilio Auth Token   "),
        ("TWILIO_FROM_NUMBER",  "Twilio From Number  "),
        ("TWILIO_TO_NUMBER",    "Your Phone Number   "),
    ]:
        current = env.get(key, "")
        if len(current) > 6:
            display = "*" * (len(current) - 4) + current[-4:]
        else:
            display = current or "not set"
        raw = input(f"  {label} (now: {display}): ").strip()
        if raw:
            env[key] = raw

    section("Email Alerts")
    print("  Midas emails you when the floor is hit and trading stops.")
    print("  Uses Gmail App Password — Google Account -> Security -> App Passwords")
    print()
    current_email = env.get("NOTIFY_EMAIL", "")
    email = input(f"  Gmail address (now: {current_email or 'not set'}): ").strip()
    if email:
        env["NOTIFY_EMAIL"] = email
        current_pw = env.get("NOTIFY_EMAIL_PASSWORD", "")
        pw_display = "set" if current_pw else "not set"
        pw = input(f"  Gmail App Password (now: {pw_display}): ").strip()
        if pw:
            env["NOTIFY_EMAIL_PASSWORD"] = pw

    section("Privacy")
    print("  These settings control what Midas logs and stores.")
    print()
    print("  [1]  Standard  — logs all trades, signals, and errors (default)")
    print("  [2]  Minimal   — logs trades only, no signal detail")
    print("  [3]  Silent    — no console output, file log only")
    print()
    current_privacy = env.get("PRIVACY_MODE", "1")
    raw = input(f"  Privacy mode (now: {current_privacy}): ").strip()
    if raw in ("1", "2", "3"):
        env["PRIVACY_MODE"] = raw

    section("Trade History")
    print("  View every buy/sell Midas has made.")
    print()
    print("  [v]  View trade history")
    print("  [s]  Skip")
    print()
    hist_choice = input("  Choose [v/s]: ").strip().lower()
    if hist_choice == "v":
        _show_trade_history()

    pause()
    return env


def _show_trade_history():
    """Display trade history inline (called from setup profile)."""
    state_path = Path("fund_state.json")
    if not state_path.exists():
        print("\n  No trade history yet — run Midas first.\n")
        return

    with open(state_path) as f:
        state = json.load(f)

    trade_log = state.get("trade_log", [])
    if not trade_log:
        print("\n  No trades recorded yet.\n")
        return

    HW = 66
    print()
    print(f"  ┌{'─'*HW}┐")
    print(f"  │  {'Trade History':<{HW-2}}│")
    print(f"  ├{'─'*HW}┤")
    print(f"  │  {'Date':<11}  {'Ticker':<6}  {'Action':<4}  {'Amount':>10}  {'Gain / Loss':>11}  {'Note':<12}│")
    print(f"  ├{'─'*HW}┤")

    for t in reversed(trade_log[-50:]):
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


# ─────────────────────────────────────────────
# Main menu
# ─────────────────────────────────────────────

def main():
    if not MAIN_FILE.exists():
        print("\n  Error: main.py not found.")
        print("  Run this script from the Project Midas folder.\n")
        sys.exit(1)

    env     = read_env()
    content = MAIN_FILE.read_text(encoding="utf-8")
    changed = False

    while True:
        clear()
        header("Setup Wizard")
        print("  Configure everything without touching any Python files.")
        print()
        print("  [1]  Profile         — name, timezone, phone, email, privacy")
        print("  [2]  API Keys        — Alpaca + Finnhub + YouTube keys")
        print("  [3]  Fund            — balance, floor, deploy size")
        print("  [4]  Trading         — style (SWING/DAY), mode, frequency")
        print("  [5]  Floor Mode      — FIXED / TRAILING / LOCKED / PRINCIPAL")
        print("  [6]  Watchlist       — add / remove tickers")
        print("  [7]  Paper / Live    — toggle real money on or off")
        print("  [8]  Summary         — show current settings")
        print()
        print("  [9]  Save & Exit")
        print("  [b]  Exit without saving")
        print()

        choice = input("  Choose: ").strip()

        if choice == "1":
            env = setup_profile(env)
            changed = True
        elif choice == "2":
            env = setup_api_keys(env)
            changed = True
        elif choice == "3":
            content = setup_fund(content)
            changed = True
        elif choice == "4":
            content = setup_trading(content)
            changed = True
        elif choice == "5":
            content = setup_floor(content)
            changed = True
        elif choice == "6":
            content = setup_watchlist(content)
            changed = True
        elif choice == "7":
            content = setup_paper(content)
            changed = True
        elif choice == "8":
            clear()
            show_summary(content, env)
        elif choice == "9":
            write_env(env)
            MAIN_FILE.write_text(content, encoding="utf-8")
            print()
            print("  Saved.")
            print("  Settings written to main.py and .env")
            print()
            print("  Run  py main.py  to start trading.")
            print()
            break
        elif choice in ("0", "b", "B"):
            if not changed or confirm("Discard all changes and exit?"):
                break
        else:
            input("  Invalid choice. Press Enter to continue.")


if __name__ == "__main__":
    main()
