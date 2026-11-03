"""
Modular Strategy Plug-in Architecture.
Defines SignalModule, FilterModule, ExitModule, SizingModule, and the coordinating StrategyPipeline.
"""

from __future__ import annotations
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple, Any

from src.core.contracts import (
    Candle, OrderType, SetupType, SignalCandidate, SymbolInfo, AccountInfo, Position, Timeframe
)
from src.smc.mtf_analyzer import MultiTimeframeContext


@dataclass
class StrategyContext:
    symbol: str
    m15_candles: List[Candle]
    h1_candles: List[Candle] = field(default_factory=list)
    h4_candles: List[Candle] = field(default_factory=list)
    d1_candles: List[Candle] = field(default_factory=list)
    w1_candles: List[Candle] = field(default_factory=list)
    mtf_ctx: Optional[MultiTimeframeContext] = None
    symbol_info: Optional[SymbolInfo] = None
    account_info: Optional[AccountInfo] = None
    active_positions: List[Position] = field(default_factory=list)
    is_in_killzone: bool = True
    session_name: str = "LONDON"
    eval_time: Optional[datetime] = None


@dataclass
class RawSignal:
    symbol: str
    direction: OrderType
    entry_price: float
    setup_type: SetupType
    score: float
    rationale: str
    timestamp: datetime
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class FilterResult:
    passed: bool
    reason_code: str = ""
    details: str = ""
    real_numbers: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ExitPlan:
    stop_loss: float
    take_profit: float
    risk_reward_ratio: float
    breakeven_trigger_rr: float = 1.0
    trailing_start_rr: float = 1.5


@dataclass
class SizingPlan:
    valid: bool
    volume: float = 0.0
    risk_amount: float = 0.0
    risk_percent: float = 0.0
    margin_required: float = 0.0
    rejection_reason: str = ""


class SignalModule(ABC):
    @abstractmethod
    def generate_signal(self, ctx: StrategyContext) -> Optional[RawSignal]:
        """Generates raw entry signal without position sizing or broker constraints."""
        pass


class FilterModule(ABC):
    @abstractmethod
    def filter_signal(self, signal: RawSignal, ctx: StrategyContext) -> FilterResult:
        """Applies HTF alignment, session, spread, and news filtering."""
        pass


class ExitModule(ABC):
    @abstractmethod
    def compute_exits(self, signal: RawSignal, ctx: StrategyContext) -> ExitPlan:
        """Determines dynamic or structural SL and TP levels."""
        pass


class SizingModule(ABC):
    @abstractmethod
    def compute_size(self, signal: RawSignal, exits: ExitPlan, ctx: StrategyContext) -> SizingPlan:
        """Computes risk-fitted volume within account tier ceilings and drawdown caps."""
        pass


class StrategyPipeline:
    """
    Coordinates Signal -> Filter -> Exit -> Sizing modules into a validated SignalCandidate.
    """

    def __init__(
        self,
        name: str,
        signal_module: SignalModule,
        filter_module: FilterModule,
        exit_module: ExitModule,
        sizing_module: SizingModule
    ):
        self.name = name
        self.signal_module = signal_module
        self.filter_module = filter_module
        self.exit_module = exit_module
        self.sizing_module = sizing_module

    def evaluate(self, ctx: StrategyContext) -> Tuple[Optional[SignalCandidate], FilterResult]:
        # 1. Generate Raw Signal
        raw = self.signal_module.generate_signal(ctx)
        if not raw:
            return None, FilterResult(passed=False, reason_code="NO_RAW_SIGNAL", details="No setup identified by signal module")

        # 2. Filter Signal
        filt = self.filter_module.filter_signal(raw, ctx)
        if not filt.passed:
            return None, filt

        # 3. Compute Exits
        exits = self.exit_module.compute_exits(raw, ctx)

        # 4. Compute Sizing
        sizing = self.sizing_module.compute_size(raw, exits, ctx)
        if not sizing.valid:
            return None, FilterResult(
                passed=False,
                reason_code="SIZING_REJECTED",
                details=sizing.rejection_reason,
                real_numbers={"risk_amount": sizing.risk_amount}
            )

        # Build approved SignalCandidate
        candidate = SignalCandidate(
            id=f"{self.name}_{raw.symbol}_{int(raw.timestamp.timestamp())}",
            symbol=raw.symbol,
            timeframe=Timeframe.M15,
            direction=raw.direction,
            setup_type=raw.setup_type,
            entry_price=raw.entry_price,
            stop_loss=exits.stop_loss,
            take_profit=exits.take_profit,
            risk_reward=exits.risk_reward_ratio,
            score=raw.score,
            status="APPROVED",
            timestamp=raw.timestamp
        )

        return candidate, FilterResult(passed=True, details="Setup approved")
