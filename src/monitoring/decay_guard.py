"""
Strategy Decay Detection and CUSUM Performance Drift Engine.
Monitors live/demo trade sequence against historical out-of-sample distribution.

Alert Tiers:
- 2.0 Sigma: WARNING alert (log + Telegram notification)
- 2.5 Sigma: REDUCE RISK by 50%
- 3.0 Sigma: PAUSE TRADING (halt new entries, requires operator /run command to resume)
"""

from __future__ import annotations
import math
from dataclasses import dataclass
from typing import List, Optional, Tuple
import numpy as np


@dataclass(frozen=True)
class DecayCheckResult:
    status: str                 # 'NORMAL', 'WARNING', 'REDUCE_RISK', 'PAUSE'
    sigma_deviation: float      # Standardized deviation in sigmas
    current_expectancy_r: float # Live rolling expectancy in R
    baseline_expectancy_r: float
    current_win_rate: float
    baseline_win_rate: float
    cusum_value: float
    risk_multiplier: float      # 1.0, 0.5, or 0.0
    action_required: str


class DecayGuard:
    def __init__(
        self,
        baseline_win_rate: float = 0.50,
        baseline_expectancy_r: float = 0.40,
        baseline_std_r: float = 1.20,
        rolling_window_trades: int = 20,
        enabled: bool = False
    ):
        self.baseline_win_rate = baseline_win_rate
        self.baseline_expectancy_r = baseline_expectancy_r
        self.baseline_std_r = max(0.1, baseline_std_r)
        self.rolling_window = rolling_window_trades
        self.enabled = enabled

        # CUSUM state tracking
        self.cusum_negative: float = 0.0
        self.slack_k: float = 0.20  # Allowable deviation slack
        self.is_paused: bool = False
        self.trade_r_history: List[float] = []

    def reset_pause(self) -> None:
        """Called upon manual /run resumption from operator."""
        self.is_paused = False
        self.cusum_negative = 0.0

    def add_trade(self, return_in_r: float) -> None:
        """Records a new closed trade outcome in R-multiples."""
        self.trade_r_history.append(return_in_r)
        # Update CUSUM on negative deviation (detecting strategy decay)
        # S_t = max(0, S_{t-1} + (E_base - return_in_r) - k)
        dev = (self.baseline_expectancy_r - return_in_r) - self.slack_k
        self.cusum_negative = max(0.0, self.cusum_negative + dev)

    def evaluate(self) -> DecayCheckResult:
        """
        Evaluates the rolling trade history against the baseline distribution.
        """
        if len(self.trade_r_history) < 5:
            return DecayCheckResult(
                status="NORMAL",
                sigma_deviation=0.0,
                current_expectancy_r=self.baseline_expectancy_r,
                baseline_expectancy_r=self.baseline_expectancy_r,
                current_win_rate=self.baseline_win_rate,
                baseline_win_rate=self.baseline_win_rate,
                cusum_value=self.cusum_negative,
                risk_multiplier=1.0,
                action_required="Insufficient sample size for decay testing (<5 trades)."
            )

        window = self.trade_r_history[-self.rolling_window:]
        n = len(window)
        current_exp = float(np.mean(window))
        wins = sum(1 for r in window if r > 0)
        current_wr = float(wins / n)

        # Standard error of the mean expectancy: SE = sigma / sqrt(n)
        se_mean = self.baseline_std_r / math.sqrt(n)

        # Standardized Z-score deviation (positive means underperforming baseline)
        sigma_dev = (self.baseline_expectancy_r - current_exp) / se_mean

        # Determine Alert Tier
        if self.is_paused or sigma_dev >= 3.0:
            self.is_paused = True
            status = "PAUSE"
            mult = 0.0
            act = f"CRITICAL: Performance decayed by {sigma_dev:.2f} sigma (E={current_exp:+.2f}R vs base {self.baseline_expectancy_r:+.2f}R). TRADING PAUSED. Operator /run required to resume."
        elif sigma_dev >= 2.5:
            status = "REDUCE_RISK"
            mult = 0.50
            act = f"ALERT: Performance degraded by {sigma_dev:.2f} sigma. Risk automatically downscaled to 50%."
        elif sigma_dev >= 2.0:
            status = "WARNING"
            mult = 1.0
            act = f"WARNING: Mild underperformance detected ({sigma_dev:.2f} sigma). Monitoring closely."
        else:
            status = "NORMAL"
            mult = 1.0
            act = "Strategy performance in normal statistical bounds."

        return DecayCheckResult(
            status=status,
            sigma_deviation=round(sigma_dev, 2),
            current_expectancy_r=round(current_exp, 3),
            baseline_expectancy_r=round(self.baseline_expectancy_r, 3),
            current_win_rate=round(current_wr, 3),
            baseline_win_rate=round(self.baseline_win_rate, 3),
            cusum_value=round(self.cusum_negative, 3),
            risk_multiplier=mult,
            action_required=act
        )
