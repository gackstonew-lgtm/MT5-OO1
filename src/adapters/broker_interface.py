"""
Broker Adapter Base Interface and Contract Definitions.
Provides unified broker abstraction for Live MT5, Paper/Demo, and In-Memory Simulation.
"""

from __future__ import annotations
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional, Any

from src.core.contracts import AccountInfo, Position, Candle, OrderType


@dataclass
class TradeRequest:
    symbol: str
    action: str  # "BUY", "SELL", "MODIFY", "CANCEL", "CLOSE"
    volume: float = 0.0
    price: float = 0.0
    sl: float = 0.0
    tp: float = 0.0
    magic: int = 100101
    comment: str = ""
    ticket: int = 0
    deviation: int = 20


@dataclass
class OrderResult:
    success: bool
    ticket: int = 0
    deal: int = 0
    price: float = 0.0
    volume: float = 0.0
    retcode: int = 0
    comment: str = ""
    error_message: str = ""
    raw_response: Dict[str, Any] = field(default_factory=dict)


class BrokerAdapter(ABC):
    """
    Abstract Base Class for Broker communication.
    Guarantees deterministic, identical API whether executing on live MT5 or simulated replay.
    """

    @abstractmethod
    def ping(self) -> bool:
        """Returns True if the broker communication channel is active and responsive."""
        pass

    @abstractmethod
    def is_connected(self) -> bool:
        """Returns current operational connection state."""
        pass

    @abstractmethod
    def get_account_info(self) -> AccountInfo:
        """Fetches current account balance, equity, leverage and margin."""
        pass

    @abstractmethod
    def get_positions(self, symbol: Optional[str] = None) -> List[Position]:
        """Fetches active open positions, optionally filtered by symbol."""
        pass

    @abstractmethod
    def get_rates(self, symbol: str, timeframe: str, count: int) -> List[Candle]:
        """Fetches historical OHLCV candle bars."""
        pass

    @abstractmethod
    def send_order(self, request: TradeRequest) -> OrderResult:
        """Submits a new market or pending trade order."""
        pass

    @abstractmethod
    def modify_order(self, ticket: int, sl: float, tp: float) -> OrderResult:
        """Modifies Stop Loss and Take Profit levels of an active position or order."""
        pass

    @abstractmethod
    def cancel_order(self, ticket: int) -> OrderResult:
        """Cancels a pending order."""
        pass

    @abstractmethod
    def close_position(self, ticket: int, volume: Optional[float] = None) -> OrderResult:
        """Closes an active position in full or partially."""
        pass
