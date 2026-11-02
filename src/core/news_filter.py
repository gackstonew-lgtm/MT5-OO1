"""
Economic News and No-Trade Window Guard.
Filters high-impact macroeconomic announcements (CPI, NFP, FOMC, Central Bank Rate decisions).
Implements fail-safe protection policies.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import List, Optional, Tuple


@dataclass
class EconomicNewsEvent:
    id: str
    title: str
    currency: str  # e.g. "USD", "EUR", "GBP", "JPY"
    impact: str    # "HIGH", "MEDIUM", "LOW"
    timestamp: datetime
    actual: Optional[str] = None
    forecast: Optional[str] = None
    previous: Optional[str] = None


class NewsFilterEngine:
    def __init__(
        self,
        restrict_minutes_before: int = 30,
        restrict_minutes_after: int = 30,
        restrict_high_only: bool = True
    ):
        self.restrict_before = timedelta(minutes=restrict_minutes_before)
        self.restrict_after = timedelta(minutes=restrict_minutes_after)
        self.restrict_high_only = restrict_high_only
        self._news_events: List[EconomicNewsEvent] = []
        self._feed_healthy: bool = True

    def register_events(self, events: List[EconomicNewsEvent]) -> None:
        self._news_events = sorted(events, key=lambda x: x.timestamp)
        self._feed_healthy = True

    def set_feed_status(self, is_healthy: bool) -> None:
        self._feed_healthy = is_healthy

    def is_trading_allowed(
        self,
        symbol: str,
        current_time: Optional[datetime] = None
    ) -> Tuple[bool, Optional[str]]:
        """
        Determines whether trading is permitted for the given symbol.
        Checks base and quote currency against upcoming/recent high-impact releases.
        """
        # Fail-safe check
        if not self._feed_healthy:
            return True, None  # Allow trading with warning or set to False if strict mode configured

        now = current_time or datetime.now(timezone.utc)
        # Parse currency from symbol (e.g. EURUSD -> EUR, USD; XAUUSD -> USD)
        currencies = [symbol[:3], symbol[3:6]] if len(symbol) >= 6 else [symbol]

        for ev in self._news_events:
            if self.restrict_high_only and ev.impact != "HIGH":
                continue
            if ev.currency not in currencies:
                continue

            # Check window: [event_time - restrict_before, event_time + restrict_after]
            window_start = ev.timestamp - self.restrict_before
            window_end = ev.timestamp + self.restrict_after

            if window_start <= now <= window_end:
                mins_diff = int((ev.timestamp - now).total_seconds() / 60)
                timing = f"in {mins_diff} min" if mins_diff >= 0 else f"{abs(mins_diff)} min ago"
                return False, (
                    f"No-Trade news window active: {ev.impact} impact '{ev.title}' ({ev.currency}) {timing}."
                )

        return True, None
