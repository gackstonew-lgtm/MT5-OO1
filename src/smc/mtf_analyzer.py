"""
Multi-Timeframe Analysis Engine (MTF).
Orchestrates top-down institutional analysis across W1, D1, H4, H1, and M15.
Enforces hierarchical confluence and prevents lower-timeframe overrides.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Dict, List, Optional
from datetime import datetime, timezone

from src.core.contracts import (
    Candle, Timeframe, OrderType, TrendBias, WeeklyDailyLevels
)
from src.smc.market_structure import MarketStructureEngine
from src.smc.fvg_ob import FvgObEngine
from src.smc.weekly_daily import WeeklyDailyEngine, LiquidityInteractionResult


@dataclass
class TimeframeAnalysis:
    timeframe: Timeframe
    trend: TrendBias
    last_close: float
    swing_high: Optional[float] = None
    swing_low: Optional[float] = None
    is_premium: bool = False
    is_discount: bool = False
    fvg_count: int = 0
    ob_count: int = 0


@dataclass
class MultiTimeframeContext:
    symbol: str
    timestamp: datetime
    w1: TimeframeAnalysis
    d1: TimeframeAnalysis
    h4: TimeframeAnalysis
    h1: TimeframeAnalysis
    m15: TimeframeAnalysis
    overall_bias: TrendBias
    levels: Optional[WeeklyDailyLevels] = None
    level_interactions: List[LiquidityInteractionResult] = field(default_factory=list)
    alignment_score: float = 0.0  # 0 to 100


class MultiTimeframeAnalyzer:
    def __init__(
        self,
        struct_engine: Optional[MarketStructureEngine] = None,
        fvg_engine: Optional[FvgObEngine] = None,
        wd_engine: Optional[WeeklyDailyEngine] = None
    ):
        self.struct = struct_engine or MarketStructureEngine(swing_lookback=5)
        self.fvg = fvg_engine or FvgObEngine()
        self.wd = wd_engine or WeeklyDailyEngine()

    def analyze(
        self,
        symbol: str,
        w1_candles: List[Candle],
        d1_candles: List[Candle],
        h4_candles: List[Candle],
        h1_candles: List[Candle],
        m15_candles: List[Candle]
    ) -> MultiTimeframeContext:
        """
        Executes complete multi-timeframe analysis from W1 down to M15.
        Calculates directional alignment score.
        """
        now = datetime.now(timezone.utc)

        # 1. Weekly & Daily Levels
        levels = self.wd.calculate_levels(symbol, w1_candles, d1_candles)
        interactions = []
        if levels and m15_candles:
            interactions = self.wd.analyze_interaction(levels, m15_candles)

        # 2. Individual Timeframe Analysis
        w1_analysis = self._analyze_single_tf(symbol, Timeframe.W1, w1_candles)
        d1_analysis = self._analyze_single_tf(symbol, Timeframe.D1, d1_candles)
        h4_analysis = self._analyze_single_tf(symbol, Timeframe.H4, h4_candles)
        h1_analysis = self._analyze_single_tf(symbol, Timeframe.H1, h1_candles)
        m15_analysis = self._analyze_single_tf(symbol, Timeframe.M15, m15_candles)

        # 3. Overall Directional Bias derivation
        # H4 is the primary trend anchor, refined by H1 and Daily
        bullish_votes = 0
        bearish_votes = 0

        # Weights: W1=1, D1=2, H4=4, H1=2, M15=1
        eval_pairs = [
            (w1_analysis, 1),
            (d1_analysis, 2),
            (h4_analysis, 4),
            (h1_analysis, 2),
            (m15_analysis, 1)
        ]

        for tf_res, wt in eval_pairs:
            if tf_res.trend in [TrendBias.STRONG_BULLISH, TrendBias.WEAK_BULLISH]:
                bullish_votes += wt
            elif tf_res.trend in [TrendBias.STRONG_BEARISH, TrendBias.WEAK_BEARISH]:
                bearish_votes += wt

        total_weight = sum(wt for _, wt in eval_pairs)  # 10
        if bullish_votes >= 7:
            overall_bias = TrendBias.STRONG_BULLISH
        elif bullish_votes >= 5:
            overall_bias = TrendBias.WEAK_BULLISH
        elif bearish_votes >= 7:
            overall_bias = TrendBias.STRONG_BEARISH
        elif bearish_votes >= 5:
            overall_bias = TrendBias.WEAK_BEARISH
        else:
            overall_bias = TrendBias.RANGING

        # Alignment score (0-100)
        dominant_votes = max(bullish_votes, bearish_votes)
        alignment_score = (dominant_votes / total_weight) * 100.0

        return MultiTimeframeContext(
            symbol=symbol,
            timestamp=now,
            w1=w1_analysis,
            d1=d1_analysis,
            h4=h4_analysis,
            h1=h1_analysis,
            m15=m15_analysis,
            overall_bias=overall_bias,
            levels=levels,
            level_interactions=interactions,
            alignment_score=alignment_score
        )

    def _analyze_single_tf(self, symbol: str, tf: Timeframe, candles: List[Candle]) -> TimeframeAnalysis:
        if not candles:
            return TimeframeAnalysis(timeframe=tf, trend=TrendBias.RANGING, last_close=0.0)

        bias, _ = self.struct.analyze_structure(symbol, tf, candles)
        fvgs = self.fvg.detect_fvgs(symbol, tf, candles)
        obs = self.fvg.detect_order_blocks(symbol, tf, candles)

        sh, sl = self.struct.find_swing_points(candles)
        last_sh = sh[-1][1].high if sh else None
        last_sl = sl[-1][1].low if sl else None

        last_c = candles[-1].close
        is_prem = False
        is_disc = False
        if last_sh and last_sl and last_sh > last_sl:
            eq = (last_sh + last_sl) / 2.0
            is_prem = last_c > eq
            is_disc = last_c < eq

        return TimeframeAnalysis(
            timeframe=tf,
            trend=bias,
            last_close=last_c,
            swing_high=last_sh,
            swing_low=last_sl,
            is_premium=is_prem,
            is_discount=is_disc,
            fvg_count=len([f for f in fvgs if not f.mitigated]),
            ob_count=len([o for o in obs if not o.mitigated])
        )
