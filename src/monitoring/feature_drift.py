"""
Feature Drift Detection Engine.
Calculates Population Stability Index (PSI) and Kolmogorov-Smirnov (KS) 2-Sample Test
on market features (spread, ATR, ADX) over recent bars versus training baseline.
"""

from __future__ import annotations
import math
from dataclasses import dataclass
from typing import Dict, List, Tuple
import numpy as np


@dataclass(frozen=True)
class FeatureDriftReport:
    feature_name: str
    psi_score: float
    ks_statistic: float
    drift_level: str  # 'STABLE', 'MODERATE_SHIFT', 'SIGNIFICANT_DRIFT'
    description: str


class FeatureDriftDetector:
    def __init__(self, num_bins: int = 10):
        self.num_bins = num_bins

    def calculate_psi(self, baseline: np.ndarray, current: np.ndarray) -> float:
        """
        Calculates Population Stability Index (PSI):
        PSI = sum((A_i - E_i) * ln(A_i / E_i))
        """
        if len(baseline) < 20 or len(current) < 20:
            return 0.0

        # Create quantiles from baseline
        quantiles = np.linspace(0, 100, self.num_bins + 1)
        bin_edges = np.percentile(baseline, quantiles)
        # Ensure distinct bin edges
        bin_edges[0] = -np.inf
        bin_edges[-1] = np.inf

        base_counts, _ = np.histogram(baseline, bins=bin_edges)
        curr_counts, _ = np.histogram(current, bins=bin_edges)

        # Convert to proportions with Laplace smoothing
        eps = 1e-4
        base_props = (base_counts + eps) / (len(baseline) + eps * self.num_bins)
        curr_props = (curr_counts + eps) / (len(current) + eps * self.num_bins)

        # PSI sum
        psi = np.sum((curr_props - base_props) * np.log(curr_props / base_props))
        return max(0.0, float(psi))

    def calculate_ks_2sample(self, baseline: np.ndarray, current: np.ndarray) -> float:
        """
        Calculates two-sample Kolmogorov-Smirnov statistic D = sup_x |F_1(x) - F_2(x)|.
        """
        if len(baseline) < 5 or len(current) < 5:
            return 0.0

        data1 = np.sort(baseline)
        data2 = np.sort(current)
        n1 = len(data1)
        n2 = len(data2)

        data_all = np.concatenate([data1, data2])
        cdf1 = np.searchsorted(data1, data_all, side='right') / n1
        cdf2 = np.searchsorted(data2, data_all, side='right') / n2

        d_stat = np.max(np.abs(cdf1 - cdf2))
        return float(d_stat)

    def evaluate_feature(
        self,
        feature_name: str,
        baseline_samples: List[float],
        recent_samples: List[float]
    ) -> FeatureDriftReport:
        """
        Compares recent samples (e.g. last 100 bars) with training baseline.
        """
        b_arr = np.array(baseline_samples, dtype=np.float64)
        c_arr = np.array(recent_samples, dtype=np.float64)

        psi = self.calculate_psi(b_arr, c_arr)
        ks = self.calculate_ks_2sample(b_arr, c_arr)

        if psi >= 0.25:
            drift = "SIGNIFICANT_DRIFT"
            desc = f"Significant regime drift detected on {feature_name} (PSI={psi:.3f} >= 0.25). Model recalibration advised."
        elif psi >= 0.10:
            drift = "MODERATE_SHIFT"
            desc = f"Moderate distribution shift on {feature_name} (PSI={psi:.3f}). Monitoring stability."
        else:
            drift = "STABLE"
            desc = f"{feature_name} distribution is stable (PSI={psi:.3f} < 0.10)."

        return FeatureDriftReport(
            feature_name=feature_name,
            psi_score=round(psi, 4),
            ks_statistic=round(ks, 4),
            drift_level=drift,
            description=desc
        )
