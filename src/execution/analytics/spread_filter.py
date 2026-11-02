"""
Time-of-Day Spread Model and Toxic Window Filter.
Prevents trading during illiquid rollover hours, news spikes, and wide-spread regimes.
"""

from __future__ import annotations
from dataclasses import dataclass
from typing import Dict, Optional, Tuple


@dataclass(frozen=True)
class SpreadCheckResult:
    allowed: bool
    current_spread_pts: float
    expected_spread_pts: float
    spread_ratio: float
    reason: Optional[str]


class SpreadFilter:
    # Typical baseline median spreads in broker points (from Stage 0 profiling)
    DEFAULT_BASELINE_SPREADS = {
        "EURUSD.m": 8.0,
        "GBPUSD.m": 12.0,
        "USDJPY.m": 11.0,
        "XAUUSD.m": 26.0,
        "BTCUSD.m": 850.0,
    }

    # High-spread rollover hours in UTC (JustMarkets-Demo3 / global rollover)
    ROLLOVER_HOURS_UTC = {21, 22, 23}

    def __init__(
        self,
        max_spread_multiplier: float = 2.0,
        allow_rollover_trading: bool = False
    ):
        self.max_spread_multiplier = max_spread_multiplier
        self.allow_rollover = allow_rollover_trading
        # Hourly spread profiles: symbol -> hour -> median spread pts
        self.hourly_medians: Dict[str, Dict[int, float]] = {}

    def set_hourly_median(self, symbol: str, hour: int, median_pts: float) -> None:
        if symbol not in self.hourly_medians:
            self.hourly_medians[symbol] = {}
        self.hourly_medians[symbol][hour] = max(1.0, median_pts)

    def evaluate_spread(
        self,
        symbol: str,
        current_spread_pts: float,
        hour_utc: int
    ) -> SpreadCheckResult:
        """
        Evaluates whether current spread is acceptable for trading.
        """
        # 1. Rollover check
        if hour_utc in self.ROLLOVER_HOURS_UTC and not self.allow_rollover:
            return SpreadCheckResult(
                allowed=False,
                current_spread_pts=current_spread_pts,
                expected_spread_pts=self.get_expected_spread(symbol, hour_utc),
                spread_ratio=99.0,
                reason=f"Illiquid rollover window active ({hour_utc:02d}:00 UTC). Trading forbidden."
            )

        expected = self.get_expected_spread(symbol, hour_utc)
        ratio = current_spread_pts / expected if expected > 0 else 1.0

        if ratio > self.max_spread_multiplier:
            return SpreadCheckResult(
                allowed=False,
                current_spread_pts=current_spread_pts,
                expected_spread_pts=expected,
                spread_ratio=round(ratio, 2),
                reason=(
                    f"Spread inflated ({current_spread_pts:.1f} pts is {ratio:.1f}x "
                    f"expected {expected:.1f} pts at {hour_utc:02d}:00 UTC). Trade skipped."
                )
            )

        return SpreadCheckResult(
            allowed=True,
            current_spread_pts=current_spread_pts,
            expected_spread_pts=expected,
            spread_ratio=round(ratio, 2),
            reason=None
        )

    def get_expected_spread(self, symbol: str, hour_utc: int) -> float:
        if symbol in self.hourly_medians and hour_utc in self.hourly_medians[symbol]:
            return self.hourly_medians[symbol][hour_utc]
        return self.DEFAULT_BASELINE_SPREADS.get(symbol, 20.0)
