"""
Quantitative Validation Suite.
Implements walk-forward testing with purging and embargo, Deflated Sharpe Ratio,
Probability of Backtest Overfitting, Monte Carlo ruin analysis, and cost stress testing.
"""

from .backtest_engine import RealisticBacktestEngine, BacktestResult, SimulatedTrade
from .walk_forward import WalkForwardEngine, WalkForwardWindow
from .statistics import QuantitativeStats, compute_quant_statistics, deflated_sharpe_ratio
from .monte_carlo import MonteCarloEngine, MonteCarloSummary
from .cost_sensitivity import CostSensitivityStressTest

__all__ = [
    "RealisticBacktestEngine",
    "BacktestResult",
    "SimulatedTrade",
    "WalkForwardEngine",
    "WalkForwardWindow",
    "QuantitativeStats",
    "compute_quant_statistics",
    "deflated_sharpe_ratio",
    "MonteCarloEngine",
    "MonteCarloSummary",
    "CostSensitivityStressTest",
]
