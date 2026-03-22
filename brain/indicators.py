import pandas as pd
import numpy as np


def ema(prices: pd.Series, period: int) -> pd.Series:
    """Exponential Moving Average."""
    return prices.ewm(span=period, adjust=False).mean()


def sma(prices: pd.Series, period: int) -> pd.Series:
    """Simple Moving Average."""
    return prices.rolling(window=period).mean()


def rsi(prices: pd.Series, period: int = 14) -> pd.Series:
    """
    Relative Strength Index.
    - Above 70: overbought (sell signal)
    - Below 30: oversold (buy signal)
    - 40-60: neutral zone
    """
    delta = prices.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)

    avg_gain = gain.ewm(com=period - 1, adjust=False).mean()
    avg_loss = loss.ewm(com=period - 1, adjust=False).mean()

    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def ema_crossover(prices: pd.Series, fast: int = 12, slow: int = 26) -> dict:
    """
    Detects EMA crossover events and current trend direction.

    Returns:
        trend: 'up' or 'down'
        bullish_crossover: True when fast EMA crosses above slow EMA (buy signal)
        bearish_crossunder: True when fast EMA crosses below slow EMA (sell signal)
        fast_ema: current fast EMA value
        slow_ema: current slow EMA value
    """
    fast_ema = ema(prices, fast)
    slow_ema = ema(prices, slow)
    diff = fast_ema - slow_ema

    bullish_crossover = bool((diff.iloc[-1] > 0) and (diff.iloc[-2] <= 0))
    bearish_crossunder = bool((diff.iloc[-1] < 0) and (diff.iloc[-2] >= 0))
    trend = "up" if diff.iloc[-1] > 0 else "down"

    return {
        "trend": trend,
        "bullish_crossover": bullish_crossover,
        "bearish_crossunder": bearish_crossunder,
        "fast_ema": float(fast_ema.iloc[-1]),
        "slow_ema": float(slow_ema.iloc[-1]),
        "diff": float(diff.iloc[-1]),
        "diff_prev": float(diff.iloc[-2]),
    }


def plateau_detected(prices: pd.Series, days: int = 10, range_pct: float = 0.02) -> bool:
    """
    Returns True when a stock has gone sideways — price stuck in a tight range,
    momentum stalled. Used by PLATEAU mode to exit before a drop comes.

    days      — how many recent days to measure
    range_pct — max price range (as % of low) to qualify as a plateau
                e.g. 0.02 = price hasn't moved more than 2% in the last N days
    """
    if len(prices) < days:
        return False
    recent = prices.iloc[-days:]
    price_range = (recent.max() - recent.min()) / recent.min()
    return bool(price_range < range_pct)


def surge_exit_approaching(
    prices: pd.Series,
    rsi_series: pd.Series,
    trend: dict,
    rsi_threshold: float = 75.0,
) -> bool:
    """
    Returns True when multiple signs suggest the surge is about to end.
    Used by SURGE mode to hold longer but exit before the peak turns.

    Requires ALL three conditions:
    1. RSI is very high (overbought territory above threshold)
    2. EMA spread is narrowing (trend losing steam)
    3. Price momentum is slowing (last 3 days smaller moves than prior 3)
    """
    current_rsi = float(rsi_series.iloc[-1])
    if current_rsi <= rsi_threshold:
        return False

    # EMA spread shrinking — fast EMA pulling back toward slow EMA
    ema_spread_shrinking = trend["diff"] < trend["diff_prev"]

    # Momentum slowing — average move in last 3 days smaller than 3 days before
    if len(prices) >= 6:
        recent_moves = prices.iloc[-3:].diff().abs().mean()
        prior_moves = prices.iloc[-6:-3].diff().abs().mean()
        momentum_slowing = recent_moves < prior_moves
    else:
        momentum_slowing = False

    return ema_spread_shrinking and momentum_slowing
