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


class SmcStrategyEngine:
    def __init__(
        self,
        min_score: float = 75.0,
        min_rr: float = 2.0,
        pip_size: float = 0.0001
    ):
        self.min_score = min_score
        self.min_rr = min_rr
        self.pip_size = pip_size
        self.struct_engine = MarketStructureEngine(swing_lookback=5)
        self.fvg_engine = FvgObEngine(min_fvg_pips=1.5, pip_size=pip_size)

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
                is_in_killzone=is_in_killzone
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
                is_in_killzone=is_in_killzone
            )
            if candidate:
                candidates.append(candidate)

        return candidates

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
        is_in_killzone: bool
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

        # Scoring
        score, reasons, rejection_reasons = self._score_setup(
            direction=OrderType.BUY,
            setup_type=setup_type,
            mtf_ctx=mtf_ctx,
            m15_events=m15_events,
            best_fvg=best_fvg,
            best_ob=best_ob,
            is_in_killzone=is_in_killzone,
            rr=rr
        )

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
        is_in_killzone: bool
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

        score, reasons, rejection_reasons = self._score_setup(
            direction=OrderType.SELL,
            setup_type=setup_type,
            mtf_ctx=mtf_ctx,
            m15_events=m15_events,
            best_fvg=best_fvg,
            best_ob=best_ob,
            is_in_killzone=is_in_killzone,
            rr=rr
        )

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
        rr: float
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
