"""
Unit tests for Phase 2: Fast Scalp Entry Mode (M1/M5 closed bars, HTF bias,
spread/ATR filters, cooldown, and hourly trade rate ceiling).
"""

import unittest
from datetime import datetime, timezone, timedelta
from src.core.contracts import Candle, Timeframe, TrendBias, SymbolInfo, OrderType
from src.execution.fast_mode_engine import FastScalpEntryEngine


class TestFastScalpEntryEngine(unittest.TestCase):
    def setUp(self):
        self.engine = FastScalpEntryEngine()
        self.engine._enabled = True  # Enable in-memory for testing
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
            spread=15.0,
            ask=2505.15,
            bid=2505.00,
            stops_level=10,
            freeze_level=5
        )

    def _make_m5_candles(self, count: int = 20, base_time: datetime = None) -> list:
        t0 = base_time or datetime(2026, 1, 6, 13, 0, tzinfo=timezone.utc)  # NY session
        candles = []
        price = 2500.00
        for i in range(count - 1):
            t = t0 + timedelta(minutes=5 * i)
            c = Candle(
                time=t,
                open=price,
                high=price + 1.50,
                low=price - 0.50,
                close=price + 0.80,
                tick_volume=200,
                complete=True
            )
            candles.append(c)
            price += 0.20

        # Final bullish breakout candle closing above prior high
        t_last = t0 + timedelta(minutes=5 * (count - 1))
        prev_high = candles[-1].high
        candles.append(
            Candle(
                time=t_last,
                open=prev_high - 0.50,
                high=prev_high + 2.50,
                low=prev_high - 0.80,
                close=prev_high + 2.00,
                tick_volume=450,
                complete=True
            )
        )
        return candles

    def test_valid_scalp_entry_on_closed_m5_bars(self):
        """Verifies valid bullish scalp candidate when HTF, session, spread, and ATR align."""
        candles = self._make_m5_candles(20)
        cand, reason = self.engine.evaluate_scalp_entry(
            symbol="XAUUSD.m",
            entry_candles=candles,
            symbol_info=self.sym_info,
            m15_bias=TrendBias.STRONG_BULLISH,
            h1_bias=TrendBias.STRONG_BULLISH,
            entry_timeframe=Timeframe.M5
        )
        self.assertIsNotNone(cand, f"Expected candidate, got rejection: {reason}")
        self.assertEqual(cand.direction, OrderType.BUY)
        self.assertGreaterEqual(cand.risk_reward, 2.0)
        self.assertLess(cand.stop_loss, cand.entry_price)
        self.assertGreater(cand.take_profit, cand.entry_price)

    def test_incomplete_forming_bar_ignored_no_repaint(self):
        """Unclosed (complete=False) bars must never trigger an entry."""
        candles = self._make_m5_candles(14)
        # Add 10 incomplete bars
        for i in range(10):
            candles.append(
                Candle(
                    time=datetime(2026, 1, 6, 14, i, tzinfo=timezone.utc),
                    open=2510.0,
                    high=2520.0,
                    low=2509.0,
                    close=2519.0,
                    complete=False
                )
            )
        cand, reason = self.engine.evaluate_scalp_entry(
            symbol="XAUUSD.m",
            entry_candles=candles,
            symbol_info=self.sym_info
        )
        self.assertIsNone(cand)
        self.assertIn("Insufficient closed", reason)

    def test_spread_pct_of_sl_filter(self):
        """Rejects entry when spread exceeds max_spread_pct_of_sl (10% of SL)."""
        candles = self._make_m5_candles(20)
        wide_sym = SymbolInfo(
            symbol="XAUUSD.m", digits=2, point=0.01, tick_size=0.01, tick_value=1.0,
            contract_size=100.0, spread=120.0  # $1.20 spread on ~$4.50 SL (> 25%)
        )
        cand, reason = self.engine.evaluate_scalp_entry(
            symbol="XAUUSD.m",
            entry_candles=candles,
            symbol_info=wide_sym,
            m15_bias=TrendBias.STRONG_BULLISH,
            h1_bias=TrendBias.STRONG_BULLISH
        )
        self.assertIsNone(cand)
        self.assertIn("Spread", reason)

    def test_cooldown_and_hourly_max_trades_anti_churn(self):
        """Enforces post-trade cooldown (120s) and max 3 trades per hour."""
        t_now = datetime(2026, 1, 6, 14, 0, tzinfo=timezone.utc)
        self.engine.record_trade_executed("XAUUSD.m", t_now)

        # 30s later -> blocked by 120s cooldown
        ok_cd, reason_cd = self.engine.check_anti_churn_limits("XAUUSD.m", t_now + timedelta(seconds=30))
        self.assertFalse(ok_cd)
        self.assertIn("Cooldown active", reason_cd)

        # Record 2 more trades spaced 10 mins apart -> reaches 3 trades in 1 hour
        self.engine.record_trade_executed("XAUUSD.m", t_now + timedelta(minutes=10))
        self.engine.record_trade_executed("XAUUSD.m", t_now + timedelta(minutes=20))

        ok_hr, reason_hr = self.engine.check_anti_churn_limits("XAUUSD.m", t_now + timedelta(minutes=30))
        self.assertFalse(ok_hr)
        self.assertIn("Hourly trade limit reached", reason_hr)


if __name__ == "__main__":
    unittest.main()
