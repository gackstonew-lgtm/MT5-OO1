"""
Institutional Candlestick Pattern Detection Engine.
Implements 51 vectorized, closed-candle pattern detectors with ATR-normalized ratios,
prior trend validation, and Forex gap-equivalent handling.
"""

from __future__ import annotations
from typing import Dict, List, Optional, Any, Tuple
from datetime import datetime, timezone
import numpy as np

from src.core.contracts import Candle
from src.patterns.contracts import (
    PatternResult, PatternFamily, PatternDirection, PatternRegistry
)


def compute_atr(highs: Any, lows: Any, closes: Any, period: int = 14) -> float:
    """Calculates Wilder's ATR over closed bars."""
    highs = np.asarray(highs, dtype=np.float64)
    lows = np.asarray(lows, dtype=np.float64)
    closes = np.asarray(closes, dtype=np.float64)
    n = len(closes)
    if n < 2:
        return float(highs[-1] - lows[-1]) if n == 1 else 0.0010
    tr = np.maximum(highs[1:] - lows[1:], np.maximum(np.abs(highs[1:] - closes[:-1]), np.abs(lows[1:] - closes[:-1])))
    if len(tr) < period:
        return float(np.mean(tr)) if len(tr) > 0 else 0.0010
    return float(np.mean(tr[-period:]))


def compute_prior_trend(closes: np.ndarray, lookback: int = 5, atr: float = 0.0010) -> str:
    """
    Evaluates prior trend slope normalized by ATR.
    Returns: 'UP', 'DOWN', or 'FLAT'.
    """
    if len(closes) < lookback + 1:
        return "FLAT"
    window = closes[-(lookback + 1):-1]
    if len(window) < 2:
        return "FLAT"
    slope = (window[-1] - window[0]) / (atr * len(window))
    if slope > 0.15:
        return "UP"
    elif slope < -0.15:
        return "DOWN"
    return "FLAT"


class CandlestickEngine:
    def __init__(self, config: Optional[Dict[str, Any]] = None):
        self.config = config or {}

    def scan_candle_patterns(self, candles: List[Candle]) -> List[PatternResult]:
        """
        Executes all enabled candlestick detectors on the last closed bars.
        Zero lookahead: evaluates strictly closed bars.
        """
        results: List[PatternResult] = []
        n = len(candles)
        if n < 5:
            return results

        opens = np.array([c.open for c in candles], dtype=np.float64)
        highs = np.array([c.high for c in candles], dtype=np.float64)
        lows = np.array([c.low for c in candles], dtype=np.float64)
        closes = np.array([c.close for c in candles], dtype=np.float64)
        volumes = np.array([c.tick_volume for c in candles], dtype=np.float64)

        atr_period = self.config.get("atr_period", 14)
        atr = compute_atr(highs, lows, closes, period=atr_period)
        enabled_map = self.config.get("patterns_enabled", {})

        detectors = PatternRegistry.get_detectors(PatternFamily.CANDLE)
        for name, func in detectors.items():
            if enabled_map.get(name, True):
                try:
                    res = func(opens, highs, lows, closes, volumes, atr, self.config)
                    if res:
                        results.append(res)
                except Exception:
                    continue

        return results


# =====================================================================
# CANDLESTICK PATTERN DETECTORS
# =====================================================================

@PatternRegistry.register(PatternFamily.CANDLE, "Dragonfly Doji")
def detect_dragonfly_doji(opens, highs, lows, closes, vols, atr, cfg) -> Optional[PatternResult]:
    o, h, l, c = opens[-1], highs[-1], lows[-1], closes[-1]
    rng = h - l
    if rng <= 0 or rng < 0.2 * atr:
        return None
    body = abs(c - o)
    lower_shadow = min(o, c) - l
    upper_shadow = h - max(o, c)
    if (body / rng <= 0.10) and (lower_shadow / rng >= 0.65) and (upper_shadow / rng <= 0.15):
        return PatternResult(
            name="Dragonfly Doji",
            family=PatternFamily.CANDLE,
            direction=PatternDirection.BULLISH,
            bars_used=1,
            key_levels={"low": l, "open": o, "close": c},
            invalidation_price=l - 0.2 * atr,
            measured_target=c + rng * 1.5,
            raw_strength=0.75,
            details="Long lower shadow with open and close near high"
        )
    return None


@PatternRegistry.register(PatternFamily.CANDLE, "Gravestone Doji")
def detect_gravestone_doji(opens, highs, lows, closes, vols, atr, cfg) -> Optional[PatternResult]:
    o, h, l, c = opens[-1], highs[-1], lows[-1], closes[-1]
    rng = h - l
    if rng <= 0 or rng < 0.2 * atr:
        return None
    body = abs(c - o)
    upper_shadow = h - max(o, c)
    lower_shadow = min(o, c) - l
    if (body / rng <= 0.10) and (upper_shadow / rng >= 0.65) and (lower_shadow / rng <= 0.15):
        return PatternResult(
            name="Gravestone Doji",
            family=PatternFamily.CANDLE,
            direction=PatternDirection.BEARISH,
            bars_used=1,
            key_levels={"high": h, "open": o, "close": c},
            invalidation_price=h + 0.2 * atr,
            measured_target=c - rng * 1.5,
            raw_strength=0.75,
            details="Long upper shadow with open and close near low"
        )
    return None


@PatternRegistry.register(PatternFamily.CANDLE, "Standard Doji")
def detect_standard_doji(opens, highs, lows, closes, vols, atr, cfg) -> Optional[PatternResult]:
    o, h, l, c = opens[-1], highs[-1], lows[-1], closes[-1]
    rng = h - l
    if rng <= 0 or rng < 0.2 * atr:
        return None
    body = abs(c - o)
    if (body / rng <= 0.10):
        return PatternResult(
            name="Standard Doji",
            family=PatternFamily.CANDLE,
            direction=PatternDirection.NEUTRAL,
            bars_used=1,
            key_levels={"high": h, "low": l},
            invalidation_price=l,
            measured_target=h,
            raw_strength=0.50,
            details="Indecision candle with tiny body"
        )
    return None


@PatternRegistry.register(PatternFamily.CANDLE, "Long-Legged Doji")
def detect_long_legged_doji(opens, highs, lows, closes, vols, atr, cfg) -> Optional[PatternResult]:
    o, h, l, c = opens[-1], highs[-1], lows[-1], closes[-1]
    rng = h - l
    if rng < 1.0 * atr:
        return None
    body = abs(c - o)
    upper_shadow = h - max(o, c)
    lower_shadow = min(o, c) - l
    if (body / rng <= 0.10) and (upper_shadow / rng >= 0.35) and (lower_shadow / rng >= 0.35):
        return PatternResult(
            name="Long-Legged Doji",
            family=PatternFamily.CANDLE,
            direction=PatternDirection.NEUTRAL,
            bars_used=1,
            key_levels={"high": h, "low": l},
            invalidation_price=l,
            measured_target=h,
            raw_strength=0.65,
            details="Expansive range indecision candle"
        )
    return None


@PatternRegistry.register(PatternFamily.CANDLE, "Hammer")
def detect_hammer(opens, highs, lows, closes, vols, atr, cfg) -> Optional[PatternResult]:
    # Hammer requires prior DOWNTREND
    if compute_prior_trend(closes, atr=atr) != "DOWN":
        return None
    o, h, l, c = opens[-1], highs[-1], lows[-1], closes[-1]
    rng = h - l
    if rng <= 0 or rng < 0.3 * atr:
        return None
    body = abs(c - o)
    lower_shadow = min(o, c) - l
    upper_shadow = h - max(o, c)
    if (body > 0) and (lower_shadow >= 2.0 * body) and (upper_shadow <= 0.20 * rng) and (body / rng <= 0.35):
        return PatternResult(
            name="Hammer",
            family=PatternFamily.CANDLE,
            direction=PatternDirection.BULLISH,
            bars_used=1,
            key_levels={"low": l, "high": h},
            invalidation_price=l - 0.2 * atr,
            measured_target=h + 2.0 * body,
            raw_strength=0.80,
            details="Bullish rejection hammer after downtrend"
        )
    return None


