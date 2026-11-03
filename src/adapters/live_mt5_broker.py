"""
Live MT5 Broker Adapter.
Wraps the low-level BridgeClient to communicate with PythonBridgeEA.mq5 via TCP socket.
"""

from __future__ import annotations
from datetime import datetime, timezone
from typing import Dict, List, Optional, Any

from src.adapters.broker_interface import BrokerAdapter, TradeRequest, OrderResult
from src.core.contracts import AccountInfo, Position, Candle, OrderType
from src.execution.bridge_client import BridgeClient


class LiveMT5Broker(BrokerAdapter):
    """
    MT5 Bridge implementation of BrokerAdapter.
    Preserves exact socket protocol and wire formats established in PythonBridgeEA.mq5.
    """

    def __init__(self, bridge_client: BridgeClient):
        self.bridge = bridge_client

    def ping(self) -> bool:
        return self.bridge.ping()

    def is_connected(self) -> bool:
        return self.bridge.is_connected()

    def get_account_info(self) -> AccountInfo:
        return self.bridge.get_account_info()

    def get_positions(self, symbol: Optional[str] = None) -> List[Position]:
        positions = self.bridge.get_positions()
        if symbol:
            return [p for p in positions if p.symbol == symbol]
        return positions

    def get_rates(self, symbol: str, timeframe: str, count: int) -> List[Candle]:
        return self.bridge.get_rates(symbol=symbol, timeframe=timeframe, count=count)

    def send_order(self, request: TradeRequest) -> OrderResult:
        ot = OrderType.BUY if request.action.upper() == "BUY" else OrderType.SELL
        res = self.bridge.execute_trade(
            symbol=request.symbol,
            order_type=ot,
            volume=request.volume,
            stop_loss=request.sl,
            take_profit=request.tp,
            price=request.price,
            magic=request.magic,
            comment=request.comment
        )

        success = res.get("status") == "SUCCESS"
        return OrderResult(
            success=success,
            ticket=int(res.get("ticket", 0)),
            deal=int(res.get("deal", 0)),
            price=float(res.get("price", request.price)),
            volume=request.volume,
            retcode=int(res.get("retcode", 10009 if success else 10004)),
            comment=request.comment,
            error_message="" if success else str(res.get("message", "Unknown MT5 execution error")),
            raw_response=res
        )

    def modify_order(self, ticket: int, sl: float, tp: float) -> OrderResult:
        res = self.bridge.modify_order(ticket=ticket, sl=sl, tp=tp)
        success = res.get("status") == "SUCCESS"
        return OrderResult(
            success=success,
            ticket=ticket,
            error_message="" if success else str(res.get("message", "Modify failed")),
            raw_response=res
        )

    def cancel_order(self, ticket: int) -> OrderResult:
        res = self.bridge.cancel_order(ticket=ticket)
        success = res.get("status") == "SUCCESS"
        return OrderResult(
            success=success,
            ticket=ticket,
            error_message="" if success else str(res.get("message", "Cancel failed")),
            raw_response=res
        )

    def close_position(self, ticket: int, volume: Optional[float] = None) -> OrderResult:
        res = self.bridge.close_position(ticket=ticket, volume=volume)
        success = res.get("status") == "SUCCESS"
        return OrderResult(
            success=success,
            ticket=ticket,
            error_message="" if success else str(res.get("message", "Close failed")),
            raw_response=res
        )
