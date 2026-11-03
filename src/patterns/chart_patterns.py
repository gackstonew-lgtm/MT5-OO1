"""
Institutional Chart Pattern & Harmonic Detection Engine.
Implements swing-based, closed-bar pattern detectors with ATR-normalized tolerances,
measured-move target projections, and strict candle-close breakout confirmation.
"""

from __future__ import annotations
from typing import Dict, List, Optional, Any, Tuple
from datetime import datetime, timezone
import numpy as np

from src.core.contracts import Candle
from src.patterns.contracts import (
    PatternResult, PatternFamily, PatternDirection, PatternRegistry
)
from src.patterns.candlesticks import compute_atr


def extract_swings(highs: np.ndarray, lows: np.ndarray, lookback: int = 3) -> Tuple[List[Tuple[int, float]], List[Tuple[int, float]]]:
    """
    Identifies swing highs and swing lows on closed bars.
    Returns: (swing_highs, swing_lows) where each element is (bar_index, price).
    """
    n = len(highs)
    k = lookback
    swing_highs: List[Tuple[int, float]] = []
    swing_lows: List[Tuple[int, float]] = []

    if n < 2 * k + 1:
        return swing_highs, swing_lows

    for i in range(k, n - 1):  # Do not include the very last bar (it's the breakout/confirmation bar)
        if all(highs[i] > highs[j] for j in range(max(0, i - k), i)) and \
           all(highs[i] >= highs[j] for j in range(i + 1, min(n, i + k + 1))):
            swing_highs.append((i, float(highs[i])))

        if all(lows[i] < lows[j] for j in range(max(0, i - k), i)) and \
           all(lows[i] <= lows[j] for j in range(i + 1, min(n, i + k + 1))):
            swing_lows.append((i, float(lows[i])))

    return swing_highs, swing_lows


# ============================================================================
# 1. HEAD AND SHOULDERS & INVERSE HEAD AND SHOULDERS
# ============================================================================

@PatternRegistry.register(PatternFamily.CHART, "Head and Shoulders")
def detect_head_and_shoulders(opens, highs, lows, closes, vols, atr, cfg) -> Optional[PatternResult]:
    sh, sl = extract_swings(highs, lows, lookback=2)
    if len(sh) < 3 or len(sl) < 2:
        return None
    
    # Take the last 3 swing highs and 2 intermediate swing lows
    sh3 = sh[-3:]
    (i1, h1), (i2, h2), (i3, h3) = sh3
    
    # Head must be strictly higher than both shoulders
    if not (h2 > h1 and h2 > h3):
        return None
    
    # Symmetry check: shoulders should be within tolerance
    sym_tol = cfg.get("symmetry_tolerance_pct", 0.15)
    if abs(h1 - h3) / max(h1, h3) > sym_tol:
        return None
    
    # Intermediate lows between shoulders and head
    troughs = [p for i, p in sl if i1 < i < i3]
    if len(troughs) < 2:
        return None
    neckline = float(np.mean(troughs[-2:]))
    
    # Confirmation: closed bar broke below neckline
    curr_close = closes[-1]
    if curr_close < neckline and closes[-2] >= neckline:
        head_height = h2 - neckline
        measured_target = neckline - head_height
        invalidation = h3 + 0.2 * atr
        bars_used = len(closes) - i1
        
        return PatternResult(
            name="Head and Shoulders",
            family=PatternFamily.CHART,
            direction=PatternDirection.BEARISH,
            bars_used=bars_used,
            key_levels={"left_shoulder": h1, "head": h2, "right_shoulder": h3, "neckline": neckline},
            invalidation_price=invalidation,
            measured_target=measured_target,
            raw_strength=0.90,
            confirmed=True,
            details=f"Head & Shoulders confirmed: close {curr_close:.5f} < neckline {neckline:.5f}"
        )
    return None


@PatternRegistry.register(PatternFamily.CHART, "Inverse Head and Shoulders")
def detect_inverse_head_and_shoulders(opens, highs, lows, closes, vols, atr, cfg) -> Optional[PatternResult]:
    sh, sl = extract_swings(highs, lows, lookback=2)
    if len(sl) < 3 or len(sh) < 2:
        return None
    
    sl3 = sl[-3:]
    (i1, l1), (i2, l2), (i3, l3) = sl3
    
    if not (l2 < l1 and l2 < l3):
        return None
    
    sym_tol = cfg.get("symmetry_tolerance_pct", 0.15)
    if abs(l1 - l3) / max(l1, l3) > sym_tol:
        return None
    
    peaks = [p for i, p in sh if i1 < i < i3]
    if len(peaks) < 2:
        return None
    neckline = float(np.mean(peaks[-2:]))
    
    curr_close = closes[-1]
    if curr_close > neckline and closes[-2] <= neckline:
        head_depth = neckline - l2
        measured_target = neckline + head_depth
        invalidation = l3 - 0.2 * atr
        bars_used = len(closes) - i1
        
        return PatternResult(
            name="Inverse Head and Shoulders",
            family=PatternFamily.CHART,
            direction=PatternDirection.BULLISH,
            bars_used=bars_used,
            key_levels={"left_shoulder": l1, "head": l2, "right_shoulder": l3, "neckline": neckline},
            invalidation_price=invalidation,
            measured_target=measured_target,
            raw_strength=0.90,
            confirmed=True,
            details=f"Inverse Head & Shoulders confirmed: close {curr_close:.5f} > neckline {neckline:.5f}"
        )
    return None


# ============================================================================
# 2. DOUBLE TOP & DOUBLE BOTTOM
# ============================================================================

