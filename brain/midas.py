import os
import time
import logging
from datetime import datetime, timedelta
from dotenv import load_dotenv

from .data_feeds import get_price_history, get_intraday_history, get_current_price
from .signal_engine import generate_signal, BUY, SELL, STYLE_SWING, STYLE_DAY
from .fund_manager import FundManager, FLOOR_FIXED, FLOOR_TRAILING
from .trader import Trader
from .notifier import notify_floor_hit
from .sms import notify_floor_hit_sms, verify_2fa
from .politician import PoliticianTracker
from .youtube import YouTubeTracker

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("midas.log", encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger("Midas")


class Midas:
    """
    The brain of Project Midas.

    Orchestrates the full trading loop:
    1. Sync fund state with live Alpaca account
    2. Check fund health (floor protection)
    3. For each ticker in the watchlist:
       a. Pull price history
       b. Get current price
       c. Generate signal (trend + momentum + sentiment)
       d. Execute BUY or SELL via Alpaca (paper or live)
    4. Sleep, repeat

    paper=True  → Alpaca paper trading (fake money, real market data) — safe for testing
    paper=False → Live trading (real money) — only when ready
    """

    def __init__(
        self,
        watchlist: list,
        starting_balance: float,
        floor_amount: float,
        entry_point: float,
        deploy_fraction: float = 0.10,
        cycle_interval: int = 300,
        paper: bool = True,
        reinvest_percent: float = 0.75,
        liquid_reserve_percent: float = 0.25,
        reentry_dip_percent: float = 0.04,
        watcher_expiry_days: int = 60,
        run_duration_days: int = None,
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
        floor_mode: str = "FIXED",
        trailing_floor_pct: float = 0.15,
        notify_email: str = None,
        notify_email_password: str = None,
        twilio_sid: str = None,
        twilio_token: str = None,
        twilio_from: str = None,
        twilio_to: str = None,
        enable_2fa: bool = True,
        layer4_enabled: bool = True,
        layer4_lookback_days: int = 90,
        performance_fee: float = 0.20,
        layer5_enabled: bool = True,
        layer5_lookback_days: int = 7,
        trading_style: str = "SWING",
        day_close_buffer_min: int = 10,
    ):
        self.watchlist = watchlist
        self.deploy_fraction = deploy_fraction
        self.cycle_interval = cycle_interval
        self.reinvest_percent = reinvest_percent
        self.liquid_reserve_percent = liquid_reserve_percent
        self.watcher_expiry_days = watcher_expiry_days
        self.stop_at = (datetime.now() + timedelta(days=run_duration_days)) if run_duration_days else None
        self.trading_mode = trading_mode
        self.plateau_days = plateau_days
        self.plateau_range_pct = plateau_range_pct
        self.active_reentry_dip = slump_reentry_pct if trading_mode == "PLATEAU" else reentry_dip_percent
        self.trade_frequency = trade_frequency
        self.confidence_min_scale = confidence_min_scale
        self.confidence_max_scale = confidence_max_scale
        self.max_trades_month = max_trades_month
        self.notify_email = notify_email
        self.notify_email_password = notify_email_password
        self.twilio_sid   = twilio_sid
        self.twilio_token = twilio_token
        self.twilio_from  = twilio_from
        self.twilio_to    = twilio_to
        self.enable_2fa   = enable_2fa
        self.politician_tracker = PoliticianTracker(lookback_days=layer4_lookback_days) if layer4_enabled else None

        youtube_key = os.getenv("YOUTUBE_API_KEY")
        if layer5_enabled and youtube_key:
            self.youtube_tracker = YouTubeTracker(api_key=youtube_key, lookback_days=layer5_lookback_days)
        else:
            self.youtube_tracker = None
            if layer5_enabled and not youtube_key:
                log.info("Layer 5 disabled — YOUTUBE_API_KEY not set in .env")

        self.trading_style = trading_style
        self.day_close_buffer_min = day_close_buffer_min

        self.fund = FundManager(
            starting_balance=starting_balance,
            floor_amount=floor_amount,
            entry_point=entry_point,
            floor_mode=floor_mode,
            trailing_floor_pct=trailing_floor_pct,
            allocation_mode=allocation_mode,
            allocations=allocations or {},
            performance_fee=performance_fee,
        )

        alpaca_key = os.getenv("ALPACA_API_KEY")
        alpaca_secret = os.getenv("ALPACA_SECRET_KEY")
        self.finnhub_key = os.getenv("FINNHUB_API_KEY")

        if not all([alpaca_key, alpaca_secret, self.finnhub_key]):
            raise EnvironmentError(
                "Missing API keys. Set ALPACA_API_KEY, ALPACA_SECRET_KEY, "
                "and FINNHUB_API_KEY in your .env file."
            )

        self.alpaca_key = alpaca_key
        self.alpaca_secret = alpaca_secret

        self.trader = Trader(alpaca_key, alpaca_secret, paper=paper)

    # -------------------------------------------------------------------------
    # Main loop
    # -------------------------------------------------------------------------

    def run(self):
        """Run Midas until stopped or run_duration_days is reached."""
        # 2FA check — verify identity before trading starts
        twilio_ready = all([self.twilio_sid, self.twilio_token, self.twilio_from, self.twilio_to])
        if self.enable_2fa and twilio_ready:
            verified = verify_2fa(
                to=self.twilio_to,
                from_=self.twilio_from,
                account_sid=self.twilio_sid,
                auth_token=self.twilio_token,
            )
            if not verified:
                log.error("2FA failed — Midas will not start.")
                return
        elif self.enable_2fa and not twilio_ready:
            log.info("2FA skipped — Twilio credentials not configured.")

        if self.stop_at:
            log.info(f"Midas started. Will run until {self.stop_at.strftime('%Y-%m-%d %H:%M')}.")
        else:
            log.info("Midas started in passive income mode (runs until manually stopped).")
        self._sync_from_alpaca()
        while True:
            if self.stop_at and datetime.now() >= self.stop_at:
                log.info("Run duration reached — Midas is stopping. Closing all positions.")
                self._close_all_positions()
                break
            try:
                self._cycle()
            except Exception as e:
                log.error(f"Unhandled error in cycle: {e}")
            log.info(f"Sleeping {self.cycle_interval}s until next cycle...")
            time.sleep(self.cycle_interval)

    # -------------------------------------------------------------------------
    # Single cycle
    # -------------------------------------------------------------------------

    def _cycle(self):
        """One full analysis pass across the watchlist."""
        log.info("=== Cycle start ===")

        # ── Day trading: market-hours guard ───────────────────────────────────
        if self.trading_style == STYLE_DAY:
            clock = self.trader.get_clock()
            if not clock["is_open"]:
                log.info("Market closed — skipping cycle (day trading mode).")
                return
            # Auto-close all positions before market close
            from datetime import timezone
            now_utc = datetime.now(timezone.utc)
            mins_to_close = (clock["next_close"] - now_utc).total_seconds() / 60
            if mins_to_close <= self.day_close_buffer_min:
                log.info(
                    f"End of day — {mins_to_close:.1f} min to close. "
                    f"Closing all positions to avoid overnight risk."
                )
                self._close_all_positions()
                return

        # Sync local fund state with Alpaca's actual account on each cycle
        self._sync_from_alpaca()

        # Update high water mark with current total value
        self.fund.update_peak(self.fund.total_value)

        s = self.fund.status()
        floor_label = f"${s['floor_amount']} ({s['floor_mode']})"
        if s["floor_mode"] == "TRAILING":
            floor_label += f" | Peak: ${s['high_water_mark']}"
        log.info(
            f"Fund: ${s['total_value']} total | "
            f"${s['liquid_balance']} liquid | "
            f"${s['invested']} invested | "
            f"Floor: {floor_label}"
        )

        # Floor check — hard override, always runs first
        if self.fund.floor_hit:
            log.warning("FLOOR HIT — emergency stop triggered.")
            positions_closed = list(self.fund.state["positions"].keys())
            self.fund.trigger_floor()
            self._close_all_positions()
            notify_floor_hit(
                to_email=self.notify_email,
                from_email=self.notify_email,
                password=self.notify_email_password,
                fund_value=self.fund.total_value,
                floor_amount=self.fund.floor_amount,
                positions_closed=positions_closed,
                total_recovered=self.fund.state["balance"],
                starting_balance=self.fund.starting_balance,
            )
            if all([self.twilio_sid, self.twilio_token, self.twilio_from, self.twilio_to]):
                notify_floor_hit_sms(
                    to=self.twilio_to,
                    from_=self.twilio_from,
                    account_sid=self.twilio_sid,
                    auth_token=self.twilio_token,
                    fund_value=self.fund.total_value,
                    floor_amount=self.fund.floor_amount,
                    positions_closed=positions_closed,
                )
            return

        if self.fund.state["paused"]:
            log.info("Trading paused (floor was hit). Call fund.resume() to restart.")
            return

        # ── Phase 1: Fetch all data and generate signals ──────────────────────
        ticker_data = {}
        for ticker in self.watchlist:
            try:
                if self.trading_style == STYLE_DAY:
                    history = get_intraday_history(ticker, self.alpaca_key, self.alpaca_secret)
                else:
                    history = get_price_history(ticker, self.alpaca_key, self.alpaca_secret)
                current_price = get_current_price(ticker, self.alpaca_key, self.alpaca_secret)
                signal = generate_signal(
                    ticker, history, self.finnhub_key,
                    mode=self.trading_mode,
                    plateau_days=self.plateau_days,
                    plateau_range_pct=self.plateau_range_pct,
                    trade_frequency=self.trade_frequency,
                    politician_tracker=self.politician_tracker,
                    youtube_tracker=self.youtube_tracker,
                    trading_style=self.trading_style,
                )
                ticker_data[ticker] = {"price": current_price, "signal": signal, "watcher_fired": False}
                pol_str = ""
                if signal["politician_signal"]:
                    pol_str = f" | Congress: {signal['politician_signal']} ({signal['politician_buys']}B/{signal['politician_sells']}S)"
                yt_str = ""
                if signal["youtube_signal"]:
                    yt_str = f" | YouTube: {signal['youtube_signal']} ({signal['youtube_videos']} videos)"
                log.info(
                    f"[{ticker}] ${current_price:.2f} | "
                    f"Signal: {signal['signal']} | "
                    f"Confidence: {signal['confidence']:.0f}/100 | "
                    f"RSI: {signal['rsi']} | "
                    f"Trend: {signal['trend']} | "
                    f"Sentiment: {signal['sentiment']}"
                    + pol_str
                    + yt_str
                    + (f" | Reason: {signal['sell_reason']}" if signal.get("sell_reason") else "")
                )
            except Exception as e:
                log.error(f"[{ticker}] Error fetching data: {e}")

        # ── Phase 2: Re-entry watchers (pre-committed capital, always runs) ───
        for ticker, data in ticker_data.items():
            try:
                self.fund.update_watcher_high(ticker, data["price"])
                reinvest_amount = self.fund.check_reentry(ticker, data["price"], self.active_reentry_dip)
                if reinvest_amount > 0 and self.fund.can_invest:
                    order = self.trader.buy(ticker, reinvest_amount)
                    if order["status"] not in ("rejected", "error"):
                        self.fund.deploy(ticker, reinvest_amount)
                        log.info(f"[{ticker}] RE-ENTRY — ${reinvest_amount:.2f} at dip (${data['price']:.2f})")
                    data["watcher_fired"] = True
            except Exception as e:
                log.error(f"[{ticker}] Watcher error: {e}")

        # ── Phase 3: Sells (always allowed, no budget gate) ───────────────────
        for ticker, data in ticker_data.items():
            if data["watcher_fired"]:
                continue
            try:
                if data["signal"]["signal"] == SELL and ticker in self.fund.state["positions"]:
                    result = self.trader.sell_all(ticker)
                    if result["status"] != "no_position":
                        proceeds = result["proceeds"]
                        gain = self.fund.exit_position(ticker, proceeds)
                        self.fund.add_watcher(ticker, proceeds, data["price"], self.reinvest_percent, self.watcher_expiry_days)
                        fee_str = ""
                        if gain > 0:
                            fee = round(gain * self.fund.performance_fee, 2)
                            fees_total = self.fund.state.get("fees_owed", 0.0)
                            fee_str = f" | Fee: ${fee:.2f} (total owed: ${fees_total:.2f})"
                        log.info(
                            f"[{ticker}] SELL confirmed — ${proceeds:.2f} returned | Gain: ${gain:.2f}"
                            + fee_str +
                            f" | Watching for re-entry (reinvest ${proceeds * self.reinvest_percent:.2f} at next dip)"
                        )
            except Exception as e:
                log.error(f"[{ticker}] Sell error: {e}")

        # ── Phase 4: Priority buys — sorted by confidence, highest first ──────
        buy_candidates = [
            (ticker, data) for ticker, data in ticker_data.items()
            if not data["watcher_fired"]
            and data["signal"]["signal"] == BUY
            and self.fund.can_invest
        ]
        buy_candidates.sort(key=lambda x: x[1]["signal"]["confidence"], reverse=True)

        if buy_candidates:
            log.info(f"Priority wallet — {len(buy_candidates)} buy candidate(s): " +
                     ", ".join(f"{t}({d['signal']['confidence']:.0f})" for t, d in buy_candidates))

        for ticker, data in buy_candidates:
            if not self.fund.can_invest:
                break
            budget = self.fund.budget_remaining(self.max_trades_month)
            if budget is not None and budget <= 0:
                log.info(f"Monthly trade budget reached ({self.max_trades_month}) — skipping remaining buys")
                break
            try:
                signal = data["signal"]
                conf_scale = self.confidence_min_scale + (self.confidence_max_scale - self.confidence_min_scale) * (signal["confidence"] / 100)
                amount = self.fund.deployable_balance * self.deploy_fraction * conf_scale
                cap = self.fund.allocation_cap(ticker, self.fund.total_value)
                if cap is not None:
                    if cap <= 0:
                        log.info(f"[{ticker}] BUY skipped — allocation limit reached ({self.fund.allocation_mode})")
                        continue
                    amount = min(amount, cap)
                order = self.trader.buy(ticker, amount)
                if order["status"] not in ("rejected", "error"):
                    self.fund.deploy(ticker, amount)
                    self.fund.record_monthly_trade()
                    budget_str = f" | Budget: {budget - 1} left" if budget is not None else ""
                    log.info(f"[{ticker}] BUY confirmed — ${amount:.2f} deployed (conf: {signal['confidence']:.0f}/100, scale: {conf_scale:.2f}x){budget_str}")
            except Exception as e:
                log.error(f"[{ticker}] Buy error: {e}")

        log.info("=== Cycle end ===")

    # -------------------------------------------------------------------------
    # Account sync
    # -------------------------------------------------------------------------

    def _sync_from_alpaca(self):
        """
        Pull live account state from Alpaca and reconcile with local fund state.
        This keeps the fund manager accurate even if orders were filled
        between cycles or the app was restarted.
        """
        try:
            status = self.trader.sync_status()

            if status["trading_blocked"] or status["account_blocked"]:
                log.warning("Alpaca account is blocked — check your account status.")

            # Update local balance to match actual Alpaca cash
            self.fund.state["balance"] = status["cash"]

            # Sync open positions to match Alpaca's actual positions
            self.fund.state["positions"] = status["positions"]

            # Recalculate invested as sum of current position market values
            self.fund.state["invested"] = sum(status["positions"].values())

            self.fund._save()
            log.info(
                f"Synced from Alpaca — "
                f"Portfolio: ${status['portfolio_value']:.2f} | "
                f"Cash: ${status['cash']:.2f} | "
                f"Buying power: ${status['buying_power']:.2f}"
            )
        except Exception as e:
            log.error(f"Failed to sync from Alpaca: {e}")

    # -------------------------------------------------------------------------
    # Emergency close
    # -------------------------------------------------------------------------

    def _close_all_positions(self):
        """Close every open position via Alpaca immediately (floor triggered)."""
        positions = list(self.fund.state["positions"].keys())
        if not positions:
            return
        log.warning(f"Emergency close — closing all positions: {positions}")
        for ticker in positions:
            try:
                result = self.trader.sell_all(ticker)
                proceeds = result.get("proceeds", 0.0)
                gain = self.fund.exit_position(ticker, proceeds)
                log.warning(f"[{ticker}] Emergency closed — ${proceeds:.2f} | Gain: ${gain:.2f}")
            except Exception as e:
                log.error(f"[{ticker}] Failed to emergency close: {e}")
