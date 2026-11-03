"""
Unit tests for Asia Range calculation, liquidity zones, and London Judas Swing detection.
"""

import unittest
from datetime import datetime, timezone, timedelta
from src.core.contracts import Candle, Timeframe, OrderType
from src.smc.session_liquidity import SessionLiquidityEngine


class TestSessionLiquidityEngine(unittest.TestCase):
    def setUp(self):
        self.engine = SessionLiquidityEngine(pip_multiplier=10000.0)

    def _generate_asia_candles(self, base_date: datetime) -> list:
        candles = []
        # 00:00 to 09:00 UTC = 36 M15 bars
        for i in range(36):
            t = base_date + timedelta(minutes=i * 15)
            # High 1.0850 at bar 10, Low 1.0810 at bar 20, around 1.0830 otherwise
            h = 1.0850 if i == 10 else 1.0835
            l = 1.0810 if i == 20 else 1.0825
            c = Candle(
                time=t,
                open=1.0830,
                high=h,
                low=l,
                close=1.0830,
                tick_volume=100
            )
            candles.append(c)
        return candles

    def test_asia_range_calculation(self):
        """Verifies calculation of Asian session High, Low, Midpoint, and Range in pips."""
        base_date = datetime(2026, 1, 6, 0, 0, tzinfo=timezone.utc)
        candles = self._generate_asia_candles(base_date)

        asia_range = self.engine.calculate_asia_range("EURUSD", candles, target_date=base_date)
        self.assertIsNotNone(asia_range)
        self.assertEqual(asia_range.symbol, "EURUSD")
        self.assertEqual(asia_range.date, "2026-01-06")
        self.assertAlmostEqual(asia_range.asia_high, 1.0850)
        self.assertAlmostEqual(asia_range.asia_low, 1.0810)
        self.assertAlmostEqual(asia_range.asia_mid, 1.0830)
        self.assertAlmostEqual(asia_range.range_pips, 40.0) # (1.0850 - 1.0810) * 10000
        self.assertEqual(asia_range.bars_count, 36)

    def test_register_asia_liquidity_zones(self):
        """Asia High forms BSL, Asia Low forms SSL."""
        base_date = datetime(2026, 1, 6, 0, 0, tzinfo=timezone.utc)
        candles = self._generate_asia_candles(base_date)
        asia_range = self.engine.calculate_asia_range("EURUSD", candles, target_date=base_date)

        zones = self.engine.register_asia_liquidity_zones(asia_range, pip_size=0.0001)
        self.assertEqual(len(zones), 2)

        bsl = next(z for z in zones if z.level_type == "BSL")
        ssl = next(z for z in zones if z.level_type == "SSL")

        self.assertAlmostEqual(bsl.price, 1.0850)
        self.assertAlmostEqual(ssl.price, 1.0810)

    def test_london_judas_swing_high_sweep(self):
        """A London candle sweeping Asia High and closing back inside gives a SELL bias."""
        base_date = datetime(2026, 1, 6, 0, 0, tzinfo=timezone.utc)
        candles = self._generate_asia_candles(base_date)
        asia_range = self.engine.calculate_asia_range("EURUSD", candles, target_date=base_date)

        # London bar at 07:15 UTC: Sweeps 1.0850 up to 1.0855 (+5 pips) but closes at 1.0845
        london_sweep_bar = Candle(
            time=datetime(2026, 1, 6, 7, 15, tzinfo=timezone.utc),
            open=1.0840,
            high=1.0855,
            low=1.0838,
            close=1.0845,
            tick_volume=500
        )
        candles.append(london_sweep_bar)

        sweep_event = self.engine.detect_london_asia_sweep("EURUSD", candles, asia_range)
        self.assertIsNotNone(sweep_event)
        self.assertEqual(sweep_event.sweep_type, "SWEEP_HIGH")
        self.assertEqual(sweep_event.direction_bias, OrderType.SELL)
        self.assertAlmostEqual(sweep_event.sweep_distance_pips, 5.0)
        self.assertTrue(sweep_event.confirmed)

    def test_london_judas_swing_low_sweep(self):
        """A London candle sweeping Asia Low and closing back inside gives a BUY bias."""
        base_date = datetime(2026, 1, 6, 0, 0, tzinfo=timezone.utc)
        candles = self._generate_asia_candles(base_date)
        asia_range = self.engine.calculate_asia_range("EURUSD", candles, target_date=base_date)

        # London bar at 08:30 UTC: Sweeps 1.0810 down to 1.0802 (+8 pips) but closes at 1.0818
        london_sweep_bar = Candle(
            time=datetime(2026, 1, 6, 8, 30, tzinfo=timezone.utc),
            open=1.0815,
            high=1.0820,
            low=1.0802,
            close=1.0818,
            tick_volume=500
        )
        candles.append(london_sweep_bar)

        sweep_event = self.engine.detect_london_asia_sweep("EURUSD", candles, asia_range)
        self.assertIsNotNone(sweep_event)
        self.assertEqual(sweep_event.sweep_type, "SWEEP_LOW")
        self.assertEqual(sweep_event.direction_bias, OrderType.BUY)
        self.assertAlmostEqual(sweep_event.sweep_distance_pips, 8.0)


if __name__ == "__main__":
    unittest.main()
