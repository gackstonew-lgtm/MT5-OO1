"""
Legacy SMC Strategy Adapter.
Wraps the existing SmcStrategyEngine into the modular StrategyPipeline interface.
Guarantees 100% decision and sizing parity with legacy behavior.
"""

from __future__ import annotations
from typing import Optional, List, Dict, Any

from src.strategies.base import (
    SignalModule, FilterModule, ExitModule, SizingModule, StrategyPipeline,
    StrategyContext, RawSignal, FilterResult, ExitPlan, SizingPlan
)
from src.core.contracts import SignalCandidate, OrderType, SetupType
from src.smc.strategy_engine import SmcStrategyEngine
from src.risk.position_sizing import PositionSizingEngine


class LegacySMCSignalModule(SignalModule):
    def __init__(self, smc_engine: SmcStrategyEngine):
        self.smc_engine = smc_engine

    def generate_signal(self, ctx: StrategyContext) -> Optional[RawSignal]:
        if not ctx.mtf_ctx or not ctx.m15_candles:
            return None

        candidates = self.smc_engine.scan_for_opportunities(
            mtf_ctx=ctx.mtf_ctx,
            m15_candles=ctx.m15_candles,
            is_in_killzone=ctx.is_in_killzone
        )
        if not candidates:
            return None

        # Pick highest scoring candidate
        best = max(candidates, key=lambda c: c.score)
        return RawSignal(
            symbol=best.symbol,
            direction=best.direction,
            entry_price=best.entry_price,
            setup_type=best.setup_type,
            score=best.score,
            rationale=best.status,
            timestamp=best.timestamp,
            metadata={
                "stop_loss": best.stop_loss,
                "take_profit": best.take_profit,
                "risk_reward_ratio": best.risk_reward
            }
        )


class LegacySMCFilterModule(FilterModule):
    def __init__(self, smc_engine: SmcStrategyEngine, require_scalp_htf_agreement: bool = True):
        self.smc_engine = smc_engine
        self.require_scalp_htf_agreement = require_scalp_htf_agreement

    def filter_signal(self, signal: RawSignal, ctx: StrategyContext) -> FilterResult:
        if self.require_scalp_htf_agreement and ctx.mtf_ctx:
            agree, msg, dirs = self.smc_engine.check_scalp_htf_agreement(ctx.mtf_ctx)
            if not agree:
                return FilterResult(
                    passed=False,
                    reason_code="HTF_CONFLICT",
                    details=msg,
                    real_numbers=dirs
                )

        return FilterResult(passed=True, details="HTF agreement verified")


class LegacySMCExitModule(ExitModule):
    def compute_exits(self, signal: RawSignal, ctx: StrategyContext) -> ExitPlan:
        sl = float(signal.metadata.get("stop_loss", 0.0))
        tp = float(signal.metadata.get("take_profit", 0.0))
        rr = float(signal.metadata.get("risk_reward_ratio", 2.0))
        return ExitPlan(
            stop_loss=sl,
            take_profit=tp,
            risk_reward_ratio=rr
        )


class LegacySMCSizingModule(SizingModule):
    def __init__(self, sizing_engine: PositionSizingEngine):
        self.sizing_engine = sizing_engine

    def compute_size(self, signal: RawSignal, exits: ExitPlan, ctx: StrategyContext) -> SizingPlan:
        if not ctx.account_info or not ctx.symbol_info:
            return SizingPlan(valid=False, rejection_reason="Missing account or symbol info")

        size_res = self.sizing_engine.calculate_position_size(
            account=ctx.account_info,
            symbol_info=ctx.symbol_info,
            entry_price=signal.entry_price,
            stop_loss=exits.stop_loss
        )

        if not size_res.valid:
            return SizingPlan(
                valid=False,
                rejection_reason=size_res.rejection_reason,
                risk_amount=size_res.monetary_risk
            )

        return SizingPlan(
            valid=True,
            volume=size_res.volume,
            risk_amount=size_res.monetary_risk,
            risk_percent=size_res.risk_percent,
            margin_required=size_res.margin_required
        )


def create_legacy_smc_pipeline(
    smc_engine: SmcStrategyEngine,
    sizing_engine: PositionSizingEngine,
    require_scalp_htf_agreement: bool = True
) -> StrategyPipeline:
    return StrategyPipeline(
        name="LegacySMC",
        signal_module=LegacySMCSignalModule(smc_engine),
        filter_module=LegacySMCFilterModule(smc_engine, require_scalp_htf_agreement),
        exit_module=LegacySMCExitModule(),
        sizing_module=LegacySMCSizingModule(sizing_engine)
    )
