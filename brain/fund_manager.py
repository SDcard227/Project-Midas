import json
from pathlib import Path
from datetime import datetime


FLOOR_FIXED     = "FIXED"      # Floor stays at the amount you set — never moves
FLOOR_TRAILING  = "TRAILING"   # Floor rises with the fund — always X% below peak
FLOOR_LOCKED    = "LOCKED"     # Custom floor amount locked in cash — only capital above it is deployed
FLOOR_PRINCIPAL = "PRINCIPAL"  # Starting balance locked in cash forever — Midas only ever trades with gains


class FundManager:
    """
    Tracks the state of the Midas fund.

    Responsibilities:
    - Track liquid balance and invested capital
    - Enforce the floor (minimum fund value — hard stop)
    - Gate all capital deployment behind safety checks
    - Persist state to disk so it survives restarts

    Floor modes:
        FIXED    — floor is always the fixed dollar amount set at start
        TRAILING — floor rises as the fund grows, always trailing_floor_pct below peak
        LOCKED   — floor amount is permanently locked in cash, never invested
                   Midas only trades with capital above the floor — floor is mathematically unreachable
    """

    def __init__(
        self,
        starting_balance: float,
        floor_amount: float,
        entry_point: float,
        state_file: str = "fund_state.json",
        floor_mode: str = FLOOR_FIXED,
        trailing_floor_pct: float = 0.15,
        protect_principal: bool = False,
        allocation_mode: str = "FREE_RANGE",
        allocations: dict = None,
        performance_fee: float = 0.20,
    ):
        self.starting_balance = starting_balance
        # PRINCIPAL mode: floor is always the starting balance, locked in cash
        self.original_floor = starting_balance if floor_mode == FLOOR_PRINCIPAL else floor_amount
        self.entry_point = entry_point
        self.floor_mode = floor_mode
        self.trailing_floor_pct = trailing_floor_pct
        self.protect_principal = protect_principal
        self.allocation_mode = allocation_mode
        self.allocations = allocations or {}
        self.performance_fee = performance_fee

        self.state_file = Path(state_file)
        self.state = self._load_state()

    # -------------------------------------------------------------------------
    # State persistence
    # -------------------------------------------------------------------------

    def _load_state(self) -> dict:
        if self.state_file.exists():
            with open(self.state_file) as f:
                return json.load(f)
        return {
            "balance": self.starting_balance,
            "invested": 0.0,
            "positions": {},       # ticker -> cost basis
            "paused": False,
            "total_trades": 0,
            "total_gains": 0.0,
            "watchers": {},        # ticker -> re-entry watcher state
            "high_water_mark": self.starting_balance,
            "monthly_trades": 0,
            "trade_month": datetime.now().strftime("%Y-%m"),
            "trade_log": [],       # full history: [{date, ticker, action, amount, gain, notes}]
            "fees_owed": 0.0,      # total performance fee accumulated, not yet collected
            "fees_collected": 0.0, # total performance fee already paid out
            "fee_log": [],         # [{date, ticker, gain, fee, balance_after}]
        }

    def _save(self):
        with open(self.state_file, "w") as f:
            json.dump(self.state, f, indent=2)

    # -------------------------------------------------------------------------
    # Properties
    # -------------------------------------------------------------------------

    @property
    def total_value(self) -> float:
        return self.state["balance"] + self.state["invested"]

    @property
    def floor_amount(self) -> float:
        """
        The active floor — depends on floor mode.
        FIXED:    always the original floor set at startup
        TRAILING: max(original_floor, peak * (1 - trailing_pct))
                  rises with the fund, never falls below the original floor
        """
        if self.floor_mode == FLOOR_TRAILING:
            peak = self.state.get("high_water_mark", self.starting_balance)
            trailing = peak * (1 - self.trailing_floor_pct)
            return round(max(self.original_floor, trailing), 2)
        return self.original_floor

    @property
    def floor_hit(self) -> bool:
        return self.total_value <= self.floor_amount

    @property
    def deployable_balance(self) -> float:
        """
        The amount of cash available to deploy.
        LOCKED/PRINCIPAL mode: only capital above the floor — floor amount is never touched.
        All other modes: full liquid balance.
        """
        if self.floor_mode in (FLOOR_LOCKED, FLOOR_PRINCIPAL):
            return max(0.0, self.state["balance"] - self.original_floor)
        return self.state["balance"]

    @property
    def can_invest(self) -> bool:
        return (
            not self.state["paused"]
            and not self.floor_hit
            and self.deployable_balance >= self.entry_point
        )

    def update_peak(self, total_value: float):
        """
        Update the high water mark if the fund has reached a new peak.
        Call this each cycle after syncing from Alpaca.
        """
        if total_value > self.state.get("high_water_mark", 0):
            self.state["high_water_mark"] = round(total_value, 2)
            self._save()

    # -------------------------------------------------------------------------
    # Capital management
    # -------------------------------------------------------------------------

    def deploy(self, ticker: str, amount: float) -> float:
        """
        Deploy capital into a position.
        Caps at available liquid balance.
        Returns the actual amount deployed.
        """
        if not self.can_invest:
            return 0.0

        deploy_amount = min(amount, self.deployable_balance)
        self.state["balance"] -= deploy_amount
        self.state["invested"] += deploy_amount
        self.state["positions"][ticker] = (
            self.state["positions"].get(ticker, 0.0) + deploy_amount
        )
        self.state["total_trades"] += 1
        self.state.setdefault("trade_log", []).append({
            "date": datetime.now().strftime("%Y-%m-%d"),
            "ticker": ticker,
            "action": "BUY",
            "amount": round(deploy_amount, 2),
            "gain": None,
            "notes": "",
        })
        self._save()
        return deploy_amount

    def exit_position(self, ticker: str, return_value: float) -> float:
        """
        Close a position and return capital to the liquid balance.
        Returns the gain/loss on the trade.
        """
        cost_basis = self.state["positions"].pop(ticker, 0.0)
        gain = return_value - cost_basis

        # Performance fee — only on profitable trades
        fee = 0.0
        if gain > 0 and self.performance_fee > 0:
            fee = round(gain * self.performance_fee, 2)
            self.state.setdefault("fees_owed", 0.0)
            self.state["fees_owed"] = round(self.state["fees_owed"] + fee, 2)
            self.state.setdefault("fee_log", []).append({
                "date":   datetime.now().strftime("%Y-%m-%d"),
                "ticker": ticker,
                "gain":   round(gain, 2),
                "fee":    fee,
            })

        self.state["invested"] = max(0.0, self.state["invested"] - cost_basis)
        self.state["balance"] += return_value
        self.state["total_gains"] += gain
        self.state.setdefault("trade_log", []).append({
            "date":   datetime.now().strftime("%Y-%m-%d"),
            "ticker": ticker,
            "action": "SELL",
            "amount": round(return_value, 2),
            "gain":   round(gain, 2),
            "notes":  f"basis ${cost_basis:.2f}" + (f" | fee ${fee:.2f}" if fee > 0 else ""),
        })
        self._save()
        return gain

    # -------------------------------------------------------------------------
    # Re-entry watcher
    # -------------------------------------------------------------------------

    def add_watcher(self, ticker: str, proceeds: float, sell_price: float, reinvest_percent: float, expiry_days: int = 60):
        """
        After a sell, register a watcher for this ticker.
        Tracks the post-sell peak so we can detect the next dip.

        proceeds         — the sale proceeds available for reinvestment
        sell_price       — price at which we sold (starting reference for peak tracking)
        reinvest_percent — fraction of proceeds to redeploy on re-entry
        expiry_days      — cancel watcher and return funds if not triggered within this many days
        """
        if "watchers" not in self.state:
            self.state["watchers"] = {}
        self.state["watchers"][ticker] = {
            "proceeds": proceeds,
            "reinvest_amount": round(proceeds * reinvest_percent, 2),
            "sell_price": sell_price,
            "post_sell_high": sell_price,
            "days_watching": 0,
            "expiry_days": expiry_days,
        }
        self._save()

    def update_watcher_high(self, ticker: str, current_price: float):
        """Update the post-sell peak if price has gone higher, and increment days counter."""
        watcher = self.state.get("watchers", {}).get(ticker)
        if not watcher:
            return
        if current_price > watcher["post_sell_high"]:
            watcher["post_sell_high"] = current_price
        watcher["days_watching"] = watcher.get("days_watching", 0) + 1
        self._save()

    def check_reentry(self, ticker: str, current_price: float, dip_percent: float) -> float:
        """
        Check if a watched ticker has dipped enough below its post-sell peak
        to trigger re-entry, or if the watcher has expired.

        Returns the reinvest amount if dip triggered, 0.0 otherwise.
        On expiry, returns reserved funds to balance and removes watcher.
        Removes the watcher on trigger.
        """
        watcher = self.state.get("watchers", {}).get(ticker)
        if not watcher:
            return 0.0

        # Expiry check — return funds to balance if watcher times out
        if watcher["days_watching"] >= watcher.get("expiry_days", 60):
            self.state["balance"] += watcher["reinvest_amount"]
            del self.state["watchers"][ticker]
            self._save()
            return 0.0

        peak = watcher["post_sell_high"]
        dip_threshold = peak * (1 - dip_percent)

        # Only trigger if price has first risen above sell price (confirming a peak)
        # then dropped by dip_percent from that peak
        if peak > watcher["sell_price"] and current_price <= dip_threshold:
            reinvest_amount = watcher["reinvest_amount"]
            del self.state["watchers"][ticker]
            self._save()
            return reinvest_amount

        return 0.0

    def remove_watcher(self, ticker: str):
        """Manually remove a watcher (e.g. if floor is triggered)."""
        self.state.get("watchers", {}).pop(ticker, None)
        self._save()

    # -------------------------------------------------------------------------
    # Allocation control
    # -------------------------------------------------------------------------

    def allocation_cap(self, ticker: str, total_value: float) -> float:
        """
        Returns the max additional $ that can be deployed into this ticker.
        Returns None for FREE_RANGE (no limit).

        FREE_RANGE  — no limits, deploy freely (default)
        PERCENTAGE  — max % of current total fund value per ticker
        MANUAL      — max fixed dollar amount per ticker
        """
        if self.allocation_mode == "FREE_RANGE":
            return None

        current_invested = self.state["positions"].get(ticker, 0.0)

        if self.allocation_mode == "PERCENTAGE":
            max_allowed = total_value * self.allocations.get(ticker, 1.0)
        elif self.allocation_mode == "MANUAL":
            max_allowed = self.allocations.get(ticker, float("inf"))
        else:
            return None

        return max(0.0, round(max_allowed - current_invested, 2))

    # -------------------------------------------------------------------------
    # Performance fee
    # -------------------------------------------------------------------------

    def fees_summary(self) -> dict:
        """Returns a summary of performance fees owed and collected."""
        return {
            "fee_rate":      self.performance_fee,
            "fees_owed":     round(self.state.get("fees_owed", 0.0), 2),
            "fees_collected": round(self.state.get("fees_collected", 0.0), 2),
            "fee_log":       self.state.get("fee_log", []),
        }

    def collect_fees(self) -> float:
        """
        Mark all outstanding fees as collected (paid out).
        Call this when you withdraw your cut from the user.
        Returns the amount collected.
        """
        owed = self.state.get("fees_owed", 0.0)
        if owed > 0:
            self.state["fees_collected"] = round(
                self.state.get("fees_collected", 0.0) + owed, 2
            )
            self.state["fees_owed"] = 0.0
            self._save()
        return owed

    # -------------------------------------------------------------------------
    # Monthly trade budget
    # -------------------------------------------------------------------------

    def _sync_trade_month(self):
        """Reset the monthly counter if we've rolled into a new month."""
        current_month = datetime.now().strftime("%Y-%m")
        if self.state.get("trade_month") != current_month:
            self.state["monthly_trades"] = 0
            self.state["trade_month"] = current_month
            self._save()

    def budget_remaining(self, max_trades: int) -> int:
        """
        Returns how many new buys are left in the monthly budget.
        Returns None if max_trades is None (unlimited).
        """
        if max_trades is None:
            return None
        self._sync_trade_month()
        return max(0, max_trades - self.state.get("monthly_trades", 0))

    def record_monthly_trade(self):
        """Increment the monthly trade counter. Call after every new BUY execution."""
        self._sync_trade_month()
        self.state["monthly_trades"] = self.state.get("monthly_trades", 0) + 1
        self._save()

    # -------------------------------------------------------------------------
    # Floor protection
    # -------------------------------------------------------------------------

    def trigger_floor(self):
        """
        Emergency stop — pause all trading activity.
        Called automatically when total fund value hits the floor.
        """
        self.state["paused"] = True
        self._save()

    def resume(self):
        """Manually resume trading after a floor pause."""
        self.state["paused"] = False
        self._save()

    # -------------------------------------------------------------------------
    # Status
    # -------------------------------------------------------------------------

    def status(self) -> dict:
        return {
            "total_value": round(self.total_value, 2),
            "liquid_balance": round(self.state["balance"], 2),
            "invested": round(self.state["invested"], 2),
            "floor_amount": self.floor_amount,
            "floor_mode": self.floor_mode,
            "high_water_mark": round(self.state.get("high_water_mark", self.starting_balance), 2),
            "floor_hit": self.floor_hit,
            "paused": self.state["paused"],
            "open_positions": dict(self.state["positions"]),
            "watching_reentry": list(self.state.get("watchers", {}).keys()),
            "total_trades": self.state["total_trades"],
            "total_gains": round(self.state["total_gains"], 2),
            "monthly_trades": self.state.get("monthly_trades", 0),
            "trade_month": self.state.get("trade_month", ""),
        }
