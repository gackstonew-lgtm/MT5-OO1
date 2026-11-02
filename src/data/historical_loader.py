"""
Historical Data Loader for MT5.
Fetches multi-timeframe candles and ticks, converts broker server time to true UTC,
and caches datasets locally in data/historical/ (gitignored).
"""

from __future__ import annotations
import os
import json
import logging
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional, Tuple, Any
import numpy as np

try:
    import MetaTrader5 as mt5
    MT5_AVAILABLE = True
except ImportError:
    mt5 = None
    MT5_AVAILABLE = False

from src.core.contracts import Timeframe, Candle

logger = logging.getLogger("HistoricalDataLoader")

# Map contract Timeframe enum to MT5 constants
TIMEFRAME_MT5_MAP = {
    Timeframe.W1: 0x8001 if not MT5_AVAILABLE else mt5.TIMEFRAME_W1,
    Timeframe.D1: 0x4001 if not MT5_AVAILABLE else mt5.TIMEFRAME_D1,
    Timeframe.H4: 0x4004 if not MT5_AVAILABLE else mt5.TIMEFRAME_H4,
    Timeframe.H1: 0x4001 if not MT5_AVAILABLE else mt5.TIMEFRAME_H1,
    Timeframe.M15: 0x000F if not MT5_AVAILABLE else mt5.TIMEFRAME_M15,
    Timeframe.M5: 0x0005 if not MT5_AVAILABLE else mt5.TIMEFRAME_M5,
    Timeframe.M1: 0x0001 if not MT5_AVAILABLE else mt5.TIMEFRAME_M1,
}

CACHE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "data", "historical"))


