"""
Golden Master Decision Dataset Generator.
Replays 30 recorded trading days across all enabled symbols (EURUSD.m, XAUUSD.m, BTCUSD.m)
through the untouched baseline TradingBotOrchestrator.
Captures every single closed-bar decision outcome, filter check, and order specification.
Persists the canonical dataset to data/golden_master/baseline_decisions_30d.json.
"""

from __future__ import annotations
import os
import sys
import json
import time
import bisect
import io
import contextlib
from pathlib import Path
from datetime import datetime, timezone
from typing import Dict, List, Any

# Ensure project root in sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.core.contracts import Candle, Timeframe, AccountInfo, DecisionOutcome, ExecutionMode, SymbolInfo
from src.core.orchestrator import TradingBotOrchestrator
from src.core.config import AppConfig


def load_candles(symbol: str, tf: str) -> List[Candle]:
    """Loads historical JSON candles for a symbol and timeframe."""
    path = PROJECT_ROOT / "data" / "historical" / f"{symbol}_{tf}.json"
    if not path.exists():
        # Try without suffix
        alt_sym = symbol.split(".")[0]
        path = PROJECT_ROOT / "data" / "historical" / f"{alt_sym}_{tf}.json"

    if not path.exists():
        return []

    with open(path, "r", encoding="utf-8") as f:
        raw_list = json.load(f)

    candles = []
    for item in raw_list:
        t_str = item["time"]
        t = datetime.fromisoformat(t_str)
        c = Candle(
            time=t,
            open=float(item["open"]),
            high=float(item["high"]),
            low=float(item["low"]),
            close=float(item["close"]),
            tick_volume=int(item.get("tick_volume", 100)),
            spread=int(item.get("spread", 10)),
            complete=bool(item.get("complete", True))
        )
        candles.append(c)
    return candles


