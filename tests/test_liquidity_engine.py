"""
Unit tests for Liquidity Engine (BSL, SSL, EQH, EQL, Sweeps).
"""

import unittest
from datetime import datetime, timezone, timedelta
from src.core.contracts import Candle, Timeframe
from src.smc.liquidity import LiquidityEngine


class TestLiquidityEngine(unittest.TestCase):
    def setUp(self):
        self.engine = LiquidityEngine(eqh_tolerance_pips=2.0, pip_size=0.0001)
        self.base_time = datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)

    def test_equal_highs_detection(self):
        # Create candles with two equal swing highs at 1.0900
        candles = []
        pattern = [
            1.0800, 1.0850, 1.0900, 1.0860, 1.0840, # First swing high
            1.0860, 1.0900, 1.0850, 1.0820          # Second equal high
        ]
        for i, p in enumerate(pattern):
            candles.append(
                Candle(
                    time=self.base_time + timedelta(minutes=15 * i),
                    open=p - 0.0020, high=p, low=p - 0.0030, close=p - 0.0010
                )
            )

        pools = self.engine.identify_liquidity_pools("EURUSD", Timeframe.M15, candles, swing_lookback=2)
        eqh_pools = [p for p in pools if p.level_type == "EQH"]
        self.assertTrue(len(eqh_pools) >= 1)
        self.assertAlmostEqual(eqh_pools[0].price, 1.0900, places=4)


if __name__ == "__main__":
    unittest.main()
