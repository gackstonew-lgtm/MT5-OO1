"""
Unit Tests for Phase 5: Always-On Reliability, Watchdog & Telegram Controls
(`src/execution/watchdog.py` and `src/notifications/telegram_service.py`).
"""

import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

from src.core.contracts import OrderType, Position, SymbolInfo, TradeOrder
from src.execution.watchdog import FastWatchdog
from src.notifications.telegram_service import (
    TelegramCommandHandler, TelegramConfig, TelegramNotifier
)


class TestFastReliabilityWatchdog(unittest.TestCase):
    def setUp(self) -> None:
        self.watchdog = FastWatchdog(config_path="config/fast_mode.json")

    def test_heartbeat_timeout_enters_safe_state_and_reconnect_reconciles(self):
        self.watchdog.record_heartbeat(now_ts=1000.0)
        healthy, reason = self.watchdog.check_heartbeat_health(now_ts=1005.0)
        self.assertTrue(healthy)
        self.assertFalse(self.watchdog.safe_state_active)

        # Register an expected position
        pos1 = Position(
            ticket=701, symbol="EURUSD", order_type=OrderType.BUY, volume=0.10,
            open_price=1.1000, current_price=1.1010, stop_loss=1.0980, take_profit=1.1050,
            profit=10.0, open_time=datetime.now(timezone.utc)
        )
        self.watchdog.register_expected_position(pos1)

        # Simulate 20 seconds without heartbeat (> 15s timeout)
        healthy2, reason2 = self.watchdog.check_heartbeat_health(now_ts=1025.0)
        self.assertFalse(healthy2)
        self.assertTrue(self.watchdog.safe_state_active)
        self.assertIn("EA heartbeat timeout", reason2)
        self.assertEqual(len(self.watchdog.critical_alerts), 1)

        # Verify new trades are blocked while in safe state
        can_open, block_reason = self.watchdog.can_open_new_trade()
        self.assertFalse(can_open)
        self.assertIn("Watchdog Safe State active", block_reason)

        # Reconnect heartbeat with matching MT5 position -> reconciles and clears safe state
        report = self.watchdog.record_heartbeat(now_ts=1030.0, mt5_positions=[pos1])
        self.assertIsNotNone(report)
        self.assertTrue(report.in_sync)
        self.assertFalse(self.watchdog.safe_state_active)

    def test_reconciliation_detects_missing_unexpected_and_unprotected_positions(self):
        pos_expected = Position(
            ticket=801, symbol="EURUSD", order_type=OrderType.BUY, volume=0.10,
            open_price=1.1000, current_price=1.1010, stop_loss=1.0980, take_profit=1.1050,
            profit=10.0
        )
        self.watchdog.register_expected_position(pos_expected)

        # MT5 has closed #801 and has an unexpected #802 WITHOUT a stop loss
        pos_unexpected_no_sl = Position(
            ticket=802, symbol="XAUUSD", order_type=OrderType.SELL, volume=0.05,
            open_price=2300.0, current_price=2298.0, stop_loss=0.0, take_profit=2280.0,
            profit=10.0
        )
        report = self.watchdog.reconcile_positions([pos_unexpected_no_sl], now_ts=2000.0)
        self.assertFalse(report.in_sync)
        self.assertEqual(report.missing_in_mt5, {801})
        self.assertEqual(report.unexpected_in_mt5, {802})
        self.assertEqual(report.unprotected_tickets, [802])
        self.assertTrue(self.watchdog.safe_state_active)

    def test_stale_tick_guard_and_clock_drift_and_weekend_guard(self):
        now_wed = datetime(2026, 1, 7, 14, 0, 0, tzinfo=timezone.utc)  # Wednesday 14:00 UTC
        stale_sym = SymbolInfo(
            symbol="EURUSD",
            last_tick_time=now_wed - timedelta(seconds=8.0)  # 8s old > 5s limit
        )
        ok_tick, tick_reason = self.watchdog.validate_tick_freshness(stale_sym, now_utc=now_wed)
        self.assertFalse(ok_tick)
        self.assertIn("Stale tick on EURUSD", tick_reason)

        # Clock drift check (> 10s drift)
        self.watchdog.clear_safe_state()
        ok_clock, clock_reason = self.watchdog.validate_clock_sanity(
            broker_utc_time=now_wed - timedelta(seconds=15.0),
            local_utc_time=now_wed
        )
        self.assertFalse(ok_clock)
        self.assertIn("Clock drift sanity check failed", clock_reason)

        # Weekend check (Saturday)
        sat_dt = datetime(2026, 1, 10, 12, 0, 0, tzinfo=timezone.utc)
        mkt_open, mkt_reason = self.watchdog.is_market_open(sat_dt)
        self.assertFalse(mkt_open)
        self.assertIn("Saturday", mkt_reason)

    def test_graceful_shutdown_cancels_orphaned_pendings_and_leaves_sl_tp_protected(self):
        pending_orders = [
            TradeOrder(symbol="EURUSD", order_type=OrderType.BUY_LIMIT, volume=0.1, entry_price=1.0950, stop_loss=1.0930, take_profit=1.1000, ticket=9001),
            TradeOrder(symbol="GBPUSD", order_type=OrderType.SELL_LIMIT, volume=0.1, entry_price=1.2750, stop_loss=1.2770, take_profit=1.2700, ticket=9002),
        ]
        open_positions = [
            Position(ticket=9101, symbol="EURUSD", order_type=OrderType.BUY, volume=0.1, open_price=1.1000, current_price=1.1010, stop_loss=1.0980, take_profit=1.1050, profit=10.0)
        ]
        cancelled_list = []
        res = self.watchdog.graceful_shutdown(
            pending_orders=pending_orders,
            open_positions=open_positions,
            cancel_order_callback=lambda t: (cancelled_list.append(t) or True)
        )
        self.assertEqual(res["cancelled_pending_tickets"], [9001, 9002])
        self.assertEqual(res["open_positions_left_protected"], [9101])
        self.assertTrue(self.watchdog.safe_state_active)

    def test_telegram_fastmode_flatten_auth_and_margin_commands(self):
        cfg = MagicMock(bot_token="test_token", chat_id="123456", enabled=False, proxy=None)
        notifier = MagicMock(spec=TelegramNotifier)
        closed_counter = {"count": 0}

        def mock_closeall() -> int:
            closed_counter["count"] += 3
            return 3

        handler = TelegramCommandHandler(
            config=cfg,
            notifier=notifier,
            closeall_handler=mock_closeall,
            balance_provider=lambda: {"balance": 5000.0, "equity": 5000.0, "margin": 200.0, "free_margin": 4800.0, "margin_level": 2500.0},
            positions_provider=lambda: []
        )
        replies = []
        handler._reply = lambda text: replies.append(text)

        # Use a temp config file for /fastmode toggle so we don't mutate repo config
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as tf:
            json.dump({"enabled": False, "entry_timeframe": "M5", "default_account_mode": "DEMO"}, tf)
            temp_cfg_path = tf.name

        try:
            handler.fast_mode_config_path = temp_cfg_path

            # 1. Unauthorized chat_id attempting /flatten must be rejected!
            handler._process_message({"chat": {"id": "999999"}, "from": {"first_name": "Attacker"}, "text": "/flatten"})
            self.assertEqual(closed_counter["count"], 0)
            self.assertEqual(len(replies), 0)

            # 2. Authorized chat_id executing /flatten succeeds
            handler._process_message({"chat": {"id": "123456"}, "from": {"first_name": "Admin"}, "text": "/flatten"})
            self.assertEqual(closed_counter["count"], 3)
            self.assertIn("EMERGENCY FLATTEN EXECUTED", replies[-1])

            # 3. /fastmode on -> enables in config; /fastmode off -> disables in config
            handler._process_message({"chat": {"id": "123456"}, "from": {"first_name": "Admin"}, "text": "/fastmode on"})
            self.assertIn("FAST MODE SCALPING LAYER: ENABLED", replies[-1])
            with open(temp_cfg_path, "r", encoding="utf-8") as f:
                self.assertTrue(json.load(f)["enabled"])

            handler._process_message({"chat": {"id": "123456"}, "from": {"first_name": "Admin"}, "text": "/fastmode off"})
            self.assertIn("FAST MODE SCALPING LAYER: DISABLED", replies[-1])
            with open(temp_cfg_path, "r", encoding="utf-8") as f:
                self.assertFalse(json.load(f)["enabled"])

            # 4. /margin command
            handler._process_message({"chat": {"id": "123456"}, "from": {"first_name": "Admin"}, "text": "/margin"})
            self.assertIn("MARGIN & OPEN-RISK HEALTH", replies[-1])
            self.assertIn("2500.0%", replies[-1])
        finally:
            if os.path.exists(temp_cfg_path):
                os.remove(temp_cfg_path)


if __name__ == "__main__":
    unittest.main()
