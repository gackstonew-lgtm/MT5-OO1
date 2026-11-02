"""
Unit tests for Sessions, Kill Zones, and Volatility Engine.
"""

import unittest
from datetime import datetime, timezone, time
from src.core.contracts import Candle, SessionType, VolatilityRegime
from src.core.sessions import TradingSessionEngine
from src.core.volatility import VolatilityEngine


class TestSessionsVolatility(unittest.TestCase):
    def setUp(self):
        self.session_engine = TradingSessionEngine()
        self.vol_engine = VolatilityEngine(atr_period=5, lookback_percentile=10)

    def test_london_killzone(self):
        # 08:30 UTC is inside London Killzone (07:00 - 10:30 UTC)
        dt = datetime(2026, 1, 1, 8, 30, tzinfo=timezone.utc)
        in_kz, kz_name = self.session_engine.evaluate_killzone(dt)
        self.assertTrue(in_kz)
        self.assertEqual(kz_name, "LONDON_KILL_ZONE")

    def test_new_york_killzone(self):
        # 14:00 UTC is inside New York Killzone (13:00 - 16:00 UTC)
        dt = datetime(2026, 1, 1, 14, 0, tzinfo=timezone.utc)
        in_kz, kz_name = self.session_engine.evaluate_killzone(dt)
        self.assertTrue(in_kz)
        self.assertEqual(kz_name, "NEW_YORK_KILL_ZONE")

    def test_volatility_regime_classification(self):
        # Create candles with stable range
        candles = []
        base = datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)
        for i in range(30):
            candles.append(
                Candle(
                    time=base, open=1.0800, high=1.0820, low=1.0790, close=1.0810
                )
            )
        regime, atr = self.vol_engine.classify_regime(candles)
        self.assertIn(regime, [VolatilityRegime.LOW, VolatilityRegime.NORMAL, VolatilityRegime.HIGH])
        self.assertTrue(atr > 0.0)


if __name__ == "__main__":
    unittest.main()
