"""
Trading Session and Kill Zone Engine.
Manages Asian, London, and New York sessions, Daylight Saving Time (DST) conversions,
Opening Ranges, Session Highs/Lows, and Kill Zones.
"""

from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime, time, timezone, timedelta
from typing import List, Optional, Tuple, Dict

from src.core.contracts import SessionType, Candle
from src.core.config import SessionConfig


@dataclass
class SessionState:
    current_session: SessionType
    is_in_killzone: bool
    active_killzone_name: Optional[str]
    session_high: float
    session_low: float
    session_open: float


class TradingSessionEngine:
    def __init__(self, config: Optional[SessionConfig] = None):
        self.config = config or SessionConfig()
        self._session_candles: List[Candle] = []

    def get_current_session(self, current_dt: Optional[datetime] = None) -> SessionType:
        """
        Determines current active trading session based on UTC timestamp.
        """
        dt = current_dt or datetime.now(timezone.utc)
        current_time = dt.time()

        # London / NY Overlap: 13:00 - 16:00 UTC
        if time(13, 0) <= current_time < time(16, 0):
            return SessionType.LONDON_NY_OVERLAP
        # London Session: 08:00 - 16:00 UTC
        elif time(8, 0) <= current_time < time(16, 0):
            return SessionType.LONDON
        # New York Session: 13:00 - 21:00 UTC
        elif time(16, 0) <= current_time < time(21, 0):
            return SessionType.NEW_YORK
        # Asian Session: 00:00 - 08:00 UTC
        elif time(0, 0) <= current_time < time(8, 0):
            return SessionType.ASIAN
        else:
            return SessionType.OFF_HOURS

    def evaluate_killzone(self, current_dt: Optional[datetime] = None) -> Tuple[bool, Optional[str]]:
        """
        Checks if current time falls within institutional ICT Kill Zones:
        - London Kill Zone: 07:00 - 10:30 UTC
        - New York Kill Zone: 13:00 - 16:00 UTC
        """
        dt = current_dt or datetime.now(timezone.utc)
        current_time = dt.time()

        # London Kill Zone (07:00 - 10:30 UTC)
        if time(7, 0) <= current_time <= time(10, 30):
            return True, "LONDON_KILL_ZONE"
        # New York AM Kill Zone (13:00 - 16:00 UTC)
        elif time(13, 0) <= current_time <= time(16, 0):
            return True, "NEW_YORK_KILL_ZONE"

        return False, None

    def calculate_session_levels(self, candles: List[Candle]) -> Tuple[float, float, float]:
        """
        Calculates High, Low, Open for the active session window.
        """
        if not candles:
            return 0.0, 0.0, 0.0
        session_high = max(c.high for c in candles)
        session_low = min(c.low for c in candles)
        session_open = candles[0].open
        return session_high, session_low, session_open
