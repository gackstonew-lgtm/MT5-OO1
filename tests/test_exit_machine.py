"""
Unit tests for Phase 6: Exit Machine (Time Stop, Session-End Flatten, 1R Partial, Swing Trailing).
"""

import unittest
from datetime import datetime, timezone, timedelta

from src.core.contracts import Position, OrderType, Candle, SymbolInfo
from src.core.config import RiskConfig
from src.risk.trade_management import TradeManagementEngine


class TestExitMachine(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 11, 3, 15, 0, 0, tzinfo=timezone.utc)
        self.sym_info = SymbolInfo(
            symbol="EURUSD", digits=5, point=0.00001, tick_size=0.00001, tick_value=1.0,
            min_lot=0.01, max_lot=50.0, lot_step=0.01, spread=5.0, ask=1.10005, bid=1.10000
        )
        self.open_time = self.now - timedelta(hours=10)
        self.position = Position(
            ticket=9001,
            symbol="EURUSD",
            order_type=OrderType.BUY,
            volume=0.20,
            open_price=1.1000,
            current_price=1.1020,
            stop_loss=1.0950,
            take_profit=1.1150,
            profit=40.0,
            open_time=self.open_time,
            initial_risk_amount=100.0,
            initial_sl_distance=0.0050
        )

        # Generate 40 candles spaced by 15 mins
        self.candles = []
        for i in range(40):
            t = self.open_time + timedelta(minutes=i * 15)
            self.candles.append(
                Candle(time=t, open=1.1000, high=1.1030, low=1.0990, close=1.1020, tick_volume=100)
            )

    def test_default_rules_are_off(self):
        """Verifies all Phase 6 exit rules default to OFF."""
        cfg = RiskConfig()
        self.assertFalse(cfg.enable_time_stop)
        self.assertFalse(cfg.enable_session_end_flatten)
        self.assertFalse(cfg.enable_partial_at_1r)
        self.assertFalse(cfg.enable_swing_trailing)

    def test_time_stop_triggers_full_close(self):
        """Verifies time stop triggers when bars_open exceeds time_stop_max_bars."""
        cfg = RiskConfig(enable_time_stop=True, time_stop_max_bars=30)
        engine = TradeManagementEngine(config=cfg)

        actions = engine.evaluate_position(self.position, self.sym_info, self.candles)
        time_stops = [a for a in actions if a.action_type == "FULL_CLOSE" and "Time stop" in a.reason]
        self.assertEqual(len(time_stops), 1)
        self.assertEqual(time_stops[0].ticket, 9001)

    def test_session_end_flatten_triggers(self):
        """Verifies session-end flatten triggers at or after 21:00 UTC."""
        cfg = RiskConfig(enable_session_end_flatten=True)
        engine = TradeManagementEngine(config=cfg)

        # Candle at 21:15 UTC
        late_candles = list(self.candles)
        late_candles.append(
            Candle(
                time=datetime(2026, 11, 3, 21, 15, 0, tzinfo=timezone.utc),
                open=1.1020, high=1.1025, low=1.1015, close=1.1020, tick_volume=100
            )
        )
        actions = engine.evaluate_position(self.position, self.sym_info, late_candles)
        flattens = [a for a in actions if a.action_type == "FULL_CLOSE" and "flatten" in a.reason]
        self.assertEqual(len(flattens), 1)

    def test_partial_tp_at_1r_triggers(self):
        """Verifies 1.0R partial profit-taking triggers when current_r >= 1.0."""
        cfg = RiskConfig(enable_partial_at_1r=True)
        engine = TradeManagementEngine(config=cfg)

        # Set price at +1.1R (open=1.1000, sl=1.0950 -> risk=0.0050. current_price=1.1055 -> profit=0.0055 = 1.1R)
        self.position.current_price = 1.1055
        actions = engine.evaluate_position(self.position, self.sym_info, self.candles)
        partials_1r = [a for a in actions if a.action_type == "PARTIAL_CLOSE" and "1.0R" in a.reason]
        self.assertEqual(len(partials_1r), 1)
        self.assertEqual(partials_1r[0].close_volume, 0.10)
        self.assertIsNotNone(partials_1r[0].amendment)

    def test_swing_trailing_advances_beyond_confirmed_swing(self):
        """Verifies structure-based swing trailing stop advances SL beyond confirmed swing low."""
        cfg = RiskConfig(enable_swing_trailing=True, enable_break_even=False, trailing_stop_enabled=False)
        engine = TradeManagementEngine(config=cfg)

        # Create candle sequence with a confirmed swing low at bar 10 (low = 1.1010)
        # Position open = 1.1000, current = 1.1080, initial SL = 1.0950
        self.position.current_price = 1.1080
        candles = []
        base_t = self.now - timedelta(hours=3)
        for i in range(15):
            t = base_t + timedelta(minutes=i * 15)
            # Bar 10 has a clear fractal low (1.1010) surrounded by higher lows (1.1025)
            c_low = 1.1010 if i == 10 else 1.1025 + abs(i - 10) * 0.0003
            candles.append(
                Candle(time=t, open=1.1040, high=1.1070, low=c_low, close=1.1060, tick_volume=100)
            )

        actions = engine.evaluate_position(self.position, self.sym_info, candles)
        swing_trails = [a for a in actions if a.action_type == "MODIFY_SL" and "Structure-based swing" in a.reason]
        self.assertEqual(len(swing_trails), 1)
        # SL trailed to round_price(1.1010 - 2 pips) = 1.10080
        self.assertGreater(swing_trails[0].new_sl, self.position.stop_loss)
        self.assertLess(swing_trails[0].new_sl, self.position.current_price)


if __name__ == "__main__":
    unittest.main()
