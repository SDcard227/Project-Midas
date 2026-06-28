import os
import logging
import requests
from datetime import datetime, timedelta

log = logging.getLogger("Midas.Politician")

# Layer 4 — congressional trade signal.
#
# The old free source (House/Senate Stock Watcher public S3 buckets) had its public
# access revoked — every URL now returns 403/404. We query Financial Modeling Prep
# per symbol instead: Senate + House trades. Uses FMP_API_KEY (the SAME free key as
# the company-dossier ownership pie). No key -> graceful neutral (no crash, no signal).
_FMP_SENATE = "https://financialmodelingprep.com/stable/senate-trades"
_FMP_HOUSE  = "https://financialmodelingprep.com/stable/house-trades"
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120 Safari/537.36"}
CACHE_HOURS = 6


def _key():
    return os.getenv("FMP_API_KEY")


class PoliticianTracker:
    """Congressional buy/sell signal for a ticker (FMP Senate + House, per symbol).

    If politicians are buying: mild bullish boost. Selling: mild bearish flag.
    Per-ticker cache (CACHE_HOURS) keeps us well inside FMP's free rate limit.
    """

    def __init__(self, lookback_days: int = 90):
        self.lookback_days = lookback_days
        self._cache = {}   # ticker -> (fetched_at, [trades])

    def _fetch(self, ticker):
        key = _key()
        if not key:
            return []
        trades = []
        for url, chamber in [(_FMP_SENATE, "Senate"), (_FMP_HOUSE, "House")]:
            try:
                r = requests.get(url, params={"symbol": ticker, "apikey": key},
                                 headers=HEADERS, timeout=12)
                if not r.ok:
                    continue
                data = r.json()
                if not isinstance(data, list):
                    continue
                for t in data:
                    typ = str(t.get("type") or t.get("transaction") or "").lower()
                    ds = (t.get("transactionDate") or t.get("transaction_date")
                          or t.get("disclosureDate") or t.get("dateRecieved") or "")
                    if not ds:
                        continue
                    try:
                        date = datetime.strptime(str(ds)[:10], "%Y-%m-%d")
                    except ValueError:
                        continue
                    name = (t.get("representative") or t.get("senator")
                            or (str(t.get("firstName", "")) + " " + str(t.get("lastName", ""))).strip()
                            or t.get("office") or "Unknown")
                    trades.append({"type": typ, "date": date, "name": name, "chamber": chamber})
            except Exception as e:
                log.warning(f"Politician (FMP {chamber}) failed: {e}")
        return trades

    def _get(self, ticker):
        now = datetime.now()
        hit = self._cache.get(ticker)
        if hit and (now - hit[0]).total_seconds() < CACHE_HOURS * 3600:
            return hit[1]
        trades = self._fetch(ticker)
        self._cache[ticker] = (now, trades)
        return trades

    def get_signal(self, ticker: str) -> dict:
        """signal: positive|neutral|negative · score +1/0/-1 · buys/sells · recent[]."""
        try:
            trades = self._get((ticker or "").upper())
        except Exception as e:
            log.warning(f"Politician tracker unavailable: {e}")
            return _neutral()

        cutoff = datetime.now() - timedelta(days=self.lookback_days)
        relevant = [t for t in trades if t["date"] >= cutoff]
        buys  = sum(1 for t in relevant if "purchase" in t["type"] or "buy" in t["type"])
        sells = sum(1 for t in relevant if "sale" in t["type"] or "sell" in t["type"])

        if buys > sells:
            signal, score = "positive", 1
        elif sells > buys:
            signal, score = "negative", -1
        else:
            signal, score = "neutral", 0

        recent = sorted(relevant, key=lambda x: x["date"], reverse=True)[:3]
        recent_str = [
            f"{t['name']} ({t['chamber']}) — {t['type'].title() or 'Trade'} on {t['date'].strftime('%Y-%m-%d')}"
            for t in recent
        ]
        return {"signal": signal, "score": score, "buys": buys, "sells": sells,
                "total": len(relevant), "recent": recent_str}


def _neutral() -> dict:
    return {"signal": "neutral", "score": 0, "buys": 0, "sells": 0, "total": 0, "recent": []}
