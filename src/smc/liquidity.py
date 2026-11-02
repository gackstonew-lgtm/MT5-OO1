"""
Liquidity Engine.
Identifies Buy-Side Liquidity (BSL), Sell-Side Liquidity (SSL),
Equal Highs (EQH), Equal Lows (EQL), Internal and External Liquidity pools,
and verifies liquidity sweeps with rejection confirmation.
"""

from __future__ import annotations
from typing import List, Optional, Tuple
from datetime import datetime

from src.core.contracts import (
    Candle, Timeframe, OrderType, LiquidityZone
)


class LiquidityEngine:
    def __init__(self, eqh_tolerance_pips: float = 2.0, pip_size: float = 0.0001):
        self.eqh_tolerance_pips = eqh_tolerance_pips
        self.pip_size = pip_size

    def identify_liquidity_pools(
        self,
        symbol: str,
        timeframe: Timeframe,
        candles: List[Candle],
        swing_lookback: int = 5
    ) -> List[LiquidityZone]:
        """
        Identifies major BSL and SSL pools, including Equal Highs and Equal Lows.
        """
        pools: List[LiquidityZone] = []
        n = len(candles)
        if n < 2 * swing_lookback + 1:
            return pools

        tolerance = self.eqh_tolerance_pips * self.pip_size
        k = swing_lookback

        swing_highs: List[Tuple[int, Candle]] = []
        swing_lows: List[Tuple[int, Candle]] = []

        for i in range(k, n - k):
            c = candles[i]
            if all(c.high > candles[j].high for j in range(i - k, i)) and \
               all(c.high >= candles[j].high for j in range(i + 1, i + k + 1)):
                swing_highs.append((i, c))

            if all(c.low < candles[j].low for j in range(i - k, i)) and \
               all(c.low <= candles[j].low for j in range(i + 1, i + k + 1)):
                swing_lows.append((i, c))

        # Check for Equal Highs (EQH - high liquidity magnet)
        for i in range(len(swing_highs) - 1):
            sh1 = swing_highs[i][1]
            sh2 = swing_highs[i + 1][1]
            if abs(sh1.high - sh2.high) <= tolerance:
                pools.append(
                    LiquidityZone(
                        id=f"{symbol}_{timeframe}_EQH_{sh2.time.isoformat()}",
                        symbol=symbol,
                        timeframe=timeframe,
                        level_type="EQH",
                        price=max(sh1.high, sh2.high),
                        high=max(sh1.high, sh2.high) + tolerance,
                        low=min(sh1.high, sh2.high),
                        formation_time=sh2.time
                    )
                )

        # Check for Equal Lows (EQL - high liquidity magnet)
        for i in range(len(swing_lows) - 1):
            sl1 = swing_lows[i][1]
            sl2 = swing_lows[i + 1][1]
            if abs(sl1.low - sl2.low) <= tolerance:
                pools.append(
                    LiquidityZone(
                        id=f"{symbol}_{timeframe}_EQL_{sl2.time.isoformat()}",
                        symbol=symbol,
                        timeframe=timeframe,
                        level_type="EQL",
                        price=min(sl1.low, sl2.low),
                        high=max(sl1.low, sl2.low),
                        low=min(sl1.low, sl2.low) - tolerance,
                        formation_time=sl2.time
                    )
                )

        # Add single swing highs as BSL
        for idx, c in swing_highs:
            pools.append(
                LiquidityZone(
                    id=f"{symbol}_{timeframe}_BSL_{c.time.isoformat()}",
                    symbol=symbol,
                    timeframe=timeframe,
                    level_type="BSL",
                    price=c.high,
                    high=c.high + tolerance,
                    low=c.high,
                    formation_time=c.time
                )
            )

        # Add single swing lows as SSL
        for idx, c in swing_lows:
            pools.append(
                LiquidityZone(
                    id=f"{symbol}_{timeframe}_SSL_{c.time.isoformat()}",
                    symbol=symbol,
                    timeframe=timeframe,
                    level_type="SSL",
                    price=c.low,
                    high=c.low,
                    low=c.low - tolerance,
                    formation_time=c.time
                )
            )

        return pools

    def verify_sweep(
        self,
        pool: LiquidityZone,
        recent_candles: List[Candle]
    ) -> Tuple[bool, bool, float]:
        """
        Determines whether a liquidity pool was swept.
        Returns: (is_swept, is_rejection_confirmed, sweep_distance_pips)
        """
        swept = False
        rejection_confirmed = False
        max_distance = 0.0

        for c in recent_candles:
            if pool.level_type in ["BSL", "EQH"]:
                if c.high > pool.price:
                    swept = True
                    dist = (c.high - pool.price) / self.pip_size
                    max_distance = max(max_distance, dist)
                    # Rejection: candle closed back below the level
                    if c.close < pool.price:
                        rejection_confirmed = True
            elif pool.level_type in ["SSL", "EQL"]:
                if c.low < pool.price:
                    swept = True
                    dist = (pool.price - c.low) / self.pip_size
                    max_distance = max(max_distance, dist)
                    # Rejection: candle closed back above the level
                    if c.close > pool.price:
                        rejection_confirmed = True

        return swept, rejection_confirmed, max_distance
