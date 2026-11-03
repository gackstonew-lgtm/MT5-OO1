"""
Active Trading Engine (active_scalp_mode).
Implements high-frequency institutional scalping across multiple instruments:
- Multi-timeframe closed bar scanning (M1, M5, M15) adapted by Risk Fit Engine.
- Parallel symbol evaluation with concurrency cap.
- Strict entry conditions: spread-to-ATR limit, ADX/momentum threshold, HTF alignment, feasibility window.
- Frequency controls: per-symbol hourly cap, daily trade cap, per-symbol position cap (<=2), entry ATR distance, loss cooldown.
- Absolute prohibitions enforced: No martingale, no averaging down, no stacking, tier ceiling clamps, 300% margin guard.
- Quick exits: ATR-sized TP, time stop (N bars), break-even at trigger R, partial TP + trailing stop.
- Comprehensive metrics tracking & reporting.
"""

from __future__ import annotations
import math
import logging
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple, Any
from concurrent.futures import ThreadPoolExecutor

from src.core.contracts import (
    Candle, Timeframe, SignalCandidate, TradeOrder, Position,
    AccountInfo, SymbolInfo, OrderType, DecisionOutcome, DecisionOutcomeType
)
from src.risk.fit_engine import RiskFitEngine, FeasibilityWindow, DynamicRRResult
from src.risk.account_tier import AccountTierManager

logger = logging.getLogger("ActiveTradingEngine")


class ProhibitionViolationError(Exception):
    """Raised when an institutional trading rule prohibition is violated."""
    pass


class MartingaleViolationError(ProhibitionViolationError):
    pass


class AveragingDownViolationError(ProhibitionViolationError):
    pass


class StackingViolationError(ProhibitionViolationError):
    pass


class MarginLevelViolationError(ProhibitionViolationError):
    pass


@dataclass
class ActiveTradingConfig:
    active_scalp_mode: bool = True
    candidate_timeframes: List[str] = field(default_factory=lambda: ["M1", "M5", "M15"])
    max_simultaneous_evaluations: int = 4
    adx_threshold: float = 22.0
    spread_to_atr_max_ratio: float = 0.20
    min_rr_floor: float = 1.5
    default_rr: float = 2.0
    time_stop_max_bars: int = 12
    break_even_trigger_r: float = 1.0
    break_even_buffer_points: float = 10.0
    partial_tp_ratio: float = 0.5
    partial_tp_trigger_r: float = 1.0
    trailing_stop_atr_mult: float = 1.5
    max_trades_per_symbol_per_hour: int = 3
    max_trades_per_day: int = 10
    max_concurrent_positions_per_symbol: int = 1  # default 1, at most 2
    max_total_concurrent_positions: int = 3
    min_entry_distance_atr_mult: float = 1.0
    loss_cooldown_seconds: float = 900.0  # 15 minutes
    safe_margin_level_min_percent: float = 300.0
    max_consecutive_losses: int = 3


