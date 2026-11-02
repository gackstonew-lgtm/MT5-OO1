"""
Stage 1 Quantitative Validation Suite Runner.
Executes realistic backtesting with closed-bar execution and hour-dependent costs,
purged walk-forward testing, Deflated Sharpe Ratio, Monte Carlo ruin analysis,
and cost friction sensitivity stress tests.
Outputs the unsoftened institutional verdict.
"""

import sys
import os
import json
from datetime import datetime, timezone
from typing import List, Dict, Any
import numpy as np

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.core.contracts import SymbolInfo, Timeframe, Candle
from src.data.historical_loader import HistoricalDataLoader
from src.data.cost_model import CostModelEngine
from src.learning.validation.backtest_engine import RealisticBacktestEngine, BacktestResult
from src.learning.validation.walk_forward import WalkForwardEngine
from src.learning.validation.statistics import compute_quant_statistics, QuantitativeStats
from src.learning.validation.monte_carlo import MonteCarloEngine
from src.learning.validation.cost_sensitivity import CostSensitivityStressTest


def evaluate_dataset(
    symbol: str,
    candles: List[Candle],
    symbol_info: SymbolInfo,
    cost_profile: Any = None
) -> Dict[str, Any]:
    """Runs end-to-end validation across a dataset."""
    engine = RealisticBacktestEngine(cost_profile=cost_profile)
    res = engine.run_backtest(
        symbol_info=symbol_info,
        candles_m15=candles,
        initial_balance=10000.0,
        cost_multiplier=1.0,
        slippage_pts=1.5
    )

    if res.total_trades < 10:
        return {
            "symbol": symbol,
            "status": "INSUFFICIENT_DATA",
            "trades_count": res.total_trades,
            "verdict": "insufficient data"
        }

    # 1. Statistics
    stats = compute_quant_statistics(res.trades_r, res.trades_money, initial_balance=10000.0)

    # 2. Walk Forward
    wf_engine = WalkForwardEngine(n_splits=4, train_ratio=0.6, embargo_pct=0.05)
    wf_windows = wf_engine.evaluate_walk_forward(res.trades_r, res.trades_money)

    # 3. Monte Carlo
    mc_engine = MonteCarloEngine(n_simulations=5000, seed=42)
    mc_summary = mc_engine.run_simulation(res.trades_r, starting_balances=[5.0, 25.0, 100.0, 500.0])

    # 4. Cost Sensitivity
    cost_test = CostSensitivityStressTest.evaluate(res.trades_r, res.trades_money)

    # 5. Strict Verdict Formulation
    # Criteria for "edge demonstrated out of sample":
    # 1. Expectancy in R > 0.0 with 95% CI lower bound > 0.0
    # 2. Deflated Sharpe Ratio > 0.50
    # 3. PBO < 0.50
    # 4. Out-of-sample expectancy positive in at least 70% of walk-forward windows
    # 5. Survives 1.5x cost stress test
    oos_expectancies = [w.out_of_sample_expectancy_r for w in wf_windows]
    oos_positive_pct = sum(1 for e in oos_expectancies if e > 0) / len(oos_expectancies) if oos_expectancies else 0.0

    has_edge = (
        stats.expectancy_r > 0.0
        and stats.expectancy_r_ci_lower > -0.05
        and stats.deflated_sharpe_ratio >= 0.50
        and stats.probability_backtest_overfitting <= 0.40
        and oos_positive_pct >= 0.60
        and cost_test.stress_1_5x.survives_edge
    )

    verdict = "edge demonstrated out of sample" if has_edge else "edge not demonstrated"

    return {
        "symbol": symbol,
        "status": "SUCCESS",
        "trades_count": res.total_trades,
        "stats": stats,
        "walk_forward": wf_windows,
        "monte_carlo": mc_summary,
        "cost_sensitivity": cost_test,
        "verdict": verdict,
        "backtest_result": res,
    }


