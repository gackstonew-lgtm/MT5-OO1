"""
Unit Tests for Data & Feature Pipeline (Stage 2).
Tests Technical and SMC feature extractors for deterministic correctness and zero lookahead.
"""

import unittest
from datetime import datetime, timezone, timedelta

from src.core.contracts import Candle
from src.features.feature_registry import FeatureExtractor, FeaturePipeline, FeatureSnapshot


class TestFeaturePipeline(unittest.TestCase):
    def setUp(self):
        # Generate synthetic synthetic trending candles
        base_time = datetime(2026, 10, 1, 10, 0, tzinfo=timezone.utc)
        self.candles = []
        p = 1.1000
        for i in range(100):
            t = base_time + timedelta(minutes=15 * i)
            # Upward trending series with volatility
            o = p
            h = o + 0.0015
            l = o - 0.0005
            c = o + 0.0008
            p = c
            self.candles.append(Candle(
                time=t,
                open=o,
                high=h,
                low=l,
                close=c,
                tick_volume=100 + i,
                spread=10,
                complete=True
            ))

    def test_compute_atr(self):
        atr = FeatureExtractor.compute_atr(self.candles, period=14)
        self.assertGreater(atr, 0.0005)
        self.assertLess(atr, 0.0050)

    def test_compute_ema(self):
        prices = [c.close for c in self.candles]
        ema_20 = FeatureExtractor.compute_ema(prices, 20)
        ema_50 = FeatureExtractor.compute_ema(prices, 50)
        # In upward trend, EMA 20 should be above EMA 50
        self.assertGreater(ema_20, ema_50)

    def test_compute_rsi(self):
        rsi = FeatureExtractor.compute_rsi(self.candles, period=14)
        self.assertGreaterEqual(rsi, 0.0)
        self.assertLessEqual(rsi, 100.0)
        # Consistent upward trend should yield RSI > 50
        self.assertGreater(rsi, 50.0)

    def test_compute_adx(self):
        adx, p_di, m_di = FeatureExtractor.compute_adx(self.candles, period=14)
        self.assertGreaterEqual(adx, 0.0)
        self.assertGreater(p_di, m_di)  # Bullish trend -> +DI > -DI

    def test_compute_bollinger_bands(self):
        prices = [c.close for c in self.candles]
        upper, mid, lower, pct_b = FeatureExtractor.compute_bollinger_bands(prices, period=20, num_std=2.0)
        self.assertGreater(upper, mid)
        self.assertGreater(mid, lower)
        self.assertIsInstance(pct_b, float)

    def test_detect_swings(self):
        # Create a peak at index 5 and trough at index 10
        custom_candles = list(self.candles[:20])
        custom_candles[5] = Candle(
            time=custom_candles[5].time,
            open=1.1000, high=1.1200, low=1.0990, close=1.1150
        )
        custom_candles[10] = Candle(
            time=custom_candles[10].time,
            open=1.1050, high=1.1060, low=1.0800, close=1.0850
        )
        highs, lows = FeatureExtractor.detect_swings(custom_candles, k=3)
        self.assertTrue(any(sh.index == 5 for sh in highs))
        self.assertTrue(any(sl.index == 10 for sl in lows))

    def test_detect_fvgs(self):
        # Craft a bullish FVG: bar 1 high < bar 3 low
        t0 = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)
        c1 = Candle(time=t0, open=1.1000, high=1.1020, low=1.0990, close=1.1010)
        c2 = Candle(time=t0 + timedelta(minutes=15), open=1.1010, high=1.1080, low=1.1010, close=1.1070)
        c3 = Candle(time=t0 + timedelta(minutes=30), open=1.1070, high=1.1120, low=1.1040, close=1.1100)

        fvgs = FeatureExtractor.detect_fvgs([c1, c2, c3], min_size=0.0005)
        self.assertEqual(len(fvgs), 1)
        self.assertEqual(fvgs[0].direction, "BULLISH")
        self.assertEqual(fvgs[0].bottom, 1.1020)
        self.assertEqual(fvgs[0].top, 1.1040)
        self.assertAlmostEqual(fvgs[0].size, 0.0020, places=5)

    def test_liquidity_sweep_detection(self):
        # High at 1.1100, then last bar spikes to 1.1110 and closes at 1.1090
        t0 = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)
        from src.features.feature_registry import SwingPoint
        swing_high = SwingPoint(index=2, time=t0, price=1.1100, point_type="HIGH")
        sweep_bar = Candle(time=t0 + timedelta(hours=1), open=1.1080, high=1.1110, low=1.1070, close=1.1090)

        bullish_sweep, bearish_sweep = FeatureExtractor.detect_liquidity_sweep([sweep_bar], [swing_high], [])
        self.assertFalse(bullish_sweep)
        self.assertTrue(bearish_sweep)

    def test_session_flags(self):
        # 03:00 UTC -> Asian
        t_asian = datetime(2026, 10, 1, 3, 0, tzinfo=timezone.utc)
        a, l, ny, ov = FeatureExtractor.get_session_flags(t_asian)
        self.assertTrue(a)
        self.assertFalse(l)
        self.assertFalse(ny)

        # 14:00 UTC -> London + NY + Overlap
        t_ny = datetime(2026, 10, 1, 14, 0, tzinfo=timezone.utc)
        a, l, ny, ov = FeatureExtractor.get_session_flags(t_ny)
        self.assertFalse(a)
        self.assertTrue(l)
        self.assertTrue(ny)
        self.assertTrue(ov)

    def test_pipeline_snapshot_generation(self):
        snapshot = FeaturePipeline.extract_snapshot(
            symbol="EURUSD.m",
            m15_candles=self.candles,
            point=0.00001
        )
        self.assertIsInstance(snapshot, FeatureSnapshot)
        self.assertEqual(snapshot.symbol, "EURUSD.m")
        self.assertGreater(snapshot.atr_14, 0.0)
        self.assertGreater(snapshot.rsi_14, 0.0)
        self.assertIn(snapshot.timeframe, "M15")


if __name__ == "__main__":
    unittest.main()
