"""
Real-Time Algorithmic Trading Dashboard.
Aggregates live account metrics, multi-timeframe market states,
active positions, rejected setups, performance KPIs, and risk limits.
"""

from __future__ import annotations
import json
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from typing import Dict, List, Any, Optional

from src.core.contracts import AccountInfo, Position, SignalCandidate, TrendBias
from src.learning.performance_tracker import PerformanceSummary


@dataclass
class DashboardState:
    timestamp: str
    execution_mode: str
    # 1. Account Overview
    balance: float
    equity: float
    floating_pnl: float
    realized_pnl: float
    margin_used: float
    free_margin: float
    current_drawdown_pct: float
    # 2. Market Overview
    symbols_overview: Dict[str, Dict[str, Any]]
    active_session: str
    is_killzone: bool
    # 3. Trading Activity
    open_positions_count: int
    open_positions: List[Dict[str, Any]]
    recent_signals: List[Dict[str, Any]]
    # 4. Performance Analytics
    total_trades: int
    win_rate_pct: float
    profit_factor: float
    expectancy: float
    average_r: float
    # 5. Risk Dashboard
    risk_per_trade_pct: float
    aggregate_open_risk_pct: float
    daily_loss_pct: float
    circuit_breaker_active: bool
    circuit_breaker_status: str


class TradingDashboard:
    def __init__(self, execution_mode: str = "SIMULATION"):
        self.execution_mode = execution_mode

    def build_snapshot(
        self,
        account: AccountInfo,
        active_positions: List[Position],
        signals: List[SignalCandidate],
        market_contexts: Dict[str, Any],
        perf: PerformanceSummary,
        portfolio_risk_engine
    ) -> DashboardState:
        """Constructs unified dashboard state."""
        floating = sum(p.profit for p in active_positions)
        realized = perf.net_profit
        peak = max(account.balance, account.equity)
        current_dd = ((peak - account.equity) / peak * 100.0) if peak > 0 else 0.0

        pos_list = [
            {
                "ticket": p.ticket,
                "symbol": p.symbol,
                "type": p.order_type.value,
                "volume": p.volume,
                "open_price": p.open_price,
                "current_price": p.current_price,
                "sl": p.stop_loss,
                "tp": p.take_profit,
                "profit": p.profit
            }
            for p in active_positions
        ]

        sig_list = [
            {
                "id": s.id,
                "symbol": s.symbol,
                "direction": s.direction.value,
                "status": s.status,
                "score": s.score,
                "rr": s.risk_reward,
                "reasons": s.reasons,
                "rejections": s.rejection_reasons
            }
            for s in signals[-10:]
        ]

        sym_overview = {}
        for sym, ctx in market_contexts.items():
            sym_overview[sym] = {
                "w1_bias": ctx.w1.trend.value if hasattr(ctx, 'w1') else "N/A",
                "d1_bias": ctx.d1.trend.value if hasattr(ctx, 'd1') else "N/A",
                "h4_trend": ctx.h4.trend.value if hasattr(ctx, 'h4') else "N/A",
                "h1_confluence": ctx.h1.trend.value if hasattr(ctx, 'h1') else "N/A",
                "overall": ctx.overall_bias.value if hasattr(ctx, 'overall_bias') else "N/A"
            }

        curr_open_risk = sum(p.initial_risk_amount for p in active_positions)
        agg_open_risk_pct = (curr_open_risk / account.equity * 100.0) if account.equity > 0 else 0.0
        daily_loss_pct = (portfolio_risk_engine.daily_realized_loss / portfolio_risk_engine.daily_starting_equity * 100.0) if portfolio_risk_engine.daily_starting_equity > 0 else 0.0

        return DashboardState(
            timestamp=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
            execution_mode=self.execution_mode,
            balance=round(account.balance, 2),
            equity=round(account.equity, 2),
            floating_pnl=round(floating, 2),
            realized_pnl=round(realized, 2),
            margin_used=round(account.margin, 2),
            free_margin=round(account.free_margin, 2),
            current_drawdown_pct=round(current_dd, 2),
            symbols_overview=sym_overview,
            active_session="ACTIVE",
            is_killzone=True,
            open_positions_count=len(active_positions),
            open_positions=pos_list,
            recent_signals=sig_list,
            total_trades=perf.total_trades,
            win_rate_pct=perf.win_rate_pct,
            profit_factor=perf.profit_factor,
            expectancy=perf.expectancy,
            average_r=perf.average_r_multiple,
            risk_per_trade_pct=portfolio_risk_engine.config.risk_percentage,
            aggregate_open_risk_pct=round(agg_open_risk_pct, 2),
            daily_loss_pct=round(daily_loss_pct, 2),
            circuit_breaker_active=portfolio_risk_engine.circuit_breaker_active,
            circuit_breaker_status=portfolio_risk_engine.circuit_breaker_reason or "OPERATIONAL_NORMAL"
        )

    def render_cli(self, state: DashboardState) -> str:
        """Renders an institutional ASCII dashboard view."""
        cb_color = "[ALERT HALTED]" if state.circuit_breaker_active else "[NORMAL]"
        out = []
        out.append("=" * 80)
        out.append(f"  QUANT INSTITUTIONAL MT5 DAY-TRADING DASHBOARD | MODE: {state.execution_mode}")
        out.append(f"  Timestamp: {state.timestamp} | Status: {cb_color}")
        out.append("=" * 80)
        out.append(" [1] ACCOUNT OVERVIEW")
        out.append(f"  Balance: ${state.balance:<10.2f} | Equity: ${state.equity:<10.2f} | Floating P&L: ${state.floating_pnl:<8.2f}")
        out.append(f"  Free Margin: ${state.free_margin:<8.2f} | Realized P&L: ${state.realized_pnl:<8.2f} | Current DD: {state.current_drawdown_pct:.2f}%")
        out.append("-" * 80)
        out.append(" [2] RISK & CIRCUIT BREAKERS")
        out.append(f"  Risk Per Trade: {state.risk_per_trade_pct}% | Open Risk: {state.aggregate_open_risk_pct:.2f}% | Daily Loss: {state.daily_loss_pct:.2f}%")
        out.append(f"  Breaker Status: {state.circuit_breaker_status}")
        out.append("-" * 80)
        out.append(" [3] PERFORMANCE METRICS")
        out.append(f"  Trades: {state.total_trades:<4} | Win Rate: {state.win_rate_pct:<5.1f}% | Profit Factor: {state.profit_factor:<4.2f} | Expectancy: ${state.expectancy:<5.2f}")
        out.append("-" * 80)
        out.append(f" [4] ACTIVE POSITIONS ({state.open_positions_count})")
        if not state.open_positions:
            out.append("  No open positions.")
        else:
            for p in state.open_positions:
                out.append(f"  #{p['ticket']} {p['symbol']:<7} {p['type']:<4} {p['volume']:.2f} lots @ {p['open_price']:.5f} | SL: {p['sl']:.5f} | TP: {p['tp']:.5f} | PnL: ${p['profit']:.2f}")
        out.append("=" * 80)
        return "\n".join(out)