@PatternRegistry.register(PatternFamily.CHART, "Double Top")
def detect_double_top(opens, highs, lows, closes, vols, atr, cfg) -> Optional[PatternResult]:
    sh, sl = extract_swings(highs, lows, lookback=2)
    if len(sh) < 2 or len(sl) < 1:
        return None
    
    (i1, h1), (i2, h2) = sh[-2], sh[-1]
    touch_tol = cfg.get("touch_tolerance_pct", 0.05)
    if abs(h1 - h2) / max(h1, h2) > touch_tol and abs(h1 - h2) > 0.8 * atr:
        return None
    
    troughs = [p for i, p in sl if i1 < i < i2]
    if not troughs:
        return None
    neckline = min(troughs)
    
    curr_close = closes[-1]
    if curr_close < neckline and closes[-2] >= neckline:
        height = max(h1, h2) - neckline
        measured_target = neckline - height
        invalidation = max(h1, h2) + 0.2 * atr
        bars_used = len(closes) - i1
        
        return PatternResult(
            name="Double Top",
            family=PatternFamily.CHART,
            direction=PatternDirection.BEARISH,
            bars_used=bars_used,
            key_levels={"top_1": h1, "top_2": h2, "neckline": neckline},
            invalidation_price=invalidation,
            measured_target=measured_target,
            raw_strength=0.85,
            confirmed=True,
            details=f"Double Top confirmed: close {curr_close:.5f} < neckline {neckline:.5f}"
        )
    return None


@PatternRegistry.register(PatternFamily.CHART, "Double Bottom")
def detect_double_bottom(opens, highs, lows, closes, vols, atr, cfg) -> Optional[PatternResult]:
    sh, sl = extract_swings(highs, lows, lookback=2)
    if len(sl) < 2 or len(sh) < 1:
        return None
    
    (i1, l1), (i2, l2) = sl[-2], sl[-1]
    touch_tol = cfg.get("touch_tolerance_pct", 0.05)
    if abs(l1 - l2) / max(l1, l2) > touch_tol and abs(l1 - l2) > 0.8 * atr:
        return None
    
    peaks = [p for i, p in sh if i1 < i < i2]
    if not peaks:
        return None
    neckline = max(peaks)
    
    curr_close = closes[-1]
    if curr_close > neckline and closes[-2] <= neckline:
        depth = neckline - min(l1, l2)
        measured_target = neckline + depth
        invalidation = min(l1, l2) - 0.2 * atr
        bars_used = len(closes) - i1
        
        return PatternResult(
            name="Double Bottom",
            family=PatternFamily.CHART,
            direction=PatternDirection.BULLISH,
            bars_used=bars_used,
            key_levels={"bottom_1": l1, "bottom_2": l2, "neckline": neckline},
            invalidation_price=invalidation,
            measured_target=measured_target,
            raw_strength=0.85,
            confirmed=True,
            details=f"Double Bottom confirmed: close {curr_close:.5f} > neckline {neckline:.5f}"
        )
    return None


# ============================================================================
# 3. TRIPLE TOP & TRIPLE BOTTOM
# ============================================================================

@PatternRegistry.register(PatternFamily.CHART, "Triple Top")
def detect_triple_top(opens, highs, lows, closes, vols, atr, cfg) -> Optional[PatternResult]:
    sh, sl = extract_swings(highs, lows, lookback=2)
    if len(sh) < 3 or len(sl) < 2:
        return None
    
    (i1, h1), (i2, h2), (i3, h3) = sh[-3:]
    touch_tol = cfg.get("touch_tolerance_pct", 0.05)
    max_h = max(h1, h2, h3)
    min_h = min(h1, h2, h3)
    if (max_h - min_h) / max_h > touch_tol:
        return None
    
    troughs = [p for i, p in sl if i1 < i < i3]
    if len(troughs) < 2:
        return None
    neckline = min(troughs)
    
    curr_close = closes[-1]
    if curr_close < neckline and closes[-2] >= neckline:
        height = max_h - neckline
        measured_target = neckline - height
        invalidation = max_h + 0.2 * atr
        bars_used = len(closes) - i1
        
        return PatternResult(
            name="Triple Top",
            family=PatternFamily.CHART,
            direction=PatternDirection.BEARISH,
            bars_used=bars_used,
            key_levels={"top_1": h1, "top_2": h2, "top_3": h3, "neckline": neckline},
            invalidation_price=invalidation,
            measured_target=measured_target,
            raw_strength=0.88,
            confirmed=True,
            details=f"Triple Top confirmed: close {curr_close:.5f} < neckline {neckline:.5f}"
        )
    return None


@PatternRegistry.register(PatternFamily.CHART, "Triple Bottom")
def detect_triple_bottom(opens, highs, lows, closes, vols, atr, cfg) -> Optional[PatternResult]:
    sh, sl = extract_swings(highs, lows, lookback=2)
    if len(sl) < 3 or len(sh) < 2:
        return None
    
    (i1, l1), (i2, l2), (i3, l3) = sl[-3:]
    touch_tol = cfg.get("touch_tolerance_pct", 0.05)
    max_l = max(l1, l2, l3)
    min_l = min(l1, l2, l3)
    if (max_l - min_l) / max_l > touch_tol:
        return None
    
    peaks = [p for i, p in sh if i1 < i < i3]
    if len(peaks) < 2:
        return None
    neckline = max(peaks)
    
    curr_close = closes[-1]
    if curr_close > neckline and closes[-2] <= neckline:
        depth = neckline - min_l
        measured_target = neckline + depth
        invalidation = min_l - 0.2 * atr
        bars_used = len(closes) - i1
        
        return PatternResult(
            name="Triple Bottom",
            family=PatternFamily.CHART,
            direction=PatternDirection.BULLISH,
            bars_used=bars_used,
            key_levels={"bottom_1": l1, "bottom_2": l2, "bottom_3": l3, "neckline": neckline},
            invalidation_price=invalidation,
            measured_target=measured_target,
            raw_strength=0.88,
            confirmed=True,
            details=f"Triple Bottom confirmed: close {curr_close:.5f} > neckline {neckline:.5f}"
        )
    return None


