"""
worker.py — the always-on scanner loop.

This is what turns Midas from "smart modules" into a living system. It runs the
news pipeline on a loop: fetch -> AI filter -> ledger -> peer-review haulers ->
persist. Confidence accumulates ACROSS cycles, so leaks actually climb the ladder
into whispers and haulers over time. The web app (sim_server.py) just reads the
ledger this worker keeps fresh.

Run locally:
    python worker.py
Run on Render:
    add a second service of type "Background Worker", start command: python worker.py

Config (.env):
    SCAN_INTERVAL_SECONDS   how often to scan (default 300 = 5 min)
    ANTHROPIC_API_KEY       required for the AI layer (else it fetches but can't judge)

Stop with Ctrl+C — it finishes the current wait and exits cleanly.
"""
import os
import sys
import time
import logging

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from dotenv import load_dotenv
load_dotenv()

from brain.signal_ledger import SignalLedger
from brain.news_pipeline import run_scan

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.FileHandler("worker.log", encoding="utf-8"),
              logging.StreamHandler()],
)
log = logging.getLogger("Midas.Worker")

INTERVAL = int(os.getenv("SCAN_INTERVAL_SECONDS", "300"))


def main():
    # One shared, persistent ledger across every cycle — this is what lets
    # confidence build over time instead of resetting each scan.
    ledger = SignalLedger()

    if not os.getenv("ANTHROPIC_API_KEY"):
        log.warning("ANTHROPIC_API_KEY not set — the firehose will run but the AI "
                    "filter is OFF, so nothing gets classified or scored.")

    log.info(f"Worker started. Scanning every {INTERVAL}s. Ctrl+C to stop.")
    cycle = 0
    while True:
        cycle += 1
        try:
            r = run_scan(ledger=ledger)
            w = len(r.get("whispers", []))
            h = len(r.get("haulers", []))
            log.info(
                f"Cycle {cycle}: scanned {r['scanned']} from {len(r['sources'])} sources "
                f"| AI {'on' if r['ai_enabled'] else 'OFF'} "
                f"| {r['market_moving']} market-moving | {w} whispers, {h} haulers"
            )
            for hl in r.get("haulers", []):
                rv = hl.get("review", {})
                log.info(
                    f"  HAULER {hl['ticker']} {hl['direction']} "
                    f"conf {hl.get('reviewed_confidence', hl['confidence'])} "
                    f"[{rv.get('status', 'unreviewed')}] {hl['source_count']} src"
                )
        except Exception as e:
            log.error(f"Cycle {cycle} failed: {e}")

        time.sleep(INTERVAL)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        log.info("Worker stopped (Ctrl+C).")
