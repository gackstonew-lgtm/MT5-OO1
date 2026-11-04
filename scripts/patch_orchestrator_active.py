import sys
import re

with open("src/core/orchestrator.py", "r", encoding="utf-8") as f:
    content = f.read()

# 1. Imports
target_imports = "from src.monitoring.alerts_telegram import TelegramAlertManager"
new_imports = """from src.monitoring.alerts_telegram import TelegramAlertManager
from src.execution.active_mode import ActiveTradingEngine, ActiveTradingConfig, ProhibitionViolationError
from concurrent.futures import ThreadPoolExecutor"""

if target_imports in content:
    content = content.replace(target_imports, new_imports)
    print("Replaced imports")
else:
    print("Failed to find target imports")

# 2. Init active engine
target_init = """        self.dashboard = TradingDashboard(execution_mode=self.config.execution_mode.value)"""
new_init = """        self.dashboard = TradingDashboard(execution_mode=self.config.execution_mode.value)
        self.active_engine = ActiveTradingEngine()"""

if target_init in content:
    content = content.replace(target_init, new_init)
    print("Replaced __init__")
else:
    print("Failed to find target init")

# 3. Add parallel scan method and prohibition checks in run_cycle_for_symbol
target_pre_dry = """        # Dry run support
        if getattr(self.config, "dry_run", False):"""

active_check = """        # Active Mode Prohibitions & Frequency Controls (Part 6)
        if getattr(self.config.strategy, "active_scalp_mode", True) and hasattr(self, "active_engine"):
            point = symbol_info.point or 0.00001
            atr_pts = 100.0
            if m15 and len(m15) >= 14:
                ranges = [abs(c.high - c.low) / point for c in m15[-14:]]
                atr_pts = sum(ranges) / len(ranges)

            allowed, prohib_msg = self.active_engine.verify_prohibitions(
                symbol=symbol,
                direction=best.direction,
                candidate_price=best.entry_price,
                candidate_risk_pct=risk_pct,
                candidate_lots=check.calculated_volume,
                atr=atr_pts * point,
                account=self.account_info,
                active_positions=active_positions
            )
            if not allowed:
                outcome = DecisionOutcome(
                    symbol=symbol,
                    bar_time=bar_time,
                    outcome_type=DecisionOutcomeType.TRADE_SKIPPED,
                    reason_code="PROHIBITION_BLOCKED",
                    details=prohib_msg,
                    real_numbers={"reason": prohib_msg, "balance": bal}
                )
                self._record_and_dispatch_outcome(outcome)
                return None

        # Dry run support
        if getattr(self.config, "dry_run", False):"""

if target_pre_dry in content:
    content = content.replace(target_pre_dry, active_check)
    print("Replaced pre-trade prohibition check")
else:
    print("Failed to find target pre dry")

# 4. Record trade opened in active engine on filled trade
target_fill = """            self._record_and_dispatch_outcome(outcome)
            self.alerts.alert_trade_executed("""

new_fill = """            self._record_and_dispatch_outcome(outcome)
            if hasattr(self, "active_engine"):
                self.active_engine.record_trade_opened(symbol=order.symbol, lots=order.volume, risk_pct=risk_pct)
            self.alerts.alert_trade_executed("""

if target_fill in content:
    content = content.replace(target_fill, new_fill)
    print("Replaced fill recording")
else:
    print("Failed to find target fill")

# 5. Add run_cycle_all_symbols
parallel_code = """
    def run_cycle_all_symbols(self) -> Dict[str, Optional[SignalCandidate]]:
        \"\"\"Scans all enabled symbols in parallel with a concurrency cap (Part 6 #1).\"\"\"
        max_workers = min(len(self.config.symbols), getattr(self.active_engine.config, "max_simultaneous_evaluations", 4))
        results = {}
        with ThreadPoolExecutor(max_workers=max(max_workers, 1)) as executor:
            futures = {executor.submit(self.run_cycle_for_symbol, sym): sym for sym in self.config.symbols}
            for fut in futures:
                sym = futures[fut]
                try:
                    results[sym] = fut.result()
                except Exception as e:
                    print(f"Error evaluating symbol {sym} in parallel cycle: {e}")
                    results[sym] = None
        return results
"""

content += parallel_code

with open("src/core/orchestrator.py", "w", encoding="utf-8") as f:
    f.write(content)
print("Finished patching src/core/orchestrator.py")
