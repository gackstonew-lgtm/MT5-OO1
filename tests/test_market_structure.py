"""
Unit tests for Market Structure, BOS, CHoCH, and Displacement.
"""

import unittest
from datetime import datetime, timezone, timedelta
from src.core.contracts import Candle, Timeframe, OrderType, MarketStructureType
from src.smc.market_structure import MarketStructureEngine


class TestMarketStructureEngine(unittest.TestCase):
    def setUp(self):
        self.engine = MarketStructureEngine(swing_lookback=3, displacement_ratio_min=0.60)
        self.base_time = datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)

    def test_swing_high_and_low_detection(self):
        # Create a defined peak: 1.0, 1.1, 1.2, 1.5 (peak), 1.3, 1.1, 1.0
        prices = [1.0, 1.1, 1.2, 1.5, 1.3, 1.1, 1.0]
        candles = []
        for i, p in enumerate(prices):
            candles.append(
                Candle(
                    time=self.base_time + timedelta(minutes=15 * i),
                    open=p - 0.05, high=p, low=p - 0.10, close=p - 0.02
                )
            )

        highs, lows = self.engine.find_swing_points(candles)
        self.assertEqual(len(highs), 1)
        self.assertEqual(highs[0][1].high, 1.5)

    def test_displacement_validation(self):
        # Strong body candle: open 1.0800, high 1.0850, low 1.0795, close 1.0845
        # Range = 0.0055, Body = 0.0045 -> Ratio = 0.0045 / 0.0055 = 0.818 (> 0.60)
        c_disp = Candle(
            time=self.base_time,
            open=1.0800, high=1.0850, low=1.0795, close=1.0845
        )
        self.assertTrue(self.engine.is_displacement_candle(c_disp))

        # Doji candle (weak displacement)
        c_doji = Candle(
            time=self.base_time,
            open=1.0820, high=1.0850, low=1.0790, close=1.0821
        )
        self.assertFalse(self.engine.is_displacement_candle(c_doji))


if __name__ == "__main__":
    unittest.main()
