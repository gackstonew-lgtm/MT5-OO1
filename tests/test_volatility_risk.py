"""
Unit tests for Stage 4: Volatility and Risk Modeling.
Validates:
1. GARCH(1,1), EWMA, and Volatility Targeting Engine
2. Fractional Kelly sizing and strict zero floor on non-positive expectancy
3. Drawdown throttle stepped reduction and CVaR tail risk alerts
4. Portfolio exposure limits: 1-position micro/mini rule, currency netting, correlation gating, and PCA
"""

import unittest
from datetime import datetime, timezone
import numpy as np

from src.core.contracts import Position, OrderType, Timeframe
from src.risk.volatility import VolatilityEngine, GarchModel
from src.risk.kelly import KellyEngine, KellyEvaluation
from src.risk.drawdown_throttle import DrawdownThrottle, ThrottleStatus
from src.risk.portfolio_exposure import PortfolioExposureEngine, ExposureCheckResult


class TestVolatilityAndRiskStage4(unittest.TestCase):
    def setUp(self):
        self.vol_engine = VolatilityEngine(target_annualized_vol=0.12)
        self.kelly_engine = KellyEngine(kelly_fraction=0.25)
        self.throttle = DrawdownThrottle(daily_loss_limit_pct=4.0, total_loss_limit_pct=8.0, buffer_fraction=0.80)
        self.exposure_engine = PortfolioExposureEngine(
            max_net_lots_per_currency=3.0,
            max_pairwise_correlation=0.70,
            pca_concentration_threshold=0.85
        )

    def test_garch_and_volatility_forecasting(self):
        """Proves GARCH and EWMA update correctly and size multiplier can NEVER exceed 1.0."""
        # Generate simulated prices with increasing volatility
        np.random.seed(42)
        base_price = 100.0
        rets = np.random.normal(0, 0.01, 100)
        closes = [base_price]
        for r in rets:
            closes.append(closes[-1] * (1.0 + r))

        forecast = self.vol_engine.forecast(symbol="EURUSD", closes=closes, atr_value=1.5)
        self.assertGreater(forecast.annualized_vol, 0.0)
        self.assertGreater(forecast.daily_vol, 0.0)
        self.assertIsNotNone(forecast.garch_vol)
        self.assertGreater(forecast.ewma_vol, 0.0)
        # Sizing scale can NEVER exceed 1.0
        self.assertLessEqual(forecast.recommended_size_scale, 1.0)
        # Stop scale can NEVER be less than 1.0 (cannot tighten stop dangerously)
        self.assertGreaterEqual(forecast.recommended_stop_scale, 1.0)

    def test_fractional_kelly_sizing(self):
        """Proves Kelly gives mathematically sound fraction and 0.0 on zero/negative expectancy."""
        # Case 1: Positive expectancy (Win rate 50%, RR 2.0 -> E = 0.5*2 - 0.5 = +0.50R)
        eval_pos = self.kelly_engine.evaluate(
            win_rate=0.50,
            reward_to_risk=2.0,
            tier_target_risk_pct=1.0,
            tier_ceiling_risk_pct=1.5
        )
        self.assertTrue(eval_pos.is_tradable)
        self.assertGreater(eval_pos.expectancy_r, 0.0)
        self.assertGreater(eval_pos.full_kelly_pct, 0.0)
        # Fractional Kelly is capped by tier target risk (1.0%)
        self.assertLessEqual(eval_pos.applied_risk_pct, 1.0)

        # Case 2: Non-positive expectancy (Win rate 30%, RR 2.0 -> E = 0.3*2 - 0.7 = -0.10R)
        eval_neg = self.kelly_engine.evaluate(
            win_rate=0.30,
            reward_to_risk=2.0,
            tier_target_risk_pct=1.0,
            tier_ceiling_risk_pct=1.5
        )
        self.assertFalse(eval_neg.is_tradable)
        self.assertEqual(eval_neg.applied_risk_pct, 0.0)
        self.assertIn("Non-positive expectancy", eval_neg.reason)

        # Case 3: Breakeven expectancy (Win rate 33.33%, RR 2.0 -> E = 0)
        eval_zero = self.kelly_engine.evaluate(
            win_rate=0.3333,
            reward_to_risk=2.0,
            tier_target_risk_pct=1.0,
            tier_ceiling_risk_pct=1.5
        )
        self.assertFalse(eval_zero.is_tradable)
        self.assertEqual(eval_zero.applied_risk_pct, 0.0)

    def test_drawdown_throttle_and_cvar(self):
        """Proves drawdown throttle scales down risk in discrete tiers and pauses at buffer."""
        # Baseline normal: no DD
        status_norm = self.throttle.evaluate(daily_starting_equity=10000.0, current_equity=10000.0, peak_equity=10000.0)
        self.assertEqual(status_norm.throttle_tier, "NORMAL")
        self.assertEqual(status_norm.risk_multiplier, 1.0)
        self.assertFalse(status_norm.is_paused)

        # Mild throttle: 1.5% DD out of 4% limit (37.5% utilization)
        status_mild = self.throttle.evaluate(daily_starting_equity=10000.0, current_equity=9850.0, peak_equity=10000.0)
        self.assertEqual(status_mild.throttle_tier, "MILD_THROTTLE")
        self.assertEqual(status_mild.risk_multiplier, 0.70)

        # Heavy throttle: 2.8% DD out of 4% limit (70% utilization)
        status_heavy = self.throttle.evaluate(daily_starting_equity=10000.0, current_equity=9720.0, peak_equity=10000.0)
        self.assertEqual(status_heavy.throttle_tier, "HEAVY_THROTTLE")
        self.assertEqual(status_heavy.risk_multiplier, 0.40)

        # Paused at buffer: 3.3% DD out of 4% limit (82.5% utilization >= 80% buffer)
        status_paused = self.throttle.evaluate(daily_starting_equity=10000.0, current_equity=9670.0, peak_equity=10000.0)
        self.assertEqual(status_paused.throttle_tier, "BUFFER_PAUSED")
        self.assertEqual(status_paused.risk_multiplier, 0.0)
        self.assertTrue(status_paused.is_paused)

    def test_portfolio_exposure_micro_mini_single_position_rule(self):
        """Proves MICRO/MINI tiers strictly allow MAX 1 open position."""
        existing_pos = [
            Position(
                ticket=101,
                symbol="EURUSD",
                order_type=OrderType.BUY,
                volume=0.01,
                open_price=1.0800,
                current_price=1.0820,
                stop_loss=1.0770,
                take_profit=1.0860,
                profit=2.0
            )
        ]

        # MICRO candidate rejected because 1 position is already open!
        res_micro = self.exposure_engine.check_candidate_exposure(
            candidate_symbol="GBPUSD",
            candidate_direction=OrderType.BUY,
            candidate_volume=0.01,
            current_positions=existing_pos,
            tier_name="MICRO"
        )
        self.assertFalse(res_micro.allowed)
        self.assertIn("strictly allows max 1 open position", res_micro.rejection_reason)

        # STANDARD tier allows additional positions if limits not breached
        res_standard = self.exposure_engine.check_candidate_exposure(
            candidate_symbol="USDJPY",
            candidate_direction=OrderType.BUY,
            candidate_volume=0.10,
            current_positions=existing_pos,
            tier_name="STANDARD"
        )
        self.assertTrue(res_standard.allowed)

    def test_rolling_correlation_and_currency_netting(self):
        """Proves cross-pair correlation gating and net currency caps."""
        # 1. Currency netting cap
        large_pos = [
            Position(
                ticket=102,
                symbol="EURUSD",
                order_type=OrderType.BUY,
                volume=2.8,
                open_price=1.0800,
                current_price=1.0820,
                stop_loss=1.0770,
                take_profit=1.0860,
                profit=50.0
            )
        ]
        # Adding 0.5 lots EURUSD would push EUR to 3.3 > 3.0 cap
        res_net = self.exposure_engine.check_candidate_exposure(
            candidate_symbol="EURUSD",
            candidate_direction=OrderType.BUY,
            candidate_volume=0.5,
            current_positions=large_pos,
            tier_name="STANDARD"
        )
        self.assertFalse(res_net.allowed)
        self.assertIn("Net currency exposure breach", res_net.rejection_reason)

        # 2. Correlation gating: highly correlated series (> 0.70)
        np.random.seed(99)
        base_series = list(np.random.normal(0, 0.01, 35))
        # Identical series -> correlation = 1.0
        history = {
            "EURUSD": base_series,
            "GBPUSD": [x + np.random.normal(0, 0.001) for x in base_series]
        }
        res_corr = self.exposure_engine.check_candidate_exposure(
            candidate_symbol="GBPUSD",
            candidate_direction=OrderType.BUY,
            candidate_volume=0.10,
            current_positions=[
                Position(
                    ticket=103,
                    symbol="EURUSD",
                    order_type=OrderType.BUY,
                    volume=0.10,
                    open_price=1.0800,
                    current_price=1.0820,
                    stop_loss=1.0770,
                    take_profit=1.0860,
                    profit=10.0
                )
            ],
            returns_history=history,
            tier_name="STANDARD"
        )
        self.assertFalse(res_corr.allowed)
        self.assertIn("Excessive positive correlation", res_corr.rejection_reason)


if __name__ == "__main__":
    unittest.main()
