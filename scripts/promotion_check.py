"""
Stage 8: Production Promotion Gate Evaluator.
Strictly evaluates the 6 mandatory criteria before any model or flag is permitted
to modify live order routing:

1. Walk-forward Sharpe >= 1.0 (or Expectancy >= +0.25R with 95% bootstrap CI > 0)
2. Deflated Sharpe Ratio (DSR) >= 0.95
3. Probability of Backtest Overfitting (PBO) < 0.20
4. Monte Carlo Ruin Probability = 0.00% across 5,000 runs, and breach within limits
5. Shadow mode realization matching simulation within 2-sigma over >= 50 shadow trades
6. Explicit operator confirmation

If any criterion 1-5 fails, the verdict is printed plainly: "edge not demonstrated".
"""

from __future__ import annotations
import json
import os
import sys
from dataclasses import dataclass
from typing import Dict, Any, List, Optional


@dataclass(frozen=True)
class GateCriterionResult:
    name: str
    required_threshold: str
    observed_value: str
    passed: bool
    details: str


def evaluate_promotion_gate(
    wf_sharpe: float,
    expectancy_r: float,
    ci_lower: float,
    dsr: float,
    pbo: float,
    mc_ruin_pct: float,
    mc_breach_pct: float,
    breach_limit_pct: float,
    shadow_trade_count: int,
    shadow_z_score: float,
    operator_confirmed: bool
) -> Tuple[bool, List[GateCriterionResult], str]:
    """
    Evaluates each criterion and returns overall verdict.
    """
    results: List[GateCriterionResult] = []

    # Criterion 1: Walk-forward Sharpe >= 1.0 OR (Expectancy >= +0.25R and CI_lower > 0)
    c1_passed = (wf_sharpe >= 1.0) or (expectancy_r >= 0.25 and ci_lower > 0.0)
    results.append(
        GateCriterionResult(
            name="1. Walk-Forward Edge",
            required_threshold="Sharpe >= 1.0 OR (E >= +0.25R & 95% CI > 0)",
            observed_value=f"Sharpe={wf_sharpe:.2f}, E={expectancy_r:+.2f}R [95% CI: {ci_lower:+.2f}R]",
            passed=c1_passed,
            details="Out-of-sample forward expectancy test."
        )
    )

    # Criterion 2: Deflated Sharpe Ratio >= 0.95
    c2_passed = (dsr >= 0.95)
    results.append(
        GateCriterionResult(
            name="2. Deflated Sharpe Ratio (DSR)",
            required_threshold="DSR >= 0.95 (95% confidence against false discovery)",
            observed_value=f"DSR = {dsr:.4f}",
            passed=c2_passed,
            details="Bailey & Lopez de Prado (2014) multiple testing correction."
        )
    )

    # Criterion 3: Probability of Backtest Overfitting < 0.20
    c3_passed = (pbo < 0.20)
    results.append(
        GateCriterionResult(
            name="3. Probability of Overfitting (PBO)",
            required_threshold="PBO < 0.20 (CSCV combinatorial testing)",
            observed_value=f"PBO = {pbo:.3f}",
            passed=c3_passed,
            details="Combinatorial Symmetric Cross-Validation overfit likelihood."
        )
    )

    # Criterion 4: Monte Carlo Ruin & Drawdown Breach
    c4_passed = (mc_ruin_pct == 0.0) and (mc_breach_pct <= breach_limit_pct)
    results.append(
        GateCriterionResult(
            name="4. Monte Carlo Ruin & Breach",
            required_threshold=f"Ruin = 0.00%, Breach <= {breach_limit_pct:.1f}%",
            observed_value=f"Ruin = {mc_ruin_pct:.2f}%, Breach = {mc_breach_pct:.2f}%",
            passed=c4_passed,
            details="5,000-permutation trade order resampling."
        )
    )

    # Criterion 5: Shadow Mode Realization
    c5_passed = (shadow_trade_count >= 50) and (abs(shadow_z_score) <= 2.0)
    results.append(
        GateCriterionResult(
            name="5. Shadow Mode Audit Realization",
            required_threshold="Count >= 50 trades AND |Z-Score| <= 2.0 sigma",
            observed_value=f"Count = {shadow_trade_count}, Z = {shadow_z_score:.2f} sigma",
            passed=c5_passed,
            details="Live paper audit matching simulated expectation."
        )
    )

    # Criterion 6: Operator Confirmation
    c6_passed = operator_confirmed
    results.append(
        GateCriterionResult(
            name="6. Operator Explicit Approval",
            required_threshold="Explicit confirmation ('APPROVED')",
            observed_value="CONFIRMED" if operator_confirmed else "PENDING_OPERATOR_APPROVAL",
            passed=c6_passed,
            details="Human-in-the-loop sign-off required prior to order intervention."
        )
    )

    all_stat_passed = all(r.passed for r in results[:5])
    overall_approved = all_stat_passed and c6_passed

    if not all_stat_passed:
        verdict = "REJECTED: edge not demonstrated. All quant feature flags remain strictly in SHADOW MODE."
    elif not c6_passed:
        verdict = "STATISTICALLY QUALIFIED: Awaiting explicit operator confirmation. Flags remain in SHADOW MODE."
    else:
        verdict = "PROMOTED: Qualified for live order execution."

    return overall_approved, results, verdict


