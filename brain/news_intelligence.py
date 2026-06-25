"""
news_intelligence.py — Claude-powered news classifier (the "AI filter").

This is the core of Project Midas' news-edge concept: instead of counting
bullish/bearish keywords, it reads raw headlines with a real language model and
returns structured, market-relevant signals.

The same call powers two things:
  1. A real Layer-3 sentiment read (drop-in compatible with sentiment.get_sentiment).
  2. The firehose filter — for each headline: is it market-moving? which ticker?
     how fresh (breaking vs stale)? — which is what lets a high-volume news
     scraper surface the 3 signals hidden in 10,000 junk items.

Model: claude-haiku-4-5 — cheap and fast, the right tier for high-volume
classification. Structured outputs force clean JSON, so there is no parsing
guesswork. Prompt caching keeps the (fixed) instruction prefix near-free at scale.

Degrades gracefully: if the anthropic SDK isn't installed or ANTHROPIC_API_KEY
isn't set, get_news_signal() returns a neutral verdict so the bot keeps running —
same pattern the YouTube / Twilio layers already use.

Setup:
    pip install anthropic
    ANTHROPIC_API_KEY=...   in your .env
    ANTHROPIC_MODEL=claude-haiku-4-5   (optional override)
"""
import os
import json
import logging

log = logging.getLogger("Midas.News")

DEFAULT_MODEL = os.getenv("ANTHROPIC_MODEL", "claude-haiku-4-5")
MAX_HEADLINES_PER_CALL = 25   # keep one call cheap and well under the token cap

# Lazy import so the bot still runs if anthropic isn't installed.
try:
    import anthropic
    _SDK_OK = True
except ImportError:
    _SDK_OK = False


# Fixed instruction prefix — identical on every call, so it caches cleanly.
_SYSTEM = """You are a financial news analyst for an automated trading system.
You will be given a list of news headlines. For EACH headline, judge it purely
on its likely effect on the named company's stock, as a short-term trader would.

For each headline return:
- sentiment: "bullish", "bearish", or "neutral" for the primary ticker.
- confidence: integer 0-100, how strongly the headline supports that sentiment.
- market_moving: true only if this headline could plausibly move the stock price
  today. Routine coverage, opinion pieces, and old news are NOT market-moving.
- novelty: "breaking" (new, time-sensitive event), "developing" (follow-up on a
  known story), or "stale" (recap / evergreen / already widely known).
- tickers: stock symbols the headline is primarily about (uppercase). [] if none.
- event_type: a short tag, e.g. "earnings", "M&A", "guidance", "regulatory",
  "product", "legal", "macro", "analyst", "insider", "other".
- rationale: one short clause explaining the call.

Be skeptical. Most headlines are neutral and not market-moving. Do not invent
tickers that aren't clearly implied. Judge the headline text only."""


# JSON schema for structured outputs. Note: numeric min/max are not enforceable
# in structured-output schemas, so confidence is a plain integer (we clamp it
# ourselves below). additionalProperties:false + required are mandatory.
_SCHEMA = {
    "type": "object",
    "properties": {
        "headlines": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "sentiment": {"type": "string", "enum": ["bullish", "bearish", "neutral"]},
                    "confidence": {"type": "integer"},
                    "market_moving": {"type": "boolean"},
                    "novelty": {"type": "string", "enum": ["breaking", "developing", "stale"]},
                    "tickers": {"type": "array", "items": {"type": "string"}},
                    "event_type": {"type": "string"},
                    "rationale": {"type": "string"},
                },
                "required": [
                    "sentiment", "confidence", "market_moving",
                    "novelty", "tickers", "event_type", "rationale",
                ],
                "additionalProperties": False,
            },
        },
    },
    "required": ["headlines"],
    "additionalProperties": False,
}


def _client():
    """Return an Anthropic client, or None if unavailable (no SDK / no key)."""
    if not _SDK_OK:
        log.info("News AI disabled - `pip install anthropic` to enable.")
        return None
    if not os.getenv("ANTHROPIC_API_KEY"):
        log.info("News AI disabled - ANTHROPIC_API_KEY not set in .env.")
        return None
    return anthropic.Anthropic()


