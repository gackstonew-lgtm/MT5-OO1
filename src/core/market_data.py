"""
Market Data Engine.
Handles historical and live candle buffers, MT5 rates acquisition,
data validation, and integrity checks across all monitored timeframes.
"""

from __future__ import annotations
from datetime import datetime, timezone
from typing import Dict, List, Optional
import numpy as np

from src.core.contracts import Candle, Timeframe, SymbolInfo


class MarketDataEngine:
    def __init__(self, symbols: Optional[List[str]] = None):
        self.symbols = symbols or ["EURUSD", "GBPUSD", "USDJPY", "XAUUSD"]
        # Buffers: symbol -> timeframe -> list of completed Candle objects
        self._buffers: Dict[str, Dict[Timeframe, List[Candle]]] = {
            s: {tf: [] for tf in Timeframe} for s in self.symbols
        }
        self._symbol_info: Dict[str, SymbolInfo] = {}

    def register_symbol_info(self, info: SymbolInfo) -> None:
        self._symbol_info[info.symbol] = info

    def get_symbol_info(self, symbol: str) -> SymbolInfo:
        if symbol not in self._symbol_info:
            # Fallback default EURUSD-like specifications
            return SymbolInfo(symbol=symbol)
        return self._symbol_info[symbol]

    def set_candles(self, symbol: str, timeframe: Timeframe, candles: List[Candle]) -> None:
        """Sets and validates historical candles for a specific symbol & timeframe."""
        if symbol not in self._buffers:
            self._buffers[symbol] = {tf: [] for tf in Timeframe}
        validated = self._validate_and_sort_candles(candles)
        self._buffers[symbol][timeframe] = validated

    def append_candle(self, symbol: str, timeframe: Timeframe, candle: Candle) -> None:
        """Appends a new completed candle, replacing the last if same timestamp."""
        if symbol not in self._buffers:
            self._buffers[symbol] = {tf: [] for tf in Timeframe}
        buf = self._buffers[symbol][timeframe]
        if buf and buf[-1].time == candle.time:
            buf[-1] = candle
        else:
            buf.append(candle)

    def get_candles(self, symbol: str, timeframe: Timeframe, count: Optional[int] = None) -> List[Candle]:
        """Retrieves the most recent candles (completed only)."""
        buf = self._buffers.get(symbol, {}).get(timeframe, [])
        if count is None or count >= len(buf):
            return list(buf)
        return list(buf[-count:])

    def get_latest_candle(self, symbol: str, timeframe: Timeframe) -> Optional[Candle]:
        buf = self._buffers.get(symbol, {}).get(timeframe, [])
        return buf[-1] if buf else None

    def _validate_and_sort_candles(self, candles: List[Candle]) -> List[Candle]:
        """Ensures candle integrity: time order, high >= low, open/close inside high/low."""
        valid: List[Candle] = []
        for c in candles:
            if c.high < c.low or c.open < 0 or c.close < 0:
                continue
            if c.open > c.high or c.open < c.low or c.close > c.high or c.close < c.low:
                continue
            valid.append(c)
        valid.sort(key=lambda x: x.time)
        return valid

    def load_from_mt5(self, mt5_connector, count: int = 300) -> bool:
        """Loads historical rates from live MT5 instance for all symbols and timeframes."""
        try:
            import MetaTrader5 as mt5
            tf_map = {
                Timeframe.W1: mt5.TIMEFRAME_W1,
                Timeframe.D1: mt5.TIMEFRAME_D1,
                Timeframe.H4: mt5.TIMEFRAME_H4,
                Timeframe.H1: mt5.TIMEFRAME_H1,
                Timeframe.M15: mt5.TIMEFRAME_M15,
                Timeframe.M5: mt5.TIMEFRAME_M5,
                Timeframe.M1: mt5.TIMEFRAME_M1,
            }
            for sym in self.symbols:
                # Update symbol info
                info = mt5.symbol_info(sym)
                if info is not None:
                    self.register_symbol_info(
                        SymbolInfo(
                            symbol=sym,
                            digits=info.digits,
                            point=info.point,
                            tick_size=info.trade_tick_size,
                            tick_value=info.trade_tick_value,
                            contract_size=info.trade_contract_size,
                            min_lot=info.volume_min,
                            max_lot=info.volume_max,
                            lot_step=info.volume_step,
                            spread=float(info.spread),
                            ask=info.ask,
                            bid=info.bid
                        )
                    )
                for tf, mt5_tf in tf_map.items():
                    rates = mt5.copy_rates_from_pos(sym, mt5_tf, 0, count)
                    if rates is not None and len(rates) > 0:
                        candle_list = []
                        for r in rates:
                            dt = datetime.fromtimestamp(r['time'], tz=timezone.utc)
                            candle_list.append(
                                Candle(
                                    time=dt,
                                    open=float(r['open']),
                                    high=float(r['high']),
                                    low=float(r['low']),
                                    close=float(r['close']),
                                    tick_volume=int(r['tick_volume']),
                                    spread=int(r['spread']),
                                    complete=True
                                )
                            )
                        self.set_candles(sym, tf, candle_list)
            return True
        except Exception as e:
            print(f"[MarketDataEngine] Failed to load from MT5: {e}")
            return False
