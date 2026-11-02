"""
Monte Carlo Simulation & Ruin Risk Engine.
Resamples trade sequences 5,000+ times to evaluate ending equity distributions,
drawdown breach probabilities (DEMO 10%, LIVE 6%, PROP 4%), and probability of ruin
from $5, $25, $100, and $500 starting balances under Stage 0A tier rules.
"""

from __future__ import annotations
import math
from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional
import numpy as np

from src.risk.account_tier import AccountTierManager


@dataclass
class RuinAnalysis:
    starting_balance: float
    ruin_count: int
    total_simulations: int
    probability_of_ruin: float
    median_ending_equity: float
    p5_ending_equity: float
    p95_ending_equity: float
    worst_drawdown_pct: float


@dataclass
class MonteCarloSummary:
    simulations_count: int
    trades_per_sim: int
    prob_breach_demo_dd_10pct: float
    prob_breach_live_dd_6pct: float
    prob_breach_prop_dd_4pct: float
    median_max_drawdown_pct: float
    p95_max_drawdown_pct: float
    p99_max_drawdown_pct: float
    ruin_by_starting_balance: Dict[float, RuinAnalysis] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "simulations_count": self.simulations_count,
            "trades_per_sim": self.trades_per_sim,
            "prob_breach_demo_dd_10pct": round(self.prob_breach_demo_dd_10pct, 4),
            "prob_breach_live_dd_6pct": round(self.prob_breach_live_dd_6pct, 4),
            "prob_breach_prop_dd_4pct": round(self.prob_breach_prop_dd_4pct, 4),
            "median_max_drawdown_pct": round(self.median_max_drawdown_pct, 2),
            "p95_max_drawdown_pct": round(self.p95_max_drawdown_pct, 2),
            "p99_max_drawdown_pct": round(self.p99_max_drawdown_pct, 2),
            "ruin_analysis": {
                f"${bal:,.0f}": {
                    "prob_ruin": round(r.probability_of_ruin, 4),
                    "median_equity": round(r.median_ending_equity, 2),
                    "p5_equity": round(r.p5_ending_equity, 2),
                    "worst_dd_pct": round(r.worst_drawdown_pct, 2),
                }
                for bal, r in self.ruin_by_starting_balance.items()
            }
        }


class MonteCarloEngine:
    def __init__(self, n_simulations: int = 5000, seed: int = 42):
        self.n_simulations = n_simulations
        self.seed = seed
        self.tier_mgr = AccountTierManager()

    def run_simulation(
        self,
        trades_r: List[float],
        starting_balances: Optional[List[float]] = None
    ) -> MonteCarloSummary:
        """
        Executes 5,000+ Monte Carlo reshufflings with replacement.
        Simulates capital trajectory for each starting capital ($5, $25, $100, $500)
        using Stage 0A tier sizing rules.
        """
        np.random.seed(self.seed)
        r_arr = np.array(trades_r, dtype=np.float64)
        if len(r_arr) == 0:
            # Fallback zero distribution
            r_arr = np.array([-1.0, 1.0])

        n_trades = len(r_arr)
        sim_trades_count = max(50, n_trades)

        # 1. Standard Drawdown distribution on normalized R
        max_drawdowns = []
        breach_demo = 0   # 10% DD
        breach_live = 0   # 6% DD
        breach_prop = 0   # 4% DD

        # Assuming 1% risk per 1R for standard profile
        for _ in range(self.n_simulations):
            sampled_r = np.random.choice(r_arr, size=sim_trades_count, replace=True)
            # Equity curve starting at 10,000 (1% base risk)
            eq = 10000.0
            peak = eq
            worst_dd = 0.0

            for r in sampled_r:
                risk_amt = eq * 0.01
                pnl = r * risk_amt
                eq += pnl
                if eq > peak:
                    peak = eq
                dd = (peak - eq) / peak * 100.0
                if dd > worst_dd:
                    worst_dd = dd

            max_drawdowns.append(worst_dd)
            if worst_dd >= 10.0:
                breach_demo += 1
            if worst_dd >= 6.0:
                breach_live += 1
            if worst_dd >= 4.0:
                breach_prop += 1

        md_arr = np.array(max_drawdowns)

        # 2. Ruin Analysis across Starting Balances ($5, $25, $100, $500)
        balances_to_test = starting_balances or [5.0, 25.0, 100.0, 500.0]
        ruin_results: Dict[float, RuinAnalysis] = {}

        for start_bal in balances_to_test:
            ruin_count = 0
            end_equities = []
            worst_dds = []

            for _ in range(self.n_simulations):
                sampled_r = np.random.choice(r_arr, size=sim_trades_count, replace=True)
                cap = start_bal
                peak = cap
                local_worst_dd = 0.0
                is_ruined = False

                for r in sampled_r:
                    # Stage 0A Floor Guard: below $5 disables trading / ruin
                    if cap < 5.0:
                        is_ruined = True
                        break

                    # Get tier risk percentage dynamically
                    tier_def = self.tier_mgr.get_tier_def(
                        "MICRO" if cap < 25.0 else ("MINI" if cap < 100.0 else ("SMALL" if cap < 500.0 else "STANDARD"))
                    )
                    risk_pct = tier_def.target_risk_pct / 100.0
                    risk_amt = cap * risk_pct

                    pnl = r * risk_amt
                    cap += pnl

                    if cap > peak:
                        peak = cap
                    dd = (peak - cap) / peak * 100.0
                    if dd > local_worst_dd:
                        local_worst_dd = dd

                    if cap < 5.0:
                        is_ruined = True
                        break

                if is_ruined:
                    ruin_count += 1

                end_equities.append(cap)
                worst_dds.append(local_worst_dd)

            end_eq_arr = np.array(end_equities)
            ruin_results[start_bal] = RuinAnalysis(
                starting_balance=start_bal,
                ruin_count=ruin_count,
                total_simulations=self.n_simulations,
                probability_of_ruin=float(ruin_count / self.n_simulations),
                median_ending_equity=float(np.median(end_eq_arr)),
                p5_ending_equity=float(np.percentile(end_eq_arr, 5)),
                p95_ending_equity=float(np.percentile(end_eq_arr, 95)),
                worst_drawdown_pct=float(np.max(worst_dds)) if len(worst_dds) > 0 else 0.0
            )

        return MonteCarloSummary(
            simulations_count=self.n_simulations,
            trades_per_sim=sim_trades_count,
            prob_breach_demo_dd_10pct=float(breach_demo / self.n_simulations),
            prob_breach_live_dd_6pct=float(breach_live / self.n_simulations),
            prob_breach_prop_dd_4pct=float(breach_prop / self.n_simulations),
            median_max_drawdown_pct=float(np.median(md_arr)),
            p95_max_drawdown_pct=float(np.percentile(md_arr, 95)),
            p99_max_drawdown_pct=float(np.percentile(md_arr, 99)),
            ruin_by_starting_balance=ruin_results
        )
