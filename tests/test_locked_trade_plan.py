"""
Unit tests for Phase 2: Locked Immutable TradePlan and Amendment Lifecycle.
"""

import unittest
from datetime import datetime, timezone
from dataclasses import FrozenInstanceError

from src.core.contracts import (
    TradePlan, PlanAmendment, OrderType, Timeframe, SignalCandidate,
    SetupType, AccountInfo, SymbolInfo
)
from src.core.config import AppConfig
from src.risk.position_sizing import PositionSizingEngine
from src.risk.portfolio_risk import PortfolioRiskEngine
from src.risk.trade_management import TradeManagementEngine
from src.execution.order_manager import OrderManager


class DummyBridge:
    def __init__(self, ping_ok: bool = True):
        self._ping = ping_ok

    def ping(self) -> bool:
        return self._ping

    def get_positions(self):
        return []

    def execute_trade(self, **kwargs):
        return {"status": "SUCCESS", "ticket": 123456, "price": kwargs.get("price", 1.1000)}


class TestLockedTradePlan(unittest.TestCase):
    def setUp(self):
        self.now = datetime.now(timezone.utc)
        self.plan = TradePlan(
            plan_id="PLAN_TEST_001",
            symbol="EURUSD",
            direction=OrderType.BUY,
            entry_price=1.10000,
            stop_loss=1.09500,
            take_profit=1.11000,
            lots=0.10,
            risk_percent=1.0,
            reward_to_risk=2.0,
            structure_state="BOS_CONFIRMED",
            timestamp=self.now
        )

    def test_plan_immutability(self):
        """Verifies TradePlan cannot be directly modified after creation."""
        with self.assertRaises(FrozenInstanceError):
            self.plan.entry_price = 1.10500  # type: ignore

        with self.assertRaises(FrozenInstanceError):
            self.plan.stop_loss = 1.09800  # type: ignore

        with self.assertRaises(FrozenInstanceError):
            self.plan.lots = 0.50  # type: ignore

    def test_plan_amendment_immutability(self):
        """Verifies PlanAmendment is immutable."""
        amendment = PlanAmendment(
            amendment_type="BREAK_EVEN",
            old_sl=1.09500,
            new_sl=1.10020,
            timestamp=self.now,
            reason="Break-even activated at +1.0R"
        )
        with self.assertRaises(FrozenInstanceError):
            amendment.new_sl = 1.10050  # type: ignore

    def test_plan_with_amendment_lifecycle(self):
        """Verifies TradePlan.with_amendment creates an updated immutable copy with preserved history."""
        amendment = PlanAmendment(
            amendment_type="BREAK_EVEN",
            old_sl=1.09500,
            new_sl=1.10020,
            timestamp=self.now,
            reason="Break-even activated at +1.0R"
        )
        amended_plan = self.plan.with_amendment(amendment)

        # Original plan remains untouched
        self.assertEqual(self.plan.stop_loss, 1.09500)
        self.assertEqual(len(self.plan.amendments), 0)

        # Amended plan reflects new SL and contains amendment history
        self.assertEqual(amended_plan.stop_loss, 1.10020)
        self.assertEqual(len(amended_plan.amendments), 1)
        self.assertEqual(amended_plan.amendments[0].amendment_type, "BREAK_EVEN")
        self.assertEqual(amended_plan.plan_id, self.plan.plan_id)

    def test_plan_formatting(self):
        """Verifies string log and Telegram signal text formatting."""
        log_str = self.plan.format_log()
        self.assertIn("[LOCKED_TRADE_PLAN]", log_str)
        self.assertIn("PlanID=PLAN_TEST_001", log_str)
        self.assertIn("Symbol=EURUSD", log_str)
        self.assertIn("Entry=1.10000", log_str)
        self.assertIn("SL=1.09500", log_str)
        self.assertIn("TP=1.11000", log_str)
        self.assertIn("Structure=BOS_CONFIRMED", log_str)

        sig_str = self.plan.format_signal_text()
        self.assertIn("LOCKED TRADE PLAN", sig_str)
        self.assertIn("PLAN_TEST_001", sig_str)
        self.assertIn("EURUSD", sig_str)

    def test_order_manager_creates_and_locks_plan(self):
        """Verifies OrderManager creates and locks TradePlan during checklist."""
        config = AppConfig()
        bridge = DummyBridge(ping_ok=True)
        sizing = PositionSizingEngine()
        portfolio_risk = PortfolioRiskEngine(config=config.risk)
        order_mgr = OrderManager(config=config, bridge=bridge, sizing_engine=sizing, portfolio_risk=portfolio_risk)

        candidate = SignalCandidate(
            id="SIG_EURUSD_M15_001",
            symbol="EURUSD",
            timeframe=Timeframe.M15,
            direction=OrderType.BUY,
            setup_type=SetupType.M15_SMC_OTE,
            entry_price=1.10000,
            stop_loss=1.09500,
            take_profit=1.11000,
            risk_reward=2.0,
            score=85.0,
            timestamp=self.now,
            status="APPROVED",
            reasons=["BOS confirmed by candle close"]
        )

        account = AccountInfo(account_number=1001, balance=10000.0, equity=10000.0, margin=0.0, free_margin=10000.0)
        sym_info = SymbolInfo(
            symbol="EURUSD", digits=5, point=0.00001, tick_size=0.00001, tick_value=1.0,
            min_lot=0.01, max_lot=50.0, lot_step=0.01, spread=5.0, ask=1.10005, bid=1.10000
        )

        result = order_mgr.run_pre_trade_checklist(candidate, account, sym_info, [])
        self.assertTrue(result.passed)
        self.assertIsNotNone(result.trade_plan)
        self.assertEqual(result.trade_plan.symbol, "EURUSD")
        self.assertEqual(result.trade_plan.structure_state, "BOS_CONFIRMED")
        self.assertGreater(result.trade_plan.lots, 0.0)

        # Execute and check registered plan
        order = order_mgr.execute_signal(candidate, account, sym_info, [])
        self.assertIsNotNone(order)
        stored_plan = order_mgr.get_trade_plan(order.ticket)
        self.assertIsNotNone(stored_plan)
        self.assertEqual(stored_plan.symbol, "EURUSD")

        # Amend plan (e.g. break-even)
        amendment = PlanAmendment(
            amendment_type="BREAK_EVEN",
            old_sl=1.09500,
            new_sl=1.10010,
            timestamp=datetime.now(timezone.utc),
            reason="Break-even activated at +1.0R"
        )
        updated = order_mgr.apply_plan_amendment(order.ticket, amendment)
        self.assertIsNotNone(updated)
        self.assertEqual(updated.stop_loss, 1.10010)
        self.assertEqual(len(updated.amendments), 1)


if __name__ == "__main__":
    unittest.main()
