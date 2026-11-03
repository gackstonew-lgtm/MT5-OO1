"""
Monte Carlo "Time Machine" Replay Gate.
Simulates TradePlan execution across thousands of block-bootstrapped M15 price paths
resampled from the symbol's recent sessions to evaluate empirical P(target), P(stop),
expected R, and confidence intervals before committing capital.
"""

from __future__ import annotations
import math
import time
import logging
from dataclasses import dataclass
from typing import List, Optional, Tuple, Any
import numpy as np

from src.core.contracts import TradePlan, Candle, OrderType

logger = logging.getLogger("ReplayGate")


@dataclass
class ReplayGateResult:
    passed: bool
    prob_target: float
    prob_stop: float
    prob_neither: float
    expected_r: float
    ci_lower: float
    ci_upper: float
    runtime_ms: float
    simulations_count: int
    rejection_reason: Optional[str] = None
    log_line: str = ""


class MonteCarloReplayGate:
    """
    Vectorized Monte Carlo path-simulation engine.
    Resamples blocks of M15 price changes from recent closed bars to preserve
    volatility clustering, auto-correlation, and tail risk.
    """

    def __init__(
        self,
        n_simulations: int = 10000,
        block_size: int = 8,
        max_horizon_bars: int = 32,
        min_edge: float = 0.05,
        min_expected_r: float = 0.15,
        min_sample_bars: int = 100,
        enabled: bool = True
    ):
        self.n_simulations = n_simulations
        self.block_size = block_size
        self.max_horizon_bars = max_horizon_bars
        self.min_edge = min_edge
        self.min_expected_r = min_expected_r
        self.min_sample_bars = min_sample_bars
        self.enabled = enabled

    def evaluate_plan(
        self,
        plan: TradePlan,
        recent_candles: List[Candle],
        spread_pts: float = 0.0,
        point: float = 0.00001,
        commission_per_lot: float = 7.0
    ) -> ReplayGateResult:
        """
        Executes block-bootstrap path simulations for the given TradePlan.
        Returns ReplayGateResult with empirical probabilities and edge gating.
        """
        start_t = time.perf_counter()

        if not self.enabled:
            elapsed_ms = (time.perf_counter() - start_t) * 1000.0
            return ReplayGateResult(
                passed=True,
                prob_target=1.0,
                prob_stop=0.0,
                prob_neither=0.0,
                expected_r=plan.reward_to_risk,
                ci_lower=plan.reward_to_risk,
                ci_upper=plan.reward_to_risk,
                runtime_ms=elapsed_ms,
                simulations_count=0,
                log_line=f"[REPLAY_GATE] BYPASS | Feature disabled in config."
            )

        if len(recent_candles) < self.min_sample_bars:
            elapsed_ms = (time.perf_counter() - start_t) * 1000.0
            reason = f"Replay sample too small (have {len(recent_candles)}, required {self.min_sample_bars})"
            log_line = f"[REPLAY_GATE] REJECTED | {plan.symbol} | Reason: {reason}"
            logger.info(log_line)
            return ReplayGateResult(
                passed=False,
                prob_target=0.0,
                prob_stop=0.0,
                prob_neither=1.0,
                expected_r=0.0,
                ci_lower=0.0,
                ci_upper=0.0,
                runtime_ms=elapsed_ms,
                simulations_count=0,
                rejection_reason=reason,
                log_line=log_line
            )

        # 1. Calculate price deltas from recent closed candles
        closes = np.array([c.close for c in recent_candles], dtype=np.float64)
        deltas = np.diff(closes)

        # 2. Extract overlapping blocks of length `block_size`
        k = self.block_size
        n_deltas = len(deltas)
        if n_deltas < k:
            elapsed_ms = (time.perf_counter() - start_t) * 1000.0
            reason = "Insufficient delta history for block bootstrap."
            return ReplayGateResult(
                passed=False, prob_target=0.0, prob_stop=0.0, prob_neither=1.0,
                expected_r=0.0, ci_lower=0.0, ci_upper=0.0, runtime_ms=elapsed_ms,
                simulations_count=0, rejection_reason=reason
            )

        # Build blocks matrix
        num_blocks = n_deltas - k + 1
        blocks = np.lib.stride_tricks.sliding_window_view(deltas, window_shape=k)

        # 3. Deterministic per-trade seed for 100% reproducibility
        seed_str = f"{plan.symbol}_{plan.entry_price:.5f}_{plan.stop_loss:.5f}_{plan.take_profit:.5f}"
        seed = abs(hash(seed_str)) % (2**31 - 1)
        rng = np.random.RandomState(seed)

        # 4. Generate bootstrap paths of length `max_horizon_bars`
        blocks_needed = math.ceil(self.max_horizon_bars / k)
        block_idx = rng.randint(0, num_blocks, size=(self.n_simulations, blocks_needed))
        sampled_blocks = blocks[block_idx]  # shape: (n_sims, blocks_needed, k)
        sampled_deltas = sampled_blocks.reshape(self.n_simulations, -1)[:, :self.max_horizon_bars]

        # Spread and commission drag adjustment in price
        drag_price = (spread_pts * point) + (commission_per_lot * point)

        # Cumulative price trajectories
        cum_deltas = np.cumsum(sampled_deltas, axis=1)

        is_buy = plan.direction in (OrderType.BUY, OrderType.BUY_LIMIT, OrderType.BUY_STOP)
        entry = plan.entry_price
        sl = plan.stop_loss
        tp = plan.take_profit
        sl_dist = abs(entry - sl)
        tp_dist = abs(tp - entry)

        if sl_dist <= 0:
            sl_dist = 0.0001

        if is_buy:
            # Trajectories start from entry
            paths = entry + cum_deltas
            hit_tp = paths >= (tp + drag_price)
            hit_sl = paths <= (sl - drag_price)
        else:
            paths = entry - cum_deltas
            hit_tp = paths <= (tp - drag_price)
            hit_sl = paths >= (sl + drag_price)

        # Find first bar where TP or SL is triggered
        tp_any = np.any(hit_tp, axis=1)
        sl_any = np.any(hit_sl, axis=1)

        # argmax gives index of first True
        tp_first_idx = np.where(tp_any, np.argmax(hit_tp, axis=1), 999999)
        sl_first_idx = np.where(sl_any, np.argmax(hit_sl, axis=1), 999999)

        # Event classification
        target_first = (tp_first_idx < sl_first_idx) & tp_any
        stop_first = (sl_first_idx < tp_first_idx) & sl_any
        neither = ~target_first & ~stop_first

        n_sims = float(self.n_simulations)
        p_target = float(np.sum(target_first)) / n_sims
        p_stop = float(np.sum(stop_first)) / n_sims
        p_neither = float(np.sum(neither)) / n_sims

        # Terminal R calculation per path
        r_outcomes = np.zeros(self.n_simulations, dtype=np.float64)
        target_r = tp_dist / sl_dist
        r_outcomes[target_first] = target_r
        r_outcomes[stop_first] = -1.0

        # For neither, compute unrealized R at horizon end
        final_price = paths[:, -1]
        if is_buy:
            unrealized_r = (final_price - entry) / sl_dist
        else:
            unrealized_r = (entry - final_price) / sl_dist
        r_outcomes[neither] = np.clip(unrealized_r[neither], -1.0, target_r)

        mean_r = float(np.mean(r_outcomes))
        std_r = float(np.std(r_outcomes, ddof=1)) if len(r_outcomes) > 1 else 0.0
        se_r = std_r / math.sqrt(n_sims)
        ci_lower = mean_r - 1.96 * se_r
        ci_upper = mean_r + 1.96 * se_r

        elapsed_ms = (time.perf_counter() - start_t) * 1000.0

        # Gating evaluation:
        # 1. P(target) must exceed P(stop) + min_edge (reject coin flips)
        # 2. Expected R must meet min_expected_r
        rejection_reason = None
        if p_target <= (p_stop + self.min_edge):
            rejection_reason = (
                f"Negative or near-coin-flip edge: P(target)={p_target:.2%} <= "
                f"P(stop)={p_stop:.2%} + edge_req={self.min_edge:.2%}"
            )
        elif mean_r < self.min_expected_r:
            rejection_reason = (
                f"Insufficient expected return: Expected R {mean_r:.2f} < "
                f"threshold {self.min_expected_r:.2f}"
            )

        passed = rejection_reason is None

        status_str = "APPROVED" if passed else "REJECTED"
        log_line = (
            f"[REPLAY_GATE] {status_str} | {plan.symbol} | "
            f"Sims: {self.n_simulations} | P(Target): {p_target:.2%} | "
            f"P(Stop): {p_stop:.2%} | P(Neither): {p_neither:.2%} | "
            f"ExpR: {mean_r:.2f} (95% CI: [{ci_lower:.2f}, {ci_upper:.2f}]) | "
            f"Time: {elapsed_ms:.1f}ms"
        )
        if rejection_reason:
            log_line += f" | Reason: {rejection_reason}"

        logger.info(log_line)

        return ReplayGateResult(
            passed=passed,
            prob_target=p_target,
            prob_stop=p_stop,
            prob_neither=p_neither,
            expected_r=mean_r,
            ci_lower=ci_lower,
            ci_upper=ci_upper,
            runtime_ms=elapsed_ms,
            simulations_count=self.n_simulations,
            rejection_reason=rejection_reason,
            log_line=log_line
        )