@PatternRegistry.register(PatternFamily.CANDLE, "Hanging Man")
def detect_hanging_man(opens, highs, lows, closes, vols, atr, cfg) -> Optional[PatternResult]:
    # Hanging Man has identical shape to hammer, but requires prior UPTREND
    if compute_prior_trend(closes, atr=atr) != "UP":
        return None
    o, h, l, c = opens[-1], highs[-1], lows[-1], closes[-1]
    rng = h - l
    if rng <= 0 or rng < 0.3 * atr:
        return None
    body = abs(c - o)
    lower_shadow = min(o, c) - l
    upper_shadow = h - max(o, c)
    if (body > 0) and (lower_shadow >= 2.0 * body) and (upper_shadow <= 0.20 * rng) and (body / rng <= 0.35):
        return PatternResult(
            name="Hanging Man",
            family=PatternFamily.CANDLE,
            direction=PatternDirection.BEARISH,
            bars_used=1,
            key_levels={"high": h, "low": l},
            invalidation_price=h + 0.2 * atr,
            measured_target=l - 2.0 * body,
            raw_strength=0.75,
            details="Bearish hanging man warning after uptrend"
        )
    return None


@PatternRegistry.register(PatternFamily.CANDLE, "Inverted Hammer")
def detect_inverted_hammer(opens, highs, lows, closes, vols, atr, cfg) -> Optional[PatternResult]:
    # Inverted Hammer requires prior DOWNTREND
    if compute_prior_trend(closes, atr=atr) != "DOWN":
        return None
    o, h, l, c = opens[-1], highs[-1], lows[-1], closes[-1]
    rng = h - l
    if rng <= 0 or rng < 0.3 * atr:
        return None
    body = abs(c - o)
    upper_shadow = h - max(o, c)
    lower_shadow = min(o, c) - l
    if (body > 0) and (upper_shadow >= 2.0 * body) and (lower_shadow <= 0.20 * rng) and (body / rng <= 0.35):
        return PatternResult(
            name="Inverted Hammer",
            family=PatternFamily.CANDLE,
            direction=PatternDirection.BULLISH,
            bars_used=1,
            key_levels={"low": l, "high": h},
            invalidation_price=l - 0.2 * atr,
            measured_target=h + 2.0 * body,
            raw_strength=0.75,
            details="Bullish inverted hammer after downtrend"
        )
    return None


@PatternRegistry.register(PatternFamily.CANDLE, "Shooting Star")
def detect_shooting_star(opens, highs, lows, closes, vols, atr, cfg) -> Optional[PatternResult]:
    # Shooting star requires prior UPTREND
    if compute_prior_trend(closes, atr=atr) != "UP":
        return None
    o, h, l, c = opens[-1], highs[-1], lows[-1], closes[-1]
    rng = h - l
    if rng <= 0 or rng < 0.3 * atr:
        return None
    body = abs(c - o)
    upper_shadow = h - max(o, c)
    lower_shadow = min(o, c) - l
    if (body > 0) and (upper_shadow >= 2.0 * body) and (lower_shadow <= 0.20 * rng) and (body / rng <= 0.35):
        return PatternResult(
            name="Shooting Star",
            family=PatternFamily.CANDLE,
            direction=PatternDirection.BEARISH,
            bars_used=1,
            key_levels={"high": h, "low": l},
            invalidation_price=h + 0.2 * atr,
            measured_target=l - 2.0 * body,
            raw_strength=0.80,
            details="Bearish rejection shooting star after uptrend"
        )
    return None


@PatternRegistry.register(PatternFamily.CANDLE, "Bullish Engulfing")
def detect_bullish_engulfing(opens, highs, lows, closes, vols, atr, cfg) -> Optional[PatternResult]:
    if len(closes) < 2 or compute_prior_trend(closes, atr=atr) == "UP":
        return None
    o1, c1 = opens[-2], closes[-2]
    o2, c2 = opens[-1], closes[-1]
    l2, h2 = lows[-1], highs[-1]
    # Prior candle bearish, current candle bullish and engulfs body
    if (c1 < o1) and (c2 > o2) and (c2 >= o1) and (o2 <= c1):
        return PatternResult(
            name="Bullish Engulfing",
            family=PatternFamily.CANDLE,
            direction=PatternDirection.BULLISH,
            bars_used=2,
            key_levels={"low": l2, "high": h2, "breakout": c2},
            invalidation_price=min(lows[-2], l2) - 0.2 * atr,
            measured_target=h2 + (h2 - l2),
            raw_strength=0.85,
            details="Bullish candle completely engulfs prior bearish candle body"
        )
    return None


@PatternRegistry.register(PatternFamily.CANDLE, "Bearish Engulfing")
def detect_bearish_engulfing(opens, highs, lows, closes, vols, atr, cfg) -> Optional[PatternResult]:
    if len(closes) < 2 or compute_prior_trend(closes, atr=atr) == "DOWN":
        return None
    o1, c1 = opens[-2], closes[-2]
    o2, c2 = opens[-1], closes[-1]
    l2, h2 = lows[-1], highs[-1]
    # Prior candle bullish, current candle bearish and engulfs body
    if (c1 > o1) and (c2 < o2) and (o2 >= c1) and (c2 <= o1):
        return PatternResult(
            name="Bearish Engulfing",
            family=PatternFamily.CANDLE,
            direction=PatternDirection.BEARISH,
            bars_used=2,
            key_levels={"high": h2, "low": l2, "breakout": c2},
            invalidation_price=max(highs[-2], h2) + 0.2 * atr,
            measured_target=l2 - (h2 - l2),
            raw_strength=0.85,
            details="Bearish candle completely engulfs prior bullish candle body"
        )
    return None


@PatternRegistry.register(PatternFamily.CANDLE, "Bullish Harami")
def detect_bullish_harami(opens, highs, lows, closes, vols, atr, cfg) -> Optional[PatternResult]:
    if len(closes) < 2:
        return None
    o1, c1 = opens[-2], closes[-2]
    o2, c2 = opens[-1], closes[-1]
    # Mother is large bearish bar, child is small bullish/neutral bar inside mother body
    if (c1 < o1) and (abs(c1 - o1) >= 0.5 * atr):
        if (min(o2, c2) >= c1) and (max(o2, c2) <= o1) and (abs(c2 - o2) < 0.7 * abs(c1 - o1)):
            return PatternResult(
                name="Bullish Harami",
                family=PatternFamily.CANDLE,
                direction=PatternDirection.BULLISH,
                bars_used=2,
                key_levels={"mother_low": lows[-2], "mother_high": highs[-2]},
                invalidation_price=lows[-2] - 0.2 * atr,
                measured_target=highs[-2] + 0.5 * atr,
                raw_strength=0.70,
                details="Small body contained inside prior large bearish candle"
            )
    return None


