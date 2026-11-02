"""
Unit tests for Portfolio Risk and Circuit Breaker Engine.
"""

import unittest
from datetime import datetime, timezone
from src.core.contracts import AccountInfo, Position, SignalCandidate, OrderType, SetupType, Timeframe
from src.core.config import RiskConfig
from src.risk.portfolio_risk import PortfolioRiskEngine


class TestPortfolioRiskEngine(unittest.TestCase):
    def setUp(self):
        self.config = RiskConfig(
            max_daily_loss_percent=2.0,
            max_weekly_loss_percent=4.0,
            max_aggregate_open_risk_percent=1.5,
            max_simultaneous_positions=3,
            max_consecutive_losses=3
        )
        self.risk_engine = PortfolioRiskEngine(self.config)
        self.account = AccountInfo(balance=10000.0, equity=10000.0, free_margin=10000.0)

        self.candidate = SignalCandidate(
            id="EURUSD_TEST",
            symbol="EURUSD",
            timeframe=Timeframe.M15,
            direction=OrderType.BUY,
            setup_type=SetupType.DAILY_REVERSAL,
            entry_price=1.0850,
            stop_loss=1.0830,
            take_profit=1.0900,
            risk_reward=2.5,
            score=85.0,
            timestamp=datetime.now(timezone.utc)
        )

    def test_daily_loss_limit_circuit_breaker(self):
        self.risk_engine.initialize_day(10000.0)
        # Realize $210 loss (> 2% of $10,000)
        self.risk_engine.record_closed_trade(-210.0)

        allowed, reason = self.risk_engine.evaluate_new_order_permission(
            self.candidate, self.account, [], 50.0
        )
        self.assertFalse(allowed)
        self.assertTrue(self.risk_engine.circuit_breaker_active)
        self.assertIn("Daily loss limit breached", reason)

    def test_max_simultaneous_positions(self):
        active = [
            Position(ticket=1, symbol="EURUSD", order_type=OrderType.BUY, volume=0.1, open_price=1.08, current_price=1.08, stop_loss=1.075, take_profit=1.09, profit=10.0),
            Position(ticket=2, symbol="GBPUSD", order_type=OrderType.BUY, volume=0.1, open_price=1.28, current_price=1.28, stop_loss=1.275, take_profit=1.29, profit=10.0),
            Position(ticket=3, symbol="USDJPY", order_type=OrderType.SELL, volume=0.1, open_price=150.0, current_price=150.0, stop_loss=150.5, take_profit=149.0, profit=10.0),
        ]
        # 3 positions is the limit, 4th must be rejected
        allowed, reason = self.risk_engine.evaluate_new_order_permission(
            self.candidate, self.account, active, 50.0
        )
        self.assertFalse(allowed)
        self.assertIn("Maximum simultaneous positions reached", reason)

    def test_circuit_breaker_manual_reset(self):
        self.risk_engine.initialize_day(10000.0)
        self.risk_engine.record_closed_trade(-250.0)
        self.assertTrue(self.risk_engine.circuit_breaker_active)

        # Invalid token fails
        self.assertFalse(self.risk_engine.manual_reset_circuit_breaker("WRONG_TOKEN"))
        self.assertTrue(self.risk_engine.circuit_breaker_active)

        # Valid token resets
        self.assertTrue(self.risk_engine.manual_reset_circuit_breaker("AUTHORIZE_SYSTEM_RESET"))
        self.assertFalse(self.risk_engine.circuit_breaker_active)


if __name__ == "__main__":
    unittest.main()
