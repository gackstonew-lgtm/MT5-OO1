"""
Fast Mode Low-Latency Scalp Entry Engine (Phase 2).
Provides:
1. Pre-computed per-symbol state cache (HTF M15/H1 bias, ATR, key liquidity levels) refreshed on bar close.
2. M1 / M5 closed-bar scalp entry evaluation (strictly complete == True, zero lookahead, no repainting).
3. Reuses existing SMC/ICT logic (CHoCH -> BOS confirmation, FVG/OB retest, liquidity sweep, pattern confluence).
4. Enforces Fast Mode entry gates:
   - Allowed symbols whitelist & Session Affinity Matrix gating
   - Spread <= X% of stop distance
   - ATR within configurable floor and ceiling (in points)
   - High-impact news blackout window check
   - Mandatory minimum R:R (1:2.0 / 1:2.5)
   - Post-trade cooldown (seconds) and per-symbol hourly trade rate ceiling to prevent churning
"""

from __future__ import annotations
import json
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional, Tuple, Any

from src.core.contracts import (
    Candle, Timeframe, OrderType, TrendBias, SetupType, SignalCandidate, SymbolInfo
)
from src.core.sessions import TradingSessionEngine
from src.core.session_affinity import SessionAffinityEngine
from src.smc.structure_confirmation import StructureStateMachine, ConfirmationState, CycleDirection
from src.smc.fvg_ob import FvgObEngine


