"""
Unit tests for Phase 5: Multi-Symbol Scanner and News Blackout Filter.
"""

import unittest
from datetime import datetime, timezone, timedelta

from src.core.contracts import Timeframe, TrendBias
from src.smc.structure_confirmation import StructureStateMachine, ConfirmationState
from src.smc.news_filter import NewsFilterEngine, EconomicEvent
from src.smc.symbol_scanner import SymbolScanner


class TestSymbolScanner(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 11, 3, 13, 0, 0, tzinfo=timezone.utc)
        self.news_filter = NewsFilterEngine(blackout_minutes_before=15, blackout_minutes_after=15)
        self.struct_machine = StructureStateMachine()

        # Set up a symbol with BOS_CONFIRMED
        s_bos = self.struct_machine.get_state("EURUSD", Timeframe.M15)
        s_bos.state = ConfirmationState.BOS_CONFIRMED

        # Set up a symbol with CHOCH_WARNING
        s_choch = self.struct_machine.get_state("GBPUSD", Timeframe.M15)
        s_choch.state = ConfirmationState.CHOCH_WARNING

        # Set up a symbol in IDLE
        s_idle = self.struct_machine.get_state("BTCUSD", Timeframe.M15)
        s_idle.state = ConfirmationState.IDLE

    def test_scanner_ranking_order(self):
        """Verifies symbols are ranked primarily by structure state and confluence."""
        scanner = SymbolScanner(top_n=2, news_filter=self.news_filter, struct_machine=self.struct_machine)

        symbols_data = {
            "EURUSD": {"htf_aligned": True, "spread_pts": 5.0, "atr_pts": 60.0, "session": "LONDON"},
            "GBPUSD": {"htf_aligned": True, "spread_pts": 8.0, "atr_pts": 50.0, "session": "LONDON"},
            "BTCUSD": {"htf_aligned": False, "spread_pts": 200.0, "atr_pts": 300.0, "session": "OFF_HOURS"}
        }

        ranked, selected = scanner.rank_symbols(symbols_data, current_time=self.now)

        # EURUSD (BOS_CONFIRMED) must rank #1
        self.assertEqual(ranked[0].symbol, "EURUSD")
        self.assertEqual(ranked[0].structure_state, "BOS_CONFIRMED")
        self.assertGreater(ranked[0].total_score, ranked[1].total_score)

        # GBPUSD (CHOCH_WARNING) must rank #2
        self.assertEqual(ranked[1].symbol, "GBPUSD")

        # Top 2 selected
        self.assertEqual(selected, ["EURUSD", "GBPUSD"])
        self.assertNotIn("BTCUSD", selected)

    def test_news_blackout_excludes_symbol(self):
        """Verifies high-impact economic news excludes symbol during blackout window."""
        # Add high-impact EUR event at 13:10 UTC (current time is 13:00, so 10 mins before event)
        event = EconomicEvent(
            event_id="ECB_01",
            currency="EUR",
            title="ECB Interest Rate Decision",
            event_time=self.now + timedelta(minutes=10),
            impact="HIGH"
        )
        self.news_filter.add_event(event)

        in_blackout, reason = self.news_filter.is_in_blackout("EURUSD", current_time=self.now)
        self.assertTrue(in_blackout)
        self.assertIn("ECB Interest Rate Decision", reason)

        scanner = SymbolScanner(top_n=2, news_filter=self.news_filter, struct_machine=self.struct_machine)
        symbols_data = {
            "EURUSD": {"htf_aligned": True, "spread_pts": 5.0, "atr_pts": 60.0, "session": "LONDON"},
            "GBPUSD": {"htf_aligned": True, "spread_pts": 8.0, "atr_pts": 50.0, "session": "LONDON"}
        }

        ranked, selected = scanner.rank_symbols(symbols_data, current_time=self.now)

        # EURUSD has USD, so it is in blackout
        eur_item = [s for s in ranked if s.symbol == "EURUSD"][0]
        self.assertTrue(eur_item.in_news_blackout)
        self.assertEqual(eur_item.news_score, 0.0)

        # EURUSD must be excluded from selected top list
        self.assertNotIn("EURUSD", selected)
        self.assertIn("GBPUSD", selected)


if __name__ == "__main__":
    unittest.main()
