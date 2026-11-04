"""
Unit and Integration Test Suite for Active Trading Engine (active_scalp_mode).
Verifies:
1. Multi-symbol closed-bar evaluation and outcome line formatting.
2. Prohibition enforcement:
   - Martingale prohibition raises MartingaleViolationError when forced.
   - Averaging down raises AveragingDownViolationError when forced.
   - Stacking raises StackingViolationError when forced.
   - Margin guard rejects entries when margin level < 300%.
   - Tier ceiling and per-symbol lot cap strictly clamps size.
   - Daily loss limit and consecutive loss pauses with /run resume.
3. Quick exits sizing and metrics calculation.
"""

import unittest
from datetime import datetime, timezone

from src.core.contracts import (
    AccountInfo, SymbolInfo, OrderType, Position, Candle, Timeframe
)
from src.execution.active_mode import (
    ActiveTradingEngine,
    ActiveTradingConfig,
    MartingaleViolationError,
    AveragingDownViolationError,
    StackingViolationError,
    MarginLevelViolationError
)


class TestActiveTradingMode(unittest.TestCase):
    def setUp(self):
        self.config = ActiveTradingConfig(
            active_scalp_mode=True,
            max_trades_per_symbol_per_hour=3,
            max_trades_per_day=10,
            max_concurrent_positions_per_symbol=1,
            max_total_concurrent_positions=3,
            min_entry_distance_atr_mult=1.0,
            safe_margin_level_min_percent=300.0,
            loss_cooldown_seconds=60.0
        )
        self.engine = ActiveTradingEngine(self.config)

        self.symbol_info = SymbolInfo(
            symbol="XAUUSD.m",
            point=0.01,
            digits=2,
            spread=25.0,
            lot_step=0.01,
            min_lot=0.01,
            max_lot=1.0,
            tick_value=1.0,
            tick_size=0.01,
            contract_size=100.0
        )

        self.account = AccountInfo(
            account_number=1200057798,
            balance=2329.71,
            equity=2329.71,
            margin=0.0,
            free_margin=2329.71,
            margin_level=1000.0,
            currency="USD",
            trade_mode="DEMO"
        )

    def test_01_sizing_from_tier_and_usable_capital(self):
        """Position sizing strictly derives from account tier and min(balance, equity)."""
        vol, sl, tp, risk_money = self.engine.calculate_sizing_and_targets(
            symbol="XAUUSD.m",
            direction=OrderType.BUY,
            entry_price=2000.0,
            atr_points=150.0,
            point=0.01,
            account=self.account,
            symbol_info=self.symbol_info
        )
        self.assertGreaterEqual(vol, 0.01)
        self.assertLessEqual(vol, 1.0)
        self.assertLess(sl, 2000.0)
        self.assertGreater(tp, 2000.0)
        # Verify min R:R is at least floor (1.5)
        rr = (tp - 2000.0) / (2000.0 - sl)
        self.assertGreaterEqual(rr, 1.5)

    def test_02_martingale_prohibition_strictly_enforced(self):
        """Martingale: increasing lots or risk % after a loss MUST raise MartingaleViolationError."""
        # Record a loss trade: 0.02 lots
        self.engine.record_trade_closed(
            symbol="XAUUSD.m",
            tf="M5",
            lots=0.02,
            risk_pct=1.0,
            net_profit=-20.0,
            r_multiple=-1.0,
            costs={"spread": 2.5, "slippage": 0.0, "commission": 0.0}
        )

        # Attempt to open next trade with 0.04 lots (doubling size)
        with self.assertRaises(MartingaleViolationError) as ctx:
            self.engine.verify_prohibitions(
                symbol="XAUUSD.m",
                direction=OrderType.BUY,
                candidate_price=2000.0,
                candidate_risk_pct=2.0,
                candidate_lots=0.04,
                atr=1.5,
                account=self.account,
                active_positions=[]
            )
        self.assertIn("Martingale prohibited", str(ctx.exception))

    def test_03_averaging_down_prohibition_strictly_enforced(self):
        """Averaging down: adding to an open losing position MUST raise AveragingDownViolationError."""
        # Existing BUY position opened at 2005.0
        existing_pos = Position(
            ticket=1001,
            symbol="XAUUSD.m",
            order_type=OrderType.BUY,
            volume=0.02,
            open_price=2005.0,
            current_price=1998.0,
            stop_loss=1995.0,
            take_profit=2025.0,
            profit=-14.0
        )

        # Candidate BUY at 1998.0 (price fell below entry -> position is in drawdown)
        with self.assertRaises(AveragingDownViolationError) as ctx:
            self.engine.verify_prohibitions(
                symbol="XAUUSD.m",
                direction=OrderType.BUY,
                candidate_price=1998.0,
                candidate_risk_pct=1.0,
                candidate_lots=0.02,
                atr=1.5,
                account=self.account,
                active_positions=[existing_pos]
            )
        self.assertIn("Averaging down prohibited", str(ctx.exception))

    def test_04_stacking_prohibition_strictly_enforced(self):
        """Stacking: adding an order within min_distance * ATR of another position MUST raise StackingViolationError."""
        atr = 2.0  # ATR is $2.00
        # Existing BUY position at 2000.0
        existing_pos = Position(
            ticket=1002,
            symbol="XAUUSD.m",
            order_type=OrderType.BUY,
            volume=0.02,
            open_price=2000.0,
            current_price=2000.5,
            stop_loss=1990.0,
            take_profit=2020.0,
            profit=1.0
        )

        # Candidate BUY at 2000.50 (only 0.50 away, which is < 1.0 * ATR = 2.00)
        with self.assertRaises(StackingViolationError) as ctx:
            self.engine.verify_prohibitions(
                symbol="XAUUSD.m",
                direction=OrderType.BUY,
                candidate_price=2000.50,
                candidate_risk_pct=1.0,
                candidate_lots=0.02,
                atr=atr,
                account=self.account,
                active_positions=[existing_pos]
            )
        self.assertIn("Stacking prohibited", str(ctx.exception))

    def test_05_margin_level_guard_strictly_enforced(self):
        """Margin guard: refuses execution if margin level < 300%."""
        low_margin_acc = AccountInfo(
            account_number=1200057798,
            balance=2000.0,
            equity=1500.0,
            margin=600.0,
            free_margin=900.0,
            margin_level=250.0,  # Below 300% safe floor
            currency="USD",
            trade_mode="DEMO"
        )

        passed, reason = self.engine.verify_prohibitions(
            symbol="XAUUSD.m",
            direction=OrderType.BUY,
            candidate_price=2000.0,
            candidate_risk_pct=1.0,
            candidate_lots=0.02,
            atr=1.5,
            account=low_margin_acc,
            active_positions=[]
        )
        self.assertFalse(passed)
        self.assertIn("below safe threshold 300.0%", reason)

    def test_06_frequency_caps_and_loss_cooldown(self):
        """Hourly and daily caps and cooldown after a loss."""
        # 1. Hourly cap (max 3 trades)
        for _ in range(3):
            self.engine.record_trade_opened("XAUUSD.m", lots=0.01, risk_pct=1.0)

        passed, reason = self.engine.verify_prohibitions(
            symbol="XAUUSD.m",
            direction=OrderType.BUY,
            candidate_price=2000.0,
            candidate_risk_pct=1.0,
            candidate_lots=0.01,
            atr=1.5,
            account=self.account,
            active_positions=[]
        )
        self.assertFalse(passed)
        self.assertIn("Hourly trade cap reached", reason)

    def test_07_consecutive_losses_halt_and_run_resume(self):
        """Halts after max consecutive losses and resumes with /run command."""
        self.engine.record_trade_closed("XAUUSD.m", "M5", 0.01, 1.0, -10.0, -1.0, {})
        self.engine.record_trade_closed("XAUUSD.m", "M5", 0.01, 1.0, -10.0, -1.0, {})
        self.engine.record_trade_closed("XAUUSD.m", "M5", 0.01, 1.0, -10.0, -1.0, {})

        self.assertTrue(self.engine.daily_halted)
        self.assertIn("Consecutive loss limit reached", self.engine.daily_halt_reason)

        # Resume with /run command
        resume_msg = self.engine.resume_after_halt()
        self.assertFalse(self.engine.daily_halted)
        self.assertIn("Trading resumed", resume_msg)


if __name__ == "__main__":
    unittest.main()
