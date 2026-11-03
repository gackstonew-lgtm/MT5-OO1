"""
Persistent Python-MT5 Communication Bridge Server.
Runs as a long-lived standalone daemon process / Windows service.
- Port 5555: Listens for incoming native MQL5 connections from PythonBridgeEA (multi-connection, 1 per symbol).
- Port 5556: Listens for local IPC command clients (test scripts, Telegram bot, orchestrator).
Maintains concurrent connections to multiple EA instances, sends periodic PING heartbeats,
marks stale sockets, isolates slow peers, broadcasts administrative commands, and routes requests.
"""

from __future__ import annotations
import json
import logging
import socket
import sys
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, Any, Optional, List, Tuple
from concurrent.futures import ThreadPoolExecutor

from src.notifications.telegram_config import TelegramConfig
from src.notifications.telegram_service import (
    TelegramNotifier,
    TelegramCommandHandler,
    TelegramFeedManager
)

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s.%(msecs)03d] [BridgeServer] [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger("BridgeServer")


@dataclass
class EAConnection:
    socket: socket.socket
    addr: Tuple[str, int]
    magic: int
    symbol: str
    account: int
    version: str = "3.10"
    broker_utc_offset: int = 0
    connected_at: float = field(default_factory=time.time)
    last_activity: float = field(default_factory=time.time)
    last_reply_time: float = field(default_factory=time.time)
    latency_ms: float = 0.0
    pings_count: int = 0
    errors_count: int = 0
    requests_count: int = 0
    status: str = "ACTIVE"  # ACTIVE, STALE, DISCONNECTED
    send_lock: threading.Lock = field(default_factory=threading.Lock)

    def is_stale(self, timeout_sec: float = 30.0) -> bool:
        return (time.time() - self.last_activity) > timeout_sec

    def to_dict(self) -> Dict[str, Any]:
        return {
            "magic": self.magic,
            "symbol": self.symbol,
            "account": self.account,
            "version": self.version,
            "broker_utc_offset": self.broker_utc_offset,
            "connected_at": self.connected_at,
            "last_activity": self.last_activity,
            "last_reply_sec_ago": round(time.time() - self.last_reply_time, 1),
            "latency_ms": round(self.latency_ms, 1),
            "status": self.status,
            "pings_count": self.pings_count,
            "requests_count": self.requests_count,
            "errors_count": self.errors_count,
        }

    def send_raw(self, msg: str, timeout_sec: float = 3.0) -> bool:
        """Sends data through socket thread-safely with isolation and timeout."""
        with self.send_lock:
            try:
                self.socket.settimeout(timeout_sec)
                self.socket.sendall(msg.encode("utf-8"))
                return True
            except Exception as e:
                self.errors_count += 1
                logger.warning(f"[{self.symbol} | Magic {self.magic}] Send failed: {e}")
                self.status = "DISCONNECTED"
                try:
                    self.socket.close()
                except Exception:
                    pass
                return False


class PersistentBridgeServer:
    def __init__(
        self,
        ea_host: str = "127.0.0.1",
        ea_port: int = 5555,
        ipc_host: str = "127.0.0.1",
        ipc_port: int = 5556,
        heartbeat_interval_sec: float = 5.0,
        stale_timeout_sec: float = 30.0
    ):
        self.ea_host = ea_host
        self.ea_port = ea_port
        self.ipc_host = ipc_host
        self.ipc_port = ipc_port
        self.heartbeat_interval_sec = heartbeat_interval_sec
        self.stale_timeout_sec = stale_timeout_sec

        self._is_running = False
        self._ea_server_socket: Optional[socket.socket] = None
        self._ipc_server_socket: Optional[socket.socket] = None

        # Multi-connection registry keyed by magic number
        self._clients: Dict[int, EAConnection] = {}
        self._symbol_to_magic: Dict[str, int] = {}
        self._clients_lock = threading.Lock()

        # Request correlation: req_id -> (threading.Event, response_dict)
        self._pending_requests: Dict[str, Tuple[threading.Event, Dict[str, Any]]] = {}
        self._pending_lock = threading.Lock()

        # Heartbeat tracking for PING round-trip latency
        self._pending_pings: Dict[str, Tuple[int, float]] = {}  # req_id -> (magic, send_time)
        self._last_summary_log_time = 0.0

        self.broker_utc_offset: int = 0
        self.is_trading_halted: bool = False

        # Integrated Telegram Notifications & Feed
        self.notifier: Optional[TelegramNotifier] = None
        self.feed_manager: Optional[TelegramFeedManager] = None
        self.cmd_handler: Optional[TelegramCommandHandler] = None

    def start(self) -> None:
        """Starts EA server (5555) and IPC command server (5556)."""
        self._is_running = True

        # 1. Start EA listener on 5555
        self._ea_server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._ea_server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._ea_server_socket.bind((self.ea_host, self.ea_port))
        self._ea_server_socket.listen(16)
        self._ea_server_socket.settimeout(1.0)
        logger.info(f"EA Multi-Connection Listener bound to {self.ea_host}:{self.ea_port} (Awaiting MT5 EAs...)")

        # 2. Start IPC listener on 5556
        self._ipc_server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._ipc_server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._ipc_server_socket.bind((self.ipc_host, self.ipc_port))
        self._ipc_server_socket.listen(16)
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
                    reset_handler=self._reset_state_action,
                    pause_handler=self._pause_action,
                    resume_handler=self._resume_action,
                    closeall_handler=self._closeall_action,
                    killswitch_handler=self._killswitch_action
                )
                self.cmd_handler.set_symbol_choice_handler = self._set_symbol_choice_action
                logger.info("[BridgeServer] Telegram Activity Feed & Command Handler active.")
        except Exception as e:
            logger.warning(f"[BridgeServer] Failed to initialize Telegram integration: {e}")

    def stop(self) -> None:
        """Stops the bridge server cleanly."""
        self._is_running = False

        with self._clients_lock:
            for magic, client in list(self._clients.items()):
                try:
                    client.socket.close()
                except Exception:
                    pass
            self._clients.clear()
            self._symbol_to_magic.clear()

        if self._ea_server_socket:
            try:
                self._ea_server_socket.close()
            except Exception:
                pass
            self._ea_server_socket = None

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
        """Returns True if at least one EA connection is actively connected."""
        with self._clients_lock:
            return any(c.status == "ACTIVE" for c in self._clients.values())

    def get_connected_clients(self) -> List[EAConnection]:
        with self._clients_lock:
            return list(self._clients.values())

    def get_connection_by_symbol(self, symbol: str) -> Optional[EAConnection]:
        with self._clients_lock:
            magic = self._symbol_to_magic.get(symbol)
            if magic is not None:
                return self._clients.get(magic)
            # Fuzzy match without trailing suffix if needed
            for s, m in self._symbol_to_magic.items():
                if s.startswith(symbol) or symbol.startswith(s):
                    return self._clients.get(m)
            return None

    def get_connection_by_magic(self, magic: int) -> Optional[EAConnection]:
        with self._clients_lock:
            return self._clients.get(magic)

    def _ea_listener_loop(self) -> None:
        """Accepts incoming connections from MT5 EAs concurrently."""
        while self._is_running:
            try:
                client_sock, addr = self._ea_server_socket.accept()
                client_sock.settimeout(2.0)
                threading.Thread(
                    target=self._handle_ea_client_connection,
                    args=(client_sock, addr),
                    daemon=True,
                    name=f"EAClient-{addr[1]}"
                ).start()
            except socket.timeout:
                continue
            except Exception as e:
                if self._is_running:
                    logger.error(f"EA listener error: {e}")
                time.sleep(0.5)

    def _handle_ea_client_connection(self, sock: socket.socket, addr: Tuple[str, int]) -> None:
        """Performs initial handshake, registers client or rejects duplicate, and handles message loop."""
        rx_buffer = b""
        client_conn: Optional[EAConnection] = None
        disconnect_reason = "Unknown"

        try:
            # 1. Await initial handshake within 5.0 seconds
            start_wait = time.time()
            handshake_data: Optional[Dict[str, Any]] = None

            while time.time() - start_wait < 5.0:
                try:
                    chunk = sock.recv(4096)
                    if not chunk:
                        break
                    rx_buffer += chunk
                    if b"\n" in rx_buffer:
                        line, rx_buffer = rx_buffer.split(b"\n", 1)
                        text = line.decode("utf-8", errors="ignore").strip()
                        if text:
                            try:
                                handshake_data = json.loads(text)
                                break
                            except json.JSONDecodeError:
                                pass
                except socket.timeout:
                    continue

            if not handshake_data:
                logger.warning(f"[EA_HANDSHAKE_TIMEOUT] Client @ {addr[0]}:{addr[1]} failed to send handshake in 5s.")
                disconnect_reason = "Handshake timeout"
                return

            # Extract client metadata
            symbol = str(handshake_data.get("symbol", "UNKNOWN"))
            account = int(handshake_data.get("account", 0))
            magic = int(handshake_data.get("magic", 100101))
            version = str(handshake_data.get("version", "3.10"))
            broker_utc_offset = int(handshake_data.get("broker_utc_offset", 0))
            if broker_utc_offset != 0:
                self.broker_utc_offset = broker_utc_offset

            # 2. Check registry for magic number
            with self._clients_lock:
                if magic in self._clients:
                    existing = self._clients[magic]
                    if existing.is_stale(self.stale_timeout_sec) or existing.status != "ACTIVE":
                        logger.warning(
                            f"[EA_STALE_REPLACED] Replacing stale connection for Magic {magic} (symbol {existing.symbol}, "
                            f"inactive {time.time() - existing.last_activity:.1f}s) with new connection from {addr[0]}:{addr[1]}"
                        )
                        existing.status = "STALE"
                        try:
                            existing.socket.close()
                        except Exception:
                            pass
                    else:
                        # Existing is healthy and active: reject duplicate!
                        logger.warning(
                            f"[EA_DUPLICATE_REJECTED] Rejected duplicate connection for Magic {magic} ({symbol}) "
                            f"from {addr[0]}:{addr[1]} (active connection exists from {existing.addr})"
                        )
                        rejection = {
                            "status": "ERROR",
                            "action": "REJECTED",
                            "reason": f"Duplicate instance detected for Magic {magic} on {symbol}. Active instance exists.",
                            "magic": magic,
                            "symbol": symbol
                        }
                        try:
                            sock.sendall((json.dumps(rejection) + "\n").encode("utf-8"))
                        except Exception:
                            pass
                        disconnect_reason = f"Duplicate instance rejected for Magic {magic}"
                        return

                # Register connection
                client_conn = EAConnection(
                    socket=sock,
                    addr=addr,
                    magic=magic,
                    symbol=symbol,
                    account=account,
                    version=version,
                    broker_utc_offset=broker_utc_offset,
                    connected_at=time.time(),
                    last_activity=time.time(),
                    last_reply_time=time.time(),
                    status="ACTIVE"
                )
                self._clients[magic] = client_conn
                self._symbol_to_magic[symbol] = magic

            logger.info(
                f"[EA_CONNECT] Connected | Time={datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')} | "
                f"Symbol={symbol} | Magic={magic} | Account={account} | PeerPort={addr[1]} | Version={version}"
            )

            # Send HELLO acknowledgement
            ack = {
                "status": "SUCCESS",
                "action": "HELLO_ACK",
                "symbol": symbol,
                "magic": magic,
                "server_time": int(time.time()),
                "broker_utc_offset": self.broker_utc_offset
            }
            client_conn.send_raw(json.dumps(ack) + "\n")

            # 3. Main receive loop for this client
            sock.settimeout(1.0)
            while self._is_running and client_conn.status == "ACTIVE":
                try:
                    chunk = sock.recv(4096)
                    if not chunk:
                        disconnect_reason = "Remote peer closed socket"
                        break

                    client_conn.last_activity = time.time()
                    rx_buffer += chunk

                    while b"\n" in rx_buffer:
                        line, rx_buffer = rx_buffer.split(b"\n", 1)
                        text = line.decode("utf-8", errors="ignore").strip()
                        if not text:
                            continue
                        try:
                            msg = json.loads(text)
                            self._dispatch_ea_message(client_conn, msg)
                        except json.JSONDecodeError:
                            logger.warning(f"[{symbol} | Magic {magic}] Malformed JSON from EA: {text}")

                except socket.timeout:
                    # Check stale state
                    if client_conn.is_stale(self.stale_timeout_sec):
                        client_conn.status = "STALE"
                        disconnect_reason = f"Heartbeat inactivity timeout (> {self.stale_timeout_sec}s)"
                        logger.warning(f"[{symbol} | Magic {magic}] Marked connection STALE (no activity).")
                        break
                    continue
                except Exception as e:
                    disconnect_reason = f"IO Error: {e}"
                    break

        except Exception as e:
            disconnect_reason = f"Fatal handler error: {e}"
        finally:
            # Clean up connection
            if client_conn:
                duration = time.time() - client_conn.connected_at
                client_conn.status = "DISCONNECTED"
                with self._clients_lock:
                    if self._clients.get(client_conn.magic) is client_conn:
                        del self._clients[client_conn.magic]
                        self._symbol_to_magic.pop(client_conn.symbol, None)

                logger.info(
                    f"[EA_DISCONNECT] Disconnected | Time={datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')} | "
                    f"Symbol={client_conn.symbol} | Magic={client_conn.magic} | Account={client_conn.account} | "
                    f"PeerPort={client_conn.addr[1]} | Version={client_conn.version} | Duration={duration:.1f}s | Reason={disconnect_reason}"
                )
            try:
                sock.close()
            except Exception:
                pass

    def _dispatch_ea_message(self, client: EAConnection, data: Dict[str, Any]) -> None:
        """Processes message from a specific EA connection."""
        action = data.get("action")
        req_id = data.get("request_id")

        # 1. Activity Feed Events
        if action == "FEED_EVENT" or data.get("type") == "FEED_EVENT":
            lvl = data.get("level", "INFO")
            line = data.get("line", "")
            sym = data.get("symbol", client.symbol)
            tf = data.get("tf", "")

            if lvl.upper() == "SIGNAL" and sys.platform.startswith("win"):
                try:
                    import winsound
                    winsound.Beep(1000, 250)
                except Exception:
                    pass

            if self.feed_manager:
                self.feed_manager.log_event(lvl, line, sym, tf)
            return

        # 2. PONG Heartbeat Response
        if data.get("message") == "PONG" or (req_id and str(req_id).startswith("HB_")):
            now = time.time()
            client.last_reply_time = now
            client.last_activity = now
            with self._pending_lock:
                ping_info = self._pending_pings.pop(str(req_id), None)
            if ping_info:
                _, send_time = ping_info
                client.latency_ms = (now - send_time) * 1000.0

            logger.debug(
                f"[HEARTBEAT] PONG from {client.symbol} (Magic {client.magic}): "
                f"latency={client.latency_ms:.1f}ms, server_time={data.get('server_time')}"
            )
            return

        # 3. Correlated Request Response
        if req_id and req_id in self._pending_requests:
            with self._pending_lock:
                if req_id in self._pending_requests:
                    event, storage = self._pending_requests[req_id]
                    storage.clear()
                    storage.update(data)
                    event.set()
                    return

        # 4. Fallback to oldest pending request
        with self._pending_lock:
            if self._pending_requests:
                first_req_id = next(iter(self._pending_requests))
                event, storage = self._pending_requests[first_req_id]
                storage.clear()
                storage.update(data)
                event.set()
                return

        logger.debug(f"[{client.symbol}] Unhandled message from EA: {data}")

    def _ea_heartbeat_loop(self) -> None:
        """Sends periodic PING to all connected EAs and logs throttled 1/min summary."""
        while self._is_running:
            time.sleep(self.heartbeat_interval_sec)
            now = time.time()

            active_clients = self.get_connected_clients()
            for client in active_clients:
                if client.status != "ACTIVE":
                    continue
                hb_id = f"HB_{int(now * 1000)}_{client.magic}"
                with self._pending_lock:
                    self._pending_pings[hb_id] = (client.magic, now)

                ping_payload = {
                    "action": "PING",
                    "request_id": hb_id,
                    "timestamp": now,
                    "magic": client.magic,
                    "symbol": client.symbol
                }
                client.pings_count += 1
                client.send_raw(json.dumps(ping_payload) + "\n", timeout_sec=2.0)
                logger.debug(f"[HEARTBEAT] PING sent to {client.symbol} (Magic {client.magic}, req={hb_id})")

            # Throttled 1-minute summary log
            if now - self._last_summary_log_time >= 60.0:
                self._last_summary_log_time = now
                summary_parts = []
                for c in active_clients:
                    ago = now - c.last_reply_time
                    summary_parts.append(
                        f"[{c.symbol} (M:{c.magic}): State={c.status}, RTT={c.latency_ms:.1f}ms, "
                        f"Reqs={c.requests_count + c.pings_count}, Errs={c.errors_count}, LastReply={ago:.1f}s ago]"
                    )
                summary_str = " | ".join(summary_parts) if summary_parts else "No active EA connections"
                logger.info(f"[HEARTBEAT_SUMMARY] Connected EAs: {len(active_clients)} | {summary_str}")

    def forward_request_to_ea(self, payload: Dict[str, Any], timeout_sec: float = 10.0) -> Dict[str, Any]:
        """
        Routes request to target EA connection based on symbol/magic, or broadcasts administrative commands.
        Thread-safe and isolated.
        """
        # Administrative Broadcast Commands
        action = payload.get("action", "").upper()
        broadcast_actions = {"KILL_SWITCH", "PAUSE", "RESUME", "CLOSE_ALL", "DISABLE", "ENABLE", "RESET_STATE"}

        if action in broadcast_actions and not payload.get("symbol") and not payload.get("magic"):
            if action in ("KILL_SWITCH", "PAUSE", "DISABLE"):
                self.is_trading_halted = True
            elif action in ("RESUME", "ENABLE"):
                self.is_trading_halted = False
            return self._broadcast_to_all_eas(payload, timeout_sec)

        # Target specific connection
        target_client: Optional[EAConnection] = None
        target_magic = payload.get("magic")
        target_symbol = payload.get("symbol")

        if target_magic is not None:
            target_client = self.get_connection_by_magic(int(target_magic))
        elif target_symbol:
            target_client = self.get_connection_by_symbol(str(target_symbol))
            if not target_client:
                clients = self.get_connected_clients()
                active = [c for c in clients if c.status == "ACTIVE"]
                if active:
                    target_client = active[0]
        else:
            # Fall back to any active connection
            clients = self.get_connected_clients()
            active = [c for c in clients if c.status == "ACTIVE"]
            if active:
                target_client = active[0]

        if not target_client or target_client.status != "ACTIVE":
            return {
                "status": "ERROR",
                "message": f"MT5 EA not connected for symbol={target_symbol} / magic={target_magic}"
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
            target_client.requests_count += 1

            if not target_client.send_raw(req_str, timeout_sec=timeout_sec):
                return {"status": "ERROR", "message": f"Failed to transmit request to EA for {target_client.symbol}"}

            signaled = event.wait(timeout_sec)
            latency_ms = (time.time() - t0) * 1000.0

            if signaled:
                logger.info(f"[{req_id}] Action={payload.get('action')} | Symbol={target_client.symbol} | Latency={latency_ms:.1f}ms | Status={resp_storage.get('status')}")
                return resp_storage
            else:
                target_client.errors_count += 1
                logger.warning(f"[{req_id}] Action={payload.get('action')} on {target_client.symbol} timed out after {timeout_sec}s")
                return {
                    "status": "ERROR",
                    "request_id": req_id,
                    "message": f"Bridge request timed out after {timeout_sec}s awaiting MT5 EA response ({target_client.symbol})"
                }
        finally:
            with self._pending_lock:
                self._pending_requests.pop(req_id, None)

    def _broadcast_to_all_eas(self, payload: Dict[str, Any], timeout_sec: float = 6.0) -> Dict[str, Any]:
        """Delivers command concurrently to all connected EAs and aggregates acknowledgements."""
        clients = [c for c in self.get_connected_clients() if c.status == "ACTIVE"]
        if not clients:
            return {
                "status": "SUCCESS",
                "message": "Command recorded (No active EA connections)",
                "results": {}
            }

        results: Dict[str, Any] = {}

        def _send_one(client: EAConnection):
            sub_payload = dict(payload)
            sub_req_id = f"BCAST_{uuid.uuid4().hex[:8].upper()}_{client.magic}"
            sub_payload["request_id"] = sub_req_id

            event = threading.Event()
            storage: Dict[str, Any] = {}
            with self._pending_lock:
                self._pending_requests[sub_req_id] = (event, storage)

            try:
                if client.send_raw(json.dumps(sub_payload) + "\n", timeout_sec=timeout_sec):
                    if event.wait(timeout_sec):
                        return client.symbol, storage
                return client.symbol, {"status": "ERROR", "message": "Timeout or send failure"}
            finally:
                with self._pending_lock:
                    self._pending_requests.pop(sub_req_id, None)

        with ThreadPoolExecutor(max_workers=max(len(clients), 1)) as executor:
            futures = [executor.submit(_send_one, c) for c in clients]
            for fut in futures:
                try:
                    sym, res = fut.result(timeout=timeout_sec + 1.0)
                    results[sym] = res
                except Exception as e:
                    pass

        return {
            "status": "SUCCESS",
            "action": payload.get("action"),
            "broadcast_count": len(results),
            "results": results
        }

    def _ipc_listener_loop(self) -> None:
        """Accepts local IPC command connections on 5556."""
        while self._is_running:
            try:
                client_sock, addr = self._ipc_server_socket.accept()
                threading.Thread(
                    target=self._handle_ipc_client,
                    args=(client_sock,),
                    daemon=True,
                    name="IPCClient"
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
                            action = req.get("action")
                            if action == "FEED_LOG":
                                lvl = req.get("level", "INFO")
                                line = req.get("line", "")
                                sym = req.get("symbol", "")
                                tf = req.get("tf", "")
                                if self.feed_manager:
                                    self.feed_manager.log_event(lvl, line, sym, tf)
                                resp = {"status": "SUCCESS", "message": "Log processed"}
                            elif action == "FEED_GET_LAST":
                                count = int(req.get("count", 20))
                                lines = self.feed_manager.get_last_lines(count) if self.feed_manager else []
                                resp = {"status": "SUCCESS", "lines": lines}
                            elif action == "STATUS":
                                with self._clients_lock:
                                    conns_data = [c.to_dict() for c in self._clients.values()]
                                    resp = {
                                        "status": "SUCCESS",
                                        "active_connections_count": len(self._clients),
                                        "connections": conns_data,
                                        "trading_halted": self.is_trading_halted,
                                        "bridge_mode": "MULTI_CONNECTION"
                                    }
                            elif action == "SET_SYMBOL_CHOICE":
                                sym = req.get("symbol", "")
                                choice = req.get("choice", "").upper()
                                if self.feed_manager:
                                    if choice == "REDUCE":
                                        self.feed_manager.allow_reduced(sym)
                                    elif choice == "BLOCK":
                                        self.feed_manager.block_symbol(sym)
                                    elif choice == "RESET":
                                        self.feed_manager.reset_symbol(sym)
                                resp = self.forward_request_to_ea(req) if self.is_ea_connected() else {"status": "SUCCESS", "message": f"Choice {choice} recorded"}
                            elif action in ("SIGNAL", "TRIGGER_SIGNAL"):
                                if sys.platform.startswith("win"):
                                    try:
                                        import winsound
                                        winsound.Beep(1000, 250)
                                    except Exception:
                                        pass
                                line = req.get("line", f"[SIGNAL] Test Signal triggered on {req.get('symbol', 'XAUUSD.m')}")
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
        """Provides current system, connection, and account status for Telegram /status command."""
        clients = self.get_connected_clients()
        active_clients = [c for c in clients if c.status == "ACTIVE"]

        now = time.time()
        conn_details = []
        for c in clients:
            conn_details.append({
                "symbol": c.symbol,
                "magic": c.magic,
                "account": c.account,
                "state": c.status,
                "latency_ms": round(c.latency_ms, 1),
                "last_reply_sec_ago": round(now - c.last_reply_time, 1),
                "requests": c.requests_count + c.pings_count,
                "errors": c.errors_count
            })

        min_reply_age = min((now - c.last_reply_time for c in active_clients), default=-1.0)
        overall_state = "CONNECTED" if (active_clients and min_reply_age >= 0 and min_reply_age <= 60.0) else ("RECONNECTING" if clients else "DISCONNECTED")

        st: Dict[str, Any] = {
            "bridge_connected": len(active_clients) > 0,
            "bridge_state": overall_state,
            "state": "RUNNING" if len(active_clients) > 0 else "STANDBY",
            "active_connections_count": len(active_clients),
            "total_connections_count": len(clients),
            "connections": conn_details,
            "last_reply_sec_ago": round(min_reply_age, 1) if min_reply_age >= 0 else None,
            "suppressed_alerts_count": self.feed_manager.suppressed_alerts_count if self.feed_manager else 0,
            "symbol_history": self.feed_manager.symbol_history if self.feed_manager else {},
            "broker_utc_offset": self.broker_utc_offset
        }

        if active_clients:
            try:
                st_resp = self.forward_request_to_ea({"action": "STATUS"}, timeout_sec=3.0)
                if st_resp.get("status") == "SUCCESS":
                    st.update(st_resp.get("data", {}))

                acc_resp = self.forward_request_to_ea({"action": "ACCOUNT"}, timeout_sec=3.0)
                if acc_resp.get("status") == "SUCCESS":
                    st.update(acc_resp.get("data", {}))
            except Exception as e:
                logger.debug(f"Status query failed in callback: {e}")

        return st

    def _enable_trading_action(self) -> bool:
        res = self.forward_request_to_ea({"action": "ENABLE"}, timeout_sec=5.0)
        return res.get("status") == "SUCCESS"

    def _disable_trading_action(self) -> bool:
        res = self.forward_request_to_ea({"action": "DISABLE"}, timeout_sec=5.0)
        return res.get("status") == "SUCCESS"

    def _pause_action(self) -> bool:
        res = self.forward_request_to_ea({"action": "PAUSE"}, timeout_sec=5.0)
        return res.get("status") == "SUCCESS"

    def _resume_action(self) -> bool:
        res = self.forward_request_to_ea({"action": "RESUME"}, timeout_sec=5.0)
        return res.get("status") == "SUCCESS"

    def _closeall_action(self) -> int:
        res = self.forward_request_to_ea({"action": "CLOSE_ALL"}, timeout_sec=8.0)
        if res.get("status") == "SUCCESS":
            # Count across results if broadcast
            if "results" in res:
                total_closed = 0
                for r in res["results"].values():
                    total_closed += int(r.get("closed", 0) or r.get("positions_closed", 0))
                return total_closed
            return int(res.get("closed", 0) or res.get("positions_closed", 0))
        return 0

    def _killswitch_action(self) -> bool:
        res = self.forward_request_to_ea({"action": "KILL_SWITCH"}, timeout_sec=8.0)
        return res.get("status") == "SUCCESS"

    def _reset_state_action(self) -> Tuple[bool, str]:
        if not self.is_ea_connected():
            return True, "✅ <b>LOCAL RESET COMPLETE</b>: Daily counters and suppression tracking cleared (EA offline)."
        try:
            res = self.forward_request_to_ea({"action": "RESET_STATE"}, timeout_sec=5.0)
            if res.get("status") == "SUCCESS":
                baseline = float(res.get("baseline", 0.0))
                return True, f"✅ <b>STATE RESET COMPLETE</b>: Baseline balance, day-start balance, and peak equity reset to ${baseline:.2f}. Daily counters cleared."
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
