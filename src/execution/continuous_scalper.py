"""
Continuous Multi-Timeframe Scalping Execution Engine (M1 / M5 / M15).
Implements:
- State-driven lifecycle per symbol:
  MARKET_DATA -> ANALYSIS -> SIGNAL_GENERATION -> RISK_VALIDATION -> ORDER_EXECUTION
  -> POSITION_MANAGEMENT -> PROFIT_LOCKING -> POSITION_CLOSURE -> RESCAN
- Three independent scalping entry models with symmetric BUY and SELL detection:
  1. Momentum Continuation Scalp (EMA9/EMA21 alignment + displacement close)
  2. Pullback Scalp (FVG / Order Block / OTE 50%-79% / EMA21 zone + turn confirmation)
  3. Breakout / Liquidity-Sweep Scalp (Swing liquidity sweep reversal or range expansion breakout)
- Weighted confluence scoring (M15/H1 as weighted directional context rather than hard blockers)
- Adaptive R:R (default 1.5 - 2.0R) and ATR-calibrated Stop-Loss / Take-Profit placement
- Duplicate-signal idempotency guard per symbol/timeframe/direction/bar
- Immediate rescan after position closure or rejection (0s artificial cooldown)
- Concurrent multi-symbol evaluation with fair scheduling
"""

from __future__ import annotations
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
import logging
import threading
from typing import Any, Dict, List, Optional, Set, Tuple

from src.core.contracts import (
    Candle,
    OrderType,
    Position,
    SetupType,
    SignalCandidate,
    SymbolInfo,
    Timeframe,
    TrendBias,
)
from src.core.instruments import get_instrument_profile, is_gold

logger = logging.getLogger("ContinuousScalper")


class MarketRegime(str, Enum):
    BULLISH_TREND = "BULLISH_TREND"
    BEARISH_TREND = "BEARISH_TREND"
    RANGING = "RANGING"
    HIGH_VOLATILITY = "HIGH_VOLATILITY"


class ScalpingLifecycleState(str, Enum):
    MARKET_DATA = "MARKET_DATA"
    ANALYSIS = "ANALYSIS"
    SIGNAL_GENERATION = "SIGNAL_GENERATION"
    RISK_VALIDATION = "RISK_VALIDATION"
    ORDER_EXECUTION = "ORDER_EXECUTION"
    POSITION_MANAGEMENT = "POSITION_MANAGEMENT"
    PROFIT_LOCKING = "PROFIT_LOCKING"
    POSITION_CLOSURE = "POSITION_CLOSURE"
    RESCAN = "RESCAN"


@dataclass
class SymbolScalpingState:
    symbol: str
    state: ScalpingLifecycleState = ScalpingLifecycleState.MARKET_DATA
    state_history: List[ScalpingLifecycleState] = field(
        default_factory=lambda: [ScalpingLifecycleState.MARKET_DATA]
    )
    last_transition_time: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    last_evaluated_bar_time: Dict[str, datetime] = field(default_factory=dict)
    last_signal: Optional[SignalCandidate] = None
    active_ticket: Optional[int] = None
    rescan_count: int = 0

    def transition_to(self, new_state: ScalpingLifecycleState) -> None:
        self.state = new_state
        self.state_history.append(new_state)
        if len(self.state_history) > 50:
            self.state_history = self.state_history[-50:]
        self.last_transition_time = datetime.now(timezone.utc)
        if new_state == ScalpingLifecycleState.RESCAN:
            self.rescan_count += 1


