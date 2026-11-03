"""
Standalone Persistent Bridge Server Runner with Built-in Process Watchdog,
Environment Variable Loading, and Rotating File Logging.
"""

from __future__ import annotations
import os
import sys
import time
import socket
import logging
from logging.handlers import RotatingFileHandler
import threading
from pathlib import Path

# Ensure project root is in sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# Load .env file safely
env_path = PROJECT_ROOT / ".env"
if env_path.exists():
    try:
        with open(env_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    k = k.strip()
                    v = v.strip().strip("'\"")
                    if k not in os.environ:
                        os.environ[k] = v
    except Exception as e:
        print(f"Warning: Failed to parse .env: {e}")

# Setup logs directory and rotating file handler (size-based rotation, 10MB per file, keep 14 files)
LOGS_DIR = PROJECT_ROOT / "logs"
LOGS_DIR.mkdir(parents=True, exist_ok=True)
log_file = LOGS_DIR / "bridge_service.log"

logger = logging.getLogger("BridgeRunner")
logger.setLevel(logging.INFO)

formatter = logging.Formatter(
    "[%(asctime)s.%(msecs)03d] [BridgeRunner] [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)

# Console handler
console_handler = logging.StreamHandler(sys.stdout)
console_handler.setFormatter(formatter)
logger.addHandler(console_handler)

# Rotating file handler
file_handler = RotatingFileHandler(
    log_file,
    maxBytes=10 * 1024 * 1024,  # 10 MB per file
    backupCount=14,             # Keep 14 files (~14 days history)
    encoding="utf-8"
)
file_handler.setFormatter(formatter)
logger.addHandler(file_handler)

from src.execution.bridge_server import PersistentBridgeServer


class WatchdogManager:
    """Monitors the bridge server process. If main loop hangs for > 60s, triggers exit for restart."""
    def __init__(self, timeout_sec: float = 60.0):
        self.timeout_sec = timeout_sec
        self.last_heartbeat = time.time()
        self.is_running = True
        self.thread = threading.Thread(target=self._monitor_loop, daemon=True, name="BridgeWatchdog")
        self.thread.start()

    def beat(self):
        self.last_heartbeat = time.time()

    def _monitor_loop(self):
        while self.is_running:
            time.sleep(5.0)
            elapsed = time.time() - self.last_heartbeat
            if elapsed > self.timeout_sec:
                logger.critical(
                    f"[WATCHDOG_HANG_DETECTED] Bridge server main loop hung for {elapsed:.1f}s "
                    f"(> {self.timeout_sec}s threshold). Forcing process exit to trigger automatic service restart."
                )
                try:
                    sys.stderr.flush()
                    sys.stdout.flush()
                except Exception:
                    pass
                os._exit(42)  # Non-zero exit code triggers service/scheduler restart


def run_startup_selfcheck(server_port: int = 5555) -> bool:
    """Pre-flight check: ensure port 5555 is free before starting listener."""
    logger.info("Executing startup pre-flight check...")
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(1.0)
    try:
        s.connect(("127.0.0.1", server_port))
        # If we can connect before server starts, another process owns port 5555!
        logger.warning(f"[PREFLIGHT_CONFLICT] Port {server_port} is already listening by another process!")
        s.close()
        return False
    except (ConnectionRefusedError, socket.timeout):
        logger.info(f"[PREFLIGHT_PASS] Port {server_port} is clear and ready.")
        return True
    except Exception as e:
        logger.info(f"[PREFLIGHT_NOTE] Port check: {e}")
        return True
    finally:
        try:
            s.close()
        except Exception:
            pass


def main():
    logger.info("=" * 70)
    logger.info("Initializing Persistent Bridge Server Daemon (Service Mode)...")
    logger.info(f"Working Directory: {PROJECT_ROOT}")
    logger.info(f"Python Executable: {sys.executable}")
    logger.info(f"Logging to: {log_file} (10MB rotation, keep 14 files)")
    logger.info("=" * 70)

    # Initialize Watchdog
    watchdog = WatchdogManager(timeout_sec=60.0)

    restart_count = 0
    while True:
        try:
            run_startup_selfcheck(5555)
            server = PersistentBridgeServer(ea_port=5555, ipc_port=5556)
            server.start()
            logger.info("Bridge Server successfully started on Ports 5555 (EA) and 5556 (IPC).")

            # Health loop with watchdog beat
            while True:
                watchdog.beat()
                time.sleep(2.0)

        except KeyboardInterrupt:
            logger.info("Termination signal received. Shutting down gracefully...")
            try:
                server.stop()
            except Exception:
                pass
            break
        except Exception as e:
            restart_count += 1
            delay = min(30, 5 + restart_count * 2)  # Delay starting at 5s, increasing on repeated errors
            logger.error(f"Bridge server encountered error: {e}. Auto-restarting (#{restart_count}) in {delay}s...")
            try:
                server.stop()
            except Exception:
                pass
            time.sleep(delay)


if __name__ == "__main__":
    main()
