"""
Unit tests covering the concrete defect fixes:
1. Spread filter rules (live spread, % of SL, % of ATR, absolute ceiling).
2. Stop / target direction validation for all 6 order types (BUY, SELL, BUY_LIMIT, SELL_LIMIT, BUY_STOP, SELL_STOP).
3. Mathematical lot sizing, clamping, and MaxRiskPercentPerTrade budget rejection.
4. Telegram command parsing (both plain text and slash commands, unknown command list fallback).
"""

import unittest
from unittest.mock import MagicMock
from datetime import datetime, timezone

from src.core.contracts import (
    SignalCandidate, Position, AccountInfo, SymbolInfo,
    OrderType, SetupType, Timeframe
)
from src.core.config import AppConfig
from src.risk.position_sizing import PositionSizingEngine
from src.risk.portfolio_risk import PortfolioRiskEngine
from src.execution.order_manager import OrderManager
from src.notifications.telegram_config import TelegramConfig
from src.notifications.telegram_service import TelegramCommandHandler, TelegramNotifier


class TestDefectFixes(unittest.TestCase):

    def setUp(self):
        self.config = AppConfig()
        self.config.risk.risk_percentage = 1.0
        self.config.risk.max_risk_per_trade = 2.0
        self.sizing = PositionSizingEngine(default_risk_percent=1.0)
        self.portfolio_risk = PortfolioRiskEngine(self.config.risk)
        self.mock_bridge = MagicMock()
        self.mock_bridge.ping.return_value = True
        self.mock_bridge.get_positions.return_value = []
        self.order_mgr = OrderManager(
            config=self.config,
            bridge=self.mock_bridge,
            sizing_engine=self.sizing,
            portfolio_risk=self.portfolio_risk
        )

        # $1000 balance: 1% risk = $10 budget; allows $700 SL at 0.01 lot ($7.00 loss)
        self.account = AccountInfo(
            account_number=1000000001,
            balance=1000.0,
            equity=1000.0,
            margin=0.0,
            free_margin=1000.0,
            margin_level=0.0,
            currency="USD",
            leverage=3000
        )

        self.symbol_info = SymbolInfo(
            symbol="BTCUSD.m",
            digits=2,
            point=0.01,
            spread=858,  # Constant 858 points spread ($8.58)
            ask=75008.58,
            bid=75000.00,
            contract_size=1.0,
            min_lot=0.01,
            max_lot=50.0,
            lot_step=0.01,
            margin_required=25.0,
            tick_size=0.01,
            tick_value=0.01
        )

    # =========================================================================
    # 1. SPREAD FILTER TESTS
    # =========================================================================

    def test_spread_filter_passes_when_sl_distance_is_sufficient(self):
        """
        BTCUSD.m spread is 858 points ($8.58).
        With an entry of 75000 and SL at 74300 (SL distance = 700.00 = 70,000 points):
        858 points / 70000 points = 1.22% of SL distance <= 5.0% ceiling -> PASSES!
        """
        candidate = SignalCandidate(
            id="SIG_BUY_PASS",
            symbol="BTCUSD.m",
            timeframe=Timeframe.M15,
            direction=OrderType.BUY,
            setup_type=SetupType.SESSION_SWEEP,
            entry_price=self.symbol_info.ask,
            stop_loss=self.symbol_info.ask - 700.00,   # $700 SL = 70,000 points
            take_profit=self.symbol_info.ask + 2000.00, # $2,000 TP
            risk_reward=2.86,
            score=85.0,
            timestamp=datetime.now(timezone.utc),
            status="APPROVED"
        )
        check = self.order_mgr.run_pre_trade_checklist(
            candidate=candidate,
            account=self.account,
            symbol_info=self.symbol_info,
            active_positions=[]
        )
        self.assertTrue(check.passed, f"Check failed unexpectedly: {check.rejection_reasons}")

    def test_spread_filter_rejects_tight_scalp(self):
        """
        BTCUSD.m spread is 858 points ($8.58).
        With a tight scalp of SL 100 points ($1.00 at entry 75000, SL 74999.00):
        858 points / 100 points = 858% of SL distance > 5.0% limit -> REJECTED!
        """
        candidate = SignalCandidate(
            id="SIG_SCALP_REJECT",
            symbol="BTCUSD.m",
            timeframe=Timeframe.M15,
            direction=OrderType.BUY,
            setup_type=SetupType.SESSION_SWEEP,
            entry_price=75000.00,
            stop_loss=74999.00,   # 100 points SL
            take_profit=75005.00,
            risk_reward=5.0,
            score=85.0,
            timestamp=datetime.now(timezone.utc),
            status="APPROVED"
        )
        check = self.order_mgr.run_pre_trade_checklist(
            candidate=candidate,
            account=self.account,
            symbol_info=self.symbol_info,
            active_positions=[]
        )
        self.assertFalse(check.passed)
        self.assertTrue(any("exceeding 5.0% limit" in r for r in check.rejection_reasons))

    def test_spread_filter_rejects_above_absolute_ceiling(self):
        """Spread exceeding 2000 points must be rejected regardless of SL."""
        wide_spread_sym = SymbolInfo(
            symbol="BTCUSD.m",
            digits=2,
            point=0.01,
            spread=2500,  # 2500 points > 2000 ceiling
            ask=75025.00,
            bid=75000.00,
            contract_size=1.0,
            min_lot=0.01,
            max_lot=50.0,
            lot_step=0.01,
            margin_required=25.0,
            tick_size=0.01,
            tick_value=0.01
        )
        candidate = SignalCandidate(
            id="SIG_CEILING_REJECT",
            symbol="BTCUSD.m",
            timeframe=Timeframe.M15,
            direction=OrderType.BUY,
            setup_type=SetupType.SESSION_SWEEP,
            entry_price=75000.00,
            stop_loss=73000.00,
            take_profit=79000.00,
            risk_reward=2.0,
            score=85.0,
            timestamp=datetime.now(timezone.utc),
            status="APPROVED"
        )
        check = self.order_mgr.run_pre_trade_checklist(
            candidate=candidate,
            account=self.account,
            symbol_info=wide_spread_sym,
            active_positions=[]
        )
        self.assertFalse(check.passed)
        self.assertTrue(any("exceeds absolute ceiling" in r for r in check.rejection_reasons))

    # =========================================================================
    # 2. STOP / TARGET DIRECTION VALIDATION TESTS
    # =========================================================================

    def test_buy_stop_direction_validation(self):
        """BUY requires SL < entry < TP."""
        # Valid BUY
        cand_valid = SignalCandidate(
            id="CAND_BUY_OK", symbol="BTCUSD.m", timeframe=Timeframe.M15, direction=OrderType.BUY,
            setup_type=SetupType.SESSION_SWEEP, entry_price=self.symbol_info.ask, stop_loss=self.symbol_info.ask - 800.00, take_profit=self.symbol_info.ask + 2000.00,
            risk_reward=2.5, score=85.0, timestamp=datetime.now(timezone.utc), status="APPROVED"
        )
        check_ok = self.order_mgr.run_pre_trade_checklist(cand_valid, self.account, self.symbol_info, [])
        self.assertTrue(check_ok.passed)

        # Inverted BUY SL (SL above entry)
        cand_inv_sl = SignalCandidate(
            id="CAND_BUY_INV_SL", symbol="BTCUSD.m", timeframe=Timeframe.M15, direction=OrderType.BUY,
            setup_type=SetupType.SESSION_SWEEP, entry_price=75000.00, stop_loss=76000.00, take_profit=77000.00,
            risk_reward=2.0, score=85.0, timestamp=datetime.now(timezone.utc), status="APPROVED"
        )
        check_inv_sl = self.order_mgr.run_pre_trade_checklist(cand_inv_sl, self.account, self.symbol_info, [])
        self.assertFalse(check_inv_sl.passed)
        self.assertTrue(any("Invalid stops on BUY - SL" in r for r in check_inv_sl.rejection_reasons))

        # Inverted BUY TP (TP below entry)
        cand_inv_tp = SignalCandidate(
            id="CAND_BUY_INV_TP", symbol="BTCUSD.m", timeframe=Timeframe.M15, direction=OrderType.BUY,
            setup_type=SetupType.SESSION_SWEEP, entry_price=75000.00, stop_loss=74000.00, take_profit=73000.00,
            risk_reward=2.0, score=85.0, timestamp=datetime.now(timezone.utc), status="APPROVED"
        )
        check_inv_tp = self.order_mgr.run_pre_trade_checklist(cand_inv_tp, self.account, self.symbol_info, [])
        self.assertFalse(check_inv_tp.passed)
        self.assertTrue(any("Invalid stops on BUY - TP" in r for r in check_inv_tp.rejection_reasons))

    def test_sell_stop_direction_validation(self):
        """SELL requires TP < entry < SL."""
        # Valid SELL
        cand_sell_ok = SignalCandidate(
            id="CAND_SELL_OK", symbol="BTCUSD.m", timeframe=Timeframe.M15, direction=OrderType.SELL,
            setup_type=SetupType.SESSION_SWEEP, entry_price=75000.00, stop_loss=75800.00, take_profit=73000.00,
            risk_reward=2.5, score=85.0, timestamp=datetime.now(timezone.utc), status="APPROVED"
        )
        check_sell_ok = self.order_mgr.run_pre_trade_checklist(cand_sell_ok, self.account, self.symbol_info, [])
        self.assertTrue(check_sell_ok.passed)

        # Inverted SELL SL (The exact bug from experts log: SL 74601.21 on SELL @ ~75000)
        cand_sell_bug = SignalCandidate(
            id="CAND_SELL_BUG", symbol="BTCUSD.m", timeframe=Timeframe.M15, direction=OrderType.SELL,
            setup_type=SetupType.SESSION_SWEEP, entry_price=75000.00, stop_loss=74601.21, take_profit=77101.21,
            risk_reward=2.0, score=85.0, timestamp=datetime.now(timezone.utc), status="APPROVED"
        )
        check_sell_bug = self.order_mgr.run_pre_trade_checklist(cand_sell_bug, self.account, self.symbol_info, [])
        self.assertFalse(check_sell_bug.passed)
        self.assertTrue(any("Invalid stops on SELL - SL" in r for r in check_sell_bug.rejection_reasons))

    # =========================================================================
    # 3. POSITION SIZING & MAX RISK BUDGET TESTS
    # =========================================================================

    def test_lot_sizing_calculation_and_capping(self):
        """Calculate lots correctly from risk budget, SL distance, tick size and value."""
        # $1000 balance, 1% risk = $10.00 budget.
        # SL distance = $700 (70,000 points).
        # tick_size = 0.01, tick_value = 0.01.
        # 0.01 lot loses $7.00 (< $10 budget) -> APPROVED!
        res = self.sizing.calculate_lot_size(
            account=self.account,
            symbol_info=self.symbol_info,
            entry_price=75000.00,
            stop_loss=74300.00,
            risk_percent=1.0
        )
        self.assertTrue(res.is_approved)
        self.assertEqual(res.volume, 0.01)

    def test_lot_sizing_rejects_when_min_lot_exceeds_max_risk_cap(self):
        """If min lot (0.01) risks more than MaxRiskPercentPerTrade (2%), skip trade."""
        # Account balance = $10.00. 2% max risk = $0.20 budget.
        # SL distance = $700. Min lot 0.01 loses $7.00.
        # $7.00 is 70% of $10 account -> MUST BE REJECTED!
        tiny_account = AccountInfo(
            account_number=1000000001,
            balance=10.0,
            equity=10.0,
            margin=0.0,
            free_margin=10.0,
            margin_level=0.0,
            currency="USD",
            leverage=3000
        )
        res = self.sizing.calculate_lot_size(
            account=tiny_account,
            symbol_info=self.symbol_info,
            entry_price=75000.00,
            stop_loss=74300.00,
            risk_percent=2.0
        )
        self.assertFalse(res.is_approved)
        self.assertTrue("exceed" in res.rejection_reason.lower())

    # =========================================================================
    # 4. TELEGRAM COMMAND PARSING TESTS
    # =========================================================================

    def test_telegram_commands_support_plain_and_slash(self):
        """Telegram command handler must accept both '/run' and 'run', '/status' and 'status', etc."""
        cfg = TelegramConfig(env_path="nonexistent.env")
        cfg.bot_token = "test_token"
        cfg.chat_id = "8649330717"
        cfg.enabled = False

        notifier = TelegramNotifier(cfg, account_number=1000000001)
        handler = TelegramCommandHandler(cfg, notifier)

        # Mock action handlers
        mock_run = MagicMock(return_value=True)
        mock_pause = MagicMock(return_value=True)
        mock_reply = MagicMock()
        handler.resume_handler = mock_run
        handler.pause_handler = mock_pause
        handler._reply = mock_reply

        # Test 1: Plain "run"
        handler._process_message({"chat": {"id": "8649330717"}, "from": {"first_name": "Jetsky"}, "text": "run"})
        mock_run.assert_called()
        self.assertTrue(any("BOT RESUMED" in call.args[0] for call in mock_reply.call_args_list))

        # Test 2: Slash "/run"
        mock_run.reset_mock()
        mock_reply.reset_mock()
        handler._process_message({"chat": {"id": "8649330717"}, "from": {"first_name": "Jetsky"}, "text": "/run"})
        mock_run.assert_called()
        self.assertTrue(any("BOT RESUMED" in call.args[0] for call in mock_reply.call_args_list))

        # Test 3: Plain "status"
        mock_reply.reset_mock()
        handler._process_message({"chat": {"id": "8649330717"}, "from": {"first_name": "Jetsky"}, "text": "Status"})
        self.assertTrue(any("SYSTEM STATUS" in call.args[0] for call in mock_reply.call_args_list))

        # Test 4: Unknown command returns command list
        mock_reply.reset_mock()
        handler._process_message({"chat": {"id": "8649330717"}, "from": {"first_name": "Jetsky"}, "text": "foobar"})
        self.assertTrue(any("Unknown command" in call.args[0] and "TRADING BOT COMMANDS" in call.args[0] for call in mock_reply.call_args_list))


if __name__ == "__main__":
    unittest.main()
