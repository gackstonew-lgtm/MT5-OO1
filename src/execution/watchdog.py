"""
Always-On Reliability, Heartbeat Watchdog & State Reconciliation (Phase 5).
Ensures safe autonomous operation during low-latency Fast Mode execution:
- Heartbeat & watchdog between MQL5 EA and Python engine.
- Safe state on disconnect or stale data: stops opening new trades, leaves server-side SL/TP in place, emits CRITICAL alert.
- Auto-reconnect with full state reconciliation (compares expected vs MT5 actual positions; flags discrepancies).
- Stale-tick guard (rejects entries if last_tick_time > stale_tick_max_age_seconds).
- Clock/offset sanity check, market-closed/weekend guard, and graceful shutdown cancelling orphaned pending orders.
"""

from __future__ import annotations
import json
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable, Dict, List, Optional, Set, Tuple, Any

from src.core.contracts import Position, SymbolInfo, TradeOrder


@dataclass
class StateReconciliationReport:
    in_sync: bool
    expected_tickets: Set[int]
    actual_tickets: Set[int]
    missing_in_mt5: Set[int]
    unexpected_in_mt5: Set[int]
    sl_tp_mismatches: List[int]
    unprotected_tickets: List[int]
    discrepancy_messages: List[str] = field(default_factory=list)
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class FastWatchdog:
    def __init__(self, config_path: str = "config/fast_mode.json"):
        self.config_path = config_path
        self._cfg = self._load_config()

        self.last_heartbeat_ts: float = time.time()
        self.last_reconciliation_ts: float = 0.0
        self.connected: bool = True
        self.safe_state_active: bool = False
        self.safe_state_reason: Optional[str] = None

        self.critical_alerts: List[str] = []
        self.reconciliation_reports: List[StateReconciliationReport] = []
        self.expected_positions: Dict[int, Position] = {}

    def _load_config(self) -> Dict[str, Any]:
        if os.path.exists(self.config_path):
            try:
                with open(self.config_path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
        return {}

    def reload_config(self) -> None:
        self._cfg = self._load_config()

    def register_expected_position(self, position: Position) -> None:
        self.expected_positions[position.ticket] = position

    def unregister_expected_position(self, ticket: int) -> None:
        self.expected_positions.pop(ticket, None)

    def enter_safe_state(self, reason: str) -> None:
        """
        Enters protective safe state:
        - Halts all new trade openings.
        - Leaves existing server-side SL/TP orders untouched on MT5.
        - Emits a CRITICAL alert.
        """
        if not self.safe_state_active or self.safe_state_reason != reason:
            self.safe_state_active = True
            self.safe_state_reason = reason
            alert = (
                f"CRITICAL WATCHDOG SAFE STATE: {reason} | "
                f"New entries halted; existing positions remain protected by server-side SL/TP."
            )
            self.critical_alerts.append(alert)

    def clear_safe_state(self) -> None:
        self.safe_state_active = False
        self.safe_state_reason = None
        self.connected = True

    def record_heartbeat(
        self,
        now_ts: Optional[float] = None,
        mt5_positions: Optional[List[Position]] = None
    ) -> Optional[StateReconciliationReport]:
        """
        Records a live heartbeat from the MQL5 EA.
        If recovering from a disconnect, automatically runs full state reconciliation.
        """
        ts = now_ts if now_ts is not None else time.time()
        was_disconnected = not self.connected or self.safe_state_active
        self.last_heartbeat_ts = ts
        self.connected = True

        rel_cfg = self._cfg.get("reliability", {})
        recon_interval = float(rel_cfg.get("reconciliation_interval_seconds", 15.0))

        if mt5_positions is not None and (was_disconnected or (ts - self.last_reconciliation_ts) >= recon_interval):
            report = self.reconcile_positions(mt5_positions, now_ts=ts)
            if was_disconnected and report.in_sync and not report.unprotected_tickets:
                self.clear_safe_state()
            return report
        return None

    def check_heartbeat_health(self, now_ts: Optional[float] = None) -> Tuple[bool, Optional[str]]:
        """
        Checks whether the EA heartbeat is within configured timeout (3x heartbeat_interval_seconds).
        Enters safe state if heartbeat is lost.
        """
        ts = now_ts if now_ts is not None else time.time()
        rel_cfg = self._cfg.get("reliability", {})
        hb_interval = float(rel_cfg.get("heartbeat_interval_seconds", 5.0))
        timeout_s = max(hb_interval * 3.0, 10.0)

        elapsed = ts - self.last_heartbeat_ts
        if elapsed > timeout_s:
            self.connected = False
            reason = f"EA heartbeat timeout ({elapsed:.1f}s > {timeout_s:.1f}s limit)"
            self.enter_safe_state(reason)
            return False, reason

        return True, None

    def reconcile_positions(
        self,
        mt5_actual_positions: List[Position],
        now_ts: Optional[float] = None
    ) -> StateReconciliationReport:
        """
        Compares the Python bot's expected positions to MT5's actual open positions.
        Flags missing tickets, unexpected manual/orphaned tickets, SL/TP discrepancies,
        and any position missing server-side SL protection.
        Synchronizes internal state to MT5 ground truth after flagging discrepancies.
        """
        ts = now_ts if now_ts is not None else time.time()
        self.last_reconciliation_ts = ts

        expected_map = dict(self.expected_positions)
        actual_map = {p.ticket: p for p in mt5_actual_positions}

        expected_tickets = set(expected_map.keys())
        actual_tickets = set(actual_map.keys())

        missing_in_mt5 = expected_tickets - actual_tickets
        unexpected_in_mt5 = actual_tickets - expected_tickets
        common_tickets = expected_tickets & actual_tickets

        sl_tp_mismatches: List[int] = []
        unprotected_tickets: List[int] = []
        messages: List[str] = []

        if missing_in_mt5:
            for t in sorted(missing_in_mt5):
                pos = expected_map[t]
                messages.append(
                    f"Position #{t} ({pos.symbol}) closed on MT5 (likely server-side SL/TP fill while disconnected)"
                )

        if unexpected_in_mt5:
            for t in sorted(unexpected_in_mt5):
                pos = actual_map[t]
                messages.append(
                    f"Unexpected open position #{t} ({pos.symbol} {pos.order_type.value} {pos.volume} lots) found on MT5"
                )

        for t in sorted(common_tickets):
            exp_p = expected_map[t]
            act_p = actual_map[t]
            if abs(exp_p.stop_loss - act_p.stop_loss) > 1e-5 or abs(exp_p.take_profit - act_p.take_profit) > 1e-5:
                sl_tp_mismatches.append(t)
                messages.append(
                    f"SL/TP mismatch on #{t} ({act_p.symbol}): expected SL={exp_p.stop_loss}/TP={exp_p.take_profit}, "
                    f"actual SL={act_p.stop_loss}/TP={act_p.take_profit}"
                )

        for t, act_p in actual_map.items():
            if act_p.stop_loss <= 0:
                unprotected_tickets.append(t)
                messages.append(f"CRITICAL: Open position #{t} ({act_p.symbol}) has NO server-side Stop Loss!")

        in_sync = (
            len(missing_in_mt5) == 0
            and len(unexpected_in_mt5) == 0
            and len(sl_tp_mismatches) == 0
            and len(unprotected_tickets) == 0
        )

        # Synchronize internal expected map to MT5 ground truth
        self.expected_positions = dict(actual_map)

        if unprotected_tickets:
            self.enter_safe_state(f"Unprotected position(s) detected without SL: {unprotected_tickets}")
        elif unexpected_in_mt5 or sl_tp_mismatches:
            self.critical_alerts.append(
                f"RECONCILIATION DISCREPANCY: {'; '.join(messages)}"
            )

        report = StateReconciliationReport(
            in_sync=in_sync,
            expected_tickets=expected_tickets,
            actual_tickets=actual_tickets,
            missing_in_mt5=missing_in_mt5,
            unexpected_in_mt5=unexpected_in_mt5,
            sl_tp_mismatches=sl_tp_mismatches,
            unprotected_tickets=unprotected_tickets,
            discrepancy_messages=messages
        )
        self.reconciliation_reports.append(report)
        return report

    def validate_tick_freshness(
        self,
        symbol_info: SymbolInfo,
        now_utc: Optional[datetime] = None
    ) -> Tuple[bool, Optional[str]]:
        """
        Stale-tick guard: rejects new entries if `symbol_info.last_tick_time` is older than
        `stale_tick_max_age_seconds` (default 5.0s).
        """
        if symbol_info.last_tick_time is None:
            return True, None

        now = now_utc or datetime.now(timezone.utc)
        tick_time = symbol_info.last_tick_time
        if tick_time.tzinfo is None:
            tick_time = tick_time.replace(tzinfo=timezone.utc)

        rel_cfg = self._cfg.get("reliability", {})
        max_age_s = float(rel_cfg.get("stale_tick_max_age_seconds", 5.0))
        age_s = (now - tick_time).total_seconds()

        if age_s > max_age_s:
            reason = f"Stale tick on {symbol_info.symbol}: tick age {age_s:.2f}s > {max_age_s:.1f}s limit"
            self.enter_safe_state(reason)
            return False, reason

        return True, None

    def validate_clock_sanity(
        self,
        broker_utc_time: datetime,
        local_utc_time: Optional[datetime] = None
    ) -> Tuple[bool, Optional[str]]:
        """
        Clock/offset sanity check: blocks entries if broker UTC timestamp drifts from
        system UTC by more than `max_clock_drift_seconds` (default 10s).
        """
        now = local_utc_time or datetime.now(timezone.utc)
        b_utc = broker_utc_time if broker_utc_time.tzinfo else broker_utc_time.replace(tzinfo=timezone.utc)

        rel_cfg = self._cfg.get("reliability", {})
        max_drift_s = float(rel_cfg.get("max_clock_drift_seconds", 10.0))
        drift_s = abs((now - b_utc).total_seconds())

        if drift_s > max_drift_s:
            reason = f"Clock drift sanity check failed: |local_utc - broker_utc| = {drift_s:.1f}s > {max_drift_s:.1f}s"
            self.enter_safe_state(reason)
            return False, reason

        return True, None

    @staticmethod
    def is_market_open(now_utc: Optional[datetime] = None) -> Tuple[bool, Optional[str]]:
        """
        Detects FX/CFD weekend market closure (Friday 22:00 UTC through Sunday 22:00 UTC).
        """
        now = now_utc or datetime.now(timezone.utc)
        wd = now.weekday()  # Monday=0 ... Friday=4, Saturday=5, Sunday=6
        if wd == 5:
            return False, "Weekend market closed (Saturday)"
        if wd == 4 and now.hour >= 22:
            return False, "Weekend market closed (Friday >= 22:00 UTC)"
        if wd == 6 and now.hour < 22:
            return False, "Weekend market closed (Sunday < 22:00 UTC)"
        return True, None

    def can_open_new_trade(
        self,
        symbol_info: Optional[SymbolInfo] = None,
        now_utc: Optional[datetime] = None
    ) -> Tuple[bool, Optional[str]]:
        """
        Master reliability gate checked before any Fast Mode order dispatch.
        """
        if self.safe_state_active:
            return False, f"Watchdog Safe State active: {self.safe_state_reason}"

        mkt_open, mkt_reason = self.is_market_open(now_utc)
        if not mkt_open:
            return False, mkt_reason

        if symbol_info is not None:
            fresh, tick_reason = self.validate_tick_freshness(symbol_info, now_utc)
            if not fresh:
                return False, tick_reason

        return True, None

    def graceful_shutdown(
        self,
        pending_orders: List[TradeOrder],
        open_positions: List[Position],
        cancel_order_callback: Optional[Callable[[int], bool]] = None
    ) -> Dict[str, Any]:
        """
        Graceful shutdown handler:
        - Cancels all orphaned pending orders on MT5 so none trigger unattended.
        - Leaves open positions intact with their mandatory server-side SL/TP.
        """
        rel_cfg = self._cfg.get("reliability", {})
        cancel_pendings = bool(rel_cfg.get("cancel_orphaned_pendings_on_shutdown", True))

        cancelled_tickets: List[int] = []
        if cancel_pendings:
            for order in pending_orders:
                if order.ticket is not None:
                    ok = True
                    if cancel_order_callback is not None:
                        ok = bool(cancel_order_callback(order.ticket))
                    if ok:
                        cancelled_tickets.append(order.ticket)

        protected_positions = [p.ticket for p in open_positions if p.stop_loss > 0]
        unprotected_positions = [p.ticket for p in open_positions if p.stop_loss <= 0]

        self.enter_safe_state("Bot graceful shutdown initiated")
        return {
            "cancelled_pending_tickets": cancelled_tickets,
            "open_positions_left_protected": protected_positions,
            "unprotected_positions_warning": unprotected_positions
        }
