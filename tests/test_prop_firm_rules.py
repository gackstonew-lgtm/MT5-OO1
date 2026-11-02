import unittest
from datetime import datetime, timezone
from src.core.contracts import (
    SignalCandidate, OrderType, SetupType, Timeframe,
    AccountInfo, AccountProfile, DrawdownType, ExecutionMode, Position
)
from src.core.config import AppConfig, PropFirmRulesConfig, AccountProfileConfig
from src.risk.portfolio_risk import PortfolioRiskEngine

class TestPropFirmRules(unittest.TestCase):
    def setUp(self):
        self.config = AppConfig()
        self.config.account_profile = AccountProfile.PROP
        self.config.trading_enabled = True
        self.portfolio_risk = PortfolioRiskEngine(self.config)

    def test_drawdown_calculation_static(self):
        """STATIC drawdown is computed strictly from initial deposit balance."""
        prop_profile = self.config.account_profiles[AccountProfile.PROP]
        prop_profile.drawdown_type = DrawdownType.STATIC
        prop_profile.max_total_drawdown_percent = 8.0
        prop_profile.safety_buffer_percent = 80.0

        # Start: initial balance 10,000, current equity 9,500 -> 500 loss = 5.0% DD
        account = AccountInfo(
            account_number=123456,
            balance=10000.0,
            equity=9500.0,
            initial_balance=10000.0,
            peak_equity=11000.0,  # Peak was higher, but STATIC should ignore peak
            profile=AccountProfile.PROP
        )
        self.portfolio_risk.update_equity(account)
        self.assertAlmostEqual(account.drawdown_pct, 5.0, places=2)
        # 5.0% < 6.4% (80% of 8.0%), so safety buffer not yet active
        self.assertFalse(self.portfolio_risk.is_safety_buffer_active)

    def test_drawdown_calculation_trailing(self):
        """TRAILING drawdown is computed from highest high-water mark (peak equity)."""
        prop_profile = self.config.account_profiles[AccountProfile.PROP]
        prop_profile.drawdown_type = DrawdownType.TRAILING
        prop_profile.max_total_drawdown_percent = 10.0
        prop_profile.safety_buffer_percent = 80.0

        # Initial: 10,000, Peak: 12,000, Current Equity: 11,000 -> 1,000 drop from peak = 8.33% DD
        account = AccountInfo(
            account_number=123456,
            balance=11000.0,
            equity=11000.0,
            initial_balance=10000.0,
            peak_equity=12000.0,
            profile=AccountProfile.PROP
        )
        self.portfolio_risk.update_equity(account)
        # Drop from peak: (12000 - 11000) / 12000 = 8.333%
        self.assertAlmostEqual(account.drawdown_pct, 8.333, places=2)
        # 8.33% > 8.0% (80% of 10.0%), so safety buffer MUST be active!
        self.assertTrue(self.portfolio_risk.is_safety_buffer_active)

    def test_safety_buffer_blocks_new_orders(self):
        """When drawdown or daily loss reaches 80% buffer, new trades must be rejected."""
        prop_profile = self.config.account_profiles[AccountProfile.PROP]
        prop_profile.max_daily_loss_percent = 4.0
        prop_profile.safety_buffer_percent = 80.0 # Buffer threshold = 3.2%

        # Day started with 10,000 equity
        self.portfolio_risk.initialize_day(10000.0)
        self.portfolio_risk.initial_balance = 10000.0

        account = AccountInfo(
            account_number=123456,
            balance=10000.0,
            equity=9660.0, # 340 loss = 3.4% daily loss > 3.2%
            initial_balance=10000.0,
            peak_equity=10000.0,
            profile=AccountProfile.PROP,
            trading_enabled=True
        )
        self.portfolio_risk.update_equity(account)
        self.assertTrue(self.portfolio_risk.is_safety_buffer_active)

        candidate = SignalCandidate(
            id="SIG_001",
            symbol="BTCUSD.m",
            direction=OrderType.BUY,
            entry_price=70000.0,
            stop_loss=69000.0,
            take_profit=72000.0,
            setup_type=SetupType.M15_SMC_OTE,
            timeframe=Timeframe.M15,
            score=85.0,
            risk_reward=2.0,
            timestamp=datetime.now(timezone.utc)
        )
        allowed, reason = self.portfolio_risk.evaluate_new_order_permission(
            candidate=candidate,
            account=account,
            active_positions=[],
            proposed_monetary_risk=50.0,
            proposed_volume=0.01
        )
        self.assertFalse(allowed)
        self.assertIn("Safety buffer reached", reason)

    def test_trading_enabled_gate_for_prop_and_live(self):
        """On PROP or LIVE, if trading_enabled is False, orders must be strictly blocked."""
        self.config.trading_enabled = False
        account = AccountInfo(
            account_number=123456,
            balance=10000.0,
            equity=10000.0,
            profile=AccountProfile.PROP
        )
        candidate = SignalCandidate(
            id="SIG_002",
            symbol="BTCUSD.m",
            direction=OrderType.BUY,
            entry_price=70000.0,
            stop_loss=69000.0,
            take_profit=72000.0,
            setup_type=SetupType.M15_SMC_OTE,
            timeframe=Timeframe.M15,
            score=85.0,
            risk_reward=2.0,
            timestamp=datetime.now(timezone.utc)
        )
        allowed, reason = self.portfolio_risk.evaluate_new_order_permission(
            candidate=candidate,
            account=account,
            active_positions=[],
            proposed_monetary_risk=50.0,
            proposed_volume=0.01
        )
        self.assertFalse(allowed)
        self.assertIn("Trading is disabled for PROP profile", reason)

    def test_require_sl_rule(self):
        """Trades missing SL must be rejected under prop rules."""
        self.config.trading_enabled = True
        account = AccountInfo(account_number=123456, balance=10000.0, equity=10000.0, profile=AccountProfile.PROP, trading_enabled=True)
        candidate = SignalCandidate(
            id="SIG_003",
            symbol="BTCUSD.m",
            direction=OrderType.BUY,
            entry_price=70000.0,
            stop_loss=0.0, # NO SL
            take_profit=72000.0,
            setup_type=SetupType.M15_SMC_OTE,
            timeframe=Timeframe.M15,
            score=85.0,
            risk_reward=2.0,
            timestamp=datetime.now(timezone.utc)
        )
        allowed, reason = self.portfolio_risk.evaluate_new_order_permission(
            candidate=candidate,
            account=account,
            active_positions=[],
            proposed_monetary_risk=50.0,
            proposed_volume=0.01
        )
        self.assertFalse(allowed)
        self.assertIn("Stop Loss is strictly required", reason)

    def test_no_hedging_rule(self):
        """Opening a SELL while holding a BUY on same symbol must be rejected."""
        self.config.trading_enabled = True
        account = AccountInfo(account_number=123456, balance=10000.0, equity=10000.0, profile=AccountProfile.PROP, trading_enabled=True)
        existing_pos = Position(
            ticket=101,
            symbol="BTCUSD.m",
            order_type=OrderType.BUY,
            volume=0.01,
            open_price=70000.0,
            current_price=70500.0,
            stop_loss=69500.0,
            take_profit=72000.0,
            profit=5.0
        )
        candidate_sell = SignalCandidate(
            id="SIG_004",
            symbol="BTCUSD.m",
            direction=OrderType.SELL, # Opposite direction
            entry_price=70500.0,
            stop_loss=71500.0,
            take_profit=69000.0,
            setup_type=SetupType.M15_SMC_OTE,
            timeframe=Timeframe.M15,
            score=85.0,
            risk_reward=2.0,
            timestamp=datetime.now(timezone.utc)
        )
        allowed, reason = self.portfolio_risk.evaluate_new_order_permission(
            candidate=candidate_sell,
            account=account,
            active_positions=[existing_pos],
            proposed_monetary_risk=50.0,
            proposed_volume=0.01
        )
        self.assertFalse(allowed)
        self.assertIn("Hedging prohibited", reason)

    def test_no_grid_averaging_down_rule(self):
        """Averaging down into an existing losing position must be rejected."""
        self.config.trading_enabled = True
        account = AccountInfo(account_number=123456, balance=10000.0, equity=10000.0, profile=AccountProfile.PROP, trading_enabled=True)
        # Existing BUY is in a loss of -$25.0
        losing_pos = Position(
            ticket=102,
            symbol="BTCUSD.m",
            order_type=OrderType.BUY,
            volume=0.01,
            open_price=70000.0,
            current_price=69500.0,
            stop_loss=69000.0,
            take_profit=72000.0,
            profit=-25.0
        )
        # Attempting another BUY
        candidate_buy = SignalCandidate(
            id="SIG_005",
            symbol="BTCUSD.m",
            direction=OrderType.BUY,
            entry_price=69500.0,
            stop_loss=69000.0,
            take_profit=72000.0,
            setup_type=SetupType.M15_SMC_OTE,
            timeframe=Timeframe.M15,
            score=85.0,
            risk_reward=2.0,
            timestamp=datetime.now(timezone.utc)
        )
        allowed, reason = self.portfolio_risk.evaluate_new_order_permission(
            candidate=candidate_buy,
            account=account,
            active_positions=[losing_pos],
            proposed_monetary_risk=50.0,
            proposed_volume=0.01
        )
        self.assertFalse(allowed)
        self.assertIn("Grid/Averaging down prohibited", reason)

    def test_no_martingale_rule(self):
        """Increasing lot size directly after a loss must be rejected."""
        self.config.trading_enabled = True
        account = AccountInfo(account_number=123456, balance=10000.0, equity=10000.0, profile=AccountProfile.PROP, trading_enabled=True)
        
        # Record a closed losing trade that had volume 0.01
        self.portfolio_risk.record_closed_trade(profit=-50.0, volume=0.01)

        # Attempt to open next trade with volume 0.02 (doubled lot size)
        candidate = SignalCandidate(
            id="SIG_006",
            symbol="BTCUSD.m",
            direction=OrderType.BUY,
            entry_price=70000.0,
            stop_loss=69500.0,
            take_profit=71000.0,
            setup_type=SetupType.M15_SMC_OTE,
            timeframe=Timeframe.M15,
            score=85.0,
            risk_reward=2.0,
            timestamp=datetime.now(timezone.utc)
        )
        allowed, reason = self.portfolio_risk.evaluate_new_order_permission(
            candidate=candidate,
            account=account,
            active_positions=[],
            proposed_monetary_risk=50.0,
            proposed_volume=0.02 # Doubled lot size
        )
        self.assertFalse(allowed)
        self.assertIn("Martingale detected", reason)


if __name__ == "__main__":
    unittest.main()