# ============================================================================
# 4. TRIANGLES (ASCENDING, DESCENDING, SYMMETRICAL)
# ============================================================================

@PatternRegistry.register(PatternFamily.CHART, "Ascending Triangle")
def detect_ascending_triangle(opens, highs, lows, closes, vols, atr, cfg) -> Optional[PatternResult]:
    sh, sl = extract_swings(highs, lows, lookback=2)
    if len(sh) < 2 or len(sl) < 2:
        return None
    
    (i1, h1), (i2, h2) = sh[-2], sh[-1]
    (j1, l1), (j2, l2) = sl[-2], sl[-1]
    
    # Flat horizontal resistance
    if abs(h1 - h2) / max(h1, h2) > 0.05:
        return None
    # Higher lows
    if l2 <= l1:
        return None
    
    resistance = max(h1, h2)
    curr_close = closes[-1]
    if curr_close > resistance and closes[-2] <= resistance:
        height = resistance - l1
        measured_target = resistance + height
        invalidation = l2 - 0.2 * atr
        bars_used = len(closes) - min(i1, j1)
        
        return PatternResult(
            name="Ascending Triangle",
            family=PatternFamily.CHART,
            direction=PatternDirection.BULLISH,
            bars_used=bars_used,
            key_levels={"resistance": resistance, "last_low": l2},
            invalidation_price=invalidation,
            measured_target=measured_target,
            raw_strength=0.86,
            confirmed=True,
            details=f"Ascending Triangle breakout: close {curr_close:.5f} > resistance {resistance:.5f}"
        )
    return None


@PatternRegistry.register(PatternFamily.CHART, "Descending Triangle")
def detect_descending_triangle(opens, highs, lows, closes, vols, atr, cfg) -> Optional[PatternResult]:
    sh, sl = extract_swings(highs, lows, lookback=2)
    if len(sh) < 2 or len(sl) < 2:
        return None
    
    (i1, h1), (i2, h2) = sh[-2], sh[-1]
    (j1, l1), (j2, l2) = sl[-2], sl[-1]
    
    # Flat horizontal support
    if abs(l1 - l2) / max(l1, l2) > 0.05:
        return None
    # Lower highs
    if h2 >= h1:
        return None
    
    support = min(l1, l2)
    curr_close = closes[-1]
    if curr_close < support and closes[-2] >= support:
        height = h1 - support
        measured_target = support - height
        invalidation = h2 + 0.2 * atr
        bars_used = len(closes) - min(i1, j1)
        
        return PatternResult(
            name="Descending Triangle",
            family=PatternFamily.CHART,
            direction=PatternDirection.BEARISH,
            bars_used=bars_used,
            key_levels={"support": support, "last_high": h2},
            invalidation_price=invalidation,
            measured_target=measured_target,
            raw_strength=0.86,
            confirmed=True,
            details=f"Descending Triangle breakdown: close {curr_close:.5f} < support {support:.5f}"
        )
    return None


@PatternRegistry.register(PatternFamily.CHART, "Symmetrical Triangle")
def detect_symmetrical_triangle(opens, highs, lows, closes, vols, atr, cfg) -> Optional[PatternResult]:
    sh, sl = extract_swings(highs, lows, lookback=2)
    if len(sh) < 2 or len(sl) < 2:
        return None
    
    (i1, h1), (i2, h2) = sh[-2], sh[-1]
    (j1, l1), (j2, l2) = sl[-2], sl[-1]
    
    # Lower highs AND higher lows (converging coil)
    if not (h2 < h1 and l2 > l1):
        return None
    
    curr_close = closes[-1]
    base_height = h1 - l1
    bars_used = len(closes) - min(i1, j1)
    
    # Breakout upward
    if curr_close > h2 and closes[-2] <= h2:
        return PatternResult(
            name="Symmetrical Triangle",
            family=PatternFamily.CHART,
            direction=PatternDirection.BULLISH,
            bars_used=bars_used,
            key_levels={"upper_boundary": h2, "lower_boundary": l2},
            invalidation_price=l2 - 0.2 * atr,
            measured_target=curr_close + base_height,
            raw_strength=0.84,
            confirmed=True,
            details=f"Symmetrical Triangle bullish breakout above {h2:.5f}"
        )
    # Breakdown downward
    elif curr_close < l2 and closes[-2] >= l2:
        return PatternResult(
            name="Symmetrical Triangle",
            family=PatternFamily.CHART,
            direction=PatternDirection.BEARISH,
            bars_used=bars_used,
            key_levels={"upper_boundary": h2, "lower_boundary": l2},
            invalidation_price=h2 + 0.2 * atr,
            measured_target=curr_close - base_height,
            raw_strength=0.84,
            confirmed=True,
            details=f"Symmetrical Triangle bearish breakdown below {l2:.5f}"
        )
    return None


# ============================================================================
# 5. WEDGES (RISING & FALLING)
# ============================================================================

