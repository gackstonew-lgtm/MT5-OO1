"""
Integration test for Python-MT5 Bridge Communication.
Simulates MQL5 socket behavior to verify PING, ACCOUNT, POSITIONS, RATES, and TRADE.
"""

import unittest
import socket
import threading
import time
import json

from src.execution.bridge_client import BridgeClient
from src.core.contracts import OrderType


class TestExecutionBridge(unittest.TestCase):
    def setUp(self):
        self.port = 5557
        self.bridge = BridgeClient(host="127.0.0.1", port=self.port, timeout_ms=2000)
        self.assertTrue(self.bridge.start_server())
        self._stop_sim = False

    def tearDown(self):
        self._stop_sim = True
        self.bridge.disconnect()

    def _simulated_mql5_ea(self):
        """Simulates native MQL5 socket client behavior."""
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(2.0)
        connected = False
        for _ in range(10):
            try:
                sock.connect(("127.0.0.1", self.port))
                connected = True
                break
            except Exception:
                time.sleep(0.1)

        if not connected:
            return

        while not self._stop_sim:
            try:
                data = sock.recv(4096)
                if not data:
                    break
                msg = json.loads(data.decode('utf-8').strip())
                action = msg.get("action")

                if action == "PING":
                    resp = {"status": "SUCCESS", "message": "PONG", "server_time": 1700000000}
                elif action == "ACCOUNT":
                    resp = {
                        "status": "SUCCESS",
                        "data": {
                            "login": 123456, "balance": 10000.0, "equity": 10250.0,
                            "margin": 200.0, "free_margin": 10050.0, "margin_level": 5125.0,
                            "currency": "USD", "leverage": 100
                        }
                    }
                elif action == "POSITIONS":
                    resp = {
                        "status": "SUCCESS",
                        "data": [{
                            "ticket": 78910, "symbol": "EURUSD", "type": "BUY",
                            "volume": 0.25, "open_price": 1.0850, "current_price": 1.0870,
                            "sl": 1.0830, "tp": 1.0900, "profit": 50.0, "magic": 100101, "comment": "TEST"
                        }]
                    }
                elif action == "RATES":
                    resp = {
                        "status": "SUCCESS",
                        "symbol": "EURUSD",
                        "timeframe": "M15",
                        "count": 2,
                        "data": [
                            {"time": 1700000000, "open": 1.0850, "high": 1.0860, "low": 1.0840, "close": 1.0855, "tick_volume": 100, "spread": 1},
                            {"time": 1700000900, "open": 1.0855, "high": 1.0870, "low": 1.0850, "close": 1.0865, "tick_volume": 120, "spread": 1}
                        ]
                    }
                elif action == "TRADE":
                    resp = {"status": "SUCCESS", "ticket": 99901, "deal": 88801, "price": 1.0852, "retcode": 10009}
                elif action == "CANCEL":
                    resp = {"status": "SUCCESS", "ticket": msg.get("ticket"), "message": "Pending order cancelled"}
                elif action == "KILL_SWITCH":
                    resp = {"status": "SUCCESS", "message": "Kill switch active", "positions_closed": 0}
                elif action == "CLOSE":
                    resp = {"status": "SUCCESS", "ticket": msg.get("ticket")}
                elif action == "MODIFY":
                    resp = {"status": "SUCCESS", "ticket": msg.get("ticket"), "sl": msg.get("sl"), "tp": msg.get("tp")}
                else:
                    resp = {"status": "ERROR", "message": "Unknown action"}

                sock.sendall((json.dumps(resp) + "\n").encode('utf-8'))
            except socket.timeout:
                continue
            except Exception:
                break

        sock.close()

    def test_bridge_actions(self):
        # Start simulated MQL5 EA
        t = threading.Thread(target=self._simulated_mql5_ea, daemon=True)
        t.start()
        time.sleep(0.3)

        # 1. PING
        self.assertTrue(self.bridge.ping())

        # 2. ACCOUNT
        acc = self.bridge.get_account()
        self.assertIsNotNone(acc)
        self.assertEqual(acc.account_number, 123456)
        self.assertEqual(acc.equity, 10250.0)

        # 3. POSITIONS
        positions = self.bridge.get_positions()
        self.assertEqual(len(positions), 1)
        self.assertEqual(positions[0].ticket, 78910)
        self.assertEqual(positions[0].symbol, "EURUSD")
        self.assertEqual(positions[0].order_type, OrderType.BUY)

        # 4. RATES
        rates = self.bridge.get_rates("EURUSD", "M15", 2)
        self.assertEqual(rates.get("status"), "SUCCESS")
        self.assertEqual(len(rates.get("data", [])), 2)

        # 5. TRADE
        trade_res = self.bridge.execute_trade(
            symbol="EURUSD",
            order_type=OrderType.BUY,
            volume=0.25,
            stop_loss=1.0830,
            take_profit=1.0900
        )
        self.assertEqual(trade_res.get("status"), "SUCCESS")
        self.assertEqual(trade_res.get("ticket"), 99901)
        self.assertEqual(trade_res.get("deal"), 88801)

        # 6. MODIFY
        mod_res = self.bridge.modify_order(ticket=99901, sl=1.0840, tp=1.0920)
        self.assertEqual(mod_res.get("status"), "SUCCESS")

        # 7. CANCEL PENDING
        canc_res = self.bridge.cancel_order(ticket=99902)
        self.assertEqual(canc_res.get("status"), "SUCCESS")

        # 8. KILL SWITCH
        ks_res = self.bridge.kill_switch(close_all=False)
        self.assertEqual(ks_res.get("status"), "SUCCESS")


if __name__ == "__main__":
    unittest.main()
