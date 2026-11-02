"""
Persistent Python-MT5 Communication Bridge Server.
Runs as a long-lived standalone daemon process.
- Port 5555: Listens for incoming native MQL5 connection from PythonBridgeEA.
- Port 5556: Listens for local IPC command clients (test scripts, Telegram bot, orchestrator).
Maintains permanent connection to EA, sends periodic PING heartbeats, and routes requests thread-safely.
"""

from __future__ import annotations
import json
import logging
import socket
import sys
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Dict, Any, Optional
from src.notifications.telegram_config import TelegramConfig
from src.notifications.telegram_service import TelegramNotifier, TelegramCommandHandler, TelegramFeedManager

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s.%(msecs)03d] [BridgeServer] [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger("BridgeServer")


class PersistentBridgeServer:
    def __init__(
        self,
        ea_host: str = "127.0.0.1",
        ea_port: int = 5555,
        ipc_host: str = "127.0.0.1",
        ipc_port: int = 5556,
        heartbeat_interval_sec: float = 5.0
    ):
        self.ea_host = ea_host
        self.ea_port = ea_port
        self.ipc_host = ipc_host
        self.ipc_port = ipc_port
        self.heartbeat_interval_sec = heartbeat_interval_sec

        self._is_running = False
        self._ea_server_socket: Optional[socket.socket] = None
        self._ea_client_socket: Optional[socket.socket] = None
        self._ea_client_addr: Optional[tuple] = None
        self._ea_lock = threading.Lock()

        self._ipc_server_socket: Optional[socket.socket] = None
        self._ipc_lock = threading.Lock()

        self._ea_rx_buffer = b""
        self._last_ea_activity = 0.0
        self._handshake_data: Dict[str, Any] = {}
        self.broker_utc_offset: int = 0

        # Request correlation: req_id -> (threading.Event, response_dict)
        self._pending_requests: Dict[str, tuple[threading.Event, Dict[str, Any]]] = {}
        self._pending_lock = threading.Lock()

        # Integrated Telegram Notifications & Feed
        self.notifier: Optional[TelegramNotifier] = None
        self.feed_manager: Optional[TelegramFeedManager] = None
        self.cmd_handler: Optional[TelegramCommandHandler] = None

    def start(self) -> None:
        """Starts both EA server (5555) and IPC command server (5556)."""
        self._is_running = True

        # 1. Start EA listener on 5555
        self._ea_server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._ea_server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._ea_server_socket.bind((self.ea_host, self.ea_port))
        self._ea_server_socket.listen(5)
        self._ea_server_socket.settimeout(1.0)
        logger.info(f"EA Bridge Listener bound to {self.ea_host}:{self.ea_port} (Awaiting MT5 EA...)")

        # 2. Start IPC listener on 5556
        self._ipc_server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._ipc_server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._ipc_server_socket.bind((self.ipc_host, self.ipc_port))
        self._ipc_server_socket.listen(10)
        self._ipc_server_socket.settimeout(1.0)
        logger.info(f"IPC Command Listener bound to {self.ipc_host}:{self.ipc_port} (Ready for Python clients)")

        # 3. Threads
        threading.Thread(target=self._ea_listener_loop, daemon=True, name="EAListener").start()
        threading.Thread(target=self._ea_heartbeat_loop, daemon=True, name="EAHeartbeat").start()
        threading.Thread(target=self._ipc_listener_loop, daemon=True, name="IPCListener").start()

        # 4. Start Telegram Activity Feed and Command Long-Polling
        try:
            tg_cfg = TelegramConfig()
            if tg_cfg.enabled and tg_cfg.bot_token and tg_cfg.chat_id:
                self.notifier = TelegramNotifier(tg_cfg)
                self.feed_manager = TelegramFeedManager(notifier=self.notifier, feed_interval_sec=10.0)
                self.cmd_handler = TelegramCommandHandler(
                    config=tg_cfg,
                    notifier=self.notifier,
                    status_provider=self._get_status_dict,
                    enable_handler=self._enable_trading_action,
                    disable_handler=self._disable_trading_action,
                    feed_manager=self.feed_manager,
                    reset_handler=self._reset_state_action
                )
                self.cmd_handler.set_symbol_choice_handler = self._set_symbol_choice_action
                logger.info("[BridgeServer] Telegram Activity Feed & Command Handler daemon active.")
        except Exception as e:
            logger.warning(f"[BridgeServer] Failed to initialize Telegram integration: {e}")

    def stop(self) -> None:
        """Stops the bridge server cleanly."""
        self._is_running = False
        with self._ea_lock:
            if self._ea_client_socket:
                try:
                    self._ea_client_socket.close()
                except Exception:
                    pass
                self._ea_client_socket = None
            if self._ea_server_socket:
                try:
                    self._ea_server_socket.close()
                except Exception:
                    pass
                self._ea_server_socket = None

        with self._ipc_lock:
            if self._ipc_server_socket:
                try:
                    self._ipc_server_socket.close()
                except Exception:
                    pass
                self._ipc_server_socket = None
        if self.feed_manager:
            try:
                self.feed_manager.stop()
            except Exception:
                pass
        if self.cmd_handler:
            try:
                self.cmd_handler.stop()
            except Exception:
                pass
        if self.notifier:
            try:
                self.notifier.stop()
            except Exception:
                pass
        logger.info("Bridge server stopped cleanly.")

    def is_ea_connected(self) -> bool:
        with self._ea_lock:
            return self._ea_client_socket is not None

    def _ea_listener_loop(self) -> None:
        """Accepts and handles MT5 connection."""
        while self._is_running:
            try:
                client_sock, addr = self._ea_server_socket.accept()
                client_sock.settimeout(0.5)

                with self._ea_lock:
                    if self._ea_client_socket:
                        try:
                            self._ea_client_socket.close()
                        except Exception:
                            pass
                    self._ea_client_socket = client_sock
                    self._ea_client_addr = addr
                    self._ea_rx_buffer = b""
                    self._last_ea_activity = time.time()

                logger.info(f"[EA_CONNECTED] Accepted active connection from MT5 EA @ {addr[0]}:{addr[1]}")

                # Process incoming data from this connection
                self._handle_ea_client(client_sock)

            except socket.timeout:
                continue
            except Exception as e:
                if self._is_running:
                    logger.error(f"EA listener error: {e}")
                time.sleep(0.5)

    def _handle_ea_client(self, sock: socket.socket) -> None:
        """Reads incoming newline-framed JSON from MT5 EA."""
        while self._is_running:
            try:
                chunk = sock.recv(4096)
                if not chunk:
                    logger.warning("[EA_DISCONNECTED] MT5 EA connection closed by remote peer.")
                    break

                self._last_ea_activity = time.time()
                self._ea_rx_buffer += chunk

                while b"\n" in self._ea_rx_buffer:
                    line, self._ea_rx_buffer = self._ea_rx_buffer.split(b"\n", 1)
                    text = line.decode("utf-8", errors="ignore").strip()
                    if not text:
                        continue
                    try:
                        data = json.loads(text)
                        self._dispatch_ea_message(data)
                    except json.JSONDecodeError:
                        logger.warning(f"Malformed JSON from EA: {text}")

            except socket.timeout:
                continue
            except Exception as e:
                logger.warning(f"[EA_IO_ERROR] Error reading from EA: {e}")
                break

        with self._ea_lock:
            if self._ea_client_socket == sock:
                try:
                    sock.close()
                except Exception:
                    pass
                self._ea_client_socket = None
                logger.info("[EA_CLEANUP] EA socket cleared. Ready for EA reconnect.")

    def _dispatch_ea_message(self, data: Dict[str, Any]) -> None:
        """Routes message from EA to waiting request or records handshake."""
        # Intercept Activity Feed Events from MT5 EA
        if data.get("action") == "FEED_EVENT" or data.get("type") == "FEED_EVENT":
            lvl = data.get("level", "INFO")
            line = data.get("line", "")
            sym = data.get("symbol", "")
            tf = data.get("tf", "")

            # Windows PC audible notification on SIGNAL
            if lvl.upper() == "SIGNAL" and sys.platform.startswith("win"):
                try:
                    import winsound
                    winsound.Beep(1000, 250)
                except Exception:
                    pass

            if self.feed_manager:
                self.feed_manager.log_event(lvl, line, sym, tf)
            return

        # Check if greeting handshake
        if "client" in data and ("account" in data or "magic" in data):
            self._handshake_data = data
            self.broker_utc_offset = int(data.get("broker_utc_offset", 0))
            logger.info(f"[HANDSHAKE] EA handshake received: {data} (Broker UTC offset: {self.broker_utc_offset}s)")
            return

        req_id = data.get("request_id")
        if "broker_utc_offset" in data:
            self.broker_utc_offset = int(data["broker_utc_offset"])
        elif "data" in data and isinstance(data["data"], dict) and "broker_utc_offset" in data["data"]:
            self.broker_utc_offset = int(data["data"]["broker_utc_offset"])

        if req_id and not req_id.startswith("HB_"):
            with self._pending_lock:
                if req_id in self._pending_requests:
                    event, storage = self._pending_requests[req_id]
                    storage.clear()
                    storage.update(data)
                    event.set()
                    return

        # If it's a heartbeat PONG or EA-generated heartbeat response, do not route to client requests!
        if data.get("message") == "PONG" or (req_id and str(req_id).startswith("HB_")):
            now_t = time.time()
            if not hasattr(self, "_last_pong_log_time"):
                self._last_pong_log_time = 0.0
            if now_t - self._last_pong_log_time >= 60.0:
                logger.info(f"[HEARTBEAT] PONG from EA: server_time={data.get('server_time')}, utc_time={data.get('utc_time')} (throttled 1/min)")
                self._last_pong_log_time = now_t
            else:
                logger.debug(f"[HEARTBEAT] PONG from EA: server_time={data.get('server_time')}, utc_time={data.get('utc_time')}")
            return

        # Fallback: if req_id was omitted in EA response, match oldest pending client request
        with self._pending_lock:
            if self._pending_requests:
                first_req_id = next(iter(self._pending_requests))
                event, storage = self._pending_requests[first_req_id]
                storage.clear()
                storage.update(data)
                event.set()
                return

        logger.info(f"[UNHANDLED_EA_MSG] {data}")

    def _ea_heartbeat_loop(self) -> None:
        """Sends periodic PING to EA to keep the socket alive."""
        while self._is_running:
            time.sleep(self.heartbeat_interval_sec)
            if self.is_ea_connected():
                try:
                    hb_id = f"HB_{int(time.time()*1000)}"
                    ping_payload = {
                        "action": "PING",
                        "request_id": hb_id,
                        "timestamp": time.time()
                    }
                    self._send_to_ea_raw(json.dumps(ping_payload) + "\n")
                    now_t = time.time()
                    if not hasattr(self, "_last_ping_log_time"):
                        self._last_ping_log_time = 0.0
                    if now_t - self._last_ping_log_time >= 60.0:
                        logger.info(f"[HEARTBEAT] PING sent to EA: req_id={hb_id} (throttled 1/min)")
                        self._last_ping_log_time = now_t
                    else:
                        logger.debug(f"[HEARTBEAT] PING sent to EA: req_id={hb_id}")
                except Exception as e:
                    logger.error(f"[HEARTBEAT_FAILED] Missed heartbeat / PING send failed immediately: {e}")

    def _send_to_ea_raw(self, msg: str) -> bool:
        with self._ea_lock:
            if not self._ea_client_socket:
                return False
            try:
                self._ea_client_socket.sendall(msg.encode("utf-8"))
                return True
            except Exception as e:
                logger.warning(f"Failed to send to EA: {e}")
                try:
                    self._ea_client_socket.close()
                except Exception:
                    pass
                self._ea_client_socket = None
                return False

    def forward_request_to_ea(self, payload: Dict[str, Any], timeout_sec: float = 12.0) -> Dict[str, Any]:
        """
        Sends a request payload to MT5 EA and awaits correlated response.
        Thread-safe for multiple concurrent IPC clients.
        """
        if not self.is_ea_connected():
            return {
                "status": "ERROR",
                "message": "MT5 EA not connected to Bridge on port 5555"
            }

        req_id = payload.get("request_id") or f"REQ_{uuid.uuid4().hex[:12].upper()}"
        payload["request_id"] = req_id

        event = threading.Event()
        resp_storage: Dict[str, Any] = {}

        with self._pending_lock:
            self._pending_requests[req_id] = (event, resp_storage)

        try:
            req_str = json.dumps(payload) + "\n"
            t0 = time.time()
            if not self._send_to_ea_raw(req_str):
                return {"status": "ERROR", "message": "Failed to transmit request to MT5 EA socket"}

            signaled = event.wait(timeout_sec)
            latency_ms = (time.time() - t0) * 1000.0

            if signaled:
                logger.info(f"[{req_id}] Action={payload.get('action')} | Latency={latency_ms:.1f}ms | Status={resp_storage.get('status')}")
                return resp_storage
            else:
                logger.warning(f"[{req_id}] Action={payload.get('action')} timed out after {timeout_sec}s")
                return {
                    "status": "ERROR",
                    "request_id": req_id,
                    "message": f"Bridge request timed out after {timeout_sec}s awaiting MT5 EA response"
                }
        finally:
            with self._pending_lock:
                self._pending_requests.pop(req_id, None)

    def _ipc_listener_loop(self) -> None:
        """Accepts local IPC command connections on 5556."""
        while self._is_running:
            try:
                client_sock, addr = self._ipc_server_socket.accept()
                threading.Thread(
                    target=self._handle_ipc_client,
                    args=(client_sock,),
                    daemon=True
                ).start()
            except socket.timeout:
                continue
            except Exception as e:
                if self._is_running:
                    logger.error(f"IPC listener error: {e}")
                time.sleep(0.5)

    def _handle_ipc_client(self, sock: socket.socket) -> None:
        """Handles a single IPC client command request."""
        sock.settimeout(15.0)
        try:
            rx = b""
            while self._is_running:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                rx += chunk
                if b"\n" in rx:
                    line, _ = rx.split(b"\n", 1)
                    text = line.decode("utf-8", errors="ignore").strip()
                    if text:
                        try:
                            req = json.loads(text)
                            if req.get("action") == "FEED_LOG":
                                lvl = req.get("level", "INFO")
                                line = req.get("line", "")
                                sym = req.get("symbol", "")
                                tf = req.get("tf", "")
                                if self.feed_manager:
                                    self.feed_manager.log_event(lvl, line, sym, tf)
                                resp = {"status": "SUCCESS", "message": "Log processed"}
                            elif req.get("action") == "FEED_GET_LAST":
                                count = int(req.get("count", 20))
                                lines = self.feed_manager.get_last_lines(count) if self.feed_manager else []
                                resp = {"status": "SUCCESS", "lines": lines}
                            elif req.get("action") == "SET_SYMBOL_CHOICE":
                                sym = req.get("symbol", "")
                                choice = req.get("choice", "").upper()
                                if self.feed_manager:
                                    if choice == "REDUCE":
                                        self.feed_manager.allow_reduced(sym)
                                    elif choice == "BLOCK":
                                        self.feed_manager.block_symbol(sym)
                                    elif choice == "RESET":
                                        self.feed_manager.reset_symbol(sym)
                                if self.is_ea_connected():
                                    resp = self.forward_request_to_ea(req)
                                else:
                                    resp = {"status": "SUCCESS", "message": f"Choice {choice} recorded"}
                            elif req.get("action") in ("SIGNAL", "TRIGGER_SIGNAL"):
                                # Trigger local sound and feed alert
                                if sys.platform.startswith("win"):
                                    try:
                                        import winsound
                                        winsound.Beep(1000, 250)
                                    except Exception:
                                        pass
                                line = req.get("line", f"[SIGNAL] Test Signal triggered on {req.get('symbol', 'BTCUSD.m')}")
                                if self.feed_manager:
                                    self.feed_manager.log_event("SIGNAL", line, req.get("symbol", ""), req.get("tf", "M15"))
                                ea_req = dict(req)
                                ea_req["action"] = "SIGNAL"
                                resp = self.forward_request_to_ea(ea_req) if self.is_ea_connected() else {"status": "SUCCESS", "message": "Local signal triggered"}
                            else:
                                resp = self.forward_request_to_ea(req)
                        except json.JSONDecodeError as err:
                            resp = {"status": "ERROR", "message": f"Malformed JSON request: {err}"}
                        except Exception as e:
                            resp = {"status": "ERROR", "message": str(e)}

                        resp_str = json.dumps(resp) + "\n"
                        sock.sendall(resp_str.encode("utf-8"))
                        break
        except Exception as e:
            logger.debug(f"IPC client handler exception: {e}")
        finally:
            try:
                sock.close()
            except Exception:
                pass



    def _set_symbol_choice_action(self, sym: str, choice: str) -> bool:
        if not self.is_ea_connected():
            return False
        try:
            res = self.forward_request_to_ea({"action": "SET_SYMBOL_CHOICE", "symbol": sym, "choice": choice}, timeout_sec=5.0)
            return res.get("status") == "SUCCESS"
        except Exception:
            return False

    def _get_status_dict(self) -> Dict[str, Any]:
        """Provides current system and account status for Telegram /status command."""
        st: Dict[str, Any] = {
            "bridge_connected": self.is_ea_connected(),
            "state": "RUNNING" if self.is_ea_connected() else "STANDBY",
            "suppressed_alerts_count": self.feed_manager.suppressed_alerts_count if self.feed_manager else 0,
            "symbol_history": self.feed_manager.symbol_history if self.feed_manager else {},
            "broker_utc_offset": self.broker_utc_offset
        }
        if self.is_ea_connected():
            try:
                # Query STATUS for rich per-symbol specifications & open risk metrics
                st_resp = self.forward_request_to_ea({"action": "STATUS"}, timeout_sec=3.0)
                if st_resp.get("status") == "SUCCESS":
                    d = st_resp.get("data", {})
                    st.update(d)
                # Query ACCOUNT for account parameters & challenge metrics
                acc_resp = self.forward_request_to_ea({"action": "ACCOUNT"}, timeout_sec=3.0)
                if acc_resp.get("status") == "SUCCESS":
                    d = acc_resp.get("data", {})
                    st.update(d)
            except Exception as e:
                logger.debug(f"Status query failed in callback: {e}")
        return st

    def _enable_trading_action(self) -> bool:
        if not self.is_ea_connected():
            return False
        try:
            res = self.forward_request_to_ea({"action": "ENABLE"}, timeout_sec=5.0)
            return res.get("status") == "SUCCESS"
        except Exception:
            return False

    def _disable_trading_action(self) -> bool:
        if not self.is_ea_connected():
            return False
        try:
            res = self.forward_request_to_ea({"action": "DISABLE"}, timeout_sec=5.0)
            return res.get("status") == "SUCCESS"
        except Exception:
            return False

    def _reset_state_action(self) -> tuple[bool, str]:
        if not self.is_ea_connected():
            return True, "✅ <b>LOCAL RESET COMPLETE</b>: Daily counters and suppression tracking cleared (EA offline)."
        try:
            res = self.forward_request_to_ea({"action": "RESET_STATE"}, timeout_sec=5.0)
            if res.get("status") == "SUCCESS":
                baseline = float(res.get("baseline", 0.0))
                return True, f"✅ <b>STATE RESET COMPLETE</b>: Baseline balance, day-start balance, and peak equity reset to ${baseline:.2f}. Challenge progress: 0.0%. Daily counters cleared."
            else:
                err = res.get("message", "Reset refused")
                return False, f"❌ <b>RESET REFUSED</b>: {err}"
        except Exception as e:
            return False, f"⚠️ Error executing reset: {e}"


if __name__ == "__main__":
    server = PersistentBridgeServer()
    server.start()
    logger.info("Persistent Bridge Server running. Press Ctrl+C to stop.")
    try:
        while True:
            time.sleep(1.0)
    except KeyboardInterrupt:
        logger.info("Stopping Bridge Server...")
        server.stop()
