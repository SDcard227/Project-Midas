"""
Midas — Performance Fee Tracker

Usage:
  py fees.py          — show fees owed and full fee history
  py fees.py collect  — mark all outstanding fees as collected (paid out)
"""

import sys
import json
from pathlib import Path
from datetime import datetime

STATE_FILE = "fund_state.json"


def load_state() -> dict:
    p = Path(STATE_FILE)
    if not p.exists():
        print("\n  No fund state found (fund_state.json not found — run py main.py first).\n")
        sys.exit(0)
    with open(p) as f:
        return json.load(f)


def save_state(state: dict):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


def show_fees(state: dict):
    fee_log      = state.get("fee_log", [])
    fees_owed    = state.get("fees_owed", 0.0)
    fees_collected = state.get("fees_collected", 0.0)
    total_fees   = round(fees_owed + fees_collected, 2)

    W = 64
    print()
    print(f"  ┌{'─'*W}┐")
    print(f"  │  Midas Performance Fees{' '*40}│")
    print(f"  ├{'─'*W}┤")
    print(f"  │  Fee rate:        20% of profits{' '*29}│")
    print(f"  │  Fees owed:       ${fees_owed:<10.2f}  ← collect this{' '*16}│")
    print(f"  │  Fees collected:  ${fees_collected:<10.2f}{' '*33}│")
    print(f"  │  Total earned:    ${total_fees:<10.2f}{' '*33}│")
    print(f"  ├{'─'*W}┤")

    if fee_log:
        print(f"  │  {'Date':<12} {'Ticker':<7} {'User Gain':>10}  {'Your Cut':>10}{' '*12}│")
        print(f"  ├{'─'*W}┤")
        for entry in reversed(fee_log):
            print(
                f"  │  {entry['date']:<12} {entry['ticker']:<7} "
                f"${entry['gain']:>9.2f}  ${entry['fee']:>9.2f}{' '*12}│"
            )
        print(f"  ├{'─'*W}┤")
    else:
        print(f"  │  No profitable trades yet.{' '*37}│")
        print(f"  ├{'─'*W}┤")

    if fees_owed > 0:
        print(f"  │  Run  py fees.py collect  to mark ${fees_owed:.2f} as collected.{' '*(W - 48)}│")
    else:
        print(f"  │  Nothing outstanding.{' '*41}│")
    print(f"  └{'─'*W}┘")
    print()


def collect_fees(state: dict):
    owed = state.get("fees_owed", 0.0)
    if owed <= 0:
        print("\n  No fees outstanding.\n")
        return

    state["fees_collected"] = round(state.get("fees_collected", 0.0) + owed, 2)
    state["fees_owed"] = 0.0
    save_state(state)

    print()
    print(f"  Collected ${owed:.2f}.")
    print(f"  Total collected to date: ${state['fees_collected']:.2f}")
    print()


if __name__ == "__main__":
    state = load_state()

    if len(sys.argv) > 1 and sys.argv[1] == "collect":
        collect_fees(state)
    else:
        show_fees(state)
