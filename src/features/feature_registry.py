"""
Data & Feature Pipeline Registry.
Deterministic, point-in-time technical, SMC, regime, and multi-timeframe feature extraction.
Guarantees ZERO future lookahead leakage.
"""

from __future__ import annotations
import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple, Any
import numpy as np

from src.core.contracts import Candle, Timeframe


@dataclass
class SwingPoint:
    index: int
    time: datetime
    price: float
    point_type: str  # "HIGH" or "LOW"


@dataclass
class FVGFeature:
    index: int
    time: datetime
    direction: str  # "BULLISH" or "BEARISH"
    top: float
    bottom: float
    size: float
    mitigated: bool = False


@dataclass
class OrderBlockFeature:
    index: int
    time: datetime
    direction: str  # "BULLISH" or "BEARISH"
    high: float
    low: float
    displacement_atr: float


@dataclass
class FeatureSnapshot:
    symbol: str
    timestamp: datetime
    timeframe: str
    close: float
    # Technical
    atr_14: float = 0.0
    ema_20: float = 0.0
    ema_50: float = 0.0
    ema_200: float = 0.0
    rsi_14: float = 50.0
    adx_14: float = 0.0
    plus_di_14: float = 0.0
    minus_di_14: float = 0.0
    bb_upper: float = 0.0
    bb_middle: float = 0.0
    bb_lower: float = 0.0
    bb_pct_b: float = 0.5
    # SMC
    recent_swing_high: Optional[float] = None
    recent_swing_low: Optional[float] = None
    equilibrium_price: Optional[float] = None
    in_discount_zone: bool = False
    in_premium_zone: bool = False
    active_bullish_fvg: bool = False
    active_bearish_fvg: bool = False
    bullish_sweep_detected: bool = False
    bearish_sweep_detected: bool = False
    # Regime
    volatility_ratio: float = 1.0
    trend_strength: float = 0.0  # -1.0 (strong down) to +1.0 (strong up)
    session_asian: bool = False
    session_london: bool = False
    session_ny: bool = False
    session_overlap: bool = False
    # MTF Alignment
    mtf_alignment_score: float = 0.0


