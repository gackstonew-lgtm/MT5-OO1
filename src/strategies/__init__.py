"""
Strategies Package.
"""

from src.strategies.base import (
    SignalModule,
    FilterModule,
    ExitModule,
    SizingModule,
    StrategyPipeline,
    StrategyContext,
    RawSignal,
    FilterResult,
    ExitPlan,
    SizingPlan,
)
from src.strategies.legacy_adapter import (
    LegacySMCSignalModule,
    LegacySMCFilterModule,
    LegacySMCExitModule,
    LegacySMCSizingModule,
    create_legacy_smc_pipeline,
)

__all__ = [
    "SignalModule",
    "FilterModule",
    "ExitModule",
    "SizingModule",
    "StrategyPipeline",
    "StrategyContext",
    "RawSignal",
    "FilterResult",
    "ExitPlan",
    "SizingPlan",
    "LegacySMCSignalModule",
    "LegacySMCFilterModule",
    "LegacySMCExitModule",
    "LegacySMCSizingModule",
    "create_legacy_smc_pipeline",
]
