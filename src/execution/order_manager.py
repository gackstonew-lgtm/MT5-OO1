"""
Order Execution Manager and Reconciliation Engine.
Executes the mandatory Pre-Trade Checklist, validates broker responses,
monitors slippage, prevents duplicate orders, and handles simulation mode.
"""

from __future__ import annotations
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set

from src.core.contracts import (
    Candle, SignalCandidate, TradeOrder, Position, AccountInfo, SymbolInfo,
    OrderType, ExecutionMode, TradePlan, PlanAmendment
)
from src.core.config import AppConfig
from src.risk.position_sizing import PositionSizingEngine
from src.risk.portfolio_risk import PortfolioRiskEngine
from src.risk.replay_gate import MonteCarloReplayGate
from src.execution.bridge_client import BridgeClient

logger = logging.getLogger("OrderManager")


@dataclass
class PreTradeCheckResult:
    passed: bool
    rejection_reasons: List[str] = field(default_factory=list)
    calculated_volume: float = 0.0
    monetary_risk: float = 0.0
    trade_plan: Optional[TradePlan] = None


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
        self._active_trade_plans: Dict[int, TradePlan] = {}
        self._signal_trade_plans: Dict[str, TradePlan] = {}
        self.replay_gate = MonteCarloReplayGate(enabled=getattr(self.config.risk, "enable_replay_gate", False))
        self.telegram_cmd_handler: Optional[Any] = None
        self.shadow_journal: Optional[Any] = None
        self.session_engine = None
        self.affinity_engine = None
        self.session_risk = None
        self.fast_risk_guard = None
        self.fast_watchdog = None
        self.fast_exit_manager = None
        try:
            from src.core.sessions import TradingSessionEngine
            from src.core.session_affinity import SessionAffinityEngine
            from src.risk.session_risk import SessionRiskEngine
            self.session_engine = TradingSessionEngine(self.config.session)
            self.affinity_engine = SessionAffinityEngine()
            self.session_risk = SessionRiskEngine()
        except Exception:
            pass
        try:
            from src.risk.fast_risk_guard import FastRiskGuardEngine
            from src.execution.watchdog import FastWatchdog
            from src.execution.fast_exit_manager import FastTickExitManager
            self.fast_risk_guard = FastRiskGuardEngine()
            self.fast_watchdog = FastWatchdog()
            self.fast_exit_manager = FastTickExitManager()
        except Exception:
            pass

    def run_pre_trade_checklist(
        self,
        candidate: SignalCandidate,
        account: AccountInfo,
        symbol_info: SymbolInfo,
        active_positions: List[Position],
        recent_candles: Optional[List[Candle]] = None
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

        # 4. Is spread acceptable? (Adaptive Spread Filter)
        spread_pts = float(symbol_info.spread)
        pt = float(symbol_info.point) if symbol_info.point > 0 else 0.01
        sl_dist_pts = sl_distance / pt if pt > 0 else sl_distance
        is_scalp_signal = str(getattr(candidate, "id", "")).startswith("SCALP_")

        # Non-scalp (SMC/day-trading) entries use the account profile's spread ceiling
        # (default 5% of SL distance). Scalping uses the dedicated, wider risk setting.
        profile_max_spread = float(getattr(self.portfolio_risk.profile, "max_spread_pct_of_sl", 5.0))
        if is_scalp_signal:
            max_spread_pct = float(getattr(self.config.risk, "max_spread_pct_of_sl_scalping", 20.0))
        else:
            max_spread_pct = profile_max_spread

        # Absolute ceiling in points, independent of SL distance (default 2000).
        abs_ceiling = float(getattr(self.config.risk, "max_spread_points_absolute", 2000.0))
        if spread_pts > abs_ceiling:
            rejections.append(
                f"Pre-check failed: Spread ({spread_pts:.1f} pts) exceeds absolute ceiling "
                f"({abs_ceiling:.1f} pts)."
            )
        elif sl_dist_pts > 0:
            spread_pct_sl = (spread_pts / sl_dist_pts) * 100.0
            if spread_pct_sl > max_spread_pct:
                rejections.append(
                    f"Pre-check failed: Spread ({spread_pts:.1f} pts) is {spread_pct_sl:.2f}% of SL "
                    f"distance ({sl_dist_pts:.1f} pts), exceeding {max_spread_pct:.1f}% limit."
                )

        # 5. Position sizing and volume verification
        size_result = self.sizing.calculate_lot_size(
            account=account,
            symbol_info=symbol_info,
            entry_price=candidate.entry_price,
            stop_loss=candidate.stop_loss,
            risk_percent=self.config.risk.risk_percentage,
            sizing_reference=self.config.risk.sizing_reference,
            adaptive_scalping=is_scalp_signal and (getattr(self.config, "risk_profile", "") == "ADAPTIVE_SCALPING"),
            min_rr_override=float(getattr(self.config.risk, "scalping_min_risk_reward_ratio", 1.2)) if is_scalp_signal else None,
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
        max_dev = max(5.0 * symbol_info.point * 10, sl_distance * 0.35) if is_scalp_signal else (5.0 * symbol_info.point * 10)
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

        # 12. Replay Gate passed
        if self.replay_gate and self.replay_gate.enabled and recent_candles:
            temp_plan = TradePlan(
                plan_id=f"PLAN_{candidate.id}",
                symbol=candidate.symbol,
                direction=candidate.direction,
                entry_price=candidate.entry_price,
                stop_loss=candidate.stop_loss,
                take_profit=candidate.take_profit,
                lots=size_result.volume,
                risk_percent=size_result.risk_percent_actual,
                reward_to_risk=candidate.risk_reward,
                structure_state="BOS_CONFIRMED",
                timestamp=datetime.now(timezone.utc)
            )
            replay_res = self.replay_gate.evaluate_plan(
                plan=temp_plan,
                recent_candles=recent_candles,
                spread_pts=float(symbol_info.spread),
                point=float(symbol_info.point) if symbol_info.point > 0 else 0.00001
            )
            if not replay_res.passed:
                rejections.append(f"Pre-check failed (Replay Gate): {replay_res.rejection_reason}")

        # 13-16. Session-Aware Pre-Trade Checklist Items
        session_enabled = False
        sess_info = None
        if self.session_engine and hasattr(self.session_engine, "_raw_cfg"):
            session_enabled = bool(self.session_engine._raw_cfg.get("enabled", True))

        if session_enabled and self.session_engine and self.affinity_engine and self.session_risk:
            from src.core.contracts import SessionType
            broker_offset_ok = getattr(self.bridge, "broker_utc_offset", 0) != 0 or not self.session_engine.require_verified_offset
            if self.config.execution_mode in (ExecutionMode.BACKTEST, ExecutionMode.SIMULATION):
                broker_offset_ok = True

            sess_dt = candidate.timestamp if candidate.timestamp else datetime.now(timezone.utc)
            sess_info = self.session_engine.get_session_info(
                current_dt=sess_dt,
                broker_offset_verified=broker_offset_ok
            )

            # Checklist Item #13: Session active
            if sess_info.primary_session in (SessionType.OFF_HOURS, SessionType.WEEKEND):
                rejections.append(f"Pre-check failed (Session active): Market session is not active ({sess_info.primary_session.value})")

            # Checklist Item #14: Symbol allowed for session (Affinity check)
            allowed_aff, aff_weight, aff_reason = self.affinity_engine.is_symbol_allowed(
                session=sess_info.primary_session,
                raw_symbol=candidate.symbol,
                dt_utc=sess_dt
            )
            if not allowed_aff:
                rejections.append(f"Pre-check failed (Symbol allowed for session): {aff_reason}")

            # Checklist Item #15: Not in handover/rollover/blackout
            if sess_info.is_rollover_blackout:
                rejections.append("Pre-check failed (Not in handover/rollover/blackout): Daily swap rollover blackout active")
            elif sess_info.is_handover_cooldown:
                rejections.append(f"Pre-check failed (Not in handover/rollover/blackout): {sess_info.rejection_reason}")
            elif not sess_info.can_trade and sess_info.rejection_reason:
                rejections.append(f"Pre-check failed (Not in handover/rollover/blackout): {sess_info.rejection_reason}")

            # Checklist Item #16: Session trade and loss caps OK
            allowed_risk, s_mult, s_reason = self.session_risk.evaluate_session_risk_permission(
                candidate=candidate,
                active_session=sess_info.primary_session,
                account=account,
                active_positions=active_positions
            )
            if not allowed_risk:
                rejections.append(f"Pre-check failed (Session trade and loss caps OK): {s_reason}")

        # 17-19. Fast Mode Speed & Reliability Guardrails (when Fast Mode is enabled or FAST_SCALP setup)
        from src.core.contracts import SetupType
        fast_enabled = bool(self.fast_risk_guard and self.fast_risk_guard._cfg.get("enabled", False))
        if fast_enabled or getattr(candidate, "setup_type", None) == SetupType.FAST_SCALP:
            if self.fast_watchdog:
                wd_ok, wd_reason = self.fast_watchdog.can_open_new_trade(
                    symbol_info=symbol_info,
                    now_utc=candidate.timestamp
                )
                if not wd_ok:
                    rejections.append(f"Pre-check failed (Fast Watchdog): {wd_reason}")
            if self.fast_risk_guard:
                fg_ok, fg_reason = self.fast_risk_guard.validate_trade_candidate(
                    candidate=candidate,
                    account=account,
                    symbol_info=symbol_info,
                    active_positions=active_positions,
                    proposed_volume=size_result.volume,
                    proposed_monetary_risk=size_result.monetary_risk
                )
                if not fg_ok:
                    rejections.append(f"Pre-check failed (Fast Risk Guard): {fg_reason}")

        passed = len(rejections) == 0

        structure_state_str = "BOS_CONFIRMED"
        if hasattr(candidate, "rejection_reasons"):
            for r in candidate.rejection_reasons:
                if "CHoCH" in r or "trapped" in r:
                    structure_state_str = "CHOCH_WARNING"
        if hasattr(candidate, "reasons"):
            for r in candidate.reasons:
                if "BOS" in r:
                    structure_state_str = "BOS_CONFIRMED"

        plan = TradePlan(
            plan_id=f"PLAN_{candidate.id}",
            symbol=candidate.symbol,
            direction=candidate.direction,
            entry_price=candidate.entry_price,
            stop_loss=candidate.stop_loss,
            take_profit=candidate.take_profit,
            lots=size_result.volume if passed else 0.0,
            risk_percent=size_result.risk_percent_actual if passed else 0.0,
            reward_to_risk=candidate.risk_reward,
            structure_state=structure_state_str,
            timestamp=datetime.now(timezone.utc),
            session_name=sess_info.primary_session.value if sess_info else None
        )
        if sess_info:
            candidate.session_name = sess_info.primary_session.value
        candidate.trade_plan = plan
        self._signal_trade_plans[candidate.id] = plan

        # 13. Human Confirmed Gate (LIVE/PROP only when confirm_mode enabled; DEMO auto-executes)
        if passed and self.config.execution_mode in (ExecutionMode.REAL, ExecutionMode.PROP):
            if hasattr(self, "telegram_cmd_handler") and self.telegram_cmd_handler and getattr(self.telegram_cmd_handler, "confirm_mode", False):
                confirmed = self.telegram_cmd_handler.request_trade_confirmation(plan, timeout_sec=120)
                if not confirmed:
                    rejections.append("Pre-check failed (Human Confirm): Trade plan confirmation timed out or cancelled by operator.")
                    passed = False

        if hasattr(self, "shadow_journal") and self.shadow_journal:
            decision_label = "TAKEN" if passed else "REJECTED_CHECKLIST"
            for r in rejections:
                if "Structure" in r:
                    decision_label = "REJECTED_BOS"
                    break
                elif "Replay Gate" in r:
                    decision_label = "REJECTED_REPLAY"
                    break
            self.shadow_journal.record_setup(
                setup_id=candidate.id,
                symbol=candidate.symbol,
                direction=candidate.direction,
                entry_price=candidate.entry_price,
                stop_loss=candidate.stop_loss,
                take_profit=candidate.take_profit,
                reward_to_risk=candidate.risk_reward,
                decision=decision_label,
                rejection_reasons=rejections
            )
        if passed:
            import logging
            logging.getLogger("OrderManager").info(plan.format_log())
        return PreTradeCheckResult(
            passed=passed,
            rejection_reasons=rejections,
            calculated_volume=size_result.volume if passed else 0.0,
            monetary_risk=size_result.monetary_risk if passed else 0.0,
            trade_plan=plan
        )

    def execute_signal(
        self,
        candidate: SignalCandidate,
        account: AccountInfo,
        symbol_info: SymbolInfo,
        active_positions: List[Position],
        recent_candles: Optional[List[Candle]] = None,
        prevalidated: Optional[PreTradeCheckResult] = None,
    ) -> Optional[TradeOrder]:
        """
        Executes pre-trade validation, then submits order to MT5 Bridge
        or simulates order execution in SIMULATION mode.

        When `prevalidated` is supplied the orchestrator's already-executed
        checklist is reused instead of running the full checklist twice.
        """
        check = prevalidated or self.run_pre_trade_checklist(
            candidate, account, symbol_info, active_positions, recent_candles=recent_candles
        )
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
            if check.trade_plan:
                self._active_trade_plans[sim_ticket] = check.trade_plan
            self._executed_signal_ids.add(candidate.id)
            self.portfolio_risk.register_trade_executed()
            candidate.status = "EXECUTED"
            return order

        # Calculate accurate monetary risk percent for MT5 Bridge
        bal = account.balance if account.balance > 0 else 10000.0
        risk_pct = round((check.monetary_risk / bal) * 100.0, 2) if check.monetary_risk > 0 else float(self.config.risk.max_risk_per_trade_percent)

        # Mode: DEMO or REAL Live Execution via MT5 Bridge
        req_id = f"REQ_{candidate.symbol}_{int(datetime.now(timezone.utc).timestamp()*1000)}_{uuid.uuid4().hex[:6].upper()}"
        res = self.bridge.execute_trade(
            symbol=candidate.symbol,
            order_type=candidate.direction,
            volume=check.calculated_volume,
            stop_loss=candidate.stop_loss,
            take_profit=candidate.take_profit,
            price=candidate.entry_price,
            risk_percent=risk_pct,
            magic=self.config.magic_number,
            comment=f"SMC_{candidate.setup_type.value}",
            request_id=req_id
        )

        # Handle IPC Timeout with immediate reconciliation recovery
        res = res if isinstance(res, dict) else {}
        if res.get("status") == "ERROR" and ("timed out" in str(res.get("message", "")).lower() or "timeout" in str(res.get("message", "")).lower()):
            logger.warning(f"[ORDER_MANAGER] Execution timed out awaiting response for {candidate.symbol} ({req_id}). Checking MT5 positions/orders...")
            # Query existing open positions to check if MT5 actually filled the trade
            try:
                open_positions = self.bridge.get_positions()
                for pos in open_positions:
                    if (
                        pos.symbol == candidate.symbol
                        and pos.order_type == candidate.direction
                        and abs(pos.volume - check.calculated_volume) < 1e-4
                        and (pos.magic == self.config.magic_number or "SMC" in pos.comment or "SCALP" in pos.comment)
                    ):
                        logger.info(f"[ORDER_MANAGER] Reconciled timed-out order #{pos.ticket} as successfully FILLED in MT5!")
                        res = {"status": "SUCCESS", "ticket": pos.ticket, "price": pos.open_price, "retcode": 10009}
                        break
            except Exception as e:
                logger.error(f"[ORDER_MANAGER] Error during timed-out order reconciliation: {e}")

        # MT5 fill confirmation: only treat the order as executed when the bridge reports SUCCESS,
        # a positive position/order ticket, and (if supplied) a successful trade retcode.
        res = res if isinstance(res, dict) else {}
        try:
            confirmed_ticket = int(res.get("ticket", 0) or 0)
        except (TypeError, ValueError):
            confirmed_ticket = 0
        retcode = res.get("retcode")
        retcode_ok = retcode is None or int(retcode) in (10008, 10009, 10010)
        if res.get("status") == "SUCCESS" and (confirmed_ticket <= 0 or not retcode_ok):
            candidate.status = "REJECTED"
            err_msg = f"MT5 did not confirm execution (ticket={confirmed_ticket}, retcode={retcode})"
            candidate.rejection_reasons.append(err_msg)
            return TradeOrder(
                symbol=candidate.symbol,
                order_type=candidate.direction,
                volume=check.calculated_volume,
                entry_price=candidate.entry_price,
                stop_loss=candidate.stop_loss,
                take_profit=candidate.take_profit,
                magic=self.config.magic_number,
                status="REJECTED",
                error=err_msg
            )

        if res.get("status") == "SUCCESS":
            ticket = confirmed_ticket
            if check.trade_plan and ticket > 0:
                self._active_trade_plans[ticket] = check.trade_plan
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
            err_msg = str(res.get('message') or "MT5 bridge rejected trade request")
            candidate.rejection_reasons.append(f"MT5 execution error: {err_msg}")
            return TradeOrder(
                symbol=candidate.symbol,
                order_type=candidate.direction,
                volume=check.calculated_volume,
                entry_price=candidate.entry_price,
                stop_loss=candidate.stop_loss,
                take_profit=candidate.take_profit,
                magic=self.config.magic_number,
                status="REJECTED",
                error=err_msg
            )

    def get_active_positions(self) -> List[Position]:
        """Returns active positions depending on operating mode."""
        if self.config.execution_mode in [ExecutionMode.SIMULATION, ExecutionMode.BACKTEST]:
            return list(self.simulated_positions)
        return self.bridge.get_positions()

    def get_trade_plan(self, key: Any) -> Optional[TradePlan]:
        if isinstance(key, int):
            return self._active_trade_plans.get(key)
        return self._signal_trade_plans.get(str(key))

    def apply_plan_amendment(self, ticket: int, amendment: PlanAmendment) -> Optional[TradePlan]:
        import logging
        logger = logging.getLogger("OrderManager")
        current_plan = self._active_trade_plans.get(ticket)
        if not current_plan:
            logger.warning(f"[PLAN_AMENDMENT] No active plan found for ticket #{ticket}")
            return None
        new_plan = current_plan.with_amendment(amendment)
        self._active_trade_plans[ticket] = new_plan
        logger.info(
            f"[PLAN_AMENDMENT] Ticket #{ticket}: {amendment.amendment_type} | "
            f"SL amended from {amendment.old_sl:.5f} to {amendment.new_sl:.5f} | "
            f"Reason: {amendment.reason}"
        )
        return new_plan
