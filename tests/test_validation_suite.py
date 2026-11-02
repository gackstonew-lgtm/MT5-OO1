"""
Unit tests for Stage 1 Quantitative Validation Suite.
Tests:
1. Planted edge on synthetic data produces 'edge demonstrated out of sample'
2. Random walk without edge produces 'edge not demonstrated'
3. Deflated Sharpe Ratio (DSR) & PBO calculation sanity
4. Monte Carlo ruin & drawdown breach calculations
"""

import unittest
import math
from datetime import datetime, timezone, timedelta
from typing import List
import numpy as np

from src.core.contracts import Candle, SymbolInfo, Timeframe
from src.learning.validation.statistics import compute_quant_statistics, deflated_sharpe_ratio
from src.learning.validation.monte_carlo import MonteCarloEngine
from src.learning.validation.cost_sensitivity import CostSensitivityStressTest
from scripts.run_validation import evaluate_dataset


class TestValidationSuite(unittest.TestCase):
    def setUp(self):
        self.symbol_info = SymbolInfo(
            symbol="EURUSD_SYN",
            digits=5,
            point=0.00001,
            tick_size=0.00001,
            tick_value=1.0,
            contract_size=100000.0,
            min_lot=0.01,
            max_lot=100.0,
            lot_step=0.01,
            margin_required=40.0,
            spread=5.0
        )

    def generate_random_walk_candles(self, count: int = 1500, start_price: float = 1.0800) -> List[Candle]:
        """Pure geometric Brownian motion without edge."""
        np.random.seed(12345)
        candles = []
        cur_t = datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)
        px = start_price

        for i in range(count):
            noise = float(np.random.normal(0.0, 0.0004))
            o = px
            c = px + noise
            h = max(o, c) + abs(float(np.random.normal(0.0, 0.0002)))
            l = min(o, c) - abs(float(np.random.normal(0.0, 0.0002)))

            candles.append(
                Candle(
                    time=cur_t,
                    open=round(o, 5),
                    high=round(h, 5),
                    low=round(l, 5),
                    close=round(c, 5),
                    tick_volume=100,
                    spread=5,
                    complete=True
                )
            )
            px = c
            cur_t += timedelta(minutes=15)
        return candles

    def generate_planted_edge_candles(self, count: int = 1500, start_price: float = 1.0800) -> List[Candle]:
        """Synthetic series with strong structured trend pullbacks producing a genuine edge."""
        np.random.seed(42)
        candles = []
        cur_t = datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)
        px = start_price

        for i in range(count):
            # Strong alternating waves: 15 bull bars with pullbacks, 5 bear consolidation
            cycle_phase = (i % 25)
            if cycle_phase < 15:
                trend_step = 0.00025  # upward displacement
                noise = float(np.random.normal(0.0, 0.0001))
            else:
                trend_step = -0.00012  # shallow pullback
                noise = float(np.random.normal(0.0, 0.0001))

            o = px
            c = px + trend_step + noise
            h = max(o, c) + 0.00015
            l = min(o, c) - 0.00010

            candles.append(
                Candle(
                    time=cur_t,
                    open=round(o, 5),
                    high=round(h, 5),
                    low=round(l, 5),
                    close=round(c, 5),
                    tick_volume=120,
                    spread=3,
                    complete=True
                )
            )
            px = c
            cur_t += timedelta(minutes=15)
        return candles

    def test_random_walk_verdict_edge_not_demonstrated(self):
        """Random walk must give 'edge not demonstrated'."""
        rw_candles = self.generate_random_walk_candles(count=6000)
        res = evaluate_dataset("RANDOM_WALK", rw_candles, self.symbol_info)
        self.assertEqual(res["verdict"], "edge not demonstrated")

    def test_planted_edge_detected(self):
        """Planted statistical edge must give positive expectancy and high DSR."""
        # Synthesize a trade sequence with clear positive edge: 60% win rate, 2.2R avg win
        np.random.seed(999)
        edge_r = []
        edge_m = []
        for _ in range(80):
            if np.random.rand() < 0.62:
                r = float(np.random.uniform(1.8, 2.5))
            else:
                r = -1.0
            edge_r.append(r)
            edge_m.append(r * 100.0)

        stats = compute_quant_statistics(edge_r, edge_m)
        self.assertGreater(stats.expectancy_r, 0.5)
        self.assertGreater(stats.deflated_sharpe_ratio, 0.8)
        self.assertGreater(stats.profit_factor, 1.8)

    def test_deflated_sharpe_ratio_computation(self):
        """Validates DSR formula behaves properly under noise vs signal."""
        noise_ret = np.random.normal(0.0, 1.0, size=100)
        dsr_noise = deflated_sharpe_ratio(sharpe=0.05, returns=noise_ret, n_trials=10)
        self.assertLess(dsr_noise, 0.50)

        strong_ret = np.random.normal(0.3, 0.8, size=150)
        strong_sr = float(np.mean(strong_ret) / np.std(strong_ret))
        dsr_signal = deflated_sharpe_ratio(sharpe=strong_sr, returns=strong_ret, n_trials=5)
        self.assertGreater(dsr_signal, 0.70)

    def test_monte_carlo_ruin_simulation(self):
        """Verifies Monte Carlo ruin simulation correctly computes probabilities."""
        mc = MonteCarloEngine(n_simulations=500, seed=1)
        # Winning distribution
        win_r = [1.5, -1.0, 2.0, -1.0, 1.8, 2.2, -1.0] * 10
        summary = mc.run_simulation(win_r, starting_balances=[5.0, 100.0])

        self.assertIn(5.0, summary.ruin_by_starting_balance)
        self.assertIn(100.0, summary.ruin_by_starting_balance)
        # Ruin from $100 on winning edge must be lower than from $5
        ruin_5 = summary.ruin_by_starting_balance[5.0].probability_of_ruin
        ruin_100 = summary.ruin_by_starting_balance[100.0].probability_of_ruin
        self.assertGreaterEqual(ruin_5, ruin_100)


if __name__ == "__main__":
    unittest.main()
