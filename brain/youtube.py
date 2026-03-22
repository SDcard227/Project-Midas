import logging
import requests
from datetime import datetime, timedelta, timezone

log = logging.getLogger("Midas.YouTube")

SEARCH_URL    = "https://www.googleapis.com/youtube/v3/search"
CACHE_HOURS   = 2      # Refresh per-ticker YouTube data every 2 hours
MAX_RESULTS   = 15     # Videos to scan per ticker per search
LOOKBACK_DAYS = 7      # Only count videos published in the last N days

# ── Two-section scan queries ──────────────────────────────────────────────────
# Section 1 — tickers you currently HOLD: look for news that could affect the position
POSITIONS_QUERIES = ["{ticker} stock today", "{ticker} earnings", "{ticker} news"]

# Section 2 — tickers NOT held: look for breakout / momentum signals
MOVERS_QUERIES    = ["{ticker} breakout", "{ticker} squeeze", "{ticker} going up"]

# Keywords that point to a bullish sentiment in titles/descriptions
BULLISH_KEYWORDS = {
    "buy", "bullish", "long", "breakout", "rally", "surge", "upside",
    "outperform", "undervalued", "growth", "strong", "target raised",
    "upgrade", "buy signal", "all time high", "moon",
}

# Keywords that point to a bearish sentiment
BEARISH_KEYWORDS = {
    "sell", "bearish", "short", "crash", "overvalued", "avoid", "dump",
    "warning", "risk", "downside", "cut", "downgrade", "bubble",
    "sell signal", "drop", "decline", "danger",
}


class YouTubeTracker:
    """
    Layer 5 — YouTube financial news signal.

    Searches YouTube for recent videos mentioning a ticker.
    Scans titles and descriptions for bullish/bearish keywords.
    Scores each ticker based on the balance of positive vs negative coverage.

    Two scan sections:
      Section 1 — POSITIONS: tickers currently held in the fund.
                  Uses news-focused queries (today / earnings / news).
                  Detects sentiment shifts that could signal an early exit.

      Section 2 — MOVERS: watchlist tickers not currently held.
                  Uses momentum-focused queries (breakout / squeeze / going up).
                  A spike in video count = attention signal before price moves.
                  Feeds confidence as Layer 5 to weight new buy decisions higher.

    Call scan_positions(held_tickers) and scan_movers(watchlist_tickers) from
    the main trading loop. get_signal(ticker) is used by the signal engine.

    Positive coverage:  mild bullish boost to confidence.
    Negative coverage:  mild bearish drag on confidence.
    Neutral/no data:    no effect.

    Requires a free YouTube Data API v3 key:
      console.cloud.google.com → New Project → Enable YouTube Data API v3 → Create API Key
    Free quota: 10,000 units/day — each search costs 100 units (= 100 searches/day).

    Caches results per ticker for CACHE_HOURS to stay within quota limits.
    """

    def __init__(self, api_key: str, lookback_days: int = LOOKBACK_DAYS):
        self.api_key      = api_key
        self.lookback_days = lookback_days
        self._cache: dict  = {}   # ticker -> {signal, score, ...}
        self._fetched_at: dict = {}  # ticker -> datetime

    # -------------------------------------------------------------------------
    # Fetch
    # -------------------------------------------------------------------------

    def _needs_refresh(self, ticker: str) -> bool:
        last = self._fetched_at.get(ticker)
        if last is None:
            return True
        return (datetime.now() - last).total_seconds() > CACHE_HOURS * 3600

    def _fetch(self, ticker: str) -> list:
        """Search YouTube for recent videos mentioning the ticker."""
        published_after = (
            datetime.now(timezone.utc) - timedelta(days=self.lookback_days)
        ).strftime("%Y-%m-%dT%H:%M:%SZ")

        try:
            resp = requests.get(
                SEARCH_URL,
                params={
                    "part":           "snippet",
                    "q":              f"{ticker} stock",
                    "type":           "video",
                    "order":          "date",
                    "maxResults":     MAX_RESULTS,
                    "publishedAfter": published_after,
                    "key":            self.api_key,
                },
                timeout=10,
            )
            if not resp.ok:
                log.warning(f"YouTube API error for {ticker}: {resp.status_code} {resp.text[:120]}")
                return []
            return resp.json().get("items", [])
        except Exception as e:
            log.warning(f"YouTube tracker failed for {ticker}: {e}")
            return []

    # -------------------------------------------------------------------------
    # Signal
    # -------------------------------------------------------------------------

    def get_signal(self, ticker: str) -> dict:
        """
        Returns a Layer 5 signal for the given ticker.

        signal:   "positive" | "neutral" | "negative"
        score:    +1, 0, or -1
        bullish:  count of bullish keyword hits across scanned videos
        bearish:  count of bearish keyword hits across scanned videos
        videos:   total videos scanned
        top:      list of up to 3 most recent video titles
        """
        if self._needs_refresh(ticker):
            items = self._fetch(ticker)
            self._cache[ticker] = self._score(ticker, items)
            self._fetched_at[ticker] = datetime.now()

        return self._cache.get(ticker, _neutral())

    def _score(self, ticker: str, items: list) -> dict:
        bullish_hits = 0
        bearish_hits = 0
        videos       = []

        for item in items:
            snippet  = item.get("snippet", {})
            video_id = item.get("id", {}).get("videoId", "")
            title    = snippet.get("title", "")
            channel  = snippet.get("channelTitle", "")
            pub_raw  = snippet.get("publishedAt", "")
            text     = (title + " " + snippet.get("description", "")).lower()

            # Parse published date
            try:
                pub_dt  = datetime.fromisoformat(pub_raw.replace("Z", "+00:00"))
                pub_str = _time_ago(pub_dt)
            except Exception:
                pub_str = pub_raw[:10]

            # Per-video sentiment
            v_bull = sum(1 for kw in BULLISH_KEYWORDS if kw in text)
            v_bear = sum(1 for kw in BEARISH_KEYWORDS if kw in text)
            bullish_hits += v_bull
            bearish_hits += v_bear

            if title:
                videos.append({
                    "title":   title,
                    "channel": channel,
                    "ago":     pub_str,
                    "url":     f"https://www.youtube.com/watch?v={video_id}" if video_id else "",
                    "tone":    "bullish" if v_bull > v_bear else "bearish" if v_bear > v_bull else "neutral",
                })

        if bullish_hits > bearish_hits:
            signal, score = "positive", 1
        elif bearish_hits > bullish_hits:
            signal, score = "negative", -1
        else:
            signal, score = "neutral", 0

        log.info(
            f"YouTube [{ticker}] — {len(items)} videos | "
            f"{bullish_hits} bullish / {bearish_hits} bearish -> {signal}"
        )

        return {
            "signal":  signal,
            "score":   score,
            "bullish": bullish_hits,
            "bearish": bearish_hits,
            "videos":  len(items),
            "top":     [v["title"] for v in videos[:3]],
            "feed":    videos,          # full video list with metadata
        }