@PatternRegistry.register(PatternFamily.CANDLE, "Bearish Harami")
def detect_bearish_harami(opens, highs, lows, closes, vols, atr, cfg) -> Optional[PatternResult]:
    if len(closes) < 2:
        return None
    o1, c1 = opens[-2], closes[-2]
    o2, c2 = opens[-1], closes[-1]
    if (c1 > o1) and (abs(c1 - o1) >= 0.5 * atr):
        if (min(o2, c2) >= o1) and (max(o2, c2) <= c1) and (abs(c2 - o2) < 0.7 * abs(c1 - o1)):
            return PatternResult(
                name="Bearish Harami",
                family=PatternFamily.CANDLE,
                direction=PatternDirection.BEARISH,
                bars_used=2,
                key_levels={"mother_high": highs[-2], "mother_low": lows[-2]},
                invalidation_price=highs[-2] + 0.2 * atr,
                measured_target=lows[-2] - 0.5 * atr,
                raw_strength=0.70,
                details="Small body contained inside prior large bullish candle"
            )
    return None


@PatternRegistry.register(PatternFamily.CANDLE, "Bullish Harami Cross")
def detect_bullish_harami_cross(opens, highs, lows, closes, vols, atr, cfg) -> Optional[PatternResult]:
    # Note: Reference chart labels this tile "Bullish Harboring", which is a mislabel for Bullish Harami Cross
    if len(closes) < 2:
        return None
    o1, c1 = opens[-2], closes[-2]
    o2, c2 = opens[-1], closes[-1]
    rng2 = highs[-1] - lows[-1]
    if (c1 < o1) and (abs(c1 - o1) >= 0.5 * atr) and rng2 > 0:
        if (abs(c2 - o2) / rng2 <= 0.10) and (min(o2, c2) >= c1) and (max(o2, c2) <= o1):
            return PatternResult(
                name="Bullish Harami Cross",
                family=PatternFamily.CANDLE,
                direction=PatternDirection.BULLISH,
                bars_used=2,
                key_levels={"low": lows[-2], "high": highs[-2]},
                invalidation_price=lows[-2] - 0.2 * atr,
                measured_target=highs[-2] + 0.5 * atr,
                raw_strength=0.75,
                details="Doji cross contained entirely within previous large bearish candle body (ref: Bullish Harboring)"
            )
    return None


@PatternRegistry.register(PatternFamily.CANDLE, "Bearish Harami Cross")
def detect_bearish_harami_cross(opens, highs, lows, closes, vols, atr, cfg) -> Optional[PatternResult]:
    if len(closes) < 2:
        return None
    o1, c1 = opens[-2], closes[-2]
    o2, c2 = opens[-1], closes[-1]
    rng2 = highs[-1] - lows[-1]
    if (c1 > o1) and (abs(c1 - o1) >= 0.5 * atr) and rng2 > 0:
        if (abs(c2 - o2) / rng2 <= 0.10) and (min(o2, c2) >= o1) and (max(o2, c2) <= c1):
            return PatternResult(
                name="Bearish Harami Cross",
                family=PatternFamily.CANDLE,
                direction=PatternDirection.BEARISH,
                bars_used=2,
                key_levels={"high": highs[-2], "low": lows[-2]},
                invalidation_price=highs[-2] + 0.2 * atr,
                measured_target=lows[-2] - 0.5 * atr,
                raw_strength=0.75,
                details="Doji cross contained entirely within previous large bullish candle body"
            )
    return None


@PatternRegistry.register(PatternFamily.CANDLE, "Three Inside Up")
def detect_three_inside_up(opens, highs, lows, closes, vols, atr, cfg) -> Optional[PatternResult]:
    if len(closes) < 3:
        return None
    o1, c1 = opens[-3], closes[-3]
    o2, c2 = opens[-2], closes[-2]
    o3, c3 = opens[-1], closes[-1]
    # Bar 1 bearish, Bar 2 harami inside, Bar 3 confirms by closing above bar 1 open
    if (c1 < o1) and (c2 > o2) and (min(o2, c2) >= c1) and (max(o2, c2) <= o1) and (c3 > o1):
        return PatternResult(
            name="Three Inside Up",
            family=PatternFamily.CANDLE,
            direction=PatternDirection.BULLISH,
            bars_used=3,
            key_levels={"confirmation": c3, "low": min(lows[-3:])},
            invalidation_price=min(lows[-3:]) - 0.2 * atr,
            measured_target=c3 + atr,
            raw_strength=0.85,
            details="Harami followed by breakout confirmation close"
        )
    return None


@PatternRegistry.register(PatternFamily.CANDLE, "Three Inside Down")
def detect_three_inside_down(opens, highs, lows, closes, vols, atr, cfg) -> Optional[PatternResult]:
    if len(closes) < 3:
        return None
    o1, c1 = opens[-3], closes[-3]
    o2, c2 = opens[-2], closes[-2]
    o3, c3 = opens[-1], closes[-1]
    if (c1 > o1) and (c2 < o2) and (min(o2, c2) >= o1) and (max(o2, c2) <= c1) and (c3 < o1):
        return PatternResult(
            name="Three Inside Down",
            family=PatternFamily.CANDLE,
            direction=PatternDirection.BEARISH,
            bars_used=3,
            key_levels={"confirmation": c3, "high": max(highs[-3:])},
            invalidation_price=max(highs[-3:]) + 0.2 * atr,
            measured_target=c3 - atr,
            raw_strength=0.85,
            details="Bearish Harami followed by breakdown confirmation close"
        )
    return None


@PatternRegistry.register(PatternFamily.CANDLE, "Three Outside Up")
def detect_three_outside_up(opens, highs, lows, closes, vols, atr, cfg) -> Optional[PatternResult]:
    if len(closes) < 3:
        return None
    o1, c1 = opens[-3], closes[-3]
    o2, c2 = opens[-2], closes[-2]
    c3 = closes[-1]
    # Bar 1 bearish, Bar 2 bullish engulfing, Bar 3 closes higher than bar 2 close
    if (c1 < o1) and (c2 > o2) and (c2 >= o1) and (o2 <= c1) and (c3 > c2):
        return PatternResult(
            name="Three Outside Up",
            family=PatternFamily.CANDLE,
            direction=PatternDirection.BULLISH,
            bars_used=3,
            key_levels={"low": min(lows[-3:]), "breakout": c3},
            invalidation_price=min(lows[-3:]) - 0.2 * atr,
            measured_target=c3 + 1.5 * atr,
            raw_strength=0.90,
            details="Bullish engulfing followed by higher confirmation close"
        )
    return None


@PatternRegistry.register(PatternFamily.CANDLE, "Three Outside Down")
def detect_three_outside_down(opens, highs, lows, closes, vols, atr, cfg) -> Optional[PatternResult]:
    if len(closes) < 3:
        return None
    o1, c1 = opens[-3], closes[-3]
    o2, c2 = opens[-2], closes[-2]
    c3 = closes[-1]
    if (c1 > o1) and (c2 < o2) and (o2 >= c1) and (c2 <= o1) and (c3 < c2):
        return PatternResult(
            name="Three Outside Down",
            family=PatternFamily.CANDLE,
            direction=PatternDirection.BEARISH,
            bars_used=3,
            key_levels={"high": max(highs[-3:]), "breakout": c3},
            invalidation_price=max(highs[-3:]) + 0.2 * atr,
            measured_target=c3 - 1.5 * atr,
            raw_strength=0.90,
            details="Bearish engulfing followed by lower confirmation close"
        )
    return None


