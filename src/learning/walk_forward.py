"""
Walk-Forward Validation, Monte Carlo Simulation, and Drift Monitoring.
Validates parameter robustness across rolling In-Sample (IS) and Out-of-Sample (OOS) windows,
and performs Monte Carlo trade shuffling to estimate tail risk and drawdown distributions.
"""

from __future__ import annotations
import random
from dataclasses import dataclass
from typing import List, Dict, Any, Tuple
import numpy as np


@dataclass
class MonteCarloResult:
    iterations: int
    median_max_drawdown_pct: float
    p95_max_drawdown_pct: float
    p99_max_drawdown_pct: float
    probability_of_drawdown_breach: float
    risk_of_ruin_pct: float


class WalkForwardValidator:
    def __init__(self, n_splits: int = 5, train_ratio: float = 0.70):
        self.n_splits = n_splits
        self.train_ratio = train_ratio

    def create_splits(self, total_candles_count: int) -> List[Tuple[Tuple[int, int], Tuple[int, int]]]:
        """
        Generates rolling In-Sample (train) and Out-Of-Sample (test) index slices.
        Returns: [((is_start, is_end), (oos_start, oos_end)), ...]
        """
        splits = []
        window_size = total_candles_count // self.n_splits
        if window_size < 50:
            return splits

        train_size = int(window_size * self.train_ratio)

        for i in range(self.n_splits):
            start = i * (window_size // 2)
            end = start + window_size
            if end > total_candles_count:
                break
            is_slice = (start, start + train_size)
            oos_slice = (start + train_size, end)
            splits.append((is_slice, oos_slice))

        return splits

    @staticmethod
    def run_monte_carlo_simulation(
        trade_returns: List[float],
        iterations: int = 1000,
        initial_capital: float = 10000.0,
        critical_drawdown_pct: float = 10.0
    ) -> MonteCarloResult:
        """
        Simulates random order permutations of historical trade outcomes.
        Calculates tail risk and drawdown percentiles (P95, P99).
        """
        if not trade_returns:
            return MonteCarloResult(iterations, 0.0, 0.0, 0.0, 0.0, 0.0)

        max_drawdowns: List[float] = []
        breach_count = 0
        ruin_count = 0

        for _ in range(iterations):
            # Shuffle returns
            shuffled = random.sample(trade_returns, len(trade_returns))
            capital = initial_capital
            peak = initial_capital
            max_dd = 0.0

            for r in shuffled:
                capital += r
                if capital > peak:
                    peak = capital
                dd = (peak - capital) / peak * 100.0 if peak > 0 else 0.0
                if dd > max_dd:
                    max_dd = dd
                if capital <= initial_capital * 0.50:  # 50% ruin threshold
                    ruin_count += 1
                    break

            max_drawdowns.append(max_dd)
            if max_dd >= critical_drawdown_pct:
                breach_count += 1

        median_dd = float(np.median(max_drawdowns))
        p95_dd = float(np.percentile(max_drawdowns, 95))
        p99_dd = float(np.percentile(max_drawdowns, 99))
        prob_breach = (breach_count / iterations) * 100.0
        risk_of_ruin = (ruin_count / iterations) * 100.0

        return MonteCarloResult(
            iterations=iterations,
            median_max_drawdown_pct=round(median_dd, 2),
            p95_max_drawdown_pct=round(p95_dd, 2),
            p99_max_drawdown_pct=round(p99_dd, 2),
            probability_of_drawdown_breach=round(prob_breach, 2),
            risk_of_ruin_pct=round(risk_of_ruin, 2)
        )
