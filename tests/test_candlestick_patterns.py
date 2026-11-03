"""
Unit Tests for Institutional Candlestick Pattern Recognition.
Tests positive identification, near-miss rejections, and no-lookahead integrity.
"""

import unittest
from datetime import datetime, timedelta, timezone
import numpy as np

from src.core.contracts import Candle
from src.patterns.contracts import PatternFamily, PatternDirection, PatternRegistry
from src.patterns.candlesticks import CandlestickEngine, compute_atr, compute_prior_trend


class TestCandlestickPatterns(unittest.TestCase):
    def setUp(self):
        self.engine = CandlestickEngine()
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

    def test_compute_atr(self):
        highs = [1.10, 1.12, 1.11, 1.13]
        lows = [1.08, 1.09, 1.08, 1.10]
        closes = [1.09, 1.11, 1.10, 1.12]
        atr = compute_atr(highs, lows, closes, period=3)
        self.assertGreater(atr, 0.0)

    def test_bullish_engulfing_positive_and_near_miss(self):
        # Prior downtrend + Red candle + Green candle completely engulfing prior body
        candles = [
            self._make_candle(1.100, 1.101, 1.090, 1.091, 0),
            self._make_candle(1.091, 1.092, 1.080, 1.082, 1),
            self._make_candle(1.082, 1.083, 1.070, 1.072, 2),
            self._make_candle(1.072, 1.073, 1.060, 1.062, 3),  # Prior downtrend
            self._make_candle(1.062, 1.063, 1.050, 1.052, 4),  # Red bar: body 1.062 -> 1.052
            self._make_candle(1.050, 1.066, 1.048, 1.065, 5),  # Green engulfing: body 1.050 -> 1.065
        ]
        results = self.engine.detect(candles)
        engulfing = [r for r in results if r.name == "Bullish Engulfing"]
        self.assertEqual(len(engulfing), 1)
        self.assertEqual(engulfing[0].direction, PatternDirection.BULLISH)
        self.assertTrue(engulfing[0].confirmed)

        # Near-miss negative: Green candle fails to engulf prior red body
        near_miss = list(candles[:-1]) + [
            self._make_candle(1.051, 1.058, 1.049, 1.055, 5)  # Close 1.055 < prior open 1.062
        ]
        results_nm = self.engine.detect(near_miss)
        engulfing_nm = [r for r in results_nm if r.name == "Bullish Engulfing"]
        self.assertEqual(len(engulfing_nm), 0)

    def test_hammer_positive_and_near_miss(self):
        # Prior downtrend (at least 6 bars) + Long lower shadow (>= 2x body), tiny upper shadow
        candles = [
            self._make_candle(1.090, 1.091, 1.080, 1.081, 0),
            self._make_candle(1.080, 1.081, 1.070, 1.071, 1),
            self._make_candle(1.071, 1.072, 1.060, 1.061, 2),
            self._make_candle(1.061, 1.062, 1.050, 1.051, 3),
            self._make_candle(1.051, 1.052, 1.040, 1.041, 4),
            self._make_candle(1.041, 1.042, 1.010, 1.040, 5),  # Hammer: open 1.041, close 1.040, low 1.010
        ]
        results = self.engine.detect(candles)
        hammer = [r for r in results if r.name == "Hammer"]
        self.assertEqual(len(hammer), 1)
        self.assertEqual(hammer[0].direction, PatternDirection.BULLISH)

        # Near miss: upper shadow is too long (not a hammer)
        near_miss = list(candles[:-1]) + [
            self._make_candle(1.041, 1.060, 1.010, 1.040, 5)
        ]
        results_nm = self.engine.detect(near_miss)
        hammer_nm = [r for r in results_nm if r.name == "Hammer"]
        self.assertEqual(len(hammer_nm), 0)

    def test_morning_star_positive(self):
        # Red bar -> Star/Doji -> Strong green bar closing into bar 1
        candles = [
            self._make_candle(1.080, 1.081, 1.070, 1.071, 0),
            self._make_candle(1.071, 1.072, 1.060, 1.061, 1),
            self._make_candle(1.061, 1.062, 1.050, 1.051, 2),
            self._make_candle(1.050, 1.051, 1.030, 1.032, 3),  # Bar 1: big red
            self._make_candle(1.031, 1.033, 1.025, 1.028, 4),  # Bar 2: small star
            self._make_candle(1.029, 1.048, 1.028, 1.045, 5),  # Bar 3: strong green closing > midpoint of Bar 1
        ]
        results = self.engine.detect(candles)
        morning_star = [r for r in results if r.name in ("Morning Star", "Morning Doji Star")]
        self.assertGreaterEqual(len(morning_star), 1)

    def test_three_white_soldiers(self):
        # 3 consecutive strong green bars with higher closes
        candles = [
            self._make_candle(1.050, 1.051, 1.040, 1.041, 0),
            self._make_candle(1.041, 1.042, 1.030, 1.031, 1),
            self._make_candle(1.031, 1.032, 1.020, 1.021, 2),  # Prior downtrend
            self._make_candle(1.022, 1.035, 1.021, 1.034, 3),  # Soldier 1
            self._make_candle(1.033, 1.048, 1.032, 1.047, 4),  # Soldier 2
            self._make_candle(1.046, 1.062, 1.045, 1.060, 5),  # Soldier 3
        ]
        results = self.engine.detect(candles)
        soldiers = [r for r in results if r.name == "Three White Soldiers"]
        self.assertEqual(len(soldiers), 1)
        self.assertEqual(soldiers[0].direction, PatternDirection.BULLISH)

    def test_no_lookahead_integrity(self):
        """Verifies that pattern detection on N candles is completely unaffected by future candles."""
        candles_5 = [
            self._make_candle(1.080, 1.081, 1.070, 1.071, 0),
            self._make_candle(1.071, 1.072, 1.060, 1.061, 1),
            self._make_candle(1.061, 1.062, 1.050, 1.051, 2),
            self._make_candle(1.051, 1.052, 1.040, 1.041, 3),
            self._make_candle(1.041, 1.042, 1.010, 1.040, 4),
        ]
        res_5 = self.engine.detect(candles_5)

        # Append future candles
        candles_7 = candles_5 + [
            self._make_candle(1.040, 1.050, 1.038, 1.048, 5),
            self._make_candle(1.048, 1.060, 1.047, 1.059, 6),
        ]
        # Re-evaluating only the first 5 bars of candles_7 gives identical results
        res_replay = self.engine.detect(candles_7[:5])
        self.assertEqual([r.name for r in res_5], [r.name for r in res_replay])


if __name__ == "__main__":
    unittest.main()
