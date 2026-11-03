"""
Historical Backtesting and Validation Runner for Institutional SMC Strategy.
Compares strategy performance with CHoCH-Warning -> BOS-Confirmation Feature ON vs OFF.
Evaluates Trade Count, Win Rate, Avg R-Multiple, Max Drawdown, and Trapped CHoCHs Avoided.
"""

from __future__ import annotations
import sys
import json
import math
import argparse
from pathlib import Path
from datetime import datetime, timezone
from typing import List, Dict, Any, Tuple

# Ensure project root is in sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.core.contracts import (
    Candle, Timeframe, ExecutionMode, SymbolInfo, OrderType, AccountInfo, Position
)
from src.core.config import AppConfig
from src.core.orchestrator import TradingBotOrchestrator


def load_historical_candles(symbol: str, timeframe: str) -> List[Candle]:
    """Loads historical JSON candles for a symbol and timeframe."""
    path = PROJECT_ROOT / "data" / "historical" / f"{symbol}_{timeframe}.json"
    if not path.exists():
        alt_sym = symbol.split(".")[0]
        path = PROJECT_ROOT / "data" / "historical" / f"{alt_sym}_{timeframe}.json"

    if not path.exists():
        return []

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


def run_simulation(
    symbol: str,
    allow_choch_only: bool,
    eval_bars: int = 1500,
    initial_balance: float = 10000.0
) -> Dict[str, Any]:
    """
    Executes a sequential bar-by-bar backtest for the specified symbol.
    """
    config = AppConfig.load_from_dir()
    config.execution_mode = ExecutionMode.SIMULATION
    config.strategy.allow_choch_only_entries = allow_choch_only
    config.telegram_bot_token = ""
    config.telegram_chat_id = ""

    orchestrator = TradingBotOrchestrator(config)
    orchestrator.alerts = None
    orchestrator.strategy_engine.allow_choch_only_entries = allow_choch_only
    orchestrator.strategy_engine.struct_confirmation.allow_choch_only_entries = allow_choch_only

    # Load candles
    m15_all = load_historical_candles(symbol, "M15")
    h1_all = load_historical_candles(symbol, "H1")
    h4_all = load_historical_candles(symbol, "H4")
    d1_all = load_historical_candles(symbol, "D1")
    w1_all = load_historical_candles(symbol, "W1")

    if len(m15_all) < eval_bars + 100:
        eval_bars = max(200, len(m15_all) - 50)

    start_idx = len(m15_all) - eval_bars
    eval_m15 = m15_all[start_idx:]

    # Register symbol info
    sym_clean = symbol.upper()
    is_fx = "EUR" in sym_clean or "GBP" in sym_clean or "USD" in sym_clean
    is_gold = "XAU" in sym_clean
    contract_size = 100000.0 if is_fx else (100.0 if is_gold else 1.0)
    point = 0.00001 if is_fx else 0.01

    sym_info = SymbolInfo(
        symbol=symbol,
        digits=5 if is_fx else 2,
        point=point,
        tick_size=point,
        tick_value=1.0,
        contract_size=contract_size,
        min_lot=0.01,
        max_lot=50.0,
        lot_step=0.01,
        spread=10 if is_fx else 25,
        ask=eval_m15[0].close,
        bid=eval_m15[0].close
    )
    orchestrator.market_data.register_symbol_info(sym_info)

    balance = initial_balance
    peak_balance = initial_balance
    max_drawdown = 0.0
    trades: List[Dict[str, Any]] = []
    active_trades: List[Dict[str, Any]] = []

    h1_times = [c.time for c in h1_all]
    h4_times = [c.time for c in h4_all]
    d1_times = [c.time for c in d1_all]
    w1_times = [c.time for c in w1_all]

    import bisect

    for step, bar in enumerate(eval_m15):
        cur_time = bar.time
        cur_idx = start_idx + step + 1

        # Feed multi-timeframe candles up to current bar
        idx_h1 = bisect.bisect_right(h1_times, cur_time)
        idx_h4 = bisect.bisect_right(h4_times, cur_time)
        idx_d1 = bisect.bisect_right(d1_times, cur_time)
        idx_w1 = bisect.bisect_right(w1_times, cur_time)

        orchestrator.market_data.set_candles(symbol, Timeframe.M15, m15_all[max(0, cur_idx - 300):cur_idx])
        orchestrator.market_data.set_candles(symbol, Timeframe.H1, h1_all[max(0, idx_h1 - 60):idx_h1])
        orchestrator.market_data.set_candles(symbol, Timeframe.H4, h4_all[max(0, idx_h4 - 30):idx_h4])
        orchestrator.market_data.set_candles(symbol, Timeframe.D1, d1_all[max(0, idx_d1 - 20):idx_d1])
        orchestrator.market_data.set_candles(symbol, Timeframe.W1, w1_all[max(0, idx_w1 - 10):idx_w1])

        # 1. Update existing open trades against current bar's High and Low
        still_open = []
        for trade in active_trades:
            direction = trade["direction"]
            sl = trade["sl"]
            tp = trade["tp"]
            risk_usd = trade["risk_usd"]
            rr = trade["rr"]

            hit_tp = False
            hit_sl = False

            if direction == OrderType.BUY:
                if bar.low <= sl:
                    hit_sl = True
                elif bar.high >= tp:
                    hit_tp = True
            else:
                if bar.high >= sl:
                    hit_sl = True
                elif bar.low <= tp:
                    hit_tp = True

            if hit_sl:
                pnl = -risk_usd
                r_mult = -1.0
                balance += pnl
                trades.append({
                    "direction": direction.value,
                    "result": "LOSS",
                    "pnl": pnl,
                    "r_multiple": r_mult,
                    "entry_price": trade["entry_price"],
                    "close_price": sl,
                    "session": trade.get("session", "UNKNOWN"),
                    "entry_time": trade["entry_time"],
                    "close_time": cur_time.isoformat()
                })
            elif hit_tp:
                pnl = risk_usd * rr
                r_mult = rr
                balance += pnl
                trades.append({
                    "direction": direction.value,
                    "result": "WIN",
                    "pnl": pnl,
                    "r_multiple": r_mult,
                    "entry_price": trade["entry_price"],
                    "close_price": tp,
                    "session": trade.get("session", "UNKNOWN"),
                    "entry_time": trade["entry_time"],
                    "close_time": cur_time.isoformat()
                })
            else:
                still_open.append(trade)

        active_trades = still_open

        # Update peak balance & drawdown
        if balance > peak_balance:
            peak_balance = balance
        dd = (peak_balance - balance) / peak_balance * 100.0 if peak_balance > 0 else 0.0
        if dd > max_drawdown:
            max_drawdown = dd

        # Update account info
        orchestrator.account_info = AccountInfo(
            account_number=1200057798,
            balance=round(balance, 2),
            equity=round(balance, 2),
            free_margin=round(balance, 2),
            trade_mode="DEMO"
        )

        # 2. Evaluate new setups if no open trade on this symbol
        if len(active_trades) == 0:
            import contextlib, io
            with contextlib.redirect_stdout(io.StringIO()):
                signal = orchestrator.run_cycle_for_symbol(symbol)

            if signal and signal.status == "APPROVED":
                risk_usd = balance * (config.risk.risk_percentage / 100.0)
                active_trades.append({
                    "direction": signal.direction,
                    "entry_price": signal.entry_price,
                    "sl": signal.stop_loss,
                    "tp": signal.take_profit,
                    "rr": signal.risk_reward,
                    "risk_usd": risk_usd,
                    "entry_time": cur_time.isoformat(),
                    "session": getattr(signal, "session_name", "UNKNOWN") or "UNKNOWN"
                })

    trapped_count = orchestrator.strategy_engine.struct_confirmation.trapped_events_count
    wins = [t for t in trades if t["result"] == "WIN"]
    losses = [t for t in trades if t["result"] == "LOSS"]
    total_trades = len(trades)
    win_rate = (len(wins) / total_trades * 100.0) if total_trades > 0 else 0.0
    r_multiples = [t["r_multiple"] for t in trades]
    avg_r = (sum(r_multiples) / total_trades) if total_trades > 0 else 0.0
    total_pnl = balance - initial_balance

    return {
        "symbol": symbol,
        "allow_choch_only": allow_choch_only,
        "bars_evaluated": len(eval_m15),
        "total_trades": total_trades,
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": round(win_rate, 2),
        "avg_r": round(avg_r, 2),
        "total_pnl": round(total_pnl, 2),
        "final_balance": round(balance, 2),
        "max_drawdown": round(max_drawdown, 2),
        "trapped_chochs_avoided": trapped_count,
        "trades": trades
    }


