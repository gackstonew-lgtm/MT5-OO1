"""
Context Scorer and Confluence Engine for Pattern Recognition.
Evaluates detected patterns against institutional SMC context:
- Order Block and FVG location
- HTF trend alignment (W1->D1->H4->H1)
- Market structure state (CHoCH -> BOS confirmation)
- Volume and momentum expansion
- Volatility, session killzones, and news proximity
- Conflict resolution and overlap de-duplication
"""

from __future__ import annotations
from typing import Dict, List, Optional, Any, Tuple
import numpy as np

from src.core.contracts import Candle
from src.patterns.contracts import (
    PatternResult, PatternFamily, PatternDirection, ScoredPattern
)


class PatternContextEngine:
    """
    Evaluates pattern confluence and grades trade quality from 0 to 100.
    """
    def __init__(self, config: Optional[Dict[str, Any]] = None):
        self.config = config or {}
        self.min_confluence_score = float(self.config.get("min_confluence_score", 65.0))
        self.require_confirmation_close = bool(self.config.get("require_confirmation_close", True))
        self.max_stacked_patterns = int(self.config.get("max_stacked_patterns", 2))
        self.weights = self.config.get("confluence_weights", {
            "location_poi_ob_fvg": 30.0,
            "htf_trend_alignment": 25.0,
            "structure_bos_confirmed": 20.0,
            "follow_through_confirmation": 10.0,
            "volume_momentum_expansion": 10.0,
            "volatility_session_regime": 5.0
        })

    def evaluate(
        self,
        patterns: List[PatternResult],
        current_candle: Candle,
        recent_candles: List[Candle],
        atr: float,
        mtf_bias: Optional[str] = None,
        active_obs: Optional[List[Any]] = None,
        active_fvgs: Optional[List[Any]] = None,
        liquidity_levels: Optional[Dict[str, float]] = None,
        struct_state: Optional[Any] = None,
        is_in_killzone: bool = True,
        news_safe: bool = True,
        min_rr: float = 2.0
    ) -> List[ScoredPattern]:
        """
        De-duplicates, resolves opposing conflicts, and scores candidate patterns.
        """
        if not patterns:
            return []

        # 1. Conflict Resolution (Opposing patterns on the same candle)
        bullish_patterns = [p for p in patterns if p.direction == PatternDirection.BULLISH]
        bearish_patterns = [p for p in patterns if p.direction == PatternDirection.BEARISH]

        if bullish_patterns and bearish_patterns:
            # Conflict detected: opposing signals trigger simultaneously -> reject both
            conflict_res = ScoredPattern(
                pattern=bullish_patterns[0],
                confluence_score=0.0,
                passed_gate=False,
                direction=PatternDirection.NEUTRAL,
                entry_price=current_candle.close,
                stop_loss=current_candle.close,
                take_profit=current_candle.close,
                risk_reward=0.0,
                confluence_breakdown={},
                reasons=[],
                rejection_reasons=[
                    f"Conflict: Simultaneous opposing patterns detected ({bullish_patterns[0].name} vs {bearish_patterns[0].name})"
                ]
            )
            return [conflict_res]

        # 2. De-duplication and selection of top patterns
        active_candidates = bullish_patterns if bullish_patterns else bearish_patterns
        if not active_candidates:
            return []

        # Sort by raw strength descending
        sorted_candidates = sorted(active_candidates, key=lambda p: p.raw_strength, reverse=True)
        selected_patterns = sorted_candidates[:self.max_stacked_patterns]

        scored_results: List[ScoredPattern] = []
        entry_price = current_candle.close

        for pattern in selected_patterns:
            breakdown: Dict[str, float] = {}
            reasons: List[str] = []
            rejection_reasons: List[str] = []

            # --- A. LOCATION (Max 30) ---
            loc_score = 0.0
            loc_max = self.weights.get("location_poi_ob_fvg", 30.0)

            # Check OB proximity
            if active_obs:
                for ob in active_obs:
                    ob_high = getattr(ob, "high", 0.0)
                    ob_low = getattr(ob, "low", 0.0)
                    if ob_low - 0.5 * atr <= entry_price <= ob_high + 0.5 * atr:
                        loc_score += 15.0
                        reasons.append(f"At Order Block [{ob_low:.5f} - {ob_high:.5f}]")
                        break

            # Check FVG proximity
            if active_fvgs:
                for fvg in active_fvgs:
                    fvg_top = getattr(fvg, "top", 0.0)
                    fvg_bot = getattr(fvg, "bottom", 0.0)
                    if fvg_bot - 0.5 * atr <= entry_price <= fvg_top + 0.5 * atr:
                        loc_score += 15.0
                        reasons.append(f"At Fair Value Gap [{fvg_bot:.5f} - {fvg_top:.5f}]")
                        break

            # Check Liquidity sweep (PWH, PWL, PDH, PDL)
            if liquidity_levels:
                for lvl_name, lvl_val in liquidity_levels.items():
                    if abs(entry_price - lvl_val) <= 0.8 * atr:
                        loc_score += 10.0
                        reasons.append(f"Near key liquidity level {lvl_name.upper()} ({lvl_val:.5f})")
                        break

            loc_score = min(loc_max, loc_score)
            breakdown["location"] = round(loc_score, 1)

            # --- B. HTF TREND ALIGNMENT (Max 25) ---
            htf_score = 0.0
            htf_max = self.weights.get("htf_trend_alignment", 25.0)
            pat_dir = pattern.direction.value

            if mtf_bias:
                bias_clean = mtf_bias.upper()
                if bias_clean == pat_dir:
                    htf_score = htf_max
                    reasons.append(f"Aligned with HTF Trend ({bias_clean})")
                elif bias_clean in ["RANGING", "NEUTRAL"]:
                    htf_score = htf_max * 0.5
                    reasons.append("HTF Trend is Ranging/Neutral")
                else:
                    htf_score = 0.0
                    rejection_reasons.append(f"Direct conflict with HTF Trend ({bias_clean})")
            else:
                htf_score = htf_max * 0.6  # Default neutral
            breakdown["htf_trend"] = round(htf_score, 1)

            # --- C. MARKET STRUCTURE & BOS CONFIRMATION (Max 20) ---
            struct_score = 0.0
            struct_max = self.weights.get("structure_bos_confirmed", 20.0)

            if struct_state is not None:
                state_str = str(getattr(struct_state, "state", ""))
                dir_str = str(getattr(struct_state, "direction", ""))
                if "BOS_CONFIRMED" in state_str and pat_dir in dir_str:
                    struct_score = struct_max
                    reasons.append("Structure State: BOS Confirmed in direction")
                elif "CHOCH_WARNING" in state_str:
                    struct_score = 5.0
                    reasons.append("Structure State: CHoCH Warning only (awaiting BOS)")
                    if self.require_confirmation_close:
                        rejection_reasons.append("CHoCH Warning only; BOS confirmation required")
                elif "FAILED_TRAP" in state_str:
                    struct_score = 0.0
                    rejection_reasons.append("Structure State: Trapped / Failed CHoCH")
                else:
                    struct_score = struct_max * 0.75
            else:
                struct_score = struct_max * 0.75
            breakdown["structure"] = round(struct_score, 1)

            # --- D. CONFIRMATION CANDLE CLOSE (Max 10) ---
            conf_score = 0.0
            conf_max = self.weights.get("follow_through_confirmation", 10.0)
            if pattern.confirmed:
                conf_score = conf_max
                reasons.append("Closed-bar breakout confirmation validated")
            else:
                conf_score = conf_max * 0.3
                if self.require_confirmation_close:
                    rejection_reasons.append("Pattern lacks candle-close confirmation")
            breakdown["confirmation"] = round(conf_score, 1)

            # --- E. VOLUME & MOMENTUM EXPANSION (Max 10) ---
            vol_score = 0.0
            vol_max = self.weights.get("volume_momentum_expansion", 10.0)
            if recent_candles and len(recent_candles) >= 10:
                vols = [getattr(c, "tick_volume", 0) for c in recent_candles[-20:]]
                avg_vol = float(np.mean(vols)) if vols else 1.0
                curr_vol = float(getattr(current_candle, "tick_volume", 0))
                if curr_vol >= 1.2 * avg_vol:
                    vol_score = vol_max
                    reasons.append(f"Volume expansion ({curr_vol:.0f} vs avg {avg_vol:.0f})")
                elif curr_vol >= 1.0 * avg_vol:
                    vol_score = vol_max * 0.7
                else:
                    vol_score = vol_max * 0.4
            else:
                vol_score = vol_max * 0.5
            breakdown["volume_momentum"] = round(vol_score, 1)

            # --- F. VOLATILITY, SESSION & NEWS (Max 5) ---
            regime_score = 0.0
            regime_max = self.weights.get("volatility_session_regime", 5.0)
            if is_in_killzone:
                regime_score += 3.0
                reasons.append("Active institutional Killzone (London/NY)")
            if news_safe:
                regime_score += 2.0
            else:
                rejection_reasons.append("High-impact news blackout active")
            regime_score = min(regime_max, regime_score)
            breakdown["regime_news"] = round(regime_score, 1)

            # --- G. STACKED CONFLUENCE SYNERGY BONUS ---
            if len(selected_patterns) > 1:
                breakdown["stack_bonus"] = 5.0
                reasons.append(f"Synergy bonus: {len(selected_patterns)} patterns co-aligned")

            total_score = sum(breakdown.values())
            total_score = min(100.0, total_score)

            # --- H. STOP LOSS & TAKE PROFIT DERIVATION ---
            sl_buffer = 0.2 * atr
            if pattern.direction == PatternDirection.BULLISH:
                inval = pattern.invalidation_price if pattern.invalidation_price > 0 else (current_candle.low - sl_buffer)
                stop_loss = min(current_candle.low, inval) - sl_buffer
                sl_dist = entry_price - stop_loss
                if sl_dist <= 0:
                    sl_dist = 1.0 * atr
                    stop_loss = entry_price - sl_dist

                # Target calculation
                if pattern.measured_target > entry_price and (pattern.measured_target - entry_price) / sl_dist >= min_rr:
                    take_profit = pattern.measured_target
                else:
                    take_profit = entry_price + (min_rr * sl_dist)
                rr = (take_profit - entry_price) / sl_dist

            else:  # BEARISH
                inval = pattern.invalidation_price if pattern.invalidation_price > 0 else (current_candle.high + sl_buffer)
                stop_loss = max(current_candle.high, inval) + sl_buffer
                sl_dist = stop_loss - entry_price
                if sl_dist <= 0:
                    sl_dist = 1.0 * atr
                    stop_loss = entry_price + sl_dist

                # Target calculation
                if 0 < pattern.measured_target < entry_price and (entry_price - pattern.measured_target) / sl_dist >= min_rr:
                    take_profit = pattern.measured_target
                else:
                    take_profit = entry_price - (min_rr * sl_dist)
                rr = (entry_price - take_profit) / sl_dist

            # Validation Gates
            if total_score < self.min_confluence_score:
                rejection_reasons.append(
                    f"Confluence score {total_score:.1f} is below minimum {self.min_confluence_score:.1f}"
                )
            if rr < min_rr:
                rejection_reasons.append(f"Risk-Reward {rr:.2f} is below required floor {min_rr:.2f}")

            passed = (total_score >= self.min_confluence_score and rr >= min_rr and len(rejection_reasons) == 0)

            scored_results.append(ScoredPattern(
                pattern=pattern,
                confluence_score=total_score,
                passed_gate=passed,
                direction=pattern.direction,
                entry_price=entry_price,
                stop_loss=stop_loss,
                take_profit=take_profit,
                risk_reward=rr,
                confluence_breakdown=breakdown,
                reasons=reasons,
                rejection_reasons=rejection_reasons
            ))

        return scored_results
