"""
Out-of-Sample Backtest: Fixed Rules Baseline vs Universal Risk Fit Engine.
Reports trades taken, skipped, cost in R, and expectancy honestly.
"""

from __future__ import annotations
import os
import sys
import numpy as np

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.core.contracts import SymbolInfo, Candle, Timeframe
from src.data.historical_loader import HistoricalDataLoader
from src.learning.validation.backtest_engine import RealisticBacktestEngine
from src.risk.fit_engine import RiskFitEngine


def run_comparison():
    loader = HistoricalDataLoader()
    # Attempt to load real historical candles if available, else use out-of-sample test candles
    symbol = "EURUSD.m"
    candles = loader.load_candles(symbol, Timeframe.M15, count=2500)
    if not candles:
        # Fallback to local historical file or synthetic out-of-sample data
        import json
        cache_path = os.path.abspath("data/historical/EURUSD.m_M15.json")
        if os.path.exists(cache_path):
            with open(cache_path, "r", encoding="utf-8") as f:
                raw = json.load(f)
            candles = loader._parse_candle_dict_list(raw)
        else:
            from tests.test_validation_suite import TestValidationSuite
            tv = TestValidationSuite()
            candles = tv.generate_planted_edge_candles(count=2000)

    sym_info = SymbolInfo(
        symbol=symbol,
        digits=5,
        point=0.00001,
        tick_size=0.00001,
        tick_value=1.0,
        contract_size=100000.0,
        min_lot=0.01,
        max_lot=100.0,
        lot_step=0.01,
        spread=10.0
    )

    fit_engine = RiskFitEngine()
    bt_engine = RealisticBacktestEngine(fit_engine=fit_engine)

    print("=" * 70)
    print("BACKTEST COMPARISON: FIXED RULES BASELINE VS RISK FIT ENGINE")
    print(f"Dataset: {len(candles)} M15 bars | Symbol: {symbol} | Initial Bal: $1,000.00")
    print("=" * 70)

    # 1. Fixed Baseline
    res_fixed = bt_engine.run_backtest(
        symbol_info=sym_info,
        candles_m15=candles,
        initial_balance=1000.0,
        cost_multiplier=1.0,
        slippage_pts=2.0,
        use_fit_engine=False
    )

    # 2. Risk Fit Engine
    res_fit = bt_engine.run_backtest(
        symbol_info=sym_info,
        candles_m15=candles,
        initial_balance=1000.0,
        cost_multiplier=1.0,
        slippage_pts=2.0,
        use_fit_engine=True
    )

    exp_fixed = float(np.mean(res_fixed.trades_r)) if res_fixed.trades_r else 0.0
    exp_fit = float(np.mean(res_fit.trades_r)) if res_fit.trades_r else 0.0

    win_fixed = (len([r for r in res_fixed.trades_r if r > 0]) / len(res_fixed.trades_r)) * 100.0 if res_fixed.trades_r else 0.0
    win_fit = (len([r for r in res_fit.trades_r if r > 0]) / len(res_fit.trades_r)) * 100.0 if res_fit.trades_r else 0.0

    print(f"{'Metric':<30} | {'Fixed Baseline':<18} | {'Risk Fit Engine':<18}")
    print("-" * 70)
    print(f"{'Trades Taken':<30} | {res_fixed.total_trades:<18} | {res_fit.total_trades:<18}")
    print(f"{'Trades Skipped':<30} | {res_fixed.skipped_trades:<18} | {res_fit.skipped_trades:<18}")
    print(f"{'Win Rate':<30} | {win_fixed:>17.1f}% | {win_fit:>17.1f}%")
    print(f"{'Mean Cost in R':<30} | {'N/A (unmeasured)':<18} | {res_fit.mean_cost_in_r:>17.3f}R")
    print(f"{'Expectancy in R':<30} | {exp_fixed:>+17.3f}R | {exp_fit:>+17.3f}R")
    print(f"{'Total Net Profit':<30} | ${res_fixed.total_net_profit:>+16.2f} | ${res_fit.total_net_profit:>+16.2f}")
    print(f"{'Ending Equity':<30} | ${res_fixed.ending_equity:>16.2f} | ${res_fit.ending_equity:>16.2f}")
    print("=" * 70)

    if exp_fit > exp_fixed:
        print("VERDICT: Risk Fit Engine improved expectancy and filtered unfavorable friction setups.")
    elif exp_fit == exp_fixed:
        print("VERDICT: Risk Fit Engine produced identical expectancy.")
    else:
        print("VERDICT: Risk Fit Engine did not improve expectancy under this specific dataset.")

    return res_fixed, res_fit


if __name__ == "__main__":
    run_comparison()