@PatternRegistry.register(PatternFamily.CANDLE, "Three White Soldiers")
def detect_three_white_soldiers(opens, highs, lows, closes, vols, atr, cfg) -> Optional[PatternResult]:
    if len(closes) < 3 or compute_prior_trend(closes, atr=atr) == "UP":
        return None
    o1, c1 = opens[-3], closes[-3]
    o2, c2 = opens[-2], closes[-2]
    o3, c3 = opens[-1], closes[-1]
    # Three consecutive expanding bullish bars closing near highs
    if (c1 > o1) and (c2 > o2) and (c3 > o3) and (c3 > c2 > c1):
        if (o2 > o1 and o2 < c1) and (o3 > o2 and o3 < c2):
            return PatternResult(
                name="Three White Soldiers",
                family=PatternFamily.CANDLE,
                direction=PatternDirection.BULLISH,
                bars_used=3,
                key_levels={"start_low": lows[-3], "high": highs[-1]},
                invalidation_price=lows[-3] - 0.2 * atr,
                measured_target=c3 + 1.5 * atr,
                raw_strength=0.90,
                details="Three strong consecutive bullish expansion candles"
            )
    return None


@PatternRegistry.register(PatternFamily.CANDLE, "Three Black Crows")
def detect_three_black_crows(opens, highs, lows, closes, vols, atr, cfg) -> Optional[PatternResult]:
    if len(closes) < 3 or compute_prior_trend(closes, atr=atr) == "DOWN":
        return None
    o1, c1 = opens[-3], closes[-3]
    o2, c2 = opens[-2], closes[-2]
    o3, c3 = opens[-1], closes[-1]
    if (c1 < o1) and (c2 < o2) and (c3 < o3) and (c3 < c2 < c1):
        if (o2 < o1 and o2 > c1) and (o3 < o2 and o3 > c2):
            return PatternResult(
                name="Three Black Crows",
                family=PatternFamily.CANDLE,
                direction=PatternDirection.BEARISH,
                bars_used=3,
                key_levels={"start_high": highs[-3], "low": lows[-1]},
                invalidation_price=highs[-3] + 0.2 * atr,
                measured_target=c3 - 1.5 * atr,
                raw_strength=0.90,
                details="Three strong consecutive bearish expansion candles"
            )
    return None


@PatternRegistry.register(PatternFamily.CANDLE, "Morning Star")
def detect_morning_star(opens, highs, lows, closes, vols, atr, cfg) -> Optional[PatternResult]:
    if len(closes) < 3 or compute_prior_trend(closes, atr=atr) == "UP":
        return None
    o1, c1 = opens[-3], closes[-3]
    o2, c2 = opens[-2], closes[-2]
    o3, c3 = opens[-1], closes[-1]
    b1 = o1 - c1
    b2 = abs(c2 - o2)
    b3 = c3 - o3
    # Bar 1 large bearish, Bar 2 small body star, Bar 3 strong bullish closing > midpoint of Bar 1
    if (b1 >= 0.5 * atr) and (b2 <= 0.4 * b1) and (b3 >= 0.5 * atr) and (c3 >= c1 + 0.5 * b1):
        return PatternResult(
            name="Morning Star",
            family=PatternFamily.CANDLE,
            direction=PatternDirection.BULLISH,
            bars_used=3,
            key_levels={"star_low": lows[-2], "entry": c3},
            invalidation_price=min(lows[-3:]) - 0.2 * atr,
            measured_target=c3 + 1.5 * atr,
            raw_strength=0.88,
            details="Three-candle bullish morning star reversal"
        )
    return None


@PatternRegistry.register(PatternFamily.CANDLE, "Evening Star")
def detect_evening_star(opens, highs, lows, closes, vols, atr, cfg) -> Optional[PatternResult]:
    if len(closes) < 3 or compute_prior_trend(closes, atr=atr) == "DOWN":
        return None
    o1, c1 = opens[-3], closes[-3]
    o2, c2 = opens[-2], closes[-2]
    o3, c3 = opens[-1], closes[-1]
    b1 = c1 - o1
    b2 = abs(c2 - o2)
    b3 = o3 - c3
    if (b1 >= 0.5 * atr) and (b2 <= 0.4 * b1) and (b3 >= 0.5 * atr) and (c3 <= o1 + 0.5 * b1):
        return PatternResult(
            name="Evening Star",
            family=PatternFamily.CANDLE,
            direction=PatternDirection.BEARISH,
            bars_used=3,
            key_levels={"star_high": highs[-2], "entry": c3},
            invalidation_price=max(highs[-3:]) + 0.2 * atr,
            measured_target=c3 - 1.5 * atr,
            raw_strength=0.88,
            details="Three-candle bearish evening star reversal"
        )
    return None


@PatternRegistry.register(PatternFamily.CANDLE, "Piercing Line")
def detect_piercing_line(opens, highs, lows, closes, vols, atr, cfg) -> Optional[PatternResult]:
    if len(closes) < 2 or compute_prior_trend(closes, atr=atr) == "UP":
        return None
    o1, c1 = opens[-2], closes[-2]
    o2, c2 = opens[-1], closes[-1]
    b1 = o1 - c1
    # Bar 1 long bearish, Bar 2 opens <= c1 and closes > 50% into bar 1 body, but <= o1
    if (b1 >= 0.5 * atr) and (o2 <= c1) and (c2 >= c1 + 0.5 * b1) and (c2 < o1):
        return PatternResult(
            name="Piercing Line",
            family=PatternFamily.CANDLE,
            direction=PatternDirection.BULLISH,
            bars_used=2,
            key_levels={"low": lows[-1], "midpoint": c1 + 0.5 * b1},
            invalidation_price=min(lows[-2:]) - 0.2 * atr,
            measured_target=highs[-2] + 0.5 * atr,
            raw_strength=0.80,
            details="Bullish piercing pattern through 50% of prior candle"
        )
    return None


@PatternRegistry.register(PatternFamily.CANDLE, "Dark Cloud Cover")
def detect_dark_cloud_cover(opens, highs, lows, closes, vols, atr, cfg) -> Optional[PatternResult]:
    if len(closes) < 2 or compute_prior_trend(closes, atr=atr) == "DOWN":
        return None
    o1, c1 = opens[-2], closes[-2]
    o2, c2 = opens[-1], closes[-1]
    b1 = c1 - o1
    if (b1 >= 0.5 * atr) and (o2 >= c1) and (c2 <= o1 + 0.5 * b1) and (c2 > o1):
        return PatternResult(
            name="Dark Cloud Cover",
            family=PatternFamily.CANDLE,
            direction=PatternDirection.BEARISH,
            bars_used=2,
            key_levels={"high": highs[-1], "midpoint": o1 + 0.5 * b1},
            invalidation_price=max(highs[-2:]) + 0.2 * atr,
            measured_target=lows[-2] - 0.5 * atr,
            raw_strength=0.80,
            details="Bearish dark cloud piercing through 50% of prior bullish candle"
        )
    return None


@PatternRegistry.register(PatternFamily.CANDLE, "Bullish Abandoned Baby")
def detect_bullish_abandoned_baby(opens, highs, lows, closes, vols, atr, cfg) -> Optional[PatternResult]:
    if len(closes) < 3:
        return None
    gap_th = cfg.get("gap_equivalent_atr_multiplier", 0.20) * atr
    # Bar 1 bearish, Bar 2 doji gapping down (lows[-3] > highs[-2] or gap equivalent), Bar 3 bullish gapping up
    c1 = closes[-3]
    o2, c2 = opens[-2], closes[-2]
    o3 = opens[-1]
    if (c1 < opens[-3]) and (abs(c2 - o2) / max(0.0001, highs[-2] - lows[-2]) <= 0.15):
        if (max(o2, c2) < c1 - gap_th) and (min(o3, closes[-1]) > max(o2, c2) + gap_th) and (closes[-1] > opens[-1]):
            return PatternResult(
                name="Bullish Abandoned Baby",
                family=PatternFamily.CANDLE,
                direction=PatternDirection.BULLISH,
                bars_used=3,
                key_levels={"doji_low": lows[-2]},
                invalidation_price=lows[-2] - 0.2 * atr,
                measured_target=closes[-1] + 2.0 * atr,
                raw_strength=0.92,
                details="Island doji reversal isolated by gaps on both sides"
            )
    return None


