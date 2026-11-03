"""
Unit Tests for Institutional Chart and Harmonic Pattern Recognition.
Tests swing extraction, geometric pattern detection, measured targets,
and candle-close breakout confirmation.
"""

import unittest
from datetime import datetime, timedelta, timezone
import numpy as np

from src.core.contracts import Candle
from src.patterns.contracts import PatternFamily, PatternDirection
from src.patterns.chart_patterns import ChartPatternEngine, extract_swings


class TestChartPatterns(unittest.TestCase):
    def setUp(self):
        self.engine = ChartPatternEngine({
            "chart": {"enabled": True, "min_bars_lookback": 5},
            "harmonic": {"enabled": True}
        })
        self.t0 = datetime(2026, 1, 1, 10, 0, tzinfo=timezone.utc)

    def _make_candle(self, o: float, h: float, l: float, c: float, i: int = 0) -> Candle:
        return Candle(
            time=self.t0 + timedelta(minutes=15 * i),
            open=round(o, 5),
            high=round(h, 5),
            low=round(l, 5),
            close=round(c, 5),
            tick_volume=100,
            spread=1,
            complete=True
        )

    def test_double_top_detection(self):
        # Peak 1 ~ 1.100, Trough ~ 1.078, Peak 2 ~ 1.100, followed by breakdown close < 1.078
        candles = [
            self._make_candle(1.060, 1.070, 1.058, 1.068, 0),
            self._make_candle(1.068, 1.085, 1.066, 1.082, 1),
            self._make_candle(1.082, 1.100, 1.080, 1.095, 2),  # Peak 1 (high 1.100)
            self._make_candle(1.095, 1.096, 1.085, 1.088, 3),
            self._make_candle(1.088, 1.089, 1.078, 1.080, 4),  # Trough (low 1.078)
            self._make_candle(1.080, 1.090, 1.079, 1.088, 5),
            self._make_candle(1.088, 1.100, 1.086, 1.096, 6),  # Peak 2 (high 1.100)
            self._make_candle(1.096, 1.097, 1.085, 1.086, 7),
            self._make_candle(1.086, 1.087, 1.079, 1.081, 8),
            self._make_candle(1.081, 1.082, 1.070, 1.074, 9),  # Breakdown bar: close 1.074 < 1.078
        ]
        results = self.engine.detect(candles)
        dt = [r for r in results if r.name == "Double Top"]
        self.assertGreaterEqual(len(dt), 1)
        self.assertEqual(dt[0].direction, PatternDirection.BEARISH)
        self.assertTrue(dt[0].confirmed)
        self.assertGreater(dt[0].invalidation_price, 1.100)
        self.assertLess(dt[0].measured_target, 1.078)

    def test_head_and_shoulders_detection(self):
        # Left shoulder (1.090) -> Head (1.110) -> Right shoulder (1.090) -> Neckline breakdown < 1.075
        candles = [
            self._make_candle(1.070, 1.075, 1.068, 1.072, 0),
            self._make_candle(1.072, 1.073, 1.070, 1.071, 1),
            self._make_candle(1.071, 1.090, 1.080, 1.085, 2),  # Left shoulder (1.090)
            self._make_candle(1.085, 1.086, 1.080, 1.082, 3),
            self._make_candle(1.082, 1.083, 1.075, 1.076, 4),  # Trough 1 (1.075)
            self._make_candle(1.076, 1.085, 1.078, 1.080, 5),
            self._make_candle(1.080, 1.110, 1.080, 1.105, 6),  # Head (1.110)
            self._make_candle(1.105, 1.106, 1.080, 1.085, 7),
            self._make_candle(1.085, 1.086, 1.075, 1.076, 8),  # Trough 2 (1.075)
            self._make_candle(1.076, 1.080, 1.078, 1.079, 9),
            self._make_candle(1.079, 1.090, 1.079, 1.086, 10), # Right shoulder (1.090)
            self._make_candle(1.086, 1.087, 1.080, 1.082, 11),
            self._make_candle(1.082, 1.083, 1.076, 1.078, 12),
            self._make_candle(1.078, 1.079, 1.065, 1.068, 13), # Breakdown bar: close 1.068 < 1.075
        ]
        results = self.engine.detect(candles)
        hs = [r for r in results if r.name == "Head and Shoulders"]
        self.assertGreaterEqual(len(hs), 1)
        self.assertEqual(hs[0].direction, PatternDirection.BEARISH)
        self.assertTrue(hs[0].confirmed)

    def test_bull_flag_detection(self):
        candles = [
            self._make_candle(1.050, 1.060, 1.049, 1.058, 0),
            self._make_candle(1.058, 1.070, 1.057, 1.068, 1),
            self._make_candle(1.068, 1.080, 1.067, 1.078, 2),
            self._make_candle(1.078, 1.090, 1.077, 1.088, 3),  # Pole peak 1.090
            self._make_candle(1.088, 1.089, 1.083, 1.084, 4),  # Flag
            self._make_candle(1.084, 1.086, 1.082, 1.083, 5),
            self._make_candle(1.083, 1.085, 1.081, 1.082, 6),
            self._make_candle(1.082, 1.084, 1.080, 1.081, 7),
            self._make_candle(1.081, 1.083, 1.079, 1.080, 8),
            self._make_candle(1.080, 1.083, 1.079, 1.081, 9),
            self._make_candle(1.081, 1.084, 1.080, 1.082, 10),
            self._make_candle(1.082, 1.095, 1.081, 1.094, 11), # Breakout bar closing above flag top
        ]
        results = self.engine.detect(candles)
        bf = [r for r in results if r.name == "Bull Flag"]
        self.assertEqual(len(bf), 1)
        self.assertEqual(bf[0].direction, PatternDirection.BULLISH)
        self.assertTrue(bf[0].confirmed)
        self.assertGreater(bf[0].measured_target, 1.095)


if __name__ == "__main__":
    unittest.main()
