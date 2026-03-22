import os
from dotenv import load_dotenv
from brain.midas import Midas

load_dotenv()

# ─────────────────────────────────────────────
# CONFIGURE YOUR FUND HERE
# ─────────────────────────────────────────────

WATCHLIST = [
    "AAPL",   # Apple
    "MSFT",   # Microsoft
    "NVDA",   # NVIDIA
    "SPY",    # S&P 500 ETF
]

STARTING_BALANCE = 1000.00   # Total fund amount ($)
FLOOR_AMOUNT     = 800.00    # Hard minimum — used by FIXED and LOCKED modes
ENTRY_POINT      = 100.00    # Minimum liquid balance required before deploying capital
DEPLOY_FRACTION  = 0.10      # Deploy 10% of liquid balance per new trade
CYCLE_INTERVAL   = 300       # Run a cycle every 5 minutes (300 seconds)

# ─────────────────────────────────────────────
# REINVESTMENT SETTINGS
# After a sell, Midas watches for the stock to peak then dip.
# When the dip is confirmed it puts REINVEST_PERCENT back in.
# ─────────────────────────────────────────────

# ─────────────────────────────────────────────
# TRADING MODE
# CLIMB   — ride the trend, sell on bearish signal (default, balanced)
# PLATEAU — exit when stock goes sideways before it drops, re-enter at bigger slump
# SURGE   — hold longer, only exit when the peak is actively breaking down
# ─────────────────────────────────────────────

# ─────────────────────────────────────────────
# TRADING STYLE
# SWING — daily bars, holds positions for days/weeks (default)
# DAY   — 5-minute bars, intraday only — all positions close before market close
#          Use CYCLE_INTERVAL = 60 in DAY mode. Requires $25k+ to avoid PDT rule
#          (not an issue in paper trading mode).
# ─────────────────────────────────────────────

TRADING_STYLE      = "SWING"     # "SWING" | "DAY"
DAY_CLOSE_BUFFER_MIN = 10        # DAY mode: close positions this many minutes before close

TRADING_MODE       = "CLIMB"      # "CLIMB" | "PLATEAU" | "SURGE"
TRADE_FREQUENCY    = "NORMAL"     # "NORMAL" | "ACTIVE" | "AGGRESSIVE"
PLATEAU_DAYS       = 10       # PLATEAU: days of flat price to detect a plateau
PLATEAU_RANGE_PCT  = 0.02     # PLATEAU: price must stay within 2% range to qualify
SLUMP_REENTRY_PCT  = 0.08     # PLATEAU: re-enter at 8% drop (larger dip threshold)

REINVEST_PERCENT       = 0.75   # 75% of sale proceeds go back in at the next dip
LIQUID_RESERVE_PERCENT = 0.25   # 25% of sale proceeds stay liquid as a buffer
REENTRY_DIP_PERCENT    = 0.04   # CLIMB/SURGE: trigger re-entry at 4% dip from peak
WATCHER_EXPIRY_DAYS    = 60     # Cancel a re-entry watcher after this many days if never triggered

# ─────────────────────────────────────────────
# CONFIDENCE METER
# Midas scores each trade signal 0-100 based on RSI position,
# trend strength, and sentiment. Stronger signals deploy more capital.
# MIN_SCALE: multiplier at 0 confidence  (e.g. 0.5 = deploy 50% of normal)
# MAX_SCALE: multiplier at 100 confidence (e.g. 2.0 = deploy 200% of normal)
# ─────────────────────────────────────────────

CONFIDENCE_MIN_SCALE = 0.5    # At low confidence, deploy this fraction of normal
CONFIDENCE_MAX_SCALE = 2.0    # At high confidence, deploy this multiple of normal

# ─────────────────────────────────────────────
# MONTHLY TRADE BUDGET
# Max number of new BUY trades per calendar month.
# Re-entry watchers and sells are never counted — only new positions.
# Set to None for unlimited (default).
#
# Guide:
#   1–3   = very conservative
#   4–8   = balanced
#   9–15  = active
#   16–30 = aggressive
#   None  = unlimited
# ─────────────────────────────────────────────

