"""
Unit Tests for Modular Strategy Pipeline (Stage 3).
Tests SignalModule, FilterModule, ExitModule, SizingModule, StrategyPipeline,
and LegacySMCStrategy zero-trades parity.
"""

import unittest
from datetime import datetime, timezone, timedelta

from src.strategies.base import (
    SignalModule, FilterModule, ExitModule, SizingModule, StrategyPipeline,
    StrategyContext, RawSignal, FilterResult, ExitPlan, SizingPlan
)
from src.strategies.legacy_adapter import (
    LegacySMCSignalModule, LegacySMCFilterModule, LegacySMCExitModule, LegacySMCSizingModule,
    create_legacy_smc_pipeline
)
from src.core.contracts import (
    Candle, OrderType, SetupType, SignalCandidate, SymbolInfo, AccountInfo, Position,
    TrendBias, Timeframe
)
from src.smc.strategy_engine import SmcStrategyEngine
from src.risk.position_sizing import PositionSizingEngine
from src.smc.mtf_analyzer import MultiTimeframeContext, TimeframeAnalysis


class DummySignalModule(SignalModule):
    def __init__(self, should_signal: bool = True):
        self.should_signal = should_signal

    def generate_signal(self, ctx: StrategyContext):
        if not self.should_signal:
            return None
        return RawSignal(
            symbol=ctx.symbol,
            direction=OrderType.BUY,
            entry_price=1.1000,
            setup_type=SetupType.SESSION_SWEEP,
            score=85.0,
            rationale="Test dummy",
            timestamp=datetime.now(timezone.utc)
        )


class DummyFilterModule(FilterModule):
    def __init__(self, should_pass: bool = True):
        self.should_pass = should_pass

    def filter_signal(self, signal: RawSignal, ctx: StrategyContext):
        if not self.should_pass:
            return FilterResult(passed=False, reason_code="DUMMY_FILTER", details="Filtered out")
        return FilterResult(passed=True, details="Passed")


class DummyExitModule(ExitModule):
    def compute_exits(self, signal: RawSignal, ctx: StrategyContext):
        return ExitPlan(
            stop_loss=signal.entry_price - 0.0020,
            take_profit=signal.entry_price + 0.0040,
            risk_reward_ratio=2.0
        )


class DummySizingModule(SizingModule):
    def __init__(self, should_pass: bool = True):
        self.should_pass = should_pass

    def compute_size(self, signal: RawSignal, exits: ExitPlan, ctx: StrategyContext):
        if not self.should_pass:
            return SizingPlan(valid=False, rejection_reason="Risk exceeds tier floor")
        return SizingPlan(
            valid=True,
            volume=0.10,
            risk_amount=50.0,
            risk_percent=1.0,
            margin_required=110.0
        )


class TestStrategyPipeline(unittest.TestCase):
    def setUp(self):
        self.ctx = StrategyContext(
            symbol="EURUSD.m",
            m15_candles=[
                Candle(time=datetime.now(timezone.utc), open=1.1000, high=1.1050, low=1.0990, close=1.1020)
            ],
            symbol_info=SymbolInfo(
                symbol="EURUSD.m", digits=5, point=0.00001, tick_size=0.00001, tick_value=1.0,
                contract_size=100000.0, min_lot=0.01, max_lot=50.0, lot_step=0.01, spread=10,
                ask=1.1001, bid=1.1000
            ),
            account_info=AccountInfo(balance=10000.0, equity=10000.0, free_margin=10000.0)
        )

    def test_pipeline_full_success(self):
        pipeline = StrategyPipeline(
            name="TestPipeline",
            signal_module=DummySignalModule(should_signal=True),
            filter_module=DummyFilterModule(should_pass=True),
            exit_module=DummyExitModule(),
            sizing_module=DummySizingModule(should_pass=True)
        )
        cand, filt = pipeline.evaluate(self.ctx)
        self.assertTrue(filt.passed)
        self.assertIsNotNone(cand)
        self.assertEqual(cand.symbol, "EURUSD.m")
        self.assertEqual(cand.direction, OrderType.BUY)
        self.assertEqual(cand.stop_loss, 1.0980)
        self.assertEqual(cand.take_profit, 1.1040)
        self.assertEqual(cand.risk_reward, 2.0)

    def test_pipeline_no_signal(self):
        pipeline = StrategyPipeline(
            name="TestPipeline",
            signal_module=DummySignalModule(should_signal=False),
            filter_module=DummyFilterModule(should_pass=True),
            exit_module=DummyExitModule(),
            sizing_module=DummySizingModule(should_pass=True)
        )
        cand, filt = pipeline.evaluate(self.ctx)
        self.assertFalse(filt.passed)
        self.assertIsNone(cand)
        self.assertEqual(filt.reason_code, "NO_RAW_SIGNAL")

    def test_pipeline_filter_rejection(self):
        pipeline = StrategyPipeline(
            name="TestPipeline",
            signal_module=DummySignalModule(should_signal=True),
            filter_module=DummyFilterModule(should_pass=False),
            exit_module=DummyExitModule(),
            sizing_module=DummySizingModule(should_pass=True)
        )
        cand, filt = pipeline.evaluate(self.ctx)
        self.assertFalse(filt.passed)
        self.assertIsNone(cand)
        self.assertEqual(filt.reason_code, "DUMMY_FILTER")

    def test_pipeline_sizing_rejection(self):
        pipeline = StrategyPipeline(
            name="TestPipeline",
            signal_module=DummySignalModule(should_signal=True),
            filter_module=DummyFilterModule(should_pass=True),
            exit_module=DummyExitModule(),
            sizing_module=DummySizingModule(should_pass=False)
        )
        cand, filt = pipeline.evaluate(self.ctx)
        self.assertFalse(filt.passed)
        self.assertIsNone(cand)
        self.assertEqual(filt.reason_code, "SIZING_REJECTED")

    def test_legacy_smc_adapter_parity(self):
        smc_engine = SmcStrategyEngine(min_score=75.0, min_rr=2.0)
        sizing_engine = PositionSizingEngine(default_risk_percent=1.0)
        pipeline = create_legacy_smc_pipeline(smc_engine, sizing_engine, require_scalp_htf_agreement=True)

        # Build dummy MTF context with conflict
        tf_analysis = TimeframeAnalysis(timeframe=Timeframe.H4, trend=TrendBias.STRONG_BULLISH, last_close=1.10)
        tf_bearish = TimeframeAnalysis(timeframe=Timeframe.H1, trend=TrendBias.STRONG_BEARISH, last_close=1.10)
        ctx = StrategyContext(
            symbol="EURUSD.m",
            m15_candles=self.ctx.m15_candles,
            mtf_ctx=MultiTimeframeContext(
                symbol="EURUSD.m",
                timestamp=datetime.now(timezone.utc),
                w1=tf_analysis,
                d1=tf_analysis,
                h4=tf_analysis,
                h1=tf_bearish,  # Conflict: H1 bearish vs H4 bullish
                m15=tf_analysis,
                overall_bias=TrendBias.RANGING
            ),
            symbol_info=self.ctx.symbol_info,
            account_info=self.ctx.account_info
        )

        cand, filt = pipeline.evaluate(ctx)
        # Should be None because no SMC setup on 1 dummy candle
        self.assertIsNone(cand)


if __name__ == "__main__":
    unittest.main()
