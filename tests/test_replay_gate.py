"""
Unit tests for Phase 3: Monte Carlo "Time Machine" Replay Gate.
"""

import unittest
from datetime import datetime, timezone, timedelta

from src.core.contracts import TradePlan, Candle, OrderType, Timeframe
from src.risk.replay_gate import MonteCarloReplayGate, ReplayGateResult


class TestMonteCarloReplayGate(unittest.TestCase):
    def setUp(self):
        self.now = datetime.now(timezone.utc)
        # Create synthetic M15 candles for testing
        self.bullish_candles = []
        price = 1.1000
        for i in range(150):
            t = self.now - timedelta(minutes=(150 - i) * 15)
            # Drift upwards with small volatility
            c_open = price
            c_high = price + 0.0008
            c_low = price - 0.0002
            c_close = price + 0.0005
            price = c_close
            self.bullish_candles.append(
                Candle(time=t, open=c_open, high=c_high, low=c_low, close=c_close, tick_volume=100)
            )

        # Flat / choppy candles (mean 0 drift)
        self.choppy_candles = []
        price = 1.1000
        for i in range(150):
            t = self.now - timedelta(minutes=(150 - i) * 15)
            delta = 0.0005 if (i % 2 == 0) else -0.0005
            c_open = price
            c_close = price + delta
            c_high = max(c_open, c_close) + 0.0003
            c_low = min(c_open, c_close) - 0.0003
            price = c_close
            self.choppy_candles.append(
                Candle(time=t, open=c_open, high=c_high, low=c_low, close=c_close, tick_volume=100)
            )

    def test_reproducibility_with_seed(self):
        """Verifies two evaluations of the same plan produce bit-for-bit identical probabilities."""
        gate = MonteCarloReplayGate(n_simulations=2000, enabled=True)
        plan = TradePlan(
            plan_id="PLAN_TEST",
            symbol="EURUSD",
            direction=OrderType.BUY,
            entry_price=1.1000,
            stop_loss=1.0970,
            take_profit=1.1060,
            lots=0.1,
            risk_percent=1.0,
            reward_to_risk=2.0,
            structure_state="BOS_CONFIRMED",
            timestamp=self.now
        )
        res1 = gate.evaluate_plan(plan, self.bullish_candles)
        res2 = gate.evaluate_plan(plan, self.bullish_candles)

        self.assertEqual(res1.prob_target, res2.prob_target)
        self.assertEqual(res1.prob_stop, res2.prob_stop)
        self.assertEqual(res1.expected_r, res2.expected_r)

    def test_clear_edge_accepted(self):
        """Verifies a favorable plan on bullish drift passes the replay gate."""
        gate = MonteCarloReplayGate(n_simulations=2000, min_edge=0.05, min_expected_r=0.10, enabled=True)
        # Entry in line with strong upward trend
        last_price = self.bullish_candles[-1].close
        plan = TradePlan(
            plan_id="PLAN_BULLISH",
            symbol="EURUSD",
            direction=OrderType.BUY,
            entry_price=last_price,
            stop_loss=last_price - 0.0030,
            take_profit=last_price + 0.0060,
            lots=0.1,
            risk_percent=1.0,
            reward_to_risk=2.0,
            structure_state="BOS_CONFIRMED",
            timestamp=self.now
        )
        res = gate.evaluate_plan(plan, self.bullish_candles)
        self.assertTrue(res.passed)
        self.assertGreater(res.prob_target, res.prob_stop)
        self.assertGreaterEqual(res.expected_r, 0.10)
        self.assertLess(res.runtime_ms, 2000.0)  # Capped runtime under 2s

    def test_coin_flip_rejected(self):
        """Verifies a near-coin-flip or unfavorable plan on choppy candles is rejected honestly."""
        gate = MonteCarloReplayGate(n_simulations=2000, min_edge=0.05, min_expected_r=0.15, enabled=True)
        last_price = self.choppy_candles[-1].close
        plan = TradePlan(
            plan_id="PLAN_CHOPPY",
            symbol="EURUSD",
            direction=OrderType.BUY,
            entry_price=last_price,
            stop_loss=last_price - 0.0020,
            take_profit=last_price + 0.0040,
            lots=0.1,
            risk_percent=1.0,
            reward_to_risk=2.0,
            structure_state="BOS_CONFIRMED",
            timestamp=self.now
        )
        res = gate.evaluate_plan(plan, self.choppy_candles)
        self.assertFalse(res.passed)
        self.assertIsNotNone(res.rejection_reason)
        self.assertIn("[REPLAY_GATE] REJECTED", res.log_line)

    def test_sample_too_small_rejected(self):
        """Verifies evaluation with fewer bars than min_sample_bars is rejected."""
        gate = MonteCarloReplayGate(min_sample_bars=100, enabled=True)
        plan = TradePlan(
            plan_id="PLAN_SMALL",
            symbol="EURUSD",
            direction=OrderType.BUY,
            entry_price=1.1000,
            stop_loss=1.0950,
            take_profit=1.1100,
            lots=0.1,
            risk_percent=1.0,
            reward_to_risk=2.0,
            structure_state="BOS_CONFIRMED",
            timestamp=self.now
        )
        res = gate.evaluate_plan(plan, self.bullish_candles[:50])
        self.assertFalse(res.passed)
        self.assertIn("sample too small", res.rejection_reason.lower())


if __name__ == "__main__":
    unittest.main()
