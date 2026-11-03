"""
Unit Tests for Pattern Context and Confluence Engine.
Tests scoring rubric, opposing signal conflict rejection, overlap de-duplication,
and gating logic.
"""

import unittest
from datetime import datetime, timezone
from dataclasses import dataclass

from src.core.contracts import Candle
from src.patterns.contracts import (
    PatternResult, PatternFamily, PatternDirection, ScoredPattern
)
from src.patterns.context import PatternContextEngine


@dataclass
class MockOB:
    high: float
    low: float
    direction: str = "BUY"


@dataclass
class MockFVG:
    top: float
    bottom: float
    direction: str = "BUY"


class TestPatternContext(unittest.TestCase):
    def setUp(self):
        self.context_engine = PatternContextEngine({
            "min_confluence_score": 65.0,
            "require_confirmation_close": True,
            "max_stacked_patterns": 2,
            "confluence_weights": {
                "location_poi_ob_fvg": 30.0,
                "htf_trend_alignment": 25.0,
                "structure_bos_confirmed": 20.0,
                "follow_through_confirmation": 10.0,
                "volume_momentum_expansion": 10.0,
                "volatility_session_regime": 5.0
            }
        })
        self.curr_candle = Candle(
            time=datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc),
            open=1.0850,
            high=1.0880,
            low=1.0845,
            close=1.0875,
            tick_volume=150,
            spread=1,
            complete=True
        )
        self.recent_candles = [
            Candle(
                time=datetime(2026, 1, 1, 11, 0, tzinfo=timezone.utc),
                open=1.0820,
                high=1.0840,
                low=1.0810,
                close=1.0830,
                tick_volume=100,
                spread=1,
                complete=True
            )
        ] * 15

    def test_conflict_resolution_rejects_opposing_signals(self):
        """Opposing Bullish and Bearish patterns on the same bar must reject immediately."""
        bull_pat = PatternResult(
            name="Hammer",
            family=PatternFamily.CANDLE,
            direction=PatternDirection.BULLISH,
            bars_used=1,
            invalidation_price=1.0840,
            measured_target=1.0920,
            raw_strength=0.80,
            confirmed=True
        )
        bear_pat = PatternResult(
            name="Shooting Star",
            family=PatternFamily.CANDLE,
            direction=PatternDirection.BEARISH,
            bars_used=1,
            invalidation_price=1.0885,
            measured_target=1.0800,
            raw_strength=0.80,
            confirmed=True
        )

        scored = self.context_engine.evaluate(
            patterns=[bull_pat, bear_pat],
            current_candle=self.curr_candle,
            recent_candles=self.recent_candles,
            atr=0.0010
        )
        self.assertEqual(len(scored), 1)
        self.assertFalse(scored[0].passed_gate)
        self.assertEqual(scored[0].confluence_score, 0.0)
        self.assertTrue(any("Conflict" in r for r in scored[0].rejection_reasons))

    def test_high_confluence_passes_gate(self):
        """Pattern at key Order Block + aligned HTF trend + BOS confirmed must pass gate."""
        bull_pat = PatternResult(
            name="Bullish Engulfing",
            family=PatternFamily.CANDLE,
            direction=PatternDirection.BULLISH,
            bars_used=2,
            invalidation_price=1.0840,
            measured_target=1.0950,
            raw_strength=0.90,
            confirmed=True
        )
        ob = MockOB(high=1.0880, low=1.0850)
        fvg = MockFVG(top=1.0878, bottom=1.0860)

        @dataclass
        class MockStructState:
            state: str = "BOS_CONFIRMED"
            direction: str = "BULLISH"

        scored = self.context_engine.evaluate(
            patterns=[bull_pat],
            current_candle=self.curr_candle,
            recent_candles=self.recent_candles,
            atr=0.0010,
            mtf_bias="BULLISH",
            active_obs=[ob],
            active_fvgs=[fvg],
            liquidity_levels={"pwl": 1.0850},
            struct_state=MockStructState(),
            is_in_killzone=True,
            news_safe=True,
            min_rr=2.0
        )

        self.assertEqual(len(scored), 1)
        sp = scored[0]
        self.assertTrue(sp.passed_gate)
        self.assertGreaterEqual(sp.confluence_score, 65.0)
        self.assertGreaterEqual(sp.risk_reward, 2.0)
        self.assertEqual(len(sp.rejection_reasons), 0)

    def test_counter_trend_low_confluence_rejected(self):
        """Pattern directly opposing HTF trend with low score must be rejected."""
        bear_pat = PatternResult(
            name="Dark Cloud Cover",
            family=PatternFamily.CANDLE,
            direction=PatternDirection.BEARISH,
            bars_used=2,
            invalidation_price=1.0890,
            measured_target=1.0820,
            raw_strength=0.70,
            confirmed=False
        )

        scored = self.context_engine.evaluate(
            patterns=[bear_pat],
            current_candle=self.curr_candle,
            recent_candles=self.recent_candles,
            atr=0.0010,
            mtf_bias="BULLISH",  # Opposing trend
            active_obs=[],
            active_fvgs=[],
            is_in_killzone=False,
            news_safe=True,
            min_rr=2.0
        )

        self.assertEqual(len(scored), 1)
        self.assertFalse(scored[0].passed_gate)
        self.assertTrue(any("HTF Trend" in r for r in scored[0].rejection_reasons))


if __name__ == "__main__":
    unittest.main()
