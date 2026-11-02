"""
Unit tests for Mathematical Position Sizing Engine.
"""

import unittest
from src.core.contracts import AccountInfo, SymbolInfo
from src.risk.position_sizing import PositionSizingEngine


class TestPositionSizingEngine(unittest.TestCase):
    def setUp(self):
        self.sizing = PositionSizingEngine(default_risk_percent=0.5)
        self.account = AccountInfo(
            balance=10000.0,
            equity=10000.0,
            free_margin=10000.0,
            currency="USD"
        )
        self.symbol_info = SymbolInfo(
            symbol="EURUSD",
            digits=5,
            point=0.00001,
            tick_size=0.00001,
            tick_value=1.0,
            contract_size=100000.0,
            min_lot=0.01,
            max_lot=100.0,
            lot_step=0.01,
            margin_required=1000.0
        )

    def test_standard_position_sizing(self):
        # 0.5% of 10,000 = $50 risk
        # Entry = 1.0850, SL = 1.0830 -> 20 pips = 200 ticks
        # Loss per 1.0 lot = 200 * $1.0 = $200
        # Target volume = $50 / $200 = 0.25 lots
        res = self.sizing.calculate_lot_size(
            account=self.account,
            symbol_info=self.symbol_info,
            entry_price=1.0850,
            stop_loss=1.0830,
            risk_percent=0.5,
            sizing_reference="EQUITY"
        )

        self.assertTrue(res.is_approved)
        self.assertEqual(res.volume, 0.25)
        self.assertAlmostEqual(res.monetary_risk, 52.05, places=1)
        self.assertAlmostEqual(res.risk_percent_actual, 0.5, places=2)

    def test_reject_min_lot_risk_budget_breach(self):
        # Suppose a broker symbol requires minimum lot = 1.0 lot
        # With 20 pips SL, 1.0 lot loses $200, which exceeds $50 risk budget!
        # The engine MUST reject rather than forcing an oversized position!
        broker_high_min = SymbolInfo(
            symbol="EURUSD",
            min_lot=1.0,
            max_lot=100.0,
            lot_step=0.1
        )
        res = self.sizing.calculate_lot_size(
            account=self.account,
            symbol_info=broker_high_min,
            entry_price=1.0850,
            stop_loss=1.0830,
            risk_percent=0.5
        )

        self.assertFalse(res.is_approved)
        self.assertTrue("exceed" in res.rejection_reason.lower())


if __name__ == "__main__":
    unittest.main()
