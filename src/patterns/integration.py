"""
Pattern Integration Adapter.
Connects Candlestick, Chart, and Harmonic detectors with the SMC Strategy Engine
and Execution Pipeline following the strangler pattern.
"""

from __future__ import annotations
import json
import os
from typing import Dict, List, Optional, Any, Tuple
from datetime import datetime, timezone

from src.core.contracts import (
    Candle, Timeframe, OrderType, SetupType, SignalCandidate, TradePlan
)
from src.patterns.contracts import (
    PatternResult, PatternFamily, PatternDirection, ScoredPattern
)
from src.patterns.candlesticks import CandlestickEngine, compute_atr
from src.patterns.chart_patterns import ChartPatternEngine
from src.patterns.context import PatternContextEngine


class PatternIntegrationAdapter:
    """
    Modular hook for integrating pattern-recognition signals and confluence
    into the core SMC Strategy Engine.
    """
    def __init__(self, config_path: str = "config/patterns_config.json"):
        self.config_path = config_path
        self.config = self._load_config()
        self.candlestick_engine = CandlestickEngine(self.config)
        self.chart_engine = ChartPatternEngine(self.config)
        self.context_engine = PatternContextEngine(self.config)

    def _load_config(self) -> Dict[str, Any]:
        if os.path.exists(self.config_path):
            try:
                with open(self.config_path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
        return {"enabled": False}

    def reload_config(self) -> None:
        self.config = self._load_config()
        self.candlestick_engine = CandlestickEngine(self.config)
        self.chart_engine = ChartPatternEngine(self.config)
        self.context_engine = PatternContextEngine(self.config)

    @property
    def is_enabled(self) -> bool:
        return bool(self.config.get("enabled", False))

    def evaluate_symbol_patterns(
        self,
        symbol: str,
        m15_candles: List[Candle],
        mtf_bias: Optional[str] = None,
        active_obs: Optional[List[Any]] = None,
        active_fvgs: Optional[List[Any]] = None,
        liquidity_levels: Optional[Dict[str, float]] = None,
        struct_state: Optional[Any] = None,
        is_in_killzone: bool = True,
        news_safe: bool = True,
        min_rr: float = 2.0
    ) -> List[SignalCandidate]:
        """
        Evaluates candle and chart patterns on closed M15 bars.
        Returns qualifying SignalCandidates if patterns are enabled and pass all gates.
        """
        if not self.is_enabled or len(m15_candles) < 15:
            return []

        current_candle = m15_candles[-1]
        atr = compute_atr(
            np_highs := [c.high for c in m15_candles],
            np_lows := [c.low for c in m15_candles],
            np_closes := [c.close for c in m15_candles]
        )

        # 1. Detect Candlestick Patterns
        candle_patterns = self.candlestick_engine.detect(m15_candles, atr=atr)

        # 2. Detect Chart & Harmonic Patterns
        chart_patterns = self.chart_engine.detect(m15_candles, atr=atr)

        all_patterns = candle_patterns + chart_patterns
        if not all_patterns:
            return []

        # 3. Grade Confluence & Context
        scored_patterns = self.context_engine.evaluate(
            patterns=all_patterns,
            current_candle=current_candle,
            recent_candles=m15_candles,
            atr=atr,
            mtf_bias=mtf_bias,
            active_obs=active_obs,
            active_fvgs=active_fvgs,
            liquidity_levels=liquidity_levels,
            struct_state=struct_state,
            is_in_killzone=is_in_killzone,
            news_safe=news_safe,
            min_rr=min_rr
        )

        candidates: List[SignalCandidate] = []
        for sp in scored_patterns:
            if not sp.passed_gate:
                continue

            order_type = OrderType.BUY if sp.direction == PatternDirection.BULLISH else OrderType.SELL
            candidate_id = f"{symbol}_PAT_{sp.pattern.name.replace(' ', '_').upper()}_{current_candle.time.isoformat()}"

            plan = TradePlan(
                plan_id=candidate_id,
                symbol=symbol,
                direction=order_type,
                entry_price=sp.entry_price,
                stop_loss=sp.stop_loss,
                take_profit=sp.take_profit,
                lots=0.0,
                risk_percent=1.0,
                reward_to_risk=sp.risk_reward,
                structure_state=f"PATTERN_{sp.pattern.name.upper()}",
                timestamp=current_candle.time,
                pattern_name=sp.pattern.name,
                pattern_family=sp.pattern.family.value,
                pattern_score=sp.confluence_score
            )

            candidates.append(SignalCandidate(
                id=candidate_id,
                symbol=symbol,
                timeframe=Timeframe.M15,
                direction=order_type,
                setup_type=SetupType.PATTERN_CONFLUENCE,
                entry_price=sp.entry_price,
                stop_loss=sp.stop_loss,
                take_profit=sp.take_profit,
                risk_reward=sp.risk_reward,
                score=sp.confluence_score,
                timestamp=current_candle.time,
                status="APPROVED",
                reasons=[f"Pattern: {sp.pattern.name} ({sp.pattern.family.value})"] + sp.reasons,
                rejection_reasons=sp.rejection_reasons,
                trade_plan=plan
            ))

        return candidates
