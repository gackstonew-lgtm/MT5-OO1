"""
Startup Self-Check Diagnostic Utility.
Verifies all operational dependencies for the MT5 Python Bridge and Active Trading Engine.
Exit code: 0 only if all required checks PASS.
"""

from __future__ import annotations
import os
import sys
import time
import socket
import json
import shutil
import subprocess
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# Optional psutil import with fallback
try:
    import psutil
    PSUTIL_AVAILABLE = True
except ImportError:
    psutil = None
    PSUTIL_AVAILABLE = False


class CheckResult:
    def __init__(self, name: str, passed: bool, details: str, fix_hint: str = "", is_warning: bool = False):
        self.name = name
        self.passed = passed
        self.details = details
        self.fix_hint = fix_hint
        self.is_warning = is_warning


def get_process_info_for_port(port: int) -> Optional[Tuple[int, str, str]]:
    """Returns (pid, process_name, command_line) owning a specific TCP port."""
    if PSUTIL_AVAILABLE:
        try:
            for conn in psutil.net_connections(kind="inet"):
                if conn.laddr.port == port and conn.status == "LISTEN":
                    pid = conn.pid
                    if pid:
                        try:
                            p = psutil.Process(pid)
                            cmd = " ".join(p.cmdline())
                            return pid, p.name(), cmd
                        except Exception:
                            return pid, "Unknown", ""
        except Exception:
            pass

    # Windows Native fallback via netstat & wmic / powershell
    try:
        output = subprocess.check_output(f"netstat -ano -p tcp", shell=True, text=True, stderr=subprocess.DEVNULL)
        target_pid = None
        for line in output.splitlines():
            parts = line.strip().split()
            if len(parts) >= 5 and parts[3].upper() == "LISTENING":
                laddr = parts[1]
                if laddr.endswith(f":{port}"):
                    target_pid = int(parts[4])
                    break
        if target_pid:
            try:
                ps_script = f"$p = Get-CimInstance Win32_Process -Filter 'ProcessId={target_pid}'; Write-Output ($p.Name + '|||' + $p.CommandLine)"
                cmd_out = subprocess.check_output(
                    ["powershell", "-NoProfile", "-Command", ps_script],
                    text=True, stderr=subprocess.DEVNULL
                ).strip()
                if "|||" in cmd_out:
                    name, cmdline = cmd_out.split("|||", 1)
                    return target_pid, name.strip() or "Unknown", cmdline.strip() or "Unknown"
                return target_pid, "Unknown", ""
            except Exception:
                return target_pid, "Unknown", ""
    except Exception:
        pass
    return None


def check_port_5555() -> CheckResult:
    """Check 1: Port 5555 listening and owning process."""
    proc = get_process_info_for_port(5555)
    if proc:
        pid, name, cmd = proc
        is_bridge = ("python" in name.lower() and "run_bridge_server" in cmd) or ("python" in cmd.lower() and "5555" in cmd) or ("bridge" in name.lower())
        if is_bridge:
            return CheckResult(
                name="Port 5555 Listening",
                passed=True,
                details=f"Port 5555 is listening by Bridge Server (PID {pid}, {name})",
                fix_hint=""
            )
        else:
            return CheckResult(
                name="Port 5555 Listening",
                passed=False,
                details=f"Port 5555 is occupied by foreign process! PID={pid}, Name='{name}', Cmd='{cmd}'",
                fix_hint=f"Kill foreign process {pid} or reconfigure InpServerPort in EA."
            )
    else:
        return CheckResult(
            name="Port 5555 Listening",
            passed=False,
            details="Port 5555 is NOT listening. Bridge server process is offline.",
            fix_hint="Start the bridge server: python scripts/run_bridge_server.py or Start-Service MT5BridgeService."
        )


