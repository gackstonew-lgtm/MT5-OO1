"""
Master Trading Engine Orchestrator.
Coordinates the end-to-end institutional algorithmic pipeline:
Market Data -> Liquidity/Structure -> MTF -> Strategy -> Risk -> Execution -> Lifecycle Management.
"""

from __future__ import annotations
import time
from typing import Dict, List, Optional
from datetime import datetime, timezone

from src.core.contracts import (
    Candle, Timeframe, SignalCandidate, TradeOrder, Position,
    AccountInfo, SymbolInfo, ExecutionMode, OrderType, DecisionOutcome, DecisionOutcomeType
)
from src.core.config import AppConfig
from src.core.market_data import MarketDataEngine
from src.core.sessions import TradingSessionEngine
from src.core.volatility import VolatilityEngine
from src.core.news_filter import NewsFilterEngine
from src.smc.mtf_analyzer import MultiTimeframeAnalyzer, MultiTimeframeContext
from src.smc.strategy_engine import SmcStrategyEngine
from src.risk.position_sizing import PositionSizingEngine
from src.risk.portfolio_risk import PortfolioRiskEngine
from src.risk.trade_management import TradeManagementEngine
from src.risk.state_manager import StateManager
from src.execution.bridge_client import BridgeClient
from src.execution.order_manager import OrderManager
from src.learning.performance_tracker import PerformanceTracker, TradeRecord
from src.learning.champion_challenger import ChampionChallengerEngine, StrategyCandidate
from src.monitoring.dashboard import TradingDashboard, DashboardState
from src.monitoring.alerts_telegram import TelegramAlertManager
from src.execution.active_mode import ActiveTradingEngine, ActiveTradingConfig, ProhibitionViolationError
from concurrent.futures import ThreadPoolExecutor


