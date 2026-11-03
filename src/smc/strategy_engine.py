"""
ICT and Smart Money Concepts Strategy Engine.
Combines Weekly/Daily Liquidity models, Multi-Timeframe Bias,
Market Structure Shifts, FVG/OB entries, and Confluence Scoring (>= 75/100).
"""

from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional, Tuple, Dict, Any

from src.core.contracts import (
    Candle, Timeframe, OrderType, TrendBias, SetupType, SignalCandidate,
    FairValueGap, OrderBlock, MarketStructureType, WeeklyDailyLevels
)
from src.smc.mtf_analyzer import MultiTimeframeContext
from src.smc.market_structure import MarketStructureEngine
from src.smc.fvg_ob import FvgObEngine
from src.smc.weekly_daily import InteractionType
from src.smc.structure_confirmation import StructureStateMachine, ConfirmationState, CycleDirection, StructureStateSnapshot


class SmcStrategyEngine:
    def __init__(
        self,
        min_score: float = 75.0,
        min_rr: float = 2.0,
        pip_size: float = 0.0001,
        swing_lookback: int = 5,
        require_close_break: bool = True,
        require_lower_high: bool = True,
        choch_timeout_bars: int = 20,
        allow_choch_only_entries: bool = True,
        failed_choch_action: str = "RESET_AND_LOG_TRAP"
    ):
        self.min_score = min_score
        self.min_rr = min_rr
        self.pip_size = pip_size
        self.allow_choch_only_entries = allow_choch_only_entries
        self.struct_engine = MarketStructureEngine(swing_lookback=swing_lookback)
        self.fvg_engine = FvgObEngine(min_fvg_pips=1.5, pip_size=pip_size)
        self.pattern_adapter = None
        self.session_liq_engine = None
        self.session_liq_enabled = False
        try:
            import json, os
            cfg_p = "config/sessions.json"
            if os.path.exists(cfg_p):
                with open(cfg_p, "r", encoding="utf-8") as f:
                    s_cfg = json.load(f)
                    self.session_liq_enabled = bool(s_cfg.get("enabled", False))
            from src.smc.session_liquidity import SessionLiquidityEngine
            self.session_liq_engine = SessionLiquidityEngine(pip_multiplier=1.0 / pip_size if pip_size > 0 else 10000.0)
        except Exception:
            pass
        self.struct_confirmation = StructureStateMachine(
            swing_lookback=swing_lookback,
            require_close_break=require_close_break,
            require_lower_high=require_lower_high,
            choch_timeout_bars=choch_timeout_bars,
            allow_choch_only_entries=allow_choch_only_entries,
            failed_choch_action=failed_choch_action
        )

    def check_scalp_htf_agreement(
        self,
        mtf_ctx: MultiTimeframeContext
    ) -> Tuple[bool, str, Dict[str, str]]:
        """
        Evaluates whether M15, H1, and H4 directions all agree for SCALP mode.
        Returns: (all_agree: bool, log_message: str, directions: Dict[str, str])
        """
        def _bias_to_dir(bias: TrendBias) -> str:
            if bias in [TrendBias.STRONG_BULLISH, TrendBias.WEAK_BULLISH]:
                return "BULLISH"
            elif bias in [TrendBias.STRONG_BEARISH, TrendBias.WEAK_BEARISH]:
                return "BEARISH"
            return "RANGING"

        m15_dir = _bias_to_dir(mtf_ctx.m15.trend)
        h1_dir = _bias_to_dir(mtf_ctx.h1.trend)
        h4_dir = _bias_to_dir(mtf_ctx.h4.trend)

        directions = {"M15": m15_dir, "H1": h1_dir, "H4": h4_dir}

        if m15_dir != "RANGING" and m15_dir == h1_dir == h4_dir:
            return True, f"SCALP HTF agreement: All timeframes {m15_dir}", directions
        else:
            return False, f"SCALP HTF conflict (M15={m15_dir}, H1={h1_dir}, H4={h4_dir})", directions

    def scan_for_opportunities(
        self,
        mtf_ctx: MultiTimeframeContext,
        m15_candles: List[Candle],
        is_in_killzone: bool = True
    ) -> List[SignalCandidate]:
        """
        Scans M15 market data within the multi-timeframe context to identify,
        score, and validate institutional SMC trade setups.
        """
        candidates: List[SignalCandidate] = []
        if len(m15_candles) < 20:
            return candidates

        symbol = mtf_ctx.symbol
        last_candle = m15_candles[-1]
        current_price = last_candle.close

        # Update CHoCH-Warning -> BOS-Confirmation State Machine
        struct_state = self.struct_confirmation.update(symbol, Timeframe.M15, m15_candles)

        # Detect M15 structure, FVGs and OBs
        _, m15_events = self.struct_engine.analyze_structure(symbol, Timeframe.M15, m15_candles)
        m15_fvgs = self.fvg_engine.detect_fvgs(symbol, Timeframe.M15, m15_candles)
        m15_obs = self.fvg_engine.detect_order_blocks(symbol, Timeframe.M15, m15_candles)

        active_bullish_fvgs = [f for f in m15_fvgs if not f.mitigated and f.direction == OrderType.BUY]
        active_bearish_fvgs = [f for f in m15_fvgs if not f.mitigated and f.direction == OrderType.SELL]
        active_bullish_obs = [o for o in m15_obs if not o.mitigated and o.direction == OrderType.BUY]
        active_bearish_obs = [o for o in m15_obs if not o.mitigated and o.direction == OrderType.SELL]

        # Check for Weekly/Daily interactions
        interaction_types = [res.interaction for res in mtf_ctx.level_interactions]

        # Compute Asia range & check for London Judas sweeps (only when sessions enabled)
        asia_sweep = None
        if self.session_liq_engine and self.session_liq_enabled:
            asia_range = self.session_liq_engine.calculate_asia_range(symbol, m15_candles)
            if asia_range:
                asia_sweep = self.session_liq_engine.detect_london_asia_sweep(symbol, m15_candles, asia_range, pip_size=self.pip_size)

        # ------------------------------------------------------------------
        # Setup 1: BULLISH OPPORTUNITIES (Reversals & Continuations)
        # ------------------------------------------------------------------
        bullish_setup_type: Optional[SetupType] = None
        related_level = None

        if InteractionType.PWL_SWEEP in interaction_types:
            bullish_setup_type = SetupType.WEEKLY_REVERSAL
            related_level = "PWL_SWEEP"
        elif InteractionType.PDL_SWEEP in interaction_types:
            bullish_setup_type = SetupType.DAILY_REVERSAL
            related_level = "PDL_SWEEP"
        elif InteractionType.PWH_ACCEPTED_BREAK in interaction_types:
            bullish_setup_type = SetupType.WEEKLY_CONTINUATION
            related_level = "PWH_ACCEPTED_BREAK"
        elif InteractionType.PDH_ACCEPTED_BREAK in interaction_types:
            bullish_setup_type = SetupType.DAILY_CONTINUATION
            related_level = "PDH_ACCEPTED_BREAK"
        elif mtf_ctx.h4.trend in [TrendBias.STRONG_BULLISH, TrendBias.WEAK_BULLISH]:
            bullish_setup_type = SetupType.M15_SMC_OTE
            related_level = "H4_TREND_ALIGNMENT"
        elif asia_sweep and asia_sweep.direction_bias == OrderType.BUY:
            bullish_setup_type = SetupType.SESSION_SWEEP
            related_level = "ASIA_LOW_SWEEP"

        if bullish_setup_type is not None:
            candidate = self._evaluate_bullish_candidate(
                symbol=symbol,
                setup_type=bullish_setup_type,
                related_level=related_level,
                mtf_ctx=mtf_ctx,
                m15_candles=m15_candles,
                m15_events=m15_events,
                active_fvgs=active_bullish_fvgs,
                active_obs=active_bullish_obs,
                is_in_killzone=is_in_killzone,
                struct_state=struct_state
            )
            if candidate:
                candidates.append(candidate)

        # ------------------------------------------------------------------
        # Setup 2: BEARISH OPPORTUNITIES (Reversals & Continuations)
        # ------------------------------------------------------------------
        bearish_setup_type: Optional[SetupType] = None
        bear_related_level = None

        if InteractionType.PWH_SWEEP in interaction_types:
            bearish_setup_type = SetupType.WEEKLY_REVERSAL
            bear_related_level = "PWH_SWEEP"
        elif InteractionType.PDH_SWEEP in interaction_types:
            bearish_setup_type = SetupType.DAILY_REVERSAL
            bear_related_level = "PDH_SWEEP"
        elif InteractionType.PWL_ACCEPTED_BREAK in interaction_types:
            bearish_setup_type = SetupType.WEEKLY_CONTINUATION
            bear_related_level = "PWL_ACCEPTED_BREAK"
        elif InteractionType.PDL_ACCEPTED_BREAK in interaction_types:
            bearish_setup_type = SetupType.DAILY_CONTINUATION
            bear_related_level = "PDL_ACCEPTED_BREAK"
        elif mtf_ctx.h4.trend in [TrendBias.STRONG_BEARISH, TrendBias.WEAK_BEARISH]:
            bearish_setup_type = SetupType.M15_SMC_OTE
            bear_related_level = "H4_TREND_ALIGNMENT"
        elif asia_sweep and asia_sweep.direction_bias == OrderType.SELL:
            bearish_setup_type = SetupType.SESSION_SWEEP
            bear_related_level = "ASIA_HIGH_SWEEP"

        if bearish_setup_type is not None:
            candidate = self._evaluate_bearish_candidate(
                symbol=symbol,
                setup_type=bearish_setup_type,
                related_level=bear_related_level,
                mtf_ctx=mtf_ctx,
                m15_candles=m15_candles,
                m15_events=m15_events,
                active_fvgs=active_bearish_fvgs,
                active_obs=active_bearish_obs,
                is_in_killzone=is_in_killzone,
                struct_state=struct_state
            )
            if candidate:
                candidates.append(candidate)

        # Modular Pattern Confluence Hook
        pat_cands = self._evaluate_patterns_hook(
            symbol=symbol,
            mtf_ctx=mtf_ctx,
            m15_candles=m15_candles,
            active_obs=active_bullish_obs + active_bearish_obs,
            active_fvgs=active_bullish_fvgs + active_bearish_fvgs,
            struct_state=struct_state,
            is_in_killzone=is_in_killzone
        )
        if pat_cands:
            candidates.extend(pat_cands)

        return candidates

    def _evaluate_patterns_hook(
        self,
        symbol: str,
        mtf_ctx: MultiTimeframeContext,
        m15_candles: List[Candle],
        active_obs: List[OrderBlock],
        active_fvgs: List[FairValueGap],
        struct_state: Optional[StructureStateSnapshot],
        is_in_killzone: bool
    ) -> List[SignalCandidate]:
        if self.pattern_adapter is None:
            try:
                from src.patterns.integration import PatternIntegrationAdapter
                self.pattern_adapter = PatternIntegrationAdapter()
            except Exception:
                return []
        if not self.pattern_adapter.is_enabled:
            return []

        htf_bias = 'BULLISH' if mtf_ctx.h4.trend in [TrendBias.STRONG_BULLISH, TrendBias.WEAK_BULLISH] else                    'BEARISH' if mtf_ctx.h4.trend in [TrendBias.STRONG_BEARISH, TrendBias.WEAK_BEARISH] else 'RANGING'
        liq_levels = {}
        if mtf_ctx.levels:
            liq_levels = {
                'pwh': mtf_ctx.levels.pwh,
                'pwl': mtf_ctx.levels.pwl,
                'pdh': mtf_ctx.levels.pdh,
                'pdl': mtf_ctx.levels.pdl
            }
        return self.pattern_adapter.evaluate_symbol_patterns(
            symbol=symbol,
            m15_candles=m15_candles,
            mtf_bias=htf_bias,
            active_obs=active_obs,
            active_fvgs=active_fvgs,
            liquidity_levels=liq_levels,
            struct_state=struct_state,
            is_in_killzone=is_in_killzone,
            news_safe=True,
            min_rr=self.min_rr
        )

    def _evaluate_bullish_candidate(
        self,
        symbol: str,
        setup_type: SetupType,
        related_level: str,
        mtf_ctx: MultiTimeframeContext,
        m15_candles: List[Candle],
        m15_events: List,
        active_fvgs: List[FairValueGap],
        active_obs: List[OrderBlock],
        is_in_killzone: bool,
        struct_state: Optional[StructureStateSnapshot] = None
    ) -> Optional[SignalCandidate]:
        current_candle = m15_candles[-1]
        entry_price = current_candle.close

        # Invalidation / Stop Loss: below lowest point of the setup or order block low
        sh, sl = self.struct_engine.find_swing_points(m15_candles)
        recent_swing_low = sl[-1][1].low if sl else min(c.low for c in m15_candles[-10:])
        # If there's an active Bullish OB near entry, place SL below its low with 1 pip buffer
        sl_buffer = 2.0 * self.pip_size
        stop_loss = recent_swing_low - sl_buffer

        best_ob = active_obs[-1] if active_obs else None
        best_fvg = active_fvgs[-1] if active_fvgs else None
        if best_ob and best_ob.low < stop_loss:
            stop_loss = best_ob.low - sl_buffer

        sl_distance = entry_price - stop_loss
        if sl_distance <= 0:
            return None

        # Take Profit target: Next logical Buy-Side Liquidity (BSL), PDH, PWH, or recent swing high
        target_price = 0.0
        if mtf_ctx.levels:
            if mtf_ctx.levels.pdh > entry_price:
                target_price = mtf_ctx.levels.pdh
            elif mtf_ctx.levels.pwh > entry_price:
                target_price = mtf_ctx.levels.pwh

        if sh and target_price <= entry_price:
            target_price = sh[-1][1].high

        # Ensure target provides at least min_rr
        if (target_price - entry_price) / sl_distance < self.min_rr:
            target_price = entry_price + (self.min_rr * sl_distance)

        rr = (target_price - entry_price) / sl_distance

        # Structure Confirmation Gating
        if struct_state and not self.allow_choch_only_entries:
            if struct_state.direction == CycleDirection.BULLISH:
                if struct_state.state == ConfirmationState.CHOCH_WARNING:
                    rejection_reasons_init = ["CHoCH only, awaiting BOS"]
                elif struct_state.state == ConfirmationState.PULLBACK:
                    rejection_reasons_init = ["CHoCH pullback in progress, awaiting BOS"]
                elif struct_state.state == ConfirmationState.FAILED_TRAP:
                    rejection_reasons_init = ["CHoCH failed - trapped"]
                else:
                    rejection_reasons_init = []
            else:
                rejection_reasons_init = []
        else:
            rejection_reasons_init = []

        if struct_state and struct_state.state == ConfirmationState.BOS_CONFIRMED and struct_state.direction == CycleDirection.BULLISH:
            if struct_state.stop_loss_reference > 0:
                stop_loss = struct_state.stop_loss_reference - sl_buffer
                sl_distance = entry_price - stop_loss
                if sl_distance > 0:
                    rr = (target_price - entry_price) / sl_distance

        # Scoring
        score, reasons, rejection_reasons = self._score_setup(
            direction=OrderType.BUY,
            setup_type=setup_type,
            mtf_ctx=mtf_ctx,
            m15_events=m15_events,
            best_fvg=best_fvg,
            best_ob=best_ob,
            is_in_killzone=is_in_killzone,
            rr=rr,
            struct_state=struct_state
        )
        rejection_reasons.extend(rejection_reasons_init)

        status = "APPROVED" if score >= self.min_score and rr >= self.min_rr and not rejection_reasons else "REJECTED"

        return SignalCandidate(
            id=f"{symbol}_BUY_{current_candle.time.isoformat()}",
            symbol=symbol,
            timeframe=Timeframe.M15,
            direction=OrderType.BUY,
            setup_type=setup_type,
            entry_price=round(entry_price, 5),
            stop_loss=round(stop_loss, 5),
            take_profit=round(target_price, 5),
            risk_reward=round(rr, 2),
            score=round(score, 1),
            timestamp=current_candle.time,
            status=status,
            reasons=reasons,
            rejection_reasons=rejection_reasons,
            fvg=best_fvg,
            ob=best_ob,
            related_level=related_level
        )

    def _evaluate_bearish_candidate(
        self,
        symbol: str,
        setup_type: SetupType,
        related_level: str,
        mtf_ctx: MultiTimeframeContext,
        m15_candles: List[Candle],
        m15_events: List,
        active_fvgs: List[FairValueGap],
        active_obs: List[OrderBlock],
        is_in_killzone: bool,
        struct_state: Optional[StructureStateSnapshot] = None
    ) -> Optional[SignalCandidate]:
        current_candle = m15_candles[-1]
        entry_price = current_candle.close

        sh, sl = self.struct_engine.find_swing_points(m15_candles)
        recent_swing_high = sh[-1][1].high if sh else max(c.high for c in m15_candles[-10:])
        sl_buffer = 2.0 * self.pip_size
        stop_loss = recent_swing_high + sl_buffer

        best_ob = active_obs[-1] if active_obs else None
        best_fvg = active_fvgs[-1] if active_fvgs else None
        if best_ob and best_ob.high > stop_loss:
            stop_loss = best_ob.high + sl_buffer

        sl_distance = stop_loss - entry_price
        if sl_distance <= 0:
            return None

        # Take Profit target: Next logical Sell-Side Liquidity (SSL), PDL, PWL, or recent swing low
        target_price = 0.0
        if mtf_ctx.levels:
            if mtf_ctx.levels.pdl < entry_price:
                target_price = mtf_ctx.levels.pdl
            elif mtf_ctx.levels.pwl < entry_price:
                target_price = mtf_ctx.levels.pwl

        if sl and (target_price <= 0.0 or target_price >= entry_price):
            target_price = sl[-1][1].low

        if target_price <= 0.0 or target_price >= entry_price or (entry_price - target_price) / sl_distance < self.min_rr:
            target_price = entry_price - (self.min_rr * sl_distance)

        rr = (entry_price - target_price) / sl_distance

        # Structure Confirmation Gating
        if struct_state and not self.allow_choch_only_entries:
            if struct_state.direction == CycleDirection.BEARISH:
                if struct_state.state == ConfirmationState.CHOCH_WARNING:
                    rejection_reasons_init = ["CHoCH only, awaiting BOS"]
                elif struct_state.state == ConfirmationState.PULLBACK:
                    rejection_reasons_init = ["CHoCH pullback in progress, awaiting BOS"]
                elif struct_state.state == ConfirmationState.FAILED_TRAP:
                    rejection_reasons_init = ["CHoCH failed - trapped"]
                else:
                    rejection_reasons_init = []
            else:
                rejection_reasons_init = []
        else:
            rejection_reasons_init = []

        if struct_state and struct_state.state == ConfirmationState.BOS_CONFIRMED and struct_state.direction == CycleDirection.BEARISH:
            if struct_state.stop_loss_reference > 0:
                stop_loss = struct_state.stop_loss_reference + sl_buffer
                sl_distance = stop_loss - entry_price
                if sl_distance > 0:
                    rr = (entry_price - target_price) / sl_distance

        score, reasons, rejection_reasons = self._score_setup(
            direction=OrderType.SELL,
            setup_type=setup_type,
            mtf_ctx=mtf_ctx,
            m15_events=m15_events,
            best_fvg=best_fvg,
            best_ob=best_ob,
            is_in_killzone=is_in_killzone,
            rr=rr,
            struct_state=struct_state
        )
        rejection_reasons.extend(rejection_reasons_init)

        status = "APPROVED" if score >= self.min_score and rr >= self.min_rr and not rejection_reasons else "REJECTED"

        return SignalCandidate(
            id=f"{symbol}_SELL_{current_candle.time.isoformat()}",
            symbol=symbol,
            timeframe=Timeframe.M15,
            direction=OrderType.SELL,
            setup_type=setup_type,
            entry_price=round(entry_price, 5),
            stop_loss=round(stop_loss, 5),
            take_profit=round(target_price, 5),
            risk_reward=round(rr, 2),
            score=round(score, 1),
            timestamp=current_candle.time,
            status=status,
            reasons=reasons,
            rejection_reasons=rejection_reasons,
            fvg=best_fvg,
            ob=best_ob,
            related_level=related_level
        )

    def _score_setup(
        self,
        direction: OrderType,
        setup_type: SetupType,
        mtf_ctx: MultiTimeframeContext,
        m15_events: List,
        best_fvg: Optional[FairValueGap],
        best_ob: Optional[OrderBlock],
        is_in_killzone: bool,
        rr: float,
        struct_state: Optional[StructureStateSnapshot] = None
    ) -> Tuple[float, List[str], List[str]]:
        """
        Calculates confluence score (0-100) and checks hard disqualifiers.
        """
        score = 0.0
        reasons = []
        rejection_reasons = []

        is_bullish = direction == OrderType.BUY

        # 1. Weekly Alignment (15 pts)
        w1_aligned = (is_bullish and mtf_ctx.w1.trend in [TrendBias.STRONG_BULLISH, TrendBias.WEAK_BULLISH]) or \
                     (not is_bullish and mtf_ctx.w1.trend in [TrendBias.STRONG_BEARISH, TrendBias.WEAK_BEARISH])
        if w1_aligned:
            score += 15
            reasons.append("W1 trend aligned (+15)")
        elif setup_type in [SetupType.WEEKLY_REVERSAL, SetupType.DAILY_REVERSAL]:
            score += 10
            reasons.append("Reversal at key liquidity boundary (+10)")

        # 2. Daily Alignment (15 pts)
        d1_aligned = (is_bullish and mtf_ctx.d1.trend in [TrendBias.STRONG_BULLISH, TrendBias.WEAK_BULLISH]) or \
                     (not is_bullish and mtf_ctx.d1.trend in [TrendBias.STRONG_BEARISH, TrendBias.WEAK_BEARISH])
        if d1_aligned:
            score += 15
            reasons.append("D1 directional bias aligned (+15)")

        # 3. H4 Primary Trend (15 pts)
        h4_aligned = (is_bullish and mtf_ctx.h4.trend in [TrendBias.STRONG_BULLISH, TrendBias.WEAK_BULLISH]) or \
                     (not is_bullish and mtf_ctx.h4.trend in [TrendBias.STRONG_BEARISH, TrendBias.WEAK_BEARISH])
        if h4_aligned:
            score += 15
            reasons.append("H4 primary trend aligned (+15)")

        # 4. H1 Confluence (10 pts)
        h1_aligned = (is_bullish and mtf_ctx.h1.trend in [TrendBias.STRONG_BULLISH, TrendBias.WEAK_BULLISH]) or \
                     (not is_bullish and mtf_ctx.h1.trend in [TrendBias.STRONG_BEARISH, TrendBias.WEAK_BEARISH])
        if h1_aligned:
            score += 10
            reasons.append("H1 confluence aligned (+10)")

        # 5. M15 Structure Confirmation (MSS / CHoCH by candle close) (15 pts)
        mss_confirmed = any(e.direction == direction and e.event_type in [MarketStructureType.MSS, MarketStructureType.CHOCH] for e in m15_events)
        if mss_confirmed:
            score += 15
            reasons.append("M15 MSS/CHoCH confirmed by candle close (+15)")
        else:
            reasons.append("M15 structure unconfirmed (0)")

        # 6. Liquidity Sweep Interaction (10 pts)
        has_sweep = any("SWEEP" in res.interaction.value for res in mtf_ctx.level_interactions)
        if has_sweep or setup_type in [SetupType.WEEKLY_REVERSAL, SetupType.DAILY_REVERSAL]:
            score += 10
            reasons.append("Liquidity sweep confirmed with rejection (+10)")

        # 7. FVG Quality (10 pts)
        if best_fvg:
            score += 10
            reasons.append(f"Valid M15 FVG imbalance present ({best_fvg.id}) (+10)")

        # 8. Order Block Quality (5 pts)
        if best_ob:
            score += 5
            reasons.append(f"Institutional Order Block validated ({best_ob.id}) (+5)")

        # 9. Session / Killzone (5 pts)
        if is_in_killzone:
            score += 5
            reasons.append("Formed within London or New York Kill Zone (+5)")

        # Mandatory Hard Filters
        if rr < self.min_rr:
            rejection_reasons.append(f"Reward-to-risk {rr:.2f} is below mandatory threshold {self.min_rr:.2f}")

        if score < self.min_score:
            rejection_reasons.append(f"Confluence score {score:.1f} is below minimum {self.min_score:.1f}")

        return score, reasons, rejection_reasons
