import sys
from brain.backtest import Backtest

# ─────────────────────────────────────────────
# BACKTEST CONFIGURATION
# No API keys needed — uses Yahoo Finance (free)
# ─────────────────────────────────────────────

WATCHLIST = [
    "AAPL",
    "MSFT",
    "NVDA",
    "SPY",
]

START_DATE = "2022-01-01"    # Start of test window
END_DATE   = "2023-01-01"    # End of test window

STARTING_BALANCE    = 1000.00   # Simulated fund ($)
FLOOR_AMOUNT        = 800.00    # Floor — stop all trading if fund drops to this
ENTRY_POINT         = 100.00    # Minimum cash before deploying
DEPLOY_FRACTION     = 0.10      # Deploy 10% of cash per new trade
REINVEST_PERCENT    = 0.75      # Reinvest 75% of proceeds at the next dip
REENTRY_DIP_PERCENT = 0.04      # CLIMB/SURGE: trigger re-entry at 4% dip from peak
WATCHER_EXPIRY_DAYS = 60        # Cancel re-entry watcher after this many days

# ─────────────────────────────────────────────
# TRADING MODE
# CLIMB   — ride the trend, sell on bearish signal (default)
# PLATEAU — exit when stock goes sideways, re-enter at bigger slump
# SURGE   — hold longer, only exit when peak is breaking down
# ─────────────────────────────────────────────

VALID_MODES = {"climb": "CLIMB", "plateau": "PLATEAU", "surge": "SURGE"}

# Usage: py backtest_run.py [mode] [deploy_fraction]
# Examples:
#   py backtest_run.py surge
#   py backtest_run.py surge 0.20
#   py backtest_run.py climb 0.30

# Usage: py backtest_run.py [mode] [frequency] [max_trades] [year]
# Examples:
#   py backtest_run.py surge
#   py backtest_run.py surge aggressive
#   py backtest_run.py surge aggressive 8
#   py backtest_run.py surge aggressive 8 2023
#   py backtest_run.py climb normal 4 2022

VALID_FREQS = {"normal": "NORMAL", "active": "ACTIVE", "aggressive": "AGGRESSIVE"}

mode_arg = sys.argv[1].lower() if len(sys.argv) > 1 else "climb"
if mode_arg not in VALID_MODES:
    print(f"Unknown mode '{mode_arg}'. Use: climb | plateau | surge")
    sys.exit(1)
TRADING_MODE = VALID_MODES[mode_arg]

freq_arg = sys.argv[2].lower() if len(sys.argv) > 2 else "normal"
if freq_arg not in VALID_FREQS:
    print(f"Unknown frequency '{freq_arg}'. Use: normal | active | aggressive")
    sys.exit(1)
TRADE_FREQUENCY = VALID_FREQS[freq_arg]

def _is_year(val):
    return val.isdigit() and len(val) == 4 and val.startswith("20")

for raw_arg in sys.argv[3:]:
    if _is_year(raw_arg):
        START_DATE = f"{raw_arg}-01-01"
        END_DATE   = f"{int(raw_arg) + 1}-01-01"
    elif raw_arg.isdigit():
        if int(raw_arg) < 1:
            print(f"Invalid max trades '{raw_arg}'. Use a positive integer, e.g. 8")
            sys.exit(1)
        MAX_TRADES_MONTH = int(raw_arg)
    else:
        print(f"Unknown argument '{raw_arg}'. Expected a year (e.g. 2023) or trade cap (e.g. 8)")
        sys.exit(1)
PLATEAU_DAYS      = 10       # PLATEAU: days of flat price to detect plateau
PLATEAU_RANGE_PCT = 0.02     # PLATEAU: max price range to qualify as plateau
SLUMP_REENTRY_PCT = 0.08     # PLATEAU: re-enter at 8% drop (larger than default)

CONFIDENCE_MIN_SCALE = 0.5   # At 0 confidence, deploy 50% of normal amount
CONFIDENCE_MAX_SCALE = 2.0   # At 100 confidence, deploy 200% of normal amount
MAX_TRADES_MONTH     = None  # None = unlimited | e.g. 8 = max 8 new buys per month
ALLOCATION_MODE      = "FREE_RANGE"  # "FREE_RANGE" | "PERCENTAGE" | "MANUAL"
ALLOCATIONS          = {             # used when ALLOCATION_MODE is not FREE_RANGE
    "AAPL": 0.20,
    "MSFT": 0.20,
    "NVDA": 0.30,
    "SPY":  0.30,
}

# ─────────────────────────────────────────────

if __name__ == "__main__":
    bt = Backtest(
        watchlist=WATCHLIST,
        start_date=START_DATE,
        end_date=END_DATE,
        starting_balance=STARTING_BALANCE,
        floor_amount=FLOOR_AMOUNT,
        entry_point=ENTRY_POINT,
        deploy_fraction=DEPLOY_FRACTION,
        reinvest_percent=REINVEST_PERCENT,
        reentry_dip_percent=REENTRY_DIP_PERCENT,
        watcher_expiry_days=WATCHER_EXPIRY_DAYS,
        trading_mode=TRADING_MODE,
        plateau_days=PLATEAU_DAYS,
        plateau_range_pct=PLATEAU_RANGE_PCT,
        slump_reentry_pct=SLUMP_REENTRY_PCT,
        trade_frequency=TRADE_FREQUENCY,
        confidence_min_scale=CONFIDENCE_MIN_SCALE,
        confidence_max_scale=CONFIDENCE_MAX_SCALE,
        max_trades_month=MAX_TRADES_MONTH,
        allocation_mode=ALLOCATION_MODE,
        allocations=ALLOCATIONS,
    )
    bt.run()
