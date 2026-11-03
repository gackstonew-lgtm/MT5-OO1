"""
Low-Latency Persistent Socket & Broker-Constraint Order Executor (Phase 1).
Provides:
1. Persistent TCP Socket with TCP_NODELAY (no Nagle buffering) to eliminate per-request handshake latency.
2. Symbol Specification Cache (tick value, stops_level, freeze_level, filling_mode).
3. Broker Constraint Validator:
   - Auto-detects and falls back across filling modes (FOK -> IOC -> RETURN).
   - Enforces minimum stop distance (SYMBOL_TRADE_STOPS_LEVEL) and freeze level (SYMBOL_TRADE_FREEZE_LEVEL).
   - Strictly requires server-side SL and TP at order placement time.
4. Bounded Requote/Off-Quotes Retry Loop:
   - Re-validates live price and spread before each retry.
   - Enforces per-symbol max deviation (slippage points).
5. Idempotent Client Order IDs:
   - Prevents duplicate fills across retries or socket reconnects.
6. Nanosecond Latency & Slippage Instrumentation.
"""

from __future__ import annotations
import hashlib
import json
import socket
import threading
import time
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional, Tuple, Callable

from src.core.contracts import (
    SymbolInfo, OrderType, ExecutionFillingMode, ExecutionLatencyRecord
)