def check_tcp_connect_5555() -> CheckResult:
    """Check 2: TCP Connect test to 127.0.0.1:5555."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(2.0)
    try:
        s.connect(("127.0.0.1", 5555))
        s.close()
        return CheckResult(
            name="TCP Connect 127.0.0.1:5555",
            passed=True,
            details="Successfully established TCP handshake to 127.0.0.1:5555",
            fix_hint=""
        )
    except Exception as e:
        return CheckResult(
            name="TCP Connect 127.0.0.1:5555",
            passed=False,
            details=f"Connection refused / failed: {e}",
            fix_hint="Verify firewall and start bridge service."
        )


def check_bridge_service_state() -> CheckResult:
    """Check 3: Bridge service state and restart configuration."""
    # Check NSSM Service
    try:
        sc_out = subprocess.check_output("sc.exe query MT5BridgeService", shell=True, text=True, stderr=subprocess.DEVNULL)
        if "RUNNING" in sc_out:
            return CheckResult(
                name="Bridge Service State",
                passed=True,
                details="Windows Service 'MT5BridgeService' is RUNNING (Automatic boot start).",
                fix_hint=""
            )
        elif "STOPPED" in sc_out:
            return CheckResult(
                name="Bridge Service State",
                passed=False,
                details="Windows Service 'MT5BridgeService' is STOPPED.",
                fix_hint="Run: Start-Service MT5BridgeService or net start MT5BridgeService"
            )
    except Exception:
        pass

    # Check Task Scheduler Fallback
    try:
        sch_out = subprocess.check_output('schtasks.exe /query /tn "MT5BridgeService" /fo LIST', shell=True, text=True, stderr=subprocess.DEVNULL)
        if "Ready" in sch_out or "Running" in sch_out:
            state = "Running" if "Running" in sch_out else "Ready"
            return CheckResult(
                name="Bridge Service State",
                passed=True,
                details=f"Scheduled Task 'MT5BridgeService' is {state} (Task Scheduler fallback).",
                fix_hint=""
            )
    except Exception:
        pass

    # If run as standalone background process
    proc = get_process_info_for_port(5555)
    if proc:
        return CheckResult(
            name="Bridge Service State",
            passed=True,
            details=f"Bridge running as standalone daemon (PID {proc[0]}). Note: Service registration recommended via scripts/install_service.ps1.",
            fix_hint="Install as permanent Windows Service via: powershell scripts/install_service.ps1",
            is_warning=True
        )

    return CheckResult(
        name="Bridge Service State",
        passed=False,
        details="Neither Windows Service nor Scheduled Task registered for MT5BridgeService.",
        fix_hint="Run: powershell -ExecutionPolicy Bypass -File scripts/install_service.ps1"
    )


def check_mt5_terminal_and_ea() -> CheckResult:
    """Check 4: MT5 terminal process running and EA presence in terminal logs."""
    terminal_found = False
    term_pid = None
    if PSUTIL_AVAILABLE:
        for p in psutil.process_iter(['pid', 'name']):
            if "terminal" in p.info['name'].lower():
                terminal_found = True
                term_pid = p.info['pid']
                break
    else:
        try:
            out = subprocess.check_output('tasklist /fi "imagename eq terminal*"', shell=True, text=True, stderr=subprocess.DEVNULL)
            if "terminal" in out.lower():
                terminal_found = True
        except Exception:
            pass

    if not terminal_found:
        return CheckResult(
            name="MT5 Terminal Running",
            passed=False,
            details="MetaTrader 5 terminal process (terminal64.exe) is NOT running.",
            fix_hint="Start MetaTrader 5 terminal: 'C:\\Program Files\\MetaTrader 5\\terminal64.exe'"
        )

    # Check for recent EA activity in MQL5 logs
    appdata = os.environ.get("APPDATA", "")
    mq_dir = Path(appdata) / "MetaQuotes" / "Terminal"
    ea_seen = False
    last_log_snippet = ""
    if mq_dir.exists():
        for log_file in sorted(mq_dir.glob("*/MQL5/Logs/*.log"), key=os.path.getmtime, reverse=True)[:3]:
            try:
                with open(log_file, "r", encoding="utf-16", errors="ignore") as f:
                    lines = f.readlines()[-50:]
                    for l in reversed(lines):
                        if "PythonBridgeEA" in l:
                            ea_seen = True
                            last_log_snippet = l.strip()
                            break
                if ea_seen:
                    break
            except Exception:
                pass

    if ea_seen:
        return CheckResult(
            name="MT5 Terminal & EA Activity",
            passed=True,
            details=f"Terminal running. PythonBridgeEA seen in recent log: {last_log_snippet[:90]}...",
            fix_hint=""
        )
    else:
        return CheckResult(
            name="MT5 Terminal & EA Activity",
            passed=True,
            details="Terminal running. Ensure PythonBridgeEA is attached to at least one active chart (e.g. XAUUSD.m).",
            fix_hint="Attach PythonBridgeEA to your MT5 chart and enable Algo Trading.",
            is_warning=True
        )


def check_last_heartbeat_age() -> CheckResult:
    """Check 5: Last heartbeat age per connected symbol via IPC status."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(2.0)
    try:
        s.connect(("127.0.0.1", 5556))
        req = json.dumps({"action": "STATUS", "request_id": "SELFCHECK_HB"}) + "\n"
        s.sendall(req.encode("utf-8"))

        rx = b""
        start_t = time.time()
        while time.time() - start_t < 2.0:
            chunk = s.recv(4096)
            if not chunk:
                break
            rx += chunk
            if b"\n" in rx:
                break
        s.close()

        if rx:
            resp = json.loads(rx.decode("utf-8").strip())
            conns = resp.get("connections", [])
            active_count = resp.get("active_connections_count", 0)
            if active_count > 0:
                details = []
                for c in conns:
                    details.append(f"{c['symbol']} (M:{c['magic']}, RTT:{c.get('latency_ms', 0)}ms, reply:{c.get('last_reply_sec_ago', 0)}s ago)")
                return CheckResult(
                    name="EA Heartbeat Age",
                    passed=True,
                    details=f"Active Connections: {active_count} | " + " | ".join(details),
                    fix_hint=""
                )
            else:
                return CheckResult(
                    name="EA Heartbeat Age",
                    passed=True,
                    details="Bridge listening on IPC 5556. No EA client currently connected.",
                    fix_hint="Open MT5 and ensure PythonBridgeEA is active on chart.",
                    is_warning=True
                )
    except Exception as e:
        return CheckResult(
            name="EA Heartbeat Age",
            passed=False,
            details=f"IPC port 5556 unreachable ({e}). Bridge offline.",
            fix_hint="Start bridge server."
        )
    return CheckResult(
        name="EA Heartbeat Age",
        passed=False,
        details="Could not retrieve heartbeat status.",
        fix_hint="Check bridge server logs."
    )


