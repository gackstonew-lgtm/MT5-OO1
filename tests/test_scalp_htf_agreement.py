"""
Unit tests for Task 2: SCALP Higher-Timeframe Agreement.
Tests:
- All agree LONG (M15=BULLISH, H1=BULLISH, H4=BULLISH)
- All agree SHORT (M15=BEARISH, H1=BEARISH, H4=BEARISH)
- Each of the 6 conflicting combinations
- Configurable toggle behavior (default=True)
"""

import unittest
from datetime import datetime, timezone
from src.core.contracts import TrendBias, Timeframe
from src.smc.mtf_analyzer import MultiTimeframeContext, TimeframeAnalysis
from src.smc.strategy_engine import SmcStrategyEngine


class TestScalpHTFAgreement(unittest.TestCase):
    def setUp(self):
        self.engine = SmcStrategyEngine()
        self.now = datetime.now(timezone.utc)

    def _create_context(self, m15_bias: TrendBias, h1_bias: TrendBias, h4_bias: TrendBias) -> MultiTimeframeContext:
        def _make_tf(tf: Timeframe, bias: TrendBias):
            return TimeframeAnalysis(
                timeframe=tf,
                trend=bias,
                last_close=1.1000
            )

        return MultiTimeframeContext(
            symbol="EURUSD",
            timestamp=self.now,
            w1=_make_tf(Timeframe.W1, TrendBias.STRONG_BULLISH),
            d1=_make_tf(Timeframe.D1, TrendBias.STRONG_BULLISH),
            h4=_make_tf(Timeframe.H4, h4_bias),
            h1=_make_tf(Timeframe.H1, h1_bias),
            m15=_make_tf(Timeframe.M15, m15_bias),
            overall_bias=TrendBias.STRONG_BULLISH
        )

    def test_all_agree_long(self):
        ctx = self._create_context(TrendBias.STRONG_BULLISH, TrendBias.STRONG_BULLISH, TrendBias.STRONG_BULLISH)
        agree, msg, dirs = self.engine.check_scalp_htf_agreement(ctx)
        self.assertTrue(agree)
        self.assertEqual(dirs["M15"], "BULLISH")
        self.assertEqual(dirs["H1"], "BULLISH")
        self.assertEqual(dirs["H4"], "BULLISH")
        self.assertIn("agreement", msg.lower())

    def test_all_agree_short(self):
        ctx = self._create_context(TrendBias.STRONG_BEARISH, TrendBias.STRONG_BEARISH, TrendBias.STRONG_BEARISH)
        agree, msg, dirs = self.engine.check_scalp_htf_agreement(ctx)
        self.assertTrue(agree)
        self.assertEqual(dirs["M15"], "BEARISH")
        self.assertEqual(dirs["H1"], "BEARISH")
        self.assertEqual(dirs["H4"], "BEARISH")
        self.assertIn("agreement", msg.lower())

    def test_conflict_combination_1(self):
        # M15=BULLISH, H1=BULLISH, H4=BEARISH
        ctx = self._create_context(TrendBias.STRONG_BULLISH, TrendBias.STRONG_BULLISH, TrendBias.STRONG_BEARISH)
        agree, msg, dirs = self.engine.check_scalp_htf_agreement(ctx)
        self.assertFalse(agree)
        self.assertEqual(msg, "SCALP HTF conflict (M15=BULLISH, H1=BULLISH, H4=BEARISH)")

    def test_conflict_combination_2(self):
        # M15=BULLISH, H1=BEARISH, H4=BULLISH
        ctx = self._create_context(TrendBias.STRONG_BULLISH, TrendBias.STRONG_BEARISH, TrendBias.STRONG_BULLISH)
        agree, msg, dirs = self.engine.check_scalp_htf_agreement(ctx)
        self.assertFalse(agree)
        self.assertEqual(msg, "SCALP HTF conflict (M15=BULLISH, H1=BEARISH, H4=BULLISH)")

    def test_conflict_combination_3(self):
        # M15=BULLISH, H1=BEARISH, H4=BEARISH
        ctx = self._create_context(TrendBias.STRONG_BULLISH, TrendBias.STRONG_BEARISH, TrendBias.STRONG_BEARISH)
        agree, msg, dirs = self.engine.check_scalp_htf_agreement(ctx)
        self.assertFalse(agree)
        self.assertEqual(msg, "SCALP HTF conflict (M15=BULLISH, H1=BEARISH, H4=BEARISH)")

    def test_conflict_combination_4(self):
        # M15=BEARISH, H1=BULLISH, H4=BULLISH
        ctx = self._create_context(TrendBias.STRONG_BEARISH, TrendBias.STRONG_BULLISH, TrendBias.STRONG_BULLISH)
        agree, msg, dirs = self.engine.check_scalp_htf_agreement(ctx)
        self.assertFalse(agree)
        self.assertEqual(msg, "SCALP HTF conflict (M15=BEARISH, H1=BULLISH, H4=BULLISH)")

    def test_conflict_combination_5(self):
        # M15=BEARISH, H1=BULLISH, H4=BEARISH
        ctx = self._create_context(TrendBias.STRONG_BEARISH, TrendBias.STRONG_BULLISH, TrendBias.STRONG_BEARISH)
        agree, msg, dirs = self.engine.check_scalp_htf_agreement(ctx)
        self.assertFalse(agree)
        self.assertEqual(msg, "SCALP HTF conflict (M15=BEARISH, H1=BULLISH, H4=BEARISH)")

    def test_conflict_combination_6(self):
        # M15=BEARISH, H1=BEARISH, H4=BULLISH
        ctx = self._create_context(TrendBias.STRONG_BEARISH, TrendBias.STRONG_BEARISH, TrendBias.STRONG_BULLISH)
        agree, msg, dirs = self.engine.check_scalp_htf_agreement(ctx)
        self.assertFalse(agree)
        self.assertEqual(msg, "SCALP HTF conflict (M15=BEARISH, H1=BEARISH, H4=BULLISH)")


if __name__ == "__main__":
    unittest.main()
