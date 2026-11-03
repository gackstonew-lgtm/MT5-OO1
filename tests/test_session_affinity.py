"""
Unit tests for Session Affinity Matrix and Cash-Open Blackout Logic.
"""

import unittest
from datetime import datetime, timezone
from src.core.contracts import SessionType
from src.core.session_affinity import SessionAffinityEngine


class TestSessionAffinityEngine(unittest.TestCase):
    def setUp(self):
        self.affinity = SessionAffinityEngine()

    def test_symbol_alias_normalization(self):
        """Verifies broker suffixes and aliases resolve to standard canonical symbols."""
        self.assertEqual(self.affinity.resolve_symbol("EURUSD.m"), "EURUSD")
        self.assertEqual(self.affinity.resolve_symbol("GBPUSD.m"), "GBPUSD")
        self.assertEqual(self.affinity.resolve_symbol("XAUUSD.m"), "XAUUSD")
        self.assertEqual(self.affinity.resolve_symbol("GOLD"), "XAUUSD")
        self.assertEqual(self.affinity.resolve_symbol("USTEC"), "NAS100")
        self.assertEqual(self.affinity.resolve_symbol("NAS100.m"), "NAS100")
        self.assertEqual(self.affinity.resolve_symbol("US30.m"), "US30")
        self.assertEqual(self.affinity.resolve_symbol("DJ30"), "US30")

    def tearDown(self):
        # Reset mode back to default "scored"
        self.affinity.set_mode("scored")

    def test_affinity_weights_lookup(self):
        """Verifies affinity weights match the reference matrix."""
        # EURUSD in London: 1.0, Asia: 0.20
        self.assertAlmostEqual(self.affinity.get_affinity_weight(SessionType.LONDON, "EURUSD"), 1.0)
        self.assertAlmostEqual(self.affinity.get_affinity_weight(SessionType.ASIAN, "EURUSD.m"), 0.20)

        # USDJPY in Asia: 1.0, London: 0.60
        self.assertAlmostEqual(self.affinity.get_affinity_weight(SessionType.ASIAN, "USDJPY"), 1.0)

        # XAUUSD in Best Window / NY: 1.0, Asia: 0.20
        self.assertAlmostEqual(self.affinity.get_affinity_weight(SessionType.BEST_WINDOW, "XAUUSD"), 1.0)
        self.assertAlmostEqual(self.affinity.get_affinity_weight(SessionType.ASIAN, "XAUUSD"), 0.20)

    def test_strict_mode_gating(self):
        """Strict mode requires weight >= strict_threshold (default 0.80)."""
        self.affinity.set_mode("strict")

        # EURUSD in London has 1.0 >= 0.80 -> allowed
        allowed_lon, weight_lon, reason_lon = self.affinity.is_symbol_allowed(SessionType.LONDON, "EURUSD")
        self.assertTrue(allowed_lon)
        self.assertEqual(weight_lon, 1.0)
        self.assertIsNone(reason_lon)

        # EURUSD in Asia has 0.20 < 0.80 -> rejected in strict mode
        allowed_asia, weight_asia, reason_asia = self.affinity.is_symbol_allowed(SessionType.ASIAN, "EURUSD")
        self.assertFalse(allowed_asia)
        self.assertIn("threshold", reason_asia)

    def test_scored_mode_gating(self):
        """Scored mode allows trading if weight >= min_weight_floor (0.20)."""
        self.affinity.set_mode("scored")

        # EURUSD in Asia has 0.20 >= 0.20 -> allowed with score multiplier
        allowed, weight, _ = self.affinity.is_symbol_allowed(SessionType.ASIAN, "EURUSD")
        self.assertTrue(allowed)
        self.assertAlmostEqual(weight, 0.20)

        # In off-hours weight is 0.0 < 0.20 floor
        allowed_off, weight_off, _ = self.affinity.is_symbol_allowed(SessionType.OFF_HOURS, "EURUSD")
        self.assertFalse(allowed_off)

    def test_cash_open_blackout_indices(self):
        """NAS100 and US30 must be blocked during US cash open (14:00 - 15:00 UTC)."""
        # Exactly at 14:30 UTC: in blackout
        dt_cash_open = datetime(2026, 1, 6, 14, 30, tzinfo=timezone.utc)
        allowed_nas, _, reason_nas = self.affinity.is_symbol_allowed(SessionType.NEW_YORK, "NAS100", dt_utc=dt_cash_open)
        self.assertFalse(allowed_nas)
        self.assertIn("Cash Open blackout", reason_nas)

        allowed_ustec, _, reason_ustec = self.affinity.is_symbol_allowed(SessionType.NEW_YORK, "USTEC.m", dt_utc=dt_cash_open)
        self.assertFalse(allowed_ustec)
        self.assertIn("Cash Open blackout", reason_ustec)

        allowed_us30, _, reason_us30 = self.affinity.is_symbol_allowed(SessionType.NEW_YORK, "US30", dt_utc=dt_cash_open)
        self.assertFalse(allowed_us30)

        # FX pairs (EURUSD, GBPUSD) must not be affected by index cash-open blackout
        allowed_fx, _, _ = self.affinity.is_symbol_allowed(SessionType.NEW_YORK, "EURUSD", dt_utc=dt_cash_open)
        self.assertTrue(allowed_fx)

        # Outside cash open window: 15:15 UTC -> allowed
        dt_after = datetime(2026, 1, 6, 15, 15, tzinfo=timezone.utc)
        allowed_nas_after, _, _ = self.affinity.is_symbol_allowed(SessionType.NEW_YORK, "NAS100", dt_utc=dt_after)
        self.assertTrue(allowed_nas_after)


if __name__ == "__main__":
    unittest.main()