@PatternRegistry.register(PatternFamily.CHART, "Rising Wedge")
def detect_rising_wedge(opens, highs, lows, closes, vols, atr, cfg) -> Optional[PatternResult]:
    sh, sl = extract_swings(highs, lows, lookback=2)
    if len(sh) < 2 or len(sl) < 2:
        return None
    
    (i1, h1), (i2, h2) = sh[-2], sh[-1]
    (j1, l1), (j2, l2) = sl[-2], sl[-1]
    
    # Both lines sloping up: higher highs and higher lows
    if not (h2 > h1 and l2 > l1):
        return None
    
    # Lows rising faster than highs (converging upward)
    slope_highs = (h2 - h1) / max(1, (i2 - i1))
    slope_lows = (l2 - l1) / max(1, (j2 - j1))
    if slope_lows <= slope_highs:
        return None
    
    curr_close = closes[-1]
    # Bearish reversal breakdown below lower swing
    if curr_close < l2 and closes[-2] >= l2:
        invalidation = max(h1, h2) + 0.2 * atr
        measured_target = min(l1, l2) - 0.5 * (h2 - l2)
        bars_used = len(closes) - min(i1, j1)
        
        return PatternResult(
            name="Rising Wedge",
            family=PatternFamily.CHART,
            direction=PatternDirection.BEARISH,
            bars_used=bars_used,
            key_levels={"lower_trendline": l2, "peak": h2},
            invalidation_price=invalidation,
            measured_target=measured_target,
            raw_strength=0.85,
            confirmed=True,
            details=f"Rising Wedge breakdown confirmed below {l2:.5f}"
        )
    return None


@PatternRegistry.register(PatternFamily.CHART, "Falling Wedge")
def detect_falling_wedge(opens, highs, lows, closes, vols, atr, cfg) -> Optional[PatternResult]:
    sh, sl = extract_swings(highs, lows, lookback=2)
    if len(sh) < 2 or len(sl) < 2:
        return None
    
    (i1, h1), (i2, h2) = sh[-2], sh[-1]
    (j1, l1), (j2, l2) = sl[-2], sl[-1]
    
    # Both sloping down: lower highs and lower lows
    if not (h2 < h1 and l2 < l1):
        return None
    
    # Highs falling faster than lows (converging downward)
    slope_highs = abs(h2 - h1) / max(1, (i2 - i1))
    slope_lows = abs(l2 - l1) / max(1, (j2 - j1))
    if slope_highs <= slope_lows:
        return None
    
    curr_close = closes[-1]
    # Bullish reversal breakout above higher swing
    if curr_close > h2 and closes[-2] <= h2:
        invalidation = min(l1, l2) - 0.2 * atr
        measured_target = max(h1, h2) + 0.5 * (h2 - l2)
        bars_used = len(closes) - min(i1, j1)
        
        return PatternResult(
            name="Falling Wedge",
            family=PatternFamily.CHART,
            direction=PatternDirection.BULLISH,
            bars_used=bars_used,
            key_levels={"upper_trendline": h2, "trough": l2},
            invalidation_price=invalidation,
            measured_target=measured_target,
            raw_strength=0.85,
            confirmed=True,
            details=f"Falling Wedge breakout confirmed above {h2:.5f}"
        )
    return None


# ============================================================================
# 6. FLAGS & PENNANTS (BULL & BEAR)
# ============================================================================

@PatternRegistry.register(PatternFamily.CHART, "Bull Flag")
def detect_bull_flag(opens, highs, lows, closes, vols, atr, cfg) -> Optional[PatternResult]:
    if len(closes) < 12:
        return None
    
    # Flagpole: strong upward move over previous 4-8 bars
    pole_bars = 6
    flag_bars = 5
    pole_start = closes[- (pole_bars + flag_bars)]
    pole_peak = max(highs[- (flag_bars + 3): -2])
    pole_height = pole_peak - pole_start
    
    if pole_height < 2.0 * atr:
        return None
    
    # Flag: tight downward/sideways channel over last 3-6 bars
    flag_highs = highs[-flag_bars:-1]
    flag_lows = lows[-flag_bars:-1]
    flag_range = max(flag_highs) - min(flag_lows)
    
    # Consolidation must be shallow (< 50% of pole height)
    if flag_range > 0.55 * pole_height:
        return None
    
    flag_top = max(flag_highs)
    curr_close = closes[-1]
    
    if curr_close > flag_top and closes[-2] <= flag_top:
        invalidation = min(flag_lows) - 0.2 * atr
        measured_target = curr_close + pole_height
        
        return PatternResult(
            name="Bull Flag",
            family=PatternFamily.CHART,
            direction=PatternDirection.BULLISH,
            bars_used=pole_bars + flag_bars,
            key_levels={"flagpole_bottom": pole_start, "flag_top": flag_top, "flag_bottom": min(flag_lows)},
            invalidation_price=invalidation,
            measured_target=measured_target,
            raw_strength=0.88,
            confirmed=True,
            details=f"Bull Flag breakout confirmed above {flag_top:.5f}"
        )
    return None


@PatternRegistry.register(PatternFamily.CHART, "Bear Flag")
def detect_bear_flag(opens, highs, lows, closes, vols, atr, cfg) -> Optional[PatternResult]:
    if len(closes) < 12:
        return None
    
    pole_bars = 6
    flag_bars = 5
    pole_start = closes[- (pole_bars + flag_bars)]
    pole_trough = min(lows[- (flag_bars + 3): -2])
    pole_height = pole_start - pole_trough
    
    if pole_height < 2.0 * atr:
        return None
    
    flag_highs = highs[-flag_bars:-1]
    flag_lows = lows[-flag_bars:-1]
    flag_range = max(flag_highs) - min(flag_lows)
    
    if flag_range > 0.55 * pole_height:
        return None
    
    flag_bottom = min(flag_lows)
    curr_close = closes[-1]
    
    if curr_close < flag_bottom and closes[-2] >= flag_bottom:
        invalidation = max(flag_highs) + 0.2 * atr
        measured_target = curr_close - pole_height
        
        return PatternResult(
            name="Bear Flag",
            family=PatternFamily.CHART,
            direction=PatternDirection.BEARISH,
            bars_used=pole_bars + flag_bars,
            key_levels={"flagpole_top": pole_start, "flag_bottom": flag_bottom, "flag_top": max(flag_highs)},
            invalidation_price=invalidation,
            measured_target=measured_target,
            raw_strength=0.88,
            confirmed=True,
            details=f"Bear Flag breakdown confirmed below {flag_bottom:.5f}"
        )
    return None


