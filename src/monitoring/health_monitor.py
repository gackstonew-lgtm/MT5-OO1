"""
Continuous Health Monitor & Anti-Dormancy Diagnostic Engine.
Monitors:
- MT5 terminal connection & ZMQ bridge connection
- Incoming tick availability & symbol availability
- Market-data freshness across M1/M5/M15/H1/H4/D1/W1
- Strategy execution status & order execution responses
- Open positions & position-management activity
- Last successful scan time, last generated signal time, last successful trade time
- Exact diagnostic classification when no trade is executed:
  * "No qualifying signal"
  * "Spread exceeds configured limit"
  * "Market data unavailable"
  * "Broker rejects order"
  * "Insufficient margin"
  * "Minimum lot exceeds configured risk"
  * "Trading disabled"
  * "Symbol unavailable"
- Automatic recovery from temporary MT5 / bridge / data interruptions.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime, timezone
import logging
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("ContinuousHealthMonitor")

STANDARD_DIAGNOSTIC_REASONS = (
    "No qualifying signal",
    "Spread exceeds configured limit",
    "Market data unavailable",
    "Broker rejects order",
    "Insufficient margin",
    "Minimum lot exceeds configured risk",
    "Trading disabled",
    "Symbol unavailable",
)


@dataclass
class SymbolDiagnosticState:
    symbol: str
    available: bool = True
    tick_available: bool = True
    data_fresh: bool = True
    last_scan_time: Optional[datetime] = None
    last_signal_time: Optional[datetime] = None
    last_trade_time: Optional[datetime] = None
    last_diagnostic_reason: str = "No qualifying signal"
    last_diagnostic_details: str = ""
    scan_count: int = 0
    signal_count: int = 0
    executed_trade_count: int = 0
    rejection_counts: Dict[str, int] = field(default_factory=dict)


class ContinuousHealthMonitor:
    """
    Continuously tracks runtime health, prevents silent dormancy, classifies non-execution
    diagnostics with exact reasons, and orchestrates automatic MT5/bridge/data recovery.
    """

    def __init__(self, symbols: Optional[List[str]] = None):
        self.symbols: List[str] = list(symbols or ["BTCUSD.m", "EURUSD", "GBPUSD", "USDJPY", "XAUUSD"])
        self.started_at: datetime = datetime.now(timezone.utc)
        self.mt5_terminal_connected: bool = False
        self.bridge_connected: bool = False
        self.strategy_execution_active: bool = True
        self.open_positions_count: int = 0
        self.position_management_actions_count: int = 0
        self.total_scans: int = 0
        self.total_signals: int = 0
        self.total_executed_trades: int = 0
        self.total_recoveries: int = 0

        self.last_successful_scan_time: Optional[datetime] = None
        self.last_generated_signal_time: Optional[datetime] = None
        self.last_successful_trade_time: Optional[datetime] = None
        self.last_position_management_time: Optional[datetime] = None
        self.last_order_response: str = "NONE"

        self.symbol_states: Dict[str, SymbolDiagnosticState] = {
            s: SymbolDiagnosticState(symbol=s) for s in self.symbols
        }
        self.global_reason_counts: Dict[str, int] = {r: 0 for r in STANDARD_DIAGNOSTIC_REASONS}

    def _ensure_symbol(self, symbol: str) -> SymbolDiagnosticState:
        if symbol not in self.symbol_states:
            self.symbol_states[symbol] = SymbolDiagnosticState(symbol=symbol)
        return self.symbol_states[symbol]

    @staticmethod
    def classify_diagnostic_reason(raw_reason: str) -> str:
        """
        Maps any internal rejection/skip reason string to one of the 8 standard diagnostic categories.
        """
        text = (raw_reason or "").lower()
        if "symbol unavailable" in text or "not found" in text or "cannot resolve" in text:
            return "Symbol unavailable"
        if "disabled" in text or "circuit breaker" in text or "daily loss" in text or "drawdown" in text or "paused" in text:
            return "Trading disabled"
        if "minimum lot" in text or "min_lot" in text or "min lot" in text or "exceeds configured risk" in text or "risk floor" in text:
            return "Minimum lot exceeds configured risk"
        if "margin" in text or "free_margin" in text:
            return "Insufficient margin"
        if "spread" in text:
            return "Spread exceeds configured limit"
        if "insufficient" in text or "stale" in text or "market data" in text or "candle" in text or "no tick" in text:
            return "Market data unavailable"
        if "reject" in text or "retcode" in text or "broker" in text or "invalid stops" in text or "requote" in text:
            return "Broker rejects order"
        return "No qualifying signal"

    def record_scan(
        self,
        symbol: str,
        diagnostic_reason: str = "No qualifying signal",
        details: str = "",
        available: bool = True,
        tick_available: bool = True,
        data_fresh: bool = True,
    ) -> str:
        now = datetime.now(timezone.utc)
        canonical_reason = self.classify_diagnostic_reason(diagnostic_reason)
        st = self._ensure_symbol(symbol)
        st.available = available
        st.tick_available = tick_available
        st.data_fresh = data_fresh
        st.last_scan_time = now
        st.scan_count += 1
        st.last_diagnostic_reason = canonical_reason
        st.last_diagnostic_details = details or diagnostic_reason
        st.rejection_counts[canonical_reason] = st.rejection_counts.get(canonical_reason, 0) + 1

        self.total_scans += 1
        self.last_successful_scan_time = now
        self.global_reason_counts[canonical_reason] = self.global_reason_counts.get(canonical_reason, 0) + 1
        return canonical_reason

    def record_signal(self, symbol: str, direction: str, setup_type: str, score: float) -> None:
        now = datetime.now(timezone.utc)
        st = self._ensure_symbol(symbol)
        st.last_signal_time = now
        st.signal_count += 1
        self.total_signals += 1
        self.last_generated_signal_time = now
        logger.info(
            f"[HEALTH] Signal generated on {symbol}: {direction} ({setup_type}, score={score:.1f})"
        )

    def record_trade_execution(
        self,
        symbol: str,
        executed: bool,
        order_response: str,
    ) -> None:
        now = datetime.now(timezone.utc)
        st = self._ensure_symbol(symbol)
        self.last_order_response = order_response
        if executed:
            st.last_trade_time = now
            st.executed_trade_count += 1
            self.total_executed_trades += 1
            self.last_successful_trade_time = now
        else:
            canonical = self.classify_diagnostic_reason(order_response)
            st.last_diagnostic_reason = canonical
            st.last_diagnostic_details = order_response
            st.rejection_counts[canonical] = st.rejection_counts.get(canonical, 0) + 1
            self.global_reason_counts[canonical] = self.global_reason_counts.get(canonical, 0) + 1

    def record_position_management(self, open_positions_count: int, actions_taken: int = 0) -> None:
        now = datetime.now(timezone.utc)
        self.open_positions_count = open_positions_count
        if actions_taken > 0:
            self.position_management_actions_count += actions_taken
            self.last_position_management_time = now

    def check_and_recover_connections(
        self,
        bridge: Any,
        market_data: Any,
        dry_run: bool = True,
    ) -> Tuple[bool, List[str]]:
        """
        Verifies MT5 terminal & bridge connectivity and market-data availability.
        Automatically reconnects and resynchronizes if an interruption is detected.
        """
        recovery_notes: List[str] = []

        # 1. Check MT5 direct connection via MarketDataEngine
        mt5_ok = bool(getattr(market_data, "_mt5_initialized", False))
        if not mt5_ok and hasattr(market_data, "ensure_mt5_initialized"):
            try:
                mt5_ok = bool(market_data.ensure_mt5_initialized())
                if mt5_ok:
                    self.total_recoveries += 1
                    recovery_notes.append("Reconnected to MT5 terminal via Python API")
            except Exception as e:
                logger.debug(f"MT5 direct connection check error: {e}")
        self.mt5_terminal_connected = mt5_ok

        # 2. Check Bridge connection
        bridge_ok = bool(getattr(bridge, "is_connected", False))
        if not bridge_ok and not dry_run and hasattr(bridge, "connect"):
            try:
                bridge_ok = bool(bridge.connect())
                if bridge_ok:
                    self.total_recoveries += 1
                    recovery_notes.append("Reconnected ZMQ execution bridge")
            except Exception as e:
                logger.debug(f"Bridge reconnect error: {e}")
        self.bridge_connected = bridge_ok or dry_run

        # 3. Check symbol data availability & resync any empty symbol buffers
        if mt5_ok and hasattr(market_data, "sync_symbol_history"):
            from src.core.contracts import Timeframe
            for sym in self.symbols:
                m15_bars = market_data.get_candles(sym, Timeframe.M15)
                if len(m15_bars) < 20:
                    try:
                        market_data.sync_symbol_history(sym, bridge=bridge)
                        re_bars = market_data.get_candles(sym, Timeframe.M15)
                        if len(re_bars) >= 20:
                            self.total_recoveries += 1
                            recovery_notes.append(f"Resynchronized candle history for {sym} ({len(re_bars)} M15 bars)")
                    except Exception as e:
                        logger.debug(f"Market data resync error for {sym}: {e}")

        healthy = self.mt5_terminal_connected or self.bridge_connected
        return healthy, recovery_notes

    def get_health_snapshot(self) -> Dict[str, Any]:
        return {
            "mt5_terminal_connected": self.mt5_terminal_connected,
            "bridge_connected": self.bridge_connected,
            "strategy_execution_active": self.strategy_execution_active,
            "open_positions_count": self.open_positions_count,
            "position_management_actions_count": self.position_management_actions_count,
            "total_scans": self.total_scans,
            "total_signals": self.total_signals,
            "total_executed_trades": self.total_executed_trades,
            "total_recoveries": self.total_recoveries,
            "last_successful_scan_time": (
                self.last_successful_scan_time.isoformat() if self.last_successful_scan_time else None
            ),
            "last_generated_signal_time": (
                self.last_generated_signal_time.isoformat() if self.last_generated_signal_time else None
            ),
            "last_successful_trade_time": (
                self.last_successful_trade_time.isoformat() if self.last_successful_trade_time else None
            ),
            "last_order_response": self.last_order_response,
            "diagnostic_reason_counts": dict(self.global_reason_counts),
            "symbols": {
                sym: {
                    "available": st.available,
                    "tick_available": st.tick_available,
                    "data_fresh": st.data_fresh,
                    "scan_count": st.scan_count,
                    "signal_count": st.signal_count,
                    "executed_trade_count": st.executed_trade_count,
                    "last_diagnostic_reason": st.last_diagnostic_reason,
                    "last_diagnostic_details": st.last_diagnostic_details,
                }
                for sym, st in self.symbol_states.items()
            },
        }

    def format_diagnostic_report(self) -> str:
        """
        Formats a concise, human-readable diagnostic report explaining system health
        and exact per-symbol non-execution reasons if no trades have occurred recently.
        """
        scan_ts = (
            self.last_successful_scan_time.strftime("%H:%M:%S UTC")
            if self.last_successful_scan_time
            else "NEVER"
        )
        sig_ts = (
            self.last_generated_signal_time.strftime("%H:%M:%S UTC")
            if self.last_generated_signal_time
            else "NONE"
        )
        trd_ts = (
            self.last_successful_trade_time.strftime("%H:%M:%S UTC")
            if self.last_successful_trade_time
            else "NONE"
        )
        sym_Parts = []
        for sym, st in self.symbol_states.items():
            sym_Parts.append(f"{sym}: {st.last_diagnostic_reason}")
        sym_summary = " | ".join(sym_Parts)
        return (
            f"[CONTINUOUS HEALTH] MT5={'OK' if self.mt5_terminal_connected else 'SIM'} | "
            f"Bridge={'OK' if self.bridge_connected else 'OFF'} | "
            f"Scans={self.total_scans} (last={scan_ts}) | "
            f"Signals={self.total_signals} (last={sig_ts}) | "
            f"Trades={self.total_executed_trades} (last={trd_ts}) | "
            f"OpenPos={self.open_positions_count} | "
            f"Status: {sym_summary}"
        )
