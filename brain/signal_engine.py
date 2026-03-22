import pandas as pd
from .indicators import ema_crossover, rsi, plateau_detected, surge_exit_approaching
from .sentiment import get_sentiment

# Signal constants
BUY = "BUY"
SELL = "SELL"
HOLD = "HOLD"
FLOOR_TRIGGERED = "FLOOR_TRIGGERED"

# Trading style
STYLE_SWING = "SWING"   # Daily bars — multi-day / swing trading (default)
STYLE_DAY   = "DAY"     # 5-minute bars — intraday / day trading

# Trading modes
MODE_CLIMB   = "CLIMB"    # Hold through trend, sell on bearish signal (default)
MODE_PLATEAU = "PLATEAU"  # Exit when stock goes sideways, re-enter at bigger slump
MODE_SURGE   = "SURGE"    # Ride hard, only exit when peak is actively breaking down

# Trade frequency
FREQ_NORMAL     = "NORMAL"      # EMA crossovers only (default, fewer trades)
FREQ_ACTIVE     = "ACTIVE"      # EMA crossovers + RSI dip buying
FREQ_AGGRESSIVE = "AGGRESSIVE"  # EMA trend riding + RSI dip buying (most trades)

# EMA periods — swing (daily bars) vs day (5-minute bars)
_EMA_PERIODS_SWING = {
    FREQ_NORMAL:     (12, 26),
    FREQ_ACTIVE:     (5, 13),
    FREQ_AGGRESSIVE: (5, 13),
}
_EMA_PERIODS_DAY = {
    FREQ_NORMAL:     (9, 21),   # ~45min / ~105min on 5-min bars
    FREQ_ACTIVE:     (5, 13),   # ~25min / ~65min
    FREQ_AGGRESSIVE: (3, 9),    # ~15min / ~45min — tightest, most trades
}


