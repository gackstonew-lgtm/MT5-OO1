"""
Unit tests for Task 1: Closed-Bar Decision Outcome Logging and Telegram Deduplication.
"""

import unittest
from datetime import datetime, timezone, timedelta
from src.core.contracts import DecisionOutcome, DecisionOutcomeType, OrderType, Candle, Timeframe, SymbolInfo
from src.core.config import AppConfig
from src.core.orchestrator import TradingBotOrchestrator
from src.notifications.telegram_config import TelegramConfig
from src.notifications.telegram_service import TelegramNotifier


class TestDecisionOutcomeLogging(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 11, 2, 12, 0, 0, tzinfo=timezone.utc)
        self.cfg = TelegramConfig()
        self.cfg.enabled = True
        self.notifier = TelegramNotifier(self.cfg)
        self.notifier.transport.send_message = lambda *args, **kwargs: True

    def test_format_outcome_lines(self):
        # 1. TRADE SENT
        o_sent = DecisionOutcome(
            symbol="EURUSD",
            bar_time=self.now,
            outcome_type=DecisionOutcomeType.TRADE_SENT,
            details="Trade filled",
            direction="BUY",
            volume=0.25,
            entry_price=1.08500,
            stop_loss=1.08300,
            take_profit=1.09000,
            risk_dollars=50.0,
            risk_percent=0.50,
            ticket=12345
        )
        expected_sent = "TRADE SENT: EURUSD, BUY, 0.25, 1.08500, 1.08300, 1.09000, $50.00 (0.50% of balance), ticket #12345"
        self.assertEqual(o_sent.format_line(), expected_sent)

        # 2. TRADE SKIPPED
        o_skip = DecisionOutcome(
            symbol="EURUSD",
            bar_time=self.now,
            outcome_type=DecisionOutcomeType.TRADE_SKIPPED,
            reason_code="MIN_LOT_RISK_CAP",
            details="min lot 0.01 risks $28.00 (2.80%), exceeding per-trade cap 1.00% ($10.00), balance $1000.00"
        )
        expected_skip = "TRADE SKIPPED: MIN_LOT_RISK_CAP | min lot 0.01 risks $28.00 (2.80%), exceeding per-trade cap 1.00% ($10.00), balance $1000.00"
        self.assertEqual(o_skip.format_line(), expected_skip)

        # 3. NO SETUP
        o_nosetup = DecisionOutcome(
            symbol="EURUSD",
            bar_time=self.now,
            outcome_type=DecisionOutcomeType.NO_SETUP,
            details="SCALP HTF conflict (M15=BULLISH, H1=BEARISH, H4=BULLISH)"
        )
        expected_nosetup = "NO SETUP: SCALP HTF conflict (M15=BULLISH, H1=BEARISH, H4=BULLISH)"
        self.assertEqual(o_nosetup.format_line(), expected_nosetup)

    def test_telegram_deduplication_by_bar_time_and_reason(self):
        outcome = DecisionOutcome(
            symbol="EURUSD",
            bar_time=self.now,
            outcome_type=DecisionOutcomeType.NO_SETUP,
            details="SCALP HTF conflict (M15=BULLISH, H1=BEARISH, H4=BULLISH)"
        )

        # First dispatch should succeed
        res1 = self.notifier.notify_decision_outcome(outcome)
        self.assertTrue(res1)
        self.assertEqual(self.notifier.suppressed_duplicates_count, 0)

        # Second dispatch with identical (symbol, bar_time, outcome, reason) should be suppressed
        res2 = self.notifier.notify_decision_outcome(outcome)
        self.assertFalse(res2)
        self.assertEqual(self.notifier.suppressed_duplicates_count, 1)

        # Third dispatch on a NEW bar should succeed
        outcome_new_bar = DecisionOutcome(
            symbol="EURUSD",
            bar_time=self.now + timedelta(minutes=15),
            outcome_type=DecisionOutcomeType.NO_SETUP,
            details="SCALP HTF conflict (M15=BULLISH, H1=BEARISH, H4=BULLISH)"
        )
        res3 = self.notifier.notify_decision_outcome(outcome_new_bar)
        self.assertTrue(res3)
        self.assertEqual(self.notifier.suppressed_duplicates_count, 1)

        # Reset counter test
        self.notifier.reset_suppression_counter()
        self.assertEqual(self.notifier.suppressed_duplicates_count, 0)

    def test_orchestrator_closed_bar_never_exits_silently(self):
        config = AppConfig()
        orchestrator = TradingBotOrchestrator(config)
        symbol = "EURUSD"

        # 1. No candles: returns NO SETUP
        orchestrator.run_cycle_for_symbol(symbol)
        self.assertIn(symbol, orchestrator.last_decision_outcomes)
        outcome1 = orchestrator.last_decision_outcomes[symbol]
        self.assertEqual(outcome1.outcome_type, DecisionOutcomeType.NO_SETUP)
        self.assertIn("Insufficient M15 candle history", outcome1.details)

        # 2. Add candles with conflicting HTF directions
        candles = []
        base = self.now - timedelta(hours=50)
        for i in range(40):
            candles.append(Candle(time=base + timedelta(hours=i), open=1.08, high=1.09, low=1.07, close=1.085, complete=True))

        orchestrator.market_data.set_candles(symbol, Timeframe.W1, candles[:10])
        orchestrator.market_data.set_candles(symbol, Timeframe.D1, candles[:20])
        orchestrator.market_data.set_candles(symbol, Timeframe.H4, candles[:30])
        orchestrator.market_data.set_candles(symbol, Timeframe.H1, candles[:35])
        orchestrator.market_data.set_candles(symbol, Timeframe.M15, candles)

        orchestrator.run_cycle_for_symbol(symbol)
        self.assertIn(symbol, orchestrator.last_decision_outcomes)
        outcome2 = orchestrator.last_decision_outcomes[symbol]
        self.assertIn(outcome2.outcome_type, [DecisionOutcomeType.NO_SETUP, DecisionOutcomeType.TRADE_SKIPPED, DecisionOutcomeType.TRADE_SENT])


if __name__ == "__main__":
    unittest.main()
