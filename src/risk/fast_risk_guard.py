"""
Institutional Risk Guardrails for Speed / Fast Mode Scalping (Phase 4).
These guardrails strictly TIGHTEN risk and NEVER relax any baseline limit:
1. Tick-value lot sizing rounded strictly DOWN to lot_step (never inflates size).
2. ForceMinLotRiskFloor & Small-Account Awareness:
   - Computes exact minimum balance required to trade min_lot at configured risk % for current SL distance.
   - Skips trade and emits clear DECISION message if min_lot exceeds risk cap.
3. Margin Level Guard:
   - Projected margin level after entry must be >= min_projected_margin_level_pct (default 300%).
   - Critical margin floor (default 150%): blocks entries and triggers protective flatten well above stop-out.
4. Combined Open-Risk Cap & Anti-Stacking / Anti-Martingale / Anti-Grid:
   - Sum of open position SL risks + proposed trade risk <= max_combined_open_risk_pct_equity.
   - Prohibits averaging down / grid entries in drawdown, prohibits lot increases after losses (martingale),
     and blocks same-direction stacking unless explicitly enabled with independent signal ID.
5. Consecutive-Loss Circuit Breaker & Equity-Velocity Kill Switch:
   - Pauses Fast Mode after N consecutive losses.
   - If floating loss drops > X% of equity within Y seconds, triggers CRITICAL alert, flattens all, and pauses.
"""

from __future__ import annotations
import json
import math
import os
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Deque, Dict, List, Optional, Tuple, Any

from src.core.contracts import (
    AccountInfo, SymbolInfo, Position, SignalCandidate, OrderType
)


@dataclass
class SmallAccountAssessment:
    symbol: str
    is_affordable: bool
    min_lot: float
    min_lot_monetary_risk: float
    min_lot_risk_pct: float
    risk_cap_pct: float
    required_min_balance: float
    decision_message: str


@dataclass
class MarginHealthStatus:
    current_margin_level_pct: float
    projected_margin_level_pct: float
    combined_open_risk_usd: float
    combined_open_risk_pct_equity: float
    can_open_new_trade: bool
    must_flatten_immediately: bool
    reason: Optional[str] = None


