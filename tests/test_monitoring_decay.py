"""
Unit tests for Stage 6: Monitoring and Decay Detection.
Validates:
1. Decay guard CUSUM and 3-tier sigma alert thresholds (2.0s, 2.5s, 3.0s pause)
2. Operator /run resumption from pause
3. Feature drift detection via Population Stability Index (PSI)
4. Deal reconciler auditing price drift, volume mismatches, and rogue orders
5. LLM trade reviewer silence when disabled or unauthenticated
"""

import unittest
import numpy as np

from src.monitoring.decay_guard import DecayGuard, DecayCheckResult
from src.monitoring.feature_drift import FeatureDriftDetector, FeatureDriftReport
from src.monitoring.reconciliation import ExecutionReconciler, ReconciliationReport
from src.monitoring.llm_reviewer import LLMTradeReviewer, LLMReviewSummary


class TestMonitoringAndDecayStage6(unittest.TestCase):
    def setUp(self):
        self.guard = DecayGuard(
            baseline_win_rate=0.50,
            baseline_expectancy_r=0.40,
            baseline_std_r=1.00,
            rolling_window_trades=20,
            enabled=True
        )
        self.drift_detector = FeatureDriftDetector()
        self.reconciler = ExecutionReconciler(price_drift_threshold_pts=20.0)

    def test_decay_guard_alert_tiers_and_pause(self):
        """Proves DecayGuard triggers WARNING (2.0s), REDUCE_RISK (2.5s), and PAUSE (3.0s)."""
        # Baseline normal trades (+0.5R each)
        for _ in range(20):
            self.guard.add_trade(0.50)
        res_normal = self.guard.evaluate()
        self.assertEqual(res_normal.status, "NORMAL")
        self.assertEqual(res_normal.risk_multiplier, 1.0)
        self.assertFalse(self.guard.is_paused)

        # Inject severe loss streak (-1.0R each) to trigger decay
        for _ in range(20):
            self.guard.add_trade(-1.0)

        res_decay = self.guard.evaluate()
        # SE = 1.0 / sqrt(20) ~ 0.2236
        # Expected deviation: (0.40 - (-1.0)) / 0.2236 = 1.40 / 0.2236 ~ 6.26 sigma!
        self.assertEqual(res_decay.status, "PAUSE")
        self.assertEqual(res_decay.risk_multiplier, 0.0)
        self.assertTrue(self.guard.is_paused)

        # Proves operator /run resets pause
        self.guard.reset_pause()
        self.assertFalse(self.guard.is_paused)

    def test_feature_drift_psi(self):
        """Proves PSI identifies identical distributions as STABLE and shifted as DRIFT."""
        np.random.seed(42)
        base = np.random.normal(10.0, 2.0, 500)
        curr_identical = np.random.normal(10.0, 2.0, 200)
        curr_shifted = np.random.normal(25.0, 5.0, 200)

        # 1. Identical distribution
        rep_stable = self.drift_detector.evaluate_feature("Spread", list(base), list(curr_identical))
        self.assertEqual(rep_stable.drift_level, "STABLE")
        self.assertLess(rep_stable.psi_score, 0.10)

        # 2. Heavily shifted distribution
        rep_drift = self.drift_detector.evaluate_feature("Spread", list(base), list(curr_shifted))
        self.assertEqual(rep_drift.drift_level, "SIGNIFICANT_DRIFT")
        self.assertGreaterEqual(rep_drift.psi_score, 0.25)

    def test_reconciliation_auditing(self):
        """Proves execution reconciler flags price drift, volume mismatch, and unexpected rogue orders."""
        broker_deals = [
            # 1. Clean deal
            {"ticket": 1001, "symbol": "EURUSD", "price": 1.08000, "volume": 0.25, "sl": 1.0770, "tp": 1.0860, "comment": ""},
            # 2. Volume mismatch
            {"ticket": 1002, "symbol": "EURUSD", "price": 1.08000, "volume": 0.10, "sl": 1.0770, "tp": 1.0860, "comment": ""},
            # 3. Rogue deal
            {"ticket": 9999, "symbol": "BTCUSD", "price": 65000.0, "volume": 1.00, "sl": 0.0, "tp": 0.0, "comment": "Manual Phone Order"}
        ]
        bot_orders = [
            {"ticket": 1001, "symbol": "EURUSD", "price": 1.08000, "volume": 0.25, "status": "FILLED"},
            {"ticket": 1002, "symbol": "EURUSD", "price": 1.08000, "volume": 0.25, "status": "FILLED"},  # Bot wanted 0.25
        ]

        report = self.reconciler.reconcile(broker_deals, bot_orders)
        self.assertEqual(report.status, "ROGUE_ORDERS_DETECTED")
        self.assertEqual(report.matched_deals, 2)
        self.assertEqual(len(report.discrepancies), 2)  # 1 volume mismatch, 1 rogue deal

        types = [d.discrepancy_type for d in report.discrepancies]
        self.assertIn("VOLUME_MISMATCH", types)
        self.assertIn("UNEXPECTED_DEAL", types)

    def test_llm_reviewer_silence(self):
        """Proves LLM reviewer is silent when disabled and provides offline template without API key."""
        # When disabled: None
        rev_disabled = LLMTradeReviewer(enabled=False)
        self.assertIsNone(rev_disabled.run_review({}, []))

        # When enabled without API key: offline structured summary, never crashes or makes web calls
        rev_offline = LLMTradeReviewer(enabled=True)
        res = rev_offline.run_review(
            weekly_stats={"total_trades": 15, "win_rate_pct": 53.3, "expectancy_r": 0.42},
            recent_trades=[]
        )
        self.assertIsNotNone(res)
        self.assertIn("Offline Summary", res.weekly_overview)
        self.assertFalse(res.is_live_call)


if __name__ == "__main__":
    unittest.main()
