"""
Unit tests for Phase 3: Fast Tick Exit Manager (break-even, partial TP, swing trailing,
time stop, momentum failure, spread-spike exit, and pre-rollover/news flatten).
"""

import unittest
from datetime import datetime, timezone, timedelta
from src.core.contracts import Position, SymbolInfo, OrderType, Candle
from src.execution.fast_exit_manager import FastTickExitManager


class TestFastExitManager(unittest.TestCase):
    def setUp(self):
        self.manager = FastTickExitManager()
        self.sym_info = SymbolInfo(
            symbol="XAUUSD.m",
            digits=2,
            point=0.01,
            tick_size=0.01,
            tick_value=1.0,
            contract_size=100.0,
            min_lot=0.01,
            max_lot=50.0,
            lot_step=0.01,
            spread=15.0,   # $0.15
            ask=2500.15,
            bid=2500.00,
            stops_level=10,
            freeze_level=5
        )

    def _make_buy_position(self, current_price: float = 2500.00, volume: float = 0.10) -> Position:
        return Position(
            ticket=55501,
            symbol="XAUUSD.m",
            order_type=OrderType.BUY,
            volume=volume,
            open_price=2500.00,
            current_price=current_price,
            stop_loss=2496.00,   # $4.00 SL distance (1R = $4.00)
            take_profit=2510.00,
            profit=0.0,
            open_time=datetime(2026, 1, 6, 13, 0, tzinfo=timezone.utc),
            initial_sl_distance=4.00
        )

    def test_break_even_at_half_r_above_spread_plus_buffer(self):
        """At +0.6R ($2.40 profit on $4.00 SL), SL moves to open + spread + buffer."""
        pos = self._make_buy_position(current_price=2502.40)
        self.sym_info.bid = 2502.40
        actions = self.manager.evaluate_tick(pos, self.sym_info, current_time=pos.open_time + timedelta(minutes=5))

        be_acts = [a for a in actions if a.action_type == "MODIFY_SL" and "Break-even" in a.reason]
        self.assertEqual(len(be_acts), 1)
        # Open 2500.00 + (15 spread + 5 buffer) * 0.01 = 2500.20
        self.assertAlmostEqual(be_acts[0].new_sl, 2500.20)
        self.assertEqual(be_acts[0].event_level, "TRADE")

    def test_partial_tp_and_swing_trailing_at_one_r(self):
        """At +1.2R ($4.80 profit), triggers 50% partial close and trails SL behind swing low."""
        pos = self._make_buy_position(current_price=2504.80, volume=0.10)
        pos.break_even_activated = True
        self.sym_info.bid = 2504.80

        t0 = datetime(2026, 1, 6, 13, 0, tzinfo=timezone.utc)
        candles = [
            Candle(time=t0, open=2501.0, high=2502.5, low=2501.20, close=2502.2, complete=True),
            Candle(time=t0 + timedelta(minutes=5), open=2502.2, high=2503.8, low=2502.00, close=2503.5, complete=True),
            Candle(time=t0 + timedelta(minutes=10), open=2503.5, high=2505.0, low=2503.10, close=2504.8, complete=True)
        ]

        actions = self.manager.evaluate_tick(pos, self.sym_info, recent_candles=candles, current_time=t0 + timedelta(minutes=15))
        part_acts = [a for a in actions if a.action_type == "PARTIAL_CLOSE"]
        trail_acts = [a for a in actions if a.action_type == "MODIFY_SL" and "Trailing" in a.reason]

        self.assertEqual(len(part_acts), 1)
        self.assertAlmostEqual(part_acts[0].close_volume, 0.05)
        self.assertEqual(len(trail_acts), 1)
        # Min low of last 3 bars is 2501.20; minus 2 pts (0.02) = 2501.18 > open 2500.00
        self.assertAlmostEqual(trail_acts[0].new_sl, 2501.18)

    def test_time_stop_expiration(self):
        """Position held longer than time_stop_seconds (3600s) is closed with FULL_CLOSE."""
        pos = self._make_buy_position(current_price=2500.50)
        self.sym_info.bid = 2500.50
        late_time = pos.open_time + timedelta(seconds=3700)

        actions = self.manager.evaluate_tick(pos, self.sym_info, current_time=late_time)
        self.assertEqual(len(actions), 1)
        self.assertEqual(actions[0].action_type, "FULL_CLOSE")
        self.assertIn("Time stop expired", actions[0].reason)

    def test_momentum_failure_and_pre_rollover_flatten(self):
        """Verifies momentum failure exit on structural breakdown and pre-rollover flatten."""
        pos = self._make_buy_position(current_price=2499.00)
        self.sym_info.bid = 2499.00
        t0 = pos.open_time
        candles = [
            Candle(time=t0, open=2500.0, high=2500.8, low=2499.50, close=2500.2, complete=True),
            Candle(time=t0 + timedelta(minutes=1), open=2500.2, high=2500.5, low=2499.60, close=2500.0, complete=True),
            Candle(time=t0 + timedelta(minutes=2), open=2500.0, high=2500.3, low=2499.55, close=2499.8, complete=True),
            # Bearish breakdown below 2499.50 support
            Candle(time=t0 + timedelta(minutes=3), open=2499.8, high=2499.9, low=2498.80, close=2499.00, complete=True)
        ]

        actions = self.manager.evaluate_tick(pos, self.sym_info, recent_candles=candles, current_time=t0 + timedelta(minutes=4))
        self.assertEqual(len(actions), 1)
        self.assertEqual(actions[0].action_type, "FULL_CLOSE")
        self.assertIn("Momentum failure", actions[0].reason)

        # Pre-rollover flatten
        actions_roll = self.manager.evaluate_tick(pos, self.sym_info, is_pre_rollover=True)
        self.assertEqual(len(actions_roll), 1)
        self.assertIn("Pre-rollover", actions_roll[0].reason)


if __name__ == "__main__":
    unittest.main()
