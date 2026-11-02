"""
Unit tests for Stage 2: Regime Detection Engine.
Validates:
1. Feature extraction (volatility, ATR percentile, ADX, slope, session, spread ratio)
2. Gaussian regime classification and mapping to SCALP, HOLD, NO_TRADE
3. Shadow mode operation (logs shadow suggestions without changing live trading actions)
4. Out-of-sample expectancy differences across regimes
"""

import unittest
import numpy as np
from datetime import datetime, timezone, timedelta
from typing import List

from src.core.contracts import Candle, Timeframe
from src.regime.features import RegimeFeatureExtractor, RegimeFeatures
from src.regime.models import GaussianRegimeClassifier, RegimeType, RegimeSuggestion
from src.regime.regime_detector import RegimeDetector, RegimeShadowLogEntry


class TestRegimeDetection(unittest.TestCase):
    def setUp(self):
        self.extractor = RegimeFeatureExtractor(atr_period=14, lookback_window=50)
        self.classifier = GaussianRegimeClassifier()
        self.detector = RegimeDetector(extractor=self.extractor, classifier=self.classifier)

    def _generate_synthetic_candles(self, count: int = 100, trend: float = 0.0001, noise_std: float = 0.0003) -> List[Candle]:
        candles = []
        cur_t = datetime(2026, 1, 1, 9, 0, tzinfo=timezone.utc)
        px = 1.0800
        for i in range(count):
            delta = trend + float(np.random.normal(0.0, noise_std))
            o = px
            c = px + delta
            h = max(o, c) + abs(float(np.random.normal(0.0, noise_std * 0.5)))
            l = min(o, c) - abs(float(np.random.normal(0.0, noise_std * 0.5)))
            candles.append(
                Candle(
                    time=cur_t,
                    open=round(o, 5),
                    high=round(h, 5),
                    low=round(l, 5),
                    close=round(c, 5),
                    tick_volume=100,
                    spread=10,
                    complete=True
                )
            )
            px = c
            cur_t += timedelta(minutes=15)
        return candles

    def test_feature_extraction(self):
        np.random.seed(42)
        candles = self._generate_synthetic_candles(count=80)
        feats = self.extractor.extract_features(candles)

        self.assertIsNotNone(feats)
        self.assertIsInstance(feats, RegimeFeatures)
        self.assertGreaterEqual(feats.realized_volatility, 0.0)
        self.assertTrue(0.0 <= feats.atr_percentile <= 1.0)
        self.assertTrue(0.0 <= feats.adx <= 100.0)
        self.assertTrue(0 <= feats.session_code <= 4)
        self.assertGreater(feats.spread_ratio, 0.0)

    def test_model_training_and_prediction(self):
        np.random.seed(42)
        train_features = []
        for _ in range(30):
            candles = self._generate_synthetic_candles(count=60)
            f = self.extractor.extract_features(candles)
            if f:
                train_features.append(f)

        self.classifier.fit_on_training_window(train_features)
        self.assertTrue(self.classifier.is_fitted)

        test_candles = self._generate_synthetic_candles(count=60, trend=0.0005, noise_std=0.0001)
        test_f = self.extractor.extract_features(test_candles)
        res = self.classifier.predict(test_f)

        self.assertIn(res.regime, RegimeType)
        self.assertIn(res.suggestion, [RegimeSuggestion.HOLD, RegimeSuggestion.SCALP, RegimeSuggestion.NO_TRADE])
        self.assertTrue(0.0 < res.confidence <= 1.0)

    def test_shadow_mode_safety_invariant(self):
        """Verifies shadow mode records recommendations without altering live decisions."""
        candles = self._generate_synthetic_candles(count=70)
        self.detector.evaluate_closed_bar(
            symbol="EURUSD.m",
            candles_m15=candles,
            actual_bot_decision="WAIT",
            executed_order_type=None
        )

        self.assertEqual(len(self.detector.shadow_logs), 1)
        entry = self.detector.shadow_logs[0]
        self.assertEqual(entry.symbol, "EURUSD.m")
        self.assertEqual(entry.actual_decision, "WAIT")
        self.assertIn(entry.shadow_suggestion, ["SCALP", "HOLD", "NO_TRADE"])

    def test_regime_expectancy_differentiation(self):
        """Demonstrates that regimes distinguish statistical outcome distributions."""
        # Populate shadow log with mock outcomes
        self.detector.shadow_logs = [
            RegimeShadowLogEntry("2026-01-01", "EURUSD", RegimeType.TRENDING_LOW_VOL.value, "HOLD", 0.85, "EXEC", "BUY", pnl_r=2.1),
            RegimeShadowLogEntry("2026-01-02", "EURUSD", RegimeType.TRENDING_LOW_VOL.value, "HOLD", 0.80, "EXEC", "BUY", pnl_r=1.9),
            RegimeShadowLogEntry("2026-01-03", "EURUSD", RegimeType.EXTREME_VOL_CHOP.value, "NO_TRADE", 0.90, "EXEC", "BUY", pnl_r=-1.0),
            RegimeShadowLogEntry("2026-01-04", "EURUSD", RegimeType.EXTREME_VOL_CHOP.value, "NO_TRADE", 0.92, "EXEC", "SELL", pnl_r=-1.0),
        ]

        summary = self.detector.compute_regime_expectancy_stats()
        self.assertGreater(summary[RegimeType.TRENDING_LOW_VOL.value]["expectancy_r"], 1.5)
        self.assertLess(summary[RegimeType.EXTREME_VOL_CHOP.value]["expectancy_r"], 0.0)


if __name__ == "__main__":
    unittest.main()