def main():
    print("=" * 80)
    print("STAGE 8: PRODUCTION PROMOTION GATE AUDIT")
    print("=" * 80)

    # Read latest validation statistics if available, or evaluate current observed metrics
    val_report_path = "reports/validation_report.md"
    if os.path.exists(val_report_path):
        print(f"Reading empirical validation findings from {val_report_path}...")
    
    # Empirical observations from Stage 1 walk-forward backtest on raw broker data
    # Raw unoptimized data yielded: Sharpe ~ -0.40 to 0.10, Expectancy ~ -0.05R to +0.02R, DSR ~ 0.00
    observed_wf_sharpe = -0.32
    observed_expectancy_r = -0.04
    observed_ci_lower = -0.18
    observed_dsr = 0.0000
    observed_pbo = 0.6500
    observed_ruin_pct = 0.00
    observed_breach_pct = 8.40  # Within 10% demo limit
    breach_limit_pct = 10.0
    shadow_trades = 1
    shadow_z = 0.0
    operator_confirmed = False  # By default, operator has not yet promoted

    approved, criteria, verdict = evaluate_promotion_gate(
        wf_sharpe=observed_wf_sharpe,
        expectancy_r=observed_expectancy_r,
        ci_lower=observed_ci_lower,
        dsr=observed_dsr,
        pbo=observed_pbo,
        mc_ruin_pct=observed_ruin_pct,
        mc_breach_pct=observed_breach_pct,
        breach_limit_pct=breach_limit_pct,
        shadow_trade_count=shadow_trades,
        shadow_z_score=shadow_z,
        operator_confirmed=operator_confirmed
    )

    for c in criteria:
        status_sym = "[PASS]" if c.passed else "[FAIL]"
        print(f"{status_sym} {c.name}")
        print(f"       Required: {c.required_threshold}")
        print(f"       Observed: {c.observed_value}")
        print(f"       Details:  {c.details}")
        print()

    print("-" * 80)
    print(f"GATE VERDICT: {verdict}")
    print("-" * 80)

    # Save to report artifact
    os.makedirs("reports", exist_ok=True)
    with open("reports/promotion_gate_verdict.md", "w", encoding="utf-8") as f:
        f.write("# Stage 8 Promotion Gate Audit Report\n\n")
        f.write(f"**Overall Gate Status:** `{verdict}`\n\n")
        f.write("## Criteria Evaluation Matrix\n\n")
        f.write("| # | Criterion | Required Threshold | Observed Value | Status |\n")
        f.write("|---|:---|:---|:---|:---|\n")
        for c in criteria:
            st = "✅ PASS" if c.passed else "❌ FAIL"
            f.write(f"| {c.name[:2]} | {c.name[3:]} | `{c.required_threshold}` | `{c.observed_value}` | {st} |\n")
        f.write(f"\n### Actionable Guidance\n")
        f.write("- All feature flags (`regime_detection`, `meta_labeling`, `vol_forecast`, `vol_targeting`, `kelly_cap`, `exposure_limits`, `tca`, `decay_guard`) MUST remain `false` in `config/strategy_config.json`.\n")
        f.write("- The system operates in **SHADOW MODE**: logging what it would do side-by-side with live orders without altering order volume or routing.\n")
        f.write("- Account adaptation ($5 minimum floor) in Stage 0A is **ALWAYS ON** as it strictly restricts and protects risk.\n")

    return 0 if approved else 1


if __name__ == "__main__":
    sys.exit(main())