@PatternRegistry.register(PatternFamily.CHART, "Bull Pennant")
def detect_bull_pennant(opens, highs, lows, closes, vols, atr, cfg) -> Optional[PatternResult]:
    if len(closes) < 12:
        return None
    
    pole_bars = 6
    flag_bars = 5
    pole_start = closes[- (pole_bars + flag_bars)]
    pole_peak = max(highs[- (flag_bars + 3): -2])
    pole_height = pole_peak - pole_start
    if pole_height < 2.0 * atr:
        return None
    
    # Pennant consolidation must converge
    p_highs = highs[-flag_bars:-1]
    p_lows = lows[-flag_bars:-1]
    if len(p_highs) >= 4 and p_highs[-1] <= p_highs[0] and p_lows[-1] >= p_lows[0]:
        curr_close = closes[-1]
        p_top = max(p_highs)
        if curr_close > p_top:
            return PatternResult(
                name="Bull Pennant",
                family=PatternFamily.CHART,
                direction=PatternDirection.BULLISH,
                bars_used=pole_bars + flag_bars,
                key_levels={"pennant_top": p_top, "pennant_low": min(p_lows)},
                invalidation_price=min(p_lows) - 0.2 * atr,
                measured_target=curr_close + pole_height,
                raw_strength=0.87,
                confirmed=True,
                details=f"Bull Pennant breakout confirmed above {p_top:.5f}"
            )
    return None


@PatternRegistry.register(PatternFamily.CHART, "Bear Pennant")
def detect_bear_pennant(opens, highs, lows, closes, vols, atr, cfg) -> Optional[PatternResult]:
    if len(closes) < 12:
        return None
    
    pole_bars = 6
    flag_bars = 5
    pole_start = closes[- (pole_bars + flag_bars)]
    pole_trough = min(lows[- (flag_bars + 3): -2])
    pole_height = pole_start - pole_trough
    if pole_height < 2.0 * atr:
        return None
    
    p_highs = highs[-flag_bars:-1]
    p_lows = lows[-flag_bars:-1]
    if len(p_highs) >= 4 and p_highs[-1] <= p_highs[0] and p_lows[-1] >= p_lows[0]:
        curr_close = closes[-1]
        p_bot = min(p_lows)
        if curr_close < p_bot:
            return PatternResult(
                name="Bear Pennant",
                family=PatternFamily.CHART,
                direction=PatternDirection.BEARISH,
                bars_used=pole_bars + flag_bars,
                key_levels={"pennant_bottom": p_bot, "pennant_high": max(p_highs)},
                invalidation_price=max(p_highs) + 0.2 * atr,
                measured_target=curr_close - pole_height,
                raw_strength=0.87,
                confirmed=True,
                details=f"Bear Pennant breakdown confirmed below {p_bot:.5f}"
            )
    return None


# ============================================================================
# 7. RECTANGLE / RANGE & CHANNEL
# ============================================================================

@PatternRegistry.register(PatternFamily.CHART, "Rectangle Range")
def detect_rectangle_range(opens, highs, lows, closes, vols, atr, cfg) -> Optional[PatternResult]:
    sh, sl = extract_swings(highs, lows, lookback=2)
    if len(sh) < 2 or len(sl) < 2:
        return None
    
    h1, h2 = sh[-2][1], sh[-1][1]
    l1, l2 = sl[-2][1], sl[-1][1]
    
    # Parallel horizontal touches
    if abs(h1 - h2) / max(h1, h2) > 0.04 or abs(l1 - l2) / max(l1, l2) > 0.04:
        return None
    
    range_high = max(h1, h2)
    range_low = min(l1, l2)
    range_height = range_high - range_low
    
    curr_close = closes[-1]
    # Bullish breakout
    if curr_close > range_high and closes[-2] <= range_high:
        return PatternResult(
            name="Rectangle Range",
            family=PatternFamily.CHART,
            direction=PatternDirection.BULLISH,
            bars_used=len(closes) - min(sh[-2][0], sl[-2][0]),
            key_levels={"range_high": range_high, "range_low": range_low},
            invalidation_price=range_low,
            measured_target=range_high + range_height,
            raw_strength=0.83,
            confirmed=True,
            details=f"Rectangle range bullish breakout above {range_high:.5f}"
        )
    # Bearish breakdown
    elif curr_close < range_low and closes[-2] >= range_low:
        return PatternResult(
            name="Rectangle Range",
            family=PatternFamily.CHART,
            direction=PatternDirection.BEARISH,
            bars_used=len(closes) - min(sh[-2][0], sl[-2][0]),
            key_levels={"range_high": range_high, "range_low": range_low},
            invalidation_price=range_high,
            measured_target=range_low - range_height,
            raw_strength=0.83,
            confirmed=True,
            details=f"Rectangle range bearish breakdown below {range_low:.5f}"
        )
    return None


