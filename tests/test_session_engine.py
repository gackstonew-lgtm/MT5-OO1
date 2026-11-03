"""
Unit Tests for Session Engine.
Tests boundaries in 'ist_reference' and 'market_local_dst' profiles,
DST shift adaptation, overlap detection, weekend/Monday/Friday cutoffs,
rollover blackout, handover cooldown, and broker-offset-unknown fail-safe.
"""

import unittest
from datetime import datetime, time, timezone, timedelta
import zoneinfo

from src.core.contracts import SessionType, ActiveSessionInfo
from src.core.sessions import TradingSessionEngine


class TestSessionEngine(unittest.TestCase):
    def setUp(self):
        self.engine = TradingSessionEngine()

    def test_ist_reference_profile_boundaries(self):
        """Verifies static UTC windows derived from IST reference profile."""
        self.engine.set_active_profile("ist_reference")

        # 1. Asia: 00:00 - 09:00 UTC (e.g. 04:00 UTC)
        dt_asia = datetime(2026, 1, 6, 4, 0, tzinfo=timezone.utc)  # Tuesday
        info_asia = self.engine.get_session_info(dt_asia, broker_offset_verified=True)
        self.assertEqual(info_asia.primary_session, SessionType.ASIAN)
        self.assertIn(SessionType.ASIAN, info_asia.active_sessions)
        self.assertTrue(info_asia.can_trade)

        # 2. London: 06:00 - 12:00 UTC (e.g. 10:00 UTC)
        dt_lon = datetime(2026, 1, 6, 10, 0, tzinfo=timezone.utc)
        info_lon = self.engine.get_session_info(dt_lon, broker_offset_verified=True)
        self.assertEqual(info_lon.primary_session, SessionType.LONDON)
        self.assertTrue(info_lon.can_trade)

        # 3. New York & Best Window: 12:30 - 17:30 UTC (e.g. 14:00 UTC)
        dt_ny = datetime(2026, 1, 6, 14, 0, tzinfo=timezone.utc)
        info_ny = self.engine.get_session_info(dt_ny, broker_offset_verified=True)
        self.assertTrue(info_ny.is_best_window)
        self.assertIn(SessionType.NEW_YORK, info_ny.active_sessions)
        self.assertTrue(info_ny.can_trade)

        # 4. Off-Hours: 19:00 UTC
        dt_off = datetime(2026, 1, 6, 19, 0, tzinfo=timezone.utc)
        info_off = self.engine.get_session_info(dt_off, broker_offset_verified=True)
        self.assertEqual(info_off.primary_session, SessionType.OFF_HOURS)
        self.assertFalse(info_off.can_trade)

    def test_dst_adaptation_market_local_profile(self):
        """Verifies that market_local_dst adapts to Summer DST and Winter Standard time."""
        self.engine.set_active_profile("market_local_dst")

        # Summer (July): London is UTC+1 (BST), NY is UTC-4 (EDT)
        # London local 09:00 BST = 08:00 UTC
        dt_summer = datetime(2026, 7, 7, 8, 30, tzinfo=timezone.utc)
        info_summer = self.engine.get_session_info(dt_summer, broker_offset_verified=True)
        self.assertEqual(info_summer.primary_session, SessionType.LONDON)

        # Winter (January): London is UTC+0 (GMT), NY is UTC-5 (EST)
        # London local 09:00 GMT = 09:00 UTC
        dt_winter = datetime(2026, 1, 6, 9, 30, tzinfo=timezone.utc)
        info_winter = self.engine.get_session_info(dt_winter, broker_offset_verified=True)
        self.assertEqual(info_winter.primary_session, SessionType.LONDON)

        # Overlap in Summer: 13:00 UTC is 14:00 BST (London open) and 09:00 EDT (NY open)
        dt_overlap = datetime(2026, 7, 7, 13, 30, tzinfo=timezone.utc)
        info_overlap = self.engine.get_session_info(dt_overlap, broker_offset_verified=True)
        self.assertTrue(info_overlap.is_best_window)
        self.assertIn(SessionType.LONDON_NY_OVERLAP, info_overlap.active_sessions)

    def test_rollover_swap_blackout(self):
        """21:55 - 22:15 UTC must strictly trigger rollover blackout and halt trading."""
        self.engine.set_active_profile("ist_reference")
        dt_roll = datetime(2026, 1, 6, 22, 5, tzinfo=timezone.utc)
        info = self.engine.get_session_info(dt_roll, broker_offset_verified=True)
        self.assertTrue(info.is_rollover_blackout)
        self.assertFalse(info.can_trade)
        self.assertIn("rollover", info.rejection_reason.lower())

    def test_weekend_and_friday_cutoff(self):
        """Friday after 20:00 UTC and Saturdays must be tagged as weekend/closed."""
        # Friday 20:30 UTC
        dt_fri = datetime(2026, 1, 9, 20, 30, tzinfo=timezone.utc)
        info_fri = self.engine.get_session_info(dt_fri, broker_offset_verified=True)
        self.assertTrue(info_fri.is_weekend)
        self.assertTrue(info_fri.is_friday_cutoff)
        self.assertFalse(info_fri.can_trade)

        # Saturday
        dt_sat = datetime(2026, 1, 10, 12, 0, tzinfo=timezone.utc)
        info_sat = self.engine.get_session_info(dt_sat, broker_offset_verified=True)
        self.assertTrue(info_sat.is_weekend)
        self.assertFalse(info_sat.can_trade)

    def test_monday_open_skip(self):
        """Monday first 30 minutes (00:00 - 00:30 UTC) must skip trading."""
        dt_mon = datetime(2026, 1, 5, 0, 15, tzinfo=timezone.utc)  # Monday 00:15
        info_mon = self.engine.get_session_info(dt_mon, broker_offset_verified=True)
        self.assertTrue(info_mon.is_monday_open_skip)
        self.assertFalse(info_mon.can_trade)
        self.assertIn("monday", info_mon.rejection_reason.lower())

    def test_broker_offset_unknown_failsafe(self):
        """If broker UTC offset is unverified, trading must be blocked fail-safe."""
        self.engine.require_verified_offset = True
        dt = datetime(2026, 1, 6, 14, 0, tzinfo=timezone.utc)
        info = self.engine.get_session_info(dt, broker_offset_verified=False)
        self.assertFalse(info.can_trade)
        self.assertIn("fail-safe", info.rejection_reason.lower())

    def test_handover_cooldown(self):
        """Session start within first 15 mins triggers handover cooldown."""
        self.engine.set_active_profile("ist_reference")
        # London opens at 06:00 UTC; 06:05 UTC is in handover cooldown
        dt_handover = datetime(2026, 1, 6, 6, 5, tzinfo=timezone.utc)
        info = self.engine.get_session_info(dt_handover, broker_offset_verified=True)
        self.assertTrue(info.is_handover_cooldown)
        self.assertFalse(info.can_trade)
        self.assertIn("handover", info.rejection_reason.lower())


if __name__ == "__main__":
    unittest.main()
