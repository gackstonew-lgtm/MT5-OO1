"""
Unit & Integration Test Suite for Multi-Connection Bridge Server.
Tests:
1. Three simulated EA clients (XAUUSD.m, BTCUSD.m, EURUSD.m) with different magics connecting at once.
2. Concurrent request isolation and individual query routing.
3. One client disconnecting cleanly without impacting others.
4. Duplicate connection for an active magic number rejected with clear message.
5. Stale connection replaced when new connection with same magic reconnects.
6. Administrative broadcast command (KILL_SWITCH / CLOSE_ALL) reaching all active connections with aggregated results.
"""

import unittest
import socket
import threading
import time
import json
from typing import Dict, Any, Optional

from src.execution.bridge_server import PersistentBridgeServer


class SimulatedEA:
    def __init__(self, host: str, port: int, symbol: str, magic: int, account: int = 1200057798):
        self.host = host
        self.port = port
        self.symbol = symbol
        self.magic = magic
        self.account = account
        self.sock: Optional[socket.socket] = None
        self.is_running = False
        self.received_messages = []
        self.last_rejection: Optional[Dict[str, Any]] = None
        self._thread: Optional[threading.Thread] = None

    def connect(self) -> bool:
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.settimeout(2.0)
        try:
            self.sock.connect((self.host, self.port))
            # Send HELLO handshake
            handshake = {
                "action": "HELLO",
                "client": "PythonBridgeEA",
                "version": "3.10",
                "symbol": self.symbol,
                "account": self.account,
                "magic": self.magic,
                "broker_utc_offset": 7200
            }
            self.sock.sendall((json.dumps(handshake) + "\n").encode("utf-8"))

            self.is_running = True
            self._thread = threading.Thread(target=self._run_loop, daemon=True)
            self._thread.start()
            return True
        except Exception:
            return False

    def _run_loop(self):
        rx = b""
        while self.is_running and self.sock:
            try:
                chunk = self.sock.recv(4096)
                if not chunk:
                    break
                rx += chunk
                while b"\n" in rx:
                    line, rx = rx.split(b"\n", 1)
                    text = line.decode("utf-8", errors="ignore").strip()
                    if not text:
                        continue
                    try:
                        msg = json.loads(text)
                        self.received_messages.append(msg)
                        self._handle_msg(msg)
                    except json.JSONDecodeError:
                        pass
            except socket.timeout:
                continue
            except Exception:
                break

    def _handle_msg(self, msg: Dict[str, Any]):
        action = msg.get("action")
        req_id = msg.get("request_id")

        if action == "REJECTED" or msg.get("status") == "ERROR" and "Duplicate" in str(msg.get("reason", "")):
            self.last_rejection = msg
            return

        if action == "PING":
            resp = {
                "status": "SUCCESS",
                "message": "PONG",
                "request_id": req_id,
                "magic": self.magic,
                "symbol": self.symbol,
                "server_time": int(time.time()),
                "utc_time": int(time.time())
            }
            try:
                self.sock.sendall((json.dumps(resp) + "\n").encode("utf-8"))
            except Exception:
                pass
        elif action == "KILL_SWITCH":
            resp = {
                "status": "SUCCESS",
                "request_id": req_id,
                "action": "KILL_SWITCH",
                "symbol": self.symbol,
                "magic": self.magic,
                "positions_closed": 1 if self.symbol == "XAUUSD.m" else 0
            }
            try:
                self.sock.sendall((json.dumps(resp) + "\n").encode("utf-8"))
            except Exception:
                pass
        elif action == "RATES":
            resp = {
                "status": "SUCCESS",
                "request_id": req_id,
                "symbol": self.symbol,
                "magic": self.magic,
                "data": [{"time": 1700000000, "close": 2000.0 if "XAU" in self.symbol else 1.08}]
            }
            try:
                self.sock.sendall((json.dumps(resp) + "\n").encode("utf-8"))
            except Exception:
                pass

    def disconnect(self):
        self.is_running = False
        if self.sock:
            try:
                self.sock.close()
            except Exception:
                pass
            self.sock = None


