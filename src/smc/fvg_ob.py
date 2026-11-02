"""
Fair Value Gap (FVG) and Order Block (OB) Engine.
Identifies institutional imbalances (FVG, IFVG), Order Blocks,
Breaker Blocks, and computes Premium/Discount/OTE zones.
"""

from __future__ import annotations
from typing import List, Optional, Tuple, Dict
from datetime import datetime

from src.core.contracts import (
    Candle, Timeframe, OrderType, FairValueGap, OrderBlock
)


class FvgObEngine:
    def __init__(self, min_fvg_pips: float = 1.0, pip_size: float = 0.0001):
        self.min_fvg_pips = min_fvg_pips
        self.pip_size = pip_size

    def detect_fvgs(
        self,
        symbol: str,
        timeframe: Timeframe,
        candles: List[Candle]
    ) -> List[FairValueGap]:
        """
        Detects 3-candle Fair Value Gaps.
        Bullish FVG: Candle[i-2].high < Candle[i].low
        Bearish FVG: Candle[i-2].low > Candle[i].high
        Checks whether subsequent candles have mitigated the gap.
        """
        fvgs: List[FairValueGap] = []
        n = len(candles)
        if n < 3:
            return fvgs

        min_gap = self.min_fvg_pips * self.pip_size

        for i in range(2, n):
            c1 = candles[i - 2]
            c2 = candles[i - 1]  # The displacement candle
            c3 = candles[i]

            # Bullish FVG
            if c3.low > c1.high and (c3.low - c1.high) >= min_gap:
                bottom = c1.high
                top = c3.low
                mid = (top + bottom) / 2.0
                fvg_id = f"{symbol}_{timeframe}_{c2.time.isoformat()}_BULL_FVG"
                
                # Check mitigation in subsequent candles
                mitigated = False
                mitigation_time = None
                for future in candles[i + 1:]:
                    if future.low <= bottom:
                        mitigated = True
                        mitigation_time = future.time
                        break

                fvgs.append(
                    FairValueGap(
                        id=fvg_id,
                        symbol=symbol,
                        timeframe=timeframe,
                        direction=OrderType.BUY,
                        top=top,
                        bottom=bottom,
                        midpoint=mid,
                        formation_time=c2.time,
                        mitigated=mitigated,
                        mitigation_time=mitigation_time,
                        is_inverse=False
                    )
                )

            # Bearish FVG
            elif c1.low > c3.high and (c1.low - c3.high) >= min_gap:
                top = c1.low
                bottom = c3.high
                mid = (top + bottom) / 2.0
                fvg_id = f"{symbol}_{timeframe}_{c2.time.isoformat()}_BEAR_FVG"

                mitigated = False
                mitigation_time = None
                for future in candles[i + 1:]:
                    if future.high >= top:
                        mitigated = True
                        mitigation_time = future.time
                        break

                fvgs.append(
                    FairValueGap(
                        id=fvg_id,
                        symbol=symbol,
                        timeframe=timeframe,
                        direction=OrderType.SELL,
                        top=top,
                        bottom=bottom,
                        midpoint=mid,
                        formation_time=c2.time,
                        mitigated=mitigated,
                        mitigation_time=mitigation_time,
                        is_inverse=False
                    )
                )

        return fvgs

    def detect_order_blocks(
        self,
        symbol: str,
        timeframe: Timeframe,
        candles: List[Candle]
    ) -> List[OrderBlock]:
        """
        Detects Institutional Order Blocks:
        Bullish OB: The last down candle preceding strong bullish displacement.
        Bearish OB: The last up candle preceding strong bearish displacement.
        """
        order_blocks: List[OrderBlock] = []
        n = len(candles)
        if n < 4:
            return order_blocks

        for i in range(1, n - 2):
            c_ob = candles[i]
            c_disp = candles[i + 1]
            c_disp2 = candles[i + 2]

            # Bullish Order Block candidate:
            # c_ob is bearish, followed by c_disp bullish candle with high expansion
            if c_ob.is_bearish and c_disp.is_bullish:
                disp_range = (c_disp2.close - c_ob.high)
                if disp_range > 0 and c_disp.close > c_ob.high:
                    # Check mitigation
                    mitigated = False
                    for future in candles[i + 2:]:
                        if future.low <= c_ob.low:
                            mitigated = True
                            break

                    order_blocks.append(
                        OrderBlock(
                            id=f"{symbol}_{timeframe}_{c_ob.time.isoformat()}_BULL_OB",
                            symbol=symbol,
                            timeframe=timeframe,
                            direction=OrderType.BUY,
                            high=c_ob.high,
                            low=c_ob.low,
                            open_price=c_ob.open,
                            close_price=c_ob.close,
                            formation_time=c_ob.time,
                            mitigated=mitigated,
                            is_breaker=False,
                            strength_score=1.0
                        )
                    )

            # Bearish Order Block candidate:
            # c_ob is bullish, followed by c_disp bearish candle
            elif c_ob.is_bullish and c_disp.is_bearish:
                disp_range = (c_ob.low - c_disp2.close)
                if disp_range > 0 and c_disp.close < c_ob.low:
                    mitigated = False
                    for future in candles[i + 2:]:
                        if future.high >= c_ob.high:
                            mitigated = True
                            break

                    order_blocks.append(
                        OrderBlock(
                            id=f"{symbol}_{timeframe}_{c_ob.time.isoformat()}_BEAR_OB",
                            symbol=symbol,
                            timeframe=timeframe,
                            direction=OrderType.SELL,
                            high=c_ob.high,
                            low=c_ob.low,
                            open_price=c_ob.open,
                            close_price=c_ob.close,
                            formation_time=c_ob.time,
                            mitigated=mitigated,
                            is_breaker=False,
                            strength_score=1.0
                        )
                    )

        return order_blocks

    @staticmethod
    def calculate_premium_discount(
        range_high: float,
        range_low: float,
        current_price: float
    ) -> Dict[str, Any]:
        """
        Calculates ICT Premium/Discount zones and Optimal Trade Entry (OTE) levels.
        Fib levels: 0.50 (Equilibrium), 0.62 (OTE min), 0.705 (OTE sweet spot), 0.79 (OTE max).
        """
        total_range = range_high - range_low
        if total_range <= 0:
            return {
                "equilibrium": current_price,
                "is_discount": False,
                "is_premium": False,
                "ote_62": current_price,
                "ote_705": current_price,
                "ote_79": current_price
            }

        eq = range_low + 0.5 * total_range
        is_discount = current_price < eq
        is_premium = current_price > eq

        # For bullish expansion (from range_low to range_high):
        # Retracement into discount:
        fib_62 = range_high - 0.62 * total_range
        fib_705 = range_high - 0.705 * total_range
        fib_79 = range_high - 0.79 * total_range

        return {
            "equilibrium": round(eq, 5),
            "is_discount": is_discount,
            "is_premium": is_premium,
            "ote_62": round(fib_62, 5),
            "ote_705": round(fib_705, 5),
            "ote_79": round(fib_79, 5)
        }