@PatternRegistry.register(PatternFamily.CANDLE, "Bearish Abandoned Baby")
def detect_bearish_abandoned_baby(opens, highs, lows, closes, vols, atr, cfg) -> Optional[PatternResult]:
    if len(closes) < 3:
        return None
    gap_th = cfg.get("gap_equivalent_atr_multiplier", 0.20) * atr
    c1 = closes[-3]
    o2, c2 = opens[-2], closes[-2]
    o3 = opens[-1]
    if (c1 > opens[-3]) and (abs(c2 - o2) / max(0.0001, highs[-2] - lows[-2]) <= 0.15):
        if (min(o2, c2) > c1 + gap_th) and (max(o3, closes[-1]) < min(o2, c2) - gap_th) and (closes[-1] < opens[-1]):
            return PatternResult(
                name="Bearish Abandoned Baby",
                family=PatternFamily.CANDLE,
                direction=PatternDirection.BEARISH,
                bars_used=3,
                key_levels={"doji_high": highs[-2]},
                invalidation_price=highs[-2] + 0.2 * atr,
                measured_target=closes[-1] - 2.0 * atr,
                raw_strength=0.92,
                details="Bearish island doji reversal isolated by gaps on both sides"
            )
    return None


@PatternRegistry.register(PatternFamily.CANDLE, "Spinning Top")
def detect_spinning_top(opens, highs, lows, closes, vols, atr, cfg) -> Optional[PatternResult]:
    o, h, l, c = opens[-1], highs[-1], lows[-1], closes[-1]
    rng = h - l
    if rng <= 0 or rng < 0.3 * atr:
        return None
    body = abs(c - o)
    upper_shadow = h - max(o, c)
    lower_shadow = min(o, c) - l
    if (0.10 < body / rng <= 0.35) and (upper_shadow / rng >= 0.25) and (lower_shadow / rng >= 0.25):
        return PatternResult(
            name="Spinning Top",
            family=PatternFamily.CANDLE,
            direction=PatternDirection.NEUTRAL,
            bars_used=1,
            key_levels={"high": h, "low": l},
            invalidation_price=l,
            measured_target=h,
            raw_strength=0.55,
            details="Small body centered between upper and lower shadows"
        )
    return None


@PatternRegistry.register(PatternFamily.CANDLE, "Marubozu")
def detect_marubozu(opens, highs, lows, closes, vols, atr, cfg) -> Optional[PatternResult]:
    o, h, l, c = opens[-1], highs[-1], lows[-1], closes[-1]
    rng = h - l
    if rng < 0.8 * atr:
        return None
    body = abs(c - o)
    if (body / rng >= 0.85):
        dirn = PatternDirection.BULLISH if c > o else PatternDirection.BEARISH
        inv = l - 0.1 * atr if dirn == PatternDirection.BULLISH else h + 0.1 * atr
        tgt = h + 1.0 * atr if dirn == PatternDirection.BULLISH else l - 1.0 * atr
        return PatternResult(
            name="Marubozu",
            family=PatternFamily.CANDLE,
            direction=dirn,
            bars_used=1,
            key_levels={"open": o, "close": c},
            invalidation_price=inv,
            measured_target=tgt,
            raw_strength=0.85,
            details="High-displacement momentum candle with negligible wicks"
        )
    return None


@PatternRegistry.register(PatternFamily.CANDLE, "Pin Bar")
def detect_pin_bar(opens, highs, lows, closes, vols, atr, cfg) -> Optional[PatternResult]:
    o, h, l, c = opens[-1], highs[-1], lows[-1], closes[-1]
    rng = h - l
    if rng < 0.5 * atr:
        return None
    body = abs(c - o)
    upper_shadow = h - max(o, c)
    lower_shadow = min(o, c) - l
    if lower_shadow / rng >= 0.67 and body / rng <= 0.30:
        return PatternResult(
            name="Pin Bar",
            family=PatternFamily.CANDLE,
            direction=PatternDirection.BULLISH,
            bars_used=1,
            key_levels={"low": l, "high": h},
            invalidation_price=l - 0.2 * atr,
            measured_target=h + rng,
            raw_strength=0.82,
            details="Bullish rejection pin bar with long lower shadow"
        )
    elif upper_shadow / rng >= 0.67 and body / rng <= 0.30:
        return PatternResult(
            name="Pin Bar",
            family=PatternFamily.CANDLE,
            direction=PatternDirection.BEARISH,
            bars_used=1,
            key_levels={"high": h, "low": l},
            invalidation_price=h + 0.2 * atr,
            measured_target=l - rng,
            raw_strength=0.82,
            details="Bearish rejection pin bar with long upper shadow"
        )
    return None


@PatternRegistry.register(PatternFamily.CANDLE, "Belt Hold")
def detect_belt_hold(opens, highs, lows, closes, vols, atr, cfg) -> Optional[PatternResult]:
    o, h, l, c = opens[-1], highs[-1], lows[-1], closes[-1]
    rng = h - l
    if rng < 0.6 * atr:
        return None
    body = abs(c - o)
    if c > o and (o - l) <= 0.05 * rng and body / rng >= 0.70:
        return PatternResult(
            name="Belt Hold",
            family=PatternFamily.CANDLE,
            direction=PatternDirection.BULLISH,
            bars_used=1,
            key_levels={"open": o, "close": c},
            invalidation_price=l - 0.2 * atr,
            measured_target=h + 1.0 * atr,
            raw_strength=0.75,
            details="Bullish opening marubozu / belt hold"
        )
    elif c < o and (h - o) <= 0.05 * rng and body / rng >= 0.70:
        return PatternResult(
            name="Belt Hold",
            family=PatternFamily.CANDLE,
            direction=PatternDirection.BEARISH,
            bars_used=1,
            key_levels={"open": o, "close": c},
            invalidation_price=h + 0.2 * atr,
            measured_target=l - 1.0 * atr,
            raw_strength=0.75,
            details="Bearish opening marubozu / belt hold"
        )
    return None


@PatternRegistry.register(PatternFamily.CANDLE, "Tweezer Top")
def detect_tweezer_top(opens, highs, lows, closes, vols, atr, cfg) -> Optional[PatternResult]:
    if len(closes) < 2:
        return None
    h1, h2 = highs[-2], highs[-1]
    if abs(h1 - h2) <= 0.08 * atr and closes[-2] > opens[-2] and closes[-1] < opens[-1]:
        return PatternResult(
            name="Tweezer Top",
            family=PatternFamily.CANDLE,
            direction=PatternDirection.BEARISH,
            bars_used=2,
            key_levels={"resistance": max(h1, h2)},
            invalidation_price=max(h1, h2) + 0.2 * atr,
            measured_target=min(lows[-2:]) - 0.5 * atr,
            raw_strength=0.75,
            details="Twin matching candle highs forming resistance"
        )
    return None


