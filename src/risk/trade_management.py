"""
Dynamic Trade and Position Management Engine.
Manages open positions during their lifecycle:
- Account-currency-aware Net Floating Profit calculation (gross + commission + swap)
- Intelligent $5.00+ Net Profit Target & Strong-Momentum Profit Extension ($8, $10, $15+)
- Minimum Profit Floor Locking & Progressive Profit Ratchet
- Structural Reversal / Setup Invalidation Exit while in profit
- Cost-Aware Break-even protection (at +1.0R standard, or +0.5R in continuous scalping mode, covering spread + commission + buffer)
- Partial profit-taking at +1.5R (and optional 1.0R)
- Structural, ATR Volatility-Adaptive, and Swing trailing stops
- Dynamic Take-Profit on momentum stall / exhaustion
- Time-stop and Session-end flatten exits
"""

from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime, timezone
import logging
from typing import Any, Dict, List, Optional, Tuple

from src.core.config import RiskConfig
from src.core.contracts import Candle, OrderType, PlanAmendment, Position, SymbolInfo

logger = logging.getLogger("TradeManagement")


@dataclass
class PositionAction:
    ticket: int
    symbol: str
    action_type: str  # "MODIFY_SL", "PARTIAL_CLOSE", "FULL_CLOSE"
    new_sl: Optional[float] = None
    close_volume: Optional[float] = None
    reason: str = ""
    amendment: Optional[PlanAmendment] = None


