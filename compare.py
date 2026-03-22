"""
Project Midas — Mode Comparison
Runs all 9 mode/frequency combinations for a given year and ranks them.

Usage:
  py compare.py              — compare all modes for default year (2023)
  py compare.py 2024         — compare all modes for 2024
  py compare.py 2021 2024    — compare all modes across multiple years
"""

import sys
from brain.backtest import Backtest

# ─────────────────────────────────────────────
WATCHLIST        = ["AAPL", "MSFT", "NVDA", "SPY"]
STARTING_BALANCE = 1000.00
FLOOR_AMOUNT     = 800.00
ENTRY_POINT      = 100.00
DEPLOY_FRACTION  = 0.10
REINVEST_PERCENT = 0.75
REENTRY_DIP_PCT  = 0.04
WATCHER_EXPIRY   = 60

MODES  = ["CLIMB", "PLATEAU", "SURGE"]
FREQS  = ["NORMAL", "ACTIVE", "AGGRESSIVE"]

PLATEAU_DAYS      = 10
PLATEAU_RANGE_PCT = 0.02
SLUMP_REENTRY_PCT = 0.08
CONF_MIN          = 0.5
CONF_MAX          = 2.0

# Short-term capital gains tax rate (swing trades held < 1 year)
# Common brackets: 0.10 / 0.12 / 0.22 / 0.24 / 0.32 / 0.35 / 0.37
# Losses are shown at face value (no tax-loss benefit applied)
TAX_RATE = 0.22

# Transaction cost model
# Each round-trip (buy + sell) incurs:
#   COMMISSION   — flat fee per trade, each side ($0 for Alpaca, ~$1 for some brokers)
#   SPREAD_PCT   — bid-ask spread per side as % of trade value
#                  Liquid large-caps (AAPL, SPY): ~0.01-0.03%
#                  Mid-caps / ETFs:               ~0.03-0.08%
#                  Volatile/thin stocks:          ~0.10-0.25%
# Trade size is estimated as STARTING_BALANCE * DEPLOY_FRACTION per position
COMMISSION = 0.00    # $ per side (0 = commission-free broker like Alpaca)
SPREAD_PCT = 0.0003  # 0.03% per side (3 bps) — reasonable for AAPL/MSFT/SPY/NVDA
# ─────────────────────────────────────────────


def run_one(mode: str, freq: str, year: int) -> dict:
    bt = Backtest(
        watchlist=WATCHLIST,
        start_date=f"{year}-01-01",
        end_date=f"{year+1}-01-01",
        starting_balance=STARTING_BALANCE,
        floor_amount=FLOOR_AMOUNT,
        entry_point=ENTRY_POINT,
        deploy_fraction=DEPLOY_FRACTION,
        reinvest_percent=REINVEST_PERCENT,
        reentry_dip_percent=REENTRY_DIP_PCT,
        watcher_expiry_days=WATCHER_EXPIRY,
        trading_mode=mode,
        trade_frequency=freq,
        plateau_days=PLATEAU_DAYS,
        plateau_range_pct=PLATEAU_RANGE_PCT,
        slump_reentry_pct=SLUMP_REENTRY_PCT,
        confidence_min_scale=CONF_MIN,
        confidence_max_scale=CONF_MAX,
        max_trades_month=None,
        allocation_mode="FREE_RANGE",
        allocations={},
        commission=COMMISSION,
        spread_pct=SPREAD_PCT,
        tax_rate=TAX_RATE,
    )
    # Suppress internal print output during batch run
    import io, contextlib
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        bt.run()
    return bt.results


def rank_label(pos: int, total: int) -> str:
    if pos == 0:
        return " #1 "
    if pos == total - 1:
        return "last"
    return f" #{pos+1} "


def print_table(rows: list, year: int):
    W = 92
    print()
    print(f"  +{'-'*W}+")
    title = (f"  MODE COMPARISON  --  {year}  --  $1,000 start  |  "
             f"Tax: {int(TAX_RATE*100)}%  |  Spread: {SPREAD_PCT*100:.2f}%/side  |  Commission: ${COMMISSION:.2f}")
    print(f"  | {title:<{W-1}}|")
    print(f"  | {'(returns below are already net of transaction costs and tax — baked into engine)':<{W-1}}|")
    print(f"  +{'-'*W}+")
    print(
        f"  | {'':4}  {'Mode':<8}  {'Freq':<12}  {'Net Return':>20}  "
        f"{'- Costs':>8}  {'- Tax':>8}  {'Trades':>7}  {'Win%':>5}  {'Best':>9}  {'Worst':>9} |"
    )
    print(f"  +{'-'*W}+")

    for i, r in enumerate(rows):
        ret_str  = f"${r['total_return']:>+.2f} ({r['total_return_pct']:>+.1f}%)"
        costs    = r.get('total_transaction_costs', 0)
        tax      = r.get('total_tax_paid', 0)
        rank     = rank_label(i, len(rows))
        print(
            f"  | {rank} {r['mode']:<8}  {r['freq']:<12}  {ret_str:>20}  "
            f"-${costs:>5.2f}  -${tax:>5.2f}  "
            f"{r['total_trades']:>7}  {r['win_rate_pct']:>4.1f}%  "
            f"${r['best_trade']:>7.2f}  ${r['worst_trade']:>7.2f} |"
        )

    print(f"  +{'-'*W}+")
    winner = rows[0]
    loser  = rows[-1]
    print(f"  | Best:  {winner['mode']}/{winner['freq']}  "
          f"net ${winner['total_return']:+.2f}  "
          f"costs -${winner.get('total_transaction_costs',0):.2f}  "
          f"tax -${winner.get('total_tax_paid',0):.2f}{'':>18}|")
    print(f"  | Worst: {loser['mode']}/{loser['freq']}  "
          f"net ${loser['total_return']:+.2f}  "
          f"costs -${loser.get('total_transaction_costs',0):.2f}  "
          f"tax -${loser.get('total_tax_paid',0):.2f}{'':>19}|")
    print(f"  +{'-'*W}+")
    print()



