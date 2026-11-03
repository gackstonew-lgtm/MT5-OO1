"""
Unit Tests for Broker Adapter Layer (Stage 1).
Tests LiveMT5Broker, SimBroker mechanics (slippage, commissions, margin, SL/TP), and BrokerFactory.
"""

import unittest
from unittest.mock import MagicMock
from datetime import datetime, timezone

from src.adapters.broker_interface import TradeRequest, OrderResult
from src.adapters.live_mt5_broker import LiveMT5Broker
from src.adapters.sim_broker import SimBroker
from src.adapters.broker_factory import BrokerFactory
from src.core.contracts import ExecutionMode, AccountInfo, Position, OrderType, SymbolInfo
from src.core.config import AppConfig


class TestBrokerAdapters(unittest.TestCase):
    def setUp(self):
        self.sim_broker = SimBroker(
            initial_balance=10000.0,
            currency="USD",
            leverage=100,
            fx_commission_per_lot=7.0
        )
        self.eurusd_info = SymbolInfo(
            symbol="EURUSD.m",
            digits=5,
            point=0.00001,
            tick_size=0.00001,
            tick_value=1.0,
            contract_size=100000.0,
            min_lot=0.01,
            max_lot=50.0,
            lot_step=0.01,
            spread=10,
            ask=1.10010,
            bid=1.10000
        )
        self.sim_broker.register_symbol_info(self.eurusd_info)

    def test_sim_broker_initial_state(self):
        self.assertTrue(self.sim_broker.ping())
        self.assertTrue(self.sim_broker.is_connected())
        acc = self.sim_broker.get_account_info()
        self.assertEqual(acc.balance, 10000.0)
        self.assertEqual(acc.equity, 10000.0)
        self.assertEqual(acc.margin, 0.0)
        self.assertEqual(acc.free_margin, 10000.0)

    def test_sim_broker_buy_execution_with_slippage_and_commission(self):
        req = TradeRequest(
            symbol="EURUSD.m",
            action="BUY",
            volume=0.10,
            price=1.10010,
            sl=1.09500,
            tp=1.11000,
            magic=100101,
            comment="TEST_BUY"
        )
        res = self.sim_broker.send_order(req)
        self.assertTrue(res.success)
        self.assertGreater(res.ticket, 0)
        # Price should be ask + slippage
        self.assertGreaterEqual(res.price, 1.10010)

        # Commission on 0.10 lot @ $7/lot is $0.70
        acc = self.sim_broker.get_account_info()
        self.assertAlmostEqual(acc.balance, 10000.0 - 0.70, places=2)

        positions = self.sim_broker.get_positions()
        self.assertEqual(len(positions), 1)
        pos = positions[0]
        self.assertEqual(pos.ticket, res.ticket)
        self.assertEqual(pos.order_type, OrderType.BUY)
        self.assertEqual(pos.volume, 0.10)

    def test_sim_broker_margin_rejection(self):
        # Huge order exceeding margin
        req = TradeRequest(
            symbol="EURUSD.m",
            action="BUY",
            volume=50.0,  # Notional = 50 * 100,000 * 1.10 = $5.5M -> margin required = $55,000 > $10,000
            price=1.10010,
            sl=1.09500,
            tp=1.11000
        )
        res = self.sim_broker.send_order(req)
        self.assertFalse(res.success)
        self.assertEqual(res.retcode, 10019)
        self.assertIn("Insufficient free margin", res.error_message)

    def test_sim_broker_stop_loss_trigger(self):
        req = TradeRequest(
            symbol="EURUSD.m",
            action="BUY",
            volume=0.10,
            price=1.10000,
            sl=1.09500,
            tp=1.11000
        )
        res = self.sim_broker.send_order(req)
        self.assertTrue(res.success)

        # Price drops below SL
        self.sim_broker.update_market_price("EURUSD.m", bid=1.09450, ask=1.09460)

        # Position should be auto-closed
        positions = self.sim_broker.get_positions()
        self.assertEqual(len(positions), 0)
        self.assertEqual(len(self.sim_broker._trade_history), 1)
        self.assertLess(self.sim_broker._trade_history[0]["net_profit"], 0)

    def test_sim_broker_close_position(self):
        req = TradeRequest(
            symbol="EURUSD.m",
            action="SELL",
            volume=0.10,
            price=1.10000,
            sl=1.10500,
            tp=1.09000
        )
        res = self.sim_broker.send_order(req)
        ticket = res.ticket

        # Price moves in favor of sell (bid=1.0950, ask=1.0951)
        self.sim_broker.update_market_price("EURUSD.m", bid=1.09500, ask=1.09510)
        close_res = self.sim_broker.close_position(ticket)
        self.assertTrue(close_res.success)
        self.assertEqual(len(self.sim_broker.get_positions()), 0)
        self.assertGreater(self.sim_broker.get_account_info().balance, 10000.0)

    def test_live_mt5_broker_delegation(self):
        mock_bridge = MagicMock()
        mock_bridge.ping.return_value = True
        mock_bridge.is_connected.return_value = True
        mock_bridge.get_account_info.return_value = AccountInfo(balance=12345.0)
        mock_bridge.get_positions.return_value = [
            Position(ticket=101, symbol="EURUSD", order_type=OrderType.BUY, volume=0.5, open_price=1.08, current_price=1.085, stop_loss=1.075, take_profit=1.095, profit=25.0)
        ]
        mock_bridge.execute_trade.return_value = {"status": "SUCCESS", "ticket": 777, "deal": 888, "price": 1.085}

        live_broker = LiveMT5Broker(mock_bridge)
        self.assertTrue(live_broker.ping())
        self.assertTrue(live_broker.is_connected())

        acc = live_broker.get_account_info()
        self.assertEqual(acc.balance, 12345.0)

        positions = live_broker.get_positions("EURUSD")
        self.assertEqual(len(positions), 1)
        self.assertEqual(positions[0].ticket, 101)

        req = TradeRequest(symbol="EURUSD", action="BUY", volume=0.5, sl=1.08, tp=1.09)
        res = live_broker.send_order(req)
        self.assertTrue(res.success)
        self.assertEqual(res.ticket, 777)
        mock_bridge.execute_trade.assert_called_once()

    def test_broker_factory(self):
        cfg_sim = AppConfig(execution_mode=ExecutionMode.SIMULATION)
        broker_sim = BrokerFactory.create_broker(cfg_sim)
        self.assertIsInstance(broker_sim, SimBroker)

        cfg_live = AppConfig(execution_mode=ExecutionMode.DEMO)
        mock_bridge = MagicMock()
        broker_live = BrokerFactory.create_broker(cfg_live, bridge_client=mock_bridge)
        self.assertIsInstance(broker_live, LiveMT5Broker)


if __name__ == "__main__":
    unittest.main()