def run_monte_carlo_drawdown_analysis(trades: List[Dict[str, Any]], n_sims: int = 5000, risk_per_trade_pct: float = 1.0) -> Dict[str, Any]:
    """
    Monte Carlo simulation of trade-result sequence to estimate worst-case drawdown
    against MaxDailyLoss and MaxTotalDrawdown for DEMO, LIVE, and PROP profiles.
    """
    if not trades:
        return {}
    r_vals = np.array([t.get("r_multiple", 0.0) for t in trades], dtype=np.float64)
    n_trades = len(r_vals)
    sim_len = max(50, n_trades)
    rng = np.random.RandomState(42)

    max_dds = []
    demo_dd_breach = 0  # 10% DD cap
    live_dd_breach = 0  # 6% DD cap
    prop_dd_breach = 0  # 8% DD cap

    for _ in range(n_sims):
        sample = rng.choice(r_vals, size=sim_len, replace=True)
        equity = 10000.0
        peak = equity
        worst_dd = 0.0
        for r in sample:
            pnl_pct = r * (risk_per_trade_pct / 100.0)
            equity *= (1.0 + pnl_pct)
            if equity > peak:
                peak = equity
            dd = (peak - equity) / peak * 100.0
            if dd > worst_dd:
                worst_dd = dd
        max_dds.append(worst_dd)
        if worst_dd >= 10.0:
            demo_dd_breach += 1
        if worst_dd >= 6.0:
            live_dd_breach += 1
        if worst_dd >= 8.0:
            prop_dd_breach += 1

    max_dds = np.array(max_dds)
    return {
        "n_sims": n_sims,
        "median_max_dd": float(np.median(max_dds)),
        "p95_max_dd": float(np.percentile(max_dds, 95)),
        "p99_max_dd": float(np.percentile(max_dds, 99)),
        "prob_breach_demo_10pct": float(demo_dd_breach / n_sims * 100.0),
        "prob_breach_live_6pct": float(live_dd_breach / n_sims * 100.0),
        "prob_breach_prop_8pct": float(prop_dd_breach / n_sims * 100.0)
    }


