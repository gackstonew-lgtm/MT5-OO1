"""
Unit tests for Institutional Session Risk and Correlation Guard.
"""

import unittest
from datetime import datetime, timezone
from src.core.contracts import (
    SessionType, OrderType, SignalCandidate, SetupType, Timeframe, Position, AccountInfo
)
from src.risk.session_risk import SessionRiskEngine


class TestSessionRiskEngine(unittest.TestCase):
    def setUp(self):
        self.engine = SessionRiskEngine(
            max_trades_per_session=2,
            max_concurrent_session_positions=2,
            max_consecutive_losses=2,
            session_loss_cap_percent=1.0
        )
        self.account = AccountInfo(balance=10000.0, equity=10000.0)

    def _make_candidate(self, symbol: str = "EURUSD", direction: OrderType = OrderType.BUY) -> SignalCandidate:
        return SignalCandidate(
            id=f"TEST_{symbol}",
            symbol=symbol,
            timeframe=Timeframe.M15,
            direction=direction,
            setup_type=SetupType.DAILY_CONTINUATION,
            entry_price=1.0850,
            stop_loss=1.0830,
            take_profit=1.0900,
            risk_reward=2.5,
            score=0.85,
            timestamp=datetime.now(timezone.utc)
        )

    def test_risk_multipliers_by_session(self):
        """Asia receives 0.50x risk; London and NY receive 1.00x risk."""
        cand = self._make_candidate("EURUSD")

        # Asia
        allowed, mult_asia, _ = self.engine.evaluate_session_risk_permission(
            cand, SessionType.ASIAN, self.account, []
        )
        self.assertTrue(allowed)
        self.assertEqual(mult_asia, 0.50)

        # London
        allowed, mult_lon, _ = self.engine.evaluate_session_risk_permission(
            cand, SessionType.LONDON, self.account, []
        )
        self.assertTrue(allowed)
        self.assertEqual(mult_lon, 1.00)

    def test_session_trade_count_ceiling(self):
        """Max 2 trades per session must block the 3rd attempt."""
        cand = self._make_candidate("EURUSD")

        # 1st trade
        allowed, _, _ = self.engine.evaluate_session_risk_permission(
            cand, SessionType.LONDON, self.account, []
        )
        self.assertTrue(allowed)
        self.engine.record_trade_opened()

        # 2nd trade
        allowed, _, _ = self.engine.evaluate_session_risk_permission(
            cand, SessionType.LONDON, self.account, []
        )
        self.assertTrue(allowed)
        self.engine.record_trade_opened()

        # 3rd trade -> blocked
        allowed, _, reason = self.engine.evaluate_session_risk_permission(
            cand, SessionType.LONDON, self.account, []
        )
        self.assertFalse(allowed)
        self.assertIn("Session trade cap reached", reason)

    def test_consecutive_loss_circuit_breaker(self):
        """Max 2 consecutive losses in a session triggers a breaker."""
        cand = self._make_candidate("EURUSD")

        self.engine.evaluate_session_risk_permission(cand, SessionType.NEW_YORK, self.account, [])
        self.engine.record_trade_opened()
        self.engine.record_trade_closed(-50.0) # Loss 1

        allowed, _, _ = self.engine.evaluate_session_risk_permission(cand, SessionType.NEW_YORK, self.account, [])
        self.assertTrue(allowed)

        self.engine.record_trade_opened()
        self.engine.record_trade_closed(-40.0) # Loss 2

        # 3rd attempt -> blocked due to consecutive loss limit
        allowed, _, reason = self.engine.evaluate_session_risk_permission(cand, SessionType.NEW_YORK, self.account, [])
        self.assertFalse(allowed)
        self.assertIn("consecutive loss limit reached", reason)

    def test_session_loss_cap_percent(self):
        """Exceeding 1% balance loss ($100 on $10,000) halts trading for the session."""
        cand = self._make_candidate("EURUSD")

        self.engine.evaluate_session_risk_permission(cand, SessionType.LONDON, self.account, [])
        self.engine.record_trade_opened()
        # Single loss of $120 (1.2% > 1.0%)
        self.engine.record_trade_closed(-120.0)

        allowed, _, reason = self.engine.evaluate_session_risk_permission(cand, SessionType.LONDON, self.account, [])
        self.assertFalse(allowed)
        self.assertIn("Session loss cap exceeded", reason)

    def test_cross_pair_correlation_guard(self):
        """Holding EURUSD BUY blocks opening GBPUSD BUY in the same session."""
        existing_pos = Position(
            ticket=12345,
            symbol="EURUSD",
            order_type=OrderType.BUY,
            volume=0.10,
            open_price=1.0820,
            current_price=1.0830,
            stop_loss=1.0800,
            take_profit=1.0880,
            profit=10.0
        )

        cand_gbpusd_buy = self._make_candidate("GBPUSD", OrderType.BUY)
        allowed, _, reason = self.engine.evaluate_session_risk_permission(
            cand_gbpusd_buy, SessionType.LONDON, self.account, [existing_pos]
        )
        self.assertFalse(allowed)
        self.assertIn("Correlation guard", reason)

        # Opposite direction (SELL GBPUSD) or uncorrelated pair (USDJPY) is permitted
        cand_gbpusd_sell = self._make_candidate("GBPUSD", OrderType.SELL)
        allowed_sell, _, _ = self.engine.evaluate_session_risk_permission(
            cand_gbpusd_sell, SessionType.LONDON, self.account, [existing_pos]
        )
        self.assertTrue(allowed_sell)

        cand_usdjpy = self._make_candidate("USDJPY", OrderType.BUY)
        allowed_uj, _, _ = self.engine.evaluate_session_risk_permission(
            cand_usdjpy, SessionType.LONDON, self.account, [existing_pos]
        )
        self.assertTrue(allowed_uj)


if __name__ == "__main__":
    unittest.main()
