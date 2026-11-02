"""
Comprehensive Test Suite for Universal Risk Fit Engine.
Covers:
1. Balance-by-symbol matrix ($5 to $50,000 across Gold, BTC, FX, Indices, micro flags off/on)
2. User case reproduction: XAUUSD.m, balance $99, spread 26 pts, ATR_M5 2.9, tier cap 2% -> BLOCKED_BY_BALANCE
3. Property-based tests: invariants on hard ceilings, window clamping, lot truncation, R:R floor
4. Cent account (USC) and Non-USD currency conversion tests
5. Dynamic R:R, reachability ceiling, and expectancy filters
6. Active-trading timeframe adaptation and time-of-day rollover filter
"""

from __future__ import annotations
import unittest
import math
import random
from typing import Dict, List, Any

from src.core.contracts import (
    AccountInfo,
    SymbolInfo,
    OrderType,
    AccountTier,
)
from src.risk.account_tier import AccountTierManager
from src.risk.fit_engine import RiskFitEngine, RiskFitDecision


class TestRiskFitEngine(unittest.TestCase):
    def setUp(self):
        self.engine = RiskFitEngine()
        # Reset micro flags to default false
        self.engine.tier_manager.acknowledged_high_risk_microaccount = False
        self.engine.tier_manager.micro_account_min_lot_allowed = False

    # -------------------------------------------------------------------------
    # TEST 1: Exact User Case Reproduction
    # -------------------------------------------------------------------------
    def test_reproduce_user_case_xauusd_99_balance(self):
        """
        Reproduce exact user case:
        Symbol: XAUUSD.m
        Balance: $99.00
        Spread: 26 pts ($0.26)
        ATR_M5: $2.90 (290 pts)
        Tier Cap: 2% ($1.98 budget)
        Expected:
        - min_stop: 520 pts (cost 26 pts / 0.05 max cost fraction = 520 pts = $5.20)
        - max_stop: 198 pts (budget $1.98 / $0.01 loss per pt at min lot = 198 pts = $1.98)
        - Not feasible: min_stop (520) > max_stop (198)
        - Status: BLOCKED_BY_BALANCE
        - Balance needed: $260.00 ($5.20 / 0.02)
        """
        acc = AccountInfo(balance=99.0, equity=99.0, free_margin=99.0, currency="USD")
        sym = SymbolInfo(
            symbol="XAUUSD.m",
            digits=2,
            point=0.01,
            tick_size=0.01,
            tick_value=1.0,
            contract_size=100.0,
            min_lot=0.01,
            max_lot=100.0,
            lot_step=0.01,
            spread=26.0
        )
        atrs = {"M5": 2.9}

        win, meta = self.engine.solve_timeframe_feasibility(
            account=acc,
            symbol_info=sym,
            order_type=OrderType.BUY,
            entry_price=3900.0,
            atr_price=2.9,
            structural_stop_price=0.0,
            timeframe="M5",
            trading_mode="HOLD",
            spread_override_pts=26.0,
            stops_level_pts=0,
            freeze_level_pts=0,
            expected_slippage_pts=0.0,
            commission_per_lot=0.0,
            risk_percent_override=2.0
        )

        self.assertFalse(win.is_feasible)
        self.assertAlmostEqual(win.min_stop_points, 520.0, places=1)
        self.assertAlmostEqual(win.max_stop_points, 198.0, places=1)
        self.assertAlmostEqual(win.min_stop_money, 5.20, places=2)
        self.assertAlmostEqual(win.max_stop_money, 1.98, places=2)

        decision = self.engine.evaluate_symbol(
            account=acc,
            symbol_info=sym,
            order_type=OrderType.BUY,
            entry_price=3900.0,
            structural_stop_price=0.0,
            timeframe_atrs=atrs,
            preferred_timeframe="M5",
            trading_mode="HOLD",
            spread_override_pts=26.0,
            stops_level_pts=0,
            freeze_level_pts=0,
            expected_slippage_pts=0.0,
            commission_per_lot=0.0,
            risk_percent_override=2.0
        )

        self.assertEqual(decision.status, "BLOCKED_BY_BALANCE")
        self.assertFalse(decision.is_feasible)
        self.assertEqual(decision.lot, 0.0)
        self.assertAlmostEqual(decision.balance_needed, 260.0, places=1)
        self.assertIn("BLOCKED_BY_BALANCE", decision.log_line)

    # -------------------------------------------------------------------------
    # TEST 2: Balance-by-Symbol Matrix
    # -------------------------------------------------------------------------
    def test_balance_by_symbol_matrix(self):
        """
        Matrix of balances: $5, $10, $25, $50, $100, $500, $5000, $50000
        across XAUUSD.m, BTCUSD.m, EURUSD.m, GBPJPY.m, US30.std
        with micro flags off and on.
        """
        balances = [5.0, 10.0, 25.0, 50.0, 100.0, 500.0, 5000.0, 50000.0]
        symbols_config = {
            "XAUUSD.m": SymbolInfo("XAUUSD.m", digits=2, point=0.01, tick_size=0.01, tick_value=1.0, contract_size=100.0, min_lot=0.01, max_lot=50.0, lot_step=0.01, spread=25.0),
            "BTCUSD.m": SymbolInfo("BTCUSD.m", digits=2, point=0.01, tick_size=0.01, tick_value=1.0, contract_size=1.0, min_lot=0.01, max_lot=10.0, lot_step=0.01, spread=45.0),
            "EURUSD.m": SymbolInfo("EURUSD.m", digits=5, point=0.00001, tick_size=0.00001, tick_value=1.0, contract_size=100000.0, min_lot=0.01, max_lot=100.0, lot_step=0.01, spread=10.0),
            "GBPJPY.m": SymbolInfo("GBPJPY.m", digits=3, point=0.001, tick_size=0.001, tick_value=0.67, contract_size=100000.0, min_lot=0.01, max_lot=100.0, lot_step=0.01, spread=15.0),
            "US30.std": SymbolInfo("US30.std", digits=1, point=0.1, tick_size=0.1, tick_value=1.0, contract_size=1.0, min_lot=0.01, max_lot=50.0, lot_step=0.01, spread=20.0),
        }
        atrs_map = {
            "XAUUSD.m": {"M5": 2.5, "M15": 4.5, "H1": 10.0},
            "BTCUSD.m": {"M5": 150.0, "M15": 300.0, "H1": 800.0},
            "EURUSD.m": {"M5": 0.0008, "M15": 0.0015, "H1": 0.0035},
            "GBPJPY.m": {"M5": 0.120, "M15": 0.220, "H1": 0.500},
            "US30.std": {"M5": 35.0, "M15": 70.0, "H1": 160.0},
        }

        matrix_records = []
        for micro_mode in [False, True]:
            self.engine.tier_manager.acknowledged_high_risk_microaccount = micro_mode
            self.engine.tier_manager.micro_account_min_lot_allowed = micro_mode

            for bal in balances:
                acc = AccountInfo(balance=bal, equity=bal, free_margin=bal, currency="USD")
                for s_name, sym in symbols_config.items():
                    atrs = atrs_map[s_name]
                    entry_p = 2500.0 if "XAU" in s_name else (65000.0 if "BTC" in s_name else (190.0 if "JPY" in s_name else (39000.0 if "US30" in s_name else 1.0850)))
                    decision = self.engine.evaluate_symbol(
                        account=acc,
                        symbol_info=sym,
                        order_type=OrderType.BUY,
                        entry_price=entry_p,
                        structural_stop_price=0.0,
                        timeframe_atrs=atrs,
                        preferred_timeframe="M5",
                        trading_mode="HOLD"
                    )

                    matrix_records.append({
                        "micro_mode": micro_mode,
                        "balance": bal,
                        "symbol": s_name,
                        "status": decision.status,
                        "timeframe": decision.timeframe,
                        "window": f"[{decision.window.min_stop_points:.0f}, {decision.window.max_stop_points:.0f}] pts",
                        "lot": decision.lot,
                        "risk_pct": decision.real_risk_pct,
                        "rr": decision.rr_result.reachable_rr,
                    })

                    # Basic invariant checks
                    if decision.status == "TRADABLE":
                        self.assertGreater(decision.lot, 0.0)
                        self.assertLessEqual(decision.real_risk_pct, decision.ceiling_risk_pct + 1e-4)
                    elif decision.status == "MARGINAL":
                        self.assertTrue(micro_mode)
                        self.assertEqual(decision.lot, sym.min_lot)
                        self.assertLessEqual(decision.real_risk_pct, decision.ceiling_risk_pct + 1e-4)
                    elif decision.status == "BLOCKED_BY_BALANCE":
                        self.assertEqual(decision.lot, 0.0)

        # Ensure matrix ran and produced results across all combinations
        total_expected = 2 * len(balances) * len(symbols_config)
        self.assertEqual(len(matrix_records), total_expected)

    # -------------------------------------------------------------------------
    # TEST 3: Property Tests (Randomized Fuzz Invariants)
    # -------------------------------------------------------------------------
    def test_property_invariants_fuzzing(self):
        """
        Property invariants over 200 random market scenarios:
        1. real_risk_pct never exceeds tier ceiling
        2. chosen_stop is always inside [min_stop, max_stop] when feasible
        3. lot size is strictly quantized and rounded down (never up)
        4. R:R never drops below min_rr_floor for tradable setups
        """
        random.seed(42)
        symbols = [
            SymbolInfo("XAUUSD.m", digits=2, point=0.01, tick_size=0.01, tick_value=1.0, min_lot=0.01, max_lot=20.0, lot_step=0.01),
            SymbolInfo("EURUSD.m", digits=5, point=0.00001, tick_size=0.00001, tick_value=1.0, min_lot=0.01, max_lot=50.0, lot_step=0.01),
            SymbolInfo("BTCUSD.m", digits=2, point=0.01, tick_size=0.01, tick_value=1.0, min_lot=0.01, max_lot=5.0, lot_step=0.01),
        ]

        for _ in range(200):
            bal = random.uniform(5.0, 100000.0)
            acc = AccountInfo(balance=bal, equity=bal, free_margin=bal)
            sym = random.choice(symbols)
            order_type = random.choice([OrderType.BUY, OrderType.SELL])
            entry = random.uniform(10.0, 50000.0)
            struct_stop = random.uniform(0.0, entry * 0.05)
            spread_pts = random.uniform(5.0, 50.0)
            atr_m5 = random.uniform(sym.point * 10, entry * 0.02)
            atrs = {"M5": atr_m5, "M15": atr_m5 * 1.7, "H1": atr_m5 * 3.5}

            d = self.engine.evaluate_symbol(
                account=acc,
                symbol_info=sym,
                order_type=order_type,
                entry_price=entry,
                structural_stop_price=struct_stop,
                timeframe_atrs=atrs,
                spread_override_pts=spread_pts
            )

            # Invariant 1: Ceiling protection
            self.assertLessEqual(
                d.real_risk_pct,
                d.ceiling_risk_pct + 1e-4,
                f"Ceiling breach: {d.real_risk_pct:.2f}% > {d.ceiling_risk_pct:.2f}%"
            )

            # Invariant 2: Window clamping when feasible
            if d.is_feasible:
                self.assertGreaterEqual(
                    d.chosen_stop_price + 1e-7,
                    d.window.min_stop_price,
                    "Chosen stop below min_stop"
                )
                self.assertLessEqual(
                    d.chosen_stop_price - 1e-7,
                    d.window.max_stop_price,
                    "Chosen stop above max_stop"
                )

            # Invariant 3: Volume quantization and rounding down
            self.assertLessEqual(d.lot, d.raw_lot + 1e-7, "Lot rounded UP!")
            if d.lot > 0:
                steps = round(d.lot / sym.lot_step, 4)
                self.assertAlmostEqual(steps, round(steps), places=3, msg="Volume not quantized to step")

            # Invariant 4: Minimum R:R floor
            if d.status == "TRADABLE":
                self.assertGreaterEqual(d.rr_result.reachable_rr, 1.5 - 1e-5, "R:R below floor")
                self.assertGreater(d.expected_value_in_r, -1e-5, "Negative EV approved")

    # -------------------------------------------------------------------------
    # TEST 4: Cent Account (USC) & Non-USD Account Tests
    # -------------------------------------------------------------------------
    def test_cent_account_conversion(self):
        """
        Cent account (USC):
        Balance 10,000 USC = $100.00 USD.
        Ensure risk budgets and lot calculations handle cent account scaling.
        """
        # 10,000 cents with is_cent_account flag
        acc = AccountInfo(balance=10000.0, equity=10000.0, free_margin=10000.0, currency="USC", is_cent_account=True)
        sym = SymbolInfo("EURUSD.m", digits=5, point=0.00001, tick_size=0.00001, tick_value=0.01, contract_size=1000.0, min_lot=0.01, max_lot=100.0, lot_step=0.01, spread=10.0)
        atrs = {"M5": 0.0008, "M15": 0.0015, "H1": 0.0035}

        d = self.engine.evaluate_symbol(
            account=acc,
            symbol_info=sym,
            order_type=OrderType.BUY,
            entry_price=1.0850,
            structural_stop_price=0.0,
            timeframe_atrs=atrs,
            commission_per_lot=0.0
        )

        self.assertIn(d.status, ["TRADABLE", "MARGINAL"])
        self.assertGreater(d.lot, 0.0)
        self.assertLessEqual(d.real_risk_pct, d.ceiling_risk_pct + 1e-4)

    def test_non_usd_account_currency(self):
        """
        Non-USD account currency (e.g. EUR):
        Balance 1,000 EUR with EURUSD tick value converted.
        """
        acc = AccountInfo(balance=1000.0, equity=1000.0, free_margin=1000.0, currency="EUR")
        # In EUR account trading EURUSD, 1 lot 1 pip is 10 USD / 1.0850 = 9.216 EUR
        sym = SymbolInfo("EURUSD.m", digits=5, point=0.00001, tick_size=0.00001, tick_value=0.9216, contract_size=100000.0, min_lot=0.01, max_lot=100.0, lot_step=0.01, spread=8.0)
        atrs = {"M5": 0.0008, "M15": 0.0015}

        d = self.engine.evaluate_symbol(
            account=acc,
            symbol_info=sym,
            order_type=OrderType.BUY,
            entry_price=1.0850,
            structural_stop_price=0.0,
            timeframe_atrs=atrs
        )

        self.assertEqual(d.status, "TRADABLE")
        self.assertGreater(d.lot, 0.0)
        self.assertLessEqual(d.real_risk_pct, d.ceiling_risk_pct + 1e-4)

    # -------------------------------------------------------------------------
    # TEST 5: Timeframe Adaptation & Time-of-Day Filter
    # -------------------------------------------------------------------------
    def test_active_trading_timeframe_adaptation(self):
        """
        Engine starts at M5; if spread-to-ATR is too high or M5 is infeasible,
        it evaluates candidate timeframes and selects the lowest viable TF.
        """
        acc = AccountInfo(balance=250.0, equity=250.0, free_margin=250.0)
        sym = SymbolInfo("BTCUSD.m", digits=2, point=0.01, tick_size=0.01, tick_value=1.0, contract_size=1.0, min_lot=0.01, max_lot=10.0, lot_step=0.01, spread=50.0)

        # M5 ATR is very small ($10), M15 is $40, H1 is $150
        atrs = {"M5": 10.0, "M15": 40.0, "H1": 150.0}

        d = self.engine.evaluate_symbol(
            account=acc,
            symbol_info=sym,
            order_type=OrderType.BUY,
            entry_price=65000.0,
            structural_stop_price=0.0,
            timeframe_atrs=atrs,
            preferred_timeframe="M5"
        )

        self.assertIn(d.timeframe, ["M5", "M15", "M30", "H1", "H4"])
        self.assertGreater(len(d.candidate_evaluations), 1)

    def test_rollover_window_filter(self):
        """
        Verifies that during rollover window (21:55-22:15 UTC),
        trading is marked unfavourable.
        """
        from datetime import datetime, timezone
        rollover_time = datetime(2026, 11, 2, 22, 5, 0, tzinfo=timezone.utc)
        normal_time = datetime(2026, 11, 2, 14, 0, 0, tzinfo=timezone.utc)

        ok_rollover, msg_rollover = self.engine.is_time_favourable("XAUUSD.m", current_time_utc=rollover_time)
        ok_normal, msg_normal = self.engine.is_time_favourable("XAUUSD.m", current_time_utc=normal_time)

        self.assertFalse(ok_rollover)
        self.assertIn("rollover", msg_rollover.lower())
        self.assertTrue(ok_normal)

    # -------------------------------------------------------------------------
    # TEST 6: State Changes and Deduplicated Notifications
    # -------------------------------------------------------------------------
    def test_state_change_deduplication(self):
        """
        Ensures state changes (BLOCKED -> TRADABLE) trigger notifications once,
        and deduplicate on identical repeats.
        """
        acc_low = AccountInfo(balance=30.0, equity=30.0, free_margin=30.0)
        sym = SymbolInfo("XAUUSD.m", digits=2, point=0.01, tick_size=0.01, tick_value=1.0, min_lot=0.01, spread=26.0)
        atrs = {"M5": 2.9}

        # Round 1: Low balance -> BLOCKED
        d1 = self.engine.evaluate_symbol(acc_low, sym, OrderType.BUY, 3900.0, 0.0, atrs)
        n1 = self.engine.detect_state_changes([d1])
        # First check sets baseline, no transition yet
        self.assertEqual(len(n1), 0)

        # Round 2: Repeated low balance -> Still BLOCKED -> 0 notifications
        d2 = self.engine.evaluate_symbol(acc_low, sym, OrderType.BUY, 3900.0, 0.0, atrs)
        n2 = self.engine.detect_state_changes([d2])
        self.assertEqual(len(n2), 0)

        # Round 3: Deposit made! Balance $10,000 -> TRADABLE -> 1 notification
        acc_high = AccountInfo(balance=10000.0, equity=10000.0, free_margin=10000.0)
        d3 = self.engine.evaluate_symbol(acc_high, sym, OrderType.BUY, 3900.0, 0.0, atrs)
        n3 = self.engine.detect_state_changes([d3])
        self.assertEqual(len(n3), 1)
        self.assertIn("TRADABLE", n3[0])

        # Round 4: Repeated high balance -> 0 notifications (deduplicated)
        d4 = self.engine.evaluate_symbol(acc_high, sym, OrderType.BUY, 3900.0, 0.0, atrs)
        n4 = self.engine.detect_state_changes([d4])
        self.assertEqual(len(n4), 0)


if __name__ == "__main__":
    unittest.main()