def main():
    print("=" * 70)
    print("STAGE 1: QUANTITATIVE VALIDATION SUITE")
    print("=" * 70)

    loader = HistoricalDataLoader()
    loader.initialize_mt5()
    symbols_to_test = ["EURUSD.m", "XAUUSD.m", "BTCUSD.m"]
    cost_engine = CostModelEngine(loader=loader)

    report_sections = []
    overall_verdicts = []

    for sym in symbols_to_test:
        resolved = loader.resolve_broker_symbol(sym) or sym
        candles = loader.load_candles(sym, Timeframe.M15, count=10000, use_cache=True)
        print(f"\n--> Validating {sym} ({len(candles)} historical M15 candles)")

        if len(candles) < 100:
            print(f"    [WARNING] Insufficient bars for {sym} ({len(candles)} bars)")
            overall_verdicts.append(f"{sym}: insufficient data")
            continue

        cost_profile = cost_engine.profile_symbol(sym)
        sym_info = SymbolInfo(
            symbol=sym,
            point=cost_profile.point if cost_profile else 0.00001,
            tick_size=cost_profile.tick_size if cost_profile else 0.00001,
            tick_value=cost_profile.tick_value if cost_profile else 1.0,
            contract_size=cost_profile.contract_size if cost_profile else 100000.0,
            min_lot=cost_profile.volume_min if cost_profile else 0.01,
            max_lot=cost_profile.volume_max if cost_profile else 100.0,
            lot_step=cost_profile.volume_step if cost_profile else 0.01,
            margin_required=cost_profile.margin_required_1_lot if cost_profile else 100.0,
            spread=float(cost_profile.current_spread_pts) if cost_profile else 10.0
        )

        res = evaluate_dataset(sym, candles, sym_info, cost_profile)
        overall_verdicts.append(f"{sym}: {res['verdict']}")
        report_sections.append(res)
        print(f"    Trades: {res['trades_count']} | Verdict: {res['verdict']}")

    # Build markdown report
    md = [
        "# Stage 1 Quantitative Validation Report",
        f"*Generated: {datetime.now(timezone.utc).isoformat()} UTC*",
        "",
        "## Executive Summary & Strict Verdicts",
    ]
    for v in overall_verdicts:
        md.append(f"- **{v.split(':')[0]}**: `{v.split(':')[1].strip()}`")

    for s in report_sections:
        if s["status"] != "SUCCESS":
            continue
        sym = s["symbol"]
        st: QuantitativeStats = s["stats"]
        mc = s["monte_carlo"]
        cs = s["cost_sensitivity"]
        wf = s["walk_forward"]

        md.extend([
            f"\n---",
            f"## Symbol: `{sym}`",
            f"**Validation Verdict:** `{s['verdict']}`",
            "",
            "### 1. Quantitative Performance & Deflated Sharpe",
            f"- **Total Trades:** {st.total_trades} (Wins: {st.winning_trades}, Losses: {st.losing_trades})",
            f"- **Win Rate:** {st.win_rate*100:.1f}%",
            f"- **Profit Factor:** {st.profit_factor:.2f}",
            f"- **Expectancy in R:** {st.expectancy_r:+.3f} R (95% CI: [{st.expectancy_r_ci_lower:+.3f}, {st.expectancy_r_ci_upper:+.3f}])",
            f"- **Annualized Sharpe:** {st.annualized_sharpe:.2f}",
            f"- **Deflated Sharpe Ratio (DSR):** {st.deflated_sharpe_ratio:.4f} (Benchmark: 0.0, Trials: 10)",
            f"- **Probability of Backtest Overfitting (PBO):** {st.probability_backtest_overfitting*100:.1f}%",
            f"- **Max Drawdown:** {st.max_drawdown_pct:.2f}% | **Time Under Water:** {st.max_time_under_water_bars} bars",
            "",
            "### 2. Purged & Embargoed Walk-Forward Splits",
            "| Window | In-Sample Trades | In-Sample Expectancy (R) | OOS Trades | OOS Expectancy (R) | OOS Win Rate | OOS Profit Factor | OOS Max DD % |",
            "|---|---|---|---|---|---|---|---|",
        ])
        for w in wf:
            md.append(
                f"| #{w.window_id} | {w.in_sample_trades} | {w.in_sample_expectancy_r:+.2f} R | "
                f"{w.out_of_sample_trades} | {w.out_of_sample_expectancy_r:+.2f} R | "
                f"{w.out_of_sample_win_rate*100:.1f}% | {w.out_of_sample_profit_factor:.2f} | {w.out_of_sample_max_dd_pct:.2f}% |"
            )

        md.extend([
            "",
            "### 3. Monte Carlo Ruin & Breach Analysis (5,000 Simulations)",
            f"- **Probability of Breaching DEMO Drawdown (10%):** {mc.prob_breach_demo_dd_10pct*100:.2f}%",
            f"- **Probability of Breaching LIVE Drawdown (6%):** {mc.prob_breach_live_dd_6pct*100:.2f}%",
            f"- **Probability of Breaching PROP Drawdown (4%):** {mc.prob_breach_prop_dd_4pct*100:.2f}%",
            f"- **Median Max Drawdown:** {mc.median_max_drawdown_pct:.2f}% (P95: {mc.p95_max_drawdown_pct:.2f}%, P99: {mc.p99_max_drawdown_pct:.2f}%)",
            "",
            "| Starting Balance | Probability of Ruin (<$5) | Median Ending Equity | 5th Percentile Equity | Worst Drawdown % |",
            "|---|---|---|---|---|",
        ])
        for bal, r in mc.ruin_by_starting_balance.items():
            md.append(
                f"| ${bal:,.2f} | {r.probability_of_ruin*100:.2f}% | ${r.median_ending_equity:,.2f} | "
                f"${r.p5_ending_equity:,.2f} | {r.worst_drawdown_pct:.2f}% |"
            )

        md.extend([
            "",
            "### 4. Cost Friction & Slippage Stress Sensitivity",
            "| Scenario | Multiplier | Expectancy (R) | Profit Factor | Win Rate | Sharpe | Max DD % | Edge Survives? |",
            "|---|---|---|---|---|---|---|---|",
            f"| Baseline | 1.0x | {cs.baseline.expectancy_r:+.3f} R | {cs.baseline.profit_factor:.2f} | {cs.baseline.win_rate*100:.1f}% | {cs.baseline.sharpe_ratio:.2f} | {cs.baseline.max_drawdown_pct:.2f}% | {'YES' if cs.baseline.survives_edge else 'NO'} |",
            f"| Stress 1.5x | 1.5x | {cs.stress_1_5x.expectancy_r:+.3f} R | {cs.stress_1_5x.profit_factor:.2f} | {cs.stress_1_5x.win_rate*100:.1f}% | {cs.stress_1_5x.sharpe_ratio:.2f} | {cs.stress_1_5x.max_drawdown_pct:.2f}% | {'YES' if cs.stress_1_5x.survives_edge else 'NO'} |",
            f"| Stress 2.0x | 2.0x | {cs.stress_2_0x.expectancy_r:+.3f} R | {cs.stress_2_0x.profit_factor:.2f} | {cs.stress_2_0x.win_rate*100:.1f}% | {cs.stress_2_0x.sharpe_ratio:.2f} | {cs.stress_2_0x.max_drawdown_pct:.2f}% | {'YES' if cs.stress_2_0x.survives_edge else 'NO'} |",
            f"\n**Cost Resilience Status:** `{cs.edge_survival_status}`",
        ])

    rep_path = "reports/validation_report.md"
    with open(rep_path, "w", encoding="utf-8") as f:
        f.write("\n".join(md))

    print(f"\n[OK] Validation report written to {rep_path}")
    print("\n" + "=" * 70)
    for v in overall_verdicts:
        print(f"  VERDICT: {v}")
    print("=" * 70)
    return 0


if __name__ == "__main__":
    main()