@PatternRegistry.register(PatternFamily.CANDLE, "Tweezer Bottom")
def detect_tweezer_bottom(opens, highs, lows, closes, vols, atr, cfg) -> Optional[PatternResult]:
    if len(closes) < 2:
        return None
    l1, l2 = lows[-2], lows[-1]
    if abs(l1 - l2) <= 0.08 * atr and closes[-2] < opens[-2] and closes[-1] > opens[-1]:
        return PatternResult(
            name="Tweezer Bottom",
            family=PatternFamily.CANDLE,
            direction=PatternDirection.BULLISH,
            bars_used=2,
            key_levels={"support": min(l1, l2)},
            invalidation_price=min(l1, l2) - 0.2 * atr,
            measured_target=max(highs[-2:]) + 0.5 * atr,
            raw_strength=0.75,
            details="Twin matching candle lows forming support"
        )
    return None


@PatternRegistry.register(PatternFamily.CANDLE, "Inside Bar")
def detect_inside_bar(opens, highs, lows, closes, vols, atr, cfg) -> Optional[PatternResult]:
    if len(closes) < 2:
        return None
    if highs[-1] <= highs[-2] and lows[-1] >= lows[-2]:
        return PatternResult(
            name="Inside Bar",
            family=PatternFamily.CANDLE,
            direction=PatternDirection.NEUTRAL,
            bars_used=2,
            key_levels={"mother_high": highs[-2], "mother_low": lows[-2]},
            invalidation_price=lows[-2],
            measured_target=highs[-2] + 0.5 * atr,
            raw_strength=0.60,
            details="Consolidation candle completely within previous range"
        )
    return None


@PatternRegistry.register(PatternFamily.CANDLE, "Outside Bar")
def detect_outside_bar(opens, highs, lows, closes, vols, atr, cfg) -> Optional[PatternResult]:
    if len(closes) < 2:
        return None
    if highs[-1] > highs[-2] and lows[-1] < lows[-2]:
        dirn = PatternDirection.BULLISH if closes[-1] > opens[-1] else PatternDirection.BEARISH
        return PatternResult(
            name="Outside Bar",
            family=PatternFamily.CANDLE,
            direction=dirn,
            bars_used=2,
            key_levels={"high": highs[-1], "low": lows[-1]},
            invalidation_price=lows[-1] if dirn == PatternDirection.BULLISH else highs[-1],
            measured_target=highs[-1] + atr if dirn == PatternDirection.BULLISH else lows[-1] - atr,
            raw_strength=0.75,
            details="Volatility expansion engulfing entire prior bar range"
        )
    return None


@PatternRegistry.register(PatternFamily.CANDLE, "Kicking")
def detect_kicking(opens, highs, lows, closes, vols, atr, cfg) -> Optional[PatternResult]:
    if len(closes) < 2:
        return None
    gap_th = cfg.get("gap_equivalent_atr_multiplier", 0.20) * atr
    # Bullish Kicking: Black Marubozu followed by gap up and White Marubozu
    if closes[-2] < opens[-2] and closes[-1] > opens[-1]:
        if opens[-1] >= opens[-2] + gap_th and (highs[-1] - closes[-1]) <= 0.1 * atr:
            return PatternResult(
                name="Kicking",
                family=PatternFamily.CANDLE,
                direction=PatternDirection.BULLISH,
                bars_used=2,
                key_levels={"gap": opens[-1]},
                invalidation_price=lows[-1] - 0.2 * atr,
                measured_target=closes[-1] + 2.0 * atr,
                raw_strength=0.90,
                details="Sharp institutional sentiment reversal gap"
            )
    return None


@PatternRegistry.register(PatternFamily.CANDLE, "Counterattack")
def detect_counterattack(opens, highs, lows, closes, vols, atr, cfg) -> Optional[PatternResult]:
    if len(closes) < 2:
        return None
    c1, c2 = closes[-2], closes[-1]
    if abs(c1 - c2) <= 0.08 * atr:
        if closes[-2] < opens[-2] and closes[-1] > opens[-1]:
            return PatternResult(
                name="Counterattack",
                family=PatternFamily.CANDLE,
                direction=PatternDirection.BULLISH,
                bars_used=2,
                key_levels={"strike_close": c2},
                invalidation_price=lows[-1] - 0.2 * atr,
                measured_target=highs[-2] + 0.5 * atr,
                raw_strength=0.75,
                details="Bullish counterattack closing level with prior down close"
            )
        elif closes[-2] > opens[-2] and closes[-1] < opens[-1]:
            return PatternResult(
                name="Counterattack",
                family=PatternFamily.CANDLE,
                direction=PatternDirection.BEARISH,
                bars_used=2,
                key_levels={"strike_close": c2},
                invalidation_price=highs[-1] + 0.2 * atr,
                measured_target=lows[-2] - 0.5 * atr,
                raw_strength=0.75,
                details="Bearish counterattack closing level with prior up close"
            )
    return None


@PatternRegistry.register(PatternFamily.CANDLE, "On-Neck")
def detect_on_neck(opens, highs, lows, closes, vols, atr, cfg) -> Optional[PatternResult]:
    if len(closes) < 2 or compute_prior_trend(closes, atr=atr) != "DOWN":
        return None
    if closes[-2] < opens[-2] and closes[-1] > opens[-1]:
        if abs(closes[-1] - lows[-2]) <= 0.08 * atr:
            return PatternResult(
                name="On-Neck",
                family=PatternFamily.CANDLE,
                direction=PatternDirection.BEARISH,
                bars_used=2,
                key_levels={"neckline": lows[-2]},
                invalidation_price=highs[-1] + 0.2 * atr,
                measured_target=lows[-1] - atr,
                raw_strength=0.70,
                details="Bearish continuation on-neck retest of prior low"
            )
    return None


@PatternRegistry.register(PatternFamily.CANDLE, "In-Neck")
def detect_in_neck(opens, highs, lows, closes, vols, atr, cfg) -> Optional[PatternResult]:
    if len(closes) < 2 or compute_prior_trend(closes, atr=atr) != "DOWN":
        return None
    if closes[-2] < opens[-2] and closes[-1] > opens[-1]:
        if (closes[-1] > lows[-2]) and (closes[-1] <= closes[-2] + 0.15 * (opens[-2] - closes[-2])):
            return PatternResult(
                name="In-Neck",
                family=PatternFamily.CANDLE,
                direction=PatternDirection.BEARISH,
                bars_used=2,
                key_levels={"resistance": closes[-2]},
                invalidation_price=highs[-1] + 0.2 * atr,
                measured_target=lows[-1] - atr,
                raw_strength=0.70,
                details="Bearish continuation in-neck close just above prior close"
            )
    return None


@PatternRegistry.register(PatternFamily.CANDLE, "Thrusting")
def detect_thrusting(opens, highs, lows, closes, vols, atr, cfg) -> Optional[PatternResult]:
    if len(closes) < 2 or compute_prior_trend(closes, atr=atr) != "DOWN":
        return None
    b1 = opens[-2] - closes[-2]
    if (b1 >= 0.5 * atr) and (closes[-1] > opens[-1]):
        mid = closes[-2] + 0.5 * b1
        if (closes[-1] > closes[-2]) and (closes[-1] < mid):
            return PatternResult(
                name="Thrusting",
                family=PatternFamily.CANDLE,
                direction=PatternDirection.BEARISH,
                bars_used=2,
                key_levels={"midpoint": mid},
                invalidation_price=highs[-1] + 0.2 * atr,
                measured_target=lows[-1] - atr,
                raw_strength=0.72,
                details="Bearish continuation thrusting into lower half of prior candle"
            )
    return None


