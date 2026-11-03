"""
Pattern Recognition Contracts and Pluggable Registry.
Defines strongly typed PatternResult models and a centralized detector registry.
"""

from __future__ import annotations
from enum import Enum
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional, Callable, Any, Tuple
import numpy as np


class PatternFamily(str, Enum):
    CANDLE = "CANDLE"
    CHART = "CHART"
    HARMONIC = "HARMONIC"


class PatternDirection(str, Enum):
    BULLISH = "BULLISH"
    BEARISH = "BEARISH"
    NEUTRAL = "NEUTRAL"


@dataclass(frozen=True)
class PatternResult:
    name: str
    family: PatternFamily
    direction: PatternDirection
    bars_used: int
    key_levels: Dict[str, float] = field(default_factory=dict)
    invalidation_price: float = 0.0
    measured_target: float = 0.0
    raw_strength: float = 1.0  # Normalized 0.0 to 1.0
    confirmed: bool = True
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    details: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "family": self.family.value,
            "direction": self.direction.value,
            "bars_used": self.bars_used,
            "key_levels": self.key_levels,
            "invalidation_price": round(self.invalidation_price, 5),
            "measured_target": round(self.measured_target, 5),
            "raw_strength": round(self.raw_strength, 2),
            "confirmed": self.confirmed,
            "timestamp": self.timestamp.isoformat(),
            "details": self.details
        }


@dataclass(frozen=True)
class ScoredPattern:
    pattern: PatternResult
    confluence_score: float
    passed_gate: bool
    direction: PatternDirection
    entry_price: float
    stop_loss: float
    take_profit: float
    risk_reward: float
    confluence_breakdown: Dict[str, float] = field(default_factory=dict)
    reasons: List[str] = field(default_factory=list)
    rejection_reasons: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "pattern": self.pattern.to_dict(),
            "confluence_score": round(self.confluence_score, 1),
            "passed_gate": self.passed_gate,
            "direction": self.direction.value,
            "entry_price": round(self.entry_price, 5),
            "stop_loss": round(self.stop_loss, 5),
            "take_profit": round(self.take_profit, 5),
            "risk_reward": round(self.risk_reward, 2),
            "confluence_breakdown": self.confluence_breakdown,
            "reasons": self.reasons,
            "rejection_reasons": self.rejection_reasons
        }


DetectorFunc = Callable[[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray, float, Dict[str, Any]], Optional[PatternResult]]


class PatternRegistry:
    """
    Centralized registry of pluggable pattern detectors.
    """
    _detectors: Dict[PatternFamily, Dict[str, DetectorFunc]] = {
        PatternFamily.CANDLE: {},
        PatternFamily.CHART: {},
        PatternFamily.HARMONIC: {}
    }

    @classmethod
    def register(cls, family: PatternFamily, name: str):
        def decorator(func: DetectorFunc):
            cls._detectors[family][name] = func
            return func
        return decorator

    @classmethod
    def get_detectors(cls, family: PatternFamily) -> Dict[str, DetectorFunc]:
        return dict(cls._detectors[family])

    @classmethod
    def list_patterns(cls, family: Optional[PatternFamily] = None) -> List[str]:
        if family:
            return list(cls._detectors[family].keys())
        all_patterns = []
        for fam_dict in cls._detectors.values():
            all_patterns.extend(fam_dict.keys())
        return all_patterns