class PersistentBridgeSocket:
    """
    Maintains a persistent, low-latency TCP socket connection with TCP_NODELAY.
    Automatically reconnects if the connection drops.
    """
    def __init__(self, host: str = "127.0.0.1", port: int = 5556, timeout_sec: float = 2.0):
        self.host = host
        self.port = port
        self.timeout_sec = timeout_sec
        self._sock: Optional[socket.socket] = None
        self._lock = threading.Lock()
        self._rx_buffer = b""
        self.reconnect_count = 0

    def _ensure_connected(self) -> bool:
        if self._sock is not None:
            return True
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            s.settimeout(self.timeout_sec)
            s.connect((self.host, self.port))
            self._sock = s
            self._rx_buffer = b""
            return True
        except Exception:
            self._sock = None
            return False

    def close(self) -> None:
        with self._lock:
            self._close_unsafe()

    def _close_unsafe(self) -> None:
        if self._sock is not None:
            try:
                self._sock.close()
            except Exception:
                pass
            self._sock = None
            self._rx_buffer = b""

    def send_request(self, payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Sends JSON request over persistent socket; reconnects once on broken pipe."""
        with self._lock:
            for attempt in range(2):
                if not self._ensure_connected():
                    if attempt == 0:
                        self.reconnect_count += 1
                        continue
                    return None
                try:
                    msg = (json.dumps(payload) + "\n").encode("utf-8")
                    self._sock.sendall(msg)
                    start_t = time.perf_counter()
                    while (time.perf_counter() - start_t) < self.timeout_sec:
                        if b"\n" in self._rx_buffer:
                            line, self._rx_buffer = self._rx_buffer.split(b"\n", 1)
                            text = line.decode("utf-8", errors="ignore").strip()
                            if text:
                                return json.loads(text)
                        chunk = self._sock.recv(4096)
                        if not chunk:
                            raise ConnectionResetError("Socket closed by peer")
                        self._rx_buffer += chunk
                    return {"status": "ERROR", "message": "Persistent socket read timeout"}
                except Exception:
                    self._close_unsafe()
                    self.reconnect_count += 1
            return None


class BrokerConstraintValidator:
    """
    Validates and adapts order parameters to broker-specific constraints:
    - Filling mode detection & fallback (FOK / IOC / RETURN)
    - Minimum stop distance (stops_level)
    - Freeze level check (freeze_level) for modifications and closes
    - Server-side SL & TP attachment guarantee
    """
    # MT5 SYMBOL_FILLING_MODE bitmask constants:
    # SYMBOL_FILLING_FOK = 1, SYMBOL_FILLING_IOC = 2, RETURN = 0/4 (always allowed on exchange/market)
    FILLING_FOK_BIT = 1
    FILLING_IOC_BIT = 2

    @classmethod
    def resolve_filling_mode(
        cls,
        symbol_filling_bitmask: int,
        preferred_order: Optional[List[str]] = None
    ) -> str:
        """
        Determines the optimal supported filling mode for a symbol given its MT5 bitmask.
        """
        prefs = preferred_order or ["FOK", "IOC", "RETURN"]
        supported = set()
        if symbol_filling_bitmask & cls.FILLING_FOK_BIT:
            supported.add("FOK")
        if symbol_filling_bitmask & cls.FILLING_IOC_BIT:
            supported.add("IOC")
        # RETURN is supported when bitmask is 0 or 4 or as fallback
        if symbol_filling_bitmask == 0 or (symbol_filling_bitmask & 4):
            supported.add("RETURN")

        for mode in prefs:
            m = mode.upper()
            if m in supported:
                return m

        # Fallback hierarchy
        if "FOK" in supported:
            return "FOK"
        if "IOC" in supported:
            return "IOC"
        return "RETURN"

    @classmethod
    def get_next_fallback_filling_mode(cls, current_mode: str) -> Optional[str]:
        """Returns next filling mode if broker rejects with INVALID_FILL (10030)."""
        order = ["FOK", "IOC", "RETURN"]
        cur = current_mode.upper()
        if cur in order:
            idx = order.index(cur)
            if idx + 1 < len(order):
                return order[idx + 1]
        return None

    @staticmethod
    def validate_server_side_stops(
        direction: OrderType,
        entry_price: float,
        stop_loss: float,
        take_profit: float,
        symbol_info: SymbolInfo
    ) -> Tuple[bool, Optional[str]]:
        """
        Strictly requires server-side SL and TP to be non-zero, directionally valid,
        and outside the broker's minimum SYMBOL_TRADE_STOPS_LEVEL distance.
        """
        if stop_loss <= 0.0 or take_profit <= 0.0:
            return False, "Server-side Stop Loss and Take Profit must both be strictly > 0.0 at order time."

        pt = symbol_info.point if symbol_info.point > 0 else 0.00001
        min_stops_pts = max(int(getattr(symbol_info, "stops_level", 0)), 1)
        min_dist = min_stops_pts * pt

        if direction in (OrderType.BUY, OrderType.BUY_LIMIT, OrderType.BUY_STOP):
            if stop_loss >= entry_price:
                return False, f"Invalid BUY SL ({stop_loss}) >= entry ({entry_price})"
            if take_profit <= entry_price:
                return False, f"Invalid BUY TP ({take_profit}) <= entry ({entry_price})"
            if (entry_price - stop_loss) < min_dist - 1e-9:
                return False, f"BUY SL distance ({(entry_price - stop_loss)/pt:.1f} pts) violates broker stops_level ({min_stops_pts} pts)"
            if (take_profit - entry_price) < min_dist - 1e-9:
                return False, f"BUY TP distance ({(take_profit - entry_price)/pt:.1f} pts) violates broker stops_level ({min_stops_pts} pts)"
        else:
            if stop_loss <= entry_price:
                return False, f"Invalid SELL SL ({stop_loss}) <= entry ({entry_price})"
            if take_profit >= entry_price:
                return False, f"Invalid SELL TP ({take_profit}) >= entry ({entry_price})"
            if (stop_loss - entry_price) < min_dist - 1e-9:
                return False, f"SELL SL distance ({(stop_loss - entry_price)/pt:.1f} pts) violates broker stops_level ({min_stops_pts} pts)"
            if (entry_price - take_profit) < min_dist - 1e-9:
                return False, f"SELL TP distance ({(entry_price - take_profit)/pt:.1f} pts) violates broker stops_level ({min_stops_pts} pts)"

        return True, None

    @staticmethod
    def validate_modification_constraints(
        direction: OrderType,
        current_price: float,
        current_sl: float,
        current_tp: float,
        new_sl: float,
        new_tp: float,
        symbol_info: SymbolInfo
    ) -> Tuple[bool, Optional[str]]:
        """
        Verifies that modifying SL/TP respects both:
        1. SYMBOL_TRADE_FREEZE_LEVEL (cannot modify if market is too close to existing SL/TP).
        2. SYMBOL_TRADE_STOPS_LEVEL (new SL/TP must be at least stops_level away from current price).
        """
        pt = symbol_info.point if symbol_info.point > 0 else 0.00001
        freeze_pts = int(getattr(symbol_info, "freeze_level", 0))
        stops_pts = int(getattr(symbol_info, "stops_level", 0))

        freeze_dist = freeze_pts * pt
        stops_dist = stops_pts * pt

        # 1. Check Freeze Level on existing SL and TP
        if freeze_pts > 0:
            if current_sl > 0 and abs(current_price - current_sl) <= freeze_dist + 1e-9:
                return False, f"Modification blocked by broker freeze_level ({freeze_pts} pts): price {current_price} too close to current SL {current_sl}"
            if current_tp > 0 and abs(current_price - current_tp) <= freeze_dist + 1e-9:
                return False, f"Modification blocked by broker freeze_level ({freeze_pts} pts): price {current_price} too close to current TP {current_tp}"

        # 2. Check Stops Level on new SL and TP
        if stops_pts > 0:
            if new_sl > 0:
                if direction == OrderType.BUY and (current_price - new_sl) < stops_dist - 1e-9:
                    return False, f"New BUY SL {new_sl} violates broker stops_level ({stops_pts} pts) from price {current_price}"
                if direction == OrderType.SELL and (new_sl - current_price) < stops_dist - 1e-9:
                    return False, f"New SELL SL {new_sl} violates broker stops_level ({stops_pts} pts) from price {current_price}"
            if new_tp > 0:
                if direction == OrderType.BUY and (new_tp - current_price) < stops_dist - 1e-9:
                    return False, f"New BUY TP {new_tp} violates broker stops_level ({stops_pts} pts) from price {current_price}"
                if direction == OrderType.SELL and (current_price - new_tp) < stops_dist - 1e-9:
                    return False, f"New SELL TP {new_tp} violates broker stops_level ({stops_pts} pts) from price {current_price}"

        return True, None


class LowLatencyOrderDispatcher:
    """
    Executes orders with:
    - Deterministic idempotent client_order_id
    - Automatic filling mode fallback on retcode 10030 (INVALID_FILL)
    - Bounded retries on transient retcodes (10004 REQUOTE, 10021 OFF_QUOTES, 10016 CONTEXT_BUSY)
      with live price and spread re-validation before each retry
    - Full nanosecond latency and slippage instrumentation
    """
    TRANSIENT_RETCODES = {10004, 10021, 10016, 10031}  # REQUOTE, PRICE_CHANGED/OFF_QUOTES, CONTEXT_BUSY, TIMEOUT
    INVALID_FILL_RETCODE = 10030

    def __init__(
        self,
        max_retries: int = 3,
        retry_delay_ms: int = 20,
        max_deviation_map: Optional[Dict[str, int]] = None
    ):
        self.max_retries = max_retries
        self.retry_delay_ms = retry_delay_ms
        self.max_deviation_map = max_deviation_map or {"XAUUSD": 30, "EURUSD": 10, "DEFAULT": 15}
        self._executed_client_orders: Dict[str, Dict[str, Any]] = {}
        self._symbol_spec_cache: Dict[str, SymbolInfo] = {}
        self.latency_records: List[ExecutionLatencyRecord] = []
        self._lock = threading.Lock()

    def cache_symbol_spec(self, symbol_info: SymbolInfo) -> None:
        self._symbol_spec_cache[symbol_info.symbol] = symbol_info

    def get_cached_symbol_spec(self, symbol: str) -> Optional[SymbolInfo]:
        return self._symbol_spec_cache.get(symbol)

    @staticmethod
    def generate_client_order_id(
        symbol: str,
        direction: OrderType,
        bar_timestamp: datetime,
        entry_price: float
    ) -> str:
        """Generates deterministic idempotent client_order_id."""
        raw = f"{symbol}|{direction.value}|{bar_timestamp.isoformat()}|{entry_price:.5f}"
        digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12].upper()
        return f"CID_{symbol.replace('.', '_')}_{direction.value}_{digest}"

    def get_max_deviation(self, symbol: str) -> int:
        clean = symbol.replace(".m", "").replace("m", "").upper()
        return int(self.max_deviation_map.get(clean, self.max_deviation_map.get("DEFAULT", 15)))

    def execute_idempotent_order(
        self,
        client_order_id: str,
        symbol: str,
        direction: OrderType,
        volume: float,
        requested_price: float,
        stop_loss: float,
        take_profit: float,
        symbol_info: SymbolInfo,
        send_fn: Callable[[Dict[str, Any]], Dict[str, Any]],
        quote_refresh_fn: Optional[Callable[[str], Tuple[float, float, float]]] = None,
        max_spread_points: Optional[float] = None,
        bar_received_ns: int = 0,
        analysis_done_ns: int = 0,
        checklist_passed_ns: int = 0,
        magic: int = 100101,
        comment: str = "FAST_SCALP"
    ) -> Tuple[Dict[str, Any], ExecutionLatencyRecord]:
        """
        Dispatches order idempotently with bounded retries, filling-mode fallback,
        and stage-by-stage latency instrumentation.
        `quote_refresh_fn(symbol)` returns `(bid, ask, spread_points)`.
        """
        now_ns = time.perf_counter_ns()
        bar_ns = bar_received_ns or now_ns
        ana_ns = analysis_done_ns or now_ns
        chk_ns = checklist_passed_ns or now_ns

        # 1. Idempotency check: never send duplicate order if already filled
        with self._lock:
            if client_order_id in self._executed_client_orders:
                cached = self._executed_client_orders[client_order_id]
                rec = ExecutionLatencyRecord(
                    client_order_id=client_order_id,
                    symbol=symbol,
                    direction=direction.value,
                    bar_received_ns=bar_ns,
                    analysis_done_ns=ana_ns,
                    checklist_passed_ns=chk_ns,
                    order_sent_ns=now_ns,
                    fill_confirmed_ns=now_ns,
                    requested_price=requested_price,
                    fill_price=float(cached.get("price", requested_price)),
                    status="DUPLICATE_PREVENTED"
                )
                return cached, rec

        # 2. Validate server-side SL & TP and stops_level
        valid_stops, stop_err = BrokerConstraintValidator.validate_server_side_stops(
            direction=direction,
            entry_price=requested_price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            symbol_info=symbol_info
        )
        if not valid_stops:
            err_res = {"status": "REJECTED", "message": stop_err, "client_order_id": client_order_id}
            rec = ExecutionLatencyRecord(
                client_order_id=client_order_id,
                symbol=symbol,
                direction=direction.value,
                bar_received_ns=bar_ns,
                analysis_done_ns=ana_ns,
                checklist_passed_ns=chk_ns,
                order_sent_ns=now_ns,
                fill_confirmed_ns=time.perf_counter_ns(),
                requested_price=requested_price,
                status="REJECTED_STOPS_LEVEL"
            )
            self.latency_records.append(rec)
            return err_res, rec

        filling_mode = getattr(symbol_info, "filling_mode", "FOK") or "FOK"
        deviation_pts = self.get_max_deviation(symbol)
        pt = symbol_info.point if symbol_info.point > 0 else 0.00001

        requotes = 0
        current_req_price = requested_price
        current_spread_pts = float(symbol_info.spread)

        order_sent_ns = time.perf_counter_ns()
        last_resp: Dict[str, Any] = {"status": "ERROR", "message": "Not sent"}

        for attempt in range(self.max_retries + 1):
            # Re-validate live quote and spread on retry
            if attempt > 0 and quote_refresh_fn is not None:
                bid, ask, live_spread_pts = quote_refresh_fn(symbol)
                current_spread_pts = live_spread_pts
                if max_spread_points is not None and live_spread_pts > max_spread_points:
                    last_resp = {
                        "status": "REJECTED",
                        "message": f"Retry aborted: spread widened to {live_spread_pts:.1f} pts > max {max_spread_points:.1f} pts"
                    }
                    break
                new_price = ask if direction == OrderType.BUY else bid
                drift_pts = abs(new_price - requested_price) / pt
                if drift_pts > deviation_pts:
                    last_resp = {
                        "status": "REJECTED",
                        "message": f"Retry aborted: price drifted {drift_pts:.1f} pts > max deviation {deviation_pts} pts"
                    }
                    break
                current_req_price = new_price

            payload = {
                "action": "TRADE",
                "request_id": client_order_id,
                "client_order_id": client_order_id,
                "symbol": symbol,
                "type": direction.value,
                "volume": volume,
                "price": current_req_price,
                "sl": stop_loss,
                "tp": take_profit,
                "deviation": deviation_pts,
                "filling_mode": filling_mode,
                "magic": magic,
                "comment": comment
            }

            last_resp = send_fn(payload) or {"status": "ERROR", "message": "Null response"}
            retcode = int(last_resp.get("retcode", 0))
            status = str(last_resp.get("status", "ERROR")).upper()

            if status == "SUCCESS" or retcode in (10009, 10008):
                fill_ns = time.perf_counter_ns()
                fill_price = float(last_resp.get("price", current_req_price))
                # Positive slippage = adverse fill, negative = favorable price improvement
                if direction == OrderType.BUY:
                    slip_pts = round((fill_price - requested_price) / pt, 2)
                else:
                    slip_pts = round((requested_price - fill_price) / pt, 2)

                rec = ExecutionLatencyRecord(
                    client_order_id=client_order_id,
                    symbol=symbol,
                    direction=direction.value,
                    bar_received_ns=bar_ns,
                    analysis_done_ns=ana_ns,
                    checklist_passed_ns=chk_ns,
                    order_sent_ns=order_sent_ns,
                    fill_confirmed_ns=fill_ns,
                    requote_count=requotes,
                    requested_price=requested_price,
                    fill_price=fill_price,
                    slippage_points=slip_pts,
                    spread_at_entry_points=current_spread_pts,
                    filling_mode=filling_mode,
                    status="FILLED"
                )
                with self._lock:
                    self._executed_client_orders[client_order_id] = last_resp
                    self.latency_records.append(rec)
                return last_resp, rec

            # Handle INVALID_FILL (10030) by falling back to next filling mode immediately
            if retcode == self.INVALID_FILL_RETCODE:
                next_mode = BrokerConstraintValidator.get_next_fallback_filling_mode(filling_mode)
                if next_mode:
                    filling_mode = next_mode
                    continue
                break

            # Handle transient requotes / off-quotes within bounded retries
            if retcode in self.TRANSIENT_RETCODES and attempt < self.max_retries:
                requotes += 1
                if self.retry_delay_ms > 0:
                    time.sleep(self.retry_delay_ms / 1000.0)
                continue

            break

        fill_ns = time.perf_counter_ns()
        rec = ExecutionLatencyRecord(
            client_order_id=client_order_id,
            symbol=symbol,
            direction=direction.value,
            bar_received_ns=bar_ns,
            analysis_done_ns=ana_ns,
            checklist_passed_ns=chk_ns,
            order_sent_ns=order_sent_ns,
            fill_confirmed_ns=fill_ns,
            requote_count=requotes,
            requested_price=requested_price,
            fill_price=0.0,
            slippage_points=0.0,
            spread_at_entry_points=current_spread_pts,
            filling_mode=filling_mode,
            status="FAILED"
        )
        with self._lock:
            self.latency_records.append(rec)
        return last_resp, rec

    def get_quality_summary(self) -> Dict[str, Any]:
        """Returns aggregate latency, slippage, requote, and spread telemetry for /fastquality."""
        with self._lock:
            filled = [r for r in self.latency_records if r.status == "FILLED"]
            total_count = len(self.latency_records)
            if not filled:
                return {
                    "total_attempts": total_count,
                    "filled_count": 0,
                    "avg_total_ms": 0.0,
                    "avg_dispatch_ms": 0.0,
                    "max_total_ms": 0.0,
                    "avg_slippage_pts": 0.0,
                    "avg_spread_pts": 0.0,
                    "total_requotes": sum(r.requote_count for r in self.latency_records)
                }

            n = len(filled)
            return {
                "total_attempts": total_count,
                "filled_count": n,
                "avg_total_ms": round(sum(r.total_ms for r in filled) / n, 3),
                "avg_dispatch_ms": round(sum(r.dispatch_ms for r in filled) / n, 3),
                "max_total_ms": round(max(r.total_ms for r in filled), 3),
                "avg_slippage_pts": round(sum(r.slippage_points for r in filled) / n, 2),
                "avg_spread_pts": round(sum(r.spread_at_entry_points for r in filled) / n, 2),
                "total_requotes": sum(r.requote_count for r in self.latency_records)
            }