def classify_headlines(headlines: list, model: str = None) -> list:
    """
    Classify a list of headline strings.

    Returns a list of verdict dicts, one per input headline, aligned by index:
        {sentiment, confidence, market_moving, novelty, tickers, event_type, rationale}

    Returns [] if the AI layer is unavailable or the call fails — callers should
    treat an empty result as "no AI signal" and fall back, never crash.
    """
    headlines = [h for h in (headlines or []) if h and h.strip()]
    if not headlines:
        return []

    client = _client()
    if client is None:
        return []

    model = model or DEFAULT_MODEL
    verdicts = []

    # Chunk so each request stays cheap and well under the output token cap.
    for i in range(0, len(headlines), MAX_HEADLINES_PER_CALL):
        chunk = headlines[i:i + MAX_HEADLINES_PER_CALL]
        numbered = "\n".join(f"{n+1}. {h}" for n, h in enumerate(chunk))
        try:
            resp = client.messages.create(
                model=model,
                max_tokens=4000,
                # cache_control marks the fixed instructions as cacheable. It only
                # actually caches once the prefix is large enough, but it's free to
                # leave on and pays off as the instruction set grows.
                system=[{"type": "text", "text": _SYSTEM,
                         "cache_control": {"type": "ephemeral"}}],
                messages=[{"role": "user",
                           "content": f"Classify these {len(chunk)} headlines:\n{numbered}"}],
                output_config={"format": {"type": "json_schema", "schema": _SCHEMA}},
            )
            text = next((b.text for b in resp.content if b.type == "text"), "")
            items = json.loads(text).get("headlines", [])
        except Exception as e:
            log.warning(f"News AI classify failed: {e}")
            items = []

        # Pad/truncate so the output aligns 1:1 with the input chunk.
        for j in range(len(chunk)):
            v = items[j] if j < len(items) else {}
            verdicts.append(_normalize(v))

    return verdicts


def _normalize(v: dict) -> dict:
    """Clamp and default a single verdict so downstream code is safe."""
    try:
        conf = int(v.get("confidence", 0))
    except (TypeError, ValueError):
        conf = 0
    return {
        "sentiment": v.get("sentiment", "neutral"),
        "confidence": max(0, min(100, conf)),
        "market_moving": bool(v.get("market_moving", False)),
        "novelty": v.get("novelty", "stale"),
        "tickers": [str(t).upper() for t in (v.get("tickers") or [])],
        "event_type": v.get("event_type", "other"),
        "rationale": v.get("rationale", ""),
    }


def get_news_signal(ticker: str, headlines: list, model: str = None) -> dict:
    """
    Aggregate per-headline verdicts into one signal for `ticker`.

    Drop-in compatible with sentiment.get_sentiment() — returns the same
    {score, label, buzz, article_count} keys plus richer extras, so signal_engine
    can use it as Layer 3 without other changes:

        score         0.0-1.0 bullish fraction (matches Finnhub bullishPercent)
        label         "positive" | "neutral" | "negative"
        buzz          count of market-moving headlines (a real attention proxy)
        article_count headlines considered
        market_moving count of headlines flagged market-moving
        breaking      count flagged "breaking" (this is the early-signal edge)
        items         the raw verdicts (for the dashboard / first-seen tracking)

    Neutral fallback if the AI layer is off, so the bot never breaks.
    """
    verdicts = classify_headlines(headlines, model=model)
    if not verdicts:
        return {"score": 0.5, "label": "neutral", "buzz": 0,
                "article_count": len(headlines or []),
                "market_moving": 0, "breaking": 0, "items": []}

    # Only score headlines actually about this ticker (or unattributed ones).
    tk = (ticker or "").upper()
    relevant = [v for v in verdicts if not v["tickers"] or tk in v["tickers"]]
    if not relevant:
        relevant = verdicts

    # Confidence-weighted bullish fraction → 0..1, the same shape Layer 3 expects.
    bull = sum(v["confidence"] for v in relevant if v["sentiment"] == "bullish")
    bear = sum(v["confidence"] for v in relevant if v["sentiment"] == "bearish")
    total = bull + bear
    score = round(bull / total, 4) if total else 0.5

    label = "positive" if score > 0.6 else "negative" if score < 0.4 else "neutral"
    market_moving = sum(1 for v in relevant if v["market_moving"])
    breaking = sum(1 for v in relevant if v["market_moving"] and v["novelty"] == "breaking")

    return {
        "score": score,
        "label": label,
        "buzz": market_moving,
        "article_count": len(relevant),
        "market_moving": market_moving,
        "breaking": breaking,
        "items": relevant,
    }


if __name__ == "__main__":
    # Smoke test. Prints a neutral fallback if no key/SDK — that's expected and OK.
    logging.basicConfig(level=logging.INFO)
    sample = [
        "Apple beats Q3 earnings, raises full-year guidance",
        "Analyst reiterates hold rating on Apple, no change to target",
        "Apple faces new EU antitrust probe over App Store fees",
    ]
    from pprint import pprint
    pprint(get_news_signal("AAPL", sample))
