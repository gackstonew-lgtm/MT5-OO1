"""
High-Performance Python-MT5 Communication Bridge Client.
Supports both:
1. IPC Client mode via persistent Bridge Server on 127.0.0.1:5556 (recommended).
2. Direct TCP mode on 127.0.0.1:5555.
Implements robust newline-delimited JSON message framing, timestamped logging,
heartbeat monitoring, request deduplication, and broker execution validation.
"""

from __future__ import annotations
import json
import socket
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Dict, Any, Optional, List

from src.core.contracts import AccountInfo, Position, OrderType


def get_timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]


class BridgeClient:
    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 5555,
        ipc_port: int = 5556,
        timeout_ms: int = 4000
    ):
        self.host = host
        self.port = port
        self.ipc_port = ipc_port
        self.timeout_ms = timeout_ms

        self._lock = threading.Lock()
        self._server_socket: Optional[socket.socket] = None
        self._client_socket: Optional[socket.socket] = None
        self._client_addr: Optional[tuple] = None
        self._is_running = False
        self._listener_thread: Optional[threading.Thread] = None

        self._connected = False
        self._last_heartbeat = 0.0
        self._last_ping_latency_ms = 0.0
        self.handshake_data: Dict[str, Any] = {}
        self.broker_utc_offset: int = 0
        self._rx_buffer: bytes = b""

    def log(self, tag: str, message: str) -> None:
        print(f"[{get_timestamp()}] [PythonBridge] [{tag}] {message}")

    def _send_ipc_request(self, payload: Dict[str, Any], timeout_sec: float = 6.0) -> Optional[Dict[str, Any]]:
        """Sends request to persistent bridge server over IPC port 5556."""
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(timeout_sec)
        try:
            s.connect((self.host, self.ipc_port))
            if "request_id" not in payload:
                payload["request_id"] = f"REQ_{uuid.uuid4().hex[:12].upper()}"
            msg = json.dumps(payload) + "\n"
            s.sendall(msg.encode("utf-8"))

            rx = b""
            start_t = time.time()
            while time.time() - start_t < timeout_sec:
                chunk = s.recv(4096)
                if not chunk:
                    break
                rx += chunk
                if b"\n" in rx:
                    line, _ = rx.split(b"\n", 1)
                    text = line.decode("utf-8", errors="ignore").strip()
                    if text:
                        resp = json.loads(text)
                        if "broker_utc_offset" in resp:
                            self.broker_utc_offset = int(resp["broker_utc_offset"])
                        elif "data" in resp and isinstance(resp["data"], dict) and "broker_utc_offset" in resp["data"]:
                            self.broker_utc_offset = int(resp["data"]["broker_utc_offset"])
                        return resp
            return {"status": "ERROR", "message": "IPC read timed out awaiting response"}
        except ConnectionRefusedError:
            return None  # Daemon not running on 5556, fall back to direct mode
        except Exception as e:
            return {"status": "ERROR", "message": f"IPC error: {e}"}
        finally:
            try:
                s.close()
            except Exception:
                pass

    def start_server(self) -> bool:
        """Starts direct TCP bridge server on 127.0.0.1:5555 (fallback mode)."""
        with self._lock:
            if self._is_running:
                return True

            try:
                self._server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                # Exclusive bind (see bridge_server.make_exclusive_server_socket): never co-listen with the
                # persistent daemon on Windows, otherwise EA/IPC traffic is split and requests time out.
                if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
                    self._server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
                else:
                    self._server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                self._server_socket.bind((self.host, self.port))
                self._server_socket.listen(5)
                self._server_socket.settimeout(1.0)
                self._is_running = True

                self._listener_thread = threading.Thread(target=self._listen_loop, daemon=True)
                self._listener_thread.start()
                self.log("SERVER_STARTED", f"Listening on {self.host}:{self.port} (Awaiting MT5 EA connection...)")
                return True
            except Exception as e:
                self.log("ERROR", f"Failed to bind TCP server on {self.host}:{self.port}: {e}")
                return False

    def connect(self) -> bool:
        """Checks IPC connectivity or starts direct server."""
        # Check if persistent server is already running on IPC port
        ipc_test = self._send_ipc_request({"action": "PING"}, timeout_sec=2.0)
        if ipc_test is not None:
            self.log("IPC_CONNECTED", f"Connected to Persistent Bridge Server on {self.host}:{self.ipc_port}")
            self._connected = True
            return True
        return self.start_server()

    def wait_for_connection(self, timeout_seconds: float = 15.0) -> bool:
        """Blocks until the MT5 EA connection is confirmed."""
        start_t = time.time()
        self.log("WAIT", f"Waiting up to {timeout_seconds}s for MT5 EA connection...")
        while time.time() - start_t < timeout_seconds:
            if self.is_connected():
                return True
            time.sleep(0.5)
        self.log("TIMEOUT", "No MT5 connection confirmed within timeout period.")
        return False

    def _listen_loop(self) -> None:
        """Background listener accepting connections from MQL5 (direct mode)."""
        while self._is_running:
            try:
                client_sock, addr = self._server_socket.accept()
                client_sock.settimeout(self.timeout_ms / 1000.0)

                with self._lock:
                    if self._client_socket:
                        try:
                            self._client_socket.close()
                        except Exception:
                            pass
                    self._client_socket = client_sock
                    self._client_addr = addr
                    self._connected = True
                    self._last_heartbeat = time.time()
                    self._rx_buffer = b""

                self.log("CONNECT", f"Accepted MT5 connection from {addr[0]}:{addr[1]}")
            except socket.timeout:
                continue
            except Exception as e:
                if self._is_running:
                    self.log("ERROR", f"Accept loop exception: {e}")
                break

    def disconnect(self) -> None:
        """Stops server and terminates client connection."""
        self._is_running = False
        with self._lock:
            if self._client_socket:
                try:
                    self._client_socket.close()
                except Exception:
                    pass
                self._client_socket = None
            if self._server_socket:
                try:
                    self._server_socket.close()
                except Exception:
                    pass
                self._server_socket = None
            self._connected = False
        self.log("DISCONNECTED", "Bridge client disconnected.")

    def is_connected(self) -> bool:
        # Check direct EA connection first if running direct server
        if self._connected and (self._client_socket is not None):
            return True
        # Check IPC connection if IPC port specified
        if self.ipc_port is not None:
            ipc_res = self._send_ipc_request({"action": "PING"}, timeout_sec=1.5)
            if ipc_res is not None and ipc_res.get("status") == "SUCCESS":
                return True
        return False

    def _read_message(self, timeout_sec: float) -> Optional[Dict[str, Any]]:
        """Reads a newline-delimited JSON message from direct client socket."""
        start_t = time.time()
        while time.time() - start_t < timeout_sec:
            if b"\n" in self._rx_buffer:
                line, self._rx_buffer = self._rx_buffer.split(b"\n", 1)
                text = line.decode('utf-8', errors='ignore').strip()
                if text:
                    try:
                        return json.loads(text)
                    except Exception as e:
                        self.log("WARN", f"Malformed JSON ignored: {text} ({e})")
                        continue

            rem_time = max(0.1, timeout_sec - (time.time() - start_t))
            if self._client_socket:
                self._client_socket.settimeout(rem_time)
                try:
                    chunk = self._client_socket.recv(4096)
                    if not chunk:
                        return None
                    self._rx_buffer += chunk
                except socket.timeout:
                    continue
                except Exception:
                    return None
            else:
                return None
        return None

    def send_request(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """
        Sends request to MT5 EA.
        Uses direct socket if connected directly; otherwise routes via persistent IPC daemon.
        """
        # 1. If direct mode is connected locally on this instance, use direct socket
        if not (self._connected and self._client_socket):
            if self.ipc_port is not None:
                # The daemon waits up to 12s on the EA (forward_request_to_ea). Execution actions must
                # wait longer than that, otherwise the client reports "timed out" while MT5 is still
                # filling the order. Info/heartbeat requests keep a short budget.
                action = str(payload.get("action", "")).upper()
                if action in ("TRADE", "CLOSE", "MODIFY", "CANCEL", "CLOSE_ALL"):
                    ipc_timeout = max(15.0, self.timeout_ms / 1000.0 + 2.0)
                else:
                    ipc_timeout = self.timeout_ms / 1000.0 + 2.0
                ipc_res = self._send_ipc_request(payload, timeout_sec=ipc_timeout)
                if ipc_res is not None:
                    return ipc_res

        # 2. Fall back to direct mode
        with self._lock:
            if not self._connected or not self._client_socket:
                return {"status": "ERROR", "message": "MT5 Bridge not connected (Neither IPC daemon nor direct EA socket available)"}

            if "request_id" not in payload:
                payload["request_id"] = f"REQ_{uuid.uuid4().hex[:12].upper()}"

            req_str = json.dumps(payload) + "\n"
            action = payload.get("action", "UNKNOWN")
            req_id = payload.get("request_id")

            now_t = time.time()
            if not hasattr(self, "_last_ping_log_time"):
                self._last_ping_log_time = 0.0
            if action != "PING" or (now_t - self._last_ping_log_time >= 60.0):
                self.log("REQUEST", f"[{req_id}] Action={action} | Payload: {req_str.strip()}")
                if action == "PING":
                    self._last_ping_log_time = now_t

            send_t0 = time.time()
            try:
                self._client_socket.sendall(req_str.encode('utf-8'))
                timeout_sec = self.timeout_ms / 1000.0

                while time.time() - send_t0 < timeout_sec:
                    resp_obj = self._read_message(timeout_sec - (time.time() - send_t0))
                    if resp_obj is None:
                        break

                    if "client" in resp_obj and ("symbol" in resp_obj or "magic" in resp_obj):
                        self.handshake_data = resp_obj
                        if "broker_utc_offset" in resp_obj:
                            self.broker_utc_offset = int(resp_obj["broker_utc_offset"])
                        self.log("HANDSHAKE", f"EA Client Connected: {resp_obj}")
                        continue

                    if "request_id" in resp_obj and resp_obj["request_id"] != req_id:
                        self.log("WARN", f"Discarding mismatched response request_id: {resp_obj.get('request_id')}")
                        continue

                    latency_ms = (time.time() - send_t0) * 1000.0
                    self._last_heartbeat = time.time()
                    if "broker_utc_offset" in resp_obj:
                        self.broker_utc_offset = int(resp_obj["broker_utc_offset"])
                    elif "data" in resp_obj and isinstance(resp_obj["data"], dict) and "broker_utc_offset" in resp_obj["data"]:
                        self.broker_utc_offset = int(resp_obj["data"]["broker_utc_offset"])
                    if not hasattr(self, "_last_pong_log_time"):
                        self._last_pong_log_time = 0.0
                    if action != "PING" or (now_t - self._last_pong_log_time >= 60.0):
                        self.log("RESPONSE", f"[{req_id}] Latency={latency_ms:.1f}ms | Reply: {json.dumps(resp_obj)}")
                        if action == "PING":
                            self._last_pong_log_time = now_t
                    return resp_obj

                self.log("ERROR", f"[{req_id}] Socket timeout on action {action}")
                return {"status": "ERROR", "message": f"Socket timeout on {action}"}

            except Exception as e:
                self.log("ERROR", f"[{req_id}] Communication failure: {e}")
                self._connected = False
                self._client_socket = None
                return {"status": "ERROR", "message": f"Socket communication error: {str(e)}"}

    def ping(self) -> bool:
        res = self.send_request({"action": "PING", "timestamp": time.time()})
        return res.get("status") == "SUCCESS"

    def get_account(self) -> Optional[AccountInfo]:
        res = self.send_request({"action": "ACCOUNT"})
        if res.get("status") == "SUCCESS" and "data" in res:
            d = res["data"]
            return AccountInfo(
                account_number=int(d.get("login", 0)),
                balance=float(d.get("balance", 0.0)),
                equity=float(d.get("equity", 0.0)),
                margin=float(d.get("margin", 0.0)),
                free_margin=float(d.get("free_margin", 0.0)),
                margin_level=float(d.get("margin_level", 0.0)),
                currency=d.get("currency", "USD"),
                leverage=int(d.get("leverage", 100)),
                trade_mode=str(d.get("trade_mode", "DEMO")),
                profile=str(d.get("profile", "DEMO")),
                trading_enabled=bool(d.get("trading_enabled", False)),
                peak_equity=float(d.get("peak_equity", d.get("equity", 10000.0))),
                initial_balance=float(d.get("initial_balance", d.get("balance", 10000.0))),
                daily_loss_pct=float(d.get("daily_loss_pct", 0.0)),
                drawdown_pct=float(d.get("drawdown_pct", 0.0)),
                remaining_daily_loss=float(d.get("remaining_daily_loss", 0.0)),
                remaining_drawdown=float(d.get("remaining_drawdown", 0.0)),
                profit_target_pct=float(d.get("profit_target_pct", 10.0)),
                trading_days=int(d.get("trading_days", 0)),
                margin_mode_str=str(d.get("margin_mode", "HEDGING")),
                margin_mode=1 if str(d.get("margin_mode", "")).upper() == "NETTING" else 2
            )
        return None

    def enable_trading(self) -> Dict[str, Any]:
        """Sends command to authorize trading entries on LIVE/PROP."""
        return self.send_request({"action": "ENABLE"})

    def disable_trading(self) -> Dict[str, Any]:
        """Sends command to halt new trading entries."""
        return self.send_request({"action": "DISABLE"})

    def get_positions(self) -> List[Position]:
        res = self.send_request({"action": "POSITIONS"})
        positions: List[Position] = []
        if res.get("status") == "SUCCESS" and "data" in res:
            for item in res["data"]:
                positions.append(
                    Position(
                        ticket=int(item["ticket"]),
                        symbol=item["symbol"],
                        order_type=OrderType(item["type"]),
                        volume=float(item["volume"]),
                        open_price=float(item["open_price"]),
                        current_price=float(item.get("current_price", item["open_price"])),
                        stop_loss=float(item.get("sl", 0.0)),
                        take_profit=float(item.get("tp", 0.0)),
                        profit=float(item.get("profit", 0.0)),
                        swap=float(item.get("swap", 0.0)),
                        commission=float(item.get("commission", 0.0)),
                        magic=int(item.get("magic", 100101)),
                        comment=item.get("comment", ""),
                        break_even_activated=bool(item.get("be_activated", False)),
                        partial_taken=bool(item.get("partial_taken", False)),
                    )
                )
        return positions

    def get_rates(self, symbol: str = "BTCUSD.m", timeframe: str = "M15", count: int = 100) -> Dict[str, Any]:
        """Requests historical rates with both broker and UTC timestamps."""
        return self.send_request({
            "action": "RATES",
            "symbol": symbol,
            "timeframe": timeframe,
            "count": count
        })

    def get_daily_summary(self) -> Dict[str, Any]:
        """Requests reconciled daily trading summary from broker HistoryDeals."""
        return self.send_request({"action": "DAILY_SUMMARY"})

    def execute_trade(
        self,
        symbol: str = "BTCUSD.m",
        order_type: OrderType = OrderType.BUY,
        volume: float = 0.01,
        stop_loss: float = 0.0,
        take_profit: float = 0.0,
        price: float = 0.0,
        risk_percent: float = 0.0,
        mode: str = "AUTO",
        magic: int = 100101,
        comment: str = "DEMO_TEST",
        request_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """Submits real trade to MT5 EA with deduplication request_id and adaptive mode."""
        payload = {
            "action": "TRADE",
            "symbol": symbol,
            "type": order_type.value,
            "volume": volume,
            "sl": stop_loss,
            "tp": take_profit,
            "price": price,
            "risk_percent": risk_percent,
            "mode": mode,
            "magic": magic,
            "comment": comment
        }
        if request_id:
            payload["request_id"] = request_id
        return self.send_request(payload)

    def cancel_order(self, ticket: int) -> Dict[str, Any]:
        """Cancels a pending order by ticket."""
        return self.send_request({
            "action": "CANCEL",
            "ticket": ticket
        })

    def kill_switch(self, close_all: bool = False) -> Dict[str, Any]:
        """Engages emergency kill switch in EA, halting all new entries."""
        return self.send_request({
            "action": "KILL_SWITCH",
            "close_all": "true" if close_all else "false"
        })

    def close_order(self, ticket: int, volume: Optional[float] = None) -> Dict[str, Any]:
        """Requests full or partial position closure."""
        payload = {
            "action": "CLOSE",
            "ticket": ticket
        }
        if volume is not None and volume > 0:
            payload["volume"] = volume
        return self.send_request(payload)

    def modify_order(
        self,
        ticket: int,
        sl: Optional[float] = None,
        tp: Optional[float] = None,
        price: Optional[float] = None
    ) -> Dict[str, Any]:
        """Requests position or pending order SL/TP/Price modification."""
        payload: Dict[str, Any] = {
            "action": "MODIFY",
            "ticket": ticket
        }
        if sl is not None:
            payload["sl"] = sl
        if tp is not None:
            payload["tp"] = tp
        if price is not None and price > 0:
            payload["price"] = price
        return self.send_request(payload)
