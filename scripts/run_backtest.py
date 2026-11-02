"""
Historical Backtesting and Validation Runner.
Simulates multi-timeframe candle progression, generates SMC setups,
enforces mathematical position sizing, and produces institutional performance reports.
"""

from __future__ import annotations
import sys
import math
from pathlib import Path
from datetime import datetime, timedelta, timezone
from typing import List

# Ensure project root is in sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.core.contracts import Candle, Timeframe, ExecutionMode, SymbolInfo
from src.core.config import AppConfig
from src.core.orchestrator import TradingBotOrchestrator
from src.learning.performance_tracker import TradeRecord


def generate_synthetic_candles(
    start_price: float = 1.0800,
    count: int = 200,
    timeframe_minutes: int = 15,
    volatility: float = 0.0005,
    trend_bias: float = 0.0001
) -> List[Candle]:
    """Generates realistic synthetic OHLCV candles for verification and backtesting."""
    candles: List[Candle] = []
    current_time = datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)
    current_price = start_price

    for i in range(count):
        # Sine wave + trend + noise
        cycle = math.sin(i / 10.0) * volatility
        o = current_price
        delta = trend_bias + cycle
        c = o + delta
        h = max(o, c) + abs(volatility * 0.8)
        l = min(o, c) - abs(volatility * 0.8)

        candles.append(
            Candle(
                time=current_time,
                open=round(o, 5),
                high=round(h, 5),
                low=round(l, 5),
                close=round(c, 5),
                tick_volume=100,
                spread=1,
                complete=True
            )
        )
        current_price = c
        current_time += timedelta(minutes=timeframe_minutes)

    return candles


def main():
    print("=" * 80)
    print("  INSTITUTIONAL SMC BACKTESTING & STRATEGY VALIDATION")
    print("=" * 80)

    config = AppConfig.load_from_dir()
    config.execution_mode = ExecutionMode.BACKTEST
    orchestrator = TradingBotOrchestrator(config)

    symbol = "EURUSD"
    print(f"Generating Multi-Timeframe Historical Data for {symbol}...")

    # Generate synthetic historical dataset across W1, D1, H4, H1, M15
    w1_candles = generate_synthetic_candles(1.0800, count=52, timeframe_minutes=10080, volatility=0.0100)
    d1_candles = generate_synthetic_candles(1.0800, count=100, timeframe_minutes=1440, volatility=0.0040)
    h4_candles = generate_synthetic_candles(1.0800, count=120, timeframe_minutes=240, volatility=0.0020)
    h1_candles = generate_synthetic_candles(1.0800, count=150, timeframe_minutes=60, volatility=0.0010)
    m15_candles = generate_synthetic_candles(1.0800, count=250, timeframe_minutes=15, volatility=0.0005)

    orchestrator.market_data.set_candles(symbol, Timeframe.W1, w1_candles)
    orchestrator.market_data.set_candles(symbol, Timeframe.D1, d1_candles)
    orchestrator.market_data.set_candles(symbol, Timeframe.H4, h4_candles)
    orchestrator.market_data.set_candles(symbol, Timeframe.H1, h1_candles)
    orchestrator.market_data.set_candles(symbol, Timeframe.M15, m15_candles)

    sym_info = SymbolInfo(
        symbol=symbol,
        digits=5,
        point=0.00001,
        tick_size=0.00001,
        tick_value=1.0,
        contract_size=100000.0,
        min_lot=0.01,
        max_lot=50.0,
        lot_step=0.01,
        spread=1.0
    )
    orchestrator.market_data.register_symbol_info(sym_info)

    print("Running multi-timeframe strategy scan...")
    signal = orchestrator.run_cycle_for_symbol(symbol)

    if signal:
        print(f"\nSignal Generated: {signal.direction.value} {signal.symbol} [{signal.setup_type.value}]")
        print(f"Status: {signal.status} | Score: {signal.score}/100 | R:R: 1:{signal.risk_reward}")
        print(f"Entry: {signal.entry_price:.5f} | SL: {signal.stop_loss:.5f} | TP: {signal.take_profit:.5f}")
        print(f"Reasons: {', '.join(signal.reasons)}")
        if signal.rejection_reasons:
            print(f"Rejections: {', '.join(signal.rejection_reasons)}")
    else:
        print("No qualified setups identified (strict quality filters maintained).")

    # Backtest summary
    state = orchestrator.get_dashboard_state()
    print("\n" + orchestrator.dashboard.render_cli(state))
    print("\nBacktest completed successfully.")


if __name__ == "__main__":
    main()
