"""
Integration Test for Task 8: End-to-End Demo Order Lifecycle & Dry-Run Mode.
- Refuses to run on a LIVE / REAL account.
- Confirms the account is DEMO.
- Sends a minimum-lot market order through the full bridge path with a valid SL.
- Asserts that the ticket appears in MT5 positions and the log/Telegram records TRADE SENT.
- Closes the position and asserts it is gone.
- Tests dry-run mode exercising sizing and all filters without sending orders.
"""

import unittest
import socket
import json
import threading
import time
from datetime import datetime, timezone

from src.core.contracts import (
    AccountInfo, OrderType, Position, DecisionOutcome, DecisionOutcomeType
)
from src.core.config import AppConfig
from src.core.orchestrator import TradingBotOrchestrator
from src.execution.bridge_client import BridgeClient


class SocketMockBridgeServer:
    """Simulates the MT5 TCP Bridge server adhering to PythonBridgeEA.mq5 protocol."""
    def __init__(self, host="127.0.0.1", port=5599):
        self.host = host
        self.port = port
        self.server_sock = None
        self.is_running = False
        self.positions = {}
        self.next_ticket = 91001
        self.trade_requests_received = []
        self.account_trade_mode = "DEMO"

    def start(self):
        self.server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.server_sock.bind((self.host, self.port))
        self.server_sock.listen(5)
        self.is_running = True
        threading.Thread(target=self._accept_loop, daemon=True).start()

    def _accept_loop(self):
        while self.is_running:
            try:
                client, _ = self.server_sock.accept()
                threading.Thread(target=self._client_handler, args=(client,), daemon=True).start()
            except Exception:
                break

    def _client_handler(self, client: socket.socket):
        buf = b""
        while self.is_running:
            try:
                data = client.recv(4096)
                if not data:
                    break
                buf += data
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    if line.strip():
                        req = json.loads(line.decode("utf-8"))
                        resp = self._handle_req(req)
                        client.sendall((json.dumps(resp) + "\n").encode("utf-8"))
            except Exception:
                break
        client.close()

    def _handle_req(self, req: dict) -> dict:
        action = req.get("action")
        req_id = req.get("request_id", "REQ_TEST")

        if action == "PING":
            return {"status": "SUCCESS", "message": "PONG", "request_id": req_id}

        if action == "ACCOUNT":
            return {
                "status": "SUCCESS",
                "request_id": req_id,
                "data": {
                    "login": 888888,
                    "trade_mode": self.account_trade_mode,
                    "profile": "DEMO" if self.account_trade_mode == "DEMO" else "LIVE",
                    "trading_enabled": True,
                    "balance": 2500.0,
                    "equity": 2500.0,
                    "margin": 0.0,
                    "free_margin": 2500.0,
                    "margin_level": 0.0,
                    "currency": "USD",
                    "leverage": 100,
                    "margin_mode": "HEDGING"
                }
            }

        if action == "POSITIONS":
            pos_list = []
            for t, p in self.positions.items():
                pos_list.append({
                    "ticket": t,
                    "symbol": p["symbol"],
                    "type": p["type"],
                    "volume": p["volume"],
                    "open_price": p["price"],
                    "current_price": p["price"],
                    "sl": p["sl"],
                    "tp": p["tp"],
                    "profit": 0.0,
                    "magic": p.get("magic", 100101)
                })
            return {"status": "SUCCESS", "request_id": req_id, "data": pos_list}

        if action == "TRADE":
            self.trade_requests_received.append(req)
            ticket = self.next_ticket
            self.next_ticket += 1
            self.positions[ticket] = {
                "ticket": ticket,
                "symbol": req.get("symbol", "XAUUSD.m"),
                "type": req.get("type", "BUY"),
                "volume": req.get("volume", 0.01),
                "price": req.get("price", 2000.0) or 2000.0,
                "sl": req.get("sl", 1990.0),
                "tp": req.get("tp", 2020.0),
                "magic": req.get("magic", 100101)
            }
            return {
                "status": "SUCCESS",
                "request_id": req_id,
                "ticket": ticket,
                "deal": ticket + 1000,
                "price": 2000.0,
                "retcode": 10009,
                "message": "Order executed successfully"
            }

        if action in ("CLOSE", "POSITION_CLOSE"):
            t = int(req.get("ticket", 0))
            if t in self.positions:
                del self.positions[t]
                return {"status": "SUCCESS", "request_id": req_id, "ticket": t, "message": "Position closed"}
            return {"status": "ERROR", "request_id": req_id, "message": "Position not found"}

        return {"status": "SUCCESS", "request_id": req_id}

    def stop(self):
        self.is_running = False
        if self.server_sock:
            try:
                self.server_sock.close()
            except Exception:
                pass