@PatternRegistry.register(PatternFamily.CHART, "Channel")
def detect_channel(opens, highs, lows, closes, vols, atr, cfg) -> Optional[PatternResult]:
    sh, sl = extract_swings(highs, lows, lookback=2)
    if len(sh) < 2 or len(sl) < 2:
        return None
    
    (i1, h1), (i2, h2) = sh[-2], sh[-1]
    (j1, l1), (j2, l2) = sl[-2], sl[-1]
    
    slope_h = (h2 - h1) / max(1, (i2 - i1))
    slope_l = (l2 - l1) / max(1, (j2 - j1))
    
    # Slopes roughly parallel
    if (slope_h > 0 and slope_l > 0) or (slope_h < 0 and slope_l < 0):
        curr_close = closes[-1]
        # Ascending channel breakdown
        if slope_h > 0 and curr_close < l2 and closes[-2] >= l2:
            return PatternResult(
                name="Channel",
                family=PatternFamily.CHART,
                direction=PatternDirection.BEARISH,
                bars_used=len(closes) - min(i1, j1),
                key_levels={"upper_channel": h2, "lower_channel": l2},
                invalidation_price=h2 + 0.2 * atr,
                measured_target=curr_close - abs(h2 - l2),
                raw_strength=0.82,
                confirmed=True,
                details=f"Ascending channel breakdown below {l2:.5f}"
            )
        # Descending channel breakout
        elif slope_h < 0 and curr_close > h2 and closes[-2] <= h2:
            return PatternResult(
                name="Channel",
                family=PatternFamily.CHART,
                direction=PatternDirection.BULLISH,
                bars_used=len(closes) - min(i1, j1),
                key_levels={"upper_channel": h2, "lower_channel": l2},
                invalidation_price=l2 - 0.2 * atr,
                measured_target=curr_close + abs(h2 - l2),
                raw_strength=0.82,
                confirmed=True,
                details=f"Descending channel breakout above {h2:.5f}"
            )
    return None


# ============================================================================
# 8. CUP AND HANDLE & ROUNDING BOTTOM
# ============================================================================

@PatternRegistry.register(PatternFamily.CHART, "Cup and Handle")
def detect_cup_and_handle(opens, highs, lows, closes, vols, atr, cfg) -> Optional[PatternResult]:
    if len(closes) < 20:
        return None
    
    sh, sl = extract_swings(highs, lows, lookback=3)
    if len(sh) < 2 or len(sl) < 1:
        return None
    
    (i1, h1), (i2, h2) = sh[-2], sh[-1]
    # Rims must be approximately equal height
    if abs(h1 - h2) / max(h1, h2) > 0.08:
        return None
    
    cup_troughs = [p for i, p in sl if i1 < i < i2]
    if not cup_troughs:
        return None
    cup_bottom = min(cup_troughs)
    cup_depth = max(h1, h2) - cup_bottom
    
    # Handle forms after right rim: shallow consolidation
    handle_lows = lows[i2:-1]
    if len(handle_lows) < 2:
        return None
    handle_bottom = min(handle_lows)
    
    # Handle pullback must not exceed 50% of cup depth
    if (h2 - handle_bottom) > 0.50 * cup_depth:
        return None
    
    rim_line = max(h1, h2)
    curr_close = closes[-1]
    if curr_close > rim_line and closes[-2] <= rim_line:
        return PatternResult(
            name="Cup and Handle",
            family=PatternFamily.CHART,
            direction=PatternDirection.BULLISH,
            bars_used=len(closes) - i1,
            key_levels={"rim": rim_line, "cup_bottom": cup_bottom, "handle_bottom": handle_bottom},
            invalidation_price=handle_bottom - 0.2 * atr,
            measured_target=rim_line + cup_depth,
            raw_strength=0.89,
            confirmed=True,
            details=f"Cup & Handle confirmed: breakout above rim {rim_line:.5f}"
        )
    return None


@PatternRegistry.register(PatternFamily.CHART, "Rounding Bottom")
def detect_rounding_bottom(opens, highs, lows, closes, vols, atr, cfg) -> Optional[PatternResult]:
    if len(closes) < 20:
        return None
    
    # Look for curved parabolic progression
    window = closes[-20:-1]
    poly = np.polyfit(np.arange(len(window)), window, 2)
    # Positive curvature (bowl shape)
    if poly[0] > 0:
        left_lip = window[0]
        right_lip = window[-1]
        resistance = max(left_lip, right_lip)
        curr_close = closes[-1]
        if curr_close > resistance and closes[-2] <= resistance:
            trough = min(window)
            depth = resistance - trough
            return PatternResult(
                name="Rounding Bottom",
                family=PatternFamily.CHART,
                direction=PatternDirection.BULLISH,
                bars_used=20,
                key_levels={"resistance": resistance, "trough": trough},
                invalidation_price=trough - 0.2 * atr,
                measured_target=resistance + depth,
                raw_strength=0.86,
                confirmed=True,
                details=f"Rounding Bottom breakout confirmed above {resistance:.5f}"
            )
    return None


# ============================================================================
# 9. BROADENING, DIAMOND & ISLAND REVERSAL
# ============================================================================

@PatternRegistry.register(PatternFamily.CHART, "Broadening Formation")
def detect_broadening_formation(opens, highs, lows, closes, vols, atr, cfg) -> Optional[PatternResult]:
    sh, sl = extract_swings(highs, lows, lookback=2)
    if len(sh) < 2 or len(sl) < 2:
        return None
    
    (i1, h1), (i2, h2) = sh[-2], sh[-1]
    (j1, l1), (j2, l2) = sl[-2], sl[-1]
    
    # Higher highs AND lower lows (expanding megaphone)
    if h2 > h1 and l2 < l1:
        curr_close = closes[-1]
        if curr_close > h2:
            return PatternResult(
                name="Broadening Formation",
                family=PatternFamily.CHART,
                direction=PatternDirection.BULLISH,
                bars_used=len(closes) - min(i1, j1),
                key_levels={"upper_boundary": h2, "lower_boundary": l2},
                invalidation_price=l2 - 0.2 * atr,
                measured_target=curr_close + (h2 - l2),
                raw_strength=0.81,
                confirmed=True,
                details=f"Broadening Formation breakout above {h2:.5f}"
            )
        elif curr_close < l2:
            return PatternResult(
                name="Broadening Formation",
                family=PatternFamily.CHART,
                direction=PatternDirection.BEARISH,
                bars_used=len(closes) - min(i1, j1),
                key_levels={"upper_boundary": h2, "lower_boundary": l2},
                invalidation_price=h2 + 0.2 * atr,
                measured_target=curr_close - (h2 - l2),
                raw_strength=0.81,
                confirmed=True,
                details=f"Broadening Formation breakdown below {l2:.5f}"
            )
    return None