def pick_modes() -> list:
    """Interactive mode/frequency picker. Returns list of (mode, freq) tuples."""
    print()
    print("  +--------------------------------------------------+")
    print("  |  SELECT TRADING MODES                            |")
    print("  +--------------------------------------------------+")
    print("  |  [1] CLIMB     — ride the trend                  |")
    print("  |  [2] PLATEAU   — exit early on sideways move     |")
    print("  |  [3] SURGE     — hold longer, exit at breakdown  |")
    print("  |  [a] All modes                                   |")
    print("  +--------------------------------------------------+")
    raw = input("  Modes (e.g. 1 3 or a): ").strip().lower()

    if raw == "a" or raw == "":
        selected_modes = MODES[:]
    else:
        mode_map = {"1": "CLIMB", "2": "PLATEAU", "3": "SURGE"}
        selected_modes = [mode_map[c] for c in raw.split() if c in mode_map]
        if not selected_modes:
            selected_modes = MODES[:]

    print()
    print("  +--------------------------------------------------+")
    print("  |  SELECT FREQUENCIES                              |")
    print("  +--------------------------------------------------+")
    print("  |  [1] NORMAL     — low activity, fewer trades     |")
    print("  |  [2] ACTIVE     — medium activity                |")
    print("  |  [3] AGGRESSIVE — high activity, more trades     |")
    print("  |  [a] All frequencies                             |")
    print("  +--------------------------------------------------+")
    raw2 = input("  Frequencies (e.g. 1 3 or a): ").strip().lower()

    if raw2 == "a" or raw2 == "":
        selected_freqs = FREQS[:]
    else:
        freq_map = {"1": "NORMAL", "2": "ACTIVE", "3": "AGGRESSIVE"}
        selected_freqs = [freq_map[c] for c in raw2.split() if c in freq_map]
        if not selected_freqs:
            selected_freqs = FREQS[:]

    combos = [(m, f) for m in selected_modes for f in selected_freqs]
    return combos


def pick_years() -> list:
    """Interactive year picker. Returns list of ints."""
    print()
    print("  +--------------------------------------------------+")
    print("  |  SELECT YEAR(S)                                  |")
    print("  +--------------------------------------------------+")
    print("  |  [1] 2021   [2] 2022   [3] 2023   [4] 2024      |")
    print("  |  [a] All years (2021-2024)                       |")
    print("  |  Or type a custom year e.g. 2019                 |")
    print("  +--------------------------------------------------+")
    raw = input("  Year(s) (e.g. 1 3 or a or 2020): ").strip().lower()

    year_map = {"1": 2021, "2": 2022, "3": 2023, "4": 2024}

    if raw == "a" or raw == "":
        return [2021, 2022, 2023, 2024]

    years = []
    for token in raw.split():
        if token in year_map:
            years.append(year_map[token])
        elif token.isdigit() and len(token) == 4:
            years.append(int(token))

    return years if years else [2023]


def main():
    args = sys.argv[1:]

    # Non-interactive mode: years passed as CLI args, all combos
    cli_years = [int(a) for a in args if a.isdigit() and len(a) == 4]

    if cli_years:
        years  = cli_years
        combos = [(m, f) for m in MODES for f in FREQS]
    else:
        combos = pick_modes()
        years  = pick_years()

    all_results = {}
    for year in years:
        total = len(combos)
        rows  = []

        print(f"\n  Running {total} combinations for {year}...")
        print(f"  {'':-<50}")

        for i, (mode, freq) in enumerate(combos, 1):
            label = f"  [{i}/{total}]  {mode:<8}  {freq}"
            print(label, end="", flush=True)
            try:
                result = run_one(mode, freq, year)
                result["mode"] = mode
                result["freq"] = freq
                rows.append(result)
                ret = result["total_return_pct"]
                print(f"  ->  {ret:>+.1f}%")
            except Exception as e:
                print(f"  ->  ERROR: {e}")

        rows.sort(key=lambda r: r["total_return"], reverse=True)
        print_table(rows, year)
        all_results[year] = rows

    # If multiple years, print a cross-year winner summary
    if len(years) > 1:
        W = 86
        print(f"  +{'-'*W}+")
        print(f"  | {'CROSS-YEAR SUMMARY  (net returns — costs & tax baked in)':<{W-1}}|")
        print(f"  +{'-'*W}+")
        print(f"  | {'Year':<6}  {'Best combo':<22}  {'Net Return':>14}  {'- Costs':>8}  {'- Tax':>8}  {'Worst combo':<14} |")
        print(f"  +{'-'*W}+")
        for year, rows in all_results.items():
            best      = rows[0]
            worst     = rows[-1]
            best_lbl  = f"{best['mode']}/{best['freq']}"
            worst_lbl = f"{worst['mode']}/{worst['freq']}"
            print(
                f"  | {year:<6}  {best_lbl:<22}  "
                f"${best['total_return']:>+7.2f} ({best['total_return_pct']:>+5.1f}%)  "
                f"costs -${best.get('total_transaction_costs',0):>5.2f}  "
                f"tax -${best.get('total_tax_paid',0):>6.2f}  "
                f"{worst_lbl:<14} |"
            )
        print(f"  +{'-'*W}+")
        print()


if __name__ == "__main__":
    main()
