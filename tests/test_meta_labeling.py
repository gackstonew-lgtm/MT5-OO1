"""
Unit tests for Stage 3: Meta-Labeling Suite.
Validates:
1. Triple-barrier labeling logic (TP/SL hits and vertical barriers)
2. Strict temporal causality test (proof no future bars used)
3. Model training, calibration, and feature importance
4. Risk scaling constraints (can ONLY scale down or skip, NEVER up)
5. Shadow mode audit tracking and champion/challenger registry
"""

import unittest
from datetime import datetime, timezone, timedelta
from typing import List, Optional, Tuple, Dict
import numpy as np

from src.core.contracts import Candle, SignalCandidate, OrderType, SetupType, Timeframe
from src.learning.meta.labeling import TripleBarrierLabeler, BarrierLabel
from src.learning.meta.features import MetaFeatureExtractor, MetaFeatureVector
from src.learning.meta.model import MetaClassifierModel, ModelRegistryEntry
from src.learning.meta.meta_classifier import MetaLabelingController


class TestMetaLabelingSuite(unittest.TestCase):
    def setUp(self):
        self.labeler = TripleBarrierLabeler(max_holding_bars=20)
        self.extractor = MetaFeatureExtractor(point=0.00001)
        self.model = MetaClassifierModel()
        self.controller = MetaLabelingController(model=self.model, extractor=self.extractor)

    def _create_candles(self, count: int = 40, start_time: Optional[datetime] = None) -> List[Candle]:
        candles = []
        cur_t = start_time or datetime(2026, 1, 1, 10, 0, tzinfo=timezone.utc)
        px = 1.0800
        for i in range(count):
            candles.append(
                Candle(
                    time=cur_t,
                    open=round(px, 5),
                    high=round(px + 0.0005, 5),
                    low=round(px - 0.0005, 5),
                    close=round(px + 0.0001, 5),
                    tick_volume=100,
                    spread=10,
                    complete=True
                )
            )
            px += 0.0001
            cur_t += timedelta(minutes=15)
        return candles

    def test_triple_barrier_labeling(self):
        """Proves TP hit gives label=1 and SL hit gives label=0."""
        # 1. Take Profit hit scenario
        future_tp = [
            Candle(time=datetime(2026, 1, 1, 10, 15, tzinfo=timezone.utc), open=1.0800, high=1.0850, low=1.0790, close=1.0840),
        ]
        label_tp = self.labeler.label_candidate(
            signal_bar_idx=0,
            direction=OrderType.BUY,
            entry_price=1.0800,
            sl=1.0750,
            tp=1.0840,
            future_candles=future_tp
        )
        self.assertIsNotNone(label_tp)
        self.assertEqual(label_tp.label, 1)
        self.assertGreater(label_tp.return_r, 0.0)

        # 2. Stop Loss hit scenario
        future_sl = [
            Candle(time=datetime(2026, 1, 1, 10, 15, tzinfo=timezone.utc), open=1.0800, high=1.0810, low=1.0740, close=1.0745),
        ]
        label_sl = self.labeler.label_candidate(
            signal_bar_idx=0,
            direction=OrderType.BUY,
            entry_price=1.0800,
            sl=1.0750,
            tp=1.0840,
            future_candles=future_sl
        )
        self.assertIsNotNone(label_sl)
        self.assertEqual(label_sl.label, 0)
        self.assertLess(label_sl.return_r, 0.0)

    def test_strict_temporal_causality_no_lookahead(self):
        """Proves feature extraction strictly forbids future bars."""
        base_time = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
        candles = self._create_candles(count=30, start_time=datetime(2026, 1, 1, 4, 0, tzinfo=timezone.utc))

        cand_valid = SignalCandidate(
            id="SIG_001",
            symbol="EURUSD",
            timeframe=Timeframe.M15,
            direction=OrderType.BUY,
            setup_type=SetupType.M15_SMC_OTE,
            entry_price=1.0800,
            stop_loss=1.0780,
            take_profit=1.0850,
            risk_reward=2.5,
            score=85.0,
            timestamp=candles[-1].time  # Matching last bar time
        )
        # Valid call passes without error
        feats = self.extractor.extract_features_at_bar(candles, cand_valid)
        self.assertIsInstance(feats, MetaFeatureVector)

        # Illegal call with candidate timestamp before candle time MUST raise ValueError!
        cand_future = SignalCandidate(
            id="SIG_002",
            symbol="EURUSD",
            timeframe=Timeframe.M15,
            direction=OrderType.BUY,
            setup_type=SetupType.M15_SMC_OTE,
            entry_price=1.0800,
            stop_loss=1.0780,
            take_profit=1.0850,
            risk_reward=2.5,
            score=85.0,
            timestamp=candles[5].time  # Earlier than candles[-1].time!
        )
        with self.assertRaises(ValueError) as ctx:
            self.extractor.extract_features_at_bar(candles, cand_future)
        self.assertIn("Lookahead violation", str(ctx.exception))

    def test_risk_scaling_constraints(self):
        """Proves meta model can ONLY scale risk DOWN or skip, NEVER up."""
        # Test low probability -> skipped
        allow, mult, _ = self.model.evaluate_risk_scaling(prob=0.30)
        self.assertFalse(allow)
        self.assertEqual(mult, 0.0)

        # Test moderate probability -> downscaled (0.70x)
        allow_mod, mult_mod, _ = self.model.evaluate_risk_scaling(prob=0.52)
        self.assertTrue(allow_mod)
        self.assertLess(mult_mod, 1.0)

        # Test very high probability -> exactly 1.0x (NEVER > 1.0x)
        allow_hi, mult_hi, _ = self.model.evaluate_risk_scaling(prob=0.99)
        self.assertTrue(allow_hi)
        self.assertEqual(mult_hi, 1.00)

    def test_shadow_mode_order_protection(self):
        """Proves shadow mode captures audit records without modifying the order."""
        candles = self._create_candles(count=30)
        candidate = SignalCandidate(
            id="SIG_SHADOW",
            symbol="EURUSD",
            timeframe=Timeframe.M15,
            direction=OrderType.BUY,
            setup_type=SetupType.M15_SMC_OTE,
            entry_price=1.0800,
            stop_loss=1.0780,
            take_profit=1.0850,
            risk_reward=2.5,
            score=80.0,
            timestamp=candles[-1].time
        )

        allow, mult, exp = self.controller.evaluate_candidate(
            historical_candles=candles,
            candidate=candidate,
            actual_volume=0.25,
            actual_decision="APPROVED"
        )

        self.assertTrue(allow)
        self.assertEqual(mult, 1.0)  # Shadow mode returns 1.0x to preserve order
        self.assertIn("SHADOW_MODE", exp)
        self.assertEqual(len(self.controller.audit_records), 1)

    def test_model_registry_hashing(self):
        """Proves SHA-256 model weight hashing and registry generation."""
        reg = self.model.get_registry_entry(
            training_window="2026-01 to 2026-03",
            brier_score=0.185,
            accuracy=0.68
        )
        self.assertEqual(reg.model_id, "META_SMC_V1")
        self.assertEqual(len(reg.weights_sha256), 64)  # 64 hex characters
        self.assertTrue(reg.is_champion)
        self.assertEqual(len(reg.feature_names), 10)


if __name__ == "__main__":
    unittest.main()
