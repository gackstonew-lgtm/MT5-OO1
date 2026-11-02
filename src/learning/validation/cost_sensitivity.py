"""
Cost Sensitivity & Execution Drag Stress Testing.
Evaluates strategy expectancy and profit factor under 1.0x (baseline), 1.5x, and 2.0x
spread, commission, and slippage stress scenarios.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import List, Dict, Any
import numpy as np

from .statistics import QuantitativeStats, compute_quant_statistics


@dataclass
class CostStressLevelResult:
    cost_multiplier: float
    total_trades: int
    expectancy_r: float
    profit_factor: float
    win_rate: float
    sharpe_ratio: float
    max_drawdown_pct: float
    total_net_profit: float
    survives_edge: bool


@dataclass
class CostSensitivityStressTest:
    baseline: CostStressLevelResult
    stress_1_5x: CostStressLevelResult
    stress_2_0x: CostStressLevelResult
    edge_survival_status: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "baseline": {
                "mult": self.baseline.cost_multiplier,
                "expectancy_r": round(self.baseline.expectancy_r, 3),
                "profit_factor": round(self.baseline.profit_factor, 3),
                "sharpe": round(self.baseline.sharpe_ratio, 3),
                "max_dd_pct": round(self.baseline.max_drawdown_pct, 2),
                "survives": self.baseline.survives_edge,
            },
            "stress_1_5x": {
                "mult": self.stress_1_5x.cost_multiplier,
                "expectancy_r": round(self.stress_1_5x.expectancy_r, 3),
                "profit_factor": round(self.stress_1_5x.profit_factor, 3),
                "sharpe": round(self.stress_1_5x.sharpe_ratio, 3),
                "max_dd_pct": round(self.stress_1_5x.max_drawdown_pct, 2),
                "survives": self.stress_1_5x.survives_edge,
            },
            "stress_2_0x": {
                "mult": self.stress_2_0x.cost_multiplier,
                "expectancy_r": round(self.stress_2_0x.expectancy_r, 3),
                "profit_factor": round(self.stress_2_0x.profit_factor, 3),
                "sharpe": round(self.stress_2_0x.sharpe_ratio, 3),
                "max_dd_pct": round(self.stress_2_0x.max_drawdown_pct, 2),
                "survives": self.stress_2_0x.survives_edge,
            },
            "verdict": self.edge_survival_status,
        }

    @classmethod
    def evaluate(
        cls,
        trades_r: List[float],
        trades_money: List[float],
        estimated_cost_per_trade_r: float = 0.08,
        estimated_cost_per_trade_money: float = 2.50
    ) -> CostSensitivityStressTest:
        """
        Simulates the effect of scaling transaction costs by 1.0x, 1.5x, and 2.0x.
        """
        def run_scenario(mult: float) -> CostStressLevelResult:
            extra_cost_r = (mult - 1.0) * estimated_cost_per_trade_r
            extra_cost_m = (mult - 1.0) * estimated_cost_per_trade_money

            adj_r = [r - extra_cost_r for r in trades_r]
            adj_m = [m - extra_cost_m for m in trades_money]

            st = compute_quant_statistics(adj_r, adj_m)
            survives = (st.expectancy_r > 0.0) and (st.profit_factor > 1.1)
            return CostStressLevelResult(
                cost_multiplier=mult,
                total_trades=st.total_trades,
                expectancy_r=st.expectancy_r,
                profit_factor=st.profit_factor,
                win_rate=st.win_rate,
                sharpe_ratio=st.sharpe_ratio,
                max_drawdown_pct=st.max_drawdown_pct,
                total_net_profit=st.total_net_profit,
                survives_edge=survives
            )

        base = run_scenario(1.0)
        s15 = run_scenario(1.5)
        s20 = run_scenario(2.0)

        if s20.survives_edge:
            verdict = "ROBUST (Survives 2.0x Friction)"
        elif s15.survives_edge:
            verdict = "MODERATE (Survives 1.5x Friction)"
        elif base.survives_edge:
            verdict = "FRAGILE (Edge fails under cost stress)"
        else:
            verdict = "NO EDGE"

        return cls(
            baseline=base,
            stress_1_5x=s15,
            stress_2_0x=s20,
            edge_survival_status=verdict
        )
