"""
Limit Order Entry Engine with Expiration.
Converts aggressive market entries into passive limit orders placed at structural levels,
saving crossing-the-spread friction while expiring stale unfilled setups.
"""

from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

from src.core.contracts import OrderType


@dataclass(frozen=True)
class PendingLimitSpec:
    symbol: str
    limit_type: OrderType  # BUY_LIMIT or SELL_LIMIT
    limit_price: float
    stop_loss: float
    take_profit: float
    volume: float
    expiration_utc: datetime
    spread_savings_pts: float
    reason: str


class LimitOrderEngine:
    def __init__(self, expiration_minutes: int = 45):
        self.expiration_minutes = expiration_minutes

    def convert_to_limit_order(
        self,
        symbol: str,
        direction: OrderType,
        market_price: float,
        structural_entry_price: float,
        stop_loss: float,
        take_profit: float,
        volume: float,
        current_spread_pts: float,
        point: float = 0.00001
    ) -> Optional[PendingLimitSpec]:
        """
        Creates a pending limit order specification with expiration.
        """
        now = datetime.now(timezone.utc)
        expiry = now + timedelta(minutes=self.expiration_minutes)

        if direction in (OrderType.BUY, OrderType.BUY_LIMIT):
            # For BUY limit, limit_price must be <= market_price
            limit_price = min(market_price, structural_entry_price)
            limit_type = OrderType.BUY_LIMIT
            savings = max(0.0, (market_price - limit_price) / point) if point > 0 else 0.0
        elif direction in (OrderType.SELL, OrderType.SELL_LIMIT):
            # For SELL limit, limit_price must be >= market_price
            limit_price = max(market_price, structural_entry_price)
            limit_type = OrderType.SELL_LIMIT
            savings = max(0.0, (limit_price - market_price) / point) if point > 0 else 0.0
        else:
            return None

        return PendingLimitSpec(
            symbol=symbol,
            limit_type=limit_type,
            limit_price=round(limit_price, 5),
            stop_loss=round(stop_loss, 5),
            take_profit=round(take_profit, 5),
            volume=volume,
            expiration_utc=expiry,
            spread_savings_pts=round(savings + current_spread_pts, 1),
            reason=f"Passive entry at {limit_price:.5f} saves ~{current_spread_pts:.1f} pts spread. Expires in {self.expiration_minutes}m."
        )