class FeatureExtractor:
    """
    Computes technical and SMC features strictly on closed candle history.
    """

    @staticmethod
    def compute_atr(candles: List[Candle], period: int = 14) -> float:
        if len(candles) < period + 1:
            return 0.0
        trs = []
        for i in range(1, len(candles)):
            c_curr = candles[i]
            c_prev = candles[i - 1]
            tr = max(
                c_curr.high - c_curr.low,
                abs(c_curr.high - c_prev.close),
                abs(c_curr.low - c_prev.close)
            )
            trs.append(tr)
        if len(trs) < period:
            return 0.0
        # Wilders smoothed ATR
        atr = float(np.mean(trs[:period]))
        for tr in trs[period:]:
            atr = (atr * (period - 1) + tr) / period
        return float(atr)

    @staticmethod
    def compute_ema(prices: List[float], period: int) -> float:
        if len(prices) < period:
            return prices[-1] if prices else 0.0
        k = 2.0 / (period + 1.0)
        ema = float(np.mean(prices[:period]))
        for p in prices[period:]:
            ema = p * k + ema * (1.0 - k)
        return float(ema)

    @staticmethod
    def compute_rsi(candles: List[Candle], period: int = 14) -> float:
        if len(candles) < period + 1:
            return 50.0
        changes = [candles[i].close - candles[i - 1].close for i in range(1, len(candles))]
        gains = [max(0.0, chg) for chg in changes]
        losses = [max(0.0, -chg) for chg in changes]

        if len(changes) < period:
            return 50.0

        avg_gain = float(np.mean(gains[:period]))
        avg_loss = float(np.mean(losses[:period]))

        for i in range(period, len(changes)):
            avg_gain = (avg_gain * (period - 1) + gains[i]) / period
            avg_loss = (avg_loss * (period - 1) + losses[i]) / period

        if avg_loss == 0.0:
            return 100.0 if avg_gain > 0 else 50.0

        rs = avg_gain / avg_loss
        return float(100.0 - (100.0 / (1.0 + rs)))

    @staticmethod
    def compute_adx(candles: List[Candle], period: int = 14) -> Tuple[float, float, float]:
        """Returns (ADX, +DI, -DI)."""
        if len(candles) < period * 2:
            return 0.0, 0.0, 0.0

        trs, plus_dms, minus_dms = [], [], []
        for i in range(1, len(candles)):
            c_curr = candles[i]
            c_prev = candles[i - 1]

            tr = max(
                c_curr.high - c_curr.low,
                abs(c_curr.high - c_prev.close),
                abs(c_curr.low - c_prev.close)
            )
            up_move = c_curr.high - c_prev.high
            down_move = c_prev.low - c_curr.low

            plus_dm = up_move if (up_move > down_move and up_move > 0) else 0.0
            minus_dm = down_move if (down_move > up_move and down_move > 0) else 0.0

            trs.append(tr)
            plus_dms.append(plus_dm)
            minus_dms.append(minus_dm)

        smooth_tr = float(np.sum(trs[:period]))
        smooth_pdm = float(np.sum(plus_dms[:period]))
        smooth_mdm = float(np.sum(minus_dms[:period]))

        dxs = []
        for i in range(period, len(trs)):
            smooth_tr = smooth_tr - (smooth_tr / period) + trs[i]
            smooth_pdm = smooth_pdm - (smooth_pdm / period) + plus_dms[i]
            smooth_mdm = smooth_mdm - (smooth_mdm / period) + minus_dms[i]

            p_di = (smooth_pdm / smooth_tr * 100.0) if smooth_tr > 0 else 0.0
            m_di = (smooth_mdm / smooth_tr * 100.0) if smooth_tr > 0 else 0.0
            di_diff = abs(p_di - m_di)
            di_sum = p_di + m_di
            dx = (di_diff / di_sum * 100.0) if di_sum > 0 else 0.0
            dxs.append((dx, p_di, m_di))

        if not dxs or len(dxs) < period:
            return 0.0, 0.0, 0.0

        adx = float(np.mean([d[0] for d in dxs[:period]]))
        for d in dxs[period:]:
            adx = (adx * (period - 1) + d[0]) / period

        return float(adx), float(dxs[-1][1]), float(dxs[-1][2])

    @staticmethod
    def compute_bollinger_bands(prices: List[float], period: int = 20, num_std: float = 2.0) -> Tuple[float, float, float, float]:
        """Returns (upper, middle, lower, pct_b)."""
        if len(prices) < period:
            p = prices[-1] if prices else 0.0
            return p, p, p, 0.5
        window = prices[-period:]
        mid = float(np.mean(window))
        std = float(np.std(window))
        upper = mid + num_std * std
        lower = mid - num_std * std
        curr = prices[-1]
        pct_b = (curr - lower) / (upper - lower) if (upper - lower) > 0 else 0.5
        return upper, mid, lower, pct_b

    @staticmethod
    def detect_swings(candles: List[Candle], k: int = 3) -> Tuple[List[SwingPoint], List[SwingPoint]]:
        """
        Detects fractal swing highs and lows strictly with k-bar confirmation.
        A bar at index i is a swing high only if confirmed by k bars before and after.
        """
        highs: List[SwingPoint] = []
        lows: List[SwingPoint] = []
        n = len(candles)
        if n < 2 * k + 1:
            return highs, lows

        for i in range(k, n - k):
            c = candles[i]
            is_high = all(candles[i].high > candles[j].high for j in range(i - k, i + k + 1) if j != i)
            is_low = all(candles[i].low < candles[j].low for j in range(i - k, i + k + 1) if j != i)

            if is_high:
                highs.append(SwingPoint(index=i, time=c.time, price=c.high, point_type="HIGH"))
            if is_low:
                lows.append(SwingPoint(index=i, time=c.time, price=c.low, point_type="LOW"))

        return highs, lows

    @staticmethod
    def detect_fvgs(candles: List[Candle], min_size: float = 0.0) -> List[FVGFeature]:
        """
        Fair Value Gap (FVG) detection across 3-bar sequences.
        Bullish: candle[i-2].high < candle[i].low
        Bearish: candle[i-2].low > candle[i].high
        """
        fvgs: List[FVGFeature] = []
        if len(candles) < 3:
            return fvgs

        for i in range(2, len(candles)):
            c1 = candles[i - 2]
            c3 = candles[i]

            # Bullish FVG
            if c3.low > c1.high:
                size = c3.low - c1.high
                if size >= min_size:
                    mitigated = any(candles[j].low <= c1.high for j in range(i + 1, len(candles)))
                    fvgs.append(FVGFeature(
                        index=i - 1,
                        time=candles[i - 1].time,
                        direction="BULLISH",
                        top=c3.low,
                        bottom=c1.high,
                        size=size,
                        mitigated=mitigated
                    ))

            # Bearish FVG
            elif c3.high < c1.low:
                size = c1.low - c3.high
                if size >= min_size:
                    mitigated = any(candles[j].high >= c1.low for j in range(i + 1, len(candles)))
                    fvgs.append(FVGFeature(
                        index=i - 1,
                        time=candles[i - 1].time,
                        direction="BEARISH",
                        top=c1.low,
                        bottom=c3.high,
                        size=size,
                        mitigated=mitigated
                    ))

        return fvgs

    @staticmethod
    def detect_liquidity_sweep(
        candles: List[Candle],
        swing_highs: List[SwingPoint],
        swing_lows: List[SwingPoint],
        point: float = 0.00001
    ) -> Tuple[bool, bool]:
        """
        Checks if the latest closed candle swept liquidity:
        - Bullish sweep (swept lows): bar low pierced a prior swing low, but closed above it.
        - Bearish sweep (swept highs): bar high pierced a prior swing high, but closed below it.
        """
        if not candles or (not swing_highs and not swing_lows):
            return False, False

        last_bar = candles[-1]
        bullish_sweep = False
        bearish_sweep = False

        if swing_lows:
            recent_low = swing_lows[-1]
            if last_bar.low < recent_low.price and last_bar.close > recent_low.price:
                bullish_sweep = True

        if swing_highs:
            recent_high = swing_highs[-1]
            if last_bar.high > recent_high.price and last_bar.close < recent_high.price:
                bearish_sweep = True

        return bullish_sweep, bearish_sweep

    @staticmethod
    def get_session_flags(t: datetime) -> Tuple[bool, bool, bool, bool]:
        """Determines Asian, London, NY, and Overlap sessions for a given UTC timestamp."""
        utc_dt = t.astimezone(timezone.utc) if t.tzinfo else t.replace(tzinfo=timezone.utc)
        hour = utc_dt.hour
        asian = 0 <= hour < 8
        london = 7 <= hour < 16
        ny = 13 <= hour < 21
        overlap = 13 <= hour < 16
        return asian, london, ny, overlap


