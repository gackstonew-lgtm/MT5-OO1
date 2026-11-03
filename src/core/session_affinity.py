"""
Session x Symbol Affinity Matrix Engine.
Maps institutional session suitability per trading asset,
resolves broker symbol suffixes/aliases, and enforces cash-open windows for indices.
"""

from __future__ import annotations
import json
import os
import re
from datetime import datetime, time, timezone, timedelta
from typing import Dict, Any, Optional, Tuple

from src.core.contracts import SessionType


class SessionAffinityEngine:
    def __init__(self, config_path: str = "config/session_affinity.json"):
        self.config_path = config_path
        self._cfg = self._load_config()

    def _load_config(self) -> Dict[str, Any]:
        if os.path.exists(self.config_path):
            try:
                with open(self.config_path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
        return {
            "mode": "scored",
            "min_weight_floor": 0.20,
            "strict_threshold": 0.80,
            "affinity_matrix": {},
            "symbol_aliases": {}
        }

    def reload_config(self) -> None:
        self._cfg = self._load_config()

    @property
    def mode(self) -> str:
        return self._cfg.get("mode", "scored").lower()

    def set_mode(self, new_mode: str) -> bool:
        if new_mode.lower() in ("strict", "scored"):
            self._cfg["mode"] = new_mode.lower()
            try:
                with open(self.config_path, "w", encoding="utf-8") as f:
                    json.dump(self._cfg, f, indent=2)
            except Exception:
                pass
            return True
        return False

    def resolve_symbol(self, raw_symbol: str) -> str:
        """
        Normalizes broker-specific symbol names (e.g. 'EURUSD.m', 'GOLD', 'USTEC.m')
        to canonical assets.
        """
        aliases = self._cfg.get("symbol_aliases", {})
        if raw_symbol in aliases:
            return aliases[raw_symbol]
        
        # Strip common broker suffixes like .m, m, .pro, _i
        clean = raw_symbol
        if clean.endswith(".m") or clean.endswith("_i"):
            clean = clean[:-2]
        elif clean.endswith("m") and len(clean) == 7:
            clean = clean[:-1]
        
        # Map common names
        if clean.upper() in ("GOLD", "XAUUSD"):
            return "XAUUSD"
        if clean.upper() in ("USTEC", "NAS100", "NDX", "NAS"):
            return "NAS100"
        if clean.upper() in ("DJ30", "US30", "WS30"):
            return "US30"

        return aliases.get(clean, clean)

    def get_affinity_weight(self, session: SessionType, raw_symbol: str) -> float:
        """
        Retrieves affinity weight between 0.0 and 1.0.
        """
        resolved = self.resolve_symbol(raw_symbol)
        matrix = self._cfg.get("affinity_matrix", {})
        
        session_key = session.value if hasattr(session, "value") else str(session)
        session_weights = matrix.get(session_key, {})
        
        # If symbol explicitly listed in this session
        if resolved in session_weights:
            return float(session_weights[resolved])
        
        # Default unlisted weight: 0.5 for active sessions, 0.0 for off-hours
        if session in (SessionType.OFF_HOURS, SessionType.WEEKEND, SessionType.ROLLOVER_BLACKOUT):
            return 0.0
        return 0.50

    def is_symbol_allowed(
        self,
        session: SessionType,
        raw_symbol: str,
        dt_utc: Optional[datetime] = None
    ) -> Tuple[bool, float, Optional[str]]:
        """
        Evaluates whether a symbol is permitted in the active session under current mode.
        Returns: (allowed: bool, weight: float, rejection_reason: Optional[str])
        """
        dt = dt_utc or datetime.now(timezone.utc)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        
        resolved = self.resolve_symbol(raw_symbol)
        weight = self.get_affinity_weight(session, raw_symbol)

        # 1. Cash-Open Window Check for Equity Indices (NAS100, US30)
        cash_open_cfg = self._cfg.get("cash_open_window", {})
        if cash_open_cfg.get("enabled", True):
            co_symbols = cash_open_cfg.get("symbols", ["NAS100", "US30"])
            if resolved in co_symbols:
                co_time_str = cash_open_cfg.get("time_utc", "14:30")
                co_h, co_m = [int(x) for x in co_time_str.split(":")]
                mins_before = cash_open_cfg.get("minutes_before", 30)
                mins_after = cash_open_cfg.get("minutes_after", 30)

                cur_min = dt.hour * 60 + dt.minute
                co_min = co_h * 60 + co_m

                if (co_min - mins_before) <= cur_min <= (co_min + mins_after):
                    return False, weight, f"US Cash Open blackout window ({co_time_str} UTC ±{mins_before}m) active for {resolved}"

        # 2. Strict Mode: Requires weight >= strict_threshold
        if self.mode == "strict":
            strict_thresh = float(self._cfg.get("strict_threshold", 0.80))
            if weight < strict_thresh:
                return False, weight, f"Strict mode: {resolved} affinity weight {weight:.2f} < threshold {strict_thresh:.2f} in {session.value}"
            return True, weight, None

        # 3. Scored Mode: Requires weight >= min_weight_floor
        min_floor = float(self._cfg.get("min_weight_floor", 0.20))
        if weight < min_floor:
            return False, weight, f"Scored mode: {resolved} affinity weight {weight:.2f} < minimum floor {min_floor:.2f} in {session.value}"

        return True, weight, None