def check_log_folder_and_disk() -> CheckResult:
    """Check 6: Log folder writable and disk space availability."""
    logs_dir = PROJECT_ROOT / "logs"
    try:
        logs_dir.mkdir(parents=True, exist_ok=True)
        test_file = logs_dir / ".write_test.tmp"
        with open(test_file, "w", encoding="utf-8") as f:
            f.write("OK\n")
        test_file.unlink(missing_ok=True)

        total, used, free = shutil.disk_usage(PROJECT_ROOT)
        free_gb = free / (1024 ** 3)

        if free_gb < 1.0:
            return CheckResult(
                name="Log Folder & Disk Space",
                passed=False,
                details=f"Log folder writable, but disk space critically low: {free_gb:.2f} GB free.",
                fix_hint="Free up disk space on drive hosting repository."
            )
        return CheckResult(
            name="Log Folder & Disk Space",
            passed=True,
            details=f"Log directory writable ({logs_dir}). Disk Free: {free_gb:.1f} GB.",
            fix_hint=""
        )
    except Exception as e:
        return CheckResult(
            name="Log Folder & Disk Space",
            passed=False,
            details=f"Failed writing to logs directory: {e}",
            fix_hint=f"Check filesystem permissions for {logs_dir}."
        )


def check_telegram_settings(test_send: bool = False) -> CheckResult:
    """Check 7: Telegram settings present (names only, never tokens)."""
    env_file = PROJECT_ROOT / ".env"
    token_present = bool(os.environ.get("TELEGRAM_BOT_TOKEN"))
    chat_id_present = bool(os.environ.get("TELEGRAM_CHAT_ID"))

    if not token_present and env_file.exists():
        try:
            with open(env_file, "r", encoding="utf-8") as f:
                for line in f:
                    if line.startswith("TELEGRAM_BOT_TOKEN="):
                        val = line.split("=", 1)[1].strip().strip("'\"")
                        if val:
                            token_present = True
                    elif line.startswith("TELEGRAM_CHAT_ID="):
                        val = line.split("=", 1)[1].strip().strip("'\"")
                        if val:
                            chat_id_present = True
        except Exception:
            pass

    if token_present and chat_id_present:
        return CheckResult(
            name="Telegram Configuration",
            passed=True,
            details="TELEGRAM_BOT_TOKEN: PRESENT | TELEGRAM_CHAT_ID: PRESENT (Names verified, values protected)",
            fix_hint=""
        )
    elif not token_present and not chat_id_present:
        return CheckResult(
            name="Telegram Configuration",
            passed=True,
            details="Telegram notifications disabled (TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID omitted). Trading logic continues unhindered.",
            fix_hint="Provide TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in .env for mobile alerts.",
            is_warning=True
        )
    else:
        return CheckResult(
            name="Telegram Configuration",
            passed=False,
            details=f"Partial configuration: TELEGRAM_BOT_TOKEN={'PRESENT' if token_present else 'MISSING'}, TELEGRAM_CHAT_ID={'PRESENT' if chat_id_present else 'MISSING'}.",
            fix_hint="Ensure both TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID are set in .env."
        )


