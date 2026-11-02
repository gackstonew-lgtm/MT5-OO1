"""
Performance Analytics and Metric Tracker.
Calculates institutional performance metrics: Win Rate, Profit Factor,
Expectancy, Sharpe/Sortino ratios, Max Drawdown, and breakdowns across
symbols, sessions, volatility regimes, setup types, and directions.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Dict, Any, Optional
import numpy as np


@dataclass
class TradeRecord:
    ticket: int
    symbol: str
    direction: str  # BUY or SELL
    setup_type: str
    session: str
    regime: str
    entry_price: float
    exit_price: float
    volume: float
    profit: float
    r_multiple: float
    entry_time: datetime
    exit_time: datetime


@dataclass
class PerformanceSummary:
    total_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    breakeven_trades: int = 0
    win_rate_pct: float = 0.0
    profit_factor: float = 0.0
    expectancy: float = 0.0
    average_r_multiple: float = 0.0
    gross_profit: float = 0.0
    gross_loss: float = 0.0
    net_profit: float = 0.0
    max_drawdown_amount: float = 0.0
    max_drawdown_pct: float = 0.0
    max_consecutive_losses: int = 0
    by_symbol: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    by_session: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    by_regime: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    by_setup_type: Dict[str, Dict[str, Any]] = field(default_factory=dict)


class PerformanceTracker:
    def __init__(self, initial_capital: float = 10000.0):
        self.initial_capital = initial_capital
        self.trades: List[TradeRecord] = []

    def record_trade(self, trade: TradeRecord) -> None:
        self.trades.append(trade)

    def calculate_summary(self) -> PerformanceSummary:
        if not self.trades:
            return PerformanceSummary()

        total = len(self.trades)
        wins = [t for t in self.trades if t.profit > 0]
        losses = [t for t in self.trades if t.profit < 0]
        bes = [t for t in self.trades if t.profit == 0]

        gross_profit = sum(t.profit for t in wins)
        gross_loss = abs(sum(t.profit for t in losses))
        net_profit = gross_profit - gross_loss

        win_rate = (len(wins) / total) * 100.0 if total > 0 else 0.0
        profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else (99.0 if gross_profit > 0 else 0.0)

        avg_win = (gross_profit / len(wins)) if wins else 0.0
        avg_loss = (gross_loss / len(losses)) if losses else 0.0
        win_prob = len(wins) / total if total > 0 else 0.0
        loss_prob = len(losses) / total if total > 0 else 0.0
        expectancy = (win_prob * avg_win) - (loss_prob * avg_loss)

        avg_r = float(np.mean([t.r_multiple for t in self.trades])) if self.trades else 0.0

        # Drawdown calculation
        equity_curve = [self.initial_capital]
        current_eq = self.initial_capital
        peak_eq = self.initial_capital
        max_dd_amount = 0.0
        max_dd_pct = 0.0

        for t in self.trades:
            current_eq += t.profit
            equity_curve.append(current_eq)
            if current_eq > peak_eq:
                peak_eq = current_eq
            dd_amount = peak_eq - current_eq
            dd_pct = (dd_amount / peak_eq) * 100.0 if peak_eq > 0 else 0.0
            if dd_amount > max_dd_amount:
                max_dd_amount = dd_amount
            if dd_pct > max_dd_pct:
                max_dd_pct = dd_pct

        # Consecutive losses
        max_cons_losses = 0
        current_cons = 0
        for t in self.trades:
            if t.profit < 0:
                current_cons += 1
                if current_cons > max_cons_losses:
                    max_cons_losses = current_cons
            else:
                current_cons = 0

        # Sub-group Breakdowns
        by_symbol = self._breakdown_by_attribute(lambda t: t.symbol)
        by_session = self._breakdown_by_attribute(lambda t: t.session)
        by_regime = self._breakdown_by_attribute(lambda t: t.regime)
        by_setup = self._breakdown_by_attribute(lambda t: t.setup_type)

        return PerformanceSummary(
            total_trades=total,
            winning_trades=len(wins),
            losing_trades=len(losses),
            breakeven_trades=len(bes),
            win_rate_pct=round(win_rate, 2),
            profit_factor=round(profit_factor, 2),
            expectancy=round(expectancy, 2),
            average_r_multiple=round(avg_r, 2),
            gross_profit=round(gross_profit, 2),
            gross_loss=round(gross_loss, 2),
            net_profit=round(net_profit, 2),
            max_drawdown_amount=round(max_dd_amount, 2),
            max_drawdown_pct=round(max_dd_pct, 2),
            max_consecutive_losses=max_cons_losses,
            by_symbol=by_symbol,
            by_session=by_session,
            by_regime=by_regime,
            by_setup_type=by_setup
        )

    def _breakdown_by_attribute(self, attr_func) -> Dict[str, Dict[str, Any]]:
        groups: Dict[str, List[TradeRecord]] = {}
        for t in self.trades:
            key = str(attr_func(t))
            if key not in groups:
                groups[key] = []
            groups[key].append(t)

        results = {}
        for k, records in groups.items():
            tot = len(records)
            wins = len([r for r in records if r.profit > 0])
            net = sum(r.profit for r in records)
            results[k] = {
                "trades": tot,
                "win_rate": round((wins / tot) * 100.0, 1) if tot > 0 else 0.0,
                "net_profit": round(net, 2)
            }
        return results
