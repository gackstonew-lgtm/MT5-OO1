"""
Weekly and Daily Liquidity Analysis Engine.
Calculates PWH, PWL, PWO, PWC, PWM, PDH, PDL, PDO, PDC, PDM.
Classifies price interactions (Liquidity Sweeps, Accepted Breakouts, Retests).
Generates structural context for reversal and continuation trading models.
"""

from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import List, Optional, Tuple, Dict, Any
from enum import Enum

from src.core.contracts import Candle, Timeframe, WeeklyDailyLevels, OrderType, SetupType


class InteractionType(str, Enum):
    NEUTRAL = "NEUTRAL"
    PWH_SWEEP = "PWH_SWEEP"                  # Bearish Reversal Setup candidate
    PWL_SWEEP = "PWL_SWEEP"                  # Bullish Reversal Setup candidate
    PWH_ACCEPTED_BREAK = "PWH_ACCEPTED_BREAK"# Bullish Continuation Setup candidate
    PWL_ACCEPTED_BREAK = "PWL_ACCEPTED_BREAK"# Bearish Continuation Setup candidate
    PDH_SWEEP = "PDH_SWEEP"                  # Bearish Reversal Setup candidate
    PDL_SWEEP = "PDL_SWEEP"                  # Bullish Reversal Setup candidate
    PDH_ACCEPTED_BREAK = "PDH_ACCEPTED_BREAK"# Bullish Continuation Setup candidate
    PDL_ACCEPTED_BREAK = "PDL_ACCEPTED_BREAK"# Bearish Continuation Setup candidate
    PDH_RETEST_SUPPORT = "PDH_RETEST_SUPPORT"# Broken PDH holding as support
    PDL_RETEST_RESISTANCE = "PDL_RETEST_RESISTANCE" # Broken PDL holding as resistance


@dataclass
class LiquidityInteractionResult:
    interaction: InteractionType
    level_name: str
    level_price: float
    direction_bias: Optional[OrderType]
    sweep_distance_pips: float
    confirmed: bool
    description: str


