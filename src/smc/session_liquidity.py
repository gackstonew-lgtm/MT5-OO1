"""
Institutional Session Liquidity and Asia Range Engine.
Computes daily Asian Session High, Low, and Midpoint (00:00 - 09:00 UTC).
Registers institutional liquidity pools and detects London Asian-Range Sweeps (Judas Swings).
"""

from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime, time, timezone, timedelta
from typing import List, Optional, Tuple, Dict, Any

from src.core.contracts import Candle, Timeframe, OrderType, LiquidityZone


@dataclass(frozen=True)
class AsiaRangeLevels:
    symbol: str
    date: str  # YYYY-MM-DD
    asia_high: float
    asia_low: float
    asia_mid: float
    range_pips: float
    bars_count: int


@dataclass(frozen=True)
class AsiaSweepEvent:
    symbol: str
    sweep_type: str  # 'SWEEP_HIGH' or 'SWEEP_LOW'
    sweep_price: float
    asia_level: float
    sweep_distance_pips: float
    direction_bias: OrderType  # BUY for low sweep, SELL for high sweep
    candle_time: datetime
    confirmed: bool
    description: str


class SessionLiquidityEngine:
    """
    Manages session-level liquidity levels and sweep detection.
    """
    def __init__(self, pip_multiplier: float = 10000.0):
        self.pip_multiplier = pip_multiplier

    def calculate_asia_range(
        self,
        symbol: str,
        m15_candles: List[Candle],
        target_date: Optional[datetime] = None
    ) -> Optional[AsiaRangeLevels]:
        """
        Calculates the Asian Session High, Low, and Midpoint for target date (00:00 to 09:00 UTC).
        """
        if not m15_candles:
            return None

        dt_ref = target_date or m15_candles[-1].time
        if dt_ref.tzinfo is None:
            dt_ref = dt_ref.replace(tzinfo=timezone.utc)
        target_day_str = dt_ref.strftime("%Y-%m-%d")

        # Extract bars belonging to Asian session on this date
        asia_candles = []
        for c in m15_candles:
            c_time = c.time if c.time.tzinfo else c.time.replace(tzinfo=timezone.utc)
            if c_time.strftime("%Y-%m-%d") == target_day_str:
                if time(0, 0) <= c_time.time() < time(9, 0):
                    asia_candles.append(c)

        if not asia_candles:
            return None

        h = max(c.high for c in asia_candles)
        l = min(c.low for c in asia_candles)
        mid = round((h + l) / 2.0, 5)
        range_pips = round((h - l) * self.pip_multiplier, 1)

        return AsiaRangeLevels(
            symbol=symbol,
            date=target_day_str,
            asia_high=h,
            asia_low=l,
            asia_mid=mid,
            range_pips=range_pips,
            bars_count=len(asia_candles)
        )

    def register_asia_liquidity_zones(
        self,
        asia_range: AsiaRangeLevels,
        pip_size: float = 0.0001
    ) -> List[LiquidityZone]:
        """
        Creates BSL (Buy-Side Liquidity) at Asia High and SSL (Sell-Side Liquidity) at Asia Low.
        """
        zones: List[LiquidityZone] = []
        tolerance = 2.0 * pip_size

        # Asia High BSL
        zones.append(
            LiquidityZone(
                id=f"{asia_range.symbol}_ASIA_BSL_{asia_range.date}",
                symbol=asia_range.symbol,
                timeframe=Timeframe.M15,
                level_type="BSL",
                price=asia_range.asia_high,
                high=asia_range.asia_high + tolerance,
                low=asia_range.asia_high,
                formation_time=datetime.strptime(asia_range.date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
            )
        )

        # Asia Low SSL
        zones.append(
            LiquidityZone(
                id=f"{asia_range.symbol}_ASIA_SSL_{asia_range.date}",
                symbol=asia_range.symbol,
                timeframe=Timeframe.M15,
                level_type="SSL",
                price=asia_range.asia_low,
                high=asia_range.asia_low,
                low=asia_range.asia_low - tolerance,
                formation_time=datetime.strptime(asia_range.date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
            )
        )

        return zones

    def detect_london_asia_sweep(
        self,
        symbol: str,
        m15_candles: List[Candle],
        asia_range: AsiaRangeLevels,
        pip_size: float = 0.0001
    ) -> Optional[AsiaSweepEvent]:
        """
        Evaluates whether current London candle swept Asia High or Low and rejected.
        """
        if not m15_candles or not asia_range:
            return None

        curr = m15_candles[-1]
        c_time = curr.time if curr.time.tzinfo else curr.time.replace(tzinfo=timezone.utc)

        # Must be in London session (06:00 to 12:00 UTC under reference profile)
        if not (time(6, 0) <= c_time.time() < time(12, 0)):
            return None

        # 1. Bearish Judas Swing: Sweeps Asia High then closes back below it
        if curr.high > asia_range.asia_high and curr.close < asia_range.asia_high:
            dist_pips = round((curr.high - asia_range.asia_high) / pip_size, 1)
            return AsiaSweepEvent(
                symbol=symbol,
                sweep_type="SWEEP_HIGH",
                sweep_price=curr.high,
                asia_level=asia_range.asia_high,
                sweep_distance_pips=dist_pips,
                direction_bias=OrderType.SELL,
                candle_time=c_time,
                confirmed=True,
                description=f"London Judas Swing: Swept Asia High {asia_range.asia_high:.5f} by {dist_pips} pips and rejected"
            )

        # 2. Bullish Judas Swing: Sweeps Asia Low then closes back above it
        if curr.low < asia_range.asia_low and curr.close > asia_range.asia_low:
            dist_pips = round((asia_range.asia_low - curr.low) / pip_size, 1)
            return AsiaSweepEvent(
                symbol=symbol,
                sweep_type="SWEEP_LOW",
                sweep_price=curr.low,
                asia_level=asia_range.asia_low,
                sweep_distance_pips=dist_pips,
                direction_bias=OrderType.BUY,
                candle_time=c_time,
                confirmed=True,
                description=f"London Judas Swing: Swept Asia Low {asia_range.asia_low:.5f} by {dist_pips} pips and rejected"
            )

        return None