class TradeManagementEngine:
    def __init__(self, config: Optional[RiskConfig] = None):
        self.config = config or RiskConfig()
        # Per-ticket lifecycle state. The bridge returns fresh Position objects each
        # cycle, so peak profit / break-even / partial flags must be persisted here.
        self._position_states: Dict[int, Dict[str, Any]] = {}

    def _restore_state(self, position: Position) -> Dict[str, Any]:
        state = self._position_states.setdefault(
            int(position.ticket),
            {"peak_net_profit": 0.0, "break_even_activated": False, "partial_taken": False},
        )
        position.peak_net_profit = max(
            float(getattr(position, "peak_net_profit", 0.0) or 0.0),
            float(state.get("peak_net_profit", 0.0) or 0.0),
        )
        if state.get("break_even_activated"):
            position.break_even_activated = True
        if state.get("partial_taken"):
            position.partial_taken = True
        return state

    def _persist_state(self, position: Position, state: Dict[str, Any]) -> None:
        state["peak_net_profit"] = max(
            float(state.get("peak_net_profit", 0.0) or 0.0),
            float(getattr(position, "peak_net_profit", 0.0) or 0.0),
        )
        state["break_even_activated"] = bool(state.get("break_even_activated")) or bool(
            getattr(position, "break_even_activated", False)
        )
        state["partial_taken"] = bool(state.get("partial_taken")) or bool(
            getattr(position, "partial_taken", False)
        )

    def forget_position(self, ticket: int) -> None:
        """Drops lifecycle state when a position is fully closed."""
        self._position_states.pop(int(ticket), None)

    @staticmethod
    def calculate_net_profit(position: Position, symbol_info: SymbolInfo) -> float:
        """
        Computes net floating profit in account currency:
        net_profit = gross_profit + commission + swap.
        If position.profit is 0 but current_price differs from open_price, derives gross profit
        using symbol tick_size, tick_value, and volume.
        """
        is_buy = position.order_type == OrderType.BUY
        price_diff = (
            (position.current_price - position.open_price)
            if is_buy
            else (position.open_price - position.current_price)
        )
        gross_profit = float(position.profit)
        if abs(gross_profit) < 1e-9 and abs(price_diff) > 1e-9:
            tick_size = symbol_info.tick_size if symbol_info.tick_size > 0 else (symbol_info.point or 0.00001)
            tick_value = symbol_info.tick_value if symbol_info.tick_value > 0 else 1.0
            gross_profit = (price_diff / tick_size) * tick_value * float(position.volume)

        commission = float(getattr(position, "commission", 0.0))
        swap = float(getattr(position, "swap", 0.0))
        return round(gross_profit + commission + swap, 2)

    @staticmethod
    def calculate_atr(candles: List[Candle], period: int = 14) -> float:
        """Calculates Average True Range from completed candles."""
        if not candles:
            return 0.0
        completed = [c for c in candles if getattr(c, "complete", True)]
        if len(completed) < 2:
            completed = list(candles)
        if len(completed) < 2:
            return completed[-1].range if completed else 0.0
        trs: List[float] = []
        window = completed[-(period + 1) :]
        for i in range(1, len(window)):
            cur = window[i]
            prev = window[i - 1]
            tr = max(
                cur.high - cur.low,
                abs(cur.high - prev.close),
                abs(cur.low - prev.close),
            )
            trs.append(tr)
        return sum(trs) / len(trs) if trs else completed[-1].range

    @staticmethod
    def _assess_momentum_and_structure(
        position: Position,
        recent_candles: List[Candle],
        net_profit: float,
    ) -> Tuple[bool, bool, bool, str]:
        """
        Evaluates recent completed candles to determine:
        (is_strong_momentum, is_weakening_momentum, is_structural_reversal, details)
        """
        if not recent_candles or len(recent_candles) < 3:
            return True, False, False, "Insufficient bars for momentum check; defaulting to hold"

        is_buy = position.order_type == OrderType.BUY
        completed = [c for c in recent_candles if getattr(c, "complete", True)]
        if len(completed) < 3:
            completed = list(recent_candles)

        last = completed[-1]
        prev = completed[-2]

        # 1. Check for Structural Reversal against open position (requires >= 5 bars)
        is_structural_reversal = False
        if len(completed) >= 5:
            prior_window = completed[-5:-1]
            prior_swing_low = min(c.low for c in prior_window)
            prior_swing_high = max(c.high for c in prior_window)
            last_range = max(last.range, 1e-9)
            body_ratio = last.body / last_range
            if is_buy and last.is_bearish and last.close < prior_swing_low and body_ratio >= 0.45:
                is_structural_reversal = True
                return (
                    False,
                    True,
                    True,
                    f"Bearish structural break below prior swing low {prior_swing_low:.5f} (close={last.close:.5f})",
                )
            if (not is_buy) and last.is_bullish and last.close > prior_swing_high and body_ratio >= 0.45:
                is_structural_reversal = True
                return (
                    False,
                    True,
                    True,
                    f"Bullish structural break above prior swing high {prior_swing_high:.5f} (close={last.close:.5f})",
                )

        # 2. Check for Weakening Momentum / Exhaustion
        last_range = max(last.range, 1e-9)
        if is_buy:
            two_counter_bars = last.is_bearish and prev.is_bearish
            exhaustion_wick = (last.upper_wick / last_range >= 0.55) and (last.close <= last.open)
        else:
            two_counter_bars = last.is_bullish and prev.is_bullish
            exhaustion_wick = (last.lower_wick / last_range >= 0.55) and (last.close >= last.open)

        peak_net = float(getattr(position, "peak_net_profit", 0.0))
        profit_giveback = peak_net >= 6.5 and net_profit <= peak_net * 0.72

        if two_counter_bars or exhaustion_wick or profit_giveback:
            reason_parts = []
            if two_counter_bars:
                reason_parts.append("2 consecutive counter-direction closed bars")
            if exhaustion_wick:
                reason_parts.append("exhaustion rejection wick >= 55% of bar range")
            if profit_giveback:
                reason_parts.append(f"profit retraced from peak ${peak_net:.2f} to ${net_profit:.2f}")
            return False, True, False, ", ".join(reason_parts)

        return True, False, False, "Momentum & market structure remain aligned with position"

    def compute_cost_aware_break_even_sl(
        self,
        position: Position,
        symbol_info: SymbolInfo,
    ) -> Optional[float]:
        """
        Calculates break-even Stop-Loss price including spread, commission, swap,
        and configured break_even_buffer_pips so a break-even exit never results in a net loss.
        """
        point = symbol_info.point if symbol_info.point > 0 else 0.00001
        pip_size = point * 10
        tick_size = symbol_info.tick_size if symbol_info.tick_size > 0 else point
        tick_value = symbol_info.tick_value if symbol_info.tick_value > 0 else 1.0
        usd_per_price_unit = (tick_value / tick_size) * max(float(position.volume), 0.01)

        buffer_dist = float(getattr(self.config, "break_even_buffer_pips", 2.0)) * pip_size
        costs_usd = -(float(getattr(position, "commission", 0.0)) + float(getattr(position, "swap", 0.0)))
        cost_price_offset = (max(0.0, costs_usd) / usd_per_price_unit) if usd_per_price_unit > 1e-9 else 0.0
        spread_offset = float(getattr(symbol_info, "spread", 0.0)) * point * 0.5
        total_offset = max(buffer_dist, cost_price_offset + spread_offset + (0.5 * pip_size))

        min_stop_pts = max(int(getattr(symbol_info, "stops_level", 0)), int(getattr(symbol_info, "freeze_level", 0)), 2)
        min_stop_dist = min_stop_pts * point

        is_buy = position.order_type == OrderType.BUY
        if is_buy:
            candidate_sl = symbol_info.round_price(position.open_price + total_offset)
            if candidate_sl > position.stop_loss and candidate_sl <= (position.current_price - min_stop_dist):
                return candidate_sl
            # Fallback to standard buffer if cost offset is too close to current price
            fallback_sl = symbol_info.round_price(position.open_price + buffer_dist)
            if fallback_sl > position.stop_loss and fallback_sl < position.current_price:
                return fallback_sl
        else:
            candidate_sl = symbol_info.round_price(position.open_price - total_offset)
            if (position.stop_loss <= 0 or candidate_sl < position.stop_loss) and candidate_sl >= (position.current_price + min_stop_dist):
                return candidate_sl
            fallback_sl = symbol_info.round_price(position.open_price - buffer_dist)
            if (position.stop_loss <= 0 or fallback_sl < position.stop_loss) and fallback_sl > position.current_price:
                return fallback_sl
        return None

    def compute_atr_trailing_sl(
        self,
        position: Position,
        symbol_info: SymbolInfo,
        recent_candles: List[Candle],
    ) -> Optional[float]:
        """
        Calculates ATR volatility-adaptive trailing stop that locks in profit as price moves favorably.
        """
        atr = self.calculate_atr(recent_candles, period=14)
        if atr <= 0:
            return None
        point = symbol_info.point if symbol_info.point > 0 else 0.00001
        mult = float(getattr(self.config, "trailing_stop_atr_multiplier", 1.2))
        trail_dist = max(atr * mult, max(int(getattr(symbol_info, "stops_level", 0)), 3) * point * 2.0)

        is_buy = position.order_type == OrderType.BUY
        if is_buy:
            candidate_sl = symbol_info.round_price(position.current_price - trail_dist)
            if candidate_sl > position.open_price and candidate_sl > position.stop_loss and candidate_sl < position.current_price:
                return candidate_sl
        else:
            candidate_sl = symbol_info.round_price(position.current_price + trail_dist)
            if (
                candidate_sl < position.open_price
                and (position.stop_loss <= 0 or candidate_sl < position.stop_loss)
                and candidate_sl > position.current_price
            ):
                return candidate_sl
        return None

    def _compute_profit_lock_sl(
        self,
        position: Position,
        symbol_info: SymbolInfo,
        target_locked_net_usd: float,
    ) -> Optional[float]:
        """
        Calculates the exact Stop Loss price that locks `target_locked_net_usd` in net profit
        (after commission and swap), while respecting broker stops_level and freeze_level.
        """
        if position.volume <= 0:
            return None
        point = symbol_info.point if symbol_info.point > 0 else 0.00001
        tick_size = symbol_info.tick_size if symbol_info.tick_size > 0 else point
        tick_value = symbol_info.tick_value if symbol_info.tick_value > 0 else 1.0
        usd_per_price_unit = (tick_value / tick_size) * float(position.volume)
        if usd_per_price_unit <= 1e-9:
            return None

        # Account for commission + swap so net profit at SL >= target_locked_net_usd
        costs_usd = -(float(getattr(position, "commission", 0.0)) + float(getattr(position, "swap", 0.0)))
        required_gross_usd = max(0.0, target_locked_net_usd + max(0.0, costs_usd))
        price_offset = required_gross_usd / usd_per_price_unit

        min_stop_pts = max(int(getattr(symbol_info, "stops_level", 0)), int(getattr(symbol_info, "freeze_level", 0)), 2)
        min_stop_dist = min_stop_pts * point

        is_buy = position.order_type == OrderType.BUY
        if is_buy:
            desired_sl = position.open_price + price_offset
            max_allowed_sl = position.current_price - min_stop_dist
            candidate_sl = symbol_info.round_price(min(desired_sl, max_allowed_sl))
            if candidate_sl > position.open_price and candidate_sl > position.stop_loss and candidate_sl < position.current_price:
                return candidate_sl
        else:
            desired_sl = position.open_price - price_offset
            min_allowed_sl = position.current_price + min_stop_dist
            candidate_sl = symbol_info.round_price(max(desired_sl, min_allowed_sl))
            if (
                candidate_sl < position.open_price
                and (position.stop_loss <= 0 or candidate_sl < position.stop_loss)
                and candidate_sl > position.current_price
            ):
                return candidate_sl
        return None

    def evaluate_position(
        self,
        position: Position,
        symbol_info: SymbolInfo,
        recent_candles: List[Candle],
        scalp_mode: bool = False,
    ) -> List[PositionAction]:
        """
        Evaluates an active position against lifecycle management rules:
        - Time-stop & Session-end flatten
        - Intelligent $5.00+ net profit target, momentum extension ($8, $10, $15+), and profit floor lock
        - Dynamic take-profit in continuous scalping mode when momentum stalls in profit
        - Structural reversal exit while in profit
        - Cost-aware break-even, partial take-profit, and structural/ATR/swing trailing stops
        """
        actions: List[PositionAction] = []
        state = self._restore_state(position)
        is_buy = position.order_type == OrderType.BUY
        point = symbol_info.point if symbol_info.point > 0 else 0.00001
        pip_size = point * 10
        is_scalp_pos = scalp_mode or str(getattr(position, "comment", "")).upper().startswith("SCALP")

        # Current profit distance in price
        if is_buy:
            profit_dist = position.current_price - position.open_price
        else:
            profit_dist = position.open_price - position.current_price

        # Initial risk distance (R distance)
        r_dist = position.initial_sl_distance
        if r_dist <= 0:
            r_dist = abs(position.open_price - position.stop_loss)
            if r_dist <= 0:
                return actions

        current_r = profit_dist / r_dist
        net_profit = self.calculate_net_profit(position, symbol_info)
        if net_profit > getattr(position, "peak_net_profit", 0.0):
            position.peak_net_profit = net_profit
        self._persist_state(position, state)

        # 0A. Time Stop (close if position open >= time_stop_max_bars without reaching SL/TP)
        if getattr(self.config, "enable_time_stop", False) and position.open_time and recent_candles:
            bars_open = len([c for c in recent_candles if c.time >= position.open_time])
            if bars_open >= getattr(self.config, "time_stop_max_bars", 32):
                act = PositionAction(
                    ticket=position.ticket,
                    symbol=position.symbol,
                    action_type="FULL_CLOSE",
                    reason=f"Time stop triggered: {bars_open} bars elapsed (max {self.config.time_stop_max_bars})",
                )
                logger.info(f"[TRADE] Position Action #{act.ticket} on {act.symbol}: {act.action_type} | {act.reason}")
                actions.append(act)
                return actions

        # 0B. End-of-Session Flatten
        if getattr(self.config, "enable_session_end_flatten", False) and recent_candles:
            last_time = recent_candles[-1].time
            is_session_end = (last_time.hour >= 21) or (last_time.weekday() == 4 and last_time.hour >= 20)
            if is_session_end:
                act = PositionAction(
                    ticket=position.ticket,
                    symbol=position.symbol,
                    action_type="FULL_CLOSE",
                    reason=f"End-of-session flatten triggered at {last_time.strftime('%H:%M UTC')}",
                )
                logger.info(f"[TRADE] Position Action #{act.ticket} on {act.symbol}: {act.action_type} | {act.reason}")
                actions.append(act)
                return actions

        # 0C. Intelligent $5+ Net Profit Management, Profit Extension, Dynamic TP & Structural Reversal Protection
        min_net_target = float(getattr(self.config, "min_net_profit_target_usd", 5.0))
        floor_lock_usd = float(getattr(self.config, "profit_floor_lock_usd", 5.0))
        enable_5usd = bool(getattr(self.config, "enable_profit_management_5usd", True))
        enable_rev_exit = bool(getattr(self.config, "enable_structural_reversal_exit", True))
        allow_extension = bool(getattr(self.config, "strong_momentum_profit_extension", True))
        ext_lock_ratio = float(getattr(self.config, "profit_extension_lock_ratio", 0.65))
        dynamic_tp = bool(getattr(self.config, "dynamic_take_profit", True))

        is_strong_mom, is_weak_mom, is_struct_rev, mom_details = self._assess_momentum_and_structure(
            position=position,
            recent_candles=recent_candles,
            net_profit=net_profit,
        )

        # Case 1: Confirmed structural reversal while position is in positive net profit (or in scalp mode against structure)
        if enable_rev_exit and is_struct_rev and (net_profit > 0.0 or (is_scalp_pos and current_r >= -0.35)):
            act = PositionAction(
                ticket=position.ticket,
                symbol=position.symbol,
                action_type="FULL_CLOSE",
                reason=f"Structural reversal exit (${net_profit:.2f} net): {mom_details}",
            )
            logger.info(f"[TRADE] Position Action #{act.ticket} on {act.symbol}: {act.action_type} | {act.reason}")
            actions.append(act)
            return actions

        # Case 2: Net profit >= $5.00 threshold
        if enable_5usd and net_profit >= min_net_target:
            if is_weak_mom or not allow_extension:
                # Momentum is weakening or extension disabled -> bank the >= $5.00 net profit immediately
                act = PositionAction(
                    ticket=position.ticket,
                    symbol=position.symbol,
                    action_type="FULL_CLOSE",
                    reason=(
                        f"Net profit target >= ${min_net_target:.2f} secured (${net_profit:.2f} net) "
                        f"on weakening momentum ({mom_details})"
                    ),
                )
                logger.info(f"[TRADE] Position Action #{act.ticket} on {act.symbol}: {act.action_type} | {act.reason}")
                actions.append(act)
                return actions
            elif is_strong_mom:
                # Momentum remains strong -> allow trade to run toward $8, $10, $15+ while locking profit floor!
                if net_profit >= 8.0:
                    target_lock_usd = max(floor_lock_usd, round(net_profit * ext_lock_ratio, 2))
                else:
                    target_lock_usd = min(floor_lock_usd, round(net_profit * 0.85, 2))

                lock_sl = self._compute_profit_lock_sl(position, symbol_info, target_lock_usd)
                if lock_sl is not None:
                    position.profit_floor_locked = True
                    actions.append(
                        PositionAction(
                            ticket=position.ticket,
                            symbol=position.symbol,
                            action_type="MODIFY_SL",
                            new_sl=lock_sl,
                            reason=(
                                f"Profit extension active (${net_profit:.2f} net, strong momentum): "
                                f"locked ~${target_lock_usd:.2f} net profit floor at SL {lock_sl}"
                            ),
                            amendment=PlanAmendment(
                                amendment_type="PROFIT_FLOOR_LOCK",
                                old_sl=position.stop_loss,
                                new_sl=lock_sl,
                                timestamp=datetime.now(timezone.utc),
                                reason=f"Locked ~${target_lock_usd:.2f} net profit floor at SL {lock_sl} (net=${net_profit:.2f})",
                            ),
                        )
                    )

        # Case 2B: Dynamic Take-Profit in Continuous Scalping Mode (when momentum stalls at >= 1.2R in positive net profit)
        if is_scalp_pos and dynamic_tp and is_weak_mom and current_r >= 1.2 and net_profit > 0.0:
            act = PositionAction(
                ticket=position.ticket,
                symbol=position.symbol,
                action_type="FULL_CLOSE",
                reason=f"Dynamic Take-Profit triggered at +{current_r:.2f}R (${net_profit:.2f} net) on momentum stall ({mom_details})",
            )
            logger.info(f"[TRADE] Position Action #{act.ticket} on {act.symbol}: {act.action_type} | {act.reason}")
            actions.append(act)
            return actions

        # 1A. Break-Even Protection (at +1.0R standard, or +0.5R in continuous scalping mode)
        be_threshold_r = (
            float(getattr(self.config, "scalp_break_even_at_r", 0.5))
            if is_scalp_pos
            else float(getattr(self.config, "break_even_at_r", 1.0))
        )
        if self.config.enable_break_even and not position.break_even_activated:
            if current_r >= be_threshold_r:
                if is_scalp_pos:
                    new_sl = self.compute_cost_aware_break_even_sl(position, symbol_info)
                else:
                    buffer_dist = self.config.break_even_buffer_pips * pip_size
                    if is_buy:
                        cand_sl = symbol_info.round_price(position.open_price + buffer_dist)
                        new_sl = cand_sl if cand_sl > position.stop_loss else None
                    else:
                        cand_sl = symbol_info.round_price(position.open_price - buffer_dist)
                        new_sl = cand_sl if cand_sl < position.stop_loss else None

                if new_sl is not None:
                    state["break_even_activated"] = True
                    position.break_even_activated = True
                    actions.append(
                        PositionAction(
                            ticket=position.ticket,
                            symbol=position.symbol,
                            action_type="MODIFY_SL",
                            new_sl=new_sl,
                            reason=f"Break-even activated at +{current_r:.2f}R (SL -> {new_sl})",
                            amendment=PlanAmendment(
                                amendment_type="BREAK_EVEN",
                                old_sl=position.stop_loss,
                                new_sl=new_sl,
                                timestamp=datetime.now(timezone.utc),
                                reason=f"Break-even activated at +{current_r:.2f}R (SL -> {new_sl})",
                            ),
                        )
                    )

        # 1B. Partial Take-Profit at 1.0R with Break-Even
        if getattr(self.config, "enable_partial_at_1r", False) and not position.partial_taken and current_r >= 1.0:
            raw_partial_vol = position.volume * 0.5
            partial_vol = symbol_info.normalize_volume(raw_partial_vol)
            if partial_vol >= symbol_info.min_lot and partial_vol < position.volume:
                state["partial_taken"] = True
                position.partial_taken = True
                actions.append(
                    PositionAction(
                        ticket=position.ticket,
                        symbol=position.symbol,
                        action_type="PARTIAL_CLOSE",
                        close_volume=partial_vol,
                        reason=f"Partial TP at 1.0R triggered (+{current_r:.2f}R, close {partial_vol} lots)",
                        amendment=PlanAmendment(
                            amendment_type="PARTIAL_TP",
                            old_sl=position.stop_loss,
                            new_sl=position.stop_loss,
                            partial_volume=partial_vol,
                            timestamp=datetime.now(timezone.utc),
                            reason=f"Partial TP at 1.0R (+{current_r:.2f}R)",
                        ),
                    )
                )

        # 2. Partial Profit-Taking (scalp 1.0R by default, standard 1.5R)
        if self.config.enable_partial_tp and not position.partial_taken:
            partial_threshold_r = (
                float(getattr(self.config, "scalp_partial_tp_at_r", 1.0))
                if is_scalp_pos
                else float(self.config.partial_tp_at_r)
            )
            if current_r >= partial_threshold_r:
                raw_partial_vol = position.volume * self.config.partial_tp_ratio
                partial_vol = symbol_info.normalize_volume(raw_partial_vol)
                if partial_vol >= symbol_info.min_lot and partial_vol < position.volume:
                    state["partial_taken"] = True
                    position.partial_taken = True
                    actions.append(
                        PositionAction(
                            ticket=position.ticket,
                            symbol=position.symbol,
                            action_type="PARTIAL_CLOSE",
                            close_volume=partial_vol,
                            reason=f"Partial TP triggered at +{current_r:.2f}R (close {partial_vol} lots)",
                            amendment=PlanAmendment(
                                amendment_type="PARTIAL_TP",
                                old_sl=position.stop_loss,
                                new_sl=position.stop_loss,
                                partial_volume=partial_vol,
                                timestamp=datetime.now(timezone.utc),
                                reason=f"Partial TP triggered at +{current_r:.2f}R",
                            ),
                        )
                    )

        # 3. Structural & ATR Trailing Stop
        if self.config.trailing_stop_enabled and current_r > 1.5 and len(recent_candles) >= 3:
            recent_c = recent_candles[-2]  # Last completed candle
            if is_buy and recent_c.low > position.stop_loss:
                new_trail_sl = symbol_info.round_price(recent_c.low - (2 * pip_size))
                if new_trail_sl > position.stop_loss and new_trail_sl < position.current_price:
                    actions.append(
                        PositionAction(
                            ticket=position.ticket,
                            symbol=position.symbol,
                            action_type="MODIFY_SL",
                            new_sl=new_trail_sl,
                            reason=f"Structural trailing stop advanced to {new_trail_sl}",
                            amendment=PlanAmendment(
                                amendment_type="TRAILING_STOP",
                                old_sl=position.stop_loss,
                                new_sl=new_trail_sl,
                                timestamp=datetime.now(timezone.utc),
                                reason=f"Structural trailing stop advanced to {new_trail_sl}",
                            ),
                        )
                    )
            elif not is_buy and recent_c.high < position.stop_loss:
                new_trail_sl = symbol_info.round_price(recent_c.high + (2 * pip_size))
                if new_trail_sl < position.stop_loss and new_trail_sl > position.current_price:
                    actions.append(
                        PositionAction(
                            ticket=position.ticket,
                            symbol=position.symbol,
                            action_type="MODIFY_SL",
                            new_sl=new_trail_sl,
                            reason=f"Structural trailing stop advanced to {new_trail_sl}",
                            amendment=PlanAmendment(
                                amendment_type="TRAILING_STOP",
                                old_sl=position.stop_loss,
                                new_sl=new_trail_sl,
                                timestamp=datetime.now(timezone.utc),
                                reason=f"Structural trailing stop advanced to {new_trail_sl}",
                            ),
                        )
                    )
        elif self.config.trailing_stop_enabled and is_scalp_pos and current_r >= 0.85 and len(recent_candles) >= 3:
            atr_sl = self.compute_atr_trailing_sl(position, symbol_info, recent_candles)
            if atr_sl is not None:
                actions.append(
                    PositionAction(
                        ticket=position.ticket,
                        symbol=position.symbol,
                        action_type="MODIFY_SL",
                        new_sl=atr_sl,
                        reason=f"ATR volatility-adaptive trailing stop advanced to {atr_sl} (+{current_r:.2f}R)",
                        amendment=PlanAmendment(
                            amendment_type="TRAILING_STOP",
                            old_sl=position.stop_loss,
                            new_sl=atr_sl,
                            timestamp=datetime.now(timezone.utc),
                            reason=f"ATR volatility-adaptive trailing stop advanced to {atr_sl}",
                        ),
                    )
                )

        # 4. Structure-Based Trailing Stop (trail beyond latest confirmed swing)
        if getattr(self.config, "enable_swing_trailing", False) and len(recent_candles) >= 7:
            k = 3
            n = len(recent_candles)
            if is_buy:
                for idx in range(n - 2, k - 1, -1):
                    c = recent_candles[idx]
                    if all(c.low <= recent_candles[j].low for j in range(idx - k, idx)) and all(
                        c.low <= recent_candles[j].low for j in range(idx + 1, min(n, idx + k + 1))
                    ):
                        trail_sl = symbol_info.round_price(c.low - (2 * pip_size))
                        if trail_sl > position.stop_loss and trail_sl < position.current_price:
                            actions.append(
                                PositionAction(
                                    ticket=position.ticket,
                                    symbol=position.symbol,
                                    action_type="MODIFY_SL",
                                    new_sl=trail_sl,
                                    reason=f"Structure-based swing trailing stop advanced to {trail_sl} (beyond swing low {c.low:.5f})",
                                    amendment=PlanAmendment(
                                        amendment_type="TRAILING_STOP",
                                        old_sl=position.stop_loss,
                                        new_sl=trail_sl,
                                        timestamp=datetime.now(timezone.utc),
                                        reason=f"Swing trailing SL advanced to {trail_sl}",
                                    ),
                                )
                            )
                        break
            else:
                for idx in range(n - 2, k - 1, -1):
                    c = recent_candles[idx]
                    if all(c.high >= recent_candles[j].high for j in range(idx - k, idx)) and all(
                        c.high >= recent_candles[j].high for j in range(idx + 1, min(n, idx + k + 1))
                    ):
                        trail_sl = symbol_info.round_price(c.high + (2 * pip_size))
                        if trail_sl < position.stop_loss and trail_sl > position.current_price:
                            actions.append(
                                PositionAction(
                                    ticket=position.ticket,
                                    symbol=position.symbol,
                                    action_type="MODIFY_SL",
                                    new_sl=trail_sl,
                                    reason=f"Structure-based swing trailing stop advanced to {trail_sl} (beyond swing high {c.high:.5f})",
                                    amendment=PlanAmendment(
                                        amendment_type="TRAILING_STOP",
                                        old_sl=position.stop_loss,
                                        new_sl=trail_sl,
                                        timestamp=datetime.now(timezone.utc),
                                        reason=f"Swing trailing SL advanced to {trail_sl}",
                                    ),
                                )
                            )
                        break

        # Log all generated position management actions at TRADE level
        for act in actions:
            logger.info(f"[TRADE] Position Action #{act.ticket} on {act.symbol}: {act.action_type} | {act.reason}")

        return actions
