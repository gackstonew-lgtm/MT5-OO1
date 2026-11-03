"""
Unit Tests for CHoCH-Warning -> BOS-Confirmation State Machine.
Validates:
(a) Bearish CHoCH -> Lower High -> BOS = Confirmed Sell State
(b) CHoCH without Lower High + Close above Prior High = Failed / Trap Invalidation
(c) Wick-only break = No CHoCH (wicks ignored)
(d) CHoCH alone = No Trade Signal
(e) Timeout expiry
(f) Bullish mirrors for all cases
"""

import unittest
from datetime import datetime, timezone, timedelta
from typing import List

from src.core.contracts import Candle, Timeframe, OrderType, SetupType, TrendBias
from src.smc.structure_confirmation import (
    StructureStateMachine, ConfirmationState, CycleDirection, StructureStateSnapshot
)
from src.smc.strategy_engine import SmcStrategyEngine
from src.smc.mtf_analyzer import MultiTimeframeContext, TimeframeAnalysis


def create_candle(t_idx: int, o: float, h: float, l: float, c: float) -> Candle:
    base_time = datetime(2026, 10, 1, 0, 0, tzinfo=timezone.utc)
    return Candle(
        time=base_time + timedelta(minutes=15 * t_idx),
        open=round(o, 5),
        high=round(h, 5),
        low=round(l, 5),
        close=round(c, 5),
        tick_volume=100,
        spread=10,
        complete=True
    )


