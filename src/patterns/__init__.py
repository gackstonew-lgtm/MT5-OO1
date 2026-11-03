"""
Institutional Pattern Recognition Engine.
Exposes Candlestick, Chart, and Harmonic detectors with ATR normalization,
swing detection, confluence scoring, and execution integration.
"""

from src.patterns.contracts import (
    PatternResult,
    PatternFamily,
    PatternDirection,
    PatternRegistry,
    DetectorFunc,
    ScoredPattern
)
from src.patterns.candlesticks import (
    CandlestickEngine,
    compute_atr,
    compute_prior_trend
)
from src.patterns.chart_patterns import (
    ChartPatternEngine,
    extract_swings
)
from src.patterns.context import (
    PatternContextEngine
)

__all__ = [
    "PatternResult",
    "PatternFamily",
    "PatternDirection",
    "PatternRegistry",
    "DetectorFunc",
    "ScoredPattern",
    "CandlestickEngine",
    "ChartPatternEngine",
    "PatternContextEngine",
    "compute_atr",
    "compute_prior_trend",
    "extract_swings",
]
