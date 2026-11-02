"""
Regime Detector Controller.
Coordinates feature extraction, model inference, shadow-mode auditing,
and evaluates whether regimes differ in out-of-sample expectancy.
"""

from __future__ import annotations
import os
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import List, Dict, Optional, Tuple, Any
import numpy as np

from src.core.contracts import Candle, Timeframe
from .features import RegimeFeatureExtractor, RegimeFeatures
from .models import GaussianRegimeClassifier, RegimeType, RegimeSuggestion, RegimeInferenceResult

logger = logging.getLogger("RegimeDetector")

STRATEGY_CONFIG_PATH = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "config", "strategy_config.json")
)


@dataclass
class RegimeShadowLogEntry:
    timestamp: str
    symbol: str
    regime: str
    shadow_suggestion: str
    confidence: float
    actual_decision: str
    executed_order_type: Optional[str]
    pnl_r: Optional[float] = None


class RegimeDetector:
    def __init__(
        self,
        config_path: str = STRATEGY_CONFIG_PATH,
        extractor: Optional[RegimeFeatureExtractor] = None,
        classifier: Optional[GaussianRegimeClassifier] = None
    ):
        self.config_path = config_path
        self.extractor = extractor or RegimeFeatureExtractor()
        self.classifier = classifier or GaussianRegimeClassifier()
        self.enabled: bool = False
        self.shadow_mode: bool = True
        self.shadow_logs: List[RegimeShadowLogEntry] = []
        self.reload_config()

    def reload_config(self) -> None:
        """Reads feature flags: regime_detection and shadow_mode."""
        if os.path.exists(self.config_path):
            try:
                with open(self.config_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                flags = data.get("feature_flags", {})
                self.enabled = bool(flags.get("regime_detection", False))
                self.shadow_mode = bool(flags.get("shadow_mode", True))
            except Exception as e:
                logger.warning(f"Could not load feature flags from {self.config_path}: {e}")

    def evaluate_closed_bar(
        self,
        symbol: str,
        candles_m15: List[Candle],
        candles_h1: Optional[List[Candle]] = None,
        actual_bot_decision: str = "WAIT",
        executed_order_type: Optional[str] = None
    ) -> Optional[RegimeInferenceResult]:
        """
        Extracts features and predicts regime for the most recent closed bar.
        In shadow mode, logs recommendation without altering the order.
        """
        self.reload_config()

        features = self.extractor.extract_features(candles_m15, candles_h1)
        if features is None:
            return None

        inference = self.classifier.predict(features)

        # Shadow mode logging (Safety Rule 3)
        if self.shadow_mode or not self.enabled:
            log_entry = RegimeShadowLogEntry(
                timestamp=features.timestamp_iso,
                symbol=symbol,
                regime=inference.regime.value,
                shadow_suggestion=inference.suggestion.value,
                confidence=inference.confidence,
                actual_decision=actual_bot_decision,
                executed_order_type=executed_order_type
            )
            self.shadow_logs.append(log_entry)
            logger.info(
                f"[SHADOW_REGIME] {symbol} | Regime: {inference.regime.value} | "
                f"Suggestion: {inference.suggestion.value} ({inference.confidence:.1%}) | "
                f"Bot Action: {actual_bot_decision} | Active: {self.enabled}"
            )

        return inference

    def compute_regime_expectancy_stats(self) -> Dict[str, Dict[str, Any]]:
        """
        Compares out-of-sample expectancy across regime classifications
        to demonstrate whether regimes differ in statistical outcome.
        """
        entries_with_pnl = [e for e in self.shadow_logs if e.pnl_r is not None]
        if not entries_with_pnl:
            return {
                r.value: {"trades": 0, "expectancy_r": 0.0, "win_rate": 0.0}
                for r in RegimeType
            }

        grouped: Dict[str, List[float]] = {r.value: [] for r in RegimeType}
        for e in entries_with_pnl:
            if e.regime in grouped:
                grouped[e.regime].append(e.pnl_r)

        summary = {}
        for r_name, pnl_list in grouped.items():
            if pnl_list:
                arr = np.array(pnl_list)
                wins = arr[arr > 0]
                summary[r_name] = {
                    "trades": len(arr),
                    "expectancy_r": round(float(np.mean(arr)), 3),
                    "win_rate": round(float(len(wins) / len(arr)), 3),
                    "median_r": round(float(np.median(arr)), 3)
                }
            else:
                summary[r_name] = {"trades": 0, "expectancy_r": 0.0, "win_rate": 0.0, "median_r": 0.0}

        return summary
