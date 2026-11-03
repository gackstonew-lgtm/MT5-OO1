"""
Active Trading Mode: One Day Historical Bar Replay Demonstration.
Replays 24 hours of closed bars across at least 3 symbols (XAUUSD.m, BTCUSD.m, EURUSD)
showing honest outcome lines for every closed bar, frequency caps, and active mode metrics.
"""

import sys
import os
from pathlib import Path
from datetime import datetime, timezone

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.core.contracts import Candle, Timeframe, OrderType, AccountInfo, SymbolInfo
from src.core.config import AppConfig
from src.core.orchestrator import TradingBotOrchestrator
from src.data.historical_loader import HistoricalDataLoader


def main():
    print("=" * 80)
    print("  ACTIVE TRADING MODE: 1-DAY HISTORICAL DATA REPLAY (3 SYMBOLS)")
    print("=" * 80)

    config = AppConfig.load_from_dir()
    config.strategy.active_scalp_mode = True
    config.dry_run = True  # Output simulated trades without live order execution
    orchestrator = TradingBotOrchestrator(config)

    loader = HistoricalDataLoader()
    symbols = ["XAUUSD.m", "BTCUSD.m", "EURUSD"]

    # 1 full day of M15 bars is 96 bars (24 hours * 4 bars/hr)
    BARS_IN_DAY = 96
    candles_by_symbol = {}

    for sym in symbols:
        candles = loader.load_candles(sym, Timeframe.M15, count=BARS_IN_DAY + 30, use_cache=True)
        if len(candles) >= BARS_IN_DAY + 20:
            candles_by_symbol[sym] = candles
            print(f"--> Loaded {len(candles)} bars for {sym}")
        else:
            print(f"--> Warning: Only {len(candles)} bars for {sym}")

    print("\n" + "=" * 80)
    print("  STARTING 24-HOUR CLOSED BAR REPLAY")
    print("=" * 80)

    outcomes_summary = {"TRADE_SENT": 0, "TRADE_SKIPPED": 0, "NO_SETUP": 0}

    # Replay 96 sequential bars
    for bar_idx in range(BARS_IN_DAY):
        for sym in symbols:
            if sym not in candles_by_symbol:
                continue
            all_candles = candles_by_symbol[sym]
            # Sliced history up to current closed bar
            current_slice = all_candles[:len(all_candles) - BARS_IN_DAY + bar_idx + 1]
            orchestrator.market_data.set_candles(sym, Timeframe.M15, current_slice)

            # Evaluate closed bar
            candidate = orchestrator.run_cycle_for_symbol(sym)
            last_outcome = orchestrator.last_decision_outcomes.get(sym)
            if last_outcome:
                outcomes_summary[last_outcome.outcome_type.value] = outcomes_summary.get(last_outcome.outcome_type.value, 0) + 1

    print("\n" + "=" * 80)
    print("  24-HOUR REPLAY SUMMARY ACROSS ALL 3 SYMBOLS")
    print("=" * 80)
    print(f"Total Closed Bars Evaluated: {BARS_IN_DAY * len(candles_by_symbol)}")
    print(f"  • TRADE SENT Outcomes:     {outcomes_summary.get('TRADE_SENT', 0)}")
    print(f"  • TRADE SKIPPED Outcomes:  {outcomes_summary.get('TRADE_SKIPPED', 0)}")
    print(f"  • NO SETUP Outcomes:       {outcomes_summary.get('NO_SETUP', 0)}")
    print(f"Trades Per Day Across 3 Symbols: {outcomes_summary.get('TRADE_SENT', 0)}")
    print("=" * 80)
    return 0


if __name__ == "__main__":
    sys.exit(main())
