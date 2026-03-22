import pandas as pd
from datetime import datetime, timedelta
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest, StockLatestQuoteRequest
from alpaca.data.timeframe import TimeFrame, TimeFrameUnit


def get_price_history(
    ticker: str,
    alpaca_key: str,
    alpaca_secret: str,
    days: int = 60,
) -> pd.DataFrame:
    """
    Fetches daily OHLCV bars for the past N days from Alpaca.

    Returns a DataFrame with columns: close, volume
    Indexed by timestamp.
    """
    client = StockHistoricalDataClient(alpaca_key, alpaca_secret)

    request = StockBarsRequest(
        symbol_or_symbols=ticker,
        timeframe=TimeFrame.Day,
        start=datetime.now() - timedelta(days=days),
        end=datetime.now(),
    )

    bars = client.get_stock_bars(request)
    df = bars.df.reset_index()

    df = df[df["symbol"] == ticker][["timestamp", "close", "volume"]].copy()
    df.set_index("timestamp", inplace=True)
    df.sort_index(inplace=True)

    return df


def get_intraday_history(
    ticker: str,
    alpaca_key: str,
    alpaca_secret: str,
    bars: int = 200,
) -> pd.DataFrame:
    """
    Fetches 5-minute OHLCV bars for intraday / day trading signals.

    200 bars = ~16 hours of market time (~2 trading days).
    Returns a DataFrame with columns: close, volume — same shape as get_price_history().
    """
    client = StockHistoricalDataClient(alpaca_key, alpaca_secret)

    request = StockBarsRequest(
        symbol_or_symbols=ticker,
        timeframe=TimeFrame(5, TimeFrameUnit.Minute),
        start=datetime.now() - timedelta(days=5),   # cast wide; we limit via bars
        end=datetime.now(),
        limit=bars,
    )

    raw = client.get_stock_bars(request)
    df = raw.df.reset_index()

    df = df[df["symbol"] == ticker][["timestamp", "close", "volume"]].copy()
    df.set_index("timestamp", inplace=True)
    df.sort_index(inplace=True)

    return df


def get_current_price(ticker: str, alpaca_key: str, alpaca_secret: str) -> float:
    """
    Returns the latest price for a ticker from Alpaca.
    Uses ask price when market is open; falls back to last bar close when closed.
    """
    client = StockHistoricalDataClient(alpaca_key, alpaca_secret)
    request = StockLatestQuoteRequest(symbol_or_symbols=ticker)
    quote = client.get_stock_latest_quote(request)
    price = float(quote[ticker].ask_price)

    # ask_price is 0 when market is closed — fall back to last daily bar close
    if price == 0.0:
        bar_request = StockBarsRequest(
            symbol_or_symbols=ticker,
            timeframe=TimeFrame.Day,
            start=datetime.now() - timedelta(days=5),
            end=datetime.now(),
            limit=1,
        )
        bars = client.get_stock_bars(bar_request)
        df = bars.df.reset_index()
        df = df[df["symbol"] == ticker]
        if not df.empty:
            price = float(df["close"].iloc[-1])

    return price