@PatternRegistry.register(PatternFamily.CHART, "Diamond")
def detect_diamond(opens, highs, lows, closes, vols, atr, cfg) -> Optional[PatternResult]:
    sh, sl = extract_swings(highs, lows, lookback=2)
    if len(sh) < 3 or len(sl) < 3:
        return None
    
    # Expanding then contracting swings
    h1, h2, h3 = sh[-3][1], sh[-2][1], sh[-1][1]
    l1, l2, l3 = sl[-3][1], sl[-2][1], sl[-1][1]
    
    if h2 > h1 and h2 > h3 and l2 < l1 and l2 < l3:
        curr_close = closes[-1]
        dia_height = h2 - l2
        if curr_close < l3 and closes[-2] >= l3:
            return PatternResult(
                name="Diamond",
                family=PatternFamily.CHART,
                direction=PatternDirection.BEARISH,
                bars_used=len(closes) - sh[-3][0],
                key_levels={"diamond_peak": h2, "diamond_trough": l2, "breakout_level": l3},
                invalidation_price=h3 + 0.2 * atr,
                measured_target=curr_close - dia_height,
                raw_strength=0.85,
                confirmed=True,
                details=f"Diamond Top breakdown below {l3:.5f}"
            )
        elif curr_close > h3 and closes[-2] <= h3:
            return PatternResult(
                name="Diamond",
                family=PatternFamily.CHART,
                direction=PatternDirection.BULLISH,
                bars_used=len(closes) - sh[-3][0],
                key_levels={"diamond_peak": h2, "diamond_trough": l2, "breakout_level": h3},
                invalidation_price=l3 - 0.2 * atr,
                measured_target=curr_close + dia_height,
                raw_strength=0.85,
                confirmed=True,
                details=f"Diamond Bottom breakout above {h3:.5f}"
            )
    return None


@PatternRegistry.register(PatternFamily.CHART, "Island Reversal")
def detect_island_reversal(opens, highs, lows, closes, vols, atr, cfg) -> Optional[PatternResult]:
    if len(closes) < 5:
        return None
    
    # Island cluster: 1 to 3 isolated bars
    # Bearish Island: gap up into cluster, gap down out of cluster
    gap_thresh = 0.25 * atr
    for cluster_len in [1, 2, 3]:
        idx_entry = -(cluster_len + 1)
        # Entry gap up
        if lows[idx_entry] - highs[idx_entry - 1] >= gap_thresh or opens[idx_entry] - closes[idx_entry - 1] >= gap_thresh:
            # Exit gap down
            if highs[-1] < lows[idx_entry] or closes[-1] - opens[-1] < -gap_thresh:
                return PatternResult(
                    name="Island Reversal",
                    family=PatternFamily.CHART,
                    direction=PatternDirection.BEARISH,
                    bars_used=cluster_len + 2,
                    key_levels={"island_high": max(highs[idx_entry:-1]), "gap_level": lows[idx_entry]},
                    invalidation_price=max(highs[idx_entry:-1]) + 0.2 * atr,
                    measured_target=closes[-1] - 2.0 * atr,
                    raw_strength=0.88,
                    confirmed=True,
                    details="Bearish Island Reversal cluster isolated by gaps"
                )
        # Bullish Island: gap down into cluster, gap up out of cluster
        if highs[idx_entry - 1] - lows[idx_entry] >= gap_thresh or closes[idx_entry - 1] - opens[idx_entry] >= gap_thresh:
            if lows[-1] > highs[idx_entry] or closes[-1] - opens[-1] > gap_thresh:
                return PatternResult(
                    name="Island Reversal",
                    family=PatternFamily.CHART,
                    direction=PatternDirection.BULLISH,
                    bars_used=cluster_len + 2,
                    key_levels={"island_low": min(lows[idx_entry:-1]), "gap_level": highs[idx_entry]},
                    invalidation_price=min(lows[idx_entry:-1]) - 0.2 * atr,
                    measured_target=closes[-1] + 2.0 * atr,
                    raw_strength=0.88,
                    confirmed=True,
                    details="Bullish Island Reversal cluster isolated by gaps"
                )
    return None


# ============================================================================
# 10. HARMONIC DETECTORS (GARTLEY, BAT, BUTTERFLY, CRAB)
# ============================================================================

