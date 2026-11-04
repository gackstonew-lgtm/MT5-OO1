"""
Master Trading Engine Orchestrator.
Coordinates the end-to-end institutional algorithmic pipeline & continuous M1/M5/M15 scalping engine:
Market Data -> Liquidity/Structure -> MTF -> Continuous Scalping / SMC Strategy -> Risk -> Execution -> Lifecycle Management.
"""

from __future__ import annotations
from datetime import datetime, timezone
import time
from typing import Dict, List, Optional

from src.core.config import AppConfig
from src.core.contracts import (
    AccountInfo,
    Candle,
    DecisionOutcome,
    DecisionOutcomeType,
    ExecutionMode,
    OrderType,
    Position,
    SignalCandidate,
    SymbolInfo,
    Timeframe,
    TradeOrder,
)
from src.core.market_data import MarketDataEngine
from src.core.news_filter import NewsFilterEngine
from src.core.sessions import TradingSessionEngine
from src.core.volatility import VolatilityEngine
from src.execution.bridge_client import BridgeClient
from src.execution.continuous_scalper import ContinuousScalpingEngine, ScalpingLifecycleState
from src.execution.order_manager import OrderManager
from src.learning.champion_challenger import ChampionChallengerEngine, StrategyCandidate
from src.learning.performance_tracker import PerformanceTracker, TradeRecord
from src.monitoring.alerts_telegram import TelegramAlertManager
from src.monitoring.dashboard import DashboardState, TradingDashboard
from src.monitoring.health_monitor import ContinuousHealthMonitor
from src.risk.portfolio_risk import PortfolioRiskEngine
from src.risk.position_sizing import PositionSizingEngine
from src.risk.state_manager import StateManager
from src.risk.trade_management import TradeManagementEngine
from src.smc.mtf_analyzer import MultiTimeframeAnalyzer, MultiTimeframeContext
from src.smc.strategy_engine import SmcStrategyEngine