def _time_ago(dt: datetime) -> str:
    """Human-readable time since publication."""
    delta = datetime.now(timezone.utc) - dt
    secs  = int(delta.total_seconds())
    if secs < 3600:
        return f"{secs // 60}m ago"
    if secs < 86400:
        return f"{secs // 3600}h ago"
    return f"{secs // 86400}d ago"


    # -------------------------------------------------------------------------
    # Two-section scans
    # -------------------------------------------------------------------------

    def scan_positions(self, held_tickers: list) -> dict:
        """
        Section 1 — scan tickers you currently HOLD for news that could affect the position.
        Uses broader news queries: today / earnings / news.
        Returns dict of ticker -> signal dict.
        """
        results = {}
        for ticker in held_tickers:
            cache_key = f"pos_{ticker}"
            if cache_key not in self._fetched_at or self._needs_refresh(cache_key):
                items = []
                for q_template in POSITIONS_QUERIES:
                    query = q_template.format(ticker=ticker)
                    items += self._fetch_query(ticker, query)
                self._cache[cache_key] = self._score(ticker, items)
                self._fetched_at[cache_key] = datetime.now()
            results[ticker] = self._cache[cache_key]
        return results

    def scan_movers(self, watchlist_tickers: list, held_tickers: list = None) -> dict:
        """
        Section 2 — scan watchlist tickers NOT currently held for breakout/momentum signals.
        Uses momentum queries: breakout / squeeze / going up.
        A spike in video count is itself a signal — attention precedes price moves.
        Returns dict of ticker -> signal dict, excluding held_tickers.
        """
        held = set(held_tickers or [])
        results = {}
        for ticker in watchlist_tickers:
            if ticker in held:
                continue
            cache_key = f"mover_{ticker}"
            if cache_key not in self._fetched_at or self._needs_refresh(cache_key):
                items = []
                for q_template in MOVERS_QUERIES:
                    query = q_template.format(ticker=ticker)
                    items += self._fetch_query(ticker, query)
                self._cache[cache_key] = self._score(ticker, items)
                self._fetched_at[cache_key] = datetime.now()
            results[ticker] = self._cache[cache_key]
        return results

    def _fetch_query(self, ticker: str, query: str) -> list:
        """Fetch YouTube results for a specific search query string."""
        published_after = (
            datetime.now(timezone.utc) - timedelta(days=self.lookback_days)
        ).strftime("%Y-%m-%dT%H:%M:%SZ")
        try:
            resp = requests.get(
                SEARCH_URL,
                params={
                    "part":           "snippet",
                    "q":              query,
                    "type":           "video",
                    "order":          "date",
                    "maxResults":     MAX_RESULTS,
                    "publishedAfter": published_after,
                    "key":            self.api_key,
                },
                timeout=10,
            )
            if not resp.ok:
                log.warning(f"YouTube API error [{ticker}] query='{query}': {resp.status_code}")
                return []
            return resp.json().get("items", [])
        except Exception as e:
            log.warning(f"YouTube fetch failed [{ticker}] query='{query}': {e}")
            return []


def _neutral() -> dict:
    return {"signal": "neutral", "score": 0, "bullish": 0, "bearish": 0, "videos": 0, "top": [], "feed": []}
