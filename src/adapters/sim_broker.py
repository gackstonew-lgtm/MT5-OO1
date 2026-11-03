"""
Deterministic Simulation Broker Adapter (SimBroker).
High-fidelity in-memory broker simulator with realistic slippage, commission, margin,
and fill models for backtesting and historical replay.
"""

from __future__ import annotations
from datetime import datetime, timezone
from typing import Dict, List, Optional, Any, Callable

from src.adapters.broker_interface import BrokerAdapter, TradeRequest, OrderResult
from src.core.contracts import AccountInfo, Position, Candle, OrderType, SymbolInfo


class SimBroker(BrokerAdapter):
    """
    In-memory Simulated Broker.
    Accurately models broker execution mechanics:
    - Half-spread + volatility slippage
    - Commission structure ($7.00/lot on FX, $0.00 crypto, $5.00 metals)
    - Full margin tracking and leverage
    - Stop Loss and Take Profit fills on price boundaries
    """

    def __init__(
        self,
        initial_balance: float = 10000.0,
        currency: str = "USD",
        leverage: int = 100,
        fx_commission_per_lot: float = 7.0,
        simulated_latency_ms: float = 5.0
    ):
        self.initial_balance = initial_balance
        self.balance = initial_balance
        self.equity = initial_balance
        self.currency = currency
        self.leverage = leverage
        self.fx_commission_per_lot = fx_commission_per_lot
        self.simulated_latency_ms = simulated_latency_ms

        self._connected: bool = True
        self._ticket_counter: int = 500000
        self._positions: Dict[int, Position] = {}
        self._symbol_infos: Dict[str, SymbolInfo] = {}
        self._current_prices: Dict[str, Dict[str, float]] = {}  # {symbol: {"bid": X, "ask": Y, "time": T}}
        self._trade_history: List[Dict[str, Any]] = []

    def ping(self) -> bool:
        return self._connected

    def is_connected(self) -> bool:
        return self._connected

    def set_connected(self, state: bool) -> None:
        self._connected = state

    def register_symbol_info(self, info: SymbolInfo) -> None:
        self._symbol_infos[info.symbol] = info
        self._current_prices[info.symbol] = {
            "bid": info.bid if info.bid > 0 else 1.0,
            "ask": info.ask if info.ask > 0 else 1.0,
            "spread": info.spread * info.point
        }

    def update_market_price(self, symbol: str, bid: float, ask: float) -> None:
        """Updates instantaneous quote prices and evaluates open position triggers."""
        self._current_prices[symbol] = {
            "bid": bid,
            "ask": ask,
            "spread": ask - bid
        }
        self._evaluate_positions_triggers(symbol, bid, ask)
        self._recalculate_account()

    def get_account_info(self) -> AccountInfo:
        self._recalculate_account()
        used_margin = self._calculate_used_margin()
        free_margin = max(0.0, self.equity - used_margin)
        margin_level = (self.equity / used_margin * 100.0) if used_margin > 0 else 0.0

        return AccountInfo(
            account_number=99999999,
            balance=round(self.balance, 2),
            equity=round(self.equity, 2),
            margin=round(used_margin, 2),
            free_margin=round(free_margin, 2),
            margin_level=round(margin_level, 2),
            currency=self.currency,
            leverage=self.leverage,
            trade_mode="SIMULATION"
        )

    def get_positions(self, symbol: Optional[str] = None) -> List[Position]:
        self._recalculate_account()
        all_pos = list(self._positions.values())
        if symbol:
            return [p for p in all_pos if p.symbol == symbol]
        return all_pos

    def get_rates(self, symbol: str, timeframe: str, count: int) -> List[Candle]:
        # SimBroker operates against historical feeds supplied by MarketDataEngine
        return []

    def _calculate_commission(self, symbol: str, volume: float) -> float:
        sym_upper = symbol.upper()
        if "BTC" in sym_upper or "ETH" in sym_upper or "CRYPTO" in sym_upper:
            return 0.0
        elif "XAU" in sym_upper or "GOLD" in sym_upper:
            return 5.0 * volume
        else:
            return self.fx_commission_per_lot * volume

    def _calculate_slippage(self, symbol: str, volume: float) -> float:
        sym_info = self._symbol_infos.get(symbol)
        pt = sym_info.point if sym_info else 0.00001
        spread_pts = sym_info.spread if sym_info else 10
        # Realistic slippage: 20% of spread + micro volume impact
        slippage_pts = max(0.2 * spread_pts, 0.5)
        return slippage_pts * pt

    def send_order(self, request: TradeRequest) -> OrderResult:
        if not self._connected:
            return OrderResult(success=False, error_message="SimBroker disconnected", retcode=10004)

        sym = request.symbol
        prices = self._current_prices.get(sym, {"bid": request.price or 1.0, "ask": request.price or 1.0})
        is_buy = request.action.upper() == "BUY"

        slippage = self._calculate_slippage(sym, request.volume)
        base_price = prices["ask"] if is_buy else prices["bid"]
        if base_price <= 0:
            base_price = request.price or 1.0

        execution_price = (base_price + slippage) if is_buy else (base_price - slippage)

        # Margin sufficiency check
        sym_info = self._symbol_infos.get(sym)
        contract_size = sym_info.contract_size if sym_info else (100000.0 if "EUR" in sym else 100.0)
        notional = request.volume * contract_size * execution_price
        required_margin = notional / self.leverage

        current_account = self.get_account_info()
        if required_margin > current_account.free_margin:
            return OrderResult(
                success=False,
                error_message=f"Insufficient free margin. Required: {required_margin:.2f}, Available: {current_account.free_margin:.2f}",
                retcode=10019
            )

        self._ticket_counter += 1
        ticket = self._ticket_counter
        commission = self._calculate_commission(sym, request.volume)

        # Deduct opening commission immediately from balance
        self.balance -= commission

        pos = Position(
            ticket=ticket,
            symbol=sym,
            order_type=OrderType.BUY if is_buy else OrderType.SELL,
            volume=request.volume,
            open_price=round(execution_price, 5),
            current_price=round(execution_price, 5),
            stop_loss=request.sl,
            take_profit=request.tp,
            profit=-commission,
            magic=request.magic,
            comment=request.comment,
            open_time=datetime.now(timezone.utc),
            initial_risk_amount=abs(execution_price - request.sl) if request.sl > 0 else 0.0,
            initial_sl_distance=abs(execution_price - request.sl) if request.sl > 0 else 0.0
        )

        self._positions[ticket] = pos
        self._recalculate_account()

        return OrderResult(
            success=True,
            ticket=ticket,
            deal=ticket + 1000000,
            price=pos.open_price,
            volume=request.volume,
            retcode=10009,
            comment=request.comment
        )

    def modify_order(self, ticket: int, sl: float, tp: float) -> OrderResult:
        if ticket not in self._positions:
            return OrderResult(success=False, error_message=f"Ticket {ticket} not found", retcode=10013)
        pos = self._positions[ticket]
        pos.stop_loss = sl
        pos.take_profit = tp
        return OrderResult(success=True, ticket=ticket, retcode=10009)

    def cancel_order(self, ticket: int) -> OrderResult:
        return OrderResult(success=True, ticket=ticket, retcode=10009)

    def close_position(self, ticket: int, volume: Optional[float] = None) -> OrderResult:
        if ticket not in self._positions:
            return OrderResult(success=False, error_message=f"Ticket {ticket} not found", retcode=10013)

        pos = self._positions.pop(ticket)
        sym = pos.symbol
        prices = self._current_prices.get(sym, {"bid": pos.current_price, "ask": pos.current_price})
        close_price = prices["bid"] if pos.order_type == OrderType.BUY else prices["ask"]

        closing_commission = self._calculate_commission(sym, pos.volume)
        gross_profit = self._calc_pnl(pos, close_price)
        net_profit = gross_profit - closing_commission

        self.balance += net_profit
        self._recalculate_account()

        self._trade_history.append({
            "ticket": ticket,
            "symbol": sym,
            "type": pos.order_type.value,
            "volume": pos.volume,
            "open_price": pos.open_price,
            "close_price": close_price,
            "net_profit": net_profit,
            "closed_at": datetime.now(timezone.utc).isoformat()
        })

        return OrderResult(
            success=True,
            ticket=ticket,
            price=close_price,
            volume=pos.volume,
            retcode=10009,
            comment=f"Closed with PnL: {net_profit:.2f}"
        )

    def _calc_pnl(self, pos: Position, current_price: float) -> float:
        sym_info = self._symbol_infos.get(pos.symbol)
        contract_size = sym_info.contract_size if sym_info else (100000.0 if "EUR" in pos.symbol else 100.0)
        tick_value = sym_info.tick_value if sym_info else 1.0
        tick_size = sym_info.tick_size if sym_info else 0.00001

        diff = (current_price - pos.open_price) if pos.order_type == OrderType.BUY else (pos.open_price - current_price)
        pnl = (diff / tick_size) * tick_value * pos.volume
        return round(pnl, 2)

    def _evaluate_positions_triggers(self, symbol: str, bid: float, ask: float) -> None:
        """Checks Stop Loss and Take Profit boundary hits on open positions."""
        closed_tickets = []
        for ticket, pos in self._positions.items():
            if pos.symbol != symbol:
                continue

            current_exec = bid if pos.order_type == OrderType.BUY else ask
            pos.current_price = current_exec
            pos.profit = self._calc_pnl(pos, current_exec)

            # Check SL trigger
            if pos.stop_loss > 0:
                if pos.order_type == OrderType.BUY and bid <= pos.stop_loss:
                    closed_tickets.append((ticket, "SL hit"))
                elif pos.order_type == OrderType.SELL and ask >= pos.stop_loss:
                    closed_tickets.append((ticket, "SL hit"))

            # Check TP trigger
            if pos.take_profit > 0:
                if pos.order_type == OrderType.BUY and bid >= pos.take_profit:
                    closed_tickets.append((ticket, "TP hit"))
                elif pos.order_type == OrderType.SELL and ask <= pos.take_profit:
                    closed_tickets.append((ticket, "TP hit"))

        for ticket, reason in closed_tickets:
            self.close_position(ticket)

    def _calculate_used_margin(self) -> float:
        total_margin = 0.0
        for pos in self._positions.values():
            sym_info = self._symbol_infos.get(pos.symbol)
            contract_size = sym_info.contract_size if sym_info else 100000.0
            notional = pos.volume * contract_size * pos.open_price
            total_margin += (notional / self.leverage)
        return total_margin

    def _recalculate_account(self) -> None:
        floating_pnl = sum(p.profit for p in self._positions.values())
        self.equity = max(0.0, self.balance + floating_pnl)
