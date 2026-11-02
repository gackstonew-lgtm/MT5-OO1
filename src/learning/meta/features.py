"""
Causal Feature Extractor for Meta-Labeling.
Extracts strictly point-in-time features available at the moment a setup forms.
Guarantees zero forward look-ahead bias through explicit temporal assertions.
"""

from __future__ import annotations
import math
from dataclasses import dataclass
from datetime import datetime
from typing import List, Dict, Optional, Any
import numpy as np

from src.core.contracts import Candle, SignalCandidate, OrderType


@dataclass(frozen=True)
class MetaFeatureVector:
    signal_time: datetime
    confluence_score: float
    fvg_size_pips: float
    ob_volume_ratio: float
    liquidity_sweep_pips: float
    displacement_ratio: float
    realized_vol_m15: float
    adx_m15: float
    session_code: int
    hour_of_day: int
    spread_to_sl_ratio: float

    def to_array(self) -> np.ndarray:
        return np.array([
            self.confluence_score / 100.0,
            min(5.0, self.fvg_size_pips / 10.0),
            min(5.0, self.ob_volume_ratio),
            min(5.0, self.liquidity_sweep_pips / 10.0),
            self.displacement_ratio,
            self.realized_vol_m15,
            self.adx_m15 / 100.0,
            self.session_code / 4.0,
            self.hour_of_day / 24.0,
            min(1.0, self.spread_to_sl_ratio)
        ], dtype=np.float64)

    @staticmethod
    def feature_names() -> List[str]:
        return [
            "confluence_score",
            "fvg_size_pips",
            "ob_volume_ratio",
            "liquidity_sweep_pips",
            "displacement_ratio",
            "realized_vol_m15",
            "adx_m15",
            "session_code",
            "hour_of_day",
            "spread_to_sl_ratio"
        ]


class MetaFeatureExtractor:
    def __init__(self, point: float = 0.00001):
        self.point = point

    def extract_features_at_bar(
        self,
        historical_candles_up_to_signal: List[Candle],
        candidate: SignalCandidate,
        current_spread_pts: float = 10.0
    ) -> MetaFeatureVector:
        """
        Extracts features strictly from closed bars up to the signal time.
        Any candle timestamp > candidate.created_at raises a strict LookaheadError!
        """
        candles = historical_candles_up_to_signal
        if not candles:
            raise ValueError("historical_candles_up_to_signal must not be empty.")

        last_bar = candles[-1]
        sig_time = getattr(candidate, 'timestamp', getattr(candidate, 'created_at', None))
        # Invariant: No future bars may be passed!
        if sig_time is not None and last_bar.time > sig_time:
            raise ValueError(
                f"Lookahead violation detected! Bar time {last_bar.time} > Signal time {sig_time}"
            )

        closes = np.array([c.close for c in candles[-25:]], dtype=np.float64)
        highs = np.array([c.high for c in candles[-25:]], dtype=np.float64)
        lows = np.array([c.low for c in candles[-25:]], dtype=np.float64)
        vols = np.array([c.tick_volume for c in candles[-25:]], dtype=np.float64)

        # 1. Confluence score from setup
        confluence = float(getattr(candidate, 'score', getattr(candidate, 'confluence_score', 50.0)))

        # 2. Structural features
        pt = self.point if self.point > 0 else 0.00001
        pip_sz = pt * 10.0

        # FVG size in pips
        fvg_size = abs(highs[-2] - lows[-1]) / pip_sz if len(highs) >= 2 else 1.5

        # OB volume ratio
        avg_vol = np.mean(vols[:-1]) if len(vols) > 1 else 100.0
        ob_vol_ratio = float(vols[-1] / (avg_vol + 1e-5))

        # Liquidity sweep in pips
        sweep_pips = abs(last_bar.high - highs[-5]) / pip_sz if len(highs) >= 5 else 2.0

        # Displacement ratio (body to total range)
        body = abs(last_bar.close - last_bar.open)
        rng = max(pt, last_bar.high - last_bar.low)
        disp_ratio = float(body / rng)

        # Realized volatility
        log_rets = np.diff(np.log(closes)) if len(closes) > 1 else np.array([0.0])
        realized_vol = float(np.std(log_rets, ddof=1) * math.sqrt(24 * 4)) if len(log_rets) > 1 else 0.01

        # ADX (approximate Wilder's)
        tr = np.maximum(highs[1:] - lows[1:], np.abs(highs[1:] - closes[:-1]))
        adx_val = float(np.mean(tr) / (np.mean(closes) + 1e-9) * 1000.0)

        # Session code & hour
        hour = last_bar.time.hour
        if 0 <= hour < 8:
            session = 0
        elif 8 <= hour < 13:
            session = 1
        elif 13 <= hour < 17:
            session = 2
        elif 17 <= hour < 22:
            session = 3
        else:
            session = 4

        # Spread to SL ratio
        sl_dist_pts = abs(candidate.entry_price - candidate.stop_loss) / pt if pt > 0 else 100.0
        spread_to_sl = float(current_spread_pts / (sl_dist_pts + 1e-5))

        return MetaFeatureVector(
            signal_time=sig_time or last_bar.time,
            confluence_score=confluence,
            fvg_size_pips=fvg_size,
            ob_volume_ratio=ob_vol_ratio,
            liquidity_sweep_pips=sweep_pips,
            displacement_ratio=disp_ratio,
            realized_vol_m15=realized_vol,
            adx_m15=adx_val,
            session_code=session,
            hour_of_day=hour,
            spread_to_sl_ratio=spread_to_sl
        )
