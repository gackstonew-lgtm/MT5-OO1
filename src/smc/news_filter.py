"""
Economic News Calendar and High-Impact Blackout Window Engine.
Prevents executing orders within a configurable blackout window (e.g. 15m before/after)
around high-impact macroeconomic events (NFP, CPI, FOMC, Rate decisions).
"""

from __future__ import annotations
import logging
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Optional, Tuple

logger = logging.getLogger("NewsFilter")


@dataclass
class EconomicEvent:
    event_id: str
    currency: str
    title: str
    event_time: datetime
    impact: str = "HIGH"  # HIGH, MEDIUM, LOW


class NewsFilterEngine:
    def __init__(
        self,
        blackout_minutes_before: int = 15,
        blackout_minutes_after: int = 15,
        enabled: bool = True
    ):
        self.blackout_minutes_before = blackout_minutes_before
        self.blackout_minutes_after = blackout_minutes_after
        self.enabled = enabled
        self._events: List[EconomicEvent] = []

    def set_events(self, events: List[EconomicEvent]) -> None:
        self._events = list(events)

    def add_event(self, event: EconomicEvent) -> None:
        self._events.append(event)

    def is_in_blackout(
        self,
        symbol: str,
        current_time: Optional[datetime] = None
    ) -> Tuple[bool, Optional[str]]:
        """
        Evaluates whether current_time falls inside a high-impact blackout window
        for any currency involved in the symbol.
        """
        if not self.enabled:
            return False, None

        t = current_time or datetime.now(timezone.utc)
        sym_upper = symbol.upper()

        for ev in self._events:
            if ev.impact != "HIGH":
                continue

            # Check if event currency matches symbol components
            # e.g. "USD" in "EURUSD", "XAUUSD", "BTCUSD"
            if ev.currency.upper() in sym_upper:
                start_window = ev.event_time - timedelta(minutes=self.blackout_minutes_before)
                end_window = ev.event_time + timedelta(minutes=self.blackout_minutes_after)

                if start_window <= t <= end_window:
                    mins_diff = (ev.event_time - t).total_seconds() / 60.0
                    timing_str = f"in {mins_diff:.0f}m" if mins_diff > 0 else f"{abs(mins_diff):.0f}m ago"
                    reason = (
                        f"High-impact news blackout: {ev.currency} {ev.title} "
                        f"scheduled at {ev.event_time.strftime('%H:%M UTC')} ({timing_str}). "
                        f"Blackout active [{self.blackout_minutes_before}m before to {self.blackout_minutes_after}m after]."
                    )
                    logger.info(f"[DECISION] [NEWS_BLACKOUT] {symbol} | {reason}")
                    return True, reason

        return False, None
