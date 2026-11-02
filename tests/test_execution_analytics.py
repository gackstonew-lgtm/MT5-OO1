"""
Unit tests for Stage 5: Execution Quality and Analytics (TCA).
Validates:
1. TCA slippage calculation, latency auditing, and friction drag
2. Time-of-day spread model and rollover window filtering
3. Limit order conversion with structural entry and expiration
4. Idempotent request IDs and duplicate order suppression
5. Telegram and Markdown cost reporting
"""

import unittest
import time
from datetime import datetime, timezone

from src.core.contracts import OrderType
from src.execution.analytics.tca import TCAEngine, OrderExecutionAudit
from src.execution.analytics.spread_filter import SpreadFilter, SpreadCheckResult
from src.execution.analytics.limit_orders import LimitOrderEngine, PendingLimitSpec
from src.execution.analytics.idempotency import IdempotencyManager
from src.execution.analytics.reporting import TCAReporter


class TestExecutionAnalyticsStage5(unittest.TestCase):
    def setUp(self):
        self.tca = TCAEngine(log_path="reports/raw/test_tca.jsonl", enabled=False)
        self.spread_filter = SpreadFilter(max_spread_multiplier=2.0, allow_rollover_trading=False)
        self.limit_engine = LimitOrderEngine(expiration_minutes=45)
        self.idempotency = IdempotencyManager(ttl_seconds=10.0)

    def test_tca_audit_and_slippage(self):
        """Proves TCA accurately calculates adverse slippage and execution drag."""
        # BUY order: filled higher than intended -> adverse slippage
        audit_buy = self.tca.record_execution(
            request_id="REQ_TEST_01",
            symbol="EURUSD",
            order_type="BUY",
            volume=0.25,
            intended_price=1.08000,
            filled_price=1.08005,  # 0.5 pips / 5 pts higher
            spread_at_signal_pts=8.0,
            spread_at_fill_pts=10.0,
            latency_ms=14.5,
            retcode=10009,
            point=0.00001,
            tick_value=1.0,
            commission=1.50
        )
        self.assertEqual(audit_buy.slippage_points, 5.0)
        self.assertGreater(audit_buy.slippage_usd, 0.0)
        self.assertGreater(audit_buy.execution_drag_usd, 0.0)
        self.assertEqual(len(self.tca.records), 1)

        summary = self.tca.generate_summary()
        self.assertEqual(summary["total_trades"], 1)
        self.assertEqual(summary["mean_slippage_points"], 5.0)

    def test_spread_filter_rollover_and_spikes(self):
        """Proves spread filter blocks rollover window and wide spreads."""
        # 1. Rollover window (22:00 UTC) must be blocked
        res_rollover = self.spread_filter.evaluate_spread(symbol="EURUSD.m", current_spread_pts=12.0, hour_utc=22)
        self.assertFalse(res_rollover.allowed)
        self.assertIn("rollover window active", res_rollover.reason.lower())

        # 2. Normal hour (14:00 UTC) with normal spread (8 pts) must pass
        res_ok = self.spread_filter.evaluate_spread(symbol="EURUSD.m", current_spread_pts=8.0, hour_utc=14)
        self.assertTrue(res_ok.allowed)

        # 3. Normal hour but blown-out spread (> 2.0x expected) must be blocked
        res_spike = self.spread_filter.evaluate_spread(symbol="EURUSD.m", current_spread_pts=25.0, hour_utc=14)
        self.assertFalse(res_spike.allowed)
        self.assertIn("spread inflated", res_spike.reason.lower())

    def test_limit_order_conversion(self):
        """Proves limit orders are created with proper limit price and expiration."""
        spec = self.limit_engine.convert_to_limit_order(
            symbol="EURUSD",
            direction=OrderType.BUY,
            market_price=1.0810,
            structural_entry_price=1.0800,  # SMC pullback entry
            stop_loss=1.0770,
            take_profit=1.0860,
            volume=0.20,
            current_spread_pts=10.0,
            point=0.00001
        )
        self.assertIsNotNone(spec)
        self.assertEqual(spec.limit_type, OrderType.BUY_LIMIT)
        self.assertEqual(spec.limit_price, 1.0800)
        self.assertGreater(spec.spread_savings_pts, 0.0)
        self.assertGreater(spec.expiration_utc, datetime.now(timezone.utc))

    def test_idempotency_manager_duplicate_prevention(self):
        """Proves idempotency manager prevents duplicate order placement on retries."""
        req_id = self.idempotency.generate_request_id(
            symbol="EURUSD",
            order_type="BUY",
            volume=0.10,
            intended_price=1.0800
        )
        self.assertTrue(req_id.startswith("REQ_"))

        # First registration succeeds
        first = self.idempotency.register_request(
            request_id=req_id,
            symbol="EURUSD",
            order_type="BUY",
            volume=0.10,
            intended_price=1.0800
        )
        self.assertTrue(first)

        # Duplicate retry with same request_id MUST be rejected!
        duplicate = self.idempotency.register_request(
            request_id=req_id,
            symbol="EURUSD",
            order_type="BUY",
            volume=0.10,
            intended_price=1.0800
        )
        self.assertFalse(duplicate)

    def test_tca_reporter_formatting(self):
        """Proves Telegram and Markdown TCA reporting format properly."""
        self.tca.record_execution(
            request_id="REQ_REP",
            symbol="EURUSD",
            order_type="BUY",
            volume=0.10,
            intended_price=1.08000,
            filled_price=1.08002,
            spread_at_signal_pts=8.0,
            spread_at_fill_pts=8.0,
            latency_ms=12.0,
            retcode=10009
        )
        reporter = TCAReporter(self.tca)
        tg_text = reporter.format_telegram_summary()
        self.assertIn("Execution Quality", tg_text)
        self.assertIn("Avg Slippage", tg_text)

        md_text = reporter.format_markdown_report()
        self.assertIn("# Weekly Transaction Cost Analysis", md_text)
        self.assertIn("REQ_REP", md_text)


if __name__ == "__main__":
    unittest.main()