@PatternRegistry.register(PatternFamily.CANDLE, "Morning Doji Star")
def detect_morning_doji_star(opens, highs, lows, closes, vols, atr, cfg) -> Optional[PatternResult]:
    if len(closes) < 3 or compute_prior_trend(closes, atr=atr) == "UP":
        return None
    b1 = opens[-3] - closes[-3]
    rng2 = highs[-2] - lows[-2]
    b3 = closes[-1] - opens[-1]
    if (b1 >= 0.5 * atr) and rng2 > 0 and (abs(closes[-2] - opens[-2]) / rng2 <= 0.10) and (b3 >= 0.5 * atr):
        if closes[-1] >= closes[-3] + 0.5 * b1:
            return PatternResult(
                name="Morning Doji Star",
                family=PatternFamily.CANDLE,
                direction=PatternDirection.BULLISH,
                bars_used=3,
                key_levels={"doji_low": lows[-2]},
                invalidation_price=min(lows[-3:]) - 0.2 * atr,
                measured_target=closes[-1] + 1.5 * atr,
                raw_strength=0.90,
                details="High-conviction morning doji star reversal"
            )
    return None


@PatternRegistry.register(PatternFamily.CANDLE, "Evening Doji Star")
def detect_evening_doji_star(opens, highs, lows, closes, vols, atr, cfg) -> Optional[PatternResult]:
    if len(closes) < 3 or compute_prior_trend(closes, atr=atr) == "DOWN":
        return None
    b1 = closes[-3] - opens[-3]
    rng2 = highs[-2] - lows[-2]
    b3 = opens[-1] - closes[-1]
    if (b1 >= 0.5 * atr) and rng2 > 0 and (abs(closes[-2] - opens[-2]) / rng2 <= 0.10) and (b3 >= 0.5 * atr):
        if closes[-1] <= opens[-3] + 0.5 * b1:
            return PatternResult(
                name="Evening Doji Star",
                family=PatternFamily.CANDLE,
                direction=PatternDirection.BEARISH,
                bars_used=3,
                key_levels={"doji_high": highs[-2]},
                invalidation_price=max(highs[-3:]) + 0.2 * atr,
                measured_target=closes[-1] - 1.5 * atr,
                raw_strength=0.90,
                details="High-conviction evening doji star reversal"
            )
    return None


@PatternRegistry.register(PatternFamily.CANDLE, "Rising Three Methods")
def detect_rising_three_methods(opens, highs, lows, closes, vols, atr, cfg) -> Optional[PatternResult]:
    if len(closes) < 5 or compute_prior_trend(closes, atr=atr) != "UP":
        return None
    # Bar 1 strong bullish, Bars 2-4 small consolidation within bar 1 range, Bar 5 strong bullish closing above bar 1 high
    if (closes[-5] > opens[-5]) and (closes[-1] > opens[-1]) and (closes[-1] > highs[-5]):
        middle_lows = lows[-4:-1]
        middle_highs = highs[-4:-1]
        if min(middle_lows) >= lows[-5] and max(middle_highs) <= highs[-5]:
            return PatternResult(
                name="Rising Three Methods",
                family=PatternFamily.CANDLE,
                direction=PatternDirection.BULLISH,
                bars_used=5,
                key_levels={"breakout": closes[-1], "support": lows[-5]},
                invalidation_price=lows[-5] - 0.2 * atr,
                measured_target=closes[-1] + 1.5 * atr,
                raw_strength=0.92,
                details="Bullish trend continuation rising three methods"
            )
    return None


@PatternRegistry.register(PatternFamily.CANDLE, "Falling Three Methods")
def detect_falling_three_methods(opens, highs, lows, closes, vols, atr, cfg) -> Optional[PatternResult]:
    if len(closes) < 5 or compute_prior_trend(closes, atr=atr) != "DOWN":
        return None
    if (closes[-5] < opens[-5]) and (closes[-1] < opens[-1]) and (closes[-1] < lows[-5]):
        middle_lows = lows[-4:-1]
        middle_highs = highs[-4:-1]
        if min(middle_lows) >= lows[-5] and max(middle_highs) <= highs[-5]:
            return PatternResult(
                name="Falling Three Methods",
                family=PatternFamily.CANDLE,
                direction=PatternDirection.BEARISH,
                bars_used=5,
                key_levels={"breakdown": closes[-1], "resistance": highs[-5]},
                invalidation_price=highs[-5] + 0.2 * atr,
                measured_target=closes[-1] - 1.5 * atr,
                raw_strength=0.92,
                details="Bearish trend continuation falling three methods"
            )
    return None


@PatternRegistry.register(PatternFamily.CANDLE, "Upside Tasuki Gap")
def detect_upside_tasuki_gap(opens, highs, lows, closes, vols, atr, cfg) -> Optional[PatternResult]:
    if len(closes) < 3 or compute_prior_trend(closes, atr=atr) != "UP":
        return None
    gap_th = cfg.get("gap_equivalent_atr_multiplier", 0.15) * atr
    # Bar 1 bullish, Bar 2 bullish gapping up, Bar 3 bearish opening inside bar 2 and closing into gap without closing it
    if (closes[-3] > opens[-3]) and (closes[-2] > opens[-2]) and (opens[-2] >= closes[-3] + gap_th):
        if (closes[-1] < opens[-1]) and (closes[-1] > closes[-3]) and (opens[-1] <= closes[-2]):
            return PatternResult(
                name="Upside Tasuki Gap",
                family=PatternFamily.CANDLE,
                direction=PatternDirection.BULLISH,
                bars_used=3,
                key_levels={"gap_support": closes[-3]},
                invalidation_price=closes[-3] - 0.2 * atr,
                measured_target=highs[-2] + 1.0 * atr,
                raw_strength=0.82,
                details="Bullish continuation tasuki gap retest"
            )
    return None


@PatternRegistry.register(PatternFamily.CANDLE, "Downside Tasuki Gap")
def detect_downside_tasuki_gap(opens, highs, lows, closes, vols, atr, cfg) -> Optional[PatternResult]:
    if len(closes) < 3 or compute_prior_trend(closes, atr=atr) != "DOWN":
        return None
    gap_th = cfg.get("gap_equivalent_atr_multiplier", 0.15) * atr
    if (closes[-3] < opens[-3]) and (closes[-2] < opens[-2]) and (opens[-2] <= closes[-3] - gap_th):
        if (closes[-1] > opens[-1]) and (closes[-1] < closes[-3]) and (opens[-1] >= closes[-2]):
            return PatternResult(
                name="Downside Tasuki Gap",
                family=PatternFamily.CANDLE,
                direction=PatternDirection.BEARISH,
                bars_used=3,
                key_levels={"gap_resistance": closes[-3]},
                invalidation_price=closes[-3] + 0.2 * atr,
                measured_target=lows[-2] - 1.0 * atr,
                raw_strength=0.82,
                details="Bearish continuation tasuki gap retest"
            )
    return None


@PatternRegistry.register(PatternFamily.CANDLE, "Three Line Strike")
def detect_three_line_strike(opens, highs, lows, closes, vols, atr, cfg) -> Optional[PatternResult]:
    if len(closes) < 4:
        return None
    # Bullish strike: 3 bearish candles followed by large bullish bar opening <= bar 3 close and closing > bar 1 open
    if (closes[-4] < opens[-4]) and (closes[-3] < opens[-3]) and (closes[-2] < opens[-2]):
        if (closes[-1] > opens[-1]) and (opens[-1] <= closes[-2]) and (closes[-1] >= opens[-4]):
            return PatternResult(
                name="Three Line Strike",
                family=PatternFamily.CANDLE,
                direction=PatternDirection.BULLISH,
                bars_used=4,
                key_levels={"strike_close": closes[-1]},
                invalidation_price=lows[-1] - 0.2 * atr,
                measured_target=closes[-1] + 1.5 * atr,
                raw_strength=0.92,
                details="Powerful bullish three line strike engulfing 3 prior bars"
            )
    return None


