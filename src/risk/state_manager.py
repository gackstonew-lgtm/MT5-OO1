"""
Persistent State Manager.
Handles atomic persistence of bot state (baseline balance, peak equity, day-start balances,
and counters) with crash-resilient write-to-temporary file replacement.
"""

from __future__ import annotations
import json
import os
import tempfile
import logging
from datetime import datetime, timezone
from typing import Dict, Any, Optional

logger = logging.getLogger("StateManager")


class StateManager:
    def __init__(self, state_file: str = "data/bot_state.json"):
        self.state_file = os.path.abspath(state_file)
        os.makedirs(os.path.dirname(self.state_file), exist_ok=True)

    def load_state(self) -> Dict[str, Any]:
        """Loads state dictionary safely. Returns empty dict if missing or invalid."""
        if not os.path.exists(self.state_file):
            return {}
        try:
            with open(self.state_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data if isinstance(data, dict) else {}
        except Exception as e:
            logger.warning(f"Failed to read state from {self.state_file}: {e}. Initializing fresh state.")
            return {}

    def save_state(self, state: Dict[str, Any]) -> bool:
        """Atomically persists state using temporary file replacement."""
        dir_name = os.path.dirname(self.state_file)
        try:
            with tempfile.NamedTemporaryFile("w", dir=dir_name, delete=False, encoding="utf-8") as tf:
                temp_name = tf.name
                json.dump(state, tf, indent=2)
                tf.flush()
                os.fsync(tf.fileno())

            # Atomic replace
            os.replace(temp_name, self.state_file)
            return True
        except Exception as e:
            logger.error(f"Failed to atomically persist state: {e}")
            if 'temp_name' in locals() and os.path.exists(temp_name):
                try:
                    os.remove(temp_name)
                except Exception:
                    pass
            return False
