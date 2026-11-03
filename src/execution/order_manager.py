"""
Order Execution Manager and Reconciliation Engine.
Executes the mandatory Pre-Trade Checklist, validates broker responses,
monitors slippage, prevents duplicate orders, and handles simulation mode.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple, Set

from src.core.contracts import (
    SignalCandidate, TradeOrder, Position, AccountInfo, SymbolInfo,
    OrderType, ExecutionMode
)
from src.core.config import AppConfig
from src.risk.position_sizing import PositionSizingEngine, PositionSizeResult
from src.risk.portfolio_risk import PortfolioRiskEngine
from src.execution.bridge_client import BridgeClient


@dataclass
class PreTradeCheckResult:
    passed: bool
    rejection_reasons: List[str] = field(default_factory=list)
    calculated_volume: float = 0.0
    monetary_risk: float = 0.0


class OrderManager:
    def __init__(
        self,
        config: AppConfig,
        bridge: BridgeClient,
        sizing_engine: PositionSizingEngine,
        portfolio_risk: PortfolioRiskEngine
    ):
        self.config = config
        self.bridge = bridge
        self.sizing = sizing_engine
        self.portfolio_risk = portfolio_risk
        self.simulated_positions: List[Position] = []
        self._sim_ticket_counter = 500000
        self._executed_signal_ids: Set[str] = set()

    def run_pre_trade_checklist(
        self,
        candidate: SignalCandidate,
        account: AccountInfo,
        symbol_info: SymbolInfo,
        active_positions: List[Position]
    ) -> PreTradeCheckResult:
        """
        Executes strict 10-point Pre-Trade Validation Checklist.
        """
        rejections: List[str] = []

        # 1. Is MT5 connection healthy? (In live/demo mode)
        if self.config.execution_mode in [ExecutionMode.DEMO, ExecutionMode.REAL]:
            if not self.bridge.ping():
                rejections.append("Pre-check failed: MT5 bridge connection unhealthy or unreachable.")

        # 2. Is signal approved and valid?
        if candidate.status != "APPROVED":
            rejections.append(f"Pre-check failed: Signal candidate is {candidate.status}.")

        # 3. Is stop loss & direction valid?
        sl_distance = abs(candidate.entry_price - candidate.stop_loss)
        if sl_distance <= 0:
            rejections.append("Pre-check failed: Stop loss distance is zero or negative.")

        if candidate.direction in [OrderType.BUY, OrderType.BUY_LIMIT, OrderType.BUY_STOP]:
            if candidate.stop_loss > 0 and candidate.stop_loss >= candidate.entry_price:
                rejections.append(f"Pre-check failed: Invalid stops on BUY - SL ({candidate.stop_loss}) must be below entry ({candidate.entry_price}).")
            if candidate.take_profit > 0 and candidate.take_profit <= candidate.entry_price:
                rejections.append(f"Pre-check failed: Invalid stops on BUY - TP ({candidate.take_profit}) must be above entry ({candidate.entry_price}).")
        elif candidate.direction in [OrderType.SELL, OrderType.SELL_LIMIT, OrderType.SELL_STOP]:
            if candidate.stop_loss > 0 and candidate.stop_loss <= candidate.entry_price:
                rejections.append(f"Pre-check failed: Invalid stops on SELL - SL ({candidate.stop_loss}) must be above entry ({candidate.entry_price}).")
            if candidate.take_profit > 0 and candidate.take_profit >= candidate.entry_price:
                rejections.append(f"Pre-check failed: Invalid stops on SELL - TP ({candidate.take_profit}) must be below entry ({candidate.entry_price}).")

        # 4. Is spread acceptable? (Dynamic Spread Filter: ceiling & % of SL distance)
        spread_pts = float(symbol_info.spread)
        pt = float(symbol_info.point) if symbol_info.point > 0 else 0.01
        sl_dist_pts = sl_distance / pt if pt > 0 else sl_distance

        # Absolute ceiling check
        if spread_pts > 2000.0:
            rejections.append(f"Pre-check failed: Spread ({spread_pts:.1f} pts) exceeds absolute ceiling (2000.0 pts).")
        elif sl_dist_pts > 0:
            spread_pct_sl = (spread_pts / sl_dist_pts) * 100.0
            if spread_pct_sl > 5.0:
                rejections.append(f"Pre-check failed: Spread ({spread_pts:.1f} pts) is {spread_pct_sl:.2f}% of SL distance ({sl_dist_pts:.1f} pts), exceeding 5.0% limit.")

        # 5. Position sizing and volume verification
        size_result = self.sizing.calculate_lot_size(
            account=account,
            symbol_info=symbol_info,
            entry_price=candidate.entry_price,
            stop_loss=candidate.stop_loss,
            risk_percent=self.config.risk.risk_percentage,
            sizing_reference=self.config.risk.sizing_reference
        )
        if not size_result.is_approved:
            rejections.append(f"Pre-check failed (Sizing): {size_result.rejection_reason}")

        # 6. Portfolio risk budget and circuit breaker
        allowed, risk_reason = self.portfolio_risk.evaluate_new_order_permission(
            candidate=candidate,
            account=account,
            active_positions=active_positions,
            proposed_monetary_risk=size_result.monetary_risk,
            proposed_volume=size_result.volume
        )
        if not allowed:
            rejections.append(f"Pre-check failed (Portfolio Risk): {risk_reason}")

        # 7. Duplicate order / signal prevention
        if candidate.id in self._executed_signal_ids:
            rejections.append(f"Pre-check failed: Signal ID {candidate.id} was already executed.")

        # 8. Duplicate conflicting position check
        for p in active_positions:
            if p.symbol == candidate.symbol and p.order_type != candidate.direction:
                rejections.append(f"Pre-check failed: Conflicting active position on {candidate.symbol}.")

        # 9. Is setup still available? (Check price deviation from entry price)
        deviation = abs(symbol_info.ask - candidate.entry_price) if candidate.direction == OrderType.BUY else abs(symbol_info.bid - candidate.entry_price)
        max_dev = 5.0 * symbol_info.point * 10
        if deviation > max_dev and symbol_info.ask > 0:
            rejections.append(f"Pre-check failed: Price drifted {deviation/symbol_info.point:.1f} points away from entry.")

        # 10. Margin sufficiency
        if size_result.margin_required > account.free_margin:
            rejections.append("Pre-check failed: Free margin insufficient.")

        # 11. Structure Confirmation (BOS after CHoCH)
        allow_choch_only = getattr(self.config.strategy, "allow_choch_only_entries", False)
        if not allow_choch_only and hasattr(candidate, "rejection_reasons"):
            for r in candidate.rejection_reasons:
                if "CHoCH only" in r or "trapped" in r or "awaiting BOS" in r:
                    rejections.append(f"Pre-check failed (Structure): Structure confirmed (BOS after CHoCH) check failed: {r}")

        passed = len(rejections) == 0
        return PreTradeCheckResult(
            passed=passed,
            rejection_reasons=rejections,
            calculated_volume=size_result.volume if passed else 0.0,
            monetary_risk=size_result.monetary_risk if passed else 0.0
        )

    def execute_signal(
        self,
        candidate: SignalCandidate,
        account: AccountInfo,
        symbol_info: SymbolInfo,
        active_positions: List[Position]
    ) -> Optional[TradeOrder]:
        """
        Executes pre-trade validation, then submits order to MT5 Bridge
        or simulates order execution in SIMULATION mode.
        """
        check = self.run_pre_trade_checklist(candidate, account, symbol_info, active_positions)
        if not check.passed:
            candidate.status = "REJECTED"
            candidate.rejection_reasons.extend(check.rejection_reasons)
            return None

        # Mode: SIMULATION or BACKTEST
        if self.config.execution_mode in [ExecutionMode.SIMULATION, ExecutionMode.BACKTEST]:
            self._sim_ticket_counter += 1
            sim_ticket = self._sim_ticket_counter

            order = TradeOrder(
                ticket=sim_ticket,
                symbol=candidate.symbol,
                order_type=candidate.direction,
                volume=check.calculated_volume,
                entry_price=candidate.entry_price,
                stop_loss=candidate.stop_loss,
                take_profit=candidate.take_profit,
                magic=self.config.magic_number,
                comment=f"SIM_{candidate.setup_type.value}",
                status="FILLED",
                executed_at=datetime.now(timezone.utc)
            )

            # Register simulated position
            new_pos = Position(
                ticket=sim_ticket,
                symbol=candidate.symbol,
                order_type=candidate.direction,
                volume=check.calculated_volume,
                open_price=candidate.entry_price,
                current_price=candidate.entry_price,
                stop_loss=candidate.stop_loss,
                take_profit=candidate.take_profit,
                profit=0.0,
                magic=self.config.magic_number,
                comment=order.comment,
                open_time=datetime.now(timezone.utc),
                initial_risk_amount=check.monetary_risk,
                initial_sl_distance=abs(candidate.entry_price - candidate.stop_loss)
            )
            self.simulated_positions.append(new_pos)
            self._executed_signal_ids.add(candidate.id)
            self.portfolio_risk.register_trade_executed()
            candidate.status = "EXECUTED"
            return order

        # Mode: DEMO or REAL Live Execution via MT5 Bridge
        res = self.bridge.execute_trade(
            symbol=candidate.symbol,
            order_type=candidate.direction,
            volume=check.calculated_volume,
            stop_loss=candidate.stop_loss,
            take_profit=candidate.take_profit,
            magic=self.config.magic_number,
            comment=f"SMC_{candidate.setup_type.value}"
        )

        if res.get("status") == "SUCCESS":
            ticket = int(res.get("ticket", 0))
            order = TradeOrder(
                ticket=ticket,
                symbol=candidate.symbol,
                order_type=candidate.direction,
                volume=check.calculated_volume,
                entry_price=float(res.get("price", candidate.entry_price)),
                stop_loss=candidate.stop_loss,
                take_profit=candidate.take_profit,
                magic=self.config.magic_number,
                status="FILLED",
                executed_at=datetime.now(timezone.utc)
            )
            self._executed_signal_ids.add(candidate.id)
            self.portfolio_risk.register_trade_executed()
            candidate.status = "EXECUTED"
            return order
        else:
            candidate.status = "REJECTED"
            candidate.rejection_reasons.append(f"MT5 execution error: {res.get('message')}")
            return None

    def get_active_positions(self) -> List[Position]:
        """Returns active positions depending on operating mode."""
        if self.config.execution_mode in [ExecutionMode.SIMULATION, ExecutionMode.BACKTEST]:
            return list(self.simulated_positions)
        return self.bridge.get_positions()