class FastRiskGuardEngine:
    def __init__(self, config_path: str = "config/fast_mode.json"):
        self.config_path = config_path
        self._cfg = self._load_config()

        self.paused: bool = False
        self.pause_reason: Optional[str] = None
        self.consecutive_losses: int = 0
        self.last_trade_was_loss: bool = False
        self.last_loss_volume: float = 0.0

        # Rolling equity snapshots for velocity kill switch: deque of (timestamp_sec, equity, floating_loss_usd)
        self._equity_history: Deque[Tuple[float, float, float]] = deque(maxlen=1000)
        self.critical_alerts: List[str] = []
        self.decision_messages: List[str] = []

        # Rejection telemetry counters for Phase 6 reporting
        self.rejected_by_margin_count: int = 0
        self.rejected_by_min_lot_count: int = 0
        self.rejected_by_open_risk_count: int = 0
        self.rejected_by_anti_martingale_count: int = 0

    def _load_config(self) -> Dict[str, Any]:
        if os.path.exists(self.config_path):
            try:
                with open(self.config_path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
        return {}

    def reload_config(self) -> None:
        self._cfg = self._load_config()

    @staticmethod
    def compute_position_monetary_risk(
        entry_price: float,
        stop_loss: float,
        volume: float,
        symbol_info: SymbolInfo
    ) -> float:
        """Computes exact USD monetary risk for a given stop distance and volume."""
        sl_dist = abs(entry_price - stop_loss)
        tick_sz = symbol_info.tick_size if symbol_info.tick_size > 0 else symbol_info.point
        tick_val = symbol_info.tick_value if symbol_info.tick_value > 0 else 1.0
        if tick_sz <= 0:
            tick_sz = 0.00001
        ticks = sl_dist / tick_sz
        return round(ticks * tick_val * volume, 2)

    @classmethod
    def evaluate_small_account_affordability(
        cls,
        account: AccountInfo,
        symbol_info: SymbolInfo,
        entry_price: float,
        stop_loss: float,
        risk_cap_pct: float
    ) -> SmallAccountAssessment:
        """
        Computes minimum balance needed to trade `symbol_info.min_lot` at `risk_cap_pct`
        with the current stop loss distance, and generates the DECISION message if skipped.
        """
        bal = account.balance if account.balance > 0 else 0.01
        min_lot = symbol_info.min_lot if symbol_info.min_lot > 0 else 0.01
        min_lot_risk_usd = cls.compute_position_monetary_risk(
            entry_price=entry_price,
            stop_loss=stop_loss,
            volume=min_lot,
            symbol_info=symbol_info
        )
        min_lot_risk_pct = round((min_lot_risk_usd / bal) * 100.0, 2)
        req_balance = round(min_lot_risk_usd / (risk_cap_pct / 100.0), 2) if risk_cap_pct > 0 else 0.0

        if min_lot_risk_pct > risk_cap_pct + 1e-6:
            msg = (
                f"{symbol_info.symbol} skipped: min lot ({min_lot:.2f}) risks {min_lot_risk_pct:.2f}% "
                f"(${min_lot_risk_usd:.2f}) > {risk_cap_pct:.2f}% cap; "
                f"needs about ${req_balance:.2f} balance or a cent/micro account"
            )
            return SmallAccountAssessment(
                symbol=symbol_info.symbol,
                is_affordable=False,
                min_lot=min_lot,
                min_lot_monetary_risk=min_lot_risk_usd,
                min_lot_risk_pct=min_lot_risk_pct,
                risk_cap_pct=risk_cap_pct,
                required_min_balance=req_balance,
                decision_message=msg
            )

        return SmallAccountAssessment(
            symbol=symbol_info.symbol,
            is_affordable=True,
            min_lot=min_lot,
            min_lot_monetary_risk=min_lot_risk_usd,
            min_lot_risk_pct=min_lot_risk_pct,
            risk_cap_pct=risk_cap_pct,
            required_min_balance=req_balance,
            decision_message=f"{symbol_info.symbol} affordable at min lot {min_lot:.2f} ({min_lot_risk_pct:.2f}% <= {risk_cap_pct:.2f}%)"
        )

    def calculate_safe_lot_size(
        self,
        account: AccountInfo,
        symbol_info: SymbolInfo,
        entry_price: float,
        stop_loss: float,
        risk_percent: Optional[float] = None
    ) -> Tuple[float, float, Optional[str]]:
        """
        Calculates volume strictly rounded DOWN to lot_step.
        Enforces ForceMinLotRiskFloor: never inflates size if min_lot exceeds risk cap.
        Returns `(volume, monetary_risk_usd, rejection_reason)`.
        """
        risk_cfg = self._cfg.get("risk_guardrails", {})
        max_cap_pct = float(risk_cfg.get("max_risk_percent_per_trade", 1.0))
        target_pct = min(float(risk_percent if risk_percent is not None else max_cap_pct), max_cap_pct)

        sl_dist = abs(entry_price - stop_loss)
        if sl_dist <= 0:
            return 0.0, 0.0, "Stop loss distance must be > 0"

        # Check small-account affordability first
        assessment = self.evaluate_small_account_affordability(
            account=account,
            symbol_info=symbol_info,
            entry_price=entry_price,
            stop_loss=stop_loss,
            risk_cap_pct=max_cap_pct
        )
        if not assessment.is_affordable:
            self.rejected_by_min_lot_count += 1
            self.decision_messages.append(assessment.decision_message)
            return 0.0, assessment.min_lot_monetary_risk, assessment.decision_message

        risk_budget_usd = account.balance * (target_pct / 100.0)
        risk_per_one_lot = self.compute_position_monetary_risk(entry_price, stop_loss, 1.0, symbol_info)
        if risk_per_one_lot <= 0:
            return 0.0, 0.0, "Invalid risk per 1.0 lot"

        raw_lots = risk_budget_usd / risk_per_one_lot
        safe_lots = symbol_info.normalize_volume_down(raw_lots)
        if safe_lots < symbol_info.min_lot:
            return 0.0, 0.0, f"Calculated volume ({raw_lots:.4f}) below broker min_lot ({symbol_info.min_lot})"

        actual_risk_usd = self.compute_position_monetary_risk(entry_price, stop_loss, safe_lots, symbol_info)
        return safe_lots, actual_risk_usd, None

    def check_margin_and_open_risk(
        self,
        account: AccountInfo,
        active_positions: List[Position],
        symbol_info: Optional[SymbolInfo] = None,
        proposed_volume: float = 0.0,
        proposed_monetary_risk: float = 0.0
    ) -> MarginHealthStatus:
        """
        Evaluates current margin level, projected margin level after entry,
        critical margin floor (150%), and combined open-risk cap across all positions.
        """
        risk_cfg = self._cfg.get("risk_guardrails", {})
        min_proj_margin_pct = float(risk_cfg.get("min_projected_margin_level_pct", 300.0))
        critical_margin_pct = float(risk_cfg.get("critical_margin_level_pct", 150.0))
        max_open_risk_pct = float(risk_cfg.get("max_combined_open_risk_pct_equity", 3.0))

        eq = account.equity if account.equity > 0 else account.balance
        used_margin = account.margin if account.margin > 0 else 0.0

        # Current margin level % (if used_margin == 0, treat as 99999.0%)
        if used_margin > 0:
            cur_margin_level = (eq / used_margin) * 100.0
        elif account.margin_level > 0:
            cur_margin_level = account.margin_level
        else:
            cur_margin_level = 99999.0

        # Additional margin for proposed order
        add_margin = 0.0
        if symbol_info is not None and proposed_volume > 0:
            margin_per_lot = symbol_info.margin_required if symbol_info.margin_required > 0 else 1000.0
            add_margin = margin_per_lot * proposed_volume

        proj_used_margin = used_margin + add_margin
        proj_margin_level = (eq / proj_used_margin) * 100.0 if proj_used_margin > 0 else 99999.0

        # Combined open risk across all active positions
        open_risk_usd = 0.0
        for p in active_positions:
            if p.initial_risk_amount > 0:
                open_risk_usd += p.initial_risk_amount
            elif p.stop_loss > 0:
                sl_d = abs(p.open_price - p.stop_loss)
                open_risk_usd += sl_d * p.volume * 100.0

        total_risk_usd = round(open_risk_usd + proposed_monetary_risk, 2)
        total_risk_pct_eq = round((total_risk_usd / eq) * 100.0, 2) if eq > 0 else 100.0

        # 1. Critical margin check (<= 150%) -> flatten immediately!
        if used_margin > 0 and cur_margin_level <= critical_margin_pct:
            self.rejected_by_margin_count += 1
            reason = (
                f"CRITICAL MARGIN FLOOR BREACHED: Margin level {cur_margin_level:.1f}% <= "
                f"{critical_margin_pct:.1f}% critical floor. Halting entries and flattening!"
            )
            self.critical_alerts.append(reason)
            self.paused = True
            self.pause_reason = reason
            return MarginHealthStatus(
                current_margin_level_pct=round(cur_margin_level, 1),
                projected_margin_level_pct=round(proj_margin_level, 1),
                combined_open_risk_usd=total_risk_usd,
                combined_open_risk_pct_equity=total_risk_pct_eq,
                can_open_new_trade=False,
                must_flatten_immediately=True,
                reason=reason
            )

        # 2. Projected margin level check (< 300%)
        if proj_used_margin > 0 and proj_margin_level < min_proj_margin_pct:
            self.rejected_by_margin_count += 1
            return MarginHealthStatus(
                current_margin_level_pct=round(cur_margin_level, 1),
                projected_margin_level_pct=round(proj_margin_level, 1),
                combined_open_risk_usd=total_risk_usd,
                combined_open_risk_pct_equity=total_risk_pct_eq,
                can_open_new_trade=False,
                must_flatten_immediately=False,
                reason=f"Projected margin level ({proj_margin_level:.1f}%) below floor ({min_proj_margin_pct:.1f}%)"
            )

        # 3. Combined open-risk cap check
        if total_risk_pct_eq > max_open_risk_pct + 1e-6:
            self.rejected_by_open_risk_count += 1
            return MarginHealthStatus(
                current_margin_level_pct=round(cur_margin_level, 1),
                projected_margin_level_pct=round(proj_margin_level, 1),
                combined_open_risk_usd=total_risk_usd,
                combined_open_risk_pct_equity=total_risk_pct_eq,
                can_open_new_trade=False,
                must_flatten_immediately=False,
                reason=f"Combined open risk ({total_risk_pct_eq:.2f}% / ${total_risk_usd:.2f}) exceeds {max_open_risk_pct:.2f}% equity cap"
            )

        return MarginHealthStatus(
            current_margin_level_pct=round(cur_margin_level, 1),
            projected_margin_level_pct=round(proj_margin_level, 1),
            combined_open_risk_usd=total_risk_usd,
            combined_open_risk_pct_equity=total_risk_pct_eq,
            can_open_new_trade=True,
            must_flatten_immediately=False,
            reason=None
        )

    def validate_trade_candidate(
        self,
        candidate: SignalCandidate,
        account: AccountInfo,
        symbol_info: SymbolInfo,
        active_positions: List[Position],
        proposed_volume: float,
        proposed_monetary_risk: float
    ) -> Tuple[bool, Optional[str]]:
        """
        Enforces all speed risk guardrails on a proposed trade:
        - Fast mode circuit breaker / pause status
        - Max concurrent positions (global and per-symbol)
        - No stacking same-direction positions, no averaging down / grid, no martingale lot increases
        - Projected margin level >= 300% and combined open risk <= cap
        """
        if self.paused:
            return False, f"Fast Mode paused by risk circuit breaker: {self.pause_reason}"

        risk_cfg = self._cfg.get("risk_guardrails", {})
        max_global = int(risk_cfg.get("max_concurrent_positions_global", 3))
        max_per_sym = int(risk_cfg.get("max_concurrent_positions_per_symbol", 1))
        allow_stacking = bool(risk_cfg.get("allow_same_direction_stacking", False))
        no_martingale = bool(risk_cfg.get("prohibit_martingale", True))
        no_grid = bool(risk_cfg.get("prohibit_grid_averaging", True))

        # 1. Global concurrent position cap
        if len(active_positions) >= max_global:
            return False, f"Global concurrent position limit reached ({len(active_positions)}/{max_global})"

        # 2. Per-symbol concurrent position, stacking, and averaging-down check
        sym_positions = [p for p in active_positions if p.symbol == candidate.symbol]
        if len(sym_positions) >= max_per_sym and not allow_stacking:
            return False, f"Per-symbol position limit reached on {candidate.symbol} ({len(sym_positions)}/{max_per_sym})"

        for p in sym_positions:
            # Prohibit grid / averaging down into a losing position
            if no_grid and p.order_type == candidate.direction:
                if p.profit < 0 or (
                    (p.order_type == OrderType.BUY and candidate.entry_price < p.open_price)
                    or (p.order_type == OrderType.SELL and candidate.entry_price > p.open_price)
                ):
                    self.rejected_by_anti_martingale_count += 1
                    return False, f"Anti-grid / no-averaging-down rule: cannot add to losing {candidate.symbol} position #{p.ticket}"

            if not allow_stacking and p.order_type == candidate.direction:
                return False, f"No-stacking rule: already holding {p.order_type.value} on {candidate.symbol}"

        # 3. Anti-martingale: prohibit increasing lot size immediately after a losing trade
        if no_martingale and self.last_trade_was_loss and self.last_loss_volume > 0:
            if proposed_volume > self.last_loss_volume + 1e-9:
                self.rejected_by_anti_martingale_count += 1
                return (
                    False,
                    f"Anti-martingale rule: proposed volume ({proposed_volume:.2f}) > prior loss volume ({self.last_loss_volume:.2f})"
                )

        # 4. Margin floor and combined open-risk cap
        margin_status = self.check_margin_and_open_risk(
            account=account,
            active_positions=active_positions,
            symbol_info=symbol_info,
            proposed_volume=proposed_volume,
            proposed_monetary_risk=proposed_monetary_risk
        )
        if not margin_status.can_open_new_trade:
            return False, margin_status.reason

        return True, None

    def record_trade_outcome(self, pnl_usd: float, volume: float) -> Tuple[bool, Optional[str]]:
        """
        Updates consecutive-loss tracker and triggers circuit breaker if limit reached.
        Returns `(breaker_triggered, reason)`.
        """
        risk_cfg = self._cfg.get("risk_guardrails", {})
        max_consec = int(risk_cfg.get("consecutive_loss_limit", 2))

        if pnl_usd < 0:
            self.consecutive_losses += 1
            self.last_trade_was_loss = True
            self.last_loss_volume = volume
            if self.consecutive_losses >= max_consec:
                self.paused = True
                self.pause_reason = f"Consecutive-loss circuit breaker triggered ({self.consecutive_losses} losses)"
                self.critical_alerts.append(self.pause_reason)
                return True, self.pause_reason
        else:
            self.consecutive_losses = 0
            self.last_trade_was_loss = False
            self.last_loss_volume = 0.0

        return False, None

    def evaluate_equity_velocity_kill_switch(
        self,
        account: AccountInfo,
        active_positions: List[Position],
        current_timestamp_sec: Optional[float] = None
    ) -> Tuple[bool, Optional[str]]:
        """
        Monitors floating drawdown velocity: if floating loss exceeds X% of equity
        within Y seconds (default 2.5% in 60s), triggers immediate flatten and pause with CRITICAL alert.
        """
        import time as _time
        now_s = current_timestamp_sec if current_timestamp_sec is not None else _time.time()
        risk_cfg = self._cfg.get("risk_guardrails", {})
        vel_loss_pct = float(risk_cfg.get("equity_velocity_loss_pct", 2.5))
        vel_window_s = float(risk_cfg.get("equity_velocity_window_seconds", 60.0))

        floating_pnl = sum(p.profit for p in active_positions)
        floating_loss = abs(min(0.0, floating_pnl))
        eq = account.equity if account.equity > 0 else account.balance

        self._equity_history.append((now_s, eq, floating_loss))

        # Prune older than window
        while self._equity_history and (now_s - self._equity_history[0][0]) > vel_window_s:
            self._equity_history.popleft()

        if not self._equity_history or eq <= 0:
            return False, None

        peak_eq_in_window = max(item[1] for item in self._equity_history)
        min_loss_in_window = min(item[2] for item in self._equity_history)

        eq_drop_pct = ((peak_eq_in_window - eq) / peak_eq_in_window) * 100.0 if peak_eq_in_window > 0 else 0.0
        floating_spike_pct = ((floating_loss - min_loss_in_window) / eq) * 100.0

        worst_velocity_pct = max(eq_drop_pct, floating_spike_pct, (floating_loss / eq) * 100.0)

        if worst_velocity_pct >= vel_loss_pct:
            reason = (
                f"CRITICAL EQUITY-VELOCITY KILL SWITCH: Floating loss/equity drop reached "
                f"{worst_velocity_pct:.2f}% >= {vel_loss_pct:.2f}% threshold within {vel_window_s:.0f}s! "
                f"Flattening all positions and pausing Fast Mode."
            )
            self.paused = True
            self.pause_reason = reason
            self.critical_alerts.append(reason)
            return True, reason

        return False, None
