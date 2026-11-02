"""
Standalone Persistent Bridge Server Runner.
Runs PersistentBridgeServer with automatic recovery and restart on unexpected errors.
"""

import sys
import os
import time
import logging

# Ensure project root is in sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.execution.bridge_server import PersistentBridgeServer

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s.%(msecs)03d] [BridgeRunner] [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger("BridgeRunner")


def main():
    logger.info("Initializing Persistent Bridge Server Daemon...")
    restart_count = 0

    while True:
        try:
            server = PersistentBridgeServer(ea_port=5555, ipc_port=5556)
            server.start()
            logger.info("Bridge Server successfully started on Ports 5555 (EA) and 5556 (IPC).")

            # Health loop
            while True:
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
            logger.error(f"Bridge server encountered unexpected exception: {e}. Auto-restarting (#{restart_count}) in 2s...")
            try:
                server.stop()
            except Exception:
                pass
            time.sleep(2.0)


if __name__ == "__main__":
    main()