class TradingBotOrchestrator:
    def __init__(self, config: Optional[AppConfig] = None):
        self.config = config or AppConfig.load_from_dir()

        # 1. Market Data & Environment
        self.market_data = MarketDataEngine(symbols=self.config.symbols)
        self.sessions = TradingSessionEngine(self.config.session)
        self.volatility = VolatilityEngine()
        self.news_filter = NewsFilterEngine()

        # 2. Strategy, MTF & Continuous M1/M5/M15 Scalping Engine
        self.mtf_analyzer = MultiTimeframeAnalyzer()
        self.strategy_engine = SmcStrategyEngine(
            min_score=self.config.strategy.min_confluence_score,
            min_rr=self.config.risk.min_risk_reward_ratio,
            swing_lookback=getattr(self.config.strategy, "swing_lookback_bars", 5),
            require_close_break=getattr(self.config.strategy, "require_close_break", True),
            require_lower_high=getattr(self.config.strategy, "require_lower_high", True),
            choch_timeout_bars=getattr(self.config.strategy, "choch_timeout_bars", 20),
            allow_choch_only_entries=getattr(self.config.strategy, "allow_choch_only_entries", False),
            failed_choch_action=getattr(self.config.strategy, "failed_choch_action", "RESET_AND_LOG_TRAP"),
        )
        self.continuous_scalper = ContinuousScalpingEngine(
            symbols=self.config.symbols,
            min_confluence_score=float(getattr(self.config.strategy, "scalping_min_confluence_score", 55.0)),
            min_rr=float(getattr(self.config.risk, "scalping_min_risk_reward_ratio", 1.5)),
            require_htf_agreement=bool(getattr(self.config.strategy, "continuous_scalp_require_htf_agreement", False)),
            auto_rescan_after_closure=bool(getattr(self.config, "auto_rescan_after_closure", True)),
        )

        # 3. Risk & Management
        self.sizing_engine = PositionSizingEngine(self.config.risk.risk_percentage)
        self.portfolio_risk = PortfolioRiskEngine(self.config.risk)
        self.trade_manager = TradeManagementEngine(self.config.risk)

        # 4. Bridge & Execution
        self.bridge = BridgeClient(
            host=self.config.bridge_host,
            port=self.config.bridge_port,
        )
        self.order_manager = OrderManager(
            config=self.config,
            bridge=self.bridge,
            sizing_engine=self.sizing_engine,
            portfolio_risk=self.portfolio_risk,
        )

        # Hook 1: Broker Adapter Delegation (Strangler Pattern)
        if getattr(self.config, "upgrade_enabled", False) and getattr(self.config, "broker_adapter_enabled", False):
            from src.adapters.broker_factory import BrokerFactory

            self.broker = BrokerFactory.create_broker(self.config, self.bridge)
        else:
            self.broker = None

        # Hook 3: Modular Strategy Pipeline (Strangler Pattern)
        if getattr(self.config, "upgrade_enabled", False) and getattr(self.config, "modular_strategy_enabled", False):
            from src.strategies.legacy_adapter import create_legacy_smc_pipeline

            self.strategy_pipeline = create_legacy_smc_pipeline(
                smc_engine=self.strategy_engine,
                sizing_engine=self.sizing_engine,
                require_scalp_htf_agreement=getattr(self.config.strategy, "scalp_require_htf_agreement", True),
            )
        else:
            self.strategy_pipeline = None

        # 5. Learning & Tracking
        self.performance_tracker = PerformanceTracker()
        champion_cand = StrategyCandidate(
            id="SMC_CHAMPION_V1",
            name="Institutional SMC Multi-Timeframe",
            version="1.0.0",
            parameters={"min_score": 75.0, "min_rr": 2.0},
            is_champion=True,
        )
        self.learning_engine = ChampionChallengerEngine(champion=champion_cand)

        # 6. Monitoring, Health & Alerts
        self.dashboard = TradingDashboard(execution_mode=self.config.execution_mode.value)
        self.alerts = TelegramAlertManager(
            bot_token=self.config.telegram_bot_token,
            chat_id=self.config.telegram_chat_id,
        )
        self.health_monitor = ContinuousHealthMonitor(symbols=self.config.symbols)

        # State storage
        self.active_contexts: Dict[str, MultiTimeframeContext] = {}
        self.recent_signals: List[SignalCandidate] = []
        self.account_info = AccountInfo()
        self.last_decision_outcomes: Dict[str, DecisionOutcome] = {}
        self.auto_sync_market_data: bool = False
        self.enforce_stale_data_check: bool = False
        self.max_candle_staleness_seconds: float = 7200.0
        self._executed_signal_keys: set = set()
        self._active_positions_cache: Dict[int, Position] = {}

        # 7. Atomic State Persistence & Safe Startup Load
        # Simulation/backtest runs must be deterministic and must not inherit live
        # risk state (daily counters, peak equity, circuit breakers) from disk.
        self.state_manager = StateManager("data/bot_state.json")
        if self.config.execution_mode in [ExecutionMode.DEMO, ExecutionMode.REAL]:
            saved_state = self.state_manager.load_state()
            if saved_state:
                self.portfolio_risk.load_from_dict(saved_state)

        # Reset duplicate alert suppression on startup
        if hasattr(self, "alerts") and self.alerts and hasattr(self.alerts, "notifier"):
            self.alerts.notifier.reset_suppression_counter()

    def initialize_market_data(self, max_retries: int = 3) -> Dict[str, Dict[str, object]]:
        """
        Synchronizes full historical candles across all configured symbols and timeframes
        (including M1, M5, M15, H1, H4, D1, W1) before live scanning begins,
        and enables incremental auto-sync on subsequent cycles.
        """
        self.auto_sync_market_data = True
        health = self.market_data.initialize_and_warmup(
            bridge=self.bridge if self.bridge.is_connected() else None,
            max_retries=max_retries,
        )
        for sym, tf_map in health.items():
            m1_info = tf_map.get("M1", {})
            m5_info = tf_map.get("M5", {})
            m15_info = tf_map.get("M15", {})
            h1_info = tf_map.get("H1", {})
            h4_info = tf_map.get("H4", {})
            d1_info = tf_map.get("D1", {})
            w1_info = tf_map.get("W1", {})
            print(
                f"[MarketData] Warmup {sym} (broker={m15_info.get('broker_symbol', sym)}): "
                f"W1={w1_info.get('received_count', 0)}, D1={d1_info.get('received_count', 0)}, "
                f"H4={h4_info.get('received_count', 0)}, H1={h1_info.get('received_count', 0)}, "
                f"M15={m15_info.get('received_count', 0)}, M5={m5_info.get('received_count', 0)}, "
                f"M1={m1_info.get('received_count', 0)} | Healthy={m15_info.get('is_healthy', False)}"
            )
        return health

    @staticmethod
    def classify_outcome_category(outcome: DecisionOutcome) -> str:
        """
        Maps a DecisionOutcome to one of the explicit diagnostic categories:
        - TRADE_EXECUTED
        - MARKET_CLOSED
        - INSUFFICIENT_DATA
        - STALE_DATA
        - SPREAD_TOO_HIGH
        - RISK_RESTRICTION
        - CONNECTION_FAILURE
        - ORDER_EXECUTION_FAILURE
        - DUPLICATE_SIGNAL_BLOCKED
        - NO_VALID_SETUP
        """
        if outcome.outcome_type == DecisionOutcomeType.TRADE_SENT:
            return "TRADE_EXECUTED"
        code = (outcome.reason_code or "").upper()
        details_lower = (outcome.details or "").lower()
        if code == "STALE_DATA" or "stale" in details_lower:
            return "STALE_DATA"
        if code == "DUPLICATE_SIGNAL" or "duplicate signal" in details_lower:
            return "DUPLICATE_SIGNAL_BLOCKED"
        if "insufficient" in details_lower and "candle" in details_lower:
            return "INSUFFICIENT_DATA"
        if code == "SPREAD_TOO_HIGH" or "spread" in details_lower:
            return "SPREAD_TOO_HIGH"
        if code in ("MARKET_CLOSED", "SESSION_FILTER") or "weekend" in details_lower or "market closed" in details_lower:
            return "MARKET_CLOSED"
        if code == "CONNECTION_FAILURE" or "bridge disconnected" in details_lower or "connection" in details_lower:
            return "CONNECTION_FAILURE"
        if code == "EXECUTION_FAILED":
            return "ORDER_EXECUTION_FAILURE"
        if outcome.outcome_type == DecisionOutcomeType.TRADE_SKIPPED:
            return "RISK_RESTRICTION"
        return "NO_VALID_SETUP"

    def update_account(self, account: AccountInfo) -> None:
        self.account_info = account
        self.portfolio_risk.update_equity(account)
        if hasattr(self, "state_manager"):
            self.state_manager.save_state(self.portfolio_risk.to_dict())

    def reset_state(self) -> tuple[bool, str]:
        try:
            positions = self.bridge.get_positions() if self.bridge.is_connected() else []
            if positions and len(positions) > 0:
                return False, f"❌ <b>RESET REFUSED</b>: {len(positions)} active position(s) are open. Close all positions first."
        except Exception:
            pass

        if self.bridge.is_connected():
            try:
                self.bridge.send_request({"action": "RESET_STATE"})
                acc = self.bridge.get_account()
                if acc:
                    self.account_info = acc
            except Exception:
                pass

        cur_bal = self.account_info.balance
        self.portfolio_risk.reset_state(cur_bal)
        if hasattr(self, "state_manager"):
            self.state_manager.save_state(self.portfolio_risk.to_dict())
        if hasattr(self, "alerts") and self.alerts and hasattr(self.alerts, "notifier"):
            self.alerts.notifier.reset_suppression_counter()

        return True, f"✅ <b>STATE RESET COMPLETE</b>: Baseline balance, day-start balance, and peak equity reset to ${cur_bal:.2f}. Challenge progress: 0.0%. Daily counters cleared."

    def _record_and_dispatch_outcome(self, outcome: DecisionOutcome) -> None:
        self.last_decision_outcomes[outcome.symbol] = outcome
        line = outcome.format_line()
        print(f"[{outcome.evaluated_at.strftime('%Y-%m-%d %H:%M:%S UTC')}] [{outcome.symbol}] {line}")
        if hasattr(self, "health_monitor") and self.health_monitor:
            if outcome.outcome_type == DecisionOutcomeType.TRADE_SENT:
                self.health_monitor.record_trade_execution(outcome.symbol, True, outcome.details)
            else:
                self.health_monitor.record_scan(
                    outcome.symbol,
                    diagnostic_reason=outcome.details,
                    details=outcome.details,
                )
        if hasattr(self, "alerts") and self.alerts:
            self.alerts.send_decision_outcome(outcome)

    def _execute_approved_candidate(
        self,
        symbol: str,
        candidate: SignalCandidate,
        bar_time: datetime,
    ) -> Optional[SignalCandidate]:
        """
        Runs pre-trade risk validation and order execution for an approved SignalCandidate,
        updating lifecycle state, idempotency keys, health monitor, and Telegram notifications.
        """
        sig_key = f"{symbol}_{candidate.direction.value}_{bar_time.isoformat()}"
        if sig_key in self._executed_signal_keys or self.continuous_scalper.is_duplicate_signal(sig_key):
            outcome = DecisionOutcome(
                symbol=symbol,
                bar_time=bar_time,
                outcome_type=DecisionOutcomeType.TRADE_SKIPPED,
                reason_code="DUPLICATE_SIGNAL",
                details=f"Duplicate signal blocked for {symbol} {candidate.direction.value} on bar {bar_time.isoformat()}",
                real_numbers={"signal_id": candidate.id},
            )
            self._record_and_dispatch_outcome(outcome)
            return None

        self.continuous_scalper.transition_state(symbol, ScalpingLifecycleState.RISK_VALIDATION)
        symbol_info = self.market_data.get_symbol_info(symbol)
        active_positions = self.order_manager.get_active_positions()
        check = self.order_manager.run_pre_trade_checklist(candidate, self.account_info, symbol_info, active_positions)

        bal = self.account_info.balance if self.account_info.balance > 0 else 10000.0
        risk_pct = (check.monetary_risk / bal * 100.0) if bal > 0 else 0.5

        if not check.passed:
            rej_summary = "; ".join(check.rejection_reasons)
            self.continuous_scalper.on_position_closed_or_rejected(symbol, was_closed=False)
            outcome = DecisionOutcome(
                symbol=symbol,
                bar_time=bar_time,
                outcome_type=DecisionOutcomeType.TRADE_SKIPPED,
                reason_code="RISK_CHECK_FAILED",
                details=rej_summary,
                real_numbers={"reasons": check.rejection_reasons, "balance": bal},
            )
            self._record_and_dispatch_outcome(outcome)
            if hasattr(self, "alerts") and self.alerts:
                self.alerts.alert_order_rejected(symbol=symbol, action=candidate.direction.value, reason=rej_summary)
            return None

        self.continuous_scalper.transition_state(symbol, ScalpingLifecycleState.ORDER_EXECUTION)
        if hasattr(self, "alerts") and self.alerts:
            self.alerts.alert_signal_approved(
                symbol=symbol,
                direction=candidate.direction.value,
                score=candidate.score,
                entry=candidate.entry_price,
                sl=candidate.stop_loss,
                tp=candidate.take_profit,
                rr=candidate.risk_reward,
                lots=check.calculated_volume,
                risk_pct=risk_pct,
                reason=(candidate.reasons[0] if candidate.reasons else f"Score {candidate.score:.1f}"),
            )

        # Dry run support
        if getattr(self.config, "dry_run", False):
            self._executed_signal_keys.add(sig_key)
            self.continuous_scalper.mark_signal_executed(
                sig_key,
                symbol=candidate.symbol,
                price=order.entry_price,
                direction=candidate.direction
            )
            self.continuous_scalper.mark_signal_executed(
                candidate.id,
                symbol=candidate.symbol,
                price=order.entry_price,
                direction=candidate.direction
            )
            self.continuous_scalper.transition_state(symbol, ScalpingLifecycleState.POSITION_MANAGEMENT)
            outcome = DecisionOutcome(
                symbol=symbol,
                bar_time=bar_time,
                outcome_type=DecisionOutcomeType.TRADE_SENT,
                details="Dry-run order simulated",
                direction=candidate.direction.value,
                volume=check.calculated_volume,
                entry_price=candidate.entry_price,
                stop_loss=candidate.stop_loss,
                take_profit=candidate.take_profit,
                risk_dollars=check.monetary_risk,
                risk_percent=risk_pct,
                ticket=999999,
            )
            self._record_and_dispatch_outcome(outcome)
            return candidate

        # Telegram submission notification
        if hasattr(self, "alerts") and self.alerts and hasattr(self.alerts, "alert_order_submitted"):
            self.alerts.alert_order_submitted(
                symbol=symbol,
                direction=candidate.direction.value,
                volume=check.calculated_volume,
                price=candidate.entry_price,
                sl=candidate.stop_loss,
                tp=candidate.take_profit,
                request_id=f"ORD_{candidate.id[-10:]}",
            )

        # Execute order via MT5 / Bridge
        order = self.order_manager.execute_signal(
            candidate=candidate,
            account=self.account_info,
            symbol_info=symbol_info,
            active_positions=active_positions,
        )

        if order and order.status == "FILLED":
            self._executed_signal_keys.add(sig_key)
            self.continuous_scalper.mark_signal_executed(
                sig_key,
                symbol=candidate.symbol,
                price=order.entry_price,
                direction=candidate.direction
            )
            self.continuous_scalper.mark_signal_executed(
                candidate.id,
                symbol=candidate.symbol,
                price=order.entry_price,
                direction=candidate.direction
            )
            self.continuous_scalper.transition_state(symbol, ScalpingLifecycleState.POSITION_MANAGEMENT)
            ticket = order.ticket or 0
            outcome = DecisionOutcome(
                symbol=symbol,
                bar_time=bar_time,
                outcome_type=DecisionOutcomeType.TRADE_SENT,
                details="Order successfully filled",
                direction=candidate.direction.value,
                volume=order.volume,
                entry_price=order.entry_price,
                stop_loss=order.stop_loss,
                take_profit=order.take_profit,
                risk_dollars=check.monetary_risk,
                risk_percent=risk_pct,
                ticket=ticket,
            )
            self._record_and_dispatch_outcome(outcome)
            if hasattr(self, "alerts") and self.alerts:
                self.alerts.alert_trade_executed(
                    ticket=ticket,
                    symbol=order.symbol,
                    direction=order.order_type.value,
                    volume=order.volume,
                    price=order.entry_price,
                    sl=order.stop_loss,
                    tp=order.take_profit,
                )
            return candidate
        else:
            err = getattr(order, "error", None) or "Execution returned null or unfilled"
            self.continuous_scalper.on_position_closed_or_rejected(symbol, was_closed=False)
            outcome = DecisionOutcome(
                symbol=symbol,
                bar_time=bar_time,
                outcome_type=DecisionOutcomeType.TRADE_SKIPPED,
                reason_code="EXECUTION_FAILED",
                details=f"Broker/bridge refused order: {err}",
            )
            self._record_and_dispatch_outcome(outcome)
            if hasattr(self, "alerts") and self.alerts:
                self.alerts.alert_order_rejected(symbol=symbol, action=candidate.direction.value, reason=err)
            return None

    def run_cycle_for_symbol(self, symbol: str) -> Optional[SignalCandidate]:
        """
        Executes a complete scanning cycle for a single instrument:
        1. Retrieves candles across W1, D1, H4, H1, M15 (no news-based blocking)
        2. Computes MTF bias and liquidity levels
        3. Verifies SCALP higher-timeframe agreement (if enabled on StrategyConfig)
        4. Scans for SMC setups
        5. Validates risk & executes if approved
        Every evaluation ends with exactly one outcome line (TRADE SENT, TRADE SKIPPED, NO SETUP).
        """
        now = datetime.now(timezone.utc)
        if self.auto_sync_market_data:
            self.market_data.sync_symbol_history(
                symbol=symbol,
                bridge=self.bridge if self.bridge.is_connected() else None,
                max_retries=2,
                force_full=False,
            )
        m15 = self.market_data.get_candles(symbol, Timeframe.M15)
        bar_time = m15[-1].time if (m15 and len(m15) > 0) else now

        # 1. Retrieve candles & check history sufficiency
        if not m15 or len(m15) < 20:
            outcome = DecisionOutcome(
                symbol=symbol,
                bar_time=bar_time,
                outcome_type=DecisionOutcomeType.NO_SETUP,
                reason_code="INSUFFICIENT_DATA",
                details=f"Insufficient M15 candle history (have {len(m15) if m15 else 0}, required 20)",
            )
            self._record_and_dispatch_outcome(outcome)
            return None

        # 1b. Optional Stale-Data Guard
        if self.enforce_stale_data_check:
            age_seconds = max(0.0, (now - bar_time).total_seconds())
            if age_seconds > self.max_candle_staleness_seconds:
                outcome = DecisionOutcome(
                    symbol=symbol,
                    bar_time=bar_time,
                    outcome_type=DecisionOutcomeType.TRADE_SKIPPED,
                    reason_code="STALE_DATA",
                    details=(
                        f"Stale M15 market data for {symbol}: latest closed bar is {age_seconds:.0f}s old "
                        f"(max allowed {self.max_candle_staleness_seconds:.0f}s)"
                    ),
                    real_numbers={"age_seconds": age_seconds, "max_allowed_seconds": self.max_candle_staleness_seconds},
                )
                self._record_and_dispatch_outcome(outcome)
                return None

        # 2. Weekend holding restriction check
        profile = getattr(self.portfolio_risk, "profile", None)
        if profile and getattr(profile.rules, "no_weekend_holding", False):
            if now.weekday() == 4 and now.hour >= profile.rules.weekend_close_hour_friday:
                outcome = DecisionOutcome(
                    symbol=symbol,
                    bar_time=bar_time,
                    outcome_type=DecisionOutcomeType.TRADE_SKIPPED,
                    reason_code="SESSION_FILTER",
                    details=f"Weekend holding prohibited after Friday {profile.rules.weekend_close_hour_friday:02d}:00 (current hour {now.hour:02d})",
                )
                self._record_and_dispatch_outcome(outcome)
                return None

        # 3. Multi-timeframe context
        w1 = self.market_data.get_candles(symbol, Timeframe.W1)
        d1 = self.market_data.get_candles(symbol, Timeframe.D1)
        h4 = self.market_data.get_candles(symbol, Timeframe.H4)
        h1 = self.market_data.get_candles(symbol, Timeframe.H1)
        mtf_ctx = self.mtf_analyzer.analyze(symbol, w1, d1, h4, h1, m15)
        self.active_contexts[symbol] = mtf_ctx

        # Hook 2: Feature Pipeline Extraction (Strangler Pattern)
        if getattr(self.config, "upgrade_enabled", False) and getattr(self.config, "feature_pipeline_enabled", False):
            from src.features.feature_registry import FeaturePipeline

            sym_info = self.market_data.get_symbol_info(symbol)
            self._current_feature_snapshot = FeaturePipeline.extract_snapshot(
                symbol=symbol,
                m15_candles=m15,
                h1_candles=h1,
                h4_candles=h4,
                d1_candles=d1,
                point=sym_info.point if sym_info else 0.00001,
            )

        # 4. SCALP higher-timeframe agreement check
        if getattr(self.config.strategy, "scalp_require_htf_agreement", True):
            agree, msg, dirs = self.strategy_engine.check_scalp_htf_agreement(mtf_ctx)
            if not agree:
                outcome = DecisionOutcome(
                    symbol=symbol,
                    bar_time=bar_time,
                    outcome_type=DecisionOutcomeType.NO_SETUP,
                    details=msg,
                    real_numbers=dirs,
                )
                self._record_and_dispatch_outcome(outcome)
                return None

        # 5. Session & Killzone check & SMC Strategy Scan
        is_kz, _ = self.sessions.evaluate_killzone(now)
        candidates = self.strategy_engine.scan_for_opportunities(mtf_ctx, m15, is_in_killzone=is_kz)
        if not candidates:
            outcome = DecisionOutcome(
                symbol=symbol,
                bar_time=bar_time,
                outcome_type=DecisionOutcomeType.NO_SETUP,
                details="No valid SMC liquidity sweep, MSS, or FVG/OB pattern found on closed bar",
            )
            self._record_and_dispatch_outcome(outcome)
            return None

        # Filter for top candidate
        candidates.sort(key=lambda c: c.score, reverse=True)
        best = candidates[0]
        self.recent_signals.append(best)

        # 6. Candidate approval status
        if best.status != "APPROVED":
            reasons_str = (
                "; ".join(best.rejection_reasons)
                if best.rejection_reasons
                else f"Score {best.score:.1f} < {self.config.strategy.min_confluence_score:.1f}"
            )
            outcome = DecisionOutcome(
                symbol=symbol,
                bar_time=bar_time,
                outcome_type=DecisionOutcomeType.NO_SETUP,
                details=f"Candidate {best.setup_type.value} rejected: {reasons_str}",
            )
            self._record_and_dispatch_outcome(outcome)
            # Rejected candidates are returned for observability (callers must check
            # status), matching the canonical decision-replay contract.
            return best

        return self._execute_approved_candidate(symbol=symbol, candidate=best, bar_time=bar_time)

    def run_scalping_cycle_for_symbol(self, symbol: str) -> Optional[SignalCandidate]:
        """
        Executes a continuous M1/M5/M15 scalping cycle for `symbol`:
        1. Syncs latest M1, M5, M15, H1 candles & live tick
        2. Evaluates M1/M5/M15 Momentum Continuation, Pullback, and Liquidity-Sweep/Breakout scalps
        3. Falls back to M15 SMC opportunities if no lower-timeframe scalp candidate triggered
        4. Validates risk & executes if qualified, then immediately transitions lifecycle state
        """
        now = datetime.now(timezone.utc)
        if self.auto_sync_market_data:
            self.market_data.sync_symbol_history(
                symbol=symbol,
                bridge=self.bridge if self.bridge.is_connected() else None,
                max_retries=2,
                force_full=False,
            )

        m1 = self.market_data.get_candles(symbol, Timeframe.M1)
        m5 = self.market_data.get_candles(symbol, Timeframe.M5)
        m15 = self.market_data.get_candles(symbol, Timeframe.M15)
        h1 = self.market_data.get_candles(symbol, Timeframe.H1)

        latest_bars = m1 or m5 or m15
        bar_time = latest_bars[-1].time if latest_bars else now

        if (not m1 or len(m1) < 12) and (not m5 or len(m5) < 12) and (not m15 or len(m15) < 12):
            outcome = DecisionOutcome(
                symbol=symbol,
                bar_time=bar_time,
                outcome_type=DecisionOutcomeType.NO_SETUP,
                reason_code="INSUFFICIENT_DATA",
                details=f"Market data unavailable: insufficient M1/M5/M15 candle history for {symbol}",
            )
            self._record_and_dispatch_outcome(outcome)
            return None

        sym_info = self.market_data.get_symbol_info(symbol)
        candles_by_tf = {
            Timeframe.M1: m1,
            Timeframe.M5: m5,
            Timeframe.M15: m15,
            Timeframe.H1: h1,
        }

        best = self.continuous_scalper.scan_symbol_timeframes(
            symbol=symbol,
            candles_by_tf=candles_by_tf,
            symbol_info=sym_info,
            execution_timeframes=[Timeframe.M1, Timeframe.M5, Timeframe.M15],
        )

        if best is None:
            outcome = DecisionOutcome(
                symbol=symbol,
                bar_time=bar_time,
                outcome_type=DecisionOutcomeType.NO_SETUP,
                details="No qualifying signal on M1/M5/M15 continuous scalping scan",
            )
            self._record_and_dispatch_outcome(outcome)
            return None

        self.recent_signals.append(best)
        if hasattr(self, "health_monitor") and self.health_monitor:
            self.health_monitor.record_signal(
                symbol=symbol,
                direction=best.direction.value,
                setup_type=best.setup_type.value,
                score=best.score,
            )

        return self._execute_approved_candidate(symbol=symbol, candidate=best, bar_time=best.timestamp)

    def run_continuous_scalping_tick_cycle(self, concurrent: bool = False) -> Dict[str, Optional[SignalCandidate]]:
        """
        Executes one complete continuous scalping pass across all enabled symbols (in fair round-robin
        or concurrent order) and immediately manages all open positions.
        """
        results: Dict[str, Optional[SignalCandidate]] = {}
        if concurrent and getattr(self.config, "multi_symbol_execution", True):
            raw = self.continuous_scalper.evaluate_symbols_concurrently(self.run_scalping_cycle_for_symbol)
            for sym, val in raw.items():
                results[sym] = val if isinstance(val, SignalCandidate) else None
        else:
            for sym in self.continuous_scalper.get_ordered_symbols_for_cycle():
                results[sym] = self.run_scalping_cycle_for_symbol(sym)

        self.manage_active_positions()
        return results

    def manage_active_positions(self) -> List[object]:
        """Evaluates lifecycle actions ($5+ Profit Target/Extension, Break-Even, Partial TP, Trailing SL, Full Close)."""
        executed_actions = []
        active_positions = list(self.order_manager.get_active_positions())
        scalp_mode = getattr(self.config, "trading_mode", "CONTINUOUS_SCALPING") == "CONTINUOUS_SCALPING"

        # Detect positions closed since last cycle (e.g. SL or TP hit at broker)
        current_tickets = {p.ticket for p in active_positions}
        closed_tickets = [t for t in self._active_positions_cache if t not in current_tickets]
        for t in closed_tickets:
            closed_pos = self._active_positions_cache.pop(t, None)
            if closed_pos:
                realized_pnl = float(closed_pos.profit)
                try:
                    import MetaTrader5 as mt5
                    deals = mt5.history_deals_get(position=t)
                    if deals:
                        out_deals = [d for d in deals if getattr(d, "entry", 0) == 1]
                        if out_deals:
                            realized_pnl = sum(float(d.profit) + float(d.commission) + float(d.swap) for d in out_deals)
                except Exception:
                    pass
                self.portfolio_risk.record_trade_result(
                    profit=realized_pnl,
                    volume=closed_pos.volume,
                    symbol=closed_pos.symbol
                )
                self.trade_manager.forget_position(closed_pos.ticket)
                self.continuous_scalper.on_position_closed_or_rejected(closed_pos.symbol, was_closed=True)
                if hasattr(self, "alerts") and self.alerts:
                    self.alerts.alert_trade_closed(
                        symbol=closed_pos.symbol,
                        ticket=closed_pos.ticket,
                        direction=closed_pos.order_type.value,
                        close_reason="SL/TP hit or external close",
                        money_pnl=realized_pnl,
                    )

        # Update cache with currently active positions
        for pos in active_positions:
            self._active_positions_cache[pos.ticket] = pos

        for pos in active_positions:
            self.continuous_scalper.transition_state(pos.symbol, ScalpingLifecycleState.POSITION_MANAGEMENT)
            sym_info = self.market_data.get_symbol_info(pos.symbol)
            m5 = self.market_data.get_candles(pos.symbol, Timeframe.M5)
            m15 = self.market_data.get_candles(pos.symbol, Timeframe.M15)
            recent_bars = m15 if len(m15) >= 5 else m5
            actions = self.trade_manager.evaluate_position(pos, sym_info, recent_bars, scalp_mode=scalp_mode)

            for act in actions:
                executed_actions.append(act)
                if act.amendment is not None:
                    self.order_manager.apply_plan_amendment(pos.ticket, act.amendment)

                if act.action_type == "FULL_CLOSE":
                    net_pnl = self.trade_manager.calculate_net_profit(pos, sym_info)
                    self.portfolio_risk.record_trade_result(
                        profit=net_pnl,
                        volume=pos.volume,
                        symbol=pos.symbol
                    )
                    if self.config.execution_mode in [ExecutionMode.DEMO, ExecutionMode.REAL] and not getattr(self.config, "dry_run", False):
                        self.bridge.close_order(ticket=act.ticket)
                    else:
                        self.order_manager.simulated_positions = [
                            p for p in self.order_manager.simulated_positions if p.ticket != act.ticket
                        ]
                    self._active_positions_cache.pop(pos.ticket, None)
                    self.trade_manager.forget_position(pos.ticket)
                    self.continuous_scalper.on_position_closed_or_rejected(pos.symbol, was_closed=True)
                    if hasattr(self, "alerts") and self.alerts:
                        self.alerts.alert_trade_closed(
                            symbol=pos.symbol,
                            ticket=pos.ticket,
                            direction=pos.order_type.value,
                            close_reason=act.reason,
                            money_pnl=net_pnl,
                        )
                    break

                elif act.action_type == "MODIFY_SL" and act.new_sl is not None:
                    pos.stop_loss = act.new_sl
                    self.continuous_scalper.transition_state(pos.symbol, ScalpingLifecycleState.PROFIT_LOCKING)
                    if "Break-even" in act.reason:
                        pos.break_even_activated = True
                        if hasattr(self, "alerts") and self.alerts:
                            self.alerts.alert_break_even(pos.symbol, pos.ticket, act.new_sl)
                    else:
                        if hasattr(self, "alerts") and self.alerts:
                            self.alerts.alert_trailing_stop(
                                pos.symbol,
                                pos.ticket,
                                act.new_sl,
                                pos.current_price,
                            )
                    if self.config.execution_mode in [ExecutionMode.DEMO, ExecutionMode.REAL] and not getattr(self.config, "dry_run", False):
                        self.bridge.modify_order(ticket=act.ticket, sl=act.new_sl)

                elif act.action_type == "PARTIAL_CLOSE" and act.close_volume is not None:
                    pos.volume = round(max(0.0, pos.volume - act.close_volume), 2)
                    pos.partial_taken = True
                    self.continuous_scalper.transition_state(pos.symbol, ScalpingLifecycleState.PROFIT_LOCKING)
                    if self.config.execution_mode in [ExecutionMode.DEMO, ExecutionMode.REAL] and not getattr(self.config, "dry_run", False):
                        self.bridge.close_order(ticket=act.ticket, volume=act.close_volume)
                    if hasattr(self, "alerts") and self.alerts:
                        self.alerts.alert_partial_close(
                            symbol=pos.symbol,
                            ticket=pos.ticket,
                            closed_volume=act.close_volume,
                            remaining_volume=pos.volume,
                            pnl=pos.profit * 0.5,
                        )

        if hasattr(self, "health_monitor") and self.health_monitor:
            self.health_monitor.record_position_management(len(active_positions), len(executed_actions))
        return executed_actions

    def manage_open_positions(self) -> List[object]:
        """Alias for manage_active_positions() used by continuous runtime loops."""
        return self.manage_active_positions()

    def get_dashboard_state(self) -> DashboardState:
        """Returns the current state snapshot for monitoring."""
        perf = self.performance_tracker.calculate_summary()
        active_positions = self.order_manager.get_active_positions()
        return self.dashboard.build_snapshot(
            account=self.account_info,
            active_positions=active_positions,
            signals=self.recent_signals,
            market_contexts=self.active_contexts,
            perf=perf,
            portfolio_risk_engine=self.portfolio_risk,
        )
