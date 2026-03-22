import logging
from alpaca.trading.client import TradingClient
from alpaca.trading.requests import MarketOrderRequest
from alpaca.trading.enums import OrderSide, TimeInForce

log = logging.getLogger("Midas.Trader")


class Trader:
    """
    Wraps Alpaca's trading API for order execution.

    Set paper=True to route all orders through Alpaca's paper trading
    environment — real market data, fake money. Safe for testing.

    Set paper=False only when ready to trade with real funds.
    """

    def __init__(self, api_key: str, secret_key: str, paper: bool = True):
        self.client = TradingClient(api_key, secret_key, paper=paper)
        self.paper = paper
        mode = "PAPER" if paper else "LIVE"
        log.info(f"Trader initialized in {mode} mode.")

    # -------------------------------------------------------------------------
    # Orders
    # -------------------------------------------------------------------------

    def buy(self, ticker: str, notional_amount: float) -> dict:
        """
        Place a market buy order for a dollar amount (notional).
        Alpaca handles fractional shares automatically.

        Returns order details dict.
        """
        order = MarketOrderRequest(
            symbol=ticker,
            notional=round(notional_amount, 2),
            side=OrderSide.BUY,
            time_in_force=TimeInForce.DAY,
        )
        result = self.client.submit_order(order)
        log.info(f"[{ticker}] BUY order submitted — notional: ${notional_amount:.2f} | id: {result.id}")
        return {
            "id": str(result.id),
            "ticker": ticker,
            "side": "BUY",
            "notional": notional_amount,
            "status": str(result.status),
        }

    def sell_all(self, ticker: str) -> dict:
        """
        Close the entire open position for a ticker at market price.

        Returns a dict with estimated proceeds based on current position value.
        """
        position = self._get_position(ticker)
        if position is None:
            log.warning(f"[{ticker}] No open position to sell.")
            return {"ticker": ticker, "side": "SELL", "proceeds": 0.0, "status": "no_position"}

        proceeds = float(position.market_value)
        self.client.close_position(ticker)
        log.info(f"[{ticker}] SELL order submitted — market value: ${proceeds:.2f}")
        return {
            "ticker": ticker,
            "side": "SELL",
            "proceeds": proceeds,
            "qty": float(position.qty),
            "status": "submitted",
        }

    # -------------------------------------------------------------------------
    # Account & position info
    # -------------------------------------------------------------------------

    def account_balance(self) -> float:
        """Returns total portfolio value from Alpaca (cash + positions)."""
        account = self.client.get_account()
        return float(account.portfolio_value)

    def cash_balance(self) -> float:
        """Returns uninvested cash balance from Alpaca."""
        account = self.client.get_account()
        return float(account.cash)

    def open_positions(self) -> dict:
        """Returns {ticker: market_value} for all open positions."""
        positions = self.client.get_all_positions()
        return {p.symbol: float(p.market_value) for p in positions}

    def _get_position(self, ticker: str):
        """Returns the Alpaca position object for a ticker, or None if not held."""
        try:
            return self.client.get_open_position(ticker)
        except Exception:
            return None

    # -------------------------------------------------------------------------
    # Account sync
    # -------------------------------------------------------------------------

    def get_clock(self) -> dict:
        """
        Returns current market clock from Alpaca.
        Use this to check if the market is open and when it closes.
        Handles holidays, early closes, and weekends automatically.
        """
        clock = self.client.get_clock()
        return {
            "is_open": clock.is_open,
            "next_open": clock.next_open,
            "next_close": clock.next_close,
        }

    def sync_status(self) -> dict:
        """
        Pull live account state from Alpaca.
        Use this to reconcile local fund state with actual brokerage state.
        """
        account = self.client.get_account()
        positions = self.open_positions()
        return {
            "portfolio_value": float(account.portfolio_value),
            "cash": float(account.cash),
            "buying_power": float(account.buying_power),
            "positions": positions,
            "trading_blocked": account.trading_blocked,
            "account_blocked": account.account_blocked,
        }
