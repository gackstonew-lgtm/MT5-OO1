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
    AccountInfo, SymbolInfo, ExecutionMode, OrderType
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
from src.execution.bridge_client import BridgeClient
from src.execution.order_manager import OrderManager
from src.learning.performance_tracker import PerformanceTracker, TradeRecord
from src.learning.champion_challenger import ChampionChallengerEngine, StrategyCandidate
from src.monitoring.dashboard import TradingDashboard, DashboardState
from src.monitoring.alerts_telegram import TelegramAlertManager


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
        self.alerts = TelegramAlertManager(
            bot_token=self.config.telegram_bot_token,
            chat_id=self.config.telegram_chat_id
        )

        # State storage
        self.active_contexts: Dict[str, MultiTimeframeContext] = {}
        self.recent_signals: List[SignalCandidate] = []
        self.account_info = AccountInfo()

    def update_account(self, account: AccountInfo) -> None:
        self.account_info = account

    def run_cycle_for_symbol(self, symbol: str) -> Optional[SignalCandidate]:
        """
        Executes a complete scanning cycle for a single instrument:
        1. Checks news filter
        2. Retrieves candles across W1, D1, H4, H1, M15
        3. Computes MTF bias and liquidity levels
        4. Scans for SMC setups
        5. Validates risk & executes if approved
        """
        now = datetime.now(timezone.utc)

        # 1. News restriction check
        allowed, news_reason = self.news_filter.is_trading_allowed(symbol, now)
        if not allowed:
            return None

        # 2. Retrieve candles
        w1 = self.market_data.get_candles(symbol, Timeframe.W1)
        d1 = self.market_data.get_candles(symbol, Timeframe.D1)
        h4 = self.market_data.get_candles(symbol, Timeframe.H4)
        h1 = self.market_data.get_candles(symbol, Timeframe.H1)
        m15 = self.market_data.get_candles(symbol, Timeframe.M15)

        if not m15 or len(m15) < 20:
            return None

        # 3. Multi-timeframe context
        mtf_ctx = self.mtf_analyzer.analyze(symbol, w1, d1, h4, h1, m15)
        self.active_contexts[symbol] = mtf_ctx

        # 4. Session & Killzone check
        is_kz, _ = self.sessions.evaluate_killzone(now)

        # 5. SMC Strategy Scan
        candidates = self.strategy_engine.scan_for_opportunities(mtf_ctx, m15, is_in_killzone=is_kz)
        if not candidates:
            return None

        # Filter for top candidate
        candidates.sort(key=lambda c: c.score, reverse=True)
        best = candidates[0]
        self.recent_signals.append(best)

        # 6. Execute if Approved
        if best.status == "APPROVED":
            symbol_info = self.market_data.get_symbol_info(symbol)
            active_positions = self.order_manager.get_active_positions()

            order = self.order_manager.execute_signal(
                candidate=best,
                account=self.account_info,
                symbol_info=symbol_info,
                active_positions=active_positions
            )

            if order and order.status == "FILLED":
                self.alerts.alert_trade_executed(
                    ticket=order.ticket or 0,
                    symbol=order.symbol,
                    direction=order.order_type.value,
                    volume=order.volume,
                    price=order.entry_price
                )

        return best

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
