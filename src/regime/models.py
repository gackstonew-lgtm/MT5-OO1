"""
Probabilistic Regime Classifier Models.
Implements Gaussian Markov State and Bayesian Change-Point estimation,
mapping multidimensional feature distributions to SCALP, HOLD, or NO_TRADE suggestions.
"""

from __future__ import annotations
import math
from dataclasses import dataclass
from enum import Enum
from typing import List, Dict, Optional, Tuple, Any
import numpy as np

from .features import RegimeFeatures


class RegimeType(str, Enum):
    TRENDING_LOW_VOL = "TRENDING_LOW_VOL"
    TRENDING_HIGH_VOL = "TRENDING_HIGH_VOL"
    RANGING_LOW_VOL = "RANGING_LOW_VOL"
    EXTREME_VOL_CHOP = "EXTREME_VOL_CHOP"


class RegimeSuggestion(str, Enum):
    HOLD = "HOLD"
    SCALP = "SCALP"
    NO_TRADE = "NO_TRADE"


@dataclass(frozen=True)
class RegimeInferenceResult:
    regime: RegimeType
    suggestion: RegimeSuggestion
    confidence: float
    feature_summary: Dict[str, float]
    rationale: str


class GaussianRegimeClassifier:
    def __init__(self, n_states: int = 4):
        self.n_states = n_states
        self.is_fitted = False
        # Fitted centroid parameters for the 4 states
        # [realized_vol, atr_pct, adx/100, di_diff/100, trend_slope, session, spread_ratio]
        self.centroids: Dict[RegimeType, np.ndarray] = {}
        self.cov_diags: Dict[RegimeType, np.ndarray] = {}

    def fit_on_training_window(self, feature_list: List[RegimeFeatures]) -> None:
        """
        Fits state distribution parameters strictly on in-sample training window.
        Uses k-means style expectation clustering partitioned into canonical regime semantics.
        """
        if len(feature_list) < 20:
            self._init_default_priors()
            self.is_fitted = True
            return

        X = np.array([f.to_array() for f in feature_list])

        # Feature order:
        # 0: realized_vol, 1: atr_pct, 2: adx/100, 3: di_diff/100, 4: trend_slope, 5: session/4, 6: spread_ratio
        vol_median = np.median(X[:, 0])
        adx_median = np.median(X[:, 2])

        # Cluster masks
        mask_trend_low = (X[:, 2] >= adx_median) & (X[:, 0] < vol_median)
        mask_trend_high = (X[:, 2] >= adx_median) & (X[:, 0] >= vol_median)
        mask_range_low = (X[:, 2] < adx_median) & (X[:, 0] < vol_median)
        mask_chop = (X[:, 2] < adx_median) & (X[:, 0] >= vol_median)

        masks = {
            RegimeType.TRENDING_LOW_VOL: mask_trend_low,
            RegimeType.TRENDING_HIGH_VOL: mask_trend_high,
            RegimeType.RANGING_LOW_VOL: mask_range_low,
            RegimeType.EXTREME_VOL_CHOP: mask_chop,
        }

        for r_type, m in masks.items():
            if np.sum(m) >= 3:
                self.centroids[r_type] = np.mean(X[m], axis=0)
                self.cov_diags[r_type] = np.var(X[m], axis=0) + 1e-4
            else:
                self._set_default_prior_for_regime(r_type)

        self.is_fitted = True

    def _init_default_priors(self) -> None:
        for r in RegimeType:
            self._set_default_prior_for_regime(r)

    def _set_default_prior_for_regime(self, r_type: RegimeType) -> None:
        if r_type == RegimeType.TRENDING_LOW_VOL:
            self.centroids[r_type] = np.array([0.005, 0.40, 0.35, 0.15, 0.8, 0.5, 1.0])
        elif r_type == RegimeType.TRENDING_HIGH_VOL:
            self.centroids[r_type] = np.array([0.020, 0.80, 0.45, 0.25, 1.5, 0.6, 1.2])
        elif r_type == RegimeType.RANGING_LOW_VOL:
            self.centroids[r_type] = np.array([0.004, 0.25, 0.15, 0.00, 0.0, 0.3, 0.9])
        else:  # EXTREME_VOL_CHOP
            self.centroids[r_type] = np.array([0.035, 0.95, 0.12, -0.05, 0.1, 0.8, 2.2])
        self.cov_diags[r_type] = np.array([0.001, 0.05, 0.02, 0.02, 0.2, 0.1, 0.2])

    def predict(self, features: RegimeFeatures) -> RegimeInferenceResult:
        """Computes posterior probability over regimes given feature vector."""
        if not self.is_fitted:
            self._init_default_priors()
            self.is_fitted = True

        x = features.to_array()
        log_likelihoods: Dict[RegimeType, float] = {}

        for r_type, mu in self.centroids.items():
            var = self.cov_diags[r_type]
            diff = x - mu
            # Gaussian log-density diagonal approximation
            ll = -0.5 * np.sum((diff ** 2) / var + np.log(2.0 * math.pi * var))
            log_likelihoods[r_type] = float(ll)

        # Softmax normalization
        max_ll = max(log_likelihoods.values())
        exp_ll = {r: math.exp(ll - max_ll) for r, ll in log_likelihoods.items()}
        sum_exp = sum(exp_ll.values())
        posteriors = {r: exp_ll[r] / sum_exp for r in RegimeType}

        best_regime = max(posteriors.keys(), key=lambda k: posteriors[k])
        confidence = posteriors[best_regime]

        # Map to operational suggestion
        if best_regime == RegimeType.TRENDING_LOW_VOL:
            sugg = RegimeSuggestion.HOLD
            rat = f"Sustained trend structure with controlled volatility (Confidence: {confidence:.1%})."
        elif best_regime in [RegimeType.TRENDING_HIGH_VOL, RegimeType.RANGING_LOW_VOL]:
            sugg = RegimeSuggestion.SCALP
            rat = f"Range or high-momentum dispersion favor rapid structural scalp targets (Confidence: {confidence:.1%})."
        else:
            sugg = RegimeSuggestion.NO_TRADE
            rat = f"Elevated noise / chop ratio exceeds safety bounds (Confidence: {confidence:.1%})."

        return RegimeInferenceResult(
            regime=best_regime,
            suggestion=sugg,
            confidence=round(confidence, 3),
            feature_summary={
                "realized_vol": round(features.realized_volatility, 4),
                "atr_pct": round(features.atr_percentile, 3),
                "adx": round(features.adx, 1),
                "trend_slope": round(features.trend_slope, 2),
                "spread_ratio": round(features.spread_ratio, 2),
            },
            rationale=rat
        )
