"""
Legacy SMC NewsFilterEngine Compatibility Stub.
Economic-calendar dependencies and high-impact news blackout windows have been
completely removed from the trading decision pipeline.
"""

from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional, Tuple


@dataclass
class EconomicEvent:
    event_id: str
    currency: str
    title: str
    event_time: datetime
    impact: str = "HIGH"


class NewsFilterEngine:
    """
    No-op compatibility stub: news blackout windows are permanently disabled.
    Always returns (False, None) for is_in_blackout().
    """
    def __init__(
        self,
        blackout_minutes_before: int = 0,
        blackout_minutes_after: int = 0,
        enabled: bool = False
    ):
        self.blackout_minutes_before = 0
        self.blackout_minutes_after = 0
        self.enabled = False
        self._events: List[EconomicEvent] = []

    def set_events(self, events: List[EconomicEvent]) -> None:
        self._events = []

    def add_event(self, event: EconomicEvent) -> None:
        pass

    def is_in_blackout(
        self,
        symbol: str,
        current_time: Optional[datetime] = None
    ) -> Tuple[bool, Optional[str]]:
        """Always returns (False, None) — news blackouts are completely removed."""
        return False, None
