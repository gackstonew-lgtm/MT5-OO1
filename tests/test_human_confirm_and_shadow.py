"""
Unit tests for Phase 4: Human Confirm Gate & Paper Shadow Journal.
"""

import unittest
from datetime import datetime, timezone, timedelta

from src.core.contracts import (
    TradePlan, OrderType, Timeframe, SignalCandidate, SetupType,
    AccountInfo, SymbolInfo, ExecutionMode, Candle
)
from src.core.config import AppConfig
from src.risk.position_sizing import PositionSizingEngine
from src.risk.portfolio_risk import PortfolioRiskEngine
from src.execution.order_manager import OrderManager
from src.monitoring.shadow_journal import ShadowJournal


class DummyBridge:
    def ping(self) -> bool:
        return True
    def get_positions(self):
        return []
    def execute_trade(self, **kwargs):
        return {"status": "SUCCESS", "ticket": 777123, "price": kwargs.get("price", 1.1000)}


class DummyTelegramHandler:
    def __init__(self, should_confirm: bool = True):
        self.confirm_mode = True
        self.should_confirm = should_confirm
        self.requested_plan = None

    def request_trade_confirmation(self, plan, timeout_sec=120):
        self.requested_plan = plan
        return self.should_confirm


class TestHumanConfirmAndShadow(unittest.TestCase):
    def setUp(self):
        self.now = datetime.now(timezone.utc)
        self.config = AppConfig()
        self.bridge = DummyBridge()
        self.sizing = PositionSizingEngine()
        self.portfolio_risk = PortfolioRiskEngine(config=self.config.risk)
        self.account = AccountInfo(account_number=1001, balance=10000.0, equity=10000.0, margin=0.0, free_margin=10000.0)
        self.sym_info = SymbolInfo(
            symbol="EURUSD", digits=5, point=0.00001, tick_size=0.00001, tick_value=1.0,
            min_lot=0.01, max_lot=50.0, lot_step=0.01, spread=5.0, ask=1.10005, bid=1.10000
        )

    def test_demo_mode_auto_executes_without_human_block(self):
        """Verifies DEMO mode auto-executes immediately even if confirm_mode is active."""
        self.config.execution_mode = ExecutionMode.DEMO
        order_mgr = OrderManager(self.config, self.bridge, self.sizing, self.portfolio_risk)
        dummy_tg = DummyTelegramHandler(should_confirm=False)
        order_mgr.telegram_cmd_handler = dummy_tg

        candidate = SignalCandidate(
            id="SIG_DEMO_01",
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
            reasons=["BOS confirmed"]
        )

        result = order_mgr.run_pre_trade_checklist(candidate, self.account, self.sym_info, [])
        self.assertTrue(result.passed)
        self.assertIsNone(dummy_tg.requested_plan)  # Never prompted

    def test_live_mode_confirms_when_operator_approves(self):
        """Verifies LIVE mode prompts operator and passes when confirmed."""
        self.config.execution_mode = ExecutionMode.REAL
        order_mgr = OrderManager(self.config, self.bridge, self.sizing, self.portfolio_risk)
        dummy_tg = DummyTelegramHandler(should_confirm=True)
        order_mgr.telegram_cmd_handler = dummy_tg

        candidate = SignalCandidate(
            id="SIG_LIVE_01",
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
            reasons=["BOS confirmed"]
        )

        result = order_mgr.run_pre_trade_checklist(candidate, self.account, self.sym_info, [])
        self.assertTrue(result.passed)
        self.assertIsNotNone(dummy_tg.requested_plan)
        self.assertEqual(dummy_tg.requested_plan.symbol, "EURUSD")

    def test_live_mode_rejects_on_timeout_or_operator_refusal(self):
        """Verifies LIVE mode blocks order when confirmation times out or is cancelled."""
        self.config.execution_mode = ExecutionMode.REAL
        order_mgr = OrderManager(self.config, self.bridge, self.sizing, self.portfolio_risk)
        dummy_tg = DummyTelegramHandler(should_confirm=False)
        order_mgr.telegram_cmd_handler = dummy_tg

        candidate = SignalCandidate(
            id="SIG_LIVE_02",
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
            reasons=["BOS confirmed"]
        )

        result = order_mgr.run_pre_trade_checklist(candidate, self.account, self.sym_info, [])
        self.assertFalse(result.passed)
        self.assertTrue(any("Human Confirm" in r for r in result.rejection_reasons))

    def test_shadow_journal_records_and_updates_forward(self):
        """Verifies ShadowJournal records setups and tracks hypothetical outcomes forward."""
        import tempfile
        temp_journal = tempfile.mktemp(suffix=".json")
        journal = ShadowJournal(journal_path=temp_journal)

        # Record a taken setup
        e1 = journal.record_setup(
            setup_id="SET_01",
            symbol="EURUSD",
            direction=OrderType.BUY,
            entry_price=1.1000,
            stop_loss=1.0950,
            take_profit=1.1100,
            reward_to_risk=2.0,
            decision="TAKEN"
        )
        # Record a rejected setup (filtered by BOS)
        e2 = journal.record_setup(
            setup_id="SET_02",
            symbol="EURUSD",
            direction=OrderType.SELL,
            entry_price=1.1000,
            stop_loss=1.1050,
            take_profit=1.0900,
            reward_to_risk=2.0,
            decision="REJECTED_BOS",
            rejection_reasons=["Structure confirmed check failed: CHoCH only, awaiting BOS"]
        )

        # Advance candle that hits TP for BUY and would have stopped out SELL (protecting capital)
        c1 = Candle(
            time=self.now,
            open=1.1000,
            high=1.1105,  # Hits TP on e1 (1.1100) and hits SL on e2 (1.1050)
            low=1.0990,
            close=1.1102,
            tick_volume=100
        )
        journal.update_candle("EURUSD", c1)

        self.assertEqual(e1.status, "HIT_TP")
        self.assertEqual(e1.outcome_r, 2.0)

        self.assertEqual(e2.status, "HIT_SL")
        self.assertEqual(e2.outcome_r, -1.0)

        rep = journal.generate_report()
        self.assertEqual(rep["categories"]["TAKEN"]["win_rate"], 100.0)
        self.assertEqual(rep["categories"]["REJECTED_BOS"]["avg_r"], -1.0)

        text_rep = journal.format_telegram_report()
        self.assertIn("PAPER SHADOW VALIDATION REPORT", text_rep)
        self.assertIn("Filtered by BOS Gate", text_rep)


if __name__ == "__main__":
    unittest.main()
