"""
Market Regime Detection Engine.
Extracts realized volatility, ATR percentiles, ADX, MTF slope, sessions, and spread ratios.
Fits Gaussian Markov and Bayesian Change-Point models on training windows,
emitting SCALP, HOLD, or NO_TRADE regime suggestions in shadow mode.
"""

from .features import RegimeFeatureExtractor, RegimeFeatures
from .models import RegimeType, RegimeSuggestion, GaussianRegimeClassifier
from .regime_detector import RegimeDetector

__all__ = [
    "RegimeFeatureExtractor",
    "RegimeFeatures",
    "RegimeType",
    "RegimeSuggestion",
    "GaussianRegimeClassifier",
    "RegimeDetector",
]
