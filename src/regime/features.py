"""
Regime Feature Extraction Engine.
Calculates realized volatility, ATR percentiles, ADX, multi-timeframe trend slopes,
session classifications, and spread ratios from closed bars.
"""

from __future__ import annotations
import math
from dataclasses import dataclass, field
from typing import List, Dict, Optional, Any
import numpy as np

from src.core.contracts import Candle, Timeframe


@dataclass(frozen=True)
class RegimeFeatures:
    realized_volatility: float
    atr_percentile: float
    adx: float
    plus_di: float
    minus_di: float
    trend_slope: float
    session_code: int
    spread_ratio: float
    timestamp_iso: str

    def to_array(self) -> np.ndarray:
        return np.array([
            self.realized_volatility,
            self.atr_percentile,
            self.adx / 100.0,
            (self.plus_di - self.minus_di) / 100.0,
            self.trend_slope,
            self.session_code / 4.0,
            self.spread_ratio
        ], dtype=np.float64)


class RegimeFeatureExtractor:
    def __init__(self, atr_period: int = 14, lookback_window: int = 100):
        self.atr_period = atr_period
        self.lookback_window = lookback_window

    def extract_features(
        self,
        candles_m15: List[Candle],
        candles_h1: Optional[List[Candle]] = None
    ) -> Optional[RegimeFeatures]:
        """Extracts regime feature vector from closed bars."""
        if len(candles_m15) < max(self.lookback_window, 30):
            return None

        closes = np.array([c.close for c in candles_m15], dtype=np.float64)
        highs = np.array([c.high for c in candles_m15], dtype=np.float64)
        lows = np.array([c.low for c in candles_m15], dtype=np.float64)
        spreads = np.array([max(1, c.spread) for c in candles_m15], dtype=np.float64)

        # 1. Realized Volatility (std of log returns over 20 bars)
        log_rets = np.diff(np.log(closes[-21:]))
        realized_vol = float(np.std(log_rets, ddof=1) * math.sqrt(4 * 24)) if len(log_rets) > 1 else 0.0

        # 2. True Range & ATR Percentile over lookback_window
        tr = np.maximum(
            highs[1:] - lows[1:],
            np.maximum(
                np.abs(highs[1:] - closes[:-1]),
                np.abs(lows[1:] - closes[:-1])
            )
        )
        if len(tr) < self.atr_period:
            return None

        rolling_atr = []
        for i in range(len(tr) - self.atr_period + 1):
            rolling_atr.append(np.mean(tr[i:i + self.atr_period]))

        current_atr = rolling_atr[-1]
        atr_window = rolling_atr[-self.lookback_window:]
        min_atr = min(atr_window)
        max_atr = max(atr_window)
        atr_pct = float((current_atr - min_atr) / (max_atr - min_atr + 1e-9)) if max_atr > min_atr else 0.50

        # 3. Wilder's ADX (14 periods)
        adx, plus_di, minus_di = self._compute_adx(highs[-30:], lows[-30:], closes[-30:])

        # 4. Multi-Timeframe Trend Slope (Linear regression slope on H1 closes or past 20 M15 closes)
        ref_closes = np.array([c.close for c in candles_h1[-20:]]) if (candles_h1 and len(candles_h1) >= 20) else closes[-20:]
        x = np.arange(len(ref_closes))
        slope, _ = np.polyfit(x, ref_closes, 1)
        normalized_slope = float(slope / (np.mean(ref_closes) + 1e-9) * 1000.0)

        # 5. Session classification
        last_candle = candles_m15[-1]
        hour = last_candle.time.hour
        # 0: Asian (00-07), 1: London (08-12), 2: Overlap (13-16), 3: NY (17-21), 4: Off (22-23)
        if 0 <= hour < 8:
            session_code = 0
        elif 8 <= hour < 13:
            session_code = 1
        elif 13 <= hour < 17:
            session_code = 2
        elif 17 <= hour < 22:
            session_code = 3
        else:
            session_code = 4

        # 6. Spread Ratio (current spread / 20-bar median spread)
        cur_spread = spreads[-1]
        median_spread = float(np.median(spreads[-20:]))
        spread_ratio = float(cur_spread / (median_spread + 1e-5))

        return RegimeFeatures(
            realized_volatility=realized_vol,
            atr_percentile=atr_pct,
            adx=adx,
            plus_di=plus_di,
            minus_di=minus_di,
            trend_slope=normalized_slope,
            session_code=session_code,
            spread_ratio=spread_ratio,
            timestamp_iso=last_candle.time.isoformat()
        )

    def _compute_adx(self, highs: np.ndarray, lows: np.ndarray, closes: np.ndarray) -> Tuple[float, float, float]:
        """Calculates ADX, +DI, and -DI over 14 periods."""
        n = len(closes)
        if n < 15:
            return 20.0, 20.0, 20.0

        tr = []
        plus_dm = []
        minus_dm = []

        for i in range(1, n):
            h_diff = highs[i] - highs[i - 1]
            l_diff = lows[i - 1] - lows[i]

            p_dm = h_diff if (h_diff > l_diff and h_diff > 0) else 0.0
            m_dm = l_diff if (l_diff > h_diff and l_diff > 0) else 0.0

            true_range = max(highs[i] - lows[i], max(abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1])))

            tr.append(true_range)
            plus_dm.append(p_dm)
            minus_dm.append(m_dm)

        p = min(14, len(tr))
        smooth_tr = np.mean(tr[-p:])
        smooth_pdm = np.mean(plus_dm[-p:])
        smooth_mdm = np.mean(minus_dm[-p:])

        plus_di = (smooth_pdm / (smooth_tr + 1e-9)) * 100.0
        minus_di = (smooth_mdm / (smooth_tr + 1e-9)) * 100.0

        dx_denom = plus_di + minus_di
        dx = (abs(plus_di - minus_di) / (dx_denom + 1e-9)) * 100.0
        adx = min(100.0, max(0.0, dx))

        return float(adx), float(plus_di), float(minus_di)