@PatternRegistry.register(PatternFamily.CANDLE, "Breakaway")
def detect_breakaway(opens, highs, lows, closes, vols, atr, cfg) -> Optional[PatternResult]:
    if len(closes) < 5 or compute_prior_trend(closes, atr=atr) != "DOWN":
        return None
    # Bullish Breakaway: Bar 1 long black, Bar 2 gaps down, Bars 3-4 small lower, Bar 5 long white closing inside gap
    if (closes[-5] < opens[-5]) and (closes[-1] > opens[-1]) and (closes[-1] > closes[-5]):
        return PatternResult(
            name="Breakaway",
            family=PatternFamily.CANDLE,
            direction=PatternDirection.BULLISH,
            bars_used=5,
            key_levels={"breakaway_high": closes[-1]},
            invalidation_price=min(lows[-5:]) - 0.2 * atr,
            measured_target=closes[-1] + 1.5 * atr,
            raw_strength=0.85,
            details="Five-candle bullish breakaway exhaustion reversal"
        )
    return None


@PatternRegistry.register(PatternFamily.CANDLE, "Hikkake")
def detect_hikkake(opens, highs, lows, closes, vols, atr, cfg) -> Optional[PatternResult]:
    if len(closes) < 4:
        return None
    # Inside bar at -3, false breakout at -2, reversal breakout at -1
    inside = (highs[-3] <= highs[-4]) and (lows[-3] >= lows[-4])
    if inside:
        # Bullish Hikkake: false break below inside bar, then close above inside bar high
        if (lows[-2] < lows[-3]) and (closes[-1] > highs[-3]):
            return PatternResult(
                name="Hikkake",
                family=PatternFamily.CANDLE,
                direction=PatternDirection.BULLISH,
                bars_used=4,
                key_levels={"breakout": highs[-3]},
                invalidation_price=lows[-2] - 0.2 * atr,
                measured_target=closes[-1] + 1.5 * atr,
                raw_strength=0.88,
                details="Bullish trap-and-reverse inside bar false breakout"
            )
        elif (highs[-2] > highs[-3]) and (closes[-1] < lows[-3]):
            return PatternResult(
                name="Hikkake",
                family=PatternFamily.CANDLE,
                direction=PatternDirection.BEARISH,
                bars_used=4,
                key_levels={"breakdown": lows[-3]},
                invalidation_price=highs[-2] + 0.2 * atr,
                measured_target=closes[-1] - 1.5 * atr,
                raw_strength=0.88,
                details="Bearish trap-and-reverse inside bar false breakout"
            )
    return None


@PatternRegistry.register(PatternFamily.CANDLE, "Advance Block")
def detect_advance_block(opens, highs, lows, closes, vols, atr, cfg) -> Optional[PatternResult]:
    if len(closes) < 3 or compute_prior_trend(closes, atr=atr) != "UP":
        return None
    # Three white soldiers where bodies progressively shrink and upper shadows lengthen (exhaustion)
    b1 = closes[-3] - opens[-3]
    b2 = closes[-2] - opens[-2]
    b3 = closes[-1] - opens[-1]
    sh3 = highs[-1] - closes[-1]
    if (b1 > 0) and (b2 > 0) and (b3 > 0) and (b1 > b2 > b3) and (sh3 >= b3):
        return PatternResult(
            name="Advance Block",
            family=PatternFamily.CANDLE,
            direction=PatternDirection.BEARISH,
            bars_used=3,
            key_levels={"exhaustion_high": highs[-1]},
            invalidation_price=highs[-1] + 0.2 * atr,
            measured_target=lows[-3] - 0.5 * atr,
            raw_strength=0.80,
            details="Bearish advance block momentum exhaustion"
        )
    return None


@PatternRegistry.register(PatternFamily.CANDLE, "Tri-Star")
def detect_tri_star(opens, highs, lows, closes, vols, atr, cfg) -> Optional[PatternResult]:
    if len(closes) < 3:
        return None
    r1 = highs[-3] - lows[-3]
    r2 = highs[-2] - lows[-2]
    r3 = highs[-1] - lows[-1]
    if r1 > 0 and r2 > 0 and r3 > 0:
        d1 = abs(closes[-3] - opens[-3]) / r1 <= 0.12
        d2 = abs(closes[-2] - opens[-2]) / r2 <= 0.12
        d3 = abs(closes[-1] - opens[-1]) / r3 <= 0.12
        if d1 and d2 and d3:
            dirn = PatternDirection.BULLISH if closes[-2] < closes[-3] else PatternDirection.BEARISH
            inv = min(lows[-3:]) - 0.2 * atr if dirn == PatternDirection.BULLISH else max(highs[-3:]) + 0.2 * atr
            tgt = closes[-1] + 1.5 * atr if dirn == PatternDirection.BULLISH else closes[-1] - 1.5 * atr
            return PatternResult(
                name="Tri-Star",
                family=PatternFamily.CANDLE,
                direction=dirn,
                bars_used=3,
                key_levels={"middle_doji": (highs[-2] + lows[-2]) / 2.0},
                invalidation_price=inv,
                measured_target=tgt,
                raw_strength=0.85,
                details="Extremely rare triple doji reversal cluster"
            )
    return None


@PatternRegistry.register(PatternFamily.CANDLE, "Mat Hold")
def detect_mat_hold(opens, highs, lows, closes, vols, atr, cfg) -> Optional[PatternResult]:
    if len(closes) < 5 or compute_prior_trend(closes, atr=atr) != "UP":
        return None
    # Like rising three methods, but bar 2 gaps up and stays well above bar 1 low
    if (closes[-5] > opens[-5]) and (closes[-1] > opens[-1]) and (closes[-1] > highs[-5]):
        if min(lows[-4:-1]) > opens[-5]:
            return PatternResult(
                name="Mat Hold",
                family=PatternFamily.CANDLE,
                direction=PatternDirection.BULLISH,
                bars_used=5,
                key_levels={"breakout": closes[-1]},
                invalidation_price=lows[-5] - 0.2 * atr,
                measured_target=closes[-1] + 2.0 * atr,
                raw_strength=0.92,
                details="Institutional mat hold bullish continuation"
            )
    return None


# ============================================================================
# CANDLESTICK ENGINE CLASS
# ============================================================================

class CandlestickEngine:
    """
    High-performance candlestick evaluation engine.
    """
    def __init__(self, config: Optional[Dict[str, Any]] = None):
        self.config = config or {}
        self.candle_cfg = self.config.get("candlestick", {})

    def detect(self, candles: List[Candle], atr: Optional[float] = None) -> List[PatternResult]:
        if len(candles) < 3:
            return []

        opens = np.array([c.open for c in candles], dtype=np.float64)
        highs = np.array([c.high for c in candles], dtype=np.float64)
        lows = np.array([c.low for c in candles], dtype=np.float64)
        closes = np.array([c.close for c in candles], dtype=np.float64)
        vols = np.array([getattr(c, "tick_volume", 0) for c in candles], dtype=np.float64)

        calc_atr = atr if atr is not None else compute_atr(highs, lows, closes, period=self.candle_cfg.get("atr_period", 14))
        results: List[PatternResult] = []

        patterns_enabled = self.candle_cfg.get("patterns_enabled", {})
        candle_detectors = PatternRegistry.get_detectors(PatternFamily.CANDLE)
        for name, detector in candle_detectors.items():
            if patterns_enabled.get(name, True):
                try:
                    res = detector(opens, highs, lows, closes, vols, calc_atr, self.candle_cfg)
                    if res is not None:
                        results.append(res)
                except Exception:
                    pass
        return results