def _evaluate_harmonic_xabcd(
    sh: List[Tuple[int, float]],
    sl: List[Tuple[int, float]],
    target_b_min: float,
    target_b_max: float,
    target_d_min: float,
    target_d_max: float,
    pattern_name: str,
    atr: float,
    tol: float = 0.08
) -> Optional[PatternResult]:
    """Evaluates 5-point XABCD harmonic geometry."""
    # Bullish Harmonic: X=Low, A=High, B=Low, C=High, D=Low (entry BUY at D)
    if len(sl) >= 3 and len(sh) >= 2:
        (ix, x), (ib, b), (id_, d) = sl[-3:]
        (ia, a), (ic, c) = sh[-2:]
        if ix < ia < ib < ic < id_:
            xa = a - x
            if xa > 0:
                b_ratio = (a - b) / xa
                d_ratio = (a - d) / xa
                if (target_b_min - tol <= b_ratio <= target_b_max + tol) and \
                   (target_d_min - tol <= d_ratio <= target_d_max + tol):
                    return PatternResult(
                        name=pattern_name,
                        family=PatternFamily.HARMONIC,
                        direction=PatternDirection.BULLISH,
                        bars_used=id_ - ix + 1,
                        key_levels={"X": x, "A": a, "B": b, "C": c, "D": d},
                        invalidation_price=d - 0.3 * atr,
                        measured_target=d + 0.618 * (a - d),
                        raw_strength=0.85,
                        confirmed=True,
                        details=f"Bullish {pattern_name}: B-ratio={b_ratio:.3f}, D-ratio={d_ratio:.3f}"
                    )
    
    # Bearish Harmonic: X=High, A=Low, B=High, C=Low, D=High (entry SELL at D)
    if len(sh) >= 3 and len(sl) >= 2:
        (ix, x), (ib, b), (id_, d) = sh[-3:]
        (ia, a), (ic, c) = sl[-2:]
        if ix < ia < ib < ic < id_:
            xa = x - a
            if xa > 0:
                b_ratio = (b - a) / xa
                d_ratio = (d - a) / xa
                if (target_b_min - tol <= b_ratio <= target_b_max + tol) and \
                   (target_d_min - tol <= d_ratio <= target_d_max + tol):
                    return PatternResult(
                        name=pattern_name,
                        family=PatternFamily.HARMONIC,
                        direction=PatternDirection.BEARISH,
                        bars_used=id_ - ix + 1,
                        key_levels={"X": x, "A": a, "B": b, "C": c, "D": d},
                        invalidation_price=d + 0.3 * atr,
                        measured_target=d - 0.618 * (d - a),
                        raw_strength=0.85,
                        confirmed=True,
                        details=f"Bearish {pattern_name}: B-ratio={b_ratio:.3f}, D-ratio={d_ratio:.3f}"
                    )
    return None


@PatternRegistry.register(PatternFamily.HARMONIC, "Gartley")
def detect_gartley(opens, highs, lows, closes, vols, atr, cfg) -> Optional[PatternResult]:
    sh, sl = extract_swings(highs, lows, lookback=2)
    tol = cfg.get("ratio_tolerance", 0.06)
    return _evaluate_harmonic_xabcd(sh, sl, 0.58, 0.65, 0.74, 0.82, "Gartley", atr, tol)


@PatternRegistry.register(PatternFamily.HARMONIC, "Bat")
def detect_bat(opens, highs, lows, closes, vols, atr, cfg) -> Optional[PatternResult]:
    sh, sl = extract_swings(highs, lows, lookback=2)
    tol = cfg.get("ratio_tolerance", 0.06)
    return _evaluate_harmonic_xabcd(sh, sl, 0.35, 0.52, 0.84, 0.92, "Bat", atr, tol)


@PatternRegistry.register(PatternFamily.HARMONIC, "Butterfly")
def detect_butterfly(opens, highs, lows, closes, vols, atr, cfg) -> Optional[PatternResult]:
    sh, sl = extract_swings(highs, lows, lookback=2)
    tol = cfg.get("ratio_tolerance", 0.06)
    return _evaluate_harmonic_xabcd(sh, sl, 0.74, 0.82, 1.20, 1.34, "Butterfly", atr, tol)


@PatternRegistry.register(PatternFamily.HARMONIC, "Crab")
def detect_crab(opens, highs, lows, closes, vols, atr, cfg) -> Optional[PatternResult]:
    sh, sl = extract_swings(highs, lows, lookback=2)
    tol = cfg.get("ratio_tolerance", 0.06)
    return _evaluate_harmonic_xabcd(sh, sl, 0.35, 0.65, 1.55, 1.70, "Crab", atr, tol)


# ============================================================================
# CHART PATTERN ENGINE CLASS
# ============================================================================

class ChartPatternEngine:
    """
    High-performance chart pattern and harmonic evaluation engine.
    """
    def __init__(self, config: Optional[Dict[str, Any]] = None):
        self.config = config or {}
        self.chart_cfg = self.config.get("chart", {})
        self.harmonic_cfg = self.config.get("harmonic", {})

    def detect(self, candles: List[Candle], atr: Optional[float] = None) -> List[PatternResult]:
        if len(candles) < self.chart_cfg.get("min_bars_lookback", 12):
            return []
        
        opens = np.array([c.open for c in candles], dtype=np.float64)
        highs = np.array([c.high for c in candles], dtype=np.float64)
        lows = np.array([c.low for c in candles], dtype=np.float64)
        closes = np.array([c.close for c in candles], dtype=np.float64)
        vols = np.array([getattr(c, 'tick_volume', 0) for c in candles], dtype=np.float64)
        
        calc_atr = atr if atr is not None else compute_atr(highs, lows, closes)
        results: List[PatternResult] = []
        
        # 1. Chart Patterns (if enabled)
        if self.chart_cfg.get("enabled", True):
            patterns_enabled = self.chart_cfg.get("patterns_enabled", {})
            chart_detectors = PatternRegistry.get_detectors(PatternFamily.CHART)
            for name, detector in chart_detectors.items():
                if patterns_enabled.get(name, True):
                    try:
                        res = detector(opens, highs, lows, closes, vols, calc_atr, self.chart_cfg)
                        if res is not None:
                            results.append(res)
                    except Exception:
                        pass
        
        # 2. Harmonics (if enabled)
        if self.harmonic_cfg.get("enabled", False):
            harmonics_enabled = self.harmonic_cfg.get("patterns_enabled", {})
            harmonic_detectors = PatternRegistry.get_detectors(PatternFamily.HARMONIC)
            for name, detector in harmonic_detectors.items():
                if harmonics_enabled.get(name, False):
                    try:
                        res = detector(opens, highs, lows, closes, vols, calc_atr, self.harmonic_cfg)
                        if res is not None:
                            results.append(res)
                    except Exception:
                        pass
        
        return results
