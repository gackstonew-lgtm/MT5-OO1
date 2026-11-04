"""
Legacy NewsFilterEngine Compatibility Stub.
Economic-calendar and news-based trading restrictions have been completely removed
from the trading decision pipeline per the Continuous Scalping Engine architecture.
Ordinary execution protections (spread, quote validity, slippage) remain in OrderManager.
"""

from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import List, Optional, Tuple


@dataclass
class EconomicNewsEvent:
    id: str
    title: str
    currency: str
    impact: str
    timestamp: datetime
    actual: Optional[str] = None
    forecast: Optional[str] = None
    previous: Optional[str] = None


class NewsFilterEngine:
    """
    No-op compatibility stub: news-based trade blocking is permanently disabled.
    Always permits trading so the continuous scalping engine never waits for or blocks on news.
    """
    def __init__(
        self,
        restrict_minutes_before: int = 0,
        restrict_minutes_after: int = 0,
        restrict_high_only: bool = False,
        enabled: bool = False,
    ):
        self.enabled = False
        self.restrict_before = timedelta(minutes=0)
        self.restrict_after = timedelta(minutes=0)
        self.restrict_high_only = False
        self._news_events: List[EconomicNewsEvent] = []
        self._feed_healthy: bool = True

    def register_events(self, events: List[EconomicNewsEvent]) -> None:
        self._news_events = []

    def clear_events(self) -> None:
        self._news_events = []

    def set_feed_status(self, is_healthy: bool) -> None:
        self._feed_healthy = True

    def is_trading_allowed(
        self,
        symbol: str,
        current_time: Optional[datetime] = None
    ) -> Tuple[bool, Optional[str]]:
        """Always returns (True, None) — news trading restrictions are completely removed."""
        return True, None