class WeeklyDailyEngine:
    def __init__(self, pip_multiplier: float = 10000.0):
        self.pip_multiplier = pip_multiplier

    def calculate_levels(
        self,
        symbol: str,
        weekly_candles: List[Candle],
        daily_candles: List[Candle]
    ) -> Optional[WeeklyDailyLevels]:
        """
        Calculates weekly and daily reference levels using completed candles.
        Requires at least 2 completed weekly candles and 2 completed daily candles.
        Index -1 is the current (uncompleted/in-progress or latest completed) candle,
        Index -2 is the completed previous candle.
        """
        if len(weekly_candles) < 2 or len(daily_candles) < 2:
            return None

        # Weekly candles: previous completed is [-2], current forming is [-1]
        prev_w = weekly_candles[-2]
        curr_w = weekly_candles[-1]

        pwh = prev_w.high
        pwl = prev_w.low
        pwo = prev_w.open
        pwc = prev_w.close
        pwm = round((pwh + pwl) / 2.0, 5)

        cwo = curr_w.open
        cwh = curr_w.high
        cwl = curr_w.low

        # Daily candles: previous completed is [-2], current forming is [-1]
        prev_d = daily_candles[-2]
        curr_d = daily_candles[-1]

        pdh = prev_d.high
        pdl = prev_d.low
        pdo = prev_d.open
        pdc = prev_d.close
        pdm = round((pdh + pdl) / 2.0, 5)

        cdo = curr_d.open
        cdh = curr_d.high
        cdl = curr_d.low

        return WeeklyDailyLevels(
            symbol=symbol,
            pwh=pwh,
            pwl=pwl,
            pwo=pwo,
            pwc=pwc,
            pwm=pwm,
            cwo=cwo,
            cwh=cwh,
            cwl=cwl,
            pdh=pdh,
            pdl=pdl,
            pdo=pdo,
            pdc=pdc,
            pdm=pdm,
            cdo=cdo,
            cdh=cdh,
            cdl=cdl,
            timestamp=datetime.now(timezone.utc)
        )

    def analyze_interaction(
        self,
        levels: WeeklyDailyLevels,
        recent_m15_candles: List[Candle],
        buffer_pips: float = 2.0
    ) -> List[LiquidityInteractionResult]:
        """
        Analyzes M15 candle interactions with Weekly and Daily high/low levels.
        Distinguishes between:
        1. Liquidity Sweeps (false breaks with rejection candle)
        2. Accepted Breakouts (candle body closes decisively beyond level)
        3. Retest of broken levels (support/resistance flip)
        """
        results: List[LiquidityInteractionResult] = []
        if not recent_m15_candles or len(recent_m15_candles) < 3:
            return results

        last_candle = recent_m15_candles[-1]
        prev_candle = recent_m15_candles[-2]
        buffer_val = buffer_pips / self.pip_multiplier

        # Check interaction against each key level:
        # 1. PWH Interaction
        if last_candle.high >= levels.pwh or prev_candle.high >= levels.pwh:
            # Sweep if price pierced PWH but closed back below PWH
            if last_candle.close < levels.pwh and (last_candle.high > levels.pwh or prev_candle.high > levels.pwh):
                sweep_dist = (max(last_candle.high, prev_candle.high) - levels.pwh) * self.pip_multiplier
                # Rejection confirmation: bearish body or significant upper wick
                is_rejection = last_candle.is_bearish or (last_candle.upper_wick > last_candle.body)
                results.append(
                    LiquidityInteractionResult(
                        interaction=InteractionType.PWH_SWEEP,
                        level_name="PWH",
                        level_price=levels.pwh,
                        direction_bias=OrderType.SELL,
                        sweep_distance_pips=sweep_dist,
                        confirmed=is_rejection,
                        description=f"PWH liquidity swept at {levels.pwh:.5f} with bearish rejection"
                    )
                )
            # Accepted breakout if candle closed above PWH with displacement
            elif last_candle.close > levels.pwh + buffer_val and last_candle.is_bullish:
                results.append(
                    LiquidityInteractionResult(
                        interaction=InteractionType.PWH_ACCEPTED_BREAK,
                        level_name="PWH",
                        level_price=levels.pwh,
                        direction_bias=OrderType.BUY,
                        sweep_distance_pips=0.0,
                        confirmed=True,
                        description=f"PWH breakout accepted above {levels.pwh:.5f} by candle close"
                    )
                )

        # 2. PWL Interaction
        if last_candle.low <= levels.pwl or prev_candle.low <= levels.pwl:
            if last_candle.close > levels.pwl and (last_candle.low < levels.pwl or prev_candle.low < levels.pwl):
                sweep_dist = (levels.pwl - min(last_candle.low, prev_candle.low)) * self.pip_multiplier
                is_rejection = last_candle.is_bullish or (last_candle.lower_wick > last_candle.body)
                results.append(
                    LiquidityInteractionResult(
                        interaction=InteractionType.PWL_SWEEP,
                        level_name="PWL",
                        level_price=levels.pwl,
                        direction_bias=OrderType.BUY,
                        sweep_distance_pips=sweep_dist,
                        confirmed=is_rejection,
                        description=f"PWL liquidity swept at {levels.pwl:.5f} with bullish rejection"
                    )
                )
            elif last_candle.close < levels.pwl - buffer_val and last_candle.is_bearish:
                results.append(
                    LiquidityInteractionResult(
                        interaction=InteractionType.PWL_ACCEPTED_BREAK,
                        level_name="PWL",
                        level_price=levels.pwl,
                        direction_bias=OrderType.SELL,
                        sweep_distance_pips=0.0,
                        confirmed=True,
                        description=f"PWL breakout accepted below {levels.pwl:.5f} by candle close"
                    )
                )

        # 3. PDH Interaction
        if last_candle.high >= levels.pdh or prev_candle.high >= levels.pdh:
            if last_candle.close < levels.pdh and (last_candle.high > levels.pdh or prev_candle.high > levels.pdh):
                sweep_dist = (max(last_candle.high, prev_candle.high) - levels.pdh) * self.pip_multiplier
                is_rejection = last_candle.is_bearish or (last_candle.upper_wick > last_candle.body)
                results.append(
                    LiquidityInteractionResult(
                        interaction=InteractionType.PDH_SWEEP,
                        level_name="PDH",
                        level_price=levels.pdh,
                        direction_bias=OrderType.SELL,
                        sweep_distance_pips=sweep_dist,
                        confirmed=is_rejection,
                        description=f"PDH liquidity swept at {levels.pdh:.5f} with bearish rejection"
                    )
                )
            elif last_candle.close > levels.pdh + buffer_val and last_candle.is_bullish:
                results.append(
                    LiquidityInteractionResult(
                        interaction=InteractionType.PDH_ACCEPTED_BREAK,
                        level_name="PDH",
                        level_price=levels.pdh,
                        direction_bias=OrderType.BUY,
                        sweep_distance_pips=0.0,
                        confirmed=True,
                        description=f"PDH breakout accepted above {levels.pdh:.5f}"
                    )
                )

        # 4. PDL Interaction
        if last_candle.low <= levels.pdl or prev_candle.low <= levels.pdl:
            if last_candle.close > levels.pdl and (last_candle.low < levels.pdl or prev_candle.low < levels.pdl):
                sweep_dist = (levels.pdl - min(last_candle.low, prev_candle.low)) * self.pip_multiplier
                is_rejection = last_candle.is_bullish or (last_candle.lower_wick > last_candle.body)
                results.append(
                    LiquidityInteractionResult(
                        interaction=InteractionType.PDL_SWEEP,
                        level_name="PDL",
                        level_price=levels.pdl,
                        direction_bias=OrderType.BUY,
                        sweep_distance_pips=sweep_dist,
                        confirmed=is_rejection,
                        description=f"PDL liquidity swept at {levels.pdl:.5f} with bullish rejection"
                    )
                )
            elif last_candle.close < levels.pdl - buffer_val and last_candle.is_bearish:
                results.append(
                    LiquidityInteractionResult(
                        interaction=InteractionType.PDL_ACCEPTED_BREAK,
                        level_name="PDL",
                        level_price=levels.pdl,
                        direction_bias=OrderType.SELL,
                        sweep_distance_pips=0.0,
                        confirmed=True,
                        description=f"PDL breakout accepted below {levels.pdl:.5f}"
                    )
                )

        return results
