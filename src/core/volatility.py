"""
Volatility and Market Regime Engine.
Calculates ATR, ATR Percentile, Body-to-Range ratios, and classifies
market conditions into LOW, NORMAL, HIGH, or EXTREME regimes.
"""

from __future__ import annotations
from typing import List, Tuple
import numpy as np

from src.core.contracts import Candle, VolatilityRegime


class VolatilityEngine:
    def __init__(self, atr_period: int = 14, lookback_percentile: int = 50):
        self.atr_period = atr_period
        self.lookback_percentile = lookback_percentile

    def calculate_atr(self, candles: List[Candle], period: Optional[int] = None) -> float:
        """
        Calculates Average True Range (ATR) over specified period.
        """
        p = period or self.atr_period
        n = len(candles)
        if n < p + 1:
            return 0.0

        tr_values: List[float] = []
        for i in range(1, n):
            c_curr = candles[i]
            c_prev = candles[i - 1]
            tr = max(
                c_curr.high - c_curr.low,
                abs(c_curr.high - c_prev.close),
                abs(c_curr.low - c_prev.close)
            )
            tr_values.append(tr)

        if len(tr_values) < p:
            return 0.0

        # Simple moving average of true range
        return float(np.mean(tr_values[-p:]))

    def classify_regime(self, candles: List[Candle]) -> Tuple[VolatilityRegime, float]:
        """
        Classifies volatility into:
        - LOW (< 25th percentile)
        - NORMAL (25th to 75th percentile)
        - HIGH (75th to 95th percentile)
        - EXTREME (> 95th percentile)
        """
        current_atr = self.calculate_atr(candles)
        n = len(candles)
        if n < self.lookback_percentile:
            return VolatilityRegime.NORMAL, current_atr

        # Rolling ATR distribution
        historical_atrs: List[float] = []
        for end_idx in range(n - self.lookback_percentile, n):
            sub = candles[:end_idx]
            sub_atr = self.calculate_atr(sub)
            if sub_atr > 0:
                historical_atrs.append(sub_atr)

        if not historical_atrs:
            return VolatilityRegime.NORMAL, current_atr

        p25 = np.percentile(historical_atrs, 25)
        p75 = np.percentile(historical_atrs, 75)
        p95 = np.percentile(historical_atrs, 95)

        if current_atr < p25:
            regime = VolatilityRegime.LOW
        elif current_atr <= p75:
            regime = VolatilityRegime.NORMAL
        elif current_atr <= p95:
            regime = VolatilityRegime.HIGH
        else:
            regime = VolatilityRegime.EXTREME

        return regime, current_atr