class ContinuousScalpingEngine:
    """
    Adaptive M1/M5/M15 Continuous Scalping Engine.
    Operates alongside the existing SMC/ICT components and risk controls.
    """

    def __init__(
        self,
        symbols: Optional[List[str]] = None,
        min_confluence_score: float = 45.0,
        min_rr: float = 1.2,
        require_htf_agreement: bool = False,
        auto_rescan_after_closure: bool = True,
    ):
        self.symbols: List[str] = list(
            symbols or ["BTCUSD.m", "EURUSD", "GBPUSD", "USDJPY", "XAUUSD"]
        )
        self.min_confluence_score: float = float(min_confluence_score)
        self.min_rr: float = float(min_rr)
        self.require_htf_agreement: bool = bool(require_htf_agreement)
        self.auto_rescan_after_closure: bool = bool(auto_rescan_after_closure)

        self._lock = threading.Lock()
        self.lifecycle_states: Dict[str, SymbolScalpingState] = {
            s: SymbolScalpingState(symbol=s) for s in self.symbols
        }
        self._executed_signal_keys: Set[str] = set()
        self._round_robin_index: int = 0
        self._last_entry_time: Dict[str, datetime] = {}
        self._last_entry_price: Dict[str, float] = {}
        self._last_entry_direction: Dict[str, OrderType] = {}

    def get_symbol_state(self, symbol: str) -> SymbolScalpingState:
        with self._lock:
            if symbol not in self.lifecycle_states:
                self.lifecycle_states[symbol] = SymbolScalpingState(symbol=symbol)
            return self.lifecycle_states[symbol]

    def transition_state(self, symbol: str, new_state: ScalpingLifecycleState) -> None:
        with self._lock:
            if symbol not in self.lifecycle_states:
                self.lifecycle_states[symbol] = SymbolScalpingState(symbol=symbol)
            self.lifecycle_states[symbol].transition_to(new_state)

    def is_duplicate_signal(self, signal_key: str) -> bool:
        with self._lock:
            return signal_key in self._executed_signal_keys

    def mark_signal_executed(self, signal_key: str, symbol: Optional[str] = None, price: Optional[float] = None, direction: Optional[OrderType] = None) -> None:
        with self._lock:
            self._executed_signal_keys.add(signal_key)
            if symbol:
                now_utc = datetime.now(timezone.utc)
                self._last_entry_time[symbol] = now_utc
                if price is not None:
                    self._last_entry_price[symbol] = price
                if direction is not None:
                    self._last_entry_direction[symbol] = direction
            if len(self._executed_signal_keys) > 5000:
                # Prune oldest half
                keep = list(self._executed_signal_keys)[-2500:]
                self._executed_signal_keys = set(keep)

    @staticmethod
    def _compute_ema(closes: List[float], period: int) -> float:
        if not closes:
            return 0.0
        if len(closes) < period:
            return sum(closes) / len(closes)
        k = 2.0 / (period + 1.0)
        ema = sum(closes[:period]) / period
        for price in closes[period:]:
            ema = (price * k) + (ema * (1.0 - k))
        return ema

    @staticmethod
    def _compute_atr(candles: List[Candle], period: int = 14) -> float:
        if not candles:
            return 0.0
        if len(candles) < 2:
            return max(candles[-1].range, 1e-6)
        window = candles[-(period + 1) :]
        trs: List[float] = []
        for i in range(1, len(window)):
            c = window[i]
            p = window[i - 1]
            trs.append(max(c.high - c.low, abs(c.high - p.close), abs(c.low - p.close)))
        atr = sum(trs) / len(trs) if trs else candles[-1].range
        return max(atr, 1e-6)

    @staticmethod
    def _compute_rsi(candles: List[Candle], period: int = 14) -> float:
        """Wilder-style RSI on closed candles; returns 50.0 when insufficient data."""
        if len(candles) < period + 1:
            return 50.0
        window = candles[-(period + 1):]
        gains = 0.0
        losses = 0.0
        for i in range(1, len(window)):
            change = window[i].close - window[i - 1].close
            if change >= 0:
                gains += change
            else:
                losses -= change
        avg_gain = gains / period
        avg_loss = losses / period
        if avg_loss <= 1e-12:
            return 100.0 if avg_gain > 0 else 50.0
        rs = avg_gain / avg_loss
        return 100.0 - (100.0 / (1.0 + rs))

    def _entry_quality_adjustment(
        self,
        candidate: SignalCandidate,
        candles: List[Candle],
        profile=None,
    ) -> Tuple[float, List[str], bool]:
        """
        Adds a multi-indicator confirmation layer on top of the pattern models:
        RSI positioning, distance from local extremes (anti-chasing), and volume.
        Returns (score_delta, notes, allowed).
        """
        notes: List[str] = []
        delta = 0.0
        if profile is None:
            profile = get_instrument_profile(candidate.symbol)
        if len(candles) < 15:
            return 0.0, notes, True

        rsi = self._compute_rsi(candles, 14)
        recent = candles[-20:]
        local_high = max(c.high for c in recent)
        local_low = min(c.low for c in recent)
        last = candles[-1]
        atr = self._compute_atr(candles, 14)
        span = max(local_high - local_low, 1e-9)
        pos_in_range = (last.close - local_low) / span

        notes.append(f"RSI14={rsi:.1f}")
        if candidate.direction == OrderType.BUY:
            if rsi >= 78.0:
                delta -= 8.0
                notes.append("RSI overbought (-8)")
            elif 45.0 <= rsi <= 68.0:
                delta += 4.0
                notes.append("RSI bullish room (+4)")
            # Anti-chasing: at the very top of the range with a weak body.
            if pos_in_range >= 0.95 and last.body < 0.20 * max(last.range, 1e-9) and rsi >= 78.0:
                return delta, notes, False
        else:
            if rsi <= 22.0:
                delta -= 8.0
                notes.append("RSI oversold (-8)")
            elif 32.0 <= rsi <= 55.0:
                delta += 4.0
                notes.append("RSI bearish room (+4)")
            if pos_in_range <= 0.05 and last.body < 0.20 * max(last.range, 1e-9) and rsi <= 22.0:
                return delta, notes, False

        # Volume confirmation: above-average participation for breakout/sweep setups.
        if any("BREAKOUT" in r or "SWEEP" in r for r in candidate.reasons):
            vols = [c.tick_volume for c in recent if c.tick_volume > 0]
            if vols:
                avg_vol = sum(vols[:-1]) / max(1, len(vols) - 1)
                if last.tick_volume >= 1.25 * avg_vol:
                    delta += 3.0
                    notes.append("Volume expansion (+3)")

        # Ensure TP has room relative to the local structure (avoid targets inside noise).
        if candidate.take_profit > 0:
            if candidate.direction == OrderType.BUY and candidate.take_profit > local_high + 0.25 * atr:
                delta += 2.0
                notes.append("TP clearance (+2)")
            elif candidate.direction == OrderType.SELL and candidate.take_profit < local_low - 0.25 * atr:
                delta += 2.0
                notes.append("TP clearance (+2)")

        return delta, notes, True

    @classmethod
    def classify_market_regime(cls, candles: List[Candle]) -> Tuple[MarketRegime, Dict[str, Any]]:
        """
        Classifies current market regime: BULLISH_TREND, BEARISH_TREND, RANGING, or HIGH_VOLATILITY.
        """
        completed = [c for c in candles if getattr(c, "complete", True)]
        if len(completed) < 15:
            completed = list(candles)
        if len(completed) < 10:
            return MarketRegime.RANGING, {"reason": "Insufficient candles"}

        closes = [c.close for c in completed]
        ema9 = cls._compute_ema(closes[-30:], 9)
        ema21 = cls._compute_ema(closes[-45:], 21)
        ema50 = cls._compute_ema(closes[-70:], 50) if len(closes) >= 50 else ema21
        atr14 = cls._compute_atr(completed, 14)

        long_atr = cls._compute_atr(completed, min(50, len(completed) - 1))
        vol_ratio = (atr14 / long_atr) if long_atr > 1e-9 else 1.0
        last_close = closes[-1]

        metrics = {
            "ema9": ema9,
            "ema21": ema21,
            "ema50": ema50,
            "atr": atr14,
            "volatility_ratio": round(vol_ratio, 2),
            "close": last_close,
        }

        if vol_ratio >= 1.75:
            return MarketRegime.HIGH_VOLATILITY, metrics

        if last_close > ema9 > (ema21 + 0.05 * atr14) and ema21 >= (ema50 - 0.1 * atr14):
            return MarketRegime.BULLISH_TREND, metrics

        if last_close < ema9 < (ema21 - 0.05 * atr14) and ema21 <= (ema50 + 0.1 * atr14):
            return MarketRegime.BEARISH_TREND, metrics

        return MarketRegime.RANGING, metrics

    @classmethod
    def infer_timeframe_bias(cls, candles: List[Candle]) -> TrendBias:
        """
        Determines directional bias from EMA9, EMA21, and recent swing structure.
        """
        completed = [c for c in candles if getattr(c, "complete", True)]
        if len(completed) < 10:
            completed = list(candles)
        if len(completed) < 5:
            return TrendBias.RANGING

        closes = [c.close for c in completed[-35:]]
        ema9 = cls._compute_ema(closes, 9)
        ema21 = cls._compute_ema(closes, 21)
        atr = cls._compute_atr(completed, 14)
        last_close = closes[-1]

        if last_close > ema9 > (ema21 + 0.05 * atr):
            return TrendBias.STRONG_BULLISH
        if last_close < ema9 < (ema21 - 0.05 * atr):
            return TrendBias.STRONG_BEARISH
        return TrendBias.RANGING

    def _compute_htf_context_weight(
        self,
        direction: OrderType,
        m15_bias: TrendBias,
        h1_bias: TrendBias,
    ) -> Tuple[float, bool, str]:
        """
        Computes weighted confluence score contribution from M15 and H1 context timeframes.
        Returns (bonus_score, allowed, context_note).
        """
        bull_set = (TrendBias.STRONG_BULLISH, TrendBias.WEAK_BULLISH)
        bear_set = (TrendBias.STRONG_BEARISH, TrendBias.WEAK_BEARISH)
        target_set = bull_set if direction == OrderType.BUY else bear_set
        opposing_set = bear_set if direction == OrderType.BUY else bull_set
        target_label = "BULLISH" if direction == OrderType.BUY else "BEARISH"

        m15_aligned = m15_bias in target_set
        h1_aligned = h1_bias in target_set
        both_opposing = (m15_bias in opposing_set) and (h1_bias in opposing_set)

        if self.require_htf_agreement and both_opposing:
            return 0.0, False, f"HTF conflict blocked ({m15_bias.value}/{h1_bias.value})"

        score = 0.0
        if m15_aligned and h1_aligned:
            score += 20.0
            note = f"Full M15+H1 alignment ({target_label})"
        elif m15_aligned or h1_aligned:
            score += 14.0
            note = f"Partial HTF alignment (M15={m15_bias.value}, H1={h1_bias.value})"
        elif not both_opposing:
            score += 10.0
            note = f"Neutral/ranging HTF context (M15={m15_bias.value}, H1={h1_bias.value})"
        else:
            score += 5.0
            note = f"Counter-trend scalp context (M15={m15_bias.value}, H1={h1_bias.value})"

        return score, True, note

    def _detect_momentum_continuation(
        self,
        symbol: str,
        timeframe: Timeframe,
        candles: List[Candle],
        atr: float,
        ema9: float,
        ema21: float,
        m15_bias: TrendBias,
        h1_bias: TrendBias,
        symbol_info: Optional[SymbolInfo],
    ) -> List[SignalCandidate]:
        """
        Model 1: Momentum Continuation Scalp (BUY & SELL symmetric).
        Triggers when price aligns with EMA9/EMA21 and prints an impulsive displacement candle
        closing beyond the prior bar's extreme.
        """
        candidates: List[SignalCandidate] = []
        if len(candles) < 8:
            return candidates

        last = candles[-1]
        prev = candles[-2]
        prev2 = candles[-3]
        last_range = max(last.range, 1e-9)
        body_ratio = last.body / last_range

        # BUY Momentum Continuation
        bull_ema_aligned = last.close > ema9 and ema9 >= (ema21 - 0.10 * atr)
        bull_displacement = (
            last.is_bullish
            and body_ratio >= 0.45
            and last.body >= 0.35 * atr
            and (last.close > prev.high or (prev.is_bullish and last.close > prev.close))
        )
        if bull_ema_aligned and bull_displacement:
            htf_pts, allowed, htf_note = self._compute_htf_context_weight(OrderType.BUY, m15_bias, h1_bias)
            if allowed:
                base_score = 48.0 + htf_pts
                if body_ratio >= 0.65:
                    base_score += 7.0
                if prev.is_bullish and prev2.is_bullish:
                    base_score += 5.0
                cand = self._build_scalp_candidate(
                    symbol=symbol,
                    timeframe=timeframe,
                    direction=OrderType.BUY,
                    setup_type=SetupType.FAST_SCALP,
                    model_name="MOMENTUM_CONTINUATION",
                    last_candle=last,
                    raw_sl=min(last.low, prev.low) - (0.35 * atr),
                    atr=atr,
                    score=min(98.0, base_score),
                    rationale=f"Bullish {timeframe.value} momentum continuation (EMA9>EMA21, disp={body_ratio:.0%}, {htf_note})",
                    symbol_info=symbol_info,
                )
                if cand is not None:
                    candidates.append(cand)

        # SELL Momentum Continuation
        bear_ema_aligned = last.close < ema9 and ema9 <= (ema21 + 0.10 * atr)
        bear_displacement = (
            last.is_bearish
            and body_ratio >= 0.45
            and last.body >= 0.35 * atr
            and (last.close < prev.low or (prev.is_bearish and last.close < prev.close))
        )
        if bear_ema_aligned and bear_displacement:
            htf_pts, allowed, htf_note = self._compute_htf_context_weight(OrderType.SELL, m15_bias, h1_bias)
            if allowed:
                base_score = 48.0 + htf_pts
                if body_ratio >= 0.65:
                    base_score += 7.0
                if prev.is_bearish and prev2.is_bearish:
                    base_score += 5.0
                cand = self._build_scalp_candidate(
                    symbol=symbol,
                    timeframe=timeframe,
                    direction=OrderType.SELL,
                    setup_type=SetupType.FAST_SCALP,
                    model_name="MOMENTUM_CONTINUATION",
                    last_candle=last,
                    raw_sl=max(last.high, prev.high) + (0.35 * atr),
                    atr=atr,
                    score=min(98.0, base_score),
                    rationale=f"Bearish {timeframe.value} momentum continuation (EMA9<EMA21, disp={body_ratio:.0%}, {htf_note})",
                    symbol_info=symbol_info,
                )
                if cand is not None:
                    candidates.append(cand)

        return candidates

    def _detect_pullback_scalp(
        self,
        symbol: str,
        timeframe: Timeframe,
        candles: List[Candle],
        atr: float,
        ema9: float,
        ema21: float,
        m15_bias: TrendBias,
        h1_bias: TrendBias,
        symbol_info: Optional[SymbolInfo],
    ) -> List[SignalCandidate]:
        """
        Model 2: Pullback Entry (FVG / Order Block / OTE 50%-79% / EMA21 pullback + turn candle).
        Symmetric for BUY and SELL.
        """
        candidates: List[SignalCandidate] = []
        if len(candles) < 12:
            return candidates

        last = candles[-1]
        prev = candles[-2]
        window = candles[-12:-1]
        swing_high = max(c.high for c in window)
        swing_low = min(c.low for c in window)
        impulse_range = max(swing_high - swing_low, 1e-9)

        # Detect recent Bullish or Bearish FVG in last 8 bars
        bull_fvg_touched = False
        bear_fvg_touched = False
        for i in range(len(candles) - 4, max(1, len(candles) - 10), -1):
            c_left = candles[i - 1]
            c_right = candles[i + 1]
            if c_right.low > c_left.high and last.low <= c_right.low and last.close >= c_left.high:
                bull_fvg_touched = True
            if c_right.high < c_left.low and last.high >= c_right.high and last.close <= c_left.low:
                bear_fvg_touched = True

        # BUY Pullback into OTE (38.2%-79% retracement), FVG, or EMA21 zone
        retracement_from_high = (swing_high - last.low) / impulse_range
        in_bull_pullback_zone = (
            (0.35 <= retracement_from_high <= 0.82)
            or bull_fvg_touched
            or (abs(last.low - ema21) <= 0.45 * atr and last.close > ema21)
        )
        bull_turn_confirm = last.is_bullish or (last.lower_wick / max(last.range, 1e-9) >= 0.45 and last.close > prev.low)
        if in_bull_pullback_zone and bull_turn_confirm and ema9 >= (ema21 - 0.25 * atr):
            htf_pts, allowed, htf_note = self._compute_htf_context_weight(OrderType.BUY, m15_bias, h1_bias)
            if allowed:
                base_score = 47.0 + htf_pts
                if bull_fvg_touched:
                    base_score += 8.0
                if 0.50 <= retracement_from_high <= 0.79:
                    base_score += 6.0
                cand = self._build_scalp_candidate(
                    symbol=symbol,
                    timeframe=timeframe,
                    direction=OrderType.BUY,
                    setup_type=SetupType.FAST_SCALP,
                    model_name="PULLBACK_SCALP",
                    last_candle=last,
                    raw_sl=min(last.low, prev.low) - (0.40 * atr),
                    atr=atr,
                    score=min(98.0, base_score),
                    rationale=(
                        f"Bullish {timeframe.value} pullback scalp "
                        f"(retrace={retracement_from_high:.0%}, FVG={bull_fvg_touched}, {htf_note})"
                    ),
                    symbol_info=symbol_info,
                )
                if cand is not None:
                    candidates.append(cand)

        # SELL Pullback into OTE (38.2%-79% retracement), FVG, or EMA21 zone
        retracement_from_low = (last.high - swing_low) / impulse_range
        in_bear_pullback_zone = (
            (0.35 <= retracement_from_low <= 0.82)
            or bear_fvg_touched
            or (abs(last.high - ema21) <= 0.45 * atr and last.close < ema21)
        )
        bear_turn_confirm = last.is_bearish or (last.upper_wick / max(last.range, 1e-9) >= 0.45 and last.close < prev.high)
        if in_bear_pullback_zone and bear_turn_confirm and ema9 <= (ema21 + 0.25 * atr):
            htf_pts, allowed, htf_note = self._compute_htf_context_weight(OrderType.SELL, m15_bias, h1_bias)
            if allowed:
                base_score = 47.0 + htf_pts
                if bear_fvg_touched:
                    base_score += 8.0
                if 0.50 <= retracement_from_low <= 0.79:
                    base_score += 6.0
                cand = self._build_scalp_candidate(
                    symbol=symbol,
                    timeframe=timeframe,
                    direction=OrderType.SELL,
                    setup_type=SetupType.FAST_SCALP,
                    model_name="PULLBACK_SCALP",
                    last_candle=last,
                    raw_sl=max(last.high, prev.high) + (0.40 * atr),
                    atr=atr,
                    score=min(98.0, base_score),
                    rationale=(
                        f"Bearish {timeframe.value} pullback scalp "
                        f"(retrace={retracement_from_low:.0%}, FVG={bear_fvg_touched}, {htf_note})"
                    ),
                    symbol_info=symbol_info,
                )
                if cand is not None:
                    candidates.append(cand)

        return candidates

    def _detect_liquidity_sweep_or_breakout(
        self,
        symbol: str,
        timeframe: Timeframe,
        candles: List[Candle],
        atr: float,
        m15_bias: TrendBias,
        h1_bias: TrendBias,
        symbol_info: Optional[SymbolInfo],
    ) -> List[SignalCandidate]:
        """
        Model 3: Liquidity-Sweep Reversal or Range-Expansion Breakout Scalp (BUY & SELL symmetric).
        """
        candidates: List[SignalCandidate] = []
        if len(candles) < 10:
            return candidates

        last = candles[-1]
        prev = candles[-2]
        lookback = candles[-10:-1]
        prior_low = min(c.low for c in lookback)
        prior_high = max(c.high for c in lookback)

        # 3A. Bullish Liquidity Sweep Reversal (sweeps prior_low and closes back above it)
        swept_low = (last.low < prior_low and last.close > prior_low) or (
            prev.low < prior_low and last.is_bullish and last.close > prior_low
        )
        # 3B. Bullish Range Breakout (closes decisively above prior_high with range expansion)
        bull_breakout = last.is_bullish and last.close > prior_high and last.body >= 0.45 * atr

        if swept_low or bull_breakout:
            htf_pts, allowed, htf_note = self._compute_htf_context_weight(OrderType.BUY, m15_bias, h1_bias)
            if allowed:
                sub_type = "LIQUIDITY_SWEEP" if swept_low else "RANGE_BREAKOUT"
                base_score = (52.0 if swept_low else 49.0) + htf_pts
                cand = self._build_scalp_candidate(
                    symbol=symbol,
                    timeframe=timeframe,
                    direction=OrderType.BUY,
                    setup_type=SetupType.FAST_SCALP,
                    model_name=sub_type,
                    last_candle=last,
                    raw_sl=min(last.low, prev.low) - (0.35 * atr),
                    atr=atr,
                    score=min(98.0, base_score),
                    rationale=f"Bullish {timeframe.value} {sub_type} at {prior_low if swept_low else prior_high:.5f} ({htf_note})",
                    symbol_info=symbol_info,
                )
                if cand is not None:
                    candidates.append(cand)

        # 3C. Bearish Liquidity Sweep Reversal (sweeps prior_high and closes back below it)
        swept_high = (last.high > prior_high and last.close < prior_high) or (
            prev.high > prior_high and last.is_bearish and last.close < prior_high
        )
        # 3D. Bearish Range Breakout (closes decisively below prior_low with range expansion)
        bear_breakout = last.is_bearish and last.close < prior_low and last.body >= 0.45 * atr

        if swept_high or bear_breakout:
            htf_pts, allowed, htf_note = self._compute_htf_context_weight(OrderType.SELL, m15_bias, h1_bias)
            if allowed:
                sub_type = "LIQUIDITY_SWEEP" if swept_high else "RANGE_BREAKOUT"
                base_score = (52.0 if swept_high else 49.0) + htf_pts
                cand = self._build_scalp_candidate(
                    symbol=symbol,
                    timeframe=timeframe,
                    direction=OrderType.SELL,
                    setup_type=SetupType.FAST_SCALP,
                    model_name=sub_type,
                    last_candle=last,
                    raw_sl=max(last.high, prev.high) + (0.35 * atr),
                    atr=atr,
                    score=min(98.0, base_score),
                    rationale=f"Bearish {timeframe.value} {sub_type} at {prior_high if swept_high else prior_low:.5f} ({htf_note})",
                    symbol_info=symbol_info,
                )
                if cand is not None:
                    candidates.append(cand)

        return candidates

    def _build_scalp_candidate(
        self,
        symbol: str,
        timeframe: Timeframe,
        direction: OrderType,
        setup_type: SetupType,
        model_name: str,
        last_candle: Candle,
        raw_sl: float,
        atr: float,
        score: float,
        rationale: str,
        symbol_info: Optional[SymbolInfo],
    ) -> Optional[SignalCandidate]:
        """
        Constructs a validated SignalCandidate with broker-compliant SL/TP distances and adaptive R:R.
        """
        if score < self.min_confluence_score:
            return None

        point = symbol_info.point if (symbol_info and symbol_info.point > 0) else 0.00001
        profile = get_instrument_profile(symbol)
        effective_min_score = max(float(self.min_confluence_score), float(profile.min_confidence))
        if score < effective_min_score:
            return None

        stops_pts = max(int(getattr(symbol_info, "stops_level", 0)) if symbol_info else 0, 5)
        spread_pts = float(getattr(symbol_info, "spread", 0.0)) if symbol_info else 0.0
        # Ensure SL is wide enough that current spread is well within the spread-to-SL limit.
        spread_ceiling_pct = max(5.0, min(float(profile.max_spread_pct_of_sl), 35.0))
        min_sl_for_spread = (spread_pts * point) / (spread_ceiling_pct / 100.0) if spread_pts > 0 else 0.0
        atr_min_mult = max(0.5, float(profile.atr_sl_min_multiplier))
        atr_max_mult = max(atr_min_mult, float(profile.atr_sl_max_multiplier))
        min_sl_dist = max(stops_pts * point * 2.5, atr_min_mult * atr, min_sl_for_spread, point * 20)
        is_gold_instrument = is_gold(symbol)
        if is_gold_instrument:
            # Dedicated Gold calibration: prevent whipsawing during micro-noise.
            min_sl_dist = max(min_sl_dist, point * 150.0, 1.2 * atr)
            max_sl_dist = max(atr_max_mult * atr, point * 600.0, min_sl_dist * 1.5)
        else:
            max_sl_dist = max(atr_max_mult * atr, min_sl_dist * 1.5)

        entry = float(last_candle.close)
        if symbol_info:
            if direction == OrderType.BUY and symbol_info.ask > 0 and abs(symbol_info.ask - entry) <= 2.0 * atr:
                entry = float(symbol_info.ask)
            elif direction == OrderType.SELL and symbol_info.bid > 0 and abs(symbol_info.bid - entry) <= 2.0 * atr:
                entry = float(symbol_info.bid)

        raw_dist = abs(entry - raw_sl)
        sl_dist = min(max(raw_dist, min_sl_dist), max_sl_dist)
        is_day_trade = timeframe in (Timeframe.M15, Timeframe.H1)
        profile_rr = max(float(profile.target_rr), float(self.min_rr))
        if is_day_trade:
            target_rr = max(profile_rr, self.min_rr * 1.35)
        else:
            target_rr = max(self.min_rr, profile_rr if score >= 65.0 else max(1.25, self.min_rr))
        tp_dist = sl_dist * target_rr

        if direction == OrderType.BUY:
            sl = entry - sl_dist
            tp = entry + tp_dist
        else:
            sl = entry + sl_dist
            tp = entry - tp_dist

        if symbol_info:
            entry = symbol_info.round_price(entry)
            sl = symbol_info.round_price(sl)
            tp = symbol_info.round_price(tp)

        actual_sl_dist = abs(entry - sl)
        if actual_sl_dist <= 0:
            return None

        # Ensure TP satisfies target_rr / min_rr after price rounding
        required_tp_dist = actual_sl_dist * target_rr
        if abs(tp - entry) < required_tp_dist:
            if direction == OrderType.BUY:
                tp = entry + required_tp_dist
                if symbol_info:
                    tp = symbol_info.round_price(tp)
                    if round(abs(tp - entry) / actual_sl_dist, 2) < target_rr:
                        tp = symbol_info.round_price(tp + point)
            else:
                tp = entry - required_tp_dist
                if symbol_info:
                    tp = symbol_info.round_price(tp)
                    if round(abs(tp - entry) / actual_sl_dist, 2) < target_rr:
                        tp = symbol_info.round_price(tp - point)

        actual_rr = round(abs(tp - entry) / actual_sl_dist, 2)
        if actual_rr < (self.min_rr - 0.02):
            return None

        bar_ts = int(last_candle.time.timestamp())
        mode_prefix = "DAY" if is_day_trade else "SCALP"
        sig_id = f"{mode_prefix}_{symbol}_{timeframe.value}_{model_name}_{direction.value}_{bar_ts}"

        return SignalCandidate(
            id=sig_id,
            symbol=symbol,
            timeframe=timeframe,
            direction=direction,
            setup_type=setup_type,
            entry_price=entry,
            stop_loss=sl,
            take_profit=tp,
            risk_reward=actual_rr,
            score=round(score, 2),
            timestamp=last_candle.time,
            status="APPROVED",
            reasons=[
                rationale,
                f"MODEL:{model_name}",
                f"TF:{timeframe.value}",
                f"RR:{actual_rr}",
                f"MODE:{'DAY_TRADE' if is_day_trade else 'FAST_SCALP'}",
                f"INSTRUMENT:{profile.canonical}",
            ],
            related_level=model_name,
        )

    def scan_symbol_timeframes(
        self,
        symbol: str,
        candles_by_tf: Dict[Timeframe, List[Candle]],
        symbol_info: Optional[SymbolInfo] = None,
        execution_timeframes: Optional[List[Timeframe]] = None,
    ) -> Optional[SignalCandidate]:
        """
        Evaluates M1, M5, and M15 candles for `symbol` using M15 and H1 as directional context.
        Returns the highest-scoring non-duplicate SignalCandidate, or None if no setup qualifies.
        """
        self.transition_state(symbol, ScalpingLifecycleState.MARKET_DATA)
        exec_tfs = execution_timeframes or [Timeframe.M1, Timeframe.M5, Timeframe.M15]

        m15_candles = [c for c in candles_by_tf.get(Timeframe.M15, []) if getattr(c, "complete", True)]
        h1_candles = [c for c in candles_by_tf.get(Timeframe.H1, []) if getattr(c, "complete", True)]

        self.transition_state(symbol, ScalpingLifecycleState.ANALYSIS)
        m15_bias = self.infer_timeframe_bias(m15_candles)
        h1_bias = self.infer_timeframe_bias(h1_candles)
        regime_candles = m15_candles if len(m15_candles) >= 15 else h1_candles
        regime, regime_metrics = self.classify_market_regime(regime_candles)
        gold_symbol = is_gold(symbol)
        if gold_symbol:
            logger.info(
                "[GOLD] Scanning %s | regime=%s atr=%.4f M15=%s H1=%s",
                symbol, regime.value, float(regime_metrics.get("atr", 0.0)),
                m15_bias.value, h1_bias.value,
            )

        all_candidates: List[SignalCandidate] = []
        for tf in exec_tfs:
            raw_bars = candles_by_tf.get(tf, [])
            completed = [c for c in raw_bars if getattr(c, "complete", True)]
            if len(completed) < 12:
                continue

            closes = [c.close for c in completed[-35:]]
            ema9 = self._compute_ema(closes, 9)
            ema21 = self._compute_ema(closes, 21)
            atr = self._compute_atr(completed, 14)

            all_candidates.extend(
                self._detect_momentum_continuation(
                    symbol, tf, completed, atr, ema9, ema21, m15_bias, h1_bias, symbol_info
                )
            )
            all_candidates.extend(
                self._detect_pullback_scalp(
                    symbol, tf, completed, atr, ema9, ema21, m15_bias, h1_bias, symbol_info
                )
            )
            all_candidates.extend(
                self._detect_liquidity_sweep_or_breakout(
                    symbol, tf, completed, atr, m15_bias, h1_bias, symbol_info
                )
            )

        now_utc = datetime.now(timezone.utc)
        last_entry_t = self._last_entry_time.get(symbol)
        last_entry_p = self._last_entry_price.get(symbol)
        last_entry_d = self._last_entry_direction.get(symbol)

        fresh_candidates: List[SignalCandidate] = []
        for cand in all_candidates:
            # 1. Market Regime adjustments
            if regime == MarketRegime.BULLISH_TREND:
                if cand.direction == OrderType.BUY:
                    cand.score = min(99.0, cand.score + 5.0)
                else:
                    cand.score -= 12.0
            elif regime == MarketRegime.BEARISH_TREND:
                if cand.direction == OrderType.SELL:
                    cand.score = min(99.0, cand.score + 5.0)
                else:
                    cand.score -= 12.0
            elif regime == MarketRegime.RANGING:
                if any("SWEEP" in r for r in cand.reasons):
                    cand.score = min(99.0, cand.score + 6.0)
            elif regime == MarketRegime.HIGH_VOLATILITY:
                cand.score -= 5.0
            cand.reasons.append(f"REGIME:{regime.value}")

            # 1b. Multi-indicator entry-quality layer (RSI + volume + TP clearance).
            tf_bars = [
                c for c in candles_by_tf.get(cand.timeframe, [])
                if getattr(c, "complete", True)
            ]
            q_delta, q_notes, q_allowed = self._entry_quality_adjustment(cand, tf_bars)
            if not q_allowed:
                if is_gold(symbol):
                    logger.info(
                        "[GOLD] Rejected %s %s %s: entry-quality guard (%s)",
                        cand.timeframe.value, cand.direction.value, cand.setup_type.value,
                        ", ".join(q_notes),
                    )
                continue
            cand.score = min(99.0, max(0.0, cand.score + q_delta))
            cand.reasons.extend(q_notes)

            if cand.score < self.min_confluence_score:
                continue

            # 2. Time and price spacing guard (prevents rapid repetitive entries into adverse trends)
            if last_entry_t and (now_utc - last_entry_t).total_seconds() < 90.0:
                continue
            if (
                last_entry_t
                and (now_utc - last_entry_t).total_seconds() < 300.0
                and last_entry_d == cand.direction
                and last_entry_p is not None
            ):
                cand_sl_dist = abs(cand.entry_price - cand.stop_loss)
                if abs(cand.entry_price - last_entry_p) < 0.65 * cand_sl_dist:
                    continue

            # 3. Bar-level deduplication
            dup_key = f"{cand.symbol}_{cand.timeframe.value}_{cand.direction.value}_{cand.timestamp.isoformat()}"
            if not self.is_duplicate_signal(dup_key) and not self.is_duplicate_signal(cand.id):
                fresh_candidates.append(cand)

        if not fresh_candidates:
            if gold_symbol:
                logger.info(
                    "[GOLD] No qualified setup for %s (regime=%s, raw_candidates=%d)",
                    symbol, regime.value, len(all_candidates),
                )
            return None

        self.transition_state(symbol, ScalpingLifecycleState.SIGNAL_GENERATION)
        fresh_candidates.sort(key=lambda c: (c.score, c.risk_reward), reverse=True)
        best = fresh_candidates[0]
        if gold_symbol:
            logger.info(
                "[GOLD] SIGNAL %s %s %s | score=%.1f RR=%.2f entry=%.2f SL=%.2f TP=%.2f",
                symbol, best.setup_type.value, best.direction.value, best.score,
                best.risk_reward, best.entry_price, best.stop_loss, best.take_profit,
            )
        state = self.get_symbol_state(symbol)
        state.last_signal = best
        return best

    def on_position_closed_or_rejected(self, symbol: str, was_closed: bool = True) -> ScalpingLifecycleState:
        """
        Immediately transitions the symbol lifecycle through POSITION_CLOSURE -> RESCAN -> MARKET_DATA
        without artificial cooldown when auto_rescan_after_closure is enabled.
        """
        if was_closed:
            self.transition_state(symbol, ScalpingLifecycleState.POSITION_CLOSURE)
        if self.auto_rescan_after_closure:
            self.transition_state(symbol, ScalpingLifecycleState.RESCAN)
            self.transition_state(symbol, ScalpingLifecycleState.MARKET_DATA)
        state = self.get_symbol_state(symbol)
        state.active_ticket = None
        return state.state

    def get_ordered_symbols_for_cycle(self) -> List[str]:
        """
        Returns symbols rotated in fair round-robin order so no single symbol can starve others.
        """
        with self._lock:
            if not self.symbols:
                return []
            idx = self._round_robin_index % len(self.symbols)
            self._round_robin_index = (self._round_robin_index + 1) % len(self.symbols)
            return self.symbols[idx:] + self.symbols[:idx]

    def evaluate_symbols_concurrently(
        self,
        symbol_worker_fn: Any,
        max_workers: int = 5,
    ) -> Dict[str, Any]:
        """
        Executes `symbol_worker_fn(symbol)` concurrently across all enabled symbols in fair order.
        """
        ordered = self.get_ordered_symbols_for_cycle()
        results: Dict[str, Any] = {}
        if not ordered:
            return results

        workers = max(1, min(max_workers, len(ordered)))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            future_map = {pool.submit(symbol_worker_fn, sym): sym for sym in ordered}
            for fut in as_completed(future_map):
                sym = future_map[fut]
                try:
                    results[sym] = fut.result()
                except Exception as e:
                    logger.error(f"Concurrent scalping worker error on {sym}: {e}")
                    results[sym] = {"symbol": sym, "error": str(e)}
        return results
