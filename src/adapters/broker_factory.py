"""
Broker Factory.
Instantiates the appropriate BrokerAdapter based on ExecutionMode configuration.
"""

from __future__ import annotations
from typing import Optional

from src.adapters.broker_interface import BrokerAdapter
from src.adapters.live_mt5_broker import LiveMT5Broker
from src.adapters.sim_broker import SimBroker
from src.core.config import AppConfig
from src.core.contracts import ExecutionMode
from src.execution.bridge_client import BridgeClient


class BrokerFactory:
    """Factory creating concrete BrokerAdapter instances."""

    @staticmethod
    def create_broker(
        config: AppConfig,
        bridge_client: Optional[BridgeClient] = None,
        initial_balance: float = 10000.0
    ) -> BrokerAdapter:
        if config.execution_mode in (ExecutionMode.DEMO, ExecutionMode.REAL):
            client = bridge_client or BridgeClient(
                host=config.bridge_host,
                port=config.bridge_port
            )
            return LiveMT5Broker(bridge_client=client)
        else:
            return SimBroker(
                initial_balance=initial_balance,
                fx_commission_per_lot=7.0,
                simulated_latency_ms=5.0
            )
