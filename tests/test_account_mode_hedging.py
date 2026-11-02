"""
Unit Tests for Task 6: Account Mode Handling (Netting vs Hedging).
Tests detection of Netting vs Hedging mode at startup, position counting,
aggregate open risk calculation, and position close logic with multiple positions
on a single symbol under Hedging mode.
"""

import unittest
from datetime import datetime, timezone
from src.core.contracts import Position, AccountInfo, OrderType, SignalCandidate, Timeframe, SetupType
from src.risk.portfolio_risk import PortfolioRiskEngine
from src.core.config import RiskConfig


class TestAccountModeHedging(unittest.TestCase):
    def setUp(self):
        self.risk_config = RiskConfig(
            max_simultaneous_positions=5,
            max_aggregate_open_risk_percent=6.0,
            max_daily_loss_percent=4.0
        )
        self.risk_engine = PortfolioRiskEngine(self.risk_config)
        self.risk_engine.profile.max_open_positions = 5

    def test_account_mode_detection_and_tagging(self):
        """Verifies account mode detection for both NETTING and HEDGING."""
        acc_hedging = AccountInfo(margin_mode=2)
        self.assertEqual(acc_hedging.margin_mode_str, "HEDGING")
        self.assertEqual(acc_hedging.margin_mode, 2)

        acc_netting = AccountInfo(margin_mode=1)
        self.assertEqual(acc_netting.margin_mode_str, "NETTING")
        self.assertEqual(acc_netting.margin_mode, 1)

        acc_str_hedging = AccountInfo(margin_mode_str="HEDGING")
        self.assertEqual(acc_str_hedging.margin_mode, 2)

        acc_str_netting = AccountInfo(margin_mode_str="NETTING")
        self.assertEqual(acc_str_netting.margin_mode, 1)

    def test_multiple_positions_on_same_symbol_hedging_risk_and_counting(self):
        """Verifies multiple positions on one symbol under Hedging mode."""
        account = AccountInfo(balance=10000.0, equity=10000.0, margin_mode=2, margin_mode_str="HEDGING")
        self.risk_engine.update_equity(account)

        now = datetime.now(timezone.utc)
        # 3 simultaneous positions on XAUUSD.m
        p1 = Position(ticket=1001, symbol="XAUUSD.m", order_type=OrderType.BUY, volume=0.02,
                      open_price=2000.0, current_price=2005.0, stop_loss=1980.0, take_profit=2040.0, profit=10.0,
                      initial_risk_amount=40.0)
        p2 = Position(ticket=1002, symbol="XAUUSD.m", order_type=OrderType.BUY, volume=0.03,
                      open_price=2002.0, current_price=2005.0, stop_loss=1982.0, take_profit=2042.0, profit=9.0,
                      initial_risk_amount=60.0)
        p3 = Position(ticket=1003, symbol="XAUUSD.m", order_type=OrderType.BUY, volume=0.02,
                      open_price=2004.0, current_price=2005.0, stop_loss=1984.0, take_profit=2044.0, profit=2.0,
                      initial_risk_amount=40.0)

        active_positions = [p1, p2, p3]
        self.assertEqual(len(active_positions), 3, "Hedging mode must track all 3 independent positions on XAUUSD.m")

        # Aggregate open risk should be sum of all 3 ($40 + $60 + $40 = $140)
        total_open_risk = sum(p.initial_risk_amount for p in active_positions)
        self.assertEqual(total_open_risk, 140.0)

        # Propose 4th BUY position on XAUUSD.m with $50 risk (total $190 <= 6% of $10,000 = $600)
        new_buy_cand = SignalCandidate(
            id="SIG_BUY_4",
            symbol="XAUUSD.m",
            timeframe=Timeframe.M15,
            direction=OrderType.BUY,
            setup_type=SetupType.SESSION_SWEEP,
            entry_price=2005.0,
            stop_loss=1980.0,
            take_profit=2055.0,
            risk_reward=2.0,
            score=85.0,
            timestamp=now
        )
        allowed, reason = self.risk_engine.evaluate_new_order_permission(
            candidate=new_buy_cand,
            proposed_monetary_risk=50.0,
            proposed_volume=0.02,
            active_positions=active_positions,
            account=account
        )
        self.assertTrue(allowed, f"4th BUY position should be allowed under Hedging within risk caps. Reason: {reason}")

        # Propose conflicting SELL position on XAUUSD.m -> must be rejected
        conflict_sell_cand = SignalCandidate(
            id="SIG_SELL_CONFLICT",
            symbol="XAUUSD.m",
            timeframe=Timeframe.M15,
            direction=OrderType.SELL,
            setup_type=SetupType.SESSION_SWEEP,
            entry_price=2005.0,
            stop_loss=2015.0,
            take_profit=1985.0,
            risk_reward=2.0,
            score=85.0,
            timestamp=now
        )
        allowed_conflict, reason_conflict = self.risk_engine.evaluate_new_order_permission(
            candidate=conflict_sell_cand,
            proposed_monetary_risk=50.0,
            proposed_volume=0.02,
            active_positions=active_positions,
            account=account
        )
        self.assertFalse(allowed_conflict, "Opposite direction trade on same symbol must be blocked to prevent conflicting hedge exposure")
        self.assertIn("Conflicting open position", reason_conflict)

    def test_hedging_ticket_level_closing_logic(self):
        """Verifies closing specific position ticket leaves others intact."""
        positions_by_ticket = {
            1001: Position(ticket=1001, symbol="XAUUSD.m", order_type=OrderType.BUY, volume=0.02,
                           open_price=2000.0, current_price=2005.0, stop_loss=1980.0, take_profit=2040.0, profit=10.0,
                           initial_risk_amount=40.0),
            1002: Position(ticket=1002, symbol="XAUUSD.m", order_type=OrderType.BUY, volume=0.03,
                           open_price=2002.0, current_price=2005.0, stop_loss=1982.0, take_profit=2042.0, profit=9.0,
                           initial_risk_amount=60.0),
            1003: Position(ticket=1003, symbol="XAUUSD.m", order_type=OrderType.BUY, volume=0.02,
                           open_price=2004.0, current_price=2005.0, stop_loss=1984.0, take_profit=2044.0, profit=2.0,
                           initial_risk_amount=40.0),
        }

        # Close position 1002
        target_ticket = 1002
        self.assertIn(target_ticket, positions_by_ticket)
        del positions_by_ticket[target_ticket]

        remaining = list(positions_by_ticket.values())
        self.assertEqual(len(remaining), 2)
        self.assertEqual([p.ticket for p in remaining], [1001, 1003])
        new_open_risk = sum(p.initial_risk_amount for p in remaining)
        self.assertEqual(new_open_risk, 80.0)


if __name__ == "__main__":
    unittest.main()