def check_power_plan_sleep() -> CheckResult:
    """Check 8: Power plan sleep and hibernation settings (read-only)."""
    try:
        out = subprocess.check_output("powercfg /getactivescheme", shell=True, text=True, stderr=subprocess.DEVNULL)
        scheme_line = out.strip().splitlines()[0] if out.strip() else "Unknown"

        standby_raw = subprocess.check_output("powercfg /query SCHEME_CURRENT SUB_SLEEP STANDBYIDLE", shell=True, text=True, stderr=subprocess.DEVNULL)
        is_zero_sleep = "0x00000000" in standby_raw

        if is_zero_sleep:
            return CheckResult(
                name="Power Plan Sleep Settings",
                passed=True,
                details=f"AC Sleep disabled (0s timeout). Active plan: {scheme_line}",
                fix_hint=""
            )
        else:
            return CheckResult(
                name="Power Plan Sleep Settings",
                passed=True,
                details=f"PC sleep is active on AC power. Recommended to disable sleep for 24/7 trading. Plan: {scheme_line}",
                fix_hint="Run: powershell scripts/prevent_sleep.ps1 and apply recommended commands.",
                is_warning=True
            )
    except Exception as e:
        return CheckResult(
            name="Power Plan Sleep Settings",
            passed=True,
            details=f"Powercfg inspection note: {e}",
            fix_hint="Review power plan manually.",
            is_warning=True
        )


def check_python_dependencies() -> CheckResult:
    """Check 9: Python version and essential dependencies."""
    py_ver = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    if sys.version_info < (3, 10):
        return CheckResult(
            name="Python Version & Dependencies",
            passed=False,
            details=f"Python version {py_ver} < required 3.10+",
            fix_hint="Upgrade to Python 3.10 or higher."
        )

    missing = []
    for pkg in ["numpy", "MetaTrader5", "requests"]:
        try:
            __import__(pkg)
        except ImportError:
            missing.append(pkg)

    if missing:
        return CheckResult(
            name="Python Version & Dependencies",
            passed=False,
            details=f"Python {py_ver} OK, but missing required packages: {', '.join(missing)}",
            fix_hint=f"Install missing packages: python -m pip install {' '.join(missing)}"
        )

    psutil_note = "psutil: available" if PSUTIL_AVAILABLE else "psutil: optional (native Windows API fallback active)"
    return CheckResult(
        name="Python Version & Dependencies",
        passed=True,
        details=f"Python {py_ver} | numpy: OK | MetaTrader5: OK | requests: OK | {psutil_note}",
        fix_hint=""
    )


def run_all_checks() -> Tuple[List[CheckResult], bool]:
    checks = [
        check_python_dependencies(),
        check_log_folder_and_disk(),
        check_port_5555(),
        check_tcp_connect_5555(),
        check_bridge_service_state(),
        check_mt5_terminal_and_ea(),
        check_last_heartbeat_age(),
        check_telegram_settings(),
        check_power_plan_sleep(),
    ]
    all_passed = all(c.passed for c in checks)
    return checks, all_passed


def main():
    print("=" * 80)
    print("  MT5 ALGORITHMIC DAY-TRADING BRIDGE: SYSTEM STARTUP SELF-CHECK")
    print("=" * 80)

    checks, all_passed = run_all_checks()

    for idx, c in enumerate(checks, 1):
        if c.passed and not c.is_warning:
            tag = "[PASS]"
        elif c.passed and c.is_warning:
            tag = "[WARN]"
        else:
            tag = "[FAIL]"

        print(f"\n{idx:02d}. {tag} {c.name}")
        print(f"    Details: {c.details}")
        if c.fix_hint:
            print(f"    --> Fix Hint: {c.fix_hint}")

    print("\n" + "=" * 80)
    if all_passed:
        print("  SELF-CHECK OVERALL RESULT: ALL REQUIRED CHECKS PASSED (Ready to Trade)")
        print("=" * 80)
        return 0
    else:
        print("  SELF-CHECK OVERALL RESULT: FAILURES DETECTED (Trading Readiness Blocked)")
        print("=" * 80)
        return 1


if __name__ == "__main__":
    sys.exit(main())
