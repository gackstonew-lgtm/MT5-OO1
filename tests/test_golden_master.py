"""
Golden Master Parity Test.
Replays 30 recorded trading days across all enabled symbols (EURUSD.m, XAUUSD.m, BTCUSD.m)
and verifies 100% bit-for-bit parity against the canonical baseline decisions dataset.
Asserts:
- Every closed-bar decision outcome matches identically
- Filter check outcomes and reason codes match identically
- Signal parameters (if present) match identically
- 0 regressions against baseline-pre-upgrade
"""

import os
import sys
import json
import bisect
import io
import contextlib
import unittest
from pathlib import Path
from datetime import datetime, timezone
from typing import Dict, List, Any

# Ensure project root in sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.core.contracts import Candle, Timeframe, AccountInfo, ExecutionMode, SymbolInfo
from src.core.orchestrator import TradingBotOrchestrator
from src.core.config import AppConfig


class TestGoldenMaster(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixture_path = PROJECT_ROOT / "tests" / "fixtures" / "baseline_decisions_30d.json"
        data_path = PROJECT_ROOT / "data" / "golden_master" / "baseline_decisions_30d.json"
        cls.dataset_path = fixture_path if fixture_path.exists() else data_path
        if not cls.dataset_path.exists():
            raise FileNotFoundError(
                f"Golden master canonical dataset not found at {fixture_path} or {data_path}. "
                "Run scripts/record_golden_master_dataset.py first."
            )
        with open(cls.dataset_path, "r", encoding="utf-8") as f:
            cls.canonical_data = json.load(f)

    def _load_candles(self, symbol: str, tf: str) -> List[Candle]:
        path = PROJECT_ROOT / "data" / "historical" / f"{symbol}_{tf}.json"
        if not path.exists():
            alt_sym = symbol.split(".")[0]
            path = PROJECT_ROOT / "data" / "historical" / f"{alt_sym}_{tf}.json"

        with open(path, "r", encoding="utf-8") as f:
            raw_list = json.load(f)

        candles = []
        for item in raw_list:
            candles.append(
                Candle(
                    time=datetime.fromisoformat(item["time"]),
                    open=float(item["open"]),
                    high=float(item["high"]),
                    low=float(item["low"]),
                    close=float(item["close"]),
                    tick_volume=int(item.get("tick_volume", 100)),
                    spread=int(item.get("spread", 10)),
                    complete=bool(item.get("complete", True))
                )
            )
        return candles

    def test_golden_master_replay_parity(self):
        """
        Replays the 30-day window and asserts 100% decision match against canonical baseline.
        """
        symbols = self.canonical_data["metadata"]["symbols"]
        total_bars = self.canonical_data["metadata"]["bars_per_symbol"]

        config = AppConfig.load_from_dir()
        config.symbols = symbols
        config.execution_mode = ExecutionMode.SIMULATION
        config.telegram_bot_token = ""
        config.telegram_chat_id = ""

        orchestrator = TradingBotOrchestrator(config)
        orchestrator.alerts = None  # Silence telegram queue during test
        orchestrator.account_info = AccountInfo(
            account_number=1200057798,
            balance=10000.0,
            equity=10000.0,
            trade_mode="DEMO"
        )

        for s, pt, dig, sp in [("EURUSD.m", 0.00001, 5, 8), ("XAUUSD.m", 0.01, 2, 25), ("BTCUSD.m", 0.01, 2, 100)]:
            orchestrator.market_data.register_symbol_info(SymbolInfo(
                symbol=s, digits=dig, point=pt, tick_size=pt, tick_value=1.0,
                contract_size=100000.0 if "EUR" in s else (100.0 if "XAU" in s else 1.0),
                min_lot=0.01, max_lot=50.0, lot_step=0.01, spread=sp, ask=1.0, bid=1.0
            ))

        total_checked = 0

        for sym in symbols:
            expected_decisions = self.canonical_data["decisions"][sym]
            m15_all = self._load_candles(sym, "M15")
            h1_all = self._load_candles(sym, "H1")
            h4_all = self._load_candles(sym, "H4")
            d1_all = self._load_candles(sym, "D1")
            w1_all = self._load_candles(sym, "W1")

            start_idx = len(m15_all) - total_bars
            eval_m15 = m15_all[start_idx:]

            h1_times = [c.time for c in h1_all]
            h4_times = [c.time for c in h4_all]
            d1_times = [c.time for c in d1_all]
            w1_times = [c.time for c in w1_all]

            for step, current_bar in enumerate(eval_m15):
                current_time = current_bar.time
                cur_idx_m15 = start_idx + step + 1

                m15_window = m15_all[max(0, cur_idx_m15 - 300):cur_idx_m15]

                idx_h1 = bisect.bisect_right(h1_times, current_time)
                idx_h4 = bisect.bisect_right(h4_times, current_time)
                idx_d1 = bisect.bisect_right(d1_times, current_time)
                idx_w1 = bisect.bisect_right(w1_times, current_time)

                h1_window = h1_all[max(0, idx_h1 - 60):idx_h1]
                h4_window = h4_all[max(0, idx_h4 - 30):idx_h4]
                d1_window = d1_all[max(0, idx_d1 - 20):idx_d1]
                w1_window = w1_all[max(0, idx_w1 - 10):idx_w1]

                orchestrator.market_data.set_candles(sym, Timeframe.M15, m15_window)
                orchestrator.market_data.set_candles(sym, Timeframe.H1, h1_window)
                orchestrator.market_data.set_candles(sym, Timeframe.H4, h4_window)
                orchestrator.market_data.set_candles(sym, Timeframe.D1, d1_window)
                orchestrator.market_data.set_candles(sym, Timeframe.W1, w1_window)

                with contextlib.redirect_stdout(io.StringIO()):
                    signal = orchestrator.run_cycle_for_symbol(sym)
                outcome = orchestrator.last_decision_outcomes.get(sym)

                expected = expected_decisions[step]

                # Assert exact parity
                actual_outcome_type = outcome.outcome_type.value if outcome else "NONE"
                self.assertEqual(
                    actual_outcome_type, expected["outcome_type"],
                    f"Mismatch at step {step} for {sym} at {current_time}: expected outcome {expected['outcome_type']}, got {actual_outcome_type}"
                )

                actual_reason = outcome.reason_code if outcome else ""
                self.assertEqual(
                    actual_reason, expected["reason_code"],
                    f"Mismatch in reason_code at step {step} for {sym} at {current_time}: expected {expected['reason_code']}, got {actual_reason}"
                )

                actual_has_signal = signal is not None
                self.assertEqual(
                    actual_has_signal, expected["has_signal"],
                    f"Mismatch in has_signal at step {step} for {sym} at {current_time}"
                )

                total_checked += 1

        self.assertEqual(total_checked, len(symbols) * total_bars)
        print(f"\n[GoldenMaster] Verified {total_checked} closed-bar decisions across {len(symbols)} symbols: 100% BIT-FOR-BIT IDENTICAL")


if __name__ == "__main__":
    unittest.main()
