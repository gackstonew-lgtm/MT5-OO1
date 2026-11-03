"""
Unit Tests for Phase 4: Risk Guardrails for Speed (`src/risk/fast_risk_guard.py`).
Covers:
- Lot sizing strictly rounded DOWN to lot_step (`normalize_volume_down`).
- `ForceMinLotRiskFloor` and small-account minimum balance calculation + DECISION message.
- Projected margin floor (>= 300%) and critical margin floor (<= 150% -> flatten immediately).
- Combined open-risk cap across all open positions.
- No-stacking, no-averaging-down (anti-grid), and no-lot-increase-after-loss (anti-martingale).
- Consecutive-loss circuit breaker and equity-velocity kill switch.
"""

import unittest
from datetime import datetime, timezone
from src.core.contracts import (
    AccountInfo, SymbolInfo, Position, SignalCandidate,
    OrderType, SetupType, Timeframe
)
from src.risk.fast_risk_guard import FastRiskGuardEngine


class TestFastRiskGuards(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = FastRiskGuardEngine(config_path="config/fast_mode.json")
        self.eur_sym = SymbolInfo(
            symbol="EURUSD",
            digits=5,
            point=0.00001,
            tick_size=0.00001,
            tick_value=1.0,
            min_lot=0.01,
            max_lot=50.0,
            lot_step=0.01,
            spread=8,
            margin_required=200.0
        )
        self.gold_sym = SymbolInfo(
            symbol="XAUUSD",
            digits=2,
            point=0.01,
            tick_size=0.01,
            tick_value=1.0,
            min_lot=0.01,
            max_lot=50.0,
            lot_step=0.01,
            spread=20,
            margin_required=500.0
        )

    def _make_candidate(self, symbol: str = "EURUSD", direction: OrderType = OrderType.BUY, entry: float = 1.1000, sl: float = 1.0990) -> SignalCandidate:
        return SignalCandidate(
            id="sig_fast_1",
            symbol=symbol,
            timeframe=Timeframe.M1,
            direction=direction,
            setup_type=SetupType.FAST_SCALP,
            entry_price=entry,
            stop_loss=sl,
            take_profit=entry + 2.0 * abs(entry - sl) if direction == OrderType.BUY else entry - 2.0 * abs(entry - sl),
            risk_reward=2.0,
            score=85.0,
            timestamp=datetime.now(timezone.utc)
        )

    def test_lot_sizing_rounds_strictly_down_and_respects_min_lot_floor(self):
        # Balance $1000, 1% risk = $10. SL = 60 pips (0.00600) -> $600 per 1.0 lot -> raw_lots = 10 / 600 = 0.01666...
        # Must round strictly DOWN to 0.01 (never up to 0.02!)
        acc = AccountInfo(balance=1000.0, equity=1000.0, margin=0.0, free_margin=1000.0, margin_level=0.0)
        vol, risk_usd, reason = self.engine.calculate_safe_lot_size(
            account=acc,
            symbol_info=self.eur_sym,
            entry_price=1.10000,
            stop_loss=1.09400,
            risk_percent=1.0
        )
        self.assertIsNone(reason)
        self.assertEqual(vol, 0.01)
        self.assertAlmostEqual(risk_usd, 6.00, places=2)

    def test_small_account_awareness_skips_when_min_lot_exceeds_risk_cap(self):
        # Small $50 account on XAUUSD with $10 SL distance ($10 risk on 0.01 lot = 20% of $50 > 1% cap)
        acc = AccountInfo(balance=50.0, equity=50.0, margin=0.0, free_margin=50.0, margin_level=0.0)
        vol, risk_usd, reason = self.engine.calculate_safe_lot_size(
            account=acc,
            symbol_info=self.gold_sym,
            entry_price=2300.00,
            stop_loss=2290.00,
            risk_percent=1.0
        )
        self.assertEqual(vol, 0.0)
        self.assertIsNotNone(reason)
        self.assertIn("XAUUSD skipped: min lot (0.01) risks 20.00% ($10.00) > 1.00% cap; needs about $1000.00 balance", reason)
        self.assertEqual(self.engine.rejected_by_min_lot_count, 1)

    def test_projected_margin_floor_300_and_critical_floor_150(self):
        # 1. Projected margin < 300% blocks new entry
        acc = AccountInfo(balance=1000.0, equity=1000.0, margin=250.0, free_margin=750.0, margin_level=400.0)
        # Adding 1.0 lot EURUSD (margin_required=200) -> projected used margin = 450 -> 1000/450 = 222.2% < 300%
        status = self.engine.check_margin_and_open_risk(
            account=acc,
            active_positions=[],
            symbol_info=self.eur_sym,
            proposed_volume=1.0,
            proposed_monetary_risk=5.0
        )
        self.assertFalse(status.can_open_new_trade)
        self.assertFalse(status.must_flatten_immediately)
        self.assertIn("Projected margin level (222.2%) below floor (300.0%)", status.reason)

        # 2. Critical margin floor <= 150% triggers immediate flatten + pause
        acc_crit = AccountInfo(balance=1000.0, equity=360.0, margin=250.0, free_margin=110.0, margin_level=144.0)
        status_crit = self.engine.check_margin_and_open_risk(
            account=acc_crit,
            active_positions=[],
            symbol_info=self.eur_sym,
            proposed_volume=0.01,
            proposed_monetary_risk=1.0
        )
        self.assertFalse(status_crit.can_open_new_trade)
        self.assertTrue(status_crit.must_flatten_immediately)
        self.assertTrue(self.engine.paused)
        self.assertIn("CRITICAL MARGIN FLOOR BREACHED", status_crit.reason)

    def test_combined_open_risk_cap(self):
        acc = AccountInfo(balance=1000.0, equity=1000.0, margin=50.0, free_margin=950.0, margin_level=2000.0)
        open_pos = [
            Position(
                ticket=101, symbol="GBPUSD", order_type=OrderType.BUY, volume=0.10,
                open_price=1.2700, current_price=1.2705, stop_loss=1.2680, take_profit=1.2750,
                profit=5.0, open_time=datetime.now(timezone.utc), initial_risk_amount=25.0
            )
        ]
        # Existing risk = $25 (2.5% of $1000). Proposing $10 (1.0%) -> total 3.5% > 3.0% cap
        status = self.engine.check_margin_and_open_risk(
            account=acc,
            active_positions=open_pos,
            symbol_info=self.eur_sym,
            proposed_volume=0.10,
            proposed_monetary_risk=10.0
        )
        self.assertFalse(status.can_open_new_trade)
        self.assertIn("Combined open risk (3.50% / $35.00) exceeds 3.00% equity cap", status.reason)

    def test_anti_grid_anti_stacking_and_anti_martingale(self):
        acc = AccountInfo(balance=5000.0, equity=5000.0, margin=50.0, free_margin=4950.0, margin_level=10000.0)
        cand = self._make_candidate(symbol="EURUSD", direction=OrderType.BUY, entry=1.0980, sl=1.0970)
        losing_pos = Position(
            ticket=202, symbol="EURUSD", order_type=OrderType.BUY, volume=0.10,
            open_price=1.1000, current_price=1.0980, stop_loss=1.0950, take_profit=1.1080,
            profit=-20.0, open_time=datetime.now(timezone.utc), initial_risk_amount=50.0
        )
        # Allow per-symbol limit > 1 temporarily so anti-grid is tested directly
        self.engine._cfg["risk_guardrails"]["max_concurrent_positions_per_symbol"] = 2
        self.engine._cfg["risk_guardrails"]["allow_same_direction_stacking"] = True

        allowed, reason = self.engine.validate_trade_candidate(
            candidate=cand,
            account=acc,
            symbol_info=self.eur_sym,
            active_positions=[losing_pos],
            proposed_volume=0.10,
            proposed_monetary_risk=10.0
        )
        self.assertFalse(allowed)
        self.assertIn("Anti-grid / no-averaging-down rule", reason)

        # Now test anti-martingale after a loss
        self.engine.record_trade_outcome(pnl_usd=-25.0, volume=0.10)
        allowed_mart, reason_mart = self.engine.validate_trade_candidate(
            candidate=cand,
            account=acc,
            symbol_info=self.eur_sym,
            active_positions=[],
            proposed_volume=0.15,  # larger than prior loss volume 0.10!
            proposed_monetary_risk=15.0
        )
        self.assertFalse(allowed_mart)
        self.assertIn("Anti-martingale rule: proposed volume (0.15) > prior loss volume (0.10)", reason_mart)

    def test_consecutive_loss_circuit_breaker_and_equity_velocity_kill_switch(self):
        # 2 consecutive losses pauses Fast Mode
        b1, _ = self.engine.record_trade_outcome(pnl_usd=-10.0, volume=0.05)
        self.assertFalse(b1)
        b2, reason2 = self.engine.record_trade_outcome(pnl_usd=-12.0, volume=0.05)
        self.assertTrue(b2)
        self.assertTrue(self.engine.paused)
        self.assertIn("Consecutive-loss circuit breaker triggered", reason2)

        # Reset and test equity-velocity kill switch (> 2.5% drop within 60s)
        self.engine.paused = False
        acc_start = AccountInfo(balance=10000.0, equity=10000.0, margin=100.0, free_margin=9900.0, margin_level=10000.0)
        self.engine.evaluate_equity_velocity_kill_switch(acc_start, [], current_timestamp_sec=1000.0)

        # 20 seconds later, floating loss spikes to -$280 (2.8% > 2.5% threshold)
        acc_drop = AccountInfo(balance=10000.0, equity=9720.0, margin=100.0, free_margin=9620.0, margin_level=9720.0)
        pos_loss = Position(
            ticket=303, symbol="EURUSD", order_type=OrderType.BUY, volume=1.0,
            open_price=1.1000, current_price=1.0972, stop_loss=1.0950, take_profit=1.1100,
            profit=-280.0, open_time=datetime.now(timezone.utc)
        )
        triggered, kill_reason = self.engine.evaluate_equity_velocity_kill_switch(
            acc_drop, [pos_loss], current_timestamp_sec=1020.0
        )
        self.assertTrue(triggered)
        self.assertTrue(self.engine.paused)
        self.assertIn("CRITICAL EQUITY-VELOCITY KILL SWITCH", kill_reason)


if __name__ == "__main__":
    unittest.main()
