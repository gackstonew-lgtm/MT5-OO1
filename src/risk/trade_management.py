"""
Dynamic Trade and Position Management Engine.
Manages open positions during their lifecycle:
- Break-even protection at +1.0R
- Partial profit-taking at +1.5R
- Structural and ATR trailing stops
- Invalidation / Session-end exits
"""

from __future__ import annotations
from dataclasses import dataclass
from typing import List, Optional
from datetime import datetime

from src.core.contracts import Position, OrderType, Candle, SymbolInfo
from src.core.config import RiskConfig


@dataclass
class PositionAction:
    ticket: int
    symbol: str
    action_type: str  # "MODIFY_SL", "PARTIAL_CLOSE", "FULL_CLOSE"
    new_sl: Optional[float] = None
    close_volume: Optional[float] = None
    reason: str = ""


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
        Break-even, partial take-profit, and trailing stop adjustments.
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

        # 1. Break-Even Protection (at +1.0R by default)
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
                                reason=f"Break-even activated at +{current_r:.2f}R (SL -> {new_sl})"
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
                                reason=f"Break-even activated at +{current_r:.2f}R (SL -> {new_sl})"
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
                            reason=f"Partial TP triggered at +{current_r:.2f}R (close {partial_vol} lots)"
                        )
                    )

        # 3. Structural Trailing Stop
        # If position is already beyond 1.5R, trail SL behind recent candle swing lows (for buy) or highs (for sell)
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
                            reason=f"Structural trailing stop advanced to {new_trail_sl}"
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
                            reason=f"Structural trailing stop advanced to {new_trail_sl}"
                        )
                    )

        return actions
