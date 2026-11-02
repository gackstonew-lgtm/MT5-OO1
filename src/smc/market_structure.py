"""
Market Structure Engine.
Detects Swing Highs, Swing Lows, Break of Structure (BOS),
Change of Character (CHoCH), and Market Structure Shift (MSS).
Validates displacement and candle-close confirmation.
"""

from __future__ import annotations
from typing import List, Optional, Tuple
from datetime import datetime

from src.core.contracts import (
    Candle, Timeframe, OrderType, MarketStructureType, MarketStructureEvent, TrendBias
)


class MarketStructureEngine:
    def __init__(self, swing_lookback: int = 5, displacement_ratio_min: float = 0.60):
        self.swing_lookback = swing_lookback
        self.displacement_ratio_min = displacement_ratio_min

    def find_swing_points(self, candles: List[Candle]) -> Tuple[List[Tuple[int, Candle]], List[Tuple[int, Candle]]]:
        """
        Identifies confirmed swing highs and swing lows using a symmetric lookback window.
        Returns: (swing_highs, swing_lows) where each item is (index, Candle).
        """
        swing_highs: List[Tuple[int, Candle]] = []
        swing_lows: List[Tuple[int, Candle]] = []
        n = len(candles)
        k = self.swing_lookback

        if n < 2 * k + 1:
            return swing_highs, swing_lows

        for i in range(k, n - k):
            current = candles[i]
            # Swing High: current high is strictly greater than k bars before and after
            is_high = all(current.high > candles[j].high for j in range(i - k, i)) and \
                      all(current.high >= candles[j].high for j in range(i + 1, i + k + 1))
            if is_high:
                swing_highs.append((i, current))

            # Swing Low: current low is strictly lower than k bars before and after
            is_low = all(current.low < candles[j].low for j in range(i - k, i)) and \
                     all(current.low <= candles[j].low for j in range(i + 1, i + k + 1))
            if is_low:
                swing_lows.append((i, current))

        return swing_highs, swing_lows

    def is_displacement_candle(self, candle: Candle, min_pips: float = 0.0) -> bool:
        """
        Evaluates whether a candle exhibits strong institutional displacement.
        Displacement requires:
        1. Body-to-range ratio >= displacement_ratio_min (minimal wicks, strong push)
        2. Non-zero expansion
        """
        if candle.range <= 0:
            return False
        ratio = candle.body / candle.range
        return ratio >= self.displacement_ratio_min

    def analyze_structure(
        self,
        symbol: str,
        timeframe: Timeframe,
        candles: List[Candle]
    ) -> Tuple[TrendBias, List[MarketStructureEvent]]:
        """
        Evaluates trend bias and detects BOS, CHoCH, and MSS events.
        Enforces candle close confirmation (no intrabar lookahead).
        """
        events: List[MarketStructureEvent] = []
        swing_highs, swing_lows = self.find_swing_points(candles)

        if len(swing_highs) < 2 or len(swing_lows) < 2:
            # Fallback trend evaluation based on net displacement across candle window
            if len(candles) >= 5:
                start_c = candles[0].close
                mid_c = candles[len(candles) // 2].close
                last_c = candles[-1].close
                if last_c > mid_c > start_c:
                    return TrendBias.WEAK_BULLISH, events
                elif last_c < mid_c < start_c:
                    return TrendBias.WEAK_BEARISH, events
            return TrendBias.RANGING, events

        # Determine recent swing trend
        last_high = swing_highs[-1][1].high
        prev_high = swing_highs[-2][1].high
        last_low = swing_lows[-1][1].low
        prev_low = swing_lows[-2][1].low

        is_higher_highs = last_high > prev_high
        is_higher_lows = last_low > prev_low
        is_lower_highs = last_high < prev_high
        is_lower_lows = last_low < prev_low

        if is_higher_highs and is_higher_lows:
            bias = TrendBias.STRONG_BULLISH
        elif is_higher_highs and not is_lower_lows:
            bias = TrendBias.WEAK_BULLISH
        elif is_lower_highs and is_lower_lows:
            bias = TrendBias.STRONG_BEARISH
        elif is_lower_lows and not is_higher_highs:
            bias = TrendBias.WEAK_BEARISH
        else:
            bias = TrendBias.RANGING

        # Check for confirmed breaks beyond the last swings by subsequent completed candles
        latest_candles = candles[max(swing_highs[-1][0], swing_lows[-1][0]) + 1:]

        for c in latest_candles:
            # Bullish Break above last swing high
            if c.close > last_high:
                is_disp = self.is_displacement_candle(c)
                event_type = MarketStructureType.MSS if is_disp else MarketStructureType.BOS
                # If previous trend was bearish and price breaks swing high, it's a CHoCH/MSS
                if bias in [TrendBias.STRONG_BEARISH, TrendBias.WEAK_BEARISH]:
                    event_type = MarketStructureType.CHOCH
                
                events.append(
                    MarketStructureEvent(
                        id=f"{symbol}_{timeframe}_{c.time.isoformat()}_{event_type.value}",
                        symbol=symbol,
                        timeframe=timeframe,
                        event_type=event_type,
                        direction=OrderType.BUY,
                        price=c.close,
                        timestamp=c.time,
                        swing_high=last_high,
                        swing_low=last_low,
                        confirmed_by_close=True,
                        displacement_ratio=c.body / c.range if c.range > 0 else 0.0
                    )
                )
                break

            # Bearish Break below last swing low
            if c.close < last_low:
                is_disp = self.is_displacement_candle(c)
                event_type = MarketStructureType.MSS if is_disp else MarketStructureType.BOS
                if bias in [TrendBias.STRONG_BULLISH, TrendBias.WEAK_BULLISH]:
                    event_type = MarketStructureType.CHOCH

                events.append(
                    MarketStructureEvent(
                        id=f"{symbol}_{timeframe}_{c.time.isoformat()}_{event_type.value}",
                        symbol=symbol,
                        timeframe=timeframe,
                        event_type=event_type,
                        direction=OrderType.SELL,
                        price=c.close,
                        timestamp=c.time,
                        swing_high=last_high,
                        swing_low=last_low,
                        confirmed_by_close=True,
                        displacement_ratio=c.body / c.range if c.range > 0 else 0.0
                    )
                )
                break

        return bias, events