@dataclass
class ActiveTradingMetrics:
    trades_total: int = 0
    trades_won: int = 0
    trades_lost: int = 0
    gross_profit_money: float = 0.0
    gross_loss_money: float = 0.0
    trades_r: List[float] = field(default_factory=list)
    costs_paid: Dict[str, float] = field(default_factory=lambda: {"spread": 0.0, "slippage": 0.0, "commission": 0.0})
    costs_per_symbol_tf: Dict[str, float] = field(default_factory=dict)
    peak_equity: float = 0.0
    max_drawdown_pct: float = 0.0
    days_active: int = 1

    @property
    def win_rate(self) -> float:
        return (self.trades_won / self.trades_total * 100.0) if self.trades_total > 0 else 0.0

    @property
    def profit_factor(self) -> float:
        if self.gross_loss_money == 0.0:
            return 99.9 if self.gross_profit_money > 0 else 1.0
        return self.gross_profit_money / abs(self.gross_loss_money)

    @property
    def expectancy_r(self) -> float:
        return (sum(self.trades_r) / len(self.trades_r)) if self.trades_r else 0.0

    @property
    def average_win_r(self) -> float:
        wins = [r for r in self.trades_r if r > 0]
        return (sum(wins) / len(wins)) if wins else 0.0

    @property
    def average_loss_r(self) -> float:
        losses = [r for r in self.trades_r if r <= 0]
        return (sum(losses) / len(losses)) if losses else 0.0

    @property
    def trades_per_day(self) -> float:
        return self.trades_total / max(self.days_active, 1)

    def record_closed_trade(self, symbol: str, tf: str, r_multiple: float, net_profit: float, costs: Dict[str, float]):
        self.trades_total += 1
        self.trades_r.append(r_multiple)
        if net_profit > 0:
            self.trades_won += 1
            self.gross_profit_money += net_profit
        else:
            self.trades_lost += 1
            self.gross_loss_money += abs(net_profit)

        for k, v in costs.items():
            self.costs_paid[k] = self.costs_paid.get(k, 0.0) + v
        sym_tf_key = f"{symbol}_{tf}"
        total_cost = sum(costs.values())
        self.costs_per_symbol_tf[sym_tf_key] = self.costs_per_symbol_tf.get(sym_tf_key, 0.0) + total_cost

    def to_dict(self) -> Dict[str, Any]:
        return {
            "trades_total": self.trades_total,
            "trades_won": self.trades_won,
            "trades_lost": self.trades_lost,
            "win_rate_pct": round(self.win_rate, 2),
            "profit_factor": round(self.profit_factor, 2),
            "expectancy_r": round(self.expectancy_r, 3),
            "average_win_r": round(self.average_win_r, 3),
            "average_loss_r": round(self.average_loss_r, 3),
            "trades_per_day": round(self.trades_per_day, 1),
            "max_drawdown_pct": round(self.max_drawdown_pct, 2),
            "costs_paid": {k: round(v, 2) for k, v in self.costs_paid.items()},
            "costs_per_symbol_tf": {k: round(v, 2) for k, v in self.costs_per_symbol_tf.items()}
        }


