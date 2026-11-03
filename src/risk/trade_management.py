"""
Dynamic Trade and Position Management Engine.
Manages open positions during their lifecycle:
- Break-even protection at +1.0R
- Partial profit-taking at +1.5R (and optional 1.0R)
- Structural and ATR trailing stops
- Time-stop and Session-end flatten exits
- Structure-based swing trailing stop
"""

from __future__ import annotations
from dataclasses import dataclass
from typing import List, Optional
from datetime import datetime, timezone
import logging

from src.core.contracts import Position, OrderType, Candle, SymbolInfo, PlanAmendment
from src.core.config import RiskConfig

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

    def evaluate_position(
        self,
        position: Position,
        symbol_info: SymbolInfo,
        recent_candles: List[Candle]
    ) -> List[PositionAction]:
        """
        Evaluates an active position against lifecycle management rules:
        Break-even, partial take-profit, time-stop, session-end flatten,
        and trailing stop adjustments.
        """
        actions: List[PositionAction] = []
        is_buy = position.order_type == OrderType.BUY
        point = symbol_info.point
        pip_size = point * 10

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

        # 0A. Time Stop (close if position open >= time_stop_max_bars without reaching SL/TP)
        if getattr(self.config, "enable_time_stop", False) and position.open_time and recent_candles:
            bars_open = len([c for c in recent_candles if c.time >= position.open_time])
            if bars_open >= getattr(self.config, "time_stop_max_bars", 32):
                act = PositionAction(
                    ticket=position.ticket,
                    symbol=position.symbol,
                    action_type="FULL_CLOSE",
                    reason=f"Time stop triggered: {bars_open} bars elapsed (max {self.config.time_stop_max_bars})"
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
                    reason=f"End-of-session flatten triggered at {last_time.strftime('%H:%M UTC')}"
                )
                logger.info(f"[TRADE] Position Action #{act.ticket} on {act.symbol}: {act.action_type} | {act.reason}")
                actions.append(act)
                return actions

        # 1A. Break-Even Protection (at +1.0R by default)
        if self.config.enable_break_even and not position.break_even_activated:
            if current_r >= self.config.break_even_at_r:
                buffer_dist = self.config.break_even_buffer_pips * pip_size
                if is_buy:
                    new_sl = symbol_info.round_price(position.open_price + buffer_dist)
                    if new_sl > position.stop_loss:
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
                                    reason=f"Break-even activated at +{current_r:.2f}R (SL -> {new_sl})"
                                )
                            )
                        )
                else:
                    new_sl = symbol_info.round_price(position.open_price - buffer_dist)
                    if new_sl < position.stop_loss:
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
                                    reason=f"Break-even activated at +{current_r:.2f}R (SL -> {new_sl})"
                                )
                            )
                        )

        # 1B. Partial Take-Profit at 1.0R with Break-Even
        if getattr(self.config, "enable_partial_at_1r", False) and not position.partial_taken and current_r >= 1.0:
            raw_partial_vol = position.volume * 0.5
            partial_vol = symbol_info.normalize_volume(raw_partial_vol)
            if partial_vol >= symbol_info.min_lot and partial_vol < position.volume:
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
                            reason=f"Partial TP at 1.0R (+{current_r:.2f}R)"
                        )
                    )
                )

        # 2. Partial Profit-Taking (at +1.5R by default)
        if self.config.enable_partial_tp and not position.partial_taken:
            if current_r >= self.config.partial_tp_at_r:
                raw_partial_vol = position.volume * self.config.partial_tp_ratio
                partial_vol = symbol_info.normalize_volume(raw_partial_vol)
                if partial_vol >= symbol_info.min_lot and partial_vol < position.volume:
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
                                reason=f"Partial TP triggered at +{current_r:.2f}R"
                            )
                        )
                    )

        # 3. Structural Trailing Stop
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
                                reason=f"Structural trailing stop advanced to {new_trail_sl}"
                            )
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
                                reason=f"Structural trailing stop advanced to {new_trail_sl}"
                            )
                        )
                    )

        # 4. Structure-Based Trailing Stop (trail beyond latest confirmed swing)
        if getattr(self.config, "enable_swing_trailing", False) and len(recent_candles) >= 7:
            k = 3
            n = len(recent_candles)
            if is_buy:
                for idx in range(n - 2, k - 1, -1):
                    c = recent_candles[idx]
                    if all(c.low <= recent_candles[j].low for j in range(idx - k, idx)) and \
                       all(c.low <= recent_candles[j].low for j in range(idx + 1, min(n, idx + k + 1))):
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
                                        reason=f"Swing trailing SL advanced to {trail_sl}"
                                    )
                                )
                            )
                        break
            else:
                for idx in range(n - 2, k - 1, -1):
                    c = recent_candles[idx]
                    if all(c.high >= recent_candles[j].high for j in range(idx - k, idx)) and \
                       all(c.high >= recent_candles[j].high for j in range(idx + 1, min(n, idx + k + 1))):
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
                                        reason=f"Swing trailing SL advanced to {trail_sl}"
                                    )
                                )
                            )
                        break

        # Log all generated position management actions at TRADE level
        for act in actions:
            logger.info(f"[TRADE] Position Action #{act.ticket} on {act.symbol}: {act.action_type} | {act.reason}")

        return actions