class TestMultiConnectionBridge(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ea_port = 5575
        cls.ipc_port = 5576
        cls.server = PersistentBridgeServer(
            ea_port=cls.ea_port,
            ipc_port=cls.ipc_port,
            heartbeat_interval_sec=0.5,
            stale_timeout_sec=1.5
        )
        cls.server.start()
        time.sleep(0.3)

    @classmethod
    def tearDownClass(cls):
        cls.server.stop()

    def test_01_three_simulated_eas_connect_concurrently(self):
        """1. Three simulated EA clients connect at once with different magics."""
        ea1 = SimulatedEA("127.0.0.1", self.ea_port, "XAUUSD.m", magic=100106)
        ea2 = SimulatedEA("127.0.0.1", self.ea_port, "BTCUSD.m", magic=100102)
        ea3 = SimulatedEA("127.0.0.1", self.ea_port, "EURUSD.m", magic=100103)

        try:
            self.assertTrue(ea1.connect())
            self.assertTrue(ea2.connect())
            self.assertTrue(ea3.connect())

            # Await registration
            time.sleep(0.5)

            clients = self.server.get_connected_clients()
            self.assertEqual(len(clients), 3)

            magics = {c.magic for c in clients}
            self.assertEqual(magics, {100106, 100102, 100103})

            # Check individual targeted routing
            resp_gold = self.server.forward_request_to_ea({"action": "RATES", "symbol": "XAUUSD.m"})
            self.assertEqual(resp_gold.get("status"), "SUCCESS")
            self.assertEqual(resp_gold.get("symbol"), "XAUUSD.m")

            resp_btc = self.server.forward_request_to_ea({"action": "RATES", "magic": 100102})
            self.assertEqual(resp_btc.get("status"), "SUCCESS")
            self.assertEqual(resp_btc.get("symbol"), "BTCUSD.m")

        finally:
            ea1.disconnect()
            ea2.disconnect()
            ea3.disconnect()
            time.sleep(0.3)

    def test_02_duplicate_rejected_and_stale_replaced(self):
        """2. Duplicate connection rejected; stale connection cleanly replaced."""
        ea_orig = SimulatedEA("127.0.0.1", self.ea_port, "EURUSD.m", magic=100103)
        self.assertTrue(ea_orig.connect())
        time.sleep(0.4)

        try:
            # Attempt duplicate connection with same magic while live
            ea_dup = SimulatedEA("127.0.0.1", self.ea_port, "EURUSD.m", magic=100103)
            self.assertTrue(ea_dup.connect())
            time.sleep(0.5)

            # ea_dup must be rejected
            self.assertIsNotNone(ea_dup.last_rejection)
            self.assertEqual(ea_dup.last_rejection.get("status"), "ERROR")
            self.assertIn("Duplicate", ea_dup.last_rejection.get("reason", ""))
            ea_dup.disconnect()

            # Original connection remains active and registered
            self.assertIsNotNone(self.server.get_connection_by_magic(100103))

            # Simulate stale state on original by closing its socket silently
            ea_orig.disconnect()
            # Wait for stale timeout (server stale_timeout_sec is 1.5s)
            time.sleep(2.0)

            # Reconnecting EA with same magic should now replace the stale slot
            ea_reconnect = SimulatedEA("127.0.0.1", self.ea_port, "EURUSD.m", magic=100103)
            self.assertTrue(ea_reconnect.connect())
            time.sleep(0.5)

            self.assertIsNone(ea_reconnect.last_rejection)
            conn = self.server.get_connection_by_magic(100103)
            self.assertIsNotNone(conn)
            self.assertEqual(conn.status, "ACTIVE")
            ea_reconnect.disconnect()

        finally:
            ea_orig.disconnect()
            time.sleep(0.3)

    def test_03_kill_switch_broadcast_and_disconnect_isolation(self):
        """3. Kill switch broadcast reaches all connected EAs, one disconnect does not affect others."""
        ea1 = SimulatedEA("127.0.0.1", self.ea_port, "XAUUSD.m", magic=100106)
        ea2 = SimulatedEA("127.0.0.1", self.ea_port, "BTCUSD.m", magic=100102)

        self.assertTrue(ea1.connect())
        self.assertTrue(ea2.connect())
        time.sleep(0.5)

        try:
            # Broadcast kill switch
            res = self.server.forward_request_to_ea({"action": "KILL_SWITCH"})
            self.assertEqual(res.get("status"), "SUCCESS")
            results = res.get("results", {})
            self.assertIn("XAUUSD.m", results)
            self.assertIn("BTCUSD.m", results)
            self.assertEqual(results["XAUUSD.m"].get("status"), "SUCCESS")
            self.assertEqual(results["BTCUSD.m"].get("status"), "SUCCESS")

            # Disconnect ea1 cleanly
            ea1.disconnect()
            time.sleep(0.4)

            # ea2 must still be fully responsive
            self.assertEqual(len(self.server.get_connected_clients()), 1)
            resp = self.server.forward_request_to_ea({"action": "RATES", "symbol": "BTCUSD.m"})
            self.assertEqual(resp.get("status"), "SUCCESS")

        finally:
            ea1.disconnect()
            ea2.disconnect()
            time.sleep(0.3)


if __name__ == "__main__":
    unittest.main()
