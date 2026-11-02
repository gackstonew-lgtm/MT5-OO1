"""
Unit tests for Weekly and Daily Liquidity Engine.
"""

import unittest
from datetime import datetime, timezone, timedelta
from src.core.contracts import Candle, OrderType
from src.smc.weekly_daily import WeeklyDailyEngine, InteractionType


class TestWeeklyDailyEngine(unittest.TestCase):
    def setUp(self):
        self.engine = WeeklyDailyEngine(pip_multiplier=10000.0)
        self.base_time = datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)

    def test_calculate_levels(self):
        # Create 2 weekly candles
        w1 = Candle(time=self.base_time, open=1.0800, high=1.0950, low=1.0750, close=1.0900)
        w2 = Candle(time=self.base_time + timedelta(days=7), open=1.0900, high=1.1000, low=1.0850, close=1.0920)

        # Create 2 daily candles
        d1 = Candle(time=self.base_time, open=1.0850, high=1.0920, low=1.0810, close=1.0900)
        d2 = Candle(time=self.base_time + timedelta(days=1), open=1.0900, high=1.0960, low=1.0880, close=1.0940)

        levels = self.engine.calculate_levels("EURUSD", [w1, w2], [d1, d2])
        self.assertIsNotNone(levels)
        # Previous week is w1 (index -2)
        self.assertEqual(levels.pwh, 1.0950)
        self.assertEqual(levels.pwl, 1.0750)
        self.assertEqual(levels.pwm, (1.0950 + 1.0750) / 2.0)
        # Previous day is d1 (index -2)
        self.assertEqual(levels.pdh, 1.0920)
        self.assertEqual(levels.pdl, 1.0810)
        self.assertEqual(levels.pdm, (1.0920 + 1.0810) / 2.0)

    def test_pdl_sweep_interaction(self):
        w1 = Candle(time=self.base_time, open=1.0800, high=1.0950, low=1.0750, close=1.0900)
        w2 = Candle(time=self.base_time + timedelta(days=7), open=1.0900, high=1.1000, low=1.0850, close=1.0920)
        d1 = Candle(time=self.base_time, open=1.0850, high=1.0920, low=1.0810, close=1.0900)
        d2 = Candle(time=self.base_time + timedelta(days=1), open=1.0900, high=1.0960, low=1.0880, close=1.0940)
        levels = self.engine.calculate_levels("EURUSD", [w1, w2], [d1, d2])

        # M15 candle sweeps below PDL (1.0810) to 1.0805, but closes back above at 1.0815 with bullish rejection
        c1 = Candle(time=self.base_time, open=1.0830, high=1.0840, low=1.0820, close=1.0825)
        c2 = Candle(time=self.base_time + timedelta(minutes=15), open=1.0825, high=1.0830, low=1.0805, close=1.0820)
        c3 = Candle(time=self.base_time + timedelta(minutes=30), open=1.0820, high=1.0835, low=1.0815, close=1.0830)

        interactions = self.engine.analyze_interaction(levels, [c1, c2, c3])
        pdl_sweeps = [i for i in interactions if i.interaction == InteractionType.PDL_SWEEP]
        self.assertTrue(len(pdl_sweeps) > 0)
        self.assertEqual(pdl_sweeps[0].direction_bias, OrderType.BUY)
        self.assertTrue(pdl_sweeps[0].confirmed)


if __name__ == "__main__":
    unittest.main()