class FeaturePipeline:
    """
    Central Feature Pipeline coordinating all feature extractors.
    """

    @classmethod
    def extract_snapshot(
        cls,
        symbol: str,
        m15_candles: List[Candle],
        h1_candles: Optional[List[Candle]] = None,
        h4_candles: Optional[List[Candle]] = None,
        d1_candles: Optional[List[Candle]] = None,
        point: float = 0.00001
    ) -> FeatureSnapshot:
        if not m15_candles:
            return FeatureSnapshot(symbol=symbol, timestamp=datetime.now(timezone.utc), timeframe="M15", close=0.0)

        latest_bar = m15_candles[-1]
        closes = [c.close for c in m15_candles]

        # 1. Technical Indicators
        atr_14 = FeatureExtractor.compute_atr(m15_candles, 14)
        atr_50 = FeatureExtractor.compute_atr(m15_candles, 50) if len(m15_candles) >= 51 else atr_14
        vol_ratio = (atr_14 / atr_50) if atr_50 > 0 else 1.0

        ema_20 = FeatureExtractor.compute_ema(closes, 20)
        ema_50 = FeatureExtractor.compute_ema(closes, 50)
        ema_200 = FeatureExtractor.compute_ema(closes, 200)

        rsi_14 = FeatureExtractor.compute_rsi(m15_candles, 14)
        adx_14, p_di, m_di = FeatureExtractor.compute_adx(m15_candles, 14)
        bb_upper, bb_mid, bb_lower, pct_b = FeatureExtractor.compute_bollinger_bands(closes, 20, 2.0)

        # 2. SMC Indicators
        highs, lows = FeatureExtractor.detect_swings(m15_candles, k=3)
        recent_sh = highs[-1].price if highs else None
        recent_sl = lows[-1].price if lows else None

        eq_price = None
        in_discount = False
        in_premium = False
        if recent_sh and recent_sl and recent_sh > recent_sl:
            eq_price = (recent_sh + recent_sl) / 2.0
            in_discount = latest_bar.close < eq_price
            in_premium = latest_bar.close > eq_price

        fvgs = FeatureExtractor.detect_fvgs(m15_candles, min_size=point * 5)
        active_bull_fvg = any(not f.mitigated and f.direction == "BULLISH" for f in fvgs[-5:])
        active_bear_fvg = any(not f.mitigated and f.direction == "BEARISH" for f in fvgs[-5:])

        bull_sweep, bear_sweep = FeatureExtractor.detect_liquidity_sweep(m15_candles, highs, lows, point)

        # 3. Regime & Sessions
        asian, london, ny, overlap = FeatureExtractor.get_session_flags(latest_bar.time)

        # Trend Strength (-1.0 to +1.0)
        trend_score = 0.0
        if ema_20 > ema_50 > ema_200:
            trend_score = 0.6 + (0.4 * min(adx_14 / 50.0, 1.0))
        elif ema_20 < ema_50 < ema_200:
            trend_score = -0.6 - (0.4 * min(adx_14 / 50.0, 1.0))
        elif ema_20 > ema_50:
            trend_score = 0.3
        elif ema_20 < ema_50:
            trend_score = -0.3

        # 4. Multi-Timeframe Alignment
        mtf_score = cls._compute_mtf_alignment(m15_candles, h1_candles, h4_candles, d1_candles)

        return FeatureSnapshot(
            symbol=symbol,
            timestamp=latest_bar.time,
            timeframe="M15",
            close=latest_bar.close,
            atr_14=round(atr_14, 5),
            ema_20=round(ema_20, 5),
            ema_50=round(ema_50, 5),
            ema_200=round(ema_200, 5),
            rsi_14=round(rsi_14, 2),
            adx_14=round(adx_14, 2),
            plus_di_14=round(p_di, 2),
            minus_di_14=round(m_di, 2),
            bb_upper=round(bb_upper, 5),
            bb_middle=round(bb_mid, 5),
            bb_lower=round(bb_lower, 5),
            bb_pct_b=round(pct_b, 3),
            recent_swing_high=recent_sh,
            recent_swing_low=recent_sl,
            equilibrium_price=eq_price,
            in_discount_zone=in_discount,
            in_premium_zone=in_premium,
            active_bullish_fvg=active_bull_fvg,
            active_bearish_fvg=active_bear_fvg,
            bullish_sweep_detected=bull_sweep,
            bearish_sweep_detected=bear_sweep,
            volatility_ratio=round(vol_ratio, 2),
            trend_strength=round(trend_score, 2),
            session_asian=asian,
            session_london=london,
            session_ny=ny,
            session_overlap=overlap,
            mtf_alignment_score=round(mtf_score, 2)
        )

    @classmethod
    def _compute_mtf_alignment(
        cls,
        m15: List[Candle],
        h1: Optional[List[Candle]],
        h4: Optional[List[Candle]],
        d1: Optional[List[Candle]]
    ) -> float:
        tf_scores = []
        for dataset in [m15, h1, h4, d1]:
            if dataset and len(dataset) >= 20:
                closes = [c.close for c in dataset]
                e20 = FeatureExtractor.compute_ema(closes, 20)
                e50 = FeatureExtractor.compute_ema(closes, 50) if len(closes) >= 50 else e20
                if e20 > e50:
                    tf_scores.append(1.0)
                elif e20 < e50:
                    tf_scores.append(-1.0)
                else:
                    tf_scores.append(0.0)

        if not tf_scores:
            return 0.0
        return float(np.mean(tf_scores))
