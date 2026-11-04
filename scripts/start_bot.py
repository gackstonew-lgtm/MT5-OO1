"""
Main Application Entry Point: Starts the Algorithmic Day-Trading & Continuous Scalping Engine.
Supports continuous multi-symbol M1/M5/M15 scalping, startup candle history warm-up,
incremental candle updates, automatic profit locking ($5+ target, break-even, trailing stop, partial TP),
anti-dormancy health monitoring, and safe --dry-run / DEMO execution.
"""

import argparse
from datetime import datetime, timezone
from pathlib import Path
import sys
import time

# Ensure project root is in sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.core.config import AppConfig
from src.core.contracts import AccountInfo, ExecutionMode
from src.core.orchestrator import TradingBotOrchestrator


def _sync_account_from_mt5_direct(orchestrator: TradingBotOrchestrator) -> bool:
    """Fallback account synchronization directly from MetaTrader5 Python API."""
    try:
        import MetaTrader5 as mt5

        if not mt5.initialize():
            return False
        info = mt5.account_info()
        if info is None:
            return False
        trade_mode = "DEMO" if int(getattr(info, "trade_mode", 0)) == 0 else "REAL"
        acc = AccountInfo(
            account_number=int(info.login),
            balance=float(info.balance),
            equity=float(info.equity),
            margin=float(info.margin),
            free_margin=float(info.margin_free),
            margin_level=float(info.margin_level),
            currency=str(info.currency),
            leverage=int(info.leverage),
            trade_mode=trade_mode,
            profile="DEMO" if trade_mode == "DEMO" else "LIVE",
            trading_enabled=True,
            margin_mode=int(getattr(info, "margin_mode", 2)),
            margin_mode_str="NETTING" if int(getattr(info, "margin_mode", 2)) == 1 else "HEDGING",
        )
        orchestrator.update_account(acc)
        masked_login = f"***{str(acc.account_number)[-4:]}" if acc.account_number else "N/A"
        print(
            f"[ACCOUNT_MODE] Direct MT5 Synchronized: {acc.trade_mode} [{acc.margin_mode_str}] | "
            f"Account: {masked_login} | Balance: ${acc.balance:.2f} | Equity: ${acc.equity:.2f}"
        )
        return True
    except Exception:
        return False


def main():
    parser = argparse.ArgumentParser(description="Institutional Multi-Timeframe MT5 Continuous Scalping System")
    parser.add_argument("--dry-run", action="store_true", help="Run in safe dry-run mode without sending live orders")
    parser.add_argument("--max-cycles", type=int, default=0, help="Maximum scan cycles to run (0 = continuous)")
    parser.add_argument("--interval", type=float, default=1.0, help="Sleep interval in seconds between cycles")
    args, _ = parser.parse_known_args()

    print("=" * 80)
    print("  INSTITUTIONAL MULTI-TIMEFRAME MT5 CONTINUOUS SCALPING SYSTEM")
    print("=" * 80)

    config = AppConfig.load_from_dir()
    if args.dry_run or "--dry-run" in sys.argv:
        config.dry_run = True
        print("[MODE] DRY-RUN MODE ENABLED: Exercising sizing and all filters without sending orders.")

    print(
        f"Loaded Configuration | Execution Mode: {config.execution_mode.value} | "
        f"Trading Mode: {getattr(config, 'trading_mode', 'CONTINUOUS_SCALPING')} | "
        f"News Filter: {'OFF' if not getattr(config, 'news_filter_enabled', False) else 'ON'}"
    )
    print(f"Monitored Symbols: {', '.join(config.symbols)}")
    print(
        f"Risk Profile: {getattr(config.risk, 'risk_profile', 'ADAPTIVE_SCALPING')} | "
        f"Risk per Trade: {config.risk.risk_percentage}% | "
        f"Scalp Min R:R: 1:{getattr(config.risk, 'scalping_min_risk_reward_ratio', 1.5)} | "
        f"Min Net Profit Target: ${config.risk.min_net_profit_target_usd:.2f}"
    )

    orchestrator = TradingBotOrchestrator(config)

    # In DEMO/REAL mode, try connecting to MT5 Bridge first, then fall back to direct MT5 Python API
    if config.execution_mode in [ExecutionMode.DEMO, ExecutionMode.REAL]:
        print(f"Connecting to MT5 Bridge at {config.bridge_host}:{config.bridge_port}...")
        if orchestrator.bridge.connect():
            print("Connected to MT5 Bridge successfully.")
            acc = orchestrator.bridge.get_account()
            if acc:
                orchestrator.update_account(acc)
                masked_login = f"***{str(acc.account_number)[-4:]}" if acc.account_number else "N/A"
                print(
                    f"[ACCOUNT_MODE] Bridge Synchronized: {acc.trade_mode} [{acc.margin_mode_str}] | "
                    f"Account: {masked_login} | Equity: ${acc.equity:.2f}"
                )
        else:
            print("[BRIDGE] TCP socket not active; synchronizing account & market data directly via MetaTrader5 API...")
            _sync_account_from_mt5_direct(orchestrator)

    # Warm up historical candles across all configured symbols and timeframes (W1..M1)
    print("\n[WARMUP] Synchronizing multi-timeframe candle history (W1, D1, H4, H1, M30, M15, M5, M1)...")
    orchestrator.initialize_market_data(max_retries=3)
    if hasattr(orchestrator, "alerts") and orchestrator.alerts:
        orchestrator.alerts.alert_bot_started(
            details=f"Continuous Scalping Active on {', '.join(config.symbols)} (News Filter: OFF)"
        )

    print("\nStarting continuous scalping market scan loop (Press Ctrl+C to stop)...")
    try:
        cycles = 0
        while True:
            cycles += 1
            cycle_start = time.time()

            # Check health & auto-recover if needed
            orchestrator.health_monitor.check_and_recover_connections(
                bridge=orchestrator.bridge,
                market_data=orchestrator.market_data,
                dry_run=getattr(config, "dry_run", False),
            )

            for sym in orchestrator.continuous_scalper.get_ordered_symbols_for_cycle():
                try:
                    if getattr(config, "trading_mode", "CONTINUOUS_SCALPING") == "CONTINUOUS_SCALPING":
                        sig = orchestrator.run_scalping_cycle_for_symbol(sym)
                        if sig is None:
                            orchestrator.run_cycle_for_symbol(sym)
                    else:
                        orchestrator.run_cycle_for_symbol(sym)
                except Exception as sym_err:
                    print(f"[ERROR] Isolated exception during scan of {sym}: {sym_err}")

            try:
                orchestrator.manage_active_positions()
            except Exception as mgmt_err:
                print(f"[ERROR] Isolated exception during position management: {mgmt_err}")

            # Render dashboard & health report periodically or on final bounded cycle
            if cycles % 10 == 0 or (args.max_cycles > 0 and cycles == args.max_cycles):
                print(orchestrator.health_monitor.format_diagnostic_report())
                state = orchestrator.get_dashboard_state()
                print(orchestrator.dashboard.render_cli(state))

            if args.max_cycles > 0 and cycles >= args.max_cycles:
                print(f"\n[COMPLETE] Completed {cycles}/{args.max_cycles} scheduled scan cycles cleanly.")
                break

            elapsed = time.time() - cycle_start
            sleep_for = max(0.05, float(args.interval) - elapsed)
            time.sleep(sleep_for)
    except KeyboardInterrupt:
        print("\n[Shutdown] Stopping trading engine safely...")
    finally:
        orchestrator.bridge.disconnect()
        print("[Shutdown] Engine stopped cleanly.")


if __name__ == "__main__":
    main()
