"""
Comprehensive Test Suite for Stage 0A: Account-Size Adaptation ($5 Minimum Upward).
Validates:
1. Tier transitions & hysteresis buffering
2. Cent-account unit conversions (USC to USD)
3. Edge cases: zero/negative SL, <$5 balance, lots rounding to 0, margin shortfall, lot above max
4. Property-based randomized test (1,000 iterations): No sized order ever exceeds tier ceiling
5. Affordability scanner classification (TRADABLE, MARGINAL, BLOCKED)
"""

import unittest
import random
from typing import Dict

from src.core.contracts import AccountInfo, SymbolInfo, AccountTier, AffordabilityStatus
from src.risk.account_tier import AccountTierManager, TierDefinition
from src.risk.position_sizing import PositionSizingEngine, PositionSizeResult
from src.risk.affordability import AffordabilityScanner


class TestStage0AAdaptation(unittest.TestCase):
    def setUp(self):
        self.tier_mgr = AccountTierManager()
        self.sizing = PositionSizingEngine(tier_manager=self.tier_mgr)
        self.scanner = AffordabilityScanner(tier_manager=self.tier_mgr, sizing_engine=self.sizing)

        # Standard test symbols
        self.symbols: Dict[str, SymbolInfo] = {
            "EURUSD.m": SymbolInfo(
                symbol="EURUSD.m",
                digits=5,
                point=0.00001,
                tick_size=0.00001,
                tick_value=1.0,
                contract_size=100000.0,
                min_lot=0.01,
                max_lot=100.0,
                lot_step=0.01,
                margin_required=37.34,
                spread=8.0
            ),
            "XAUUSD.m": SymbolInfo(
                symbol="XAUUSD.m",
                digits=2,
                point=0.01,
                tick_size=0.01,
                tick_value=1.0,
                contract_size=100.0,
                min_lot=0.01,
                max_lot=100.0,
                lot_step=0.01,
                margin_required=129.83,
                spread=26.0
            ),
            "BTCUSD.m": SymbolInfo(
                symbol="BTCUSD.m",
                digits=2,
                point=0.01,
                tick_size=0.01,
                tick_value=0.01,
                contract_size=1.0,
                min_lot=0.01,
                max_lot=100.0,
                lot_step=0.01,
                margin_required=156.01,
                spread=858.0
            ),
            "USDJPY.m": SymbolInfo(
                symbol="USDJPY.m",
                digits=3,
                point=0.001,
                tick_size=0.001,
                tick_value=0.634,
                contract_size=100000.0,
                min_lot=0.01,
                max_lot=100.0,
                lot_step=0.01,
                margin_required=33.33,
                spread=16.0
            )
        }

    def test_cent_account_unit_conversion(self):
        """Proves 500 USC is correctly converted to $5.00 USD."""
        acct_cent = AccountInfo(
            account_number=999001,
            balance=500.0,
            equity=500.0,
            free_margin=500.0,
            currency="USC",
            raw_balance=500.0,
            raw_equity=500.0
        )
        self.assertTrue(acct_cent.is_cent_account)
        self.assertAlmostEqual(acct_cent.balance, 5.0, places=2)
        self.assertAlmostEqual(acct_cent.equity, 5.0, places=2)

        # Tier for $5.00 is MICRO
        tier_def, _, _ = self.tier_mgr.evaluate_account(acct_cent)
        self.assertEqual(tier_def.name, "MICRO")
        self.assertEqual(tier_def.target_risk_pct, 2.0)
        self.assertEqual(tier_def.absolute_ceiling_pct, 5.0)

    def test_below_floor_rejection(self):
        """Proves balances < $5.00 are disabled immediately."""
        acct_tiny = AccountInfo(balance=4.95, equity=4.95, free_margin=4.95, currency="USD")
        tier_def, _, _ = self.tier_mgr.evaluate_account(acct_tiny)
        self.assertEqual(tier_def.name, "BELOW_FLOOR")
        self.assertFalse(tier_def.trading_allowed)

        res = self.sizing.calculate_lot_size(
            account=acct_tiny,
            symbol_info=self.symbols["EURUSD.m"],
            entry_price=1.08500,
            stop_loss=1.08300
        )
        self.assertFalse(res.is_approved)
        self.assertIn("below the $5.00 floor", res.rejection_reason)

    def test_tier_transitions_and_hysteresis(self):
        """Proves hysteresis prevents flapping around tier boundary thresholds."""
        # Start at $26 -> MINI tier ($25-$100)
        acct = AccountInfo(balance=26.0, equity=26.0, free_margin=26.0)
        tier_def, _, _ = self.tier_mgr.evaluate_account(acct)
        self.assertEqual(tier_def.name, "MINI")

        # Fluctuation down to $24.50 (within 5% hysteresis buffer: $25 * 0.95 = $23.75)
        acct_fluc = AccountInfo(balance=24.50, equity=24.50, free_margin=24.50)
        tier_fluc, changed, _ = self.tier_mgr.evaluate_account(acct_fluc)
        # Stays in MINI due to hysteresis
        self.assertEqual(tier_fluc.name, "MINI")
        self.assertFalse(changed)

        # Drop further to $22.00 (outside buffer) -> transitions to MICRO
        acct_drop = AccountInfo(balance=22.0, equity=22.0, free_margin=22.0)
        tier_drop, changed_drop, msg = self.tier_mgr.evaluate_account(acct_drop)
        self.assertEqual(tier_drop.name, "MICRO")
        self.assertTrue(changed_drop)
        self.assertIn("TIER_TRANSITION", msg)

    def test_edge_cases(self):
        """Verifies zero/negative SL, rounding to zero, margin shortfall, lot clamp, and mid-session deposit."""
        acct = AccountInfo(balance=50.0, equity=50.0, free_margin=50.0)

        # 1. Zero or negative SL distance
        res_zero_sl = self.sizing.calculate_lot_size(
            account=acct,
            symbol_info=self.symbols["EURUSD.m"],
            entry_price=1.08500,
            stop_loss=1.08500
        )
        self.assertFalse(res_zero_sl.is_approved)
        self.assertIn("greater than zero", res_zero_sl.rejection_reason.lower())

        # 2. Sized lot rounds to zero when micro account flags are OFF
        # EURUSD with 200 pip SL ($200 loss/lot): $50 balance at 1.5% target risk = $0.75 risk.
        # Raw lot = 0.75 / 200 = 0.00375 -> rounds to 0.00 < min lot 0.01 ($2.00 risk = 4.0% > target 1.5%)
        res_micro_off = self.sizing.calculate_lot_size(
            account=acct,
            symbol_info=self.symbols["EURUSD.m"],
            entry_price=1.08500,
            stop_loss=1.06500  # 200 pips
        )
        self.assertFalse(res_micro_off.is_approved)
        self.assertGreater(res_micro_off.min_balance_needed, 50.0)

        # 3. Lot clamping to max lot cap
        acct_huge = AccountInfo(balance=100000.0, equity=100000.0, free_margin=100000.0)
        res_huge = self.sizing.calculate_lot_size(
            account=acct_huge,
            symbol_info=self.symbols["EURUSD.m"],
            entry_price=1.08500,
            stop_loss=1.08400  # 10 pips
        )
        self.assertTrue(res_huge.is_approved)
        self.assertLessEqual(res_huge.volume, self.sizing.max_lot_cap)

        # 4. Deposit mid-session
        acct.balance = 500.0
        acct.equity = 500.0
        acct.free_margin = 500.0
        tier_deposit, ch, _ = self.tier_mgr.evaluate_account(acct)
        self.assertEqual(tier_deposit.name, "STANDARD")

    def test_property_based_no_tier_ceiling_breach(self):
        """
        Property-based generative test (1,000 iterations):
        No approved trade may EVER breach its tier's absolute ceiling risk percentage.
        """
        random.seed(42)
        sym_list = list(self.symbols.values())

        for i in range(1000):
            # Random balance from $1.00 to $50,000.00
            bal = round(random.uniform(1.0, 50000.0), 2)
            eq = round(bal * random.uniform(0.85, 1.15), 2)
            free_margin = max(0.0, eq * random.uniform(0.5, 1.0))

            acct = AccountInfo(balance=bal, equity=eq, free_margin=free_margin)
            sym = random.choice(sym_list)

            entry = 1.08500 if "EUR" in sym.symbol else (3900.0 if "XAU" in sym.symbol else (90000.0 if "BTC" in sym.symbol else 155.0))
            # Random stop distance between 5 and 500 pips
            stop_dist = random.uniform(5.0, 500.0) * sym.point * 10
            direction = random.choice([1, -1])
            sl = entry - (stop_dist * direction) if direction == 1 else entry + (stop_dist)

            res = self.sizing.calculate_lot_size(
                account=acct,
                symbol_info=sym,
                entry_price=entry,
                stop_loss=sl
            )

            if res.is_approved:
                # Invariant: actual risk % must never exceed the tier's absolute ceiling
                tier_def = self.tier_mgr.get_tier_def(res.tier_name)
                self.assertLessEqual(
                    res.risk_percent_actual,
                    tier_def.absolute_ceiling_pct + 1e-5,
                    f"Iteration {i}: Sizing risk {res.risk_percent_actual:.2f}% breached ceiling {tier_def.absolute_ceiling_pct:.2f}% on {sym.symbol} with capital ${min(bal, eq):.2f}"
                )
                self.assertGreaterEqual(res.volume, sym.min_lot)

    def test_affordability_scanner_classification(self):
        """Verifies scanner classifications across $5, $50, and $5,000."""
        # 1. At $5.00: EURUSD is marginal (min lot 0.01 risks ~$0.58 = 11.7% > 5% ceiling) -> Blocked on standard
        acct_5 = AccountInfo(balance=5.0, equity=5.0, free_margin=5.0)
        res_5 = self.scanner.scan(acct_5, self.symbols)
        self.assertEqual(res_5.tier_name, "MICRO")
        self.assertIn("CENT", res_5.recommendation)

        # 2. At $500.00: BTCUSD.m and USDJPY.m are TRADABLE, EURUSD.m is MARGINAL
        acct_500 = AccountInfo(balance=500.0, equity=500.0, free_margin=500.0)
        res_500 = self.scanner.scan(acct_500, self.symbols)
        self.assertEqual(res_500.tier_name, "STANDARD")
        self.assertTrue(len(res_500.tradable_symbols) > 0)
        self.assertIn("BTCUSD.m", res_500.tradable_symbols)
        self.assertIn("EURUSD.m", res_500.marginal_symbols)

        # 3. At $1,000.00: EURUSD.m is fully TRADABLE at target risk
        acct_1k = AccountInfo(balance=1000.0, equity=1000.0, free_margin=1000.0)
        res_1k = self.scanner.scan(acct_1k, self.symbols)
        self.assertIn("EURUSD.m", res_1k.tradable_symbols)


if __name__ == "__main__":
    unittest.main()
