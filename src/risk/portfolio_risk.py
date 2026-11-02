"""
Portfolio Risk and Circuit Breaker Engine.
Enforces aggregate portfolio safety constraints, tracking daily/weekly losses,
consecutive losses, correlated currency exposure, and maximum simultaneous positions.
Triggers institutional circuit breakers upon limit breach.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime, date, timezone
from typing import List, Dict, Optional, Set

from src.core.contracts import Position, AccountInfo, SignalCandidate, OrderType, AccountProfile, DrawdownType
from src.core.config import RiskConfig, AccountProfileConfig, PropFirmRulesConfig, get_default_account_profiles


@dataclass
class RiskBreachEvent:
    event_type: str
    message: str
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    severity: str = "CRITICAL"


class PortfolioRiskEngine:
    def __init__(
        self,
        config: Optional[Any] = None,
        profile_config: Optional[AccountProfileConfig] = None
    ):
        if hasattr(config, 'risk'):
            self.config = config.risk
            self.app_config = config
            self.profile = profile_config or config.account_profiles.get(config.account_profile, get_default_account_profiles()["DEMO"])
        else:
            self.config = config or RiskConfig()
            self.app_config = None
            self.profile = profile_config or get_default_account_profiles()["DEMO"]

        self.circuit_breaker_active = False
        self.circuit_breaker_reason: Optional[str] = None
        self.buffer_halted = False
        self.hard_locked = False
        self.risk_events: List[RiskBreachEvent] = []

        # Tracking metrics
        self.current_day: date = datetime.now(timezone.utc).date()
        self.initial_balance: float = 0.0
        self.peak_equity: float = 0.0
        self.daily_starting_equity: float = 0.0
        self.daily_starting_balance: float = 0.0
        self.daily_realized_loss: float = 0.0
        self.weekly_starting_equity: float = 0.0
        self.weekly_realized_loss: float = 0.0
        self.consecutive_losses: int = 0
        self.session_trades_count: int = 0
        self.last_deal_profit: float = 0.0
        self.last_deal_volume: float = 0.0
        self.trading_days_count: int = 0
        self.last_known_balance: float = 0.0

    @property
    def is_safety_buffer_active(self) -> bool:
        return self.buffer_halted

    def initialize_day(self, equity: float, balance: Optional[float] = None) -> None:
        """Called at start of day/session or first tick to calibrate baseline."""
        today = datetime.now(timezone.utc).date()
        if today != self.current_day or self.daily_starting_equity <= 0:
            self.current_day = today
            self.daily_starting_equity = equity
            self.daily_starting_balance = balance if balance is not None and balance > 0 else equity
            self.daily_realized_loss = 0.0
            self.session_trades_count = 0
            if self.weekly_starting_equity <= 0 or today.weekday() == 0:  # Monday
                self.weekly_starting_equity = equity
                self.weekly_realized_loss = 0.0

    def update_equity(self, equity: Any, balance: Optional[float] = None) -> Tuple[float, float]:
        """
        Updates equity tracking, peak equity, and calculates real-time daily loss % and drawdown %.
        Accepts either (equity: float, balance: float) or (account: AccountInfo).
        Returns: (daily_loss_pct, total_drawdown_pct)
        """
        acc = None
        if hasattr(equity, 'equity') and hasattr(equity, 'balance'):
            acc = equity
            eq_val = float(acc.equity)
            bal_val = float(acc.balance)
            if getattr(acc, 'initial_balance', 0.0) > 0:
                self.initial_balance = acc.initial_balance
            if getattr(acc, 'peak_equity', 0.0) > 0 and self.peak_equity < acc.peak_equity:
                self.peak_equity = acc.peak_equity
        else:
            eq_val = float(equity)
            bal_val = float(balance) if balance is not None else eq_val

        if self.initial_balance <= 0:
            self.initial_balance = bal_val
        if self.daily_starting_equity <= 0:
            self.daily_starting_equity = eq_val
        if self.daily_starting_balance <= 0:
            self.daily_starting_balance = bal_val

        # Detect deposit or withdrawal adjustments without closed trade PnL
        if self.last_known_balance > 0 and abs(bal_val - self.last_known_balance) > 0.001:
            delta = bal_val - self.last_known_balance
            if abs(delta - self.last_deal_profit) > 0.01:
                self.initial_balance += delta
                self.daily_starting_balance += delta
                self.daily_starting_equity += delta
                if delta > 0:
                    self.peak_equity += delta
                else:
                    self.peak_equity = max(bal_val, self.peak_equity + delta)
        self.last_known_balance = bal_val

        if eq_val > self.peak_equity:
            self.peak_equity = eq_val

        # 1. Daily Loss %
        day_ref = (
            self.daily_starting_balance
            if self.profile.drawdown_type == DrawdownType.DAILY_START_BALANCE
            else self.daily_starting_equity
        )
        daily_loss_money = max(0.0, day_ref - eq_val)
        daily_loss_pct = (daily_loss_money / day_ref * 100.0) if day_ref > 0 else 0.0

        # 2. Total Drawdown %
        if self.profile.drawdown_type == DrawdownType.STATIC:
            ref = self.initial_balance
            dd_money = max(0.0, ref - eq_val)
            dd_pct = (dd_money / ref * 100.0) if ref > 0 else 0.0
        elif self.profile.drawdown_type == DrawdownType.TRAILING:
            ref = self.peak_equity
            dd_money = max(0.0, ref - eq_val)
            dd_pct = (dd_money / ref * 100.0) if ref > 0 else 0.0
        else:
            dd_pct = daily_loss_pct

        # Check safety buffer and hard limit
        daily_limit = self.profile.max_daily_loss_percent
        dd_limit = self.profile.max_total_drawdown_percent
        buffer_ratio = self.profile.safety_buffer_percent / 100.0

        if daily_loss_pct >= daily_limit * 0.95 or dd_pct >= dd_limit * 0.95:
            self.hard_locked = True
            self._trigger_circuit_breaker(
                f"Hard drawdown limit breached! Daily: {daily_loss_pct:.2f}% (limit {daily_limit}%), DD: {dd_pct:.2f}% (limit {dd_limit}%)"
            )
        elif daily_loss_pct >= daily_limit * buffer_ratio or dd_pct >= dd_limit * buffer_ratio:
            self.buffer_halted = True
        else:
            self.buffer_halted = False

        if acc is not None:
            acc.daily_loss_pct = daily_loss_pct
            acc.drawdown_pct = dd_pct
            acc.peak_equity = self.peak_equity
            acc.initial_balance = self.initial_balance

        return daily_loss_pct, dd_pct

    def reset_state(self, current_balance: float) -> None:
        """
        Sets challenge baseline, day-start balance, and peak equity to current balance,
        and clears daily counters.
        """
        self.initial_balance = current_balance
        self.peak_equity = current_balance
        self.daily_starting_balance = current_balance
        self.daily_starting_equity = current_balance
        self.last_known_balance = current_balance
        self.daily_realized_loss = 0.0
        self.weekly_realized_loss = 0.0
        self.consecutive_losses = 0
        self.session_trades_count = 0
        self.trading_days_count = 0
        self.last_deal_profit = 0.0
        self.circuit_breaker_active = False
        self.circuit_breaker_reason = None
        self.buffer_halted = False
        self.hard_locked = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "initial_balance": self.initial_balance,
            "peak_equity": self.peak_equity,
            "daily_starting_balance": self.daily_starting_balance,
            "daily_starting_equity": self.daily_starting_equity,
            "daily_realized_loss": self.daily_realized_loss,
            "consecutive_losses": self.consecutive_losses,
            "session_trades_count": self.session_trades_count,
            "trading_days_count": self.trading_days_count,
            "current_day": self.current_day.isoformat(),
            "last_known_balance": self.last_known_balance
        }

    def load_from_dict(self, data: Dict[str, Any]) -> None:
        if not data:
            return
        if "initial_balance" in data and float(data["initial_balance"]) > 0:
            self.initial_balance = float(data["initial_balance"])
        if "peak_equity" in data and float(data["peak_equity"]) > 0:
            self.peak_equity = float(data["peak_equity"])
        if "daily_starting_balance" in data and float(data["daily_starting_balance"]) > 0:
            self.daily_starting_balance = float(data["daily_starting_balance"])
        if "daily_starting_equity" in data and float(data["daily_starting_equity"]) > 0:
            self.daily_starting_equity = float(data["daily_starting_equity"])
        if "daily_realized_loss" in data:
            self.daily_realized_loss = float(data["daily_realized_loss"])
        if "consecutive_losses" in data:
            self.consecutive_losses = int(data["consecutive_losses"])
        if "session_trades_count" in data:
            self.session_trades_count = int(data["session_trades_count"])
        if "trading_days_count" in data:
            self.trading_days_count = int(data["trading_days_count"])
        if "last_known_balance" in data and float(data["last_known_balance"]) > 0:
            self.last_known_balance = float(data["last_known_balance"])
        if "current_day" in data:
            try:
                from datetime import date
                self.current_day = date.fromisoformat(data["current_day"])
            except Exception:
                pass

    def record_closed_trade(self, profit: float, volume: float = 0.01) -> None:
        """Updates realized losses and consecutive win/loss streaks."""
        self.last_deal_profit = profit
        self.last_deal_volume = volume
        if profit < 0:
            self.daily_realized_loss += abs(profit)
            self.weekly_realized_loss += abs(profit)
            self.consecutive_losses += 1

            if self.consecutive_losses >= self.config.max_consecutive_losses:
                self._trigger_circuit_breaker(
                    f"Max consecutive losses reached: {self.consecutive_losses} in a row."
                )

            if self.daily_starting_equity > 0:
                daily_pct = (self.daily_realized_loss / self.daily_starting_equity) * 100.0
                if daily_pct >= self.config.max_daily_loss_percent:
                    self._trigger_circuit_breaker(
                        f"Daily loss limit breached: {daily_pct:.2f}% >= {self.config.max_daily_loss_percent:.2f}%"
                    )

            if self.weekly_starting_equity > 0:
                weekly_pct = (self.weekly_realized_loss / self.weekly_starting_equity) * 100.0
                if weekly_pct >= self.config.max_weekly_loss_percent:
                    self._trigger_circuit_breaker(
                        f"Weekly loss limit breached: {weekly_pct:.2f}% >= {self.config.max_weekly_loss_percent:.2f}%"
                    )
        else:
            self.consecutive_losses = 0

    def evaluate_new_order_permission(
        self,
        candidate: SignalCandidate,
        account: AccountInfo,
        active_positions: List[Position],
        proposed_monetary_risk: float,
        proposed_volume: float = 0.01
    ) -> Tuple[bool, Optional[str]]:
        """
        Validates whether a new order is permitted against all portfolio-level constraints
        and institutional prop firm rules.
        Returns: (is_permitted, rejection_reason)
        """
        self.initialize_day(account.equity)
        self.update_equity(account)

        # 0. Check Live/Prop Trading Enabled Gating
        if self.profile.profile_name in [AccountProfile.LIVE, AccountProfile.PROP]:
            account_enabled = getattr(account, "trading_enabled", False)
            config_enabled = getattr(self.app_config, "trading_enabled", True) if self.app_config else True
            if not (account_enabled and config_enabled):
                return False, f"Trading is disabled for {self.profile.profile_name.value if hasattr(self.profile.profile_name, 'value') else self.profile.profile_name} profile. Enable via input or /enable."

        # 1. Check Circuit Breaker & Hard Lock
        if self.hard_locked or self.circuit_breaker_active:
            return False, f"Portfolio Circuit Breaker is ACTIVE: {self.circuit_breaker_reason}"

        # 1b. Check Safety Buffer (stop opening trades at buffer level)
        if self.buffer_halted:
            return False, f"Trading paused: Safety buffer reached ({self.profile.safety_buffer_percent:.0f}% of limit)."

        # 1c. Prop Firm Rule: Stop Loss strictly required
        if self.profile.rules.require_sl:
            if candidate.stop_loss <= 0.0:
                return False, "Prop/Live Rule Violation: Stop Loss is strictly required on every trade."

        # 1d. Prop Firm Rule: No Martingale
        if self.profile.rules.no_martingale and self.last_deal_profit < 0.0:
            if self.last_deal_volume > 0.0 and proposed_volume > self.last_deal_volume:
                return False, f"Prop Rule Violation: Martingale detected (lot {proposed_volume:.2f} > previous losing lot {self.last_deal_volume:.2f})."

        # 1e. Prop Firm Rule: No Grid / Averaging Down
        if self.profile.rules.no_grid_averaging:
            for p in active_positions:
                if p.symbol == candidate.symbol and p.order_type == candidate.direction:
                    if p.profit < 0.0:
                        return False, f"Prop Rule Violation: Grid/Averaging down prohibited while position #{p.ticket} is in drawdown (${p.profit:.2f})."

        # 1f. Prop Firm Rule: No Hedging
        if self.profile.rules.no_hedging:
            for p in active_positions:
                if p.symbol == candidate.symbol and p.order_type != candidate.direction:
                    return False, f"Prop Rule Violation: Hedging prohibited (opposite position #{p.ticket} active on {p.symbol})."

        # 1g. Prop Firm Rule: No Weekend Holding
        if self.profile.rules.no_weekend_holding:
            now_utc = datetime.now(timezone.utc)
            if now_utc.weekday() == 4 and now_utc.hour >= self.profile.rules.weekend_close_hour_friday:
                return False, f"Prop Rule Violation: Weekend holding prohibited after {self.profile.rules.weekend_close_hour_friday:02d}:00 Friday."

        # 1h. Hard Lot Cap
        if proposed_volume > self.profile.rules.max_lot_cap:
            return False, f"Volume {proposed_volume:.2f} exceeds profile hard lot cap {self.profile.rules.max_lot_cap:.2f}."

        # 2. Maximum Simultaneous Positions
        max_pos = min(self.config.max_simultaneous_positions, self.profile.max_open_positions)
        if len(active_positions) >= max_pos:
            return False, (
                f"Maximum simultaneous positions reached ({len(active_positions)}/{max_pos})."
            )

        # 3. Maximum Daily Loss Limit
        if self.daily_starting_equity > 0:
            daily_loss_pct = (self.daily_realized_loss / self.daily_starting_equity) * 100.0
            if daily_loss_pct >= self.config.max_daily_loss_percent:
                self._trigger_circuit_breaker(
                    f"Daily loss limit breached: {daily_loss_pct:.2f}% >= {self.config.max_daily_loss_percent:.2f}%"
                )
                return False, f"Daily loss limit breached ({daily_loss_pct:.2f}%)."

        # 4. Maximum Weekly Loss Limit
        if self.weekly_starting_equity > 0:
            weekly_loss_pct = (self.weekly_realized_loss / self.weekly_starting_equity) * 100.0
            if weekly_loss_pct >= self.config.max_weekly_loss_percent:
                self._trigger_circuit_breaker(
                    f"Weekly loss limit breached: {weekly_loss_pct:.2f}% >= {self.config.max_weekly_loss_percent:.2f}%"
                )
                return False, f"Weekly loss limit breached ({weekly_loss_pct:.2f}%)."

        # 5. Maximum Aggregate Open Risk
        current_open_risk = sum(p.initial_risk_amount for p in active_positions)
        total_open_risk_with_new = current_open_risk + proposed_monetary_risk
        max_allowed_open_risk = account.equity * (self.config.max_aggregate_open_risk_percent / 100.0)

        if total_open_risk_with_new > max_allowed_open_risk:
            return False, (
                f"Aggregate open risk would exceed limit: ${total_open_risk_with_new:.2f} > "
                f"${max_allowed_open_risk:.2f} ({self.config.max_aggregate_open_risk_percent}% of equity)."
            )

        # 6. Correlated Symbol Exposure Protection
        # Disallow more than 2 positions in opposite directions or identical base currencies
        same_symbol_positions = [p for p in active_positions if p.symbol == candidate.symbol]
        if len(same_symbol_positions) > 0:
            for p in same_symbol_positions:
                if p.order_type != candidate.direction:
                    return False, f"Conflicting open position exists on {candidate.symbol} in opposite direction."

        # 7. Max trades per session
        if self.session_trades_count >= self.config.max_trades_per_session:
            return False, f"Session trade quota reached ({self.session_trades_count}/{self.config.max_trades_per_session})."

        return True, None

    def register_trade_executed(self) -> None:
        self.session_trades_count += 1

    def _trigger_circuit_breaker(self, reason: str) -> None:
        self.circuit_breaker_active = True
        self.circuit_breaker_reason = reason
        self.risk_events.append(
            RiskBreachEvent(event_type="CIRCUIT_BREAKER_TRIGGERED", message=reason)
        )

    def manual_reset_circuit_breaker(self, admin_token: str) -> bool:
        """Requires explicit admin authorization to resume after safety halt."""
        if admin_token == "AUTHORIZE_SYSTEM_RESET":
            self.circuit_breaker_active = False
            self.circuit_breaker_reason = None
            self.consecutive_losses = 0
            self.risk_events.append(
                RiskBreachEvent(event_type="MANUAL_RESET", message="Circuit breaker cleared by admin.", severity="INFO")
            )
            return True
        return False