class TradingBotOrchestrator:
    def __init__(self, config: Optional[AppConfig] = None):
        self.config = config or AppConfig.load_from_dir()

        # 1. Market Data & Environment
        self.market_data = MarketDataEngine(symbols=self.config.symbols)
        self.sessions = TradingSessionEngine(self.config.session)
        self.volatility = VolatilityEngine()
        self.news_filter = NewsFilterEngine()

        # 2. Strategy & MTF
        self.mtf_analyzer = MultiTimeframeAnalyzer()
        self.strategy_engine = SmcStrategyEngine(
            min_score=self.config.strategy.min_confluence_score,
            min_rr=self.config.risk.min_risk_reward_ratio
        )

        # 3. Risk & Management
        self.sizing_engine = PositionSizingEngine(self.config.risk.risk_percentage)
        self.portfolio_risk = PortfolioRiskEngine(self.config.risk)
        self.trade_manager = TradeManagementEngine(self.config.risk)

        # 4. Bridge & Execution
        self.bridge = BridgeClient(
            host=self.config.bridge_host,
            port=self.config.bridge_port
        )
        self.order_manager = OrderManager(
            config=self.config,
            bridge=self.bridge,
            sizing_engine=self.sizing_engine,
            portfolio_risk=self.portfolio_risk
        )

        # 5. Learning & Tracking
        self.performance_tracker = PerformanceTracker()
        champion_cand = StrategyCandidate(
            id="SMC_CHAMPION_V1",
            name="Institutional SMC Multi-Timeframe",
            version="1.0.0",
            parameters={"min_score": 75.0, "min_rr": 2.0},
            is_champion=True
        )
        self.learning_engine = ChampionChallengerEngine(champion=champion_cand)

        # 6. Monitoring & Alerts
        self.dashboard = TradingDashboard(execution_mode=self.config.execution_mode.value)
        self.active_engine = ActiveTradingEngine()
        self.alerts = TelegramAlertManager(
            bot_token=self.config.telegram_bot_token,
            chat_id=self.config.telegram_chat_id
        )

        # State storage
        self.active_contexts: Dict[str, MultiTimeframeContext] = {}
        self.recent_signals: List[SignalCandidate] = []
        self.account_info = AccountInfo()
        self.last_decision_outcomes: Dict[str, DecisionOutcome] = {}

        # 7. Atomic State Persistence & Safe Startup Load
        self.state_manager = StateManager("data/bot_state.json")
        saved_state = self.state_manager.load_state()
        if saved_state:
            self.portfolio_risk.load_from_dict(saved_state)

        # Reset duplicate alert suppression on startup
        if hasattr(self, "alerts") and self.alerts and hasattr(self.alerts, "notifier"):
            self.alerts.notifier.reset_suppression_counter()

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
        if hasattr(self, "alerts") and self.alerts:
            self.alerts.send_decision_outcome(outcome)

    def run_cycle_for_symbol(self, symbol: str) -> Optional[SignalCandidate]:
        """
        Executes a complete scanning cycle for a single instrument:
        1. Checks news filter
        2. Retrieves candles across W1, D1, H4, H1, M15
        3. Computes MTF bias and liquidity levels
        4. Verifies SCALP higher-timeframe agreement
        5. Scans for SMC setups
        6. Validates risk & executes if approved
        Every evaluation ends with exactly one outcome line (TRADE SENT, TRADE SKIPPED, NO SETUP).
        """
        now = datetime.now(timezone.utc)
        m15 = self.market_data.get_candles(symbol, Timeframe.M15)
        bar_time = m15[-1].time if (m15 and len(m15) > 0) else now

        # 1. News restriction check
        allowed, news_reason = self.news_filter.is_trading_allowed(symbol, now)
        if not allowed:
            outcome = DecisionOutcome(
                symbol=symbol,
                bar_time=bar_time,
                outcome_type=DecisionOutcomeType.TRADE_SKIPPED,
                reason_code="NEWS_FILTER",
                details=f"High-impact news active: {news_reason}",
                real_numbers={"news": news_reason}
            )
            self._record_and_dispatch_outcome(outcome)
            return None

        # 2. Retrieve candles & check history sufficiency
        if not m15 or len(m15) < 20:
            outcome = DecisionOutcome(
                symbol=symbol,
                bar_time=bar_time,
                outcome_type=DecisionOutcomeType.NO_SETUP,
                details=f"Insufficient M15 candle history (have {len(m15) if m15 else 0}, required 20)"
            )
            self._record_and_dispatch_outcome(outcome)
            return None

        # 3. Weekend holding restriction check
        profile = getattr(self.portfolio_risk, "profile", None)
        if profile and getattr(profile.rules, "no_weekend_holding", False):
            if now.weekday() == 4 and now.hour >= profile.rules.weekend_close_hour_friday:
                outcome = DecisionOutcome(
                    symbol=symbol,
                    bar_time=bar_time,
                    outcome_type=DecisionOutcomeType.TRADE_SKIPPED,
                    reason_code="SESSION_FILTER",
                    details=f"Weekend holding prohibited after Friday {profile.rules.weekend_close_hour_friday:02d}:00 (current hour {now.hour:02d})"
                )
                self._record_and_dispatch_outcome(outcome)
                return None

        # 4. Multi-timeframe context
        w1 = self.market_data.get_candles(symbol, Timeframe.W1)
        d1 = self.market_data.get_candles(symbol, Timeframe.D1)
        h4 = self.market_data.get_candles(symbol, Timeframe.H4)
        h1 = self.market_data.get_candles(symbol, Timeframe.H1)
        mtf_ctx = self.mtf_analyzer.analyze(symbol, w1, d1, h4, h1, m15)
        self.active_contexts[symbol] = mtf_ctx

        # 5. SCALP higher-timeframe agreement check
        if getattr(self.config.strategy, "scalp_require_htf_agreement", True):
            agree, msg, dirs = self.strategy_engine.check_scalp_htf_agreement(mtf_ctx)
            if not agree:
                outcome = DecisionOutcome(
                    symbol=symbol,
                    bar_time=bar_time,
                    outcome_type=DecisionOutcomeType.NO_SETUP,
                    details=msg,
                    real_numbers=dirs
                )
                self._record_and_dispatch_outcome(outcome)
                return None

        # 6. Session & Killzone check & SMC Strategy Scan
        is_kz, _ = self.sessions.evaluate_killzone(now)
        candidates = self.strategy_engine.scan_for_opportunities(mtf_ctx, m15, is_in_killzone=is_kz)
        if not candidates:
            outcome = DecisionOutcome(
                symbol=symbol,
                bar_time=bar_time,
                outcome_type=DecisionOutcomeType.NO_SETUP,
                details="No valid SMC liquidity sweep, MSS, or FVG/OB pattern found on closed bar"
            )
            self._record_and_dispatch_outcome(outcome)
            return None

        # Filter for top candidate
        candidates.sort(key=lambda c: c.score, reverse=True)
        best = candidates[0]
        self.recent_signals.append(best)

        # 7. Candidate approval status
        if best.status != "APPROVED":
            reasons_str = "; ".join(best.rejection_reasons) if best.rejection_reasons else f"Score {best.score:.1f} < {self.config.strategy.min_confluence_score:.1f}"
            outcome = DecisionOutcome(
                symbol=symbol,
                bar_time=bar_time,
                outcome_type=DecisionOutcomeType.NO_SETUP,
                details=f"Candidate {best.setup_type.value} rejected: {reasons_str}"
            )
            self._record_and_dispatch_outcome(outcome)
            return best

        # 8. Pre-trade check
        symbol_info = self.market_data.get_symbol_info(symbol)
        active_positions = self.order_manager.get_active_positions()
        check = self.order_manager.run_pre_trade_checklist(best, self.account_info, symbol_info, active_positions)

        bal = self.account_info.balance if self.account_info.balance > 0 else 10000.0
        risk_pct = (check.monetary_risk / bal * 100.0) if bal > 0 else 0.5

        if not check.passed:
            rej_summary = "; ".join(check.rejection_reasons)
            outcome = DecisionOutcome(
                symbol=symbol,
                bar_time=bar_time,
                outcome_type=DecisionOutcomeType.TRADE_SKIPPED,
                reason_code="RISK_CHECK_FAILED",
                details=rej_summary,
                real_numbers={"reasons": check.rejection_reasons, "balance": bal}
            )
            self._record_and_dispatch_outcome(outcome)
            return None

        # Active Mode Prohibitions & Frequency Controls (Part 6)
        if getattr(self.config.strategy, "active_scalp_mode", True) and hasattr(self, "active_engine"):
            point = symbol_info.point or 0.00001
            atr_pts = 100.0
            if m15 and len(m15) >= 14:
                ranges = [abs(c.high - c.low) / point for c in m15[-14:]]
                atr_pts = sum(ranges) / len(ranges)

            allowed, prohib_msg = self.active_engine.verify_prohibitions(
                symbol=symbol,
                direction=best.direction,
                candidate_price=best.entry_price,
                candidate_risk_pct=risk_pct,
                candidate_lots=check.calculated_volume,
                atr=atr_pts * point,
                account=self.account_info,
                active_positions=active_positions
            )
            if not allowed:
                outcome = DecisionOutcome(
                    symbol=symbol,
                    bar_time=bar_time,
                    outcome_type=DecisionOutcomeType.TRADE_SKIPPED,
                    reason_code="PROHIBITION_BLOCKED",
                    details=prohib_msg,
                    real_numbers={"reason": prohib_msg, "balance": bal}
                )
                self._record_and_dispatch_outcome(outcome)
                return None

        # Dry run support
        if getattr(self.config, "dry_run", False):
            outcome = DecisionOutcome(
                symbol=symbol,
                bar_time=bar_time,
                outcome_type=DecisionOutcomeType.TRADE_SENT,
                details="Dry-run order simulated",
                direction=best.direction.value,
                volume=check.calculated_volume,
                entry_price=best.entry_price,
                stop_loss=best.stop_loss,
                take_profit=best.take_profit,
                risk_dollars=check.monetary_risk,
                risk_percent=risk_pct,
                ticket=999999
            )
            self._record_and_dispatch_outcome(outcome)
            return best

        # 9. Execute order
        order = self.order_manager.execute_signal(
            candidate=best,
            account=self.account_info,
            symbol_info=symbol_info,
            active_positions=active_positions
        )

        if order and order.status == "FILLED":
            ticket = order.ticket or 0
            outcome = DecisionOutcome(
                symbol=symbol,
                bar_time=bar_time,
                outcome_type=DecisionOutcomeType.TRADE_SENT,
                details="Order successfully filled",
                direction=best.direction.value,
                volume=order.volume,
                entry_price=order.entry_price,
                stop_loss=order.stop_loss,
                take_profit=order.take_profit,
                risk_dollars=check.monetary_risk,
                risk_percent=risk_pct,
                ticket=ticket
            )
            self._record_and_dispatch_outcome(outcome)
            if hasattr(self, "active_engine"):
                self.active_engine.record_trade_opened(symbol=order.symbol, lots=order.volume, risk_pct=risk_pct)
            self.alerts.alert_trade_executed(
                ticket=ticket,
                symbol=order.symbol,
                direction=order.order_type.value,
                volume=order.volume,
                price=order.entry_price
            )
            return best
        else:
            err = getattr(order, "error", None) or "Execution returned null or unfilled"
            outcome = DecisionOutcome(
                symbol=symbol,
                bar_time=bar_time,
                outcome_type=DecisionOutcomeType.TRADE_SKIPPED,
                reason_code="EXECUTION_FAILED",
                details=f"Broker/bridge refused order: {err}"
            )
            self._record_and_dispatch_outcome(outcome)
            return None

    def manage_active_positions(self) -> None:
        """Evaluates lifecycle actions (Break-Even, Partial TP, Trailing SL)."""
        active_positions = self.order_manager.get_active_positions()
        for pos in active_positions:
            sym_info = self.market_data.get_symbol_info(pos.symbol)
            m15 = self.market_data.get_candles(pos.symbol, Timeframe.M15)
            actions = self.trade_manager.evaluate_position(pos, sym_info, m15)

            for act in actions:
                if act.action_type == "MODIFY_SL" and act.new_sl is not None:
                    pos.stop_loss = act.new_sl
                    if "Break-even" in act.reason:
                        pos.break_even_activated = True
                    if self.config.execution_mode in [ExecutionMode.DEMO, ExecutionMode.REAL]:
                        self.bridge.modify_order(ticket=act.ticket, sl=act.new_sl)

                elif act.action_type == "PARTIAL_CLOSE" and act.close_volume is not None:
                    pos.volume -= act.close_volume
                    pos.partial_taken = True
                    if self.config.execution_mode in [ExecutionMode.DEMO, ExecutionMode.REAL]:
                        self.bridge.close_order(ticket=act.ticket, volume=act.close_volume)

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
            portfolio_risk_engine=self.portfolio_risk
        )

    def run_cycle_all_symbols(self) -> Dict[str, Optional[SignalCandidate]]:
        """Scans all enabled symbols in parallel with a concurrency cap (Part 6 #1)."""
        max_workers = min(len(self.config.symbols), getattr(self.active_engine.config, "max_simultaneous_evaluations", 4))
        results = {}
        with ThreadPoolExecutor(max_workers=max(max_workers, 1)) as executor:
            futures = {executor.submit(self.run_cycle_for_symbol, sym): sym for sym in self.config.symbols}
            for fut in futures:
                sym = futures[fut]
                try:
                    results[sym] = fut.result()
                except Exception as e:
                    print(f"Error evaluating symbol {sym} in parallel cycle: {e}")
                    results[sym] = None
        return results
