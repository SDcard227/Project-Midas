import logging
import requests
from datetime import datetime, timedelta

log = logging.getLogger("Midas.Politician")

# Primary + fallback URLs per chamber
HOUSE_URLS = [
    "https://house-stock-watcher-data.s3-us-west-2.amazonaws.com/data/all_transactions.json",
    "https://raw.githubusercontent.com/house-stock-watcher/house-stock-watcher-data/master/data/all_transactions.json",
]
SENATE_URLS = [
    "https://senate-stock-watcher-data.s3-us-west-2.amazonaws.com/aggregate/all_transactions.json",
    "https://raw.githubusercontent.com/eleqtrizit/senate-stock-watcher-data/master/aggregate/all_transactions.json",
]

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0 Safari/537.36"
}

CACHE_HOURS = 6  # Refresh congressional data every 6 hours


class PoliticianTracker:
    """
    Layer 4 — Congressional trade signal.

    Pulls public House + Senate stock disclosure data (no API key required).
    Scores each ticker based on recent politician buy/sell activity.

    If politicians are buying: mild bullish boost.
    If politicians are selling: mild bearish flag.
    Neutral or no activity: no effect.

    Data source: House Stock Watcher + Senate Stock Watcher (public S3 buckets)
    Refreshes every CACHE_HOURS hours — no need to download every cycle.
    """

    def __init__(self, lookback_days: int = 90):
        self.lookback_days = lookback_days
        self._cache = []
        self._last_fetch = None

    # -------------------------------------------------------------------------
    # Data fetch + cache
    # -------------------------------------------------------------------------

    def _refresh(self):
        """Download fresh congressional trade data and merge both chambers."""
        trades = []

        for urls, chamber in [(HOUSE_URLS, "House"), (SENATE_URLS, "Senate")]:
            raw = None
            for url in urls:
                try:
                    resp = requests.get(url, headers=HEADERS, timeout=15)
                    if resp.ok:
                        raw = resp.json()
                        break
                    log.warning(f"Politician tracker: {chamber} URL returned {resp.status_code} — trying fallback")
                except Exception as e:
                    log.warning(f"Politician tracker: {chamber} URL failed ({e}) — trying fallback")
            if raw is None:
                log.warning(f"Politician tracker: all {chamber} URLs failed — skipping")
                continue
            try:
                for t in raw:
                    ticker = str(t.get("ticker", "")).strip().upper()
                    trade_type = str(t.get("type", "")).strip().lower()
                    date_str = t.get("transaction_date") or t.get("disclosure_date", "")
                    name = t.get("representative") or t.get("senator", "Unknown")

                    if not ticker or not date_str:
                        continue

                    try:
                        date = datetime.strptime(date_str[:10], "%Y-%m-%d")
                    except ValueError:
                        continue

                    trades.append({
                        "ticker":  ticker,
                        "type":    trade_type,
                        "date":    date,
                        "name":    name,
                        "chamber": chamber,
                    })
                log.info(f"Politician tracker: loaded {len(raw)} {chamber} trades")
            except Exception as e:
                log.warning(f"Politician tracker: failed to load {chamber} data — {e}")

        self._cache = trades
        self._last_fetch = datetime.now()

    def _ensure_fresh(self):
        if self._last_fetch is None or (datetime.now() - self._last_fetch).seconds > CACHE_HOURS * 3600:
            self._refresh()

    # -------------------------------------------------------------------------
    # Signal
    # -------------------------------------------------------------------------

    def get_signal(self, ticker: str) -> dict:
        """
        Returns a Layer 4 signal for the given ticker.

        signal: "positive" | "neutral" | "negative"
        score:  +1, 0, or -1
        buys:   number of purchase transactions in lookback window
        sells:  number of sale transactions in lookback window
        recent: list of the 3 most recent trades
        """
        try:
            self._ensure_fresh()
        except Exception as e:
            log.warning(f"Politician tracker unavailable: {e}")
            return _neutral()

        cutoff = datetime.now() - timedelta(days=self.lookback_days)
        relevant = [
            t for t in self._cache
            if t["ticker"] == ticker.upper() and t["date"] >= cutoff
        ]

        buys  = sum(1 for t in relevant if "purchase" in t["type"])
        sells = sum(1 for t in relevant if "sale" in t["type"])

        if buys > sells:
            signal, score = "positive", 1
        elif sells > buys:
            signal, score = "negative", -1
        else:
            signal, score = "neutral", 0

        recent = sorted(relevant, key=lambda x: x["date"], reverse=True)[:3]
        recent_str = [
            f"{t['name']} ({t['chamber']}) — {t['type'].title()} on {t['date'].strftime('%Y-%m-%d')}"
            for t in recent
        ]

        return {
            "signal":  signal,
            "score":   score,
            "buys":    buys,
            "sells":   sells,
            "total":   len(relevant),
            "recent":  recent_str,
        }


def _neutral() -> dict:
    return {"signal": "neutral", "score": 0, "buys": 0, "sells": 0, "total": 0, "recent": []}