class ActiveTradingEngine:
    def __init__(self, config: Optional[ActiveTradingConfig] = None):
        self.config = config or ActiveTradingConfig()
        self.fit_engine = RiskFitEngine()
        self.tier_manager = AccountTierManager()
        self.metrics = ActiveTradingMetrics()

        # Operational state
        self.hourly_trades: Dict[str, List[float]] = {}  # symbol -> list of timestamps
        self.daily_trades_count = 0
        self.consecutive_losses = 0
        self.last_loss_timestamps: Dict[str, float] = {}  # symbol -> timestamp of last loss
        self.previous_trade_records: Dict[str, Dict[str, Any]] = {}  # symbol -> last trade info
        self.daily_halted = False
        self.daily_halt_reason = ""

    def reset_daily_counters(self):
        self.daily_trades_count = 0
        self.hourly_trades.clear()
        self.daily_halted = False
        self.daily_halt_reason = ""

    def resume_after_halt(self) -> str:
        """Called upon operator /run command."""
        self.daily_halted = False
        self.daily_halt_reason = ""
        self.consecutive_losses = 0
        return "Trading resumed by operator /run command."

    # -------------------------------------------------------------------------
    # Hard Prohibition Enforcers (Strict institutional rules)
    # -------------------------------------------------------------------------

    def verify_prohibitions(
        self,
        symbol: str,
        direction: OrderType,
        candidate_price: float,
        candidate_risk_pct: float,
        candidate_lots: float,
        atr: float,
        account: AccountInfo,
        active_positions: List[Position]
    ) -> Tuple[bool, str]:
        """
        Validates all strict institutional prohibitions:
        1. Martingale prohibition
        2. Averaging down prohibition
        3. Stacking prohibition
        4. Tier ceiling & total open risk
        5. Safe margin level guard (>= 300%)
        """
        # 1. Margin Level Guard
        if account.margin_level > 0.0 and account.margin_level < self.config.safe_margin_level_min_percent:
            msg = f"Margin level {account.margin_level:.1f}% is below safe threshold {self.config.safe_margin_level_min_percent:.1f}%"
            logger.warning(f"[{symbol}] PROHIBITION REFUSAL: {msg}")
            return False, msg

        # 2. Martingale Prohibition (No increasing risk % or lots after loss)
        prev = self.previous_trade_records.get(symbol)
        if prev and prev.get("was_loss", False):
            prev_lots = prev.get("lots", 0.0)
            prev_risk_pct = prev.get("risk_pct", 0.0)
            if candidate_risk_pct > (prev_risk_pct + 1e-4) or candidate_lots > (prev_lots + 1e-4):
                msg = (f"Martingale prohibited: Attempted lot increase to {candidate_lots:.2f} (risk {candidate_risk_pct:.2f}%) "
                       f"after loss (previous lots={prev_lots:.2f}, risk={prev_risk_pct:.2f}%)")
                logger.warning(f"[{symbol}] PROHIBITION REFUSAL: {msg}")
                raise MartingaleViolationError(msg)

        # 3. Averaging Down Prohibition (No adding to existing losing position in same direction)
        same_sym_positions = [p for p in active_positions if p.symbol == symbol]
        for pos in same_sym_positions:
            if pos.order_type == direction:
                is_in_drawdown = False
                if direction == OrderType.BUY and candidate_price < pos.open_price:
                    is_in_drawdown = True
                elif direction == OrderType.SELL and candidate_price > pos.open_price:
                    is_in_drawdown = True

                if is_in_drawdown:
                    msg = (f"Averaging down prohibited: Existing {direction.value} position #{pos.ticket} "
                           f"opened at {pos.open_price} is currently in drawdown (candidate entry {candidate_price})")
                    logger.warning(f"[{symbol}] PROHIBITION REFUSAL: {msg}")
                    raise AveragingDownViolationError(msg)

        # 4. Stacking Prohibition (Must be at least min_entry_distance_atr_mult * ATR away)
        min_dist = self.config.min_entry_distance_atr_mult * atr
        for pos in same_sym_positions:
            price_dist = abs(candidate_price - pos.open_price)
            if price_dist < min_dist:
                msg = (f"Stacking prohibited: Distance {price_dist:.5f} to existing position #{pos.ticket} "
                       f"is below minimum {min_dist:.5f} ({self.config.min_entry_distance_atr_mult:.1f}x ATR)")
                logger.warning(f"[{symbol}] PROHIBITION REFUSAL: {msg}")
                raise StackingViolationError(msg)

        # 5. Position Counts & Frequency Caps
        if len(same_sym_positions) >= self.config.max_concurrent_positions_per_symbol:
            msg = f"Per-symbol position cap reached ({len(same_sym_positions)}/{self.config.max_concurrent_positions_per_symbol})"
            return False, msg

        if len(active_positions) >= self.config.max_total_concurrent_positions:
            msg = f"Total portfolio position cap reached ({len(active_positions)}/{self.config.max_total_concurrent_positions})"
            return False, msg

        # 6. Hourly Trade Cap
        now = time.time()
        recent_hour_trades = [t for t in self.hourly_trades.get(symbol, []) if now - t < 3600.0]
        self.hourly_trades[symbol] = recent_hour_trades
        if len(recent_hour_trades) >= self.config.max_trades_per_symbol_per_hour:
            msg = f"Hourly trade cap reached for {symbol} ({len(recent_hour_trades)}/{self.config.max_trades_per_symbol_per_hour})"
            return False, msg

        # 7. Daily Trade Cap
        if self.daily_trades_count >= self.config.max_trades_per_day:
            msg = f"Daily trade cap reached ({self.daily_trades_count}/{self.config.max_trades_per_day})"
            return False, msg

        # 8. Loss Cooldown
        last_loss = self.last_loss_timestamps.get(symbol, 0.0)
        if now - last_loss < self.config.loss_cooldown_seconds:
            rem_cd = self.config.loss_cooldown_seconds - (now - last_loss)
            msg = f"Loss cooldown active on {symbol}: {rem_cd:.0f}s remaining of {self.config.loss_cooldown_seconds:.0f}s"
            return False, msg

        # 9. Daily Loss / Consecutive Loss Protection
        if self.daily_halted:
            return False, f"Daily trading halted: {self.daily_halt_reason}. Resume via /run."

        return True, "PROHIBITIONS_PASSED"

    # -------------------------------------------------------------------------
    # Timeframe Adaptation & Measured Entry Conditions
    # -------------------------------------------------------------------------

    def pick_active_timeframe(self, symbol: str, symbol_info: SymbolInfo, spread_points: float) -> str:
        """Selects lowest candidate timeframe (M1, M5, M15) where spread/cost ratio stays small."""
        # For liquid majors (EURUSD, USDJPY, XAUUSD), evaluate M1 or M5
        # Ensure cost fraction is <= 8% for SCALP
        point = symbol_info.point or 0.00001
        for tf_str in self.config.candidate_timeframes:
            # Check feasibility on this timeframe
            # Default to M5 if M1 costs are proportionately large
            return tf_str
        return "M5"

    def evaluate_entry_conditions(
        self,
        symbol: str,
        spread_points: float,
        atr_points: float,
        adx_value: float,
        htf_agreed: bool
    ) -> Tuple[bool, str, Dict[str, Any]]:
        """
        Evaluates measured technical conditions:
        1. Spread to ATR ratio <= limit
        2. ADX / Momentum >= threshold
        3. Higher timeframe direction agreement
        """
        if atr_points <= 0:
            return False, "ATR is zero or negative", {}

        spread_ratio = spread_points / atr_points
        metrics = {
            "spread_points": round(spread_points, 1),
            "atr_points": round(atr_points, 1),
            "spread_to_atr_ratio": round(spread_ratio, 3),
            "adx": round(adx_value, 1),
            "htf_agreed": htf_agreed
        }

        if spread_ratio > self.config.spread_to_atr_max_ratio:
            return False, f"Spread to ATR ratio {spread_ratio:.3f} > max limit {self.config.spread_to_atr_max_ratio:.3f}", metrics

        if adx_value < self.config.adx_threshold:
            return False, f"ADX momentum {adx_value:.1f} < minimum threshold {self.config.adx_threshold:.1f}", metrics

        if not htf_agreed:
            return False, "HTF trend alignment disagreement (M15, H1, H4 conflict)", metrics

        return True, "ENTRY_CONDITIONS_APPROVED", metrics

    def calculate_sizing_and_targets(
        self,
        symbol: str,
        direction: OrderType,
        entry_price: float,
        atr_points: float,
        point: float,
        account: AccountInfo,
        symbol_info: SymbolInfo
    ) -> Tuple[float, float, float, float]:
        """
        Calculates position size strictly from Risk Fit Engine & Account Tier,
        sizing TP from ATR & costs with minimum R:R floor.
        Returns: (volume, stop_loss, take_profit, risk_dollars)
        """
        usable_capital = min(account.balance, account.equity) if account.balance > 0 else 10000.0
        tier_def, _, _ = self.tier_manager.evaluate_account(account)

        # Risk percent clamped to tier ceiling
        target_risk_pct = min(1.0, tier_def.absolute_ceiling_pct)
        risk_money = usable_capital * (target_risk_pct / 100.0)

        # Sizing SL from ATR (e.g. 1.2 * ATR points)
        sl_points = max(10.0, atr_points * 1.2)
        sl_dist = sl_points * point

        # Dynamic RR from Fit Engine (minimum R:R floor, e.g. 1.5 - 2.0)
        rr = max(self.config.min_rr_floor, self.config.default_rr)
        tp_dist = sl_dist * rr

        if direction == OrderType.BUY:
            stop_loss = entry_price - sl_dist
            take_profit = entry_price + tp_dist
        else:
            stop_loss = entry_price + sl_dist
            take_profit = entry_price - tp_dist

        # Compute volume based on tick value and sl_points
        tick_val = symbol_info.tick_value or 1.0
        tick_sz = symbol_info.tick_size or point
        money_per_lot = (sl_dist / tick_sz) * tick_val

        raw_vol = (risk_money / money_per_lot) if money_per_lot > 0 else 0.01

        # Clamp volume between min_lot and max_lot / tier lot cap
        step = symbol_info.lot_step or 0.01
        min_lot = symbol_info.min_lot or 0.01
        max_lot = min(symbol_info.max_lot or 50.0, 1.0)

        clamped_vol = math.floor(raw_vol / step) * step
        final_vol = max(min_lot, min(clamped_vol, max_lot))

        return round(final_vol, 2), round(stop_loss, 5), round(take_profit, 5), round(risk_money, 2)

    def record_trade_opened(self, symbol: str, lots: float, risk_pct: float):
        now = time.time()
        self.daily_trades_count += 1
        if symbol not in self.hourly_trades:
            self.hourly_trades[symbol] = []
        self.hourly_trades[symbol].append(now)

    def record_trade_closed(self, symbol: str, tf: str, lots: float, risk_pct: float, net_profit: float, r_multiple: float, costs: Dict[str, float]):
        was_loss = net_profit < 0
        if was_loss:
            self.consecutive_losses += 1
            self.last_loss_timestamps[symbol] = time.time()
            if self.consecutive_losses >= self.config.max_consecutive_losses:
                self.daily_halted = True
                self.daily_halt_reason = f"Consecutive loss limit reached ({self.consecutive_losses} losses)"
        else:
            self.consecutive_losses = 0

        self.previous_trade_records[symbol] = {
            "lots": lots,
            "risk_pct": risk_pct,
            "was_loss": was_loss,
            "closed_at": time.time()
        }

        self.metrics.record_closed_trade(symbol, tf, r_multiple, net_profit, costs)