def generate_signal(
    ticker: str,
    price_history: pd.DataFrame,
    finnhub_key: str,
    mode: str = MODE_CLIMB,
    plateau_days: int = 10,
    plateau_range_pct: float = 0.02,
    trade_frequency: str = FREQ_NORMAL,
    politician_tracker=None,
    youtube_tracker=None,
    trading_style: str = STYLE_SWING,
) -> dict:
    """
    Combines up to five input layers + trading mode + trade frequency into a single signal.

    Trading Style:
        SWING — daily bars, multi-day positions (default)
        DAY   — 5-minute bars, intraday positions (close before EOD)

    Modes:
        CLIMB   — sell on EMA crossunder or RSI > 70 (default, balanced)
        PLATEAU — also sells when price goes sideways before it drops
        SURGE   — holds longer, only exits when peak is actively breaking down

    Trade Frequency:
        NORMAL     — EMA crossovers only (fewer, higher-confidence trades)
        ACTIVE     — EMA crossovers + buys RSI dips
        AGGRESSIVE — EMA trend riding (no crossover needed) + RSI dip buying

    Layer 1 — Trend (EMA crossover)
    Layer 2 — Momentum (RSI)
    Layer 3 — Sentiment (Finnhub)
    Layer 4 — Congressional trades (optional, pass politician_tracker instance)
    Layer 5 — YouTube news sentiment (optional, pass youtube_tracker instance)
    """
    prices = price_history["close"]

    # Layer 1: Trend — EMA periods depend on both frequency and trading style
    periods_map = _EMA_PERIODS_DAY if trading_style == STYLE_DAY else _EMA_PERIODS_SWING
    fast_period, slow_period = periods_map.get(trade_frequency, (12, 26))
    trend = ema_crossover(prices, fast=fast_period, slow=slow_period)

    # Layer 2: Momentum
    rsi_series = rsi(prices)
    current_rsi = float(rsi_series.iloc[-1])

    # Layer 3: Sentiment
    sentiment = get_sentiment(ticker, finnhub_key)

    # Layer 4: Congressional trades (optional)
    politician = politician_tracker.get_signal(ticker) if politician_tracker else None

    # Layer 5: YouTube news sentiment (optional)
    youtube = youtube_tracker.get_signal(ticker) if youtube_tracker else None

    # Score each layer: +1 bullish, -1 bearish, 0 neutral
    trend_score = 1 if trend["trend"] == "up" else -1
    rsi_score = -1 if current_rsi > 70 else (1 if current_rsi < 30 else 0)
    sentiment_score = (
        1 if sentiment["label"] == "positive"
        else -1 if sentiment["label"] == "negative"
        else 0
    )
    politician_score = politician["score"] if politician else 0
    youtube_score    = youtube["score"] if youtube else 0
    total_score = trend_score + rsi_score + sentiment_score + politician_score + youtube_score

    # -------------------------------------------------------------------------
    # Mode-specific sell logic
    # -------------------------------------------------------------------------

    if mode == MODE_CLIMB:
        # Standard: sell on bearish crossunder, overbought RSI, or negative sentiment
        sell = (
            trend["bearish_crossunder"]
            or current_rsi > 70
            or sentiment["label"] == "negative"
        )
        sell_reason = (
            "bearish crossunder" if trend["bearish_crossunder"]
            else "RSI overbought" if current_rsi > 70
            else "negative sentiment" if sentiment["label"] == "negative"
            else None
        )

    elif mode == MODE_PLATEAU:
        # Exit on plateau OR standard bearish signals — whichever comes first
        is_plateau = plateau_detected(prices, days=plateau_days, range_pct=plateau_range_pct)
        sell = (
            is_plateau
            or trend["bearish_crossunder"]
            or current_rsi > 70
            or sentiment["label"] == "negative"
        )
        sell_reason = (
            "plateau detected" if is_plateau
            else "bearish crossunder" if trend["bearish_crossunder"]
            else "RSI overbought" if current_rsi > 70
            else "negative sentiment" if sentiment["label"] == "negative"
            else None
        )

    elif mode == MODE_SURGE:
        # Hold longer — only exit when peak is actively breaking down
        surge_breaking = surge_exit_approaching(prices, rsi_series, trend, rsi_threshold=75.0)
        sell = (
            surge_breaking
            or (trend["bearish_crossunder"] and sentiment["label"] == "negative")
        )
        sell_reason = (
            "surge peak breaking" if surge_breaking
            else "crossunder + negative sentiment" if sell
            else None
        )

    else:
        raise ValueError(f"Unknown trading mode: {mode}. Use CLIMB, PLATEAU, or SURGE.")

    # -------------------------------------------------------------------------
    # Buy logic — varies by trade frequency
    # -------------------------------------------------------------------------
    negative_sentiment = sentiment["label"] == "negative"

    if trade_frequency == FREQ_NORMAL:
        # Strict: only buy on confirmed EMA crossover
        buy = trend["bullish_crossover"] and current_rsi <= 60 and not negative_sentiment

    elif trade_frequency == FREQ_ACTIVE:
        # Buy on crossover OR when RSI dips below 40 (oversold) while trend is up
        rsi_dip = current_rsi < 40 and trend["trend"] == "up"
        buy = (
            (trend["bullish_crossover"] and current_rsi <= 60 and not negative_sentiment)
            or (rsi_dip and not negative_sentiment)
        )

    elif trade_frequency == FREQ_AGGRESSIVE:
        # Buy whenever fast EMA is above slow EMA (trend riding) OR RSI dip
        trend_riding = trend["fast_ema"] > trend["slow_ema"] and current_rsi <= 65
        rsi_dip = current_rsi < 40 and trend["trend"] == "up"
        buy = (trend_riding or rsi_dip) and not negative_sentiment

    else:
        raise ValueError(f"Unknown trade frequency: {trade_frequency}. Use NORMAL, ACTIVE, or AGGRESSIVE.")

    if buy:
        signal = BUY
    elif sell:
        signal = SELL
    else:
        signal = HOLD

    # -------------------------------------------------------------------------
    # Confidence score (0-100) — how strong is the buy signal?
    # Drives deploy size: high confidence = larger position.
    # Weights adjust dynamically based on which optional layers are active.
    # -------------------------------------------------------------------------
    # Layer 1+2 components (always present)
    rsi_conf     = max(0.0, min(100.0, (65.0 - current_rsi) / 35.0 * 100.0))
    spread_pct   = max(0.0, (trend["fast_ema"] - trend["slow_ema"]) / trend["slow_ema"] * 100.0) if trend["slow_ema"] > 0 else 0.0
    spread_conf  = min(100.0, spread_pct * 50.0)
    # Layer 3 component (always present)
    sentiment_conf = 100.0 if sentiment["label"] == "positive" else 50.0 if sentiment["label"] == "neutral" else 0.0
    # Layer 4 component (optional)
    politician_conf = (100.0 if politician["score"] == 1 else 50.0 if politician["score"] == 0 else 0.0) if politician else None
    # Layer 5 component (optional)
    youtube_conf    = (100.0 if youtube["score"] == 1 else 50.0 if youtube["score"] == 0 else 0.0) if youtube else None

    # Dynamic weights: cores take 80%, sentiment 20%, optional layers each steal 10%
    optional_count = sum(1 for x in (politician_conf, youtube_conf) if x is not None)
    core_weight      = 0.40 - optional_count * 0.05   # 40% → 35% → 30% per core layer
    sentiment_weight = 0.20
    optional_weight  = 0.10

    confidence = rsi_conf * core_weight + spread_conf * core_weight + sentiment_conf * sentiment_weight
    if politician_conf is not None:
        confidence += politician_conf * optional_weight
    if youtube_conf is not None:
        confidence += youtube_conf * optional_weight
    confidence = round(confidence, 1)

    return {
        "ticker": ticker,
        "signal": signal,
        "mode": mode,
        "style": trading_style,
        "frequency": trade_frequency,
        "sell_reason": sell_reason,
        "score": total_score,
        "confidence": confidence,
        # Layer 1
        "trend": trend["trend"],
        "bullish_crossover": trend["bullish_crossover"],
        "bearish_crossunder": trend["bearish_crossunder"],
        "fast_ema": round(trend["fast_ema"], 4),
        "slow_ema": round(trend["slow_ema"], 4),
        # Layer 2
        "rsi": round(current_rsi, 2),
        # Layer 3
        "sentiment": sentiment["label"],
        "sentiment_score": sentiment["score"],
        "news_buzz": sentiment["buzz"],
        "article_count": sentiment["article_count"],
        # Layer 4
        "politician_signal": politician["signal"] if politician else None,
        "politician_score":  politician["score"]  if politician else None,
        "politician_buys":   politician["buys"]   if politician else None,
        "politician_sells":  politician["sells"]  if politician else None,
        "politician_recent": politician["recent"] if politician else [],
        # Layer 5
        "youtube_signal":  youtube["signal"]  if youtube else None,
        "youtube_score":   youtube["score"]   if youtube else None,
        "youtube_bullish": youtube["bullish"] if youtube else None,
        "youtube_bearish": youtube["bearish"] if youtube else None,
        "youtube_videos":  youtube["videos"]  if youtube else None,
        "youtube_top":     youtube["top"]     if youtube else [],
    }