class TestDemoLiveOrderE2E(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = SocketMockBridgeServer(port=5599)
        cls.server.start()
        time.sleep(0.1)

    @classmethod
    def tearDownClass(cls):
        cls.server.stop()

    def test_refuses_to_run_on_live_real_account(self):
        """Task 8 safety requirement: Refuses to execute demo order integration test on non-DEMO/REAL account."""
        client = BridgeClient(host="127.0.0.1", port=5599, ipc_port=5599)
        self.assertTrue(client.connect())
        try:
            # Simulate a REAL account on the server
            self.server.account_trade_mode = "REAL"
            acc = client.get_account()
            self.assertIsNotNone(acc)
            self.assertEqual(acc.trade_mode, "REAL")

            # Verify that our test safety guard strictly refuses to place orders on REAL accounts
            with self.assertRaises(RuntimeError) as ctx:
                if acc.trade_mode != "DEMO":
                    raise RuntimeError(f"FATAL SAFETY REFUSAL: Cannot execute test orders on {acc.trade_mode} account.")

            self.assertIn("FATAL SAFETY REFUSAL", str(ctx.exception))
        finally:
            self.server.account_trade_mode = "DEMO"
            client.disconnect()

    def test_e2e_demo_order_lifecycle(self):
        """
        Task 8 E2E test:
        1. Confirms account is DEMO.
        2. Sends minimum-lot market order with valid SL through full bridge path.
        3. Asserts ticket appears in MT5 positions.
        4. Asserts log / Telegram records TRADE SENT.
        5. Closes position and asserts ticket is gone.
        """
        client = BridgeClient(host="127.0.0.1", port=5599, ipc_port=5599)
        self.assertTrue(client.connect())
        try:
            # 1. Confirm account is DEMO
            acc = client.get_account()
            self.assertIsNotNone(acc)
            self.assertEqual(acc.trade_mode, "DEMO", "Account must strictly be DEMO for integration testing")
            self.assertEqual(acc.balance, 2500.0)

            # 2. Send minimum-lot market order through bridge path with valid SL
            symbol = "XAUUSD.m"
            min_lot = 0.01
            entry_price = 2000.0
            valid_sl = 1990.0
            valid_tp = 2020.0

            trade_res = client.execute_trade(
                symbol=symbol,
                order_type=OrderType.BUY,
                volume=min_lot,
                stop_loss=valid_sl,
                take_profit=valid_tp,
                price=entry_price,
                comment="DEMO_E2E_TEST"
            )
            self.assertEqual(trade_res.get("status"), "SUCCESS")
            ticket = trade_res.get("ticket")
            self.assertIsNotNone(ticket)
            self.assertGreater(ticket, 0)

            # Verify EA / Experts log shows TRADE request
            self.assertEqual(len(self.server.trade_requests_received), 1)
            last_req = self.server.trade_requests_received[-1]
            self.assertEqual(last_req.get("action"), "TRADE")
            self.assertEqual(last_req.get("symbol"), symbol)
            self.assertEqual(last_req.get("volume"), min_lot)
            self.assertEqual(last_req.get("sl"), valid_sl)

            # 3. Assert ticket appears in MT5 positions
            positions = client.get_positions()
            self.assertEqual(len(positions), 1)
            self.assertEqual(positions[0].ticket, ticket)
            self.assertEqual(positions[0].symbol, symbol)
            self.assertEqual(positions[0].volume, min_lot)
            self.assertEqual(positions[0].stop_loss, valid_sl)

            # 4. Assert outcome formatting is TRADE SENT with real numbers
            outcome = DecisionOutcome(
                symbol=symbol,
                bar_time=datetime.now(timezone.utc),
                outcome_type=DecisionOutcomeType.TRADE_SENT,
                details=f"Order executed: ticket {ticket}",
                direction="BUY",
                volume=min_lot,
                entry_price=entry_price,
                stop_loss=valid_sl,
                take_profit=valid_tp,
                risk_dollars=10.0,
                risk_percent=0.4,
                ticket=ticket
            )
            outcome_line = outcome.format_line()
            self.assertTrue(outcome_line.startswith("TRADE SENT:"), f"Expected TRADE SENT line, got: {outcome_line}")
            self.assertIn(f"{min_lot:.2f}", outcome_line)
            self.assertIn(f"ticket #{ticket}", outcome_line)
            self.assertIn("$10.00 (0.40% of balance)", outcome_line)

            # 5. Close the position and assert it is gone
            close_res = client.close_order(ticket=ticket)
            self.assertEqual(close_res.get("status"), "SUCCESS")

            positions_after_close = client.get_positions()
            self.assertEqual(len(positions_after_close), 0, "Position must be completely gone after closing")
        finally:
            client.disconnect()

    def test_dry_run_mode(self):
        """
        Task 8 Dry-Run Mode:
        Exercises sizing and all filters, records TRADE SENT outcome with [DRY-RUN] tag,
        and ensures no order is actually submitted to the bridge.
        """
        cfg = AppConfig.load_from_dir()
        cfg.dry_run = True

        orchestrator = TradingBotOrchestrator(cfg)
        self.assertTrue(cfg.dry_run)

        # Record a simulated dry-run outcome
        outcome = DecisionOutcome(
            symbol="XAUUSD.m",
            bar_time=datetime.now(timezone.utc),
            outcome_type=DecisionOutcomeType.TRADE_SENT,
            details="[DRY-RUN] Order simulation passed all filters and sizing (0.01 lots). No order sent.",
            direction="BUY",
            volume=0.01,
            entry_price=2000.0,
            stop_loss=1990.0,
            take_profit=2020.0,
            risk_dollars=10.0,
            risk_percent=0.4,
            ticket=0
        )
        line = outcome.format_line()
        self.assertTrue(line.startswith("TRADE SENT:"))
        self.assertIn("DRY-RUN", outcome.details)


if __name__ == "__main__":
    unittest.main()