MAX_TRADES_MONTH = None

# ─────────────────────────────────────────────
# ALLOCATION CONTROL
# Controls how much of the fund can go into each stock.
#
# FREE_RANGE  — no limits, Midas allocates freely (default)
# PERCENTAGE  — max % of current fund value per ticker
# MANUAL      — max fixed dollar amount per ticker
#
# For PERCENTAGE, values must add up to ≤ 1.0
# For MANUAL, values are dollar caps per ticker
# ─────────────────────────────────────────────

ALLOCATION_MODE = "FREE_RANGE"   # "FREE_RANGE" | "PERCENTAGE" | "MANUAL"

ALLOCATIONS = {
    "AAPL": 0.20,   # PERCENTAGE: max 20% of fund in AAPL
    "MSFT": 0.20,   # PERCENTAGE: max 20% of fund in MSFT
    "NVDA": 0.30,   # PERCENTAGE: max 30% of fund in NVDA
    "SPY":  0.30,   # PERCENTAGE: max 30% of fund in SPY
}

# For MANUAL mode, replace the values above with dollar amounts:
# ALLOCATIONS = {
#     "AAPL": 200.00,
#     "MSFT": 200.00,
#     "NVDA": 300.00,
#     "SPY":  300.00,
# }

# ─────────────────────────────────────────────
# FLOOR MODE
# FIXED    — floor stays at FLOOR_AMOUNT forever (default)
# TRAILING — floor rises as your fund grows, always stays TRAILING_FLOOR_PCT below peak
#            e.g. fund hits $1500, trailing floor moves up to $1500 * (1 - 0.15) = $1275
#            protects your gains automatically — floor never falls back down
# ─────────────────────────────────────────────

FLOOR_MODE         = "FIXED"   # "FIXED" | "TRAILING" | "LOCKED" | "PRINCIPAL"
TRAILING_FLOOR_PCT = 0.15      # TRAILING only: floor trails this % below fund peak
#
# FLOOR MODE guide:
#   FIXED     — tripwire. Midas trades freely, stops if fund falls to FLOOR_AMOUNT
#   TRAILING  — same as FIXED but the floor rises as your fund grows, locking in gains
#   LOCKED    — FLOOR_AMOUNT kept in cash forever, never invested. Only trades above it
#   PRINCIPAL — your starting balance is locked in cash. Midas only ever trades with gains
#               Zero risk to your original investment

# ─────────────────────────────────────────────
# NOTIFICATIONS
# When the floor is hit and Midas pulls out, you get an email alert.
# Uses Gmail — requires a Gmail App Password (not your normal password).
# To set up: Google Account → Security → 2-Step Verification → App Passwords
# Leave as None to disable notifications.
# ─────────────────────────────────────────────

NOTIFY_EMAIL          = None   # e.g. "you@gmail.com" — sends AND receives the alert
NOTIFY_EMAIL_PASSWORD = None   # Gmail App Password (set in .env for safety)

# ─────────────────────────────────────────────
# SMS NOTIFICATIONS + 2FA (Twilio)
# Free trial at twilio.com — gives you $15 credit (~500 texts)
# Set ENABLE_2FA = True to require a phone code before Midas starts trading.
# Set all four TWILIO values in .env or via py setup.py → [1] API Keys
# ─────────────────────────────────────────────

ENABLE_2FA = False  # Twilio not configured — disable until keys are set

# ─────────────────────────────────────────────
# LAYER 4 — CONGRESSIONAL TRADE TRACKING
# Midas watches public House + Senate stock disclosures.
# When politicians are buying: mild bullish boost to confidence.
# When politicians are selling: mild bearish drag on confidence.
# No API key needed — uses free public data (updates every 6 hours).
# Set LAYER4_ENABLED = False to disable entirely.
# ─────────────────────────────────────────────

# ─────────────────────────────────────────────
# PERFORMANCE FEE
# Your cut of every profitable trade.
# 0.20 = 20% of profits go to you, tracked automatically.
# Run  py fees.py  to see what's owed and mark it as collected.
# ─────────────────────────────────────────────

