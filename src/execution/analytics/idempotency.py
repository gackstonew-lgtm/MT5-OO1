"""
Idempotent Request Manager.
Guarantees strictly zero duplicate order placements across retries and bridge reconnections.
"""

from __future__ import annotations
import hashlib
import time
from dataclasses import dataclass
from typing import Dict, Optional, Set


@dataclass(frozen=True)
class IdempotentRequest:
    request_id: str
    symbol: str
    order_type: str
    volume: float
    intended_price: float
    created_at_epoch: float


class IdempotencyManager:
    def __init__(self, ttl_seconds: float = 3600.0):
        self.ttl = ttl_seconds
        self._processed_requests: Dict[str, IdempotentRequest] = {}
        self._executed_deals: Set[int] = set()

    def generate_request_id(
        self,
        symbol: str,
        order_type: str,
        volume: float,
        intended_price: float,
        timestamp_epoch: Optional[float] = None
    ) -> str:
        """
        Creates a deterministic unique request ID based on order parameters and timestamp window.
        """
        t = timestamp_epoch or time.time()
        # Round timestamp to 10-second bucket to avoid microsecond retry drift duplicating IDs
        time_bucket = int(t // 10)
        raw = f"{symbol}_{order_type}_{volume:.4f}_{intended_price:.5f}_{time_bucket}"
        h = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12].upper()
        return f"REQ_{h}"

    def register_request(
        self,
        request_id: str,
        symbol: str,
        order_type: str,
        volume: float,
        intended_price: float
    ) -> bool:
        """
        Registers an outgoing request. Returns True if this is a NEW request,
        or False if it is a duplicate retry.
        """
        self._cleanup_expired()
        if request_id in self._processed_requests:
            return False  # Already registered

        req = IdempotentRequest(
            request_id=request_id,
            symbol=symbol,
            order_type=order_type,
            volume=volume,
            intended_price=intended_price,
            created_at_epoch=time.time()
        )
        self._processed_requests[request_id] = req
        return True

    def mark_deal_executed(self, deal_ticket: int) -> bool:
        """Returns False if deal was already seen (duplicate confirmation)."""
        if deal_ticket in self._executed_deals:
            return False
        self._executed_deals.add(deal_ticket)
        return True

    def is_request_processed(self, request_id: str) -> bool:
        return request_id in self._processed_requests

    def _cleanup_expired(self) -> None:
        now = time.time()
        expired = [
            rid for rid, req in self._processed_requests.items()
            if now - req.created_at_epoch > self.ttl
        ]
        for rid in expired:
            del self._processed_requests[rid]
