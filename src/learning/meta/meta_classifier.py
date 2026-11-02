"""
Meta-Labeling Runtime Controller.
Manages feature extraction, model inference, shadow auditing, and downstream risk gating.
"""

from __future__ import annotations
import os
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import List, Dict, Optional, Tuple, Any

from src.core.contracts import Candle, SignalCandidate, OrderType
from .features import MetaFeatureExtractor, MetaFeatureVector
from .model import MetaClassifierModel, ModelRegistryEntry

logger = logging.getLogger("MetaLabelingController")

STRATEGY_CONFIG_PATH = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "config", "strategy_config.json")
)


@dataclass
class MetaShadowAuditRecord:
    timestamp_utc: str
    symbol: str
    signal_id: str
    predicted_probability: float
    recommended_decision: str
    recommended_multiplier: float
    actual_decision: str
    actual_volume: float
    reason: str


class MetaLabelingController:
    def __init__(
        self,
        config_path: str = STRATEGY_CONFIG_PATH,
        model: Optional[MetaClassifierModel] = None,
        extractor: Optional[MetaFeatureExtractor] = None
    ):
        self.config_path = config_path
        self.model = model or MetaClassifierModel()
        self.extractor = extractor or MetaFeatureExtractor()
        self.enabled: bool = False
        self.shadow_mode: bool = True
        self.audit_records: List[MetaShadowAuditRecord] = []
        self.registry: List[ModelRegistryEntry] = []
        self.reload_config()

    def reload_config(self) -> None:
        """Loads meta_labeling and shadow_mode flags."""
        if os.path.exists(self.config_path):
            try:
                with open(self.config_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                flags = data.get("feature_flags", {})
                self.enabled = bool(flags.get("meta_labeling", False))
                self.shadow_mode = bool(flags.get("shadow_mode", True))
            except Exception as e:
                logger.warning(f"Failed to read feature flags from {self.config_path}: {e}")

    def evaluate_candidate(
        self,
        historical_candles: List[Candle],
        candidate: SignalCandidate,
        current_spread_pts: float = 10.0,
        actual_volume: float = 0.01,
        actual_decision: str = "APPROVED"
    ) -> Tuple[bool, float, str]:
        """
        Evaluates candidate setup using the Meta-Model.
        Returns: (proceed_with_trade: bool, risk_multiplier: float, explanation: str)
        In shadow mode, always returns (True, 1.0, shadow_info) so live orders are untouched!
        """
        self.reload_config()

        feats = self.extractor.extract_features_at_bar(
            historical_candles_up_to_signal=historical_candles,
            candidate=candidate,
            current_spread_pts=current_spread_pts
        )

        prob = self.model.predict_probability(feats)
        allow_trade, multiplier, explanation = self.model.evaluate_risk_scaling(prob)

        # Invariant: multiplier can NEVER exceed 1.0
        multiplier = min(1.0, max(0.0, multiplier))

        cand_time = getattr(candidate, 'timestamp', getattr(candidate, 'created_at', None))
        ts_str = cand_time.isoformat() if cand_time else datetime.now(timezone.utc).isoformat()

        audit = MetaShadowAuditRecord(
            timestamp_utc=ts_str,
            symbol=candidate.symbol,
            signal_id=candidate.id,
            predicted_probability=round(prob, 4),
            recommended_decision="TRADE" if allow_trade else "SKIP",
            recommended_multiplier=multiplier,
            actual_decision=actual_decision,
            actual_volume=actual_volume,
            reason=explanation
        )
        self.audit_records.append(audit)

        logger.info(
            f"[META_MODEL] {candidate.symbol} | Prob: {prob:.1%} | "
            f"Rec: {'ALLOW' if allow_trade else 'SKIP'} ({multiplier:.2f}x) | "
            f"Actual: {actual_decision} | Active: {self.enabled} | Shadow: {self.shadow_mode}"
        )

        # Safety Rule 2 & 3: If not promoted or in shadow mode, do not alter order!
        if self.shadow_mode or not self.enabled:
            return True, 1.0, f"[SHADOW_MODE] Predicted prob {prob:.1%}. Live order unadjusted."

        return allow_trade, multiplier, explanation