PERFORMANCE_FEE = 0.20   # 20% of profits

LAYER4_ENABLED       = False  # Data source offline — re-enable when source is restored
LAYER4_LOOKBACK_DAYS = 90   # How many days back to count politician trades

# ─────────────────────────────────────────────
# LAYER 5 — YOUTUBE NEWS SENTIMENT
# Searches YouTube for recent videos mentioning each ticker.
# Scans titles and descriptions for bullish/bearish keywords.
# Positive coverage boosts confidence. Negative coverage drags it.
#
# Requires a free YouTube Data API v3 key (set YOUTUBE_API_KEY in .env):
#   console.cloud.google.com → New Project → Enable YouTube Data API v3 → Create API Key
#   Free quota: 10,000 units/day (100 units per search = 100 searches/day)
#
# Set LAYER5_ENABLED = False to disable (or just don't set YOUTUBE_API_KEY).
# ─────────────────────────────────────────────

LAYER5_ENABLED       = True
LAYER5_LOOKBACK_DAYS = 7    # Only count videos published in the last N days

# ─────────────────────────────────────────────
# SESSION DURATION
# How long you want Midas to run before stopping.
# Set to None to run forever (true set-and-forget passive income mode).
# Or set a number of days — Midas will trade for that long then stop.
# ─────────────────────────────────────────────

RUN_DURATION_DAYS = None   # None = run forever | e.g. 30 = run for 30 days then stop

# ─────────────────────────────────────────────
# PAPER TRADING
# True  = fake money, real market data (safe — use this first)
# False = real money, live trading     (only flip when ready)
# ─────────────────────────────────────────────

PAPER_TRADING = True

# ─────────────────────────────────────────────

if __name__ == "__main__":
    midas = Midas(
        watchlist=WATCHLIST,
        starting_balance=STARTING_BALANCE,
        floor_amount=FLOOR_AMOUNT,
        entry_point=ENTRY_POINT,
        deploy_fraction=DEPLOY_FRACTION,
        cycle_interval=CYCLE_INTERVAL,
        paper=PAPER_TRADING,
        trading_mode=TRADING_MODE,
        trade_frequency=TRADE_FREQUENCY,
        plateau_days=PLATEAU_DAYS,
        plateau_range_pct=PLATEAU_RANGE_PCT,
        slump_reentry_pct=SLUMP_REENTRY_PCT,
        reinvest_percent=REINVEST_PERCENT,
        liquid_reserve_percent=LIQUID_RESERVE_PERCENT,
        reentry_dip_percent=REENTRY_DIP_PERCENT,
        watcher_expiry_days=WATCHER_EXPIRY_DAYS,
        run_duration_days=RUN_DURATION_DAYS,
        confidence_min_scale=CONFIDENCE_MIN_SCALE,
        confidence_max_scale=CONFIDENCE_MAX_SCALE,
        max_trades_month=MAX_TRADES_MONTH,
        allocation_mode=ALLOCATION_MODE,
        allocations=ALLOCATIONS,
        floor_mode=FLOOR_MODE,
        trailing_floor_pct=TRAILING_FLOOR_PCT,
        notify_email=NOTIFY_EMAIL,
        notify_email_password=NOTIFY_EMAIL_PASSWORD,
        twilio_sid=os.getenv("TWILIO_ACCOUNT_SID"),
        twilio_token=os.getenv("TWILIO_AUTH_TOKEN"),
        twilio_from=os.getenv("TWILIO_FROM_NUMBER"),
        twilio_to=os.getenv("TWILIO_TO_NUMBER"),
        enable_2fa=ENABLE_2FA,
        layer4_enabled=LAYER4_ENABLED,
        layer4_lookback_days=LAYER4_LOOKBACK_DAYS,
        performance_fee=PERFORMANCE_FEE,
        layer5_enabled=LAYER5_ENABLED,
        layer5_lookback_days=LAYER5_LOOKBACK_DAYS,
        trading_style=TRADING_STYLE,
        day_close_buffer_min=DAY_CLOSE_BUFFER_MIN,
    )
    midas.run()
