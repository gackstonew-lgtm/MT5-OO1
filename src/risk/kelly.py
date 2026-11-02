"""
Fractional Kelly Criterion Engine.
Derives mathematically sound position risk caps from out-of-sample trading edge.

Safety Invariants:
1. If win rate, reward-to-risk, or expectancy in R is non-positive (<= 0), Kelly is strictly 0.0 (trade skipped).
2. Fractional Kelly multiplier (e.g. 0.25x or 0.50x) is applied to prevent Kelly over-betting ruin.
3. Sized risk percentage is clamped to min(fractional_kelly, tier_target_risk, tier_absolute_ceiling).
   New risk logic can ONLY make the sizing more conservative, never exceed tier hard limits!
"""

from __future__ import annotations
import math
from dataclasses import dataclass
from typing import Tuple


@dataclass(frozen=True)
class KellyEvaluation:
    win_rate: float
    reward_to_risk: float
    expectancy_r: float
    full_kelly_pct: float
    fractional_kelly_pct: float
    applied_risk_pct: float
    fraction_used: float
    is_tradable: bool
    reason: str


class KellyEngine:
    def __init__(self, kelly_fraction: float = 0.25):
        """
        :param kelly_fraction: Default 0.25 (Quarter-Kelly) for optimal asymptotic growth
                               with minimal ruin risk under parameter uncertainty.
        """
        self.fraction = max(0.05, min(0.50, kelly_fraction))

    def evaluate(
        self,
        win_rate: float,
        reward_to_risk: float,
        tier_target_risk_pct: float,
        tier_ceiling_risk_pct: float
    ) -> KellyEvaluation:
        """
        Calculates fractional Kelly and clamps to tier safety limits.

        f* = p - (1 - p) / b = (p * (b + 1) - 1) / b
        where p = win_rate, b = reward_to_risk
        """
        # Edge cases and sanity guards
        if win_rate <= 0.0 or reward_to_risk <= 0.0:
            return KellyEvaluation(
                win_rate=win_rate,
                reward_to_risk=reward_to_risk,
                expectancy_r=0.0,
                full_kelly_pct=0.0,
                fractional_kelly_pct=0.0,
                applied_risk_pct=0.0,
                fraction_used=self.fraction,
                is_tradable=False,
                reason="Invalid win rate or reward-to-risk ratio."
            )

        # Expectancy in R: E[R] = p * b - (1 - p)
        expectancy_r = win_rate * reward_to_risk - (1.0 - win_rate)

        # Invariant 1: If expectancy is not strictly positive, Kelly is 0!
        if expectancy_r <= 0.0:
            return KellyEvaluation(
                win_rate=win_rate,
                reward_to_risk=reward_to_risk,
                expectancy_r=round(expectancy_r, 4),
                full_kelly_pct=0.0,
                fractional_kelly_pct=0.0,
                applied_risk_pct=0.0,
                fraction_used=self.fraction,
                is_tradable=False,
                reason=f"Non-positive expectancy in R ({expectancy_r:+.3f}R <= 0). Kelly sizing forbids trading."
            )

        # Full Kelly fraction of bankroll:
        full_kelly = (win_rate * (reward_to_risk + 1.0) - 1.0) / reward_to_risk
        full_kelly_pct = full_kelly * 100.0  # As percentage

        if full_kelly_pct <= 0.0:
            return KellyEvaluation(
                win_rate=win_rate,
                reward_to_risk=reward_to_risk,
                expectancy_r=round(expectancy_r, 4),
                full_kelly_pct=0.0,
                fractional_kelly_pct=0.0,
                applied_risk_pct=0.0,
                fraction_used=self.fraction,
                is_tradable=False,
                reason=f"Kelly criterion yields non-positive allocation ({full_kelly_pct:.2f}%)."
            )

        # Fractional Kelly (Quarter-Kelly)
        fractional_kelly_pct = full_kelly_pct * self.fraction

        # Invariant 2 & 3: Clamped to tier ceiling and tier target risk
        # The engine may ONLY restrict risk, never exceed tier target risk or ceiling!
        applied_risk_pct = min(fractional_kelly_pct, tier_target_risk_pct, tier_ceiling_risk_pct)

        reason = (
            f"Edge confirmed (E={expectancy_r:+.2f}R). Full Kelly: {full_kelly_pct:.2f}%, "
            f"{self.fraction:.2f}-Kelly: {fractional_kelly_pct:.2f}%. Applied risk: {applied_risk_pct:.2f}%."
        )

        return KellyEvaluation(
            win_rate=round(win_rate, 4),
            reward_to_risk=round(reward_to_risk, 3),
            expectancy_r=round(expectancy_r, 4),
            full_kelly_pct=round(full_kelly_pct, 3),
            fractional_kelly_pct=round(fractional_kelly_pct, 3),
            applied_risk_pct=round(applied_risk_pct, 3),
            fraction_used=self.fraction,
            is_tradable=True,
            reason=reason
        )
