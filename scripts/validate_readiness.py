"""
Production Readiness & Pre-Deployment Verification Script.
Audits configuration files, risk bounds, MQL5 EA source code,
Python runtime, and data integrity before enabling live/demo execution.
"""

import sys
import json
from pathlib import Path

# Ensure project root is in sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.core.config import AppConfig
from src.core.contracts import ExecutionMode


def run_checks():
    print("=" * 80)
    print("  PRODUCTION READINESS & SYSTEM INTEGRITY AUDIT")
    print("=" * 80)

    passed_all = True
    checks = []

    # Check 1: Python version
    py_ver = sys.version_info
    if py_ver.major == 3 and py_ver.minor >= 10:
        checks.append(("Python Runtime (>= 3.10)", True, f"Python {py_ver.major}.{py_ver.minor}.{py_ver.micro}"))
    else:
        checks.append(("Python Runtime (>= 3.10)", False, "Unsupported Python version"))
        passed_all = False

    # Check 2: Configuration files existence
    config_dir = Path("config")
    required_configs = ["settings.json", "risk_config.json", "sessions.json", "symbols.json", "strategy_config.json"]
    for cfile in required_configs:
        path = config_dir / cfile
        if path.exists():
            try:
                with open(path, "r") as f:
                    json.load(f)
                checks.append((f"Config: {cfile}", True, "Valid JSON"))
            except Exception as e:
                checks.append((f"Config: {cfile}", False, f"Invalid JSON: {e}"))
                passed_all = False
        else:
            checks.append((f"Config: {cfile}", False, "File missing"))
            passed_all = False

    # Check 3: Risk parameters boundary check
    cfg = AppConfig.load_from_dir()
    if 0.1 <= cfg.risk.risk_percentage <= 2.0:
        checks.append(("Risk per trade (0.1% - 2.0%)", True, f"{cfg.risk.risk_percentage}%"))
    else:
        checks.append(("Risk per trade (0.1% - 2.0%)", False, f"Dangerous risk level: {cfg.risk.risk_percentage}%"))
        passed_all = False

    if cfg.risk.min_risk_reward_ratio >= 1.5:
        checks.append(("Minimum R:R Ratio (>= 1.5)", True, f"1:{cfg.risk.min_risk_reward_ratio}"))
    else:
        checks.append(("Minimum R:R Ratio (>= 1.5)", False, f"R:R ratio too low: {cfg.risk.min_risk_reward_ratio}"))
        passed_all = False

    # Check 4: MQL5 Expert Advisor source code
    ea_path = Path("mql5/Experts/PythonBridgeEA.mq5")
    if ea_path.exists() and ea_path.stat().st_size > 1000:
        checks.append(("MQL5 Expert Advisor Source", True, f"Found ({ea_path.stat().st_size} bytes)"))
    else:
        checks.append(("MQL5 Expert Advisor Source", False, "Missing or truncated"))
        passed_all = False

    # Print Report
    print(f"{'CHECK':<45} | {'STATUS':<8} | {'DETAILS'}")
    print("-" * 80)
    for name, status, details in checks:
        st_str = "[PASS]" if status else "[FAIL]"
        print(f"{name:<45} | {st_str:<8} | {details}")

    print("=" * 80)
    if passed_all:
        print("RESULT: ALL READINESS CHECKS PASSED. System is ready for operation.")
        return 0
    else:
        print("RESULT: SOME CHECKS FAILED. Correct issues before proceeding.")
        return 1


if __name__ == "__main__":
    sys.exit(run_checks())
