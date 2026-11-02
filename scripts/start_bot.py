"""
Main Application Entry Point: Starts the Algorithmic Day-Trading Engine.
"""

import sys
import time
from pathlib import Path
from datetime import datetime, timezone

# Ensure project root is in sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.core.config import AppConfig
from src.core.orchestrator import TradingBotOrchestrator
from src.core.contracts import ExecutionMode


def main():
    print("=" * 80)
    print("  INSTITUTIONAL MULTI-TIMEFRAME MT5 ALGORITHMIC TRADING SYSTEM")
    print("=" * 80)

    config = AppConfig.load_from_dir()
    if "--dry-run" in sys.argv:
        config.dry_run = True
        print("[MODE] DRY-RUN MODE ENABLED: Exercising sizing and all filters without sending orders.")

    print(f"Loaded Configuration | Execution Mode: {config.execution_mode.value}")
    print(f"Monitored Symbols: {', '.join(config.symbols)}")
    print(f"Risk per Trade: {config.risk.risk_percentage}% | Min R:R: 1:{config.risk.min_risk_reward_ratio}")

    orchestrator = TradingBotOrchestrator(config)

    # In DEMO/REAL mode, try connecting to MT5 Bridge
    if config.execution_mode in [ExecutionMode.DEMO, ExecutionMode.REAL]:
        print(f"Connecting to MT5 Bridge at {config.bridge_host}:{config.bridge_port}...")
        if orchestrator.bridge.connect():
            print("Connected to MT5 Bridge successfully.")
            acc = orchestrator.bridge.get_account()
            if acc:
                orchestrator.update_account(acc)
                print(f"[ACCOUNT_MODE] Synchronized: {acc.trade_mode} [{acc.margin_mode_str}] | Login: {acc.account_number} | Equity: ${acc.equity:.2f}")
        else:
            print("WARNING: MT5 Bridge unreachable. Operating in guarded standby.")

    print("\nStarting continuous market scan loop (Press Ctrl+C to stop)...")
    try:
        cycles = 0
        while True:
            cycles += 1
            for sym in config.symbols:
                orchestrator.run_cycle_for_symbol(sym)

            orchestrator.manage_active_positions()

            # Render dashboard every 10 cycles
            if cycles % 10 == 0:
                state = orchestrator.get_dashboard_state()
                print(orchestrator.dashboard.render_cli(state))

            time.sleep(1.0)
    except KeyboardInterrupt:
        print("\n[Shutdown] Stopping trading engine safely...")
        orchestrator.bridge.disconnect()
        print("[Shutdown] Engine stopped cleanly.")


if __name__ == "__main__":
    main()