def main():
    parser = argparse.ArgumentParser(description="Institutional Backtest Runner")
    parser.add_argument("--symbol", default="EURUSD.m", help="Trading symbol (e.g. EURUSD.m)")
    parser.add_argument("--bars", type=int, default=2000, help="Number of M15 bars to evaluate")
    args = parser.parse_args()

    print("=" * 80)
    print("  INSTITUTIONAL BACKTEST COMPARISON: CHoCH-BOS CONFIRMATION")
    print(f"  Symbol: {args.symbol} | Bars: {args.bars} M15")
    print("=" * 80)

    # 1. Run Feature OFF (Baseline: allow_choch_only = True)
    print("\n[1/2] Running Baseline Replay (allow_choch_only_entries = True)...")
    base_res = run_simulation(symbol=args.symbol, allow_choch_only=True, eval_bars=args.bars)

    # 2. Run Feature ON (CHoCH-Warning -> BOS-Confirmation: allow_choch_only = False)
    print("\n[2/2] Running Feature ON Replay (allow_choch_only_entries = False)...")
    feat_res = run_simulation(symbol=args.symbol, allow_choch_only=False, eval_bars=args.bars)

    # Print Comparative Report
    print("\n" + "=" * 80)
    print("                      COMPARATIVE PERFORMANCE REPORT")
    print("=" * 80)
    print(f"{'Metric':<32} | {'Feature OFF (Baseline)':<22} | {'Feature ON (Confirmed)':<22}")
    print("-" * 80)
    b_wl = f"{base_res['wins']} / {base_res['losses']}"
    f_wl = f"{feat_res['wins']} / {feat_res['losses']}"
    b_wr = f"{base_res['win_rate']} %"
    f_wr = f"{feat_res['win_rate']} %"
    b_ar = f"{base_res['avg_r']} R"
    f_ar = f"{feat_res['avg_r']} R"
    b_dd = f"{base_res['max_drawdown']} %"
    f_dd = f"{feat_res['max_drawdown']} %"
    b_pnl = f"${base_res['total_pnl']:.2f}"
    f_pnl = f"${feat_res['total_pnl']:.2f}"
    f_trap = f"{feat_res['trapped_chochs_avoided']} trapped breaks"

    print(f"{'Total Trades':<32} | {base_res['total_trades']:<22} | {feat_res['total_trades']:<22}")
    print(f"{'Wins / Losses':<32} | {b_wl:<22} | {f_wl:<22}")
    print(f"{'Win Rate (%)':<32} | {b_wr:<22} | {f_wr:<22}")
    print(f"{'Average R-Multiple':<32} | {b_ar:<22} | {f_ar:<22}")
    print(f"{'Max Drawdown (%)':<32} | {b_dd:<22} | {f_dd:<22}")
    print(f"{'Trapped CHoCHs Avoided':<32} | {'N/A (Traded Raw)':<22} | {f_trap:<22}")
    print(f"{'Total Net PnL ($)':<32} | {b_pnl:<22} | {f_pnl:<22}")

    # 3. Monte Carlo Drawdown Risk Analysis
    mc_res = run_monte_carlo_drawdown_analysis(feat_res.get("trades", []))
    if mc_res and "median_max_dd" in mc_res:
        print("\n" + "=" * 80)
        print("          MONTE CARLO DRAWDOWN RISK AUDIT (5,000 RESAMPLED PATHS)")
        print("=" * 80)
        print(f"  • Median Max Drawdown:         {mc_res['median_max_dd']:.2f}%")
        print(f"  • 95th Percentile Drawdown:     {mc_res['p95_max_dd']:.2f}%")
        print(f"  • 99th Percentile Drawdown:     {mc_res['p99_max_dd']:.2f}%")
        print(f"  • P(Breach DEMO 10% DD Limit):  {mc_res['prob_breach_demo_10pct']:.2f}%")
        print(f"  • P(Breach LIVE 6% DD Limit):   {mc_res['prob_breach_live_6pct']:.2f}%")
        print(f"  • P(Breach PROP 8% DD Limit):   {mc_res['prob_breach_prop_8pct']:.2f}%")
    print("=" * 80)


if __name__ == "__main__":
    main()