class TestStructureConfirmation(unittest.TestCase):
    def setUp(self):
        self.sm = StructureStateMachine(
            swing_lookback=3,
            require_close_break=True,
            require_lower_high=True,
            choch_timeout_bars=15,
            allow_choch_only_entries=False,
            failed_choch_action="RESET_AND_LOG_TRAP"
        )

    # -----------------------------------------------------------------
    # (a) Bearish CHoCH -> Lower High -> BOS = Confirmed Sell State
    # -----------------------------------------------------------------
    def test_bearish_choch_to_bos_confirmation(self):
        candles: List[Candle] = []
        # Bars 0..3: Base
        for i in range(4):
            candles.append(create_candle(i, 1.1000 + i * 0.001, 1.1020 + i * 0.001, 1.0990, 1.1010 + i * 0.001))
        # Bar 4: Pre-CHoCH High (1.1150)
        candles.append(create_candle(4, 1.1100, 1.1150, 1.1080, 1.1120))
        # Bars 5..7: Pullback to confirm swing high at bar 4
        candles.append(create_candle(5, 1.1110, 1.1120, 1.1070, 1.1080))
        candles.append(create_candle(6, 1.1080, 1.1090, 1.1050, 1.1060))
        candles.append(create_candle(7, 1.1060, 1.1070, 1.1045, 1.1050))
        # Bar 8: Protected Swing Low (1.1040)
        candles.append(create_candle(8, 1.1050, 1.1060, 1.1040, 1.1055))
        # Bars 9..11: Move up to confirm swing low at bar 8
        candles.append(create_candle(9, 1.1055, 1.1070, 1.1050, 1.1060))
        candles.append(create_candle(10, 1.1060, 1.1070, 1.1055, 1.1065))
        candles.append(create_candle(11, 1.1065, 1.1070, 1.1055, 1.1060))

        # Bar 12: CHoCH Candle - decisive close below protected swing low 1.1040 (high kept <= 1.1060)
        candles.append(create_candle(12, 1.1060, 1.1060, 1.1010, 1.1020))
        snap = self.sm.update("EURUSD", Timeframe.M15, candles)

        self.assertEqual(snap.state, ConfirmationState.CHOCH_WARNING)
        self.assertEqual(snap.direction, CycleDirection.BEARISH)
        self.assertEqual(snap.broken_level, 1.1040)
        self.assertFalse(snap.is_confirmed)

        # 2. Form a Pullback with Lower High (peak at 1.1085 < 1.1150 at bar 15)
        candles.append(create_candle(13, 1.1020, 1.1050, 1.1015, 1.1040))
        candles.append(create_candle(14, 1.1040, 1.1065, 1.1030, 1.1060))
        # Bar 15: Lower High (1.1085)
        candles.append(create_candle(15, 1.1060, 1.1085, 1.1050, 1.1070))
        # Bars 16..18: Decline to confirm Lower High
        candles.append(create_candle(16, 1.1070, 1.1075, 1.1040, 1.1045))
        candles.append(create_candle(17, 1.1045, 1.1050, 1.1025, 1.1030))
        candles.append(create_candle(18, 1.1030, 1.1035, 1.1015, 1.1020))
        snap = self.sm.update("EURUSD", Timeframe.M15, candles)

        self.assertEqual(snap.state, ConfirmationState.PULLBACK)
        self.assertAlmostEqual(snap.confirmed_retrace_extreme, 1.1085, places=4)
        self.assertFalse(snap.is_confirmed)

        # 3. BOS Breakout - candle closes below post-CHoCH low (1.1010)
        candles.append(create_candle(19, 1.1020, 1.1025, 1.0980, 1.0990))
        snap = self.sm.update("EURUSD", Timeframe.M15, candles)

        self.assertEqual(snap.state, ConfirmationState.BOS_CONFIRMED)
        self.assertTrue(snap.is_confirmed)
        self.assertEqual(snap.direction, CycleDirection.BEARISH)

    # -----------------------------------------------------------------
    # (b) CHoCH with no Lower High + Close above Prior High = Trap
    # -----------------------------------------------------------------
    def test_bearish_choch_trap_invalidation(self):
        candles: List[Candle] = []
        for i in range(4):
            candles.append(create_candle(i, 1.1000 + i * 0.001, 1.1020 + i * 0.001, 1.0990, 1.1010 + i * 0.001))
        candles.append(create_candle(4, 1.1100, 1.1150, 1.1080, 1.1120))
        candles.append(create_candle(5, 1.1110, 1.1120, 1.1070, 1.1080))
        candles.append(create_candle(6, 1.1080, 1.1090, 1.1050, 1.1060))
        candles.append(create_candle(7, 1.1060, 1.1070, 1.1045, 1.1050))
        candles.append(create_candle(8, 1.1050, 1.1060, 1.1040, 1.1055))
        candles.append(create_candle(9, 1.1055, 1.1070, 1.1050, 1.1060))
        candles.append(create_candle(10, 1.1060, 1.1070, 1.1055, 1.1065))
        candles.append(create_candle(11, 1.1065, 1.1070, 1.1055, 1.1060))

        # CHoCH candle at bar 12
        candles.append(create_candle(12, 1.1060, 1.1060, 1.1010, 1.1020))
        self.sm.update("EURUSD", Timeframe.M15, candles)

        # Sudden aggressive spike closing ABOVE pre-CHoCH high (1.1150)
        candles.append(create_candle(13, 1.1030, 1.1180, 1.1030, 1.1170))
        snap = self.sm.update("EURUSD", Timeframe.M15, candles)

        self.assertEqual(snap.state, ConfirmationState.FAILED_TRAP)
        self.assertTrue(snap.is_trapped)
        self.assertFalse(snap.is_confirmed)
        self.assertGreater(self.sm.trapped_events_count, 0)

    # -----------------------------------------------------------------
    # (c) Wick-only break = No CHoCH (wicks ignored)
    # -----------------------------------------------------------------
    def test_wick_only_break_no_choch(self):
        candles: List[Candle] = []
        for i in range(4):
            candles.append(create_candle(i, 1.1000 + i * 0.001, 1.1020 + i * 0.001, 1.0990, 1.1010 + i * 0.001))
        candles.append(create_candle(4, 1.1100, 1.1150, 1.1080, 1.1120))
        candles.append(create_candle(5, 1.1110, 1.1120, 1.1070, 1.1080))
        candles.append(create_candle(6, 1.1080, 1.1090, 1.1050, 1.1060))
        candles.append(create_candle(7, 1.1060, 1.1070, 1.1045, 1.1050))
        candles.append(create_candle(8, 1.1050, 1.1060, 1.1040, 1.1055))
        candles.append(create_candle(9, 1.1055, 1.1070, 1.1050, 1.1060))
        candles.append(create_candle(10, 1.1060, 1.1070, 1.1055, 1.1065))
        candles.append(create_candle(11, 1.1065, 1.1070, 1.1055, 1.1060))

        # Bar 12: Wicks below 1.1040 (low 1.1030) BUT closes at 1.1048 (ABOVE 1.1040)
        candles.append(create_candle(12, 1.1060, 1.1060, 1.1030, 1.1048))
        snap = self.sm.update("EURUSD", Timeframe.M15, candles)

        self.assertEqual(snap.state, ConfirmationState.IDLE)
        self.assertFalse(snap.is_confirmed)

    # -----------------------------------------------------------------
    # (d) CHoCH alone = No Trade Signal (Gating validation)
    # -----------------------------------------------------------------
    def test_choch_alone_rejected_by_strategy_engine(self):
        engine = SmcStrategyEngine(
            min_score=75.0,
            min_rr=2.0,
            swing_lookback=3,
            allow_choch_only_entries=False
        )

        candles: List[Candle] = []
        for i in range(4):
            candles.append(create_candle(i, 1.1000 + i * 0.001, 1.1020 + i * 0.001, 1.0990, 1.1010 + i * 0.001))
        candles.append(create_candle(4, 1.1100, 1.1150, 1.1080, 1.1120))
        candles.append(create_candle(5, 1.1110, 1.1120, 1.1070, 1.1080))
        candles.append(create_candle(6, 1.1080, 1.1090, 1.1050, 1.1060))
        candles.append(create_candle(7, 1.1060, 1.1070, 1.1045, 1.1050))
        candles.append(create_candle(8, 1.1050, 1.1060, 1.1040, 1.1055))
        candles.append(create_candle(9, 1.1055, 1.1070, 1.1050, 1.1060))
        candles.append(create_candle(10, 1.1060, 1.1070, 1.1055, 1.1065))
        candles.append(create_candle(11, 1.1065, 1.1070, 1.1055, 1.1060))

        # Pad initial candles up to 20 so engine can evaluate
        candles.append(create_candle(12, 1.1060, 1.1065, 1.1055, 1.1060))
        candles.append(create_candle(13, 1.1060, 1.1065, 1.1055, 1.1060))
        candles.append(create_candle(14, 1.1060, 1.1065, 1.1055, 1.1060))
        candles.append(create_candle(15, 1.1060, 1.1065, 1.1055, 1.1060))
        candles.append(create_candle(16, 1.1060, 1.1065, 1.1055, 1.1060))
        candles.append(create_candle(17, 1.1060, 1.1065, 1.1055, 1.1060))
        candles.append(create_candle(18, 1.1060, 1.1065, 1.1055, 1.1060))
        candles.append(create_candle(19, 1.1060, 1.1065, 1.1055, 1.1060))

        # Bar 20: The CHoCH bar breaking 1.1040
        candles.append(create_candle(20, 1.1055, 1.1055, 1.1010, 1.1020))

        tf_analysis = TimeframeAnalysis(timeframe=Timeframe.H4, trend=TrendBias.STRONG_BEARISH, last_close=1.1020)
        mtf_ctx = MultiTimeframeContext(
            symbol="EURUSD",
            timestamp=candles[-1].time,
            w1=tf_analysis,
            d1=tf_analysis,
            h4=tf_analysis,
            h1=tf_analysis,
            m15=tf_analysis,
            overall_bias=TrendBias.STRONG_BEARISH
        )

        candidates = engine.scan_for_opportunities(mtf_ctx, candles, is_in_killzone=True)
        if candidates:
            best = candidates[0]
            self.assertEqual(best.status, "REJECTED")
            self.assertTrue(any("CHoCH only" in r or "awaiting BOS" in r for r in best.rejection_reasons))

    # -----------------------------------------------------------------
    # (e) Timeout expiry
    # -----------------------------------------------------------------
    def test_choch_timeout_expiry(self):
        candles: List[Candle] = []
        for i in range(4):
            candles.append(create_candle(i, 1.1000 + i * 0.001, 1.1020 + i * 0.001, 1.0990, 1.1010 + i * 0.001))
        candles.append(create_candle(4, 1.1100, 1.1150, 1.1080, 1.1120))
        candles.append(create_candle(5, 1.1110, 1.1120, 1.1070, 1.1080))
        candles.append(create_candle(6, 1.1080, 1.1090, 1.1050, 1.1060))
        candles.append(create_candle(7, 1.1060, 1.1070, 1.1045, 1.1050))
        candles.append(create_candle(8, 1.1050, 1.1060, 1.1040, 1.1055))
        candles.append(create_candle(9, 1.1055, 1.1070, 1.1050, 1.1060))
        candles.append(create_candle(10, 1.1060, 1.1070, 1.1055, 1.1065))
        candles.append(create_candle(11, 1.1065, 1.1070, 1.1055, 1.1060))

        # CHoCH bar at 12
        candles.append(create_candle(12, 1.1060, 1.1060, 1.1010, 1.1020))

        # Add 20 sideways bars (exceeding timeout of 15 bars)
        for i in range(13, 35):
            candles.append(create_candle(i, 1.1020, 1.1025, 1.1018, 1.1022))

        snap = self.sm.update("EURUSD", Timeframe.M15, candles)
        self.assertEqual(snap.state, ConfirmationState.IDLE)
        self.assertFalse(snap.is_confirmed)

    # -----------------------------------------------------------------
    # (f) Bullish Mirrors
    # -----------------------------------------------------------------
    def test_bullish_choch_to_bos_confirmation(self):
        # Initial downtrend: Pre-CHoCH Low at bar 4 (1.0880), Protected Swing High at bar 8 (1.0960)
        candles: List[Candle] = []
        for i in range(4):
            candles.append(create_candle(i, 1.1000 - i * 0.001, 1.1010, 1.0980 - i * 0.001, 1.0990 - i * 0.001))
        # Bar 4: Pre-CHoCH Low (1.0880)
        candles.append(create_candle(4, 1.0910, 1.0920, 1.0880, 1.0890))
        candles.append(create_candle(5, 1.0890, 1.0920, 1.0885, 1.0910))
        candles.append(create_candle(6, 1.0910, 1.0935, 1.0905, 1.0930))
        candles.append(create_candle(7, 1.0930, 1.0950, 1.0920, 1.0945))
        # Bar 8: Protected Swing High (1.0960)
        candles.append(create_candle(8, 1.0945, 1.0960, 1.0935, 1.0950))
        candles.append(create_candle(9, 1.0950, 1.0955, 1.0935, 1.0940))
        candles.append(create_candle(10, 1.0940, 1.0950, 1.0935, 1.0940))
        candles.append(create_candle(11, 1.0940, 1.0950, 1.0935, 1.0940))

        # Bar 12: Bullish CHoCH candle - close above protected swing high 1.0960 (low kept >= 1.0935)
        candles.append(create_candle(12, 1.0940, 1.0990, 1.0940, 1.0980))
        snap = self.sm.update("EURUSD", Timeframe.M15, candles)

        self.assertEqual(snap.state, ConfirmationState.CHOCH_WARNING)
        self.assertEqual(snap.direction, CycleDirection.BULLISH)
        self.assertEqual(snap.broken_level, 1.0960)
        self.assertFalse(snap.is_confirmed)

        # Pullback with Higher Low (trough at 1.0920 > 1.0880 at bar 15)
        candles.append(create_candle(13, 1.0980, 1.0985, 1.0945, 1.0955))
        candles.append(create_candle(14, 1.0955, 1.0960, 1.0930, 1.0940))
        # Higher Low at bar 15 (low = 1.0915)
        candles.append(create_candle(15, 1.0940, 1.0950, 1.0915, 1.0935))
        # Confirm Higher Low with 3 bars up
        candles.append(create_candle(16, 1.0935, 1.0965, 1.0930, 1.0960))
        candles.append(create_candle(17, 1.0960, 1.0975, 1.0950, 1.0970))
        candles.append(create_candle(18, 1.0970, 1.0985, 1.0960, 1.0980))
        snap = self.sm.update("EURUSD", Timeframe.M15, candles)

        self.assertEqual(snap.state, ConfirmationState.PULLBACK)
        self.assertAlmostEqual(snap.confirmed_retrace_extreme, 1.0915, places=4)

        # BOS Breakout - close above post-CHoCH high (1.0990)
        candles.append(create_candle(19, 1.0980, 1.1020, 1.0980, 1.1010))
        snap = self.sm.update("EURUSD", Timeframe.M15, candles)

        self.assertEqual(snap.state, ConfirmationState.BOS_CONFIRMED)
        self.assertTrue(snap.is_confirmed)
        self.assertEqual(snap.direction, CycleDirection.BULLISH)

    def test_bullish_choch_trap_invalidation(self):
        candles: List[Candle] = []
        for i in range(4):
            candles.append(create_candle(i, 1.1000 - i * 0.001, 1.1010, 1.0980 - i * 0.001, 1.0990 - i * 0.001))
        candles.append(create_candle(4, 1.0910, 1.0920, 1.0880, 1.0890))
        candles.append(create_candle(5, 1.0890, 1.0920, 1.0885, 1.0910))
        candles.append(create_candle(6, 1.0910, 1.0935, 1.0905, 1.0930))
        candles.append(create_candle(7, 1.0930, 1.0950, 1.0920, 1.0945))
        candles.append(create_candle(8, 1.0945, 1.0960, 1.0935, 1.0950))
        candles.append(create_candle(9, 1.0950, 1.0955, 1.0935, 1.0940))
        candles.append(create_candle(10, 1.0940, 1.0950, 1.0935, 1.0940))
        candles.append(create_candle(11, 1.0940, 1.0950, 1.0935, 1.0940))

        # Bullish CHoCH candle
        candles.append(create_candle(12, 1.0940, 1.0990, 1.0940, 1.0980))
        self.sm.update("EURUSD", Timeframe.M15, candles)

        # Flash dump closing BELOW pre-CHoCH low (1.0880)
        candles.append(create_candle(13, 1.0970, 1.0970, 1.0850, 1.0860))
        snap = self.sm.update("EURUSD", Timeframe.M15, candles)

        self.assertEqual(snap.state, ConfirmationState.FAILED_TRAP)
        self.assertTrue(snap.is_trapped)
        self.assertFalse(snap.is_confirmed)


if __name__ == "__main__":
    unittest.main()
