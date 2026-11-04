"""
Unit & Integration Tests for the 20-Trade Lifecycle & 8-Validation Testing Program,
Candle History Retrieval Repair, and $5+ Intelligent Profit Management.
"""

from datetime import datetime, timedelta, timezone
import unittest

from scripts.run_20_trade_validation_suite import run_validation_program
from src.core.config import RiskConfig
from src.core.contracts import Candle, OrderType, Position, SymbolInfo, Timeframe
from src.core.market_data import MarketDataEngine
from src.risk.trade_management import TradeManagementEngine


class Test20TradeValidationAndProfitManagement(unittest.TestCase):
    def test_full_20_trade_and_8_validation_program(self):
        """Verifies all 20 completed trades and 8 validation tests pass with >=$5 net profit on every winning trade."""
        report = run_validation_program()
        self.assertEqual(report["total_completed_trades"], 20)
        self.assertEqual(report["total_validation_tests"], 8)
        self.assertEqual(report["total_test_cases"], 28)
        self.assertTrue(report["all_tests_passed"])
        self.assertGreaterEqual(report["winning_trades"], 15)
        self.assertGreaterEqual(report["min_winning_trade_net_usd"], 5.0)
        self.assertGreater(report["max_winning_trade_net_usd"], 15.0)
        self.assertGreater(report["net_profit_usd"], 100.0)
        self.assertGreater(report["profit_factor"], 5.0)

    def test_forming_vs_completed_candle_separation(self):
        """Verifies MarketDataEngine separates forming (complete=False) from completed (complete=True) candles."""
        md = MarketDataEngine(symbols=["XAUUSD"])
        t0 = datetime(2026, 11, 3, 12, 0, tzinfo=timezone.utc)
        candles = []
        for i in range(25):
            candles.append(
                Candle(
                    time=t0 + timedelta(minutes=i * 15),
                    open=3930.0 + i * 0.2,
                    high=3931.0 + i * 0.2,
                    low=3929.5 + i * 0.2,
                    close=3930.5 + i * 0.2,
                    tick_volume=100,
                    spread=20,
                    complete=(i < 24),  # Last bar is forming
                )
            )
        md.set_candles("XAUUSD", Timeframe.M15, candles)
        completed = md.get_candles("XAUUSD", Timeframe.M15, include_forming=False)
        with_forming = md.get_candles("XAUUSD", Timeframe.M15, include_forming=True)
        forming = md.get_forming_candle("XAUUSD", Timeframe.M15)

        self.assertEqual(len(completed), 24)
        self.assertTrue(all(c.complete for c in completed))
        self.assertEqual(len(with_forming), 25)
        self.assertIsNotNone(forming)
        self.assertFalse(forming.complete)

    def test_5usd_net_profit_closes_on_weakening_momentum_and_extends_on_strong(self):
        """Verifies $5+ net profit management closes on weakening momentum and extends on strong momentum."""
        sym_info = SymbolInfo(
            symbol="EURUSD.m",
            digits=5,
            point=0.00001,
            tick_size=0.00001,
            tick_value=1.0,
            min_lot=0.01,
            max_lot=50.0,
            lot_step=0.01,
            stops_level=3,
            freeze_level=2,
        )
        mgr = TradeManagementEngine(RiskConfig())
        t0 = datetime(2026, 11, 3, 14, 0, tzinfo=timezone.utc)

        # Case 1: Strong momentum at $6.00 net -> Extends & locks profit floor SL
        pos_strong = Position(
            ticket=1001,
            symbol="EURUSD.m",
            order_type=OrderType.BUY,
            volume=0.05,
            open_price=1.08500,
            current_price=1.08628,
            stop_loss=1.08400,
            take_profit=1.08800,
            profit=6.40,
            commission=-0.40,
            swap=0.0,
            initial_sl_distance=0.00100,
        )
        strong_candles = [
            Candle(time=t0 + timedelta(minutes=i * 15), open=1.08500 + i * 0.0003, high=1.08540 + i * 0.0003, low=1.08495 + i * 0.0003, close=1.08535 + i * 0.0003)
            for i in range(5)
        ]
        actions_strong = mgr.evaluate_position(pos_strong, sym_info, strong_candles)
        self.assertFalse(any(a.action_type == "FULL_CLOSE" for a in actions_strong))
        lock_actions = [a for a in actions_strong if a.action_type == "MODIFY_SL" and "Profit extension" in a.reason]
        self.assertEqual(len(lock_actions), 1)
        self.assertGreater(lock_actions[0].new_sl, pos_strong.open_price)

        # Case 2: Weakening momentum (2 bearish bars) at $5.60 net -> Closes immediately to secure >=$5 net profit
        pos_weak = Position(
            ticket=1002,
            symbol="EURUSD.m",
            order_type=OrderType.BUY,
            volume=0.05,
            open_price=1.08500,
            current_price=1.08620,
            stop_loss=1.08400,
            take_profit=1.08800,
            profit=6.00,
            commission=-0.40,
            swap=0.0,
            initial_sl_distance=0.00100,
        )
        weak_candles = list(strong_candles[:3]) + [
            Candle(time=t0 + timedelta(minutes=45), open=1.08640, high=1.08642, low=1.08625, close=1.08628),
            Candle(time=t0 + timedelta(minutes=60), open=1.08628, high=1.08630, low=1.08615, close=1.08620),
        ]
        actions_weak = mgr.evaluate_position(pos_weak, sym_info, weak_candles)
        self.assertTrue(any(a.action_type == "FULL_CLOSE" for a in actions_weak))


if __name__ == "__main__":
    unittest.main()