class HistoricalDataLoader:
    def __init__(self, cache_dir: Optional[str] = None):
        self.cache_dir = cache_dir or CACHE_DIR
        os.makedirs(self.cache_dir, exist_ok=True)
        self._broker_utc_offset_seconds: Optional[int] = None

    def initialize_mt5(self) -> bool:
        if not MT5_AVAILABLE:
            logger.warning("MetaTrader5 python module is not available.")
            return False
        if not mt5.initialize():
            logger.error(f"MT5 initialization failed, error: {mt5.last_error()}")
            return False
        return True

    def shutdown_mt5(self) -> None:
        if MT5_AVAILABLE:
            try:
                mt5.shutdown()
            except Exception:
                pass

    def resolve_broker_symbol(self, requested_symbol: str) -> Optional[str]:
        """Resolves symbol against broker's available symbols (e.g. handles .m, .raw suffixes)."""
        if not MT5_AVAILABLE:
            return requested_symbol
        if mt5.symbol_info(requested_symbol) is not None:
            return requested_symbol
        
        candidates = [
            f"{requested_symbol}.m",
            requested_symbol.replace(".m", ""),
            f"{requested_symbol}.raw",
            f"{requested_symbol}.pro",
            f"{requested_symbol}m",
        ]
        for c in candidates:
            if mt5.symbol_info(c) is not None:
                return c

        # Broad search among all symbols
        all_syms = mt5.symbols_get()
        if all_syms:
            clean_req = requested_symbol.replace(".m", "").replace(".", "").upper()
            for s in all_syms:
                if clean_req in s.name.upper():
                    return s.name
        return None

    def detect_broker_utc_offset(self, symbol: str = "EURUSD.m") -> int:
        """
        Detects broker UTC offset in seconds.
        Compares last known broker tick/candle time with system UTC time.
        """
        if self._broker_utc_offset_seconds is not None:
            return self._broker_utc_offset_seconds

        default_offset = 7200  # Default broker UTC+2

        if not MT5_AVAILABLE:
            self._broker_utc_offset_seconds = default_offset
            return default_offset

        resolved = self.resolve_broker_symbol(symbol) or symbol
        tick = mt5.symbol_info_tick(resolved)
        if tick is not None and tick.time > 0:
            now_utc = datetime.now(timezone.utc).timestamp()
            raw_diff = tick.time - now_utc
            # Round to nearest hour (3600s)
            hours = round(raw_diff / 3600.0)
            # Normal broker offsets range between -12 and +14 hours
            if -12 <= hours <= 14:
                self._broker_utc_offset_seconds = int(hours * 3600)
                logger.info(f"Detected Broker UTC Offset: {self._broker_utc_offset_seconds}s ({hours:+.1f}h)")
                return self._broker_utc_offset_seconds

        # Fallback to rates comparison
        rates = mt5.copy_rates_from_pos(resolved, mt5.TIMEFRAME_M15, 0, 1)
        if rates is not None and len(rates) > 0:
            last_bar_time = rates[0]['time']
            now_utc = datetime.now(timezone.utc).timestamp()
            raw_diff = last_bar_time - now_utc
            hours = round(raw_diff / 3600.0)
            if -12 <= hours <= 14:
                self._broker_utc_offset_seconds = int(hours * 3600)
                return self._broker_utc_offset_seconds

        self._broker_utc_offset_seconds = default_offset
        return default_offset

    def load_candles(
        self,
        symbol: str,
        timeframe: Timeframe,
        count: int = 1000,
        use_cache: bool = True,
        force_fresh: bool = False
    ) -> List[Candle]:
        """
        Loads candles for symbol and timeframe, converting timestamps to UTC.
        Caches results locally in cache_dir.
        """
        cache_path = os.path.join(self.cache_dir, f"{symbol}_{timeframe.value}.json")

        if use_cache and not force_fresh and os.path.exists(cache_path):
            try:
                with open(cache_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                candles = [
                    Candle(
                        time=datetime.fromisoformat(row["time"]),
                        open=row["open"],
                        high=row["high"],
                        low=row["low"],
                        close=row["close"],
                        tick_volume=row.get("tick_volume", 0),
                        spread=row.get("spread", 0),
                        complete=row.get("complete", True),
                    )
                    for row in data
                ]
                if len(candles) >= count:
                    return candles[-count:]
            except Exception as e:
                logger.warning(f"Failed reading cache for {symbol} {timeframe}: {e}")

        # Fetch live from MT5
        if not MT5_AVAILABLE:
            return []

        resolved = self.resolve_broker_symbol(symbol)
        if not resolved:
            logger.error(f"Cannot resolve broker symbol for '{symbol}'")
            return []

        mt5_tf = TIMEFRAME_MT5_MAP.get(timeframe)
        if mt5_tf is None:
            logger.error(f"Unsupported timeframe: {timeframe}")
            return []

        rates = mt5.copy_rates_from_pos(resolved, mt5_tf, 0, count)
        if rates is None or len(rates) == 0:
            logger.warning(f"No rates returned from MT5 for {resolved} {timeframe}")
            return []

        utc_offset = self.detect_broker_utc_offset(resolved)
        candles: List[Candle] = []

        for r in rates:
            # Server time -> true UTC time
            utc_ts = int(r['time']) - utc_offset
            dt_utc = datetime.fromtimestamp(utc_ts, tz=timezone.utc)
            candles.append(
                Candle(
                    time=dt_utc,
                    open=float(r['open']),
                    high=float(r['high']),
                    low=float(r['low']),
                    close=float(r['close']),
                    tick_volume=int(r['tick_volume']),
                    spread=int(r['spread']),
                    complete=True
                )
            )

        # Cache to disk
        self._save_cache(cache_path, candles)
        return candles

    def load_ticks(self, symbol: str, count: int = 10000) -> List[Dict[str, Any]]:
        """Loads historical ticks from MT5 converted to UTC."""
        if not MT5_AVAILABLE:
            return []
        resolved = self.resolve_broker_symbol(symbol) or symbol
        ticks = mt5.copy_ticks_from(resolved, datetime.now() - timedelta(days=2), count, mt5.COPY_TICKS_ALL)
        if ticks is None or len(ticks) == 0:
            return []

        utc_offset = self.detect_broker_utc_offset(resolved)
        tick_list = []
        for t in ticks:
            utc_ts = float(t['time']) - utc_offset
            tick_list.append({
                "time_utc": datetime.fromtimestamp(utc_ts, tz=timezone.utc).isoformat(),
                "bid": float(t['bid']),
                "ask": float(t['ask']),
                "last": float(t['last']),
                "volume": float(t['volume']),
                "flags": int(t['flags']),
            })
        return tick_list

    def _save_cache(self, path: str, candles: List[Candle]) -> None:
        try:
            data = [
                {
                    "time": c.time.isoformat(),
                    "open": c.open,
                    "high": c.high,
                    "low": c.low,
                    "close": c.close,
                    "tick_volume": c.tick_volume,
                    "spread": c.spread,
                    "complete": c.complete,
                }
                for c in candles
            ]
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=1)
        except Exception as e:
            logger.error(f"Failed to save cache to {path}: {e}")