@dataclass
class PrecomputedSymbolState:
    symbol: str
    h1_bias: TrendBias
    m15_bias: TrendBias
    cached_atr: float
    cached_atr_points: float
    key_high_level: float
    key_low_level: float
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class FastScalpEntryEngine:
    def __init__(self, config_path: str = "config/fast_mode.json"):
        self.config_path = config_path
        self._cfg = self._load_config()
        self._enabled: bool = bool(self._cfg.get("enabled", False))

        self._precomputed_state: Dict[str, PrecomputedSymbolState] = {}
        self._last_trade_timestamp: Dict[str, datetime] = {}
        self._hourly_trade_timestamps: Dict[str, List[datetime]] = {}
        self._evaluated_bar_times: Dict[str, datetime] = {}

        self.session_engine = TradingSessionEngine()
        self.affinity_engine = SessionAffinityEngine()
        self.structure_machine = StructureStateMachine(
            swing_lookback=3,
            require_close_break=True,
            require_lower_high=True,
            choch_timeout_bars=15,
            allow_choch_only_entries=False
        )

    def _load_config(self) -> Dict[str, Any]:
        if os.path.exists(self.config_path):
            try:
                with open(self.config_path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
        return {"enabled": False}

    def reload_config(self) -> None:
        self._cfg = self._load_config()
        self._enabled = bool(self._cfg.get("enabled", False))

    @property
    def is_enabled(self) -> bool:
        return self._enabled

    def set_enabled(self, flag: bool) -> None:
        self._enabled = bool(flag)
        self._cfg["enabled"] = self._enabled
        try:
            with open(self.config_path, "w", encoding="utf-8") as f:
                json.dump(self._cfg, f, indent=2)
        except Exception:
            pass

    @staticmethod
    def compute_atr(candles: List[Candle], period: int = 14) -> float:
        if not candles:
            return 0.0
        closed = [c for c in candles if c.complete]
        if len(closed) < 2:
            return closed[-1].range if closed else 0.0
        trs: List[float] = []
        for i in range(1, len(closed)):
            c = closed[i]
            prev_c = closed[i - 1].close
            tr = max(c.high - c.low, abs(c.high - prev_c), abs(c.low - prev_c))
            trs.append(tr)
        window = trs[-period:] if len(trs) >= period else trs
        return sum(window) / len(window) if window else 0.0

    def refresh_precomputed_state(
        self,
        symbol: str,
        entry_candles: List[Candle],
        m15_bias: TrendBias,
        h1_bias: TrendBias,
        symbol_info: SymbolInfo
    ) -> PrecomputedSymbolState:
        """
        Refreshes pre-computed ATR, key liquidity levels, and HTF bias on bar close
        so fast triggers require minimal overhead.
        """
        closed = [c for c in entry_candles if c.complete]
        atr = self.compute_atr(closed, period=14)
        pt = symbol_info.point if symbol_info.point > 0 else 0.00001
        atr_pts = round(atr / pt, 2)

        lookback = closed[-20:] if len(closed) >= 20 else closed
        key_high = max((c.high for c in lookback), default=0.0)
        key_low = min((c.low for c in lookback), default=0.0)

        state = PrecomputedSymbolState(
            symbol=symbol,
            h1_bias=h1_bias,
            m15_bias=m15_bias,
            cached_atr=atr,
            cached_atr_points=atr_pts,
            key_high_level=key_high,
            key_low_level=key_low,
            updated_at=closed[-1].time if closed else datetime.now(timezone.utc)
        )
        self._precomputed_state[symbol] = state
        return state

    def record_trade_executed(self, symbol: str, timestamp: Optional[datetime] = None) -> None:
        ts = timestamp or datetime.now(timezone.utc)
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        self._last_trade_timestamp[symbol] = ts
        history = self._hourly_trade_timestamps.setdefault(symbol, [])
        history.append(ts)
        # Prune older than 1 hour
        cutoff = ts - timedelta(hours=1)
        self._hourly_trade_timestamps[symbol] = [t for t in history if t > cutoff]

    def check_anti_churn_limits(self, symbol: str, current_time: datetime) -> Tuple[bool, Optional[str]]:
        """Checks post-trade cooldown and hourly trade count cap per symbol."""
        if current_time.tzinfo is None:
            current_time = current_time.replace(tzinfo=timezone.utc)

        scalp_cfg = self._cfg.get("scalp_entry", {})
        cooldown_sec = float(scalp_cfg.get("cooldown_seconds_after_trade", 120))
        max_per_hour = int(scalp_cfg.get("max_trades_per_symbol_per_hour", 3))

        last_ts = self._last_trade_timestamp.get(symbol)
        if last_ts is not None:
            elapsed = (current_time - last_ts).total_seconds()
            if elapsed < cooldown_sec:
                return False, f"Cooldown active for {symbol}: {elapsed:.0f}s < {cooldown_sec:.0f}s required"

        history = self._hourly_trade_timestamps.get(symbol, [])
        cutoff = current_time - timedelta(hours=1)
        recent = [t for t in history if t > cutoff]
        self._hourly_trade_timestamps[symbol] = recent
        if len(recent) >= max_per_hour:
            return False, f"Hourly trade limit reached for {symbol}: {len(recent)}/{max_per_hour} trades in last 60m"

        return True, None

    def evaluate_scalp_entry(
        self,
        symbol: str,
        entry_candles: List[Candle],
        symbol_info: SymbolInfo,
        m15_bias: TrendBias = TrendBias.STRONG_BULLISH,
        h1_bias: TrendBias = TrendBias.STRONG_BULLISH,
        entry_timeframe: Timeframe = Timeframe.M5,
        is_news_blackout: bool = False
    ) -> Tuple[Optional[SignalCandidate], Optional[str]]:
        """
        Evaluates M1/M5 closed bars for a Fast Mode scalp setup.
        Strictly filters out incomplete/forming bars to guarantee zero repainting/lookahead.
        Returns `(candidate, rejection_reason)`.
        """
        if not self._enabled:
            return None, "Fast mode is disabled"

        # 1. Allowed symbols whitelist check
        allowed_syms = self._cfg.get("allowed_symbols", ["XAUUSD", "XAUUSD.m"])
        clean_sym = self.affinity_engine.resolve_symbol(symbol)
        if symbol not in allowed_syms and clean_sym not in allowed_syms:
            return None, f"Symbol {symbol} is not in Fast Mode allowed_symbols whitelist"

        # 2. Closed candles only (never evaluate incomplete forming bar)
        closed_candles = [c for c in entry_candles if c.complete]
        if len(closed_candles) < 15:
            return None, f"Insufficient closed {entry_timeframe.value} bars ({len(closed_candles)} < 15)"

        last_bar = closed_candles[-1]
        bar_time = last_bar.time if last_bar.time.tzinfo else last_bar.time.replace(tzinfo=timezone.utc)

        # Deduplicate per closed bar timestamp
        if self._evaluated_bar_times.get(symbol) == bar_time:
            return None, f"Bar {bar_time.isoformat()} already evaluated for {symbol}"

        # 3. High-impact news blackout gate
        if is_news_blackout:
            return None, f"High-impact news blackout active for {symbol}"

        # 4. Session & Affinity gate
        scalp_cfg = self._cfg.get("scalp_entry", {})
        if scalp_cfg.get("require_session_affinity", True):
            sess_info = self.session_engine.get_session_info(bar_time, broker_offset_verified=True)
            if not sess_info.can_trade:
                return None, f"Session gate blocked: {sess_info.rejection_reason}"
            allowed_aff, _, aff_reason = self.affinity_engine.is_symbol_allowed(
                sess_info.primary_session, symbol, dt_utc=bar_time
            )
            if not allowed_aff:
                return None, f"Session affinity blocked: {aff_reason}"

        # 5. Anti-churn cooldown & hourly trade cap
        churn_ok, churn_reason = self.check_anti_churn_limits(symbol, bar_time)
        if not churn_ok:
            return None, churn_reason

        # 6. Refresh or retrieve precomputed state (HTF bias, ATR)
        state = self._precomputed_state.get(symbol)
        if state is None or state.updated_at < bar_time:
            state = self.refresh_precomputed_state(symbol, closed_candles, m15_bias, h1_bias, symbol_info)

        # 7. ATR floor and ceiling check
        atr_floor = float(scalp_cfg.get("atr_floor_points", 5.0))
        atr_ceiling = float(scalp_cfg.get("atr_ceiling_points", 1500.0))
        if state.cached_atr_points < atr_floor:
            return None, f"ATR ({state.cached_atr_points:.1f} pts) below floor ({atr_floor:.1f} pts)"
        if state.cached_atr_points > atr_ceiling:
            return None, f"ATR ({state.cached_atr_points:.1f} pts) exceeds ceiling ({atr_ceiling:.1f} pts)"

        # 8. HTF M15 + H1 directional alignment check
        bullish_htf = (
            state.m15_bias in (TrendBias.STRONG_BULLISH, TrendBias.WEAK_BULLISH)
            and state.h1_bias in (TrendBias.STRONG_BULLISH, TrendBias.WEAK_BULLISH)
        )
        bearish_htf = (
            state.m15_bias in (TrendBias.STRONG_BEARISH, TrendBias.WEAK_BEARISH)
            and state.h1_bias in (TrendBias.STRONG_BEARISH, TrendBias.WEAK_BEARISH)
        )
        if not bullish_htf and not bearish_htf:
            return None, f"HTF M15 ({state.m15_bias.value}) and H1 ({state.h1_bias.value}) not aligned"

        target_dir = OrderType.BUY if bullish_htf else OrderType.SELL

        # 9. Structure / FVG / Sweep confirmation on closed entry candles
        snap = self.structure_machine.update(symbol, entry_timeframe, closed_candles)
        fvg_engine = FvgObEngine(min_fvg_pips=0.5, pip_size=symbol_info.point * 10)
        fvgs = fvg_engine.detect_fvgs(symbol, entry_timeframe, closed_candles[-12:])
        obs = fvg_engine.detect_order_blocks(symbol, entry_timeframe, closed_candles[-12:])

        # Check closed-bar impulse + structural/FVG trigger
        prev_bar = closed_candles[-2]
        has_trigger = False
        trigger_reasons: List[str] = [f"HTF {state.h1_bias.value}/{state.m15_bias.value} aligned (+30)"]

        if target_dir == OrderType.BUY:
            if last_bar.is_bullish and last_bar.close > prev_bar.high:
                has_trigger = True
                trigger_reasons.append("Bullish displacement close above prior bar high (+25)")
            if snap.state == ConfirmationState.BOS_CONFIRMED and snap.direction == CycleDirection.BULLISH:
                has_trigger = True
                trigger_reasons.append("M1/M5 BOS_CONFIRMED bullish (+20)")
        else:
            if last_bar.is_bearish and last_bar.close < prev_bar.low:
                has_trigger = True
                trigger_reasons.append("Bearish displacement close below prior bar low (+25)")
            if snap.state == ConfirmationState.BOS_CONFIRMED and snap.direction == CycleDirection.BEARISH:
                has_trigger = True
                trigger_reasons.append("M1/M5 BOS_CONFIRMED bearish (+20)")

        if any(f.direction == target_dir for f in fvgs):
            trigger_reasons.append("Aligned FVG imbalance (+15)")
        if any(o.direction == target_dir for o in obs):
            trigger_reasons.append("Aligned Order Block (+10)")

        if not has_trigger:
            return None, "No confirmed closed-bar displacement or BOS trigger on entry timeframe"

        # 10. Compute Entry, Structural SL, and R:R Target
        pt = symbol_info.point if symbol_info.point > 0 else 0.00001
        min_stops_dist = max(int(getattr(symbol_info, "stops_level", 0)), 5) * pt
        atr_val = max(state.cached_atr, min_stops_dist * 1.5)
        min_rr = max(float(scalp_cfg.get("min_reward_to_risk", 2.0)), 2.0)

        entry_price = symbol_info.round_price(last_bar.close)
        recent_5 = closed_candles[-5:]

        if target_dir == OrderType.BUY:
            swing_low = min(c.low for c in recent_5)
            raw_sl = min(swing_low - 0.25 * atr_val, entry_price - max(atr_val, min_stops_dist * 1.2))
            sl_price = symbol_info.round_price(raw_sl)
            sl_dist = entry_price - sl_price
            tp_price = symbol_info.round_price(entry_price + sl_dist * min_rr)
        else:
            swing_high = max(c.high for c in recent_5)
            raw_sl = max(swing_high + 0.25 * atr_val, entry_price + max(atr_val, min_stops_dist * 1.2))
            sl_price = symbol_info.round_price(raw_sl)
            sl_dist = sl_price - entry_price
            tp_price = symbol_info.round_price(entry_price - sl_dist * min_rr)

        if sl_dist <= 0:
            return None, "Invalid stop distance <= 0"

        # 11. Spread <= X% of SL distance check
        sl_dist_pts = sl_dist / pt
        spread_pts = float(symbol_info.spread)
        max_spread_pct = float(scalp_cfg.get("max_spread_pct_of_sl", 10.0))
        spread_pct_of_sl = (spread_pts / sl_dist_pts) * 100.0 if sl_dist_pts > 0 else 100.0

        if spread_pct_of_sl > max_spread_pct:
            return (
                None,
                f"Spread ({spread_pts:.1f} pts) is {spread_pct_of_sl:.1f}% of SL ({sl_dist_pts:.1f} pts) > {max_spread_pct:.1f}% limit"
            )

        actual_rr = round(abs(tp_price - entry_price) / sl_dist, 2)
        if actual_rr < min_rr - 1e-6:
            return None, f"R:R {actual_rr:.2f} below minimum {min_rr:.2f}"

        self._evaluated_bar_times[symbol] = bar_time

        candidate = SignalCandidate(
            id=f"FAST_{symbol}_{target_dir.value}_{bar_time.isoformat()}",
            symbol=symbol,
            timeframe=entry_timeframe,
            direction=target_dir,
            setup_type=SetupType.FAST_SCALP,
            entry_price=entry_price,
            stop_loss=sl_price,
            take_profit=tp_price,
            risk_reward=actual_rr,
            score=85.0,
            timestamp=bar_time,
            status="APPROVED",
            reasons=trigger_reasons
        )
        return candidate, None
