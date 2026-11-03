"""
Unit tests for Phase 1: Low-Latency Execution Path, Persistent Socket,
Non-Blocking Notification Queue, Idempotent Orders, Filling-Mode Fallback,
Stop/Freeze Levels, and Requote Retry Bounds.
"""

import time
import unittest
from datetime import datetime, timezone
from src.core.contracts import SymbolInfo, OrderType
from src.execution.async_queue import NonBlockingExecutionQueue
from src.execution.persistent_socket import (
    BrokerConstraintValidator, LowLatencyOrderDispatcher
)


class TestFastExecutionLatency(unittest.TestCase):
    def setUp(self):
        self.sym_info = SymbolInfo(
            symbol="XAUUSD.m",
            digits=2,
            point=0.01,
            tick_size=0.01,
            tick_value=1.0,
            contract_size=100.0,
            min_lot=0.01,
            max_lot=50.0,
            lot_step=0.01,
            spread=20.0,
            ask=2500.20,
            bid=2500.00,
            stops_level=15,
            freeze_level=10,
            filling_mode="FOK"
        )

    def test_non_blocking_notification_queue(self):
        """Verifies Telegram/journal tasks execute asynchronously without blocking the caller."""
        results = []
        q = NonBlockingExecutionQueue(max_size=100, start_worker=False)

        def slow_task(msg: str):
            results.append(msg)

        ok = q.enqueue("TELEGRAM", slow_task, "Trade filled #1001")
        self.assertTrue(ok)
        self.assertEqual(q.pending_count, 1)
        # Enqueue must take less than 5ms (typically < 50 microseconds)
        self.assertLess(q.last_enqueue_latency_us, 5000.0)

        # Drain synchronously for deterministic test assertion
        processed = q.flush_sync()
        self.assertEqual(processed, 1)
        self.assertEqual(results, ["Trade filled #1001"])

    def test_latency_instrumentation_and_slippage_metrics(self):
        """Verifies nanosecond stage timestamps and slippage calculation."""
        dispatcher = LowLatencyOrderDispatcher(max_retries=2, retry_delay_ms=1)
        t0 = time.perf_counter_ns() - 1_000_000
        t_ana = t0 + 500_000      # +0.5ms
        t_chk = t_ana + 300_000   # +0.3ms

        def mock_send(payload):
            return {
                "status": "SUCCESS",
                "retcode": 10009,
                "ticket": 77001,
                "price": 2500.25  # 5 pts adverse slippage from 2500.20
            }

        cid = dispatcher.generate_client_order_id(
            "XAUUSD.m", OrderType.BUY, datetime(2026, 11, 3, 12, 0, tzinfo=timezone.utc), 2500.20
        )
        resp, rec = dispatcher.execute_idempotent_order(
            client_order_id=cid,
            symbol="XAUUSD.m",
            direction=OrderType.BUY,
            volume=0.10,
            requested_price=2500.20,
            stop_loss=2495.00,
            take_profit=2515.00,
            symbol_info=self.sym_info,
            send_fn=mock_send,
            bar_received_ns=t0,
            analysis_done_ns=t_ana,
            checklist_passed_ns=t_chk
        )
        self.assertEqual(resp["status"], "SUCCESS")
        self.assertEqual(rec.status, "FILLED")
        self.assertAlmostEqual(rec.analysis_ms, 0.5, places=1)
        self.assertAlmostEqual(rec.checklist_ms, 0.3, places=1)
        self.assertGreaterEqual(rec.total_ms, 0.8)
        self.assertAlmostEqual(rec.slippage_points, 5.0, places=1)
        self.assertEqual(rec.spread_at_entry_points, 20.0)

    def test_idempotent_order_ids_under_retry_and_reconnect(self):
        """Duplicate submission of the same client_order_id must never send a second broker order."""
        dispatcher = LowLatencyOrderDispatcher()
        send_calls = []

        def mock_send(payload):
            send_calls.append(payload)
            return {"status": "SUCCESS", "retcode": 10009, "ticket": 88001, "price": 2500.20}

        cid = "CID_XAUUSD_BUY_TEST123"
        resp1, rec1 = dispatcher.execute_idempotent_order(
            client_order_id=cid,
            symbol="XAUUSD.m",
            direction=OrderType.BUY,
            volume=0.10,
            requested_price=2500.20,
            stop_loss=2495.00,
            take_profit=2515.00,
            symbol_info=self.sym_info,
            send_fn=mock_send
        )
        resp2, rec2 = dispatcher.execute_idempotent_order(
            client_order_id=cid,
            symbol="XAUUSD.m",
            direction=OrderType.BUY,
            volume=0.10,
            requested_price=2500.20,
            stop_loss=2495.00,
            take_profit=2515.00,
            symbol_info=self.sym_info,
            send_fn=mock_send
        )
        # Broker send_fn called only once!
        self.assertEqual(len(send_calls), 1)
        self.assertEqual(rec1.status, "FILLED")
        self.assertEqual(rec2.status, "DUPLICATE_PREVENTED")
        self.assertEqual(resp2["ticket"], 88001)

    def test_filling_mode_auto_detection_and_fallback(self):
        """If broker rejects FOK with retcode 10030 (INVALID_FILL), automatically falls back to IOC/RETURN."""
        self.assertEqual(BrokerConstraintValidator.resolve_filling_mode(1), "FOK")
        self.assertEqual(BrokerConstraintValidator.resolve_filling_mode(2), "IOC")
        self.assertEqual(BrokerConstraintValidator.resolve_filling_mode(0), "RETURN")

        dispatcher = LowLatencyOrderDispatcher(max_retries=3, retry_delay_ms=1)
        modes_tried = []

        def mock_send(payload):
            m = payload.get("filling_mode")
            modes_tried.append(m)
            if m == "FOK":
                return {"status": "ERROR", "retcode": 10030, "message": "Invalid fill mode"}
            return {"status": "SUCCESS", "retcode": 10009, "ticket": 88002, "price": 2500.20}

        resp, rec = dispatcher.execute_idempotent_order(
            client_order_id="CID_FILL_FALLBACK",
            symbol="XAUUSD.m",
            direction=OrderType.BUY,
            volume=0.10,
            requested_price=2500.20,
            stop_loss=2495.00,
            take_profit=2515.00,
            symbol_info=self.sym_info,
            send_fn=mock_send
        )
        self.assertEqual(modes_tried, ["FOK", "IOC"])
        self.assertEqual(rec.filling_mode, "IOC")
        self.assertEqual(rec.status, "FILLED")

    def test_stops_level_and_freeze_level_handling(self):
        """Enforces broker stops_level at entry and freeze_level during modification."""
        # 1. Missing server-side SL or TP -> rejected
        ok, err = BrokerConstraintValidator.validate_server_side_stops(
            OrderType.BUY, 2500.20, 0.0, 2510.00, self.sym_info
        )
        self.assertFalse(ok)
        self.assertIn("strictly > 0.0", err)

        # 2. SL distance 5 pts < stops_level (15 pts = $0.15) -> rejected
        ok_close, err_close = BrokerConstraintValidator.validate_server_side_stops(
            OrderType.BUY, 2500.20, 2500.15, 2510.00, self.sym_info
        )
        self.assertFalse(ok_close)
        self.assertIn("stops_level", err_close)

        # 3. Freeze level check: current price 2500.05 is within 10 pts ($0.10) of current SL 2500.00
        ok_mod, err_mod = BrokerConstraintValidator.validate_modification_constraints(
            direction=OrderType.BUY,
            current_price=2500.05,
            current_sl=2500.00,
            current_tp=2515.00,
            new_sl=2500.02,
            new_tp=2515.00,
            symbol_info=self.sym_info
        )
        self.assertFalse(ok_mod)
        self.assertIn("freeze_level", err_mod)

    def test_requote_retry_bounds_with_quote_revalidation(self):
        """Transient requotes (10004) retry up to max_retries and abort if spread spikes."""
        dispatcher = LowLatencyOrderDispatcher(max_retries=2, retry_delay_ms=1)
        attempts = [0]

        def mock_send(payload):
            attempts[0] += 1
            if attempts[0] <= 2:
                return {"status": "ERROR", "retcode": 10004, "message": "Requote"}
            return {"status": "SUCCESS", "retcode": 10009, "ticket": 99001, "price": 2500.22}

        def mock_quote_ok(sym):
            return (2500.02, 2500.22, 20.0)

        resp, rec = dispatcher.execute_idempotent_order(
            client_order_id="CID_REQUOTE_OK",
            symbol="XAUUSD.m",
            direction=OrderType.BUY,
            volume=0.10,
            requested_price=2500.20,
            stop_loss=2495.00,
            take_profit=2515.00,
            symbol_info=self.sym_info,
            send_fn=mock_send,
            quote_refresh_fn=mock_quote_ok,
            max_spread_points=35.0
        )
        self.assertEqual(rec.status, "FILLED")
        self.assertEqual(rec.requote_count, 2)

        # Now test abort when spread widens on retry
        def mock_quote_wide_spread(sym):
            return (2499.50, 2500.50, 100.0)

        resp_abort, rec_abort = dispatcher.execute_idempotent_order(
            client_order_id="CID_REQUOTE_ABORT",
            symbol="XAUUSD.m",
            direction=OrderType.BUY,
            volume=0.10,
            requested_price=2500.20,
            stop_loss=2495.00,
            take_profit=2515.00,
            symbol_info=self.sym_info,
            send_fn=lambda p: {"status": "ERROR", "retcode": 10004, "message": "Requote"},
            quote_refresh_fn=mock_quote_wide_spread,
            max_spread_points=35.0
        )
        self.assertEqual(rec_abort.status, "FAILED")
        self.assertIn("spread widened", resp_abort["message"])


if __name__ == "__main__":
    unittest.main()
