import os
import logging
import pandas as pd
from datetime import datetime, timedelta
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame
from .indicators import ema_crossover, rsi, plateau_detected, surge_exit_approaching

log = logging.getLogger("Midas.Backtest")


class Backtest:
    """
    Simulates Mitas trading logic against historical price data.

    - No API keys required
    - No live market connection
    - Uses Yahoo Finance for free historical OHLCV data
    - Sentiment is set to neutral (no Finnhub in simulation)
    - Replays history day by day and records every trade decision

    After run() completes, check .results for the full report.
    """

    def __init__(
        self,
        watchlist: list,
        start_date: str,               # "YYYY-MM-DD"
        end_date: str,                 # "YYYY-MM-DD"
        starting_balance: float,
        floor_amount: float,
        entry_point: float,
        deploy_fraction: float = 0.10,
        warmup_days: int = 30,
        reinvest_percent: float = 0.75,
        reentry_dip_percent: float = 0.04,
        watcher_expiry_days: int = 60,
        trading_mode: str = "CLIMB",
        plateau_days: int = 10,
        plateau_range_pct: float = 0.02,
        slump_reentry_pct: float = 0.08,
        trade_frequency: str = "NORMAL",
        confidence_min_scale: float = 0.5,
        confidence_max_scale: float = 2.0,
        max_trades_month: int = None,
        allocation_mode: str = "FREE_RANGE",
        allocations: dict = None,
        floor_mode: str = "FIXED",         # FIXED | TRAILING | LOCKED | PRINCIPAL
        trailing_floor_pct: float = 0.15,  # TRAILING: floor stays this % below peak
        # Cost & tax model
        # Costs are deducted from the fund at execution time so reinvestment
        # capital and compounding reflect real after-friction capital.
        # Tax is applied trade-by-trade (conservative model — treats every gain
        # as short-term ordinary income regardless of hold duration).
        commission: float = 0.0,       # $ flat fee per side (buy or sell)
        spread_pct: float = 0.0,       # bid-ask spread per side as % of trade value
        tax_rate: float = 0.0,         # short-term capital gains rate (0 = gross only)
    ):
        self.watchlist = watchlist
        self.start_date = start_date
        self.end_date = end_date
        self.starting_balance = starting_balance
        self.floor_amount = floor_amount
        self.entry_point = entry_point
        self.deploy_fraction = deploy_fraction
        self.warmup_days = warmup_days
        self.reinvest_percent = reinvest_percent
        self.reentry_dip_percent = reentry_dip_percent
        self.watcher_expiry_days = watcher_expiry_days
        self.trading_mode = trading_mode
        self.plateau_days = plateau_days
        self.plateau_range_pct = plateau_range_pct
        self.active_reentry_dip = slump_reentry_pct if trading_mode == "PLATEAU" else reentry_dip_percent
        self.trade_frequency = trade_frequency
        self.confidence_min_scale = confidence_min_scale
        self.confidence_max_scale = confidence_max_scale
        self.max_trades_month = max_trades_month
        self.allocation_mode = allocation_mode
        self.allocations = allocations or {}
        self.floor_mode = floor_mode if floor_mode else "FIXED"
        self.trailing_floor_pct = trailing_floor_pct
        self._peak_value = starting_balance  # for TRAILING mode
        self.commission  = commission
        self.spread_pct  = spread_pct
        self.tax_rate    = tax_rate

        # PRINCIPAL mode: lock starting balance as the floor
        if self.floor_mode == "PRINCIPAL":
            self.floor_amount = starting_balance

        # Running cost/tax accumulators
        self.total_transaction_costs = 0.0
        self.total_tax_paid          = 0.0

        # Monthly trade tracking for backtest simulation
        self._current_sim_month = None
        self._monthly_trade_count = 0
        # EMA periods per frequency
        self.ema_fast, self.ema_slow = {"NORMAL": (12, 26), "ACTIVE": (5, 13), "AGGRESSIVE": (5, 13)}.get(trade_frequency, (12, 26))

        # Fund state
        self.balance = starting_balance
        self.positions = {}     # ticker -> {"shares": float, "cost_basis": float}
        self.watchers = {}      # ticker -> {"reinvest_amount", "sell_price", "post_sell_high", "days_watching"}
        self.paused = False

        # Trade log
        self.trades = []
        self.daily_snapshots = []
        self.signal_timeline = []   # per-day direction + confidence for the practice grid

    # -------------------------------------------------------------------------
    # Run
    # -------------------------------------------------------------------------

    def run(self):
        """Download historical data and replay day by day."""
        print(f"\n{'='*60}")
        print(f"  Project Mitas — Backtest")
        print(f"  Tickers   : {', '.join(self.watchlist)}")
        print(f"  Period    : {self.start_date} to {self.end_date}")
        print(f"  Fund      : ${self.starting_balance:.2f} | Floor: ${self.floor_amount:.2f}")
        print(f"  Mode      : {self.trading_mode} | Frequency: {self.trade_frequency}")
        print(f"{'='*60}\n")

        # Pull full history (including warmup) from Yahoo Finance
        fetch_start = (
            datetime.strptime(self.start_date, "%Y-%m-%d") - timedelta(days=self.warmup_days)
        ).strftime("%Y-%m-%d")

        all_data = {}
        for ticker in self.watchlist:
            print(f"Downloading {ticker}...")
            df = self._download(ticker, fetch_start, self.end_date)
            if df.empty:
                print(f"  WARNING: No data returned for {ticker}, skipping.")
                continue
            all_data[ticker] = df

        if not all_data:
            print("No data available. Aborting backtest.")
            return

        # Build list of trading days in the actual test range (after warmup)
        sample_ticker = list(all_data.keys())[0]
        trading_days = all_data[sample_ticker].loc[self.start_date:self.end_date].index.tolist()

        print(f"\nSimulating {len(trading_days)} trading days...\n")

        for day in trading_days:
            self._simulate_day(day, all_data)

        self.results = self._build_report()
        self._print_report(self.results)

    # -------------------------------------------------------------------------
    # Data (Alpaca historical — replaces the dead yfinance feed)
    # -------------------------------------------------------------------------

    def _download(self, ticker: str, fetch_start: str, end: str) -> pd.DataFrame:
        """Daily closes for [fetch_start, end] from Alpaca. Returns a DataFrame
        with a single 'Close' column on a tz-naive daily DatetimeIndex — the same
        shape the simulator expects. Needs free ALPACA_API_KEY/SECRET in .env."""
        key = os.getenv("ALPACA_API_KEY")
        secret = os.getenv("ALPACA_SECRET_KEY")
        if not (key and secret):
            raise RuntimeError(
                "Backtest needs ALPACA_API_KEY and ALPACA_SECRET_KEY in your .env "
                "(free paper keys work for historical data). yfinance is no longer used."
            )
        client = StockHistoricalDataClient(key, secret)
        req = StockBarsRequest(
            symbol_or_symbols=ticker,
            timeframe=TimeFrame.Day,
            start=datetime.strptime(fetch_start, "%Y-%m-%d"),
            end=datetime.strptime(end, "%Y-%m-%d"),
        )
        try:
            bars = client.get_stock_bars(req)
        except Exception as e:
            log.warning(f"Alpaca download failed for {ticker}: {e}")
            return pd.DataFrame()
        df = getattr(bars, "df", None)
        if df is None or df.empty:
            return pd.DataFrame()
        df = df.reset_index()
        if "symbol" in df.columns:
            df = df[df["symbol"] == ticker]
        if df.empty or "close" not in df.columns or "timestamp" not in df.columns:
            return pd.DataFrame()
        idx = pd.to_datetime(df["timestamp"], utc=True).dt.tz_localize(None).dt.normalize()
        out = pd.DataFrame({"Close": df["close"].astype(float).values}, index=idx)
        out.index.name = "Date"
        return out.sort_index()

    # -------------------------------------------------------------------------
    # Single day simulation
    # -------------------------------------------------------------------------

    def _simulate_day(self, day: pd.Timestamp, all_data: dict):
        """Simulate one trading day across all tickers."""

        # Floor check — update trailing floor if enabled
        total_value = self._total_value(day, all_data)
        if self.floor_mode == "TRAILING" and total_value > self._peak_value:
            self._peak_value  = total_value
            self.floor_amount = round(self._peak_value * (1 - self.trailing_floor_pct), 2)
        if total_value <= self.floor_amount and not self.paused:
            self.paused = True
            self._close_all(day, all_data, reason="FLOOR")
            log.warning(f"{day.date()} | FLOOR HIT — trading paused. Total value: ${total_value:.2f}")

        if self.paused:
            self._snapshot(day, all_data, action="FLOOR")
            return

        # Reset monthly counter when the simulated month changes
        sim_month = day.strftime("%Y-%m")
        if sim_month != self._current_sim_month:
            self._current_sim_month = sim_month
            self._monthly_trade_count = 0

        # ── Phase 1: Build ticker data ────────────────────────────────────────
        ticker_data = {}
        for ticker, df in all_data.items():
            history = df.loc[:day]["Close"]
            if len(history) < self.warmup_days:
                continue
            ticker_data[ticker] = {
                "history": history,
                "price": float(history.iloc[-1]),
                "watcher_fired": False,
            }

        # ── Phase 2: Re-entry watchers (always runs, pre-committed capital) ───
        for ticker, data in ticker_data.items():
            if ticker not in self.watchers:
                continue
            w = self.watchers[ticker]
            w["days_watching"] += 1
            if data["price"] > w["post_sell_high"]:
                w["post_sell_high"] = data["price"]

            dip_threshold = w["post_sell_high"] * (1 - self.active_reentry_dip)
            expired = w["days_watching"] >= self.watcher_expiry_days

            if expired:
                self.balance += w["reinvest_amount"]
                del self.watchers[ticker]
                self.trades.append({
                    "date": day.date(), "ticker": ticker,
                    "action": "WATCHER EXPIRED", "price": round(data["price"], 4),
                    "shares": 0, "amount": round(w["reinvest_amount"], 2),
                })
            elif w["post_sell_high"] > w["sell_price"] and data["price"] <= dip_threshold:
                reinvest_amount = w["reinvest_amount"]
                del self.watchers[ticker]
                self.balance += reinvest_amount
                self._buy(ticker, day, data["price"], amount_override=reinvest_amount, reason="RE-ENTRY")
            data["watcher_fired"] = True

        # ── Phase 3: Signals + sells ──────────────────────────────────────────
        buy_candidates = []
        for ticker, data in ticker_data.items():
            if data["watcher_fired"]:
                continue
            result = self._signal(data["history"])
            data["result"] = result
            if result["signal"] == "SELL" and ticker in self.positions:
                self._sell(ticker, day, data["price"], reason="SIGNAL")
            elif result["signal"] == "BUY" and self._can_invest():
                buy_candidates.append((ticker, data))

        # ── Phase 4: Priority buys — sorted by confidence, highest first ──────
        buy_candidates.sort(key=lambda x: x[1]["result"]["confidence"], reverse=True)
        for ticker, data in buy_candidates:
            if not self._can_invest():
                break
            if self.max_trades_month is not None and self._monthly_trade_count >= self.max_trades_month:
                break
            self._buy(ticker, day, data["price"], confidence=data["result"]["confidence"])
            self._monthly_trade_count += 1

        # Summarize the day for the practice grid: avg confidence + what we did.
        confs = [d["result"]["confidence"] for d in ticker_data.values() if "result" in d]
        sigs  = [d["result"]["signal"] for d in ticker_data.values() if "result" in d]
        day_conf = round(sum(confs) / len(confs), 1) if confs else None
        day_action = "BUY" if "BUY" in sigs else "SELL" if "SELL" in sigs else "HOLD"
        self._snapshot(day, all_data, confidence=day_conf, action=day_action)

    # -------------------------------------------------------------------------
    # Signal (no sentiment — neutral by default in simulation)
    # -------------------------------------------------------------------------

    def _signal(self, prices: pd.Series) -> dict:
        """
        Generate signal using technical indicators + trading mode + trade frequency.
        Sentiment defaults to neutral in simulation mode (no Finnhub).
        Returns {"signal": str, "confidence": float}
        """
        trend = ema_crossover(prices, fast=self.ema_fast, slow=self.ema_slow)
        rsi_series = rsi(prices)
        current_rsi = float(rsi_series.iloc[-1])

        # Buy logic — varies by frequency
        if self.trade_frequency == "NORMAL":
            buy = trend["bullish_crossover"] and current_rsi <= 60

        elif self.trade_frequency == "ACTIVE":
            rsi_dip = current_rsi < 40 and trend["trend"] == "up"
            buy = (trend["bullish_crossover"] and current_rsi <= 60) or rsi_dip

        elif self.trade_frequency == "AGGRESSIVE":
            trend_riding = trend["fast_ema"] > trend["slow_ema"] and current_rsi <= 65
            rsi_dip = current_rsi < 40 and trend["trend"] == "up"
            buy = trend_riding or rsi_dip

        else:
            buy = trend["bullish_crossover"] and current_rsi <= 60

        if buy:
            signal = "BUY"
        else:
            # Sell logic — varies by mode
            if self.trading_mode == "CLIMB":
                sell = trend["bearish_crossunder"] or current_rsi > 70
            elif self.trading_mode == "PLATEAU":
                is_plateau = plateau_detected(prices, self.plateau_days, self.plateau_range_pct)
                sell = is_plateau or trend["bearish_crossunder"] or current_rsi > 70
            elif self.trading_mode == "SURGE":
                surge_breaking = surge_exit_approaching(prices, rsi_series, trend, rsi_threshold=75.0)
                sell = surge_breaking or trend["bearish_crossunder"]
            else:
                sell = False
            signal = "SELL" if sell else "HOLD"

        # Confidence score (no sentiment in backtest — uses neutral 50)
        rsi_conf = max(0.0, min(100.0, (65.0 - current_rsi) / 35.0 * 100.0))
        spread_pct = max(0.0, (trend["fast_ema"] - trend["slow_ema"]) / trend["slow_ema"] * 100.0) if trend["slow_ema"] > 0 else 0.0
        spread_conf = min(100.0, spread_pct * 50.0)
        confidence = round(rsi_conf * 0.5 + spread_conf * 0.5, 1)  # no sentiment weight in backtest

        return {"signal": signal, "confidence": confidence}

    # -------------------------------------------------------------------------
    # Trade execution
    # -------------------------------------------------------------------------

    def _buy(self, ticker: str, day: pd.Timestamp, price: float, amount_override: float = None, reason: str = "BUY", confidence: float = 50.0):
        if amount_override is not None:
            amount = amount_override  # re-entry: use exact reserved amount, no scaling
        else:
            conf_scale = self.confidence_min_scale + (self.confidence_max_scale - self.confidence_min_scale) * (confidence / 100)
            amount = self._deployable_balance() * self.deploy_fraction * conf_scale
            total_value = self._total_value_approx()
            cap = self._allocation_cap(ticker, total_value)
            if cap is not None:
                if cap <= 0:
                    return
                amount = min(amount, cap)
        amount = min(amount, self.balance)
        if amount < 1.0:
            return

        # Deduct buy-side transaction cost before deploying capital
        buy_cost = self.commission + amount * self.spread_pct
        buy_cost = min(buy_cost, amount)
        net_amount = amount - buy_cost         # actual capital into shares
        self.total_transaction_costs += buy_cost

        shares = net_amount / price
        self.balance -= amount                  # full amount leaves balance (cost included)

        if ticker in self.positions:
            self.positions[ticker]["shares"] += shares
            self.positions[ticker]["cost_basis"] += net_amount
        else:
            self.positions[ticker] = {"shares": shares, "cost_basis": net_amount}

        self.trades.append({
            "date": day.date(),
            "ticker": ticker,
            "action": reason,
            "price": round(price, 4),
            "shares": round(shares, 6),
            "amount": round(amount, 2),
            "cost": round(buy_cost, 4),
            "balance_after": round(self.balance, 2),
        })

    def _sell(self, ticker: str, day: pd.Timestamp, price: float, reason: str = "SIGNAL"):
        if ticker not in self.positions:
            return

        pos = self.positions.pop(ticker)
        gross_proceeds = pos["shares"] * price

        # Deduct sell-side transaction cost
        sell_cost = self.commission + gross_proceeds * self.spread_pct
        net_proceeds = gross_proceeds - sell_cost
        self.total_transaction_costs += sell_cost

        gain = net_proceeds - pos["cost_basis"]

        # Apply short-term capital gains tax to realised profit
        tax_owed = max(0.0, gain) * self.tax_rate
        after_tax_proceeds = net_proceeds - tax_owed
        self.total_tax_paid += tax_owed

        # Split after-tax, after-cost proceeds: reinvest_percent held in watcher
        reinvest_amount = round(after_tax_proceeds * self.reinvest_percent, 2)
        liquid_amount   = after_tax_proceeds - reinvest_amount
        self.balance += liquid_amount

        # Register re-entry watcher
        self.watchers[ticker] = {
            "reinvest_amount": reinvest_amount,
            "sell_price": price,
            "post_sell_high": price,
            "days_watching": 0,
        }

        self.trades.append({
            "date": day.date(),
            "ticker": ticker,
            "action": f"SELL ({reason})",
            "price": round(price, 4),
            "shares": round(pos["shares"], 6),
            "amount": round(gross_proceeds, 2),
            "cost": round(sell_cost, 4),
            "gain": round(gain, 2),
            "tax": round(tax_owed, 2),
            "gain_pct": round((gain / pos["cost_basis"]) * 100, 2) if pos["cost_basis"] else 0,
            "reinvest_reserved": reinvest_amount,
            "balance_after": round(self.balance, 2),
        })

    def _close_all(self, day: pd.Timestamp, all_data: dict, reason: str = "FLOOR"):
        for ticker in list(self.positions.keys()):
            if ticker in all_data:
                df = all_data[ticker]
                history = df.loc[:day]["Close"]
                if not history.empty:
                    self._sell(ticker, day, float(history.iloc[-1]), reason=reason)

    # -------------------------------------------------------------------------
    # Portfolio helpers
    # -------------------------------------------------------------------------

    def _total_value(self, day: pd.Timestamp, all_data: dict) -> float:
        invested = 0.0
        for ticker, pos in self.positions.items():
            if ticker in all_data:
                df = all_data[ticker]
                history = df.loc[:day]["Close"]
                if not history.empty:
                    invested += pos["shares"] * float(history.iloc[-1])
        # Include reserved reinvestment funds sitting in watchers
        reserved = sum(w["reinvest_amount"] for w in self.watchers.values())
        return self.balance + invested + reserved

    def _allocation_cap(self, ticker: str, total_value: float) -> float:
        """Returns max additional $ for this ticker, or None for FREE_RANGE."""
        if self.allocation_mode == "FREE_RANGE":
            return None
        current_cost = self.positions.get(ticker, {}).get("cost_basis", 0.0)
        if self.allocation_mode == "PERCENTAGE":
            max_allowed = total_value * self.allocations.get(ticker, 1.0)
        elif self.allocation_mode == "MANUAL":
            max_allowed = self.allocations.get(ticker, float("inf"))
        else:
            return None
        return max(0.0, max_allowed - current_cost)

    def _deployable_balance(self) -> float:
        """Capital available to deploy — in LOCKED mode, only what's above the floor."""
        if self.floor_mode == "LOCKED":
            return max(0.0, self.balance - self.floor_amount)
        return self.balance

    def _total_value_approx(self) -> float:
        """Approximate total value using cost basis (no live prices needed)."""
        invested = sum(p["cost_basis"] for p in self.positions.values())
        reserved = sum(w["reinvest_amount"] for w in self.watchers.values())
        return self.balance + invested + reserved

    def _can_invest(self) -> bool:
        return (
            not self.paused
            and self._deployable_balance() >= self.entry_point
        )

    def _snapshot(self, day: pd.Timestamp, all_data: dict, confidence=None, action="HOLD"):
        total = round(self._total_value(day, all_data), 2)
        self.daily_snapshots.append({
            "date": day.date(),
            "total_value": total,
            "cash": round(self.balance, 2),
            "open_positions": list(self.positions.keys()),
        })
        # Per-day record for the practice grid: market direction + avg confidence.
        pcts = []
        for df in all_data.values():
            h = df.loc[:day]["Close"]
            if len(h) > 1:
                prev = float(h.iloc[-2])
                if prev:
                    pcts.append(float(h.iloc[-1]) / prev - 1.0)
        avg = sum(pcts) / len(pcts) if pcts else 0.0
        self.signal_timeline.append({
            "date": str(day.date()),
            "value": total,
            "pct": round(avg * 100, 2),
            "direction": "up" if avg > 0.001 else "down" if avg < -0.001 else "flat",
            "confidence": confidence,
            "action": action,
        })

    # -------------------------------------------------------------------------
    # Report
    # -------------------------------------------------------------------------

    def _build_report(self) -> dict:
        buys  = [t for t in self.trades if t["action"] == "BUY"]
        sells = [t for t in self.trades if "SELL" in t["action"]]
        gains = [t.get("gain", 0) for t in sells]

        final_value = self.daily_snapshots[-1]["total_value"] if self.daily_snapshots else self.balance
        total_return = final_value - self.starting_balance
        total_return_pct = (total_return / self.starting_balance) * 100

        winning_trades = [g for g in gains if g > 0]
        losing_trades  = [g for g in gains if g <= 0]
        win_rate = (len(winning_trades) / len(gains) * 100) if gains else 0

        costs    = round(self.total_transaction_costs, 2)
        tax_paid = round(self.total_tax_paid, 2)

        return {
            "starting_balance": self.starting_balance,
            "final_value": round(final_value, 2),
            "total_return": round(total_return, 2),
            "total_return_pct": round(total_return_pct, 2),
            "total_transaction_costs": costs,
            "total_tax_paid": tax_paid,
            "net_return": round(total_return, 2),          # already net of costs+tax (baked in)
            "total_trades": len(self.trades),
            "buys": len(buys),
            "sells": len(sells),
            "win_rate_pct": round(win_rate, 1),
            "avg_gain": round(sum(gains) / len(gains), 2) if gains else 0,
            "best_trade": round(max(gains), 2) if gains else 0,
            "worst_trade": round(min(gains), 2) if gains else 0,
            "floor_triggered": self.paused,
            "trade_log": self.trades,
            "daily_snapshots": self.daily_snapshots,
            "signal_timeline": self.signal_timeline,
        }

    def _print_report(self, r: dict):
        costs    = r['total_transaction_costs']
        tax_paid = r['total_tax_paid']
        show_costs = self.commission > 0 or self.spread_pct > 0
        show_tax   = self.tax_rate > 0

        print(f"\n{'='*60}")
        print(f"  BACKTEST RESULTS")
        print(f"{'='*60}")
        print(f"  Starting balance : ${r['starting_balance']:.2f}")
        print(f"  Final value      : ${r['final_value']:.2f}")
        print(f"  Total return     : ${r['total_return']:.2f}  ({r['total_return_pct']:+.2f}%)")
        if show_costs or show_tax:
            print(f"{'-'*60}")
        if show_costs:
            print(f"  Transaction costs: -${costs:.2f}  (spread {self.spread_pct*100:.2f}%/side + ${self.commission:.2f} commission)")
        if show_tax:
            print(f"  Tax paid         : -${tax_paid:.2f}  ({int(self.tax_rate*100)}% short-term rate)")
        if show_costs or show_tax:
            print(f"  NOTE: return above is already net of costs & tax")
        print(f"{'-'*60}")
        print(f"  Total trades     : {r['total_trades']}  (Buys: {r['buys']} | Sells: {r['sells']})")
        print(f"  Win rate         : {r['win_rate_pct']:.1f}%")
        print(f"  Avg gain/trade   : ${r['avg_gain']:.2f}")
        print(f"  Best trade       : ${r['best_trade']:.2f}")
        print(f"  Worst trade      : ${r['worst_trade']:.2f}")
        print(f"  Floor triggered  : {'YES' if r['floor_triggered'] else 'No'}")
        print(f"{'='*60}")

        if r["trade_log"]:
            print(f"\n  TRADE LOG")
            print(f"  {'Date':<12} {'Ticker':<6} {'Action':<16} {'Price':>8} {'Amount':>9} {'Gain':>8}")
            print(f"  {'-'*12} {'-'*6} {'-'*16} {'-'*8} {'-'*9} {'-'*8}")
            for t in r["trade_log"]:
                gain_str = f"${t.get('gain', 0):+.2f}" if "gain" in t else ""
                print(
                    f"  {str(t['date']):<12} {t['ticker']:<6} {t['action']:<16} "
                    f"${t['price']:>7.2f} ${t['amount']:>8.2f} {gain_str:>8}"
                )
        print()