def main():
    print("=" * 80)
    print("  RECORDING GOLDEN MASTER BASELINE DECISIONS (30 TRADING DAYS)")
    print("=" * 80)

    symbols = ["EURUSD.m", "XAUUSD.m", "BTCUSD.m"]
    days_count = 30
    bars_per_day = 96  # M15 has 96 bars per 24 hours
    total_bars = days_count * bars_per_day  # 2,880 M15 bars

    output_dir = PROJECT_ROOT / "tests" / "fixtures"
    output_dir.mkdir(parents=True, exist_ok=True)
    output_file = output_dir / "baseline_decisions_30d.json"

    # 1. Initialize Baseline Orchestrator
    config = AppConfig.load_from_dir()
    config.symbols = symbols
    config.execution_mode = ExecutionMode.SIMULATION
    # Mute Telegram alerts during replay
    config.telegram_bot_token = ""
    config.telegram_chat_id = ""

    orchestrator = TradingBotOrchestrator(config)
    orchestrator.alerts = None  # Silence telegram queue during historical batch replay
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

    dataset: Dict[str, Any] = {
        "metadata": {
            "version": "1.0.0",
            "tag": "baseline-pre-upgrade",
            "recorded_at": datetime.now(timezone.utc).isoformat(),
            "symbols": symbols,
            "days_count": days_count,
            "bars_per_symbol": total_bars,
            "commit": "4b7127e4d5c918f1daf902c243f9882e842ebaed"
        },
        "decisions": {}
    }

    t0_all = time.time()

    for sym in symbols:
        print(f"\n[Processing {sym}] Loading multi-timeframe candle datasets...")
        m15_all = load_candles(sym, "M15")
        h1_all = load_candles(sym, "H1")
        h4_all = load_candles(sym, "H4")
        d1_all = load_candles(sym, "D1")
        w1_all = load_candles(sym, "W1")

        if len(m15_all) < total_bars + 50:
            raise ValueError(f"Insufficient M15 history for {sym}: have {len(m15_all)}, need {total_bars + 50}")

        # Slice the 30-day evaluation window (ending at the latest available bar)
        start_idx = len(m15_all) - total_bars
        eval_m15 = m15_all[start_idx:]
        print(f"  Evaluation Window: {eval_m15[0].time.isoformat()} -> {eval_m15[-1].time.isoformat()} ({len(eval_m15)} M15 bars)")

        h1_times = [c.time for c in h1_all]
        h4_times = [c.time for c in h4_all]
        d1_times = [c.time for c in d1_all]
        w1_times = [c.time for c in w1_all]

        sym_decisions: List[Dict[str, Any]] = []
        t0_sym = time.time()

        for step, current_bar in enumerate(eval_m15):
            current_time = current_bar.time
            cur_idx_m15 = start_idx + step + 1

            # Window of candles up to and including current closed bar
            m15_window = m15_all[max(0, cur_idx_m15 - 300):cur_idx_m15]

            # Fast binary search slice (no look-ahead)
            idx_h1 = bisect.bisect_right(h1_times, current_time)
            idx_h4 = bisect.bisect_right(h4_times, current_time)
            idx_d1 = bisect.bisect_right(d1_times, current_time)
            idx_w1 = bisect.bisect_right(w1_times, current_time)

            h1_window = h1_all[max(0, idx_h1 - 60):idx_h1]
            h4_window = h4_all[max(0, idx_h4 - 30):idx_h4]
            d1_window = d1_all[max(0, idx_d1 - 20):idx_d1]
            w1_window = w1_all[max(0, idx_w1 - 10):idx_w1]

            if step > 0 and step % 500 == 0:
                print(f"    [{sym}] Evaluated {step}/{len(eval_m15)} closed bars...", flush=True)
            orchestrator.market_data.set_candles(sym, Timeframe.M15, m15_window)
            orchestrator.market_data.set_candles(sym, Timeframe.H1, h1_window)
            orchestrator.market_data.set_candles(sym, Timeframe.H4, h4_window)
            orchestrator.market_data.set_candles(sym, Timeframe.D1, d1_window)
            orchestrator.market_data.set_candles(sym, Timeframe.W1, w1_window)

            # Evaluate closed bar quietly
            with contextlib.redirect_stdout(io.StringIO()):
                signal = orchestrator.run_cycle_for_symbol(sym)
            outcome = orchestrator.last_decision_outcomes.get(sym)

            record: Dict[str, Any] = {
                "step": step,
                "bar_time": current_time.isoformat(),
                "open": current_bar.open,
                "high": current_bar.high,
                "low": current_bar.low,
                "close": current_bar.close,
                "outcome_type": outcome.outcome_type.value if outcome else "NONE",
                "reason_code": outcome.reason_code if outcome else "",
                "details": outcome.details if outcome else "",
                "real_numbers": outcome.real_numbers if outcome else {},
                "has_signal": signal is not None
            }

            if signal:
                record["signal"] = {
                    "setup_type": signal.setup_type.value,
                    "direction": signal.direction.value,
                    "entry_price": signal.entry_price,
                    "stop_loss": signal.stop_loss,
                    "take_profit": signal.take_profit,
                    "risk_reward": signal.risk_reward,
                    "confluence_score": signal.score
                }

            sym_decisions.append(record)

        sym_duration = time.time() - t0_sym
        print(f"  Completed {len(sym_decisions)} decisions for {sym} in {sym_duration:.2f}s ({sym_duration/len(sym_decisions)*1000:.1f}ms/bar)")
        dataset["decisions"][sym] = sym_decisions

    total_duration = time.time() - t0_all
    print(f"\nWriting canonical golden master dataset to {output_file}...")
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(dataset, f, indent=2)

    total_records = sum(len(d) for d in dataset["decisions"].values())
    print(f"Successfully recorded {total_records} canonical decisions across 3 symbols in {total_duration:.2f}s.")
    print("=" * 80)


if __name__ == "__main__":
    main()
