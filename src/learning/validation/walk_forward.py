"""
Purged & Embargoed Walk-Forward Splitting & Evaluation Engine.
Implements non-overlapping train/test partitions with purging and embargo gaps
to prevent look-ahead bias and serial correlation leakage (Lopez de Prado, 2018).
"""

from __future__ import annotations
import math
from dataclasses import dataclass, field
from typing import List, Dict, Optional, Tuple, Any
import numpy as np

from .statistics import QuantitativeStats, compute_quant_statistics


@dataclass
class WalkForwardWindow:
    window_id: int
    train_start_idx: int
    train_end_idx: int
    test_start_idx: int
    test_end_idx: int
    in_sample_trades: int
    out_of_sample_trades: int
    in_sample_expectancy_r: float
    out_of_sample_expectancy_r: float
    in_sample_sharpe: float
    out_of_sample_sharpe: float
    out_of_sample_win_rate: float
    out_of_sample_profit_factor: float
    out_of_sample_max_dd_pct: float

    def to_dict(self) -> Dict[str, Any]:
        return {
            "window_id": self.window_id,
            "in_sample_trades": self.in_sample_trades,
            "out_of_sample_trades": self.out_of_sample_trades,
            "in_sample_expectancy_r": round(self.in_sample_expectancy_r, 3),
            "out_of_sample_expectancy_r": round(self.out_of_sample_expectancy_r, 3),
            "in_sample_sharpe": round(self.in_sample_sharpe, 3),
            "out_of_sample_sharpe": round(self.out_of_sample_sharpe, 3),
            "out_of_sample_win_rate": round(self.out_of_sample_win_rate, 4),
            "out_of_sample_profit_factor": round(self.out_of_sample_profit_factor, 3),
            "out_of_sample_max_dd_pct": round(self.out_of_sample_max_dd_pct, 2),
        }


class WalkForwardEngine:
    def __init__(
        self,
        n_splits: int = 5,
        train_ratio: float = 0.60,
        embargo_pct: float = 0.05
    ):
        self.n_splits = n_splits
        self.train_ratio = train_ratio
        self.embargo_pct = embargo_pct

    def split_indices(self, total_samples: int) -> List[Tuple[Tuple[int, int], Tuple[int, int]]]:
        """
        Generates purged and embargoed train and test index spans.
        Returns: list of ((train_start, train_end), (test_start, test_end))
        """
        if total_samples < 20:
            # Fallback single 70/30 split
            split_pt = int(total_samples * 0.7)
            return [((0, split_pt), (split_pt, total_samples))]

        windows = []
        step_size = int(total_samples / (self.n_splits + 1))
        train_len = int(step_size * (self.train_ratio / (1.0 - self.train_ratio + 1e-6)))
        train_len = max(10, min(int(total_samples * 0.5), train_len))
        embargo_gap = max(1, int(train_len * self.embargo_pct))

        for w in range(self.n_splits):
            train_start = w * step_size
            train_end = train_start + train_len
            test_start = train_end + embargo_gap
            test_end = test_start + step_size

            if test_end > total_samples:
                test_end = total_samples
                if test_start >= test_end:
                    break

            windows.append(((train_start, train_end), (test_start, test_end)))

        return windows

    def evaluate_walk_forward(
        self,
        trades_r: List[float],
        trades_money: List[float]
    ) -> List[WalkForwardWindow]:
        """Runs walk-forward analysis over historical trade outcomes."""
        n = len(trades_r)
        if n < 10:
            return []

        spans = self.split_indices(n)
        results: List[WalkForwardWindow] = []

        for w_idx, ((tr_s, tr_e), (te_s, te_e)) in enumerate(spans):
            tr_r = trades_r[tr_s:tr_e]
            tr_m = trades_money[tr_s:tr_e]
            te_r = trades_r[te_s:te_e]
            te_m = trades_money[te_s:te_e]

            is_stats = compute_quant_statistics(tr_r, tr_m)
            oos_stats = compute_quant_statistics(te_r, te_m)

            results.append(
                WalkForwardWindow(
                    window_id=w_idx + 1,
                    train_start_idx=tr_s,
                    train_end_idx=tr_e,
                    test_start_idx=te_s,
                    test_end_idx=te_e,
                    in_sample_trades=len(tr_r),
                    out_of_sample_trades=len(te_r),
                    in_sample_expectancy_r=is_stats.expectancy_r,
                    out_of_sample_expectancy_r=oos_stats.expectancy_r,
                    in_sample_sharpe=is_stats.sharpe_ratio,
                    out_of_sample_sharpe=oos_stats.sharpe_ratio,
                    out_of_sample_win_rate=oos_stats.win_rate,
                    out_of_sample_profit_factor=oos_stats.profit_factor,
                    out_of_sample_max_dd_pct=oos_stats.max_drawdown_pct
                )
            )

        return results
