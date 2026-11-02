"""
Unit tests for src/data module: HistoricalDataLoader and CostModelEngine.
"""

import unittest
from datetime import datetime, timezone
import os

from src.core.contracts import Timeframe, Candle
from src.data.historical_loader import HistoricalDataLoader
from src.data.cost_model import CostModelEngine, SymbolCostProfile


class TestDataLoaderAndCosts(unittest.TestCase):
    def setUp(self):
        self.loader = HistoricalDataLoader()
        self.is_mt5_connected = self.loader.initialize_mt5()
        self.engine = CostModelEngine(loader=self.loader)

    def tearDown(self):
        if self.is_mt5_connected:
            self.loader.shutdown_mt5()

    def test_utc_offset_detection(self):
        offset = self.loader.detect_broker_utc_offset("XAUUSD.m")
        self.assertIsInstance(offset, int)
        # Offset should be between -12h and +14h
        self.assertTrue(-43200 <= offset <= 50400)
        # Broker uses 7200s (+2h)
        self.assertEqual(offset % 3600, 0)

    def test_symbol_resolution(self):
        resolved = self.loader.resolve_broker_symbol("EURUSD")
        self.assertIsNotNone(resolved)
        self.assertTrue(resolved in ["EURUSD", "EURUSD.m"])

        resolved_xau = self.loader.resolve_broker_symbol("XAUUSD")
        self.assertIsNotNone(resolved_xau)
        self.assertTrue(resolved_xau in ["XAUUSD", "XAUUSD.m"])

    def test_candles_load_and_caching(self):
        candles = self.loader.load_candles("EURUSD.m", Timeframe.M15, count=10, use_cache=True)
        self.assertTrue(len(candles) > 0)
        c0 = candles[0]
        self.assertIsInstance(c0, Candle)
        self.assertEqual(c0.time.tzinfo, timezone.utc)
        self.assertTrue(c0.high >= c0.low)
        self.assertTrue(c0.open > 0)
        self.assertTrue(c0.close > 0)

    def test_profile_costs(self):
        profile = self.engine.profile_symbol("EURUSD.m")
        self.assertIsNotNone(profile)
        self.assertIsInstance(profile, SymbolCostProfile)
        self.assertEqual(profile.digits, 5)
        self.assertGreater(profile.point, 0)
        self.assertGreater(profile.margin_required_1_lot, 0)
        self.assertIn("M15", profile.history_bars_available)
        self.assertGreater(profile.history_bars_available["M15"], 100)


if __name__ == "__main__":
    unittest.main()
