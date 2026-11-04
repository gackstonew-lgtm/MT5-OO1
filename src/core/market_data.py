"""
Market Data Engine.
Handles historical and live candle buffers, MT5 rates acquisition,
broker symbol suffix resolution, forming-vs-completed candle separation,
incremental candle synchronization, data validation, gap/staleness detection,
and DataHealthStatus diagnostics across all monitored timeframes.
"""

from __future__ import annotations
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import time
from typing import Any, Dict, List, Optional, Tuple

from src.core.contracts import Candle, DataHealthStatus, SymbolInfo, Timeframe
from src.core.instruments import instrument_aliases, get_instrument_profile


DEFAULT_MIN_BARS: Dict[Timeframe, int] = {
    Timeframe.W1: 30,
    Timeframe.D1: 150,
    Timeframe.H4: 250,
    Timeframe.H1: 300,
    Timeframe.M30: 300,
    Timeframe.M15: 300,
    Timeframe.M5: 300,
    Timeframe.M1: 200,
}

DEFAULT_REQUEST_BARS: Dict[Timeframe, int] = {
    Timeframe.W1: 100,
    Timeframe.D1: 250,
    Timeframe.H4: 350,
    Timeframe.H1: 500,
    Timeframe.M30: 500,
    Timeframe.M15: 500,
    Timeframe.M5: 500,
    Timeframe.M1: 300,
}

STANDARD_SCAN_TIMEFRAMES: Tuple[Timeframe, ...] = (
    Timeframe.W1,
    Timeframe.D1,
    Timeframe.H4,
    Timeframe.H1,
    Timeframe.M30,
    Timeframe.M15,
    Timeframe.M5,
    Timeframe.M1,
)


class MarketDataEngine:
    def __init__(
        self,
        symbols: Optional[List[str]] = None,
        symbols_config_path: str = "config/symbols.json",
        max_buffer_size: int = 1000,
    ):
        self.symbols = symbols or ["EURUSD", "GBPUSD", "USDJPY", "XAUUSD"]
        self.max_buffer_size = max(500, int(max_buffer_size))
        self._symbols_config: Dict[str, Any] = self._load_symbols_config(symbols_config_path)

        # Buffers: symbol -> timeframe -> list of COMPLETED Candle objects (complete=True)
        self._buffers: Dict[str, Dict[Timeframe, List[Candle]]] = {
            s: {tf: [] for tf in Timeframe} for s in self.symbols
        }
        # Forming bar cache: symbol -> timeframe -> currently forming Candle (complete=False)
        self._forming_candles: Dict[str, Dict[Timeframe, Optional[Candle]]] = {
            s: {tf: None for tf in Timeframe} for s in self.symbols
        }
        self._symbol_info: Dict[str, SymbolInfo] = {}
        self._broker_symbol_map: Dict[str, str] = {}
        self._canonical_symbol_map: Dict[str, str] = {}
        self._health_status: Dict[str, Dict[Timeframe, DataHealthStatus]] = {
            s: {} for s in self.symbols
        }
        self._mt5_initialized: bool = False
        self._preload_configured_symbol_specs()

    def _load_symbols_config(self, path_str: str) -> Dict[str, Any]:
        p = Path(path_str)
        if p.exists():
            try:
                with open(p, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, dict):
                    return data
            except Exception:
                pass
        return {}

    def _preload_configured_symbol_specs(self) -> None:
        """Initializes SymbolInfo defaults from config/symbols.json so specs are accurate offline too."""
        for sym, spec in self._symbols_config.items():
            if not isinstance(spec, dict):
                continue
            info = SymbolInfo(
                symbol=sym,
                digits=int(spec.get("digits", 5)),
                point=float(spec.get("point", 0.00001)),
                tick_size=float(spec.get("tick_size", spec.get("point", 0.00001))),
                tick_value=float(spec.get("tick_value", 1.0)),
                contract_size=float(spec.get("contract_size", 100000.0)),
                min_lot=float(spec.get("min_lot", 0.01)),
                max_lot=float(spec.get("max_lot", 100.0)),
                lot_step=float(spec.get("lot_step", 0.01)),
                spread=float(spec.get("default_spread_points", 1.2)),
            )
            self._symbol_info[sym] = info

    def _alias_candidates(self, symbol: str) -> List[str]:
        """Returns deterministic candidate broker names for the same underlying instrument."""
        candidates = [symbol]
        if symbol in self._broker_symbol_map:
            mapped = self._broker_symbol_map[symbol]
            if mapped not in candidates:
                candidates.insert(0, mapped)
        if symbol in self._canonical_symbol_map:
            canon = self._canonical_symbol_map[symbol]
            if canon not in candidates:
                candidates.append(canon)
        root = symbol
        for sfx in (".m", ".std", ".pro", ".ecn", "_ecn", ".raw", ".a", ".r", ".c", ".i", "m", "+", "#"):
            if root.endswith(sfx) and len(root) > len(sfx):
                root = root[:-len(sfx)]
                break
        for suffix in (".m", ".std", "", "m", ".pro", ".ecn", "_ecn", ".raw", ".a", ".r", "+"):
            cand = f"{root}{suffix}"
            if cand not in candidates:
                candidates.append(cand)
        # Instrument-registry aliases cover broker-specific Gold/crypto names
        # such as XAUUSDm, GOLD, GOLD.m while never substituting an unrelated symbol.
        for alias in instrument_aliases(symbol):
            if alias not in candidates:
                candidates.append(alias)
        return candidates

    def register_symbol_info(self, info: SymbolInfo) -> None:
        self._symbol_info[info.symbol] = info
        base = info.symbol[:-2] if info.symbol.endswith(".m") else info.symbol
        dot_m = f"{base}.m"
        if base not in self._symbol_info:
            self._symbol_info[base] = info
        if dot_m not in self._symbol_info:
            self._symbol_info[dot_m] = info

    def get_symbol_info(self, symbol: str) -> SymbolInfo:
        for cand in self._alias_candidates(symbol):
            if cand in self._symbol_info:
                return self._symbol_info[cand]
        return SymbolInfo(symbol=symbol)

    def get_required_bars(self, symbol: str, timeframe: Timeframe, strategy_minimum: int = 20) -> int:
        """Returns configured minimum bar count for a symbol/timeframe."""
        for cand in self._alias_candidates(symbol):
            spec = self._symbols_config.get(cand)
            if isinstance(spec, dict):
                min_bars_map = spec.get("min_bars", {})
                if isinstance(min_bars_map, dict) and timeframe.value in min_bars_map:
                    return max(strategy_minimum, int(min_bars_map[timeframe.value]))
        return max(strategy_minimum, DEFAULT_MIN_BARS.get(timeframe, 50))

    def get_request_bars(self, symbol: str, timeframe: Timeframe) -> int:
        req = self.get_required_bars(symbol, timeframe, strategy_minimum=50)
        default_req = DEFAULT_REQUEST_BARS.get(timeframe, 300)
        return max(req, default_req)

    def set_candles(self, symbol: str, timeframe: Timeframe, candles: List[Candle]) -> None:
        """Sets and validates historical candles for a specific symbol & timeframe."""
        if symbol not in self._buffers:
            self._buffers[symbol] = {tf: [] for tf in Timeframe}
        if symbol not in self._forming_candles:
            self._forming_candles[symbol] = {tf: None for tf in Timeframe}

        completed_raw: List[Candle] = []
        forming_latest: Optional[Candle] = None
        for c in candles:
            if c.complete:
                completed_raw.append(c)
            else:
                if forming_latest is None or c.time >= forming_latest.time:
                    forming_latest = c

        validated = self._validate_and_sort_candles(completed_raw)
        if len(validated) > self.max_buffer_size:
            validated = validated[-self.max_buffer_size :]

        self._buffers[symbol][timeframe] = validated
        self._forming_candles[symbol][timeframe] = forming_latest

        broker_sym = self._broker_symbol_map.get(symbol)
        if broker_sym and broker_sym != symbol:
            self._buffers.setdefault(broker_sym, {tf: [] for tf in Timeframe})[timeframe] = validated
            self._forming_candles.setdefault(broker_sym, {tf: None for tf in Timeframe})[timeframe] = forming_latest

    def append_candle(self, symbol: str, timeframe: Timeframe, candle: Candle) -> None:
        """Appends a new candle. Forming candles go to _forming_candles; completed candles go to _buffers."""
        if symbol not in self._buffers:
            self._buffers[symbol] = {tf: [] for tf in Timeframe}
        if symbol not in self._forming_candles:
            self._forming_candles[symbol] = {tf: None for tf in Timeframe}

        if not self._is_valid_ohlc(candle):
            return

        if not getattr(candle, "complete", True):
            self._forming_candles[symbol][timeframe] = candle
            return

        buf = self._buffers[symbol][timeframe]
        if buf and buf[-1].time == candle.time:
            buf[-1] = candle
        elif not buf or candle.time > buf[-1].time:
            buf.append(candle)
        else:
            # Out-of-order historical bar insertion: deduplicate and re-sort
            merged = buf + [candle]
            validated, _, _ = self._validate_and_sort_candles_with_stats(merged)
            self._buffers[symbol][timeframe] = validated
            buf = self._buffers[symbol][timeframe]

        if len(buf) > self.max_buffer_size:
            del buf[: len(buf) - self.max_buffer_size]

    def get_candles(
        self,
        symbol: str,
        timeframe: Timeframe,
        count: Optional[int] = None,
        include_forming: bool = False,
    ) -> List[Candle]:
        """Retrieves the most recent candles (completed only by default)."""
        buf: List[Candle] = []
        resolved_sym = symbol
        for cand in self._alias_candidates(symbol):
            candidate_buf = self._buffers.get(cand, {}).get(timeframe, [])
            if candidate_buf:
                buf = candidate_buf
                resolved_sym = cand
                break

        result = list(buf) if (count is None or count >= len(buf)) else list(buf[-count:])
        if include_forming:
            forming = self.get_forming_candle(resolved_sym, timeframe)
            if forming is not None and (not result or forming.time > result[-1].time):
                result.append(forming)
                if count is not None and len(result) > count:
                    result = result[-count:]
        return result

    def get_forming_candle(self, symbol: str, timeframe: Timeframe) -> Optional[Candle]:
        for cand in self._alias_candidates(symbol):
            fc = self._forming_candles.get(cand, {}).get(timeframe)
            if fc is not None:
                return fc
        return None

    def get_latest_candle(self, symbol: str, timeframe: Timeframe) -> Optional[Candle]:
        candles = self.get_candles(symbol, timeframe, count=1, include_forming=False)
        return candles[-1] if candles else None

    def _is_valid_ohlc(self, c: Candle) -> bool:
        try:
            for val in (c.open, c.high, c.low, c.close):
                if val is None or math.isnan(float(val)) or math.isinf(float(val)):
                    return False
            if c.open <= 0 or c.high <= 0 or c.low <= 0 or c.close <= 0:
                return False
            if c.high < c.low:
                return False
            if c.open > c.high or c.open < c.low or c.close > c.high or c.close < c.low:
                return False
            return True
        except Exception:
            return False

    def _validate_and_sort_candles(self, candles: List[Candle]) -> List[Candle]:
        """Ensures candle integrity: time order, deduplication, high >= low, open/close inside high/low."""
        valid: List[Candle] = []
        for c in candles:
            if c.high < c.low or c.open <= 0 or c.close <= 0 or c.low <= 0:
                continue
            if c.open > c.high or c.open < c.low or c.close > c.high or c.close < c.low:
                continue
            valid.append(c)
        valid.sort(key=lambda x: x.time)
        # Fast O(N) adjacent deduplication (keeps latest for identical timestamp)
        if len(valid) > 1:
            dedup: List[Candle] = [valid[0]]
            for c in valid[1:]:
                if c.time == dedup[-1].time:
                    dedup[-1] = c
                else:
                    dedup.append(c)
            return dedup
        return valid

    def _validate_and_sort_candles_with_stats(
        self, candles: List[Candle]
    ) -> Tuple[List[Candle], int, int]:
        by_time: Dict[datetime, Candle] = {}
        duplicate_count = 0
        invalid_count = 0
        for c in candles:
            if not self._is_valid_ohlc(c):
                invalid_count += 1
                continue
            ts = c.time if c.time.tzinfo is not None else c.time.replace(tzinfo=timezone.utc)
            if ts != c.time:
                c = Candle(
                    time=ts,
                    open=c.open,
                    high=c.high,
                    low=c.low,
                    close=c.close,
                    tick_volume=c.tick_volume,
                    spread=c.spread,
                    complete=c.complete,
                    session_name=c.session_name,
                )
            if ts in by_time:
                duplicate_count += 1
            by_time[ts] = c
        sorted_candles = [by_time[k] for k in sorted(by_time.keys())]
        return sorted_candles, duplicate_count, invalid_count

    @staticmethod
    def _count_missing_gaps(candles: List[Candle], timeframe: Timeframe) -> int:
        """Counts unexpected bar gaps while ignoring normal weekend closures."""
        if len(candles) < 2:
            return 0
        expected_sec = timeframe.minutes * 60
        gaps = 0
        # Inspect recent 100 bars for gap health
        recent = candles[-100:]
        for i in range(1, len(recent)):
            prev_t = recent[i - 1].time
            curr_t = recent[i].time
            delta = (curr_t - prev_t).total_seconds()
            if delta <= expected_sec * 1.5:
                continue
            # Ignore weekend gap (Friday -> Sunday/Monday)
            if prev_t.weekday() in (4, 5) and curr_t.weekday() in (6, 0):
                continue
            # Ignore daily rollover break on D1/W1
            if timeframe in (Timeframe.D1, Timeframe.W1) and delta <= expected_sec * 3.5:
                continue
            gaps += 1
        return gaps

    def _update_health_from_buffer(
        self,
        symbol: str,
        broker_symbol: str,
        timeframe: Timeframe,
        requested_count: int,
        dup_count: int = 0,
        invalid_count: int = 0,
        source: str = "MT5_DIRECT",
        retry_count: int = 0,
        failure_reason: Optional[str] = None,
        now_utc: Optional[datetime] = None,
    ) -> DataHealthStatus:
        now = now_utc or datetime.now(timezone.utc)
        buf = self.get_candles(symbol, timeframe, include_forming=False)
        forming = self.get_forming_candle(symbol, timeframe)
        latest_completed = buf[-1].time if buf else None
        freshness_sec = (
            max(0.0, (now - latest_completed).total_seconds())
            if latest_completed is not None
            else float("inf")
        )
        gaps = self._count_missing_gaps(buf, timeframe)
        # A completed candle is fresh if within ~3x timeframe duration (or weekend)
        is_weekend = now.weekday() == 5 or (now.weekday() == 4 and now.hour >= 22) or (now.weekday() == 6 and now.hour < 21)
        max_fresh_sec = max(timeframe.minutes * 60 * 3.5, 3600.0)
        is_stale = False
        if latest_completed is None:
            is_stale = True
        elif not is_weekend and timeframe in (Timeframe.M1, Timeframe.M5, Timeframe.M15, Timeframe.M30, Timeframe.H1):
            # Only mark stale when we are comparing against live clock (and bar is older than 6 hours for intraday)
            if freshness_sec > max(max_fresh_sec, 6 * 3600.0) and source in ("MT5_DIRECT", "BRIDGE_RATES"):
                is_stale = True

        req_min = 20
        is_healthy = (
            len(buf) >= req_min
            and failure_reason is None
            and not is_stale
        )
        status = DataHealthStatus(
            symbol=symbol,
            broker_symbol=broker_symbol,
            timeframe=timeframe,
            requested_count=requested_count,
            received_count=len(buf),
            required_minimum=req_min,
            latest_completed_bar_time=latest_completed,
            forming_bar_time=forming.time if forming else None,
            freshness_seconds=freshness_sec if math.isfinite(freshness_sec) else -1.0,
            missing_gaps_count=gaps,
            duplicate_bars_removed=dup_count,
            invalid_bars_removed=invalid_count,
            connection_status="CONNECTED" if failure_reason is None else "DEGRADED",
            source=source,
            last_sync_time=now,
            retry_count=retry_count,
            is_stale=is_stale,
            is_healthy=is_healthy,
            failure_reason=failure_reason,
        )
        self._health_status.setdefault(symbol, {})[timeframe] = status
        if broker_symbol != symbol:
            self._health_status.setdefault(broker_symbol, {})[timeframe] = status
        return status

    def get_health_status(self, symbol: str, timeframe: Timeframe) -> Optional[DataHealthStatus]:
        for cand in self._alias_candidates(symbol):
            st = self._health_status.get(cand, {}).get(timeframe)
            if st is not None:
                return st
        return None

    def get_all_health_statuses(self) -> Dict[str, Dict[str, Any]]:
        report: Dict[str, Dict[str, Any]] = {}
        for sym in self.symbols:
            report[sym] = {}
            for tf in STANDARD_SCAN_TIMEFRAMES:
                st = self.get_health_status(sym, tf)
                if st is not None:
                    report[sym][tf.value] = st.to_dict()
        return report

    def ensure_mt5_initialized(self) -> bool:
        if self._mt5_initialized:
            return True
        try:
            import MetaTrader5 as mt5
            if mt5.initialize():
                self._mt5_initialized = True
                return True
        except Exception:
            pass
        return False

    def resolve_broker_symbol(self, symbol: str) -> str:
        """
        Safely resolves a configured symbol (e.g. 'EURUSD') to the broker's actual symbol
        name (e.g. 'EURUSD.m') in Market Watch without ever substituting an unrelated symbol.
        """
        if symbol in self._broker_symbol_map:
            return self._broker_symbol_map[symbol]

        if self.ensure_mt5_initialized():
            try:
                import MetaTrader5 as mt5
                # 1. First check deterministic candidate aliases
                for candidate in self._alias_candidates(symbol):
                    info = mt5.symbol_info(candidate)
                    if info is not None:
                        mt5.symbol_select(candidate, True)
                        self._broker_symbol_map[symbol] = candidate
                        self._canonical_symbol_map[candidate] = symbol
                        self._refresh_symbol_info_from_mt5(symbol, candidate)
                        return candidate

                # 2. Dynamic terminal symbols enumeration (search all available broker symbols)
                all_symbols = mt5.symbols_get()
                if all_symbols:
                    root = symbol
                    for sfx in (".m", ".std", ".pro", ".ecn", "_ecn", ".raw", ".a", ".r", ".c", ".i", "m", "+", "#"):
                        if root.endswith(sfx) and len(root) > len(sfx):
                            root = root[:-len(sfx)]
                            break
                    root_len = len(root)
                    matches = []
                    for s_item in all_symbols:
                        s_name = s_item.name
                        if root in s_name:
                            # Exact match or prefix/suffix boundary check
                            if s_name == root:
                                matches.append(s_name)
                            elif s_name.startswith(root):
                                next_c = s_name[root_len:root_len+1]
                                if next_c in ('.', '_', '-', 'm', 'a', '+'):
                                    matches.append(s_name)
                            elif s_name.endswith(root):
                                prev_c = s_name[-root_len-1:-root_len]
                                if prev_c in ('.', '_', '-'):
                                    matches.append(s_name)
                    # If exactly one clean match found, or match with standard .m suffix
                    chosen = None
                    if f"{root}.m" in matches:
                        chosen = f"{root}.m"
                    elif f"{root}.std" in matches:
                        chosen = f"{root}.std"
                    elif len(matches) == 1:
                        chosen = matches[0]

                    if chosen:
                        mt5.symbol_select(chosen, True)
                        self._broker_symbol_map[symbol] = chosen
                        self._canonical_symbol_map[chosen] = symbol
                        self._refresh_symbol_info_from_mt5(symbol, chosen)
                        return chosen
            except Exception:
                pass

        # Fallback: if config/symbols.json has .m variant and symbol doesn't have it
        dot_m = f"{symbol}.m" if not symbol.endswith(".m") else symbol
        if dot_m in self._symbols_config and symbol not in self._broker_symbol_map:
            self._broker_symbol_map[symbol] = symbol
        return self._broker_symbol_map.get(symbol, symbol)

    def _refresh_symbol_info_from_mt5(self, canonical_symbol: str, broker_symbol: str) -> Optional[SymbolInfo]:
        try:
            import MetaTrader5 as mt5
            info = mt5.symbol_info(broker_symbol)
            if info is None:
                return None
            tick = mt5.symbol_info_tick(broker_symbol)
            ask = float(tick.ask) if (tick is not None and tick.ask > 0) else float(info.ask)
            bid = float(tick.bid) if (tick is not None and tick.bid > 0) else float(info.bid)
            last_tick_dt = (
                datetime.fromtimestamp(int(tick.time), tz=timezone.utc)
                if (tick is not None and getattr(tick, "time", 0) > 0)
                else datetime.now(timezone.utc)
            )
            sym_info = SymbolInfo(
                symbol=canonical_symbol,
                digits=int(info.digits),
                point=float(info.point) if info.point > 0 else 0.00001,
                tick_size=float(info.trade_tick_size) if info.trade_tick_size > 0 else float(info.point or 0.00001),
                tick_value=float(info.trade_tick_value) if info.trade_tick_value > 0 else 1.0,
                contract_size=float(info.trade_contract_size) if info.trade_contract_size > 0 else 100000.0,
                min_lot=float(info.volume_min) if info.volume_min > 0 else 0.01,
                max_lot=float(info.volume_max) if info.volume_max > 0 else 100.0,
                lot_step=float(info.volume_step) if info.volume_step > 0 else 0.01,
                spread=float(info.spread),
                ask=ask,
                bid=bid,
                stops_level=int(getattr(info, "trade_stops_level", 0)),
                freeze_level=int(getattr(info, "trade_freeze_level", 0)),
                last_tick_time=last_tick_dt,
            )
            self.register_symbol_info(sym_info)
            if broker_symbol != canonical_symbol:
                broker_copy = SymbolInfo(
                    symbol=broker_symbol,
                    digits=sym_info.digits,
                    point=sym_info.point,
                    tick_size=sym_info.tick_size,
                    tick_value=sym_info.tick_value,
                    contract_size=sym_info.contract_size,
                    min_lot=sym_info.min_lot,
                    max_lot=sym_info.max_lot,
                    lot_step=sym_info.lot_step,
                    spread=sym_info.spread,
                    ask=sym_info.ask,
                    bid=sym_info.bid,
                    stops_level=sym_info.stops_level,
                    freeze_level=sym_info.freeze_level,
                    last_tick_time=sym_info.last_tick_time,
                )
                self._symbol_info[broker_symbol] = broker_copy
            return sym_info
        except Exception:
            return None

    def sync_symbol_timeframe(
        self,
        symbol: str,
        timeframe: Timeframe,
        count: Optional[int] = None,
        bridge: Any = None,
        max_retries: int = 3,
        force_full: bool = False,
    ) -> DataHealthStatus:
        """
        Synchronizes historical and latest candles for a single (symbol, timeframe).
        - Uses incremental updates when sufficient history is already present in buffer.
        - Separates forming bar (pos 0, complete=False) from closed bars (pos >= 1, complete=True).
        - Retries with controlled backoff up to max_retries before failing gracefully.
        """
        broker_sym = self.resolve_broker_symbol(symbol)
        existing_buf = self.get_candles(symbol, timeframe, include_forming=False)
        target_count = count or self.get_request_bars(symbol, timeframe)

        # Incremental sync if buffer already has sufficient history
        incremental = (not force_full) and (len(existing_buf) >= min(50, target_count))
        fetch_count = 6 if incremental else (target_count + 1)

        last_err: Optional[str] = None
        retries_used = 0

        for attempt in range(max(1, max_retries)):
            retries_used = attempt
            # 1. Try direct MetaTrader5 Python API first
            if self.ensure_mt5_initialized():
                try:
                    import MetaTrader5 as mt5
                    tf_map = {
                        Timeframe.W1: mt5.TIMEFRAME_W1,
                        Timeframe.D1: mt5.TIMEFRAME_D1,
                        Timeframe.H4: mt5.TIMEFRAME_H4,
                        Timeframe.H1: mt5.TIMEFRAME_H1,
                        Timeframe.M30: mt5.TIMEFRAME_M30,
                        Timeframe.M15: mt5.TIMEFRAME_M15,
                        Timeframe.M5: mt5.TIMEFRAME_M5,
                        Timeframe.M1: mt5.TIMEFRAME_M1,
                    }
                    mt5_tf = tf_map.get(timeframe)
                    if mt5_tf is not None:
                        mt5.symbol_select(broker_sym, True)
                        self._refresh_symbol_info_from_mt5(symbol, broker_sym)
                        rates = mt5.copy_rates_from_pos(broker_sym, mt5_tf, 0, fetch_count)
                        if rates is not None and len(rates) >= 2:
                            total_r = len(rates)
                            fetched_candles: List[Candle] = []
                            for idx, r in enumerate(rates):
                                is_complete = idx < (total_r - 1)
                                dt = datetime.fromtimestamp(int(r["time"]), tz=timezone.utc)
                                fetched_candles.append(
                                    Candle(
                                        time=dt,
                                        open=float(r["open"]),
                                        high=float(r["high"]),
                                        low=float(r["low"]),
                                        close=float(r["close"]),
                                        tick_volume=int(r["tick_volume"]),
                                        spread=int(r["spread"]),
                                        complete=is_complete,
                                    )
                                )
                            if incremental:
                                for c in fetched_candles:
                                    self.append_candle(symbol, timeframe, c)
                                return self._update_health_from_buffer(
                                    symbol=symbol,
                                    broker_symbol=broker_sym,
                                    timeframe=timeframe,
                                    requested_count=fetch_count - 1,
                                    source="MT5_INCREMENTAL",
                                    retry_count=retries_used,
                                    failure_reason=None,
                                )
                            else:
                                self.set_candles(symbol, timeframe, fetched_candles)
                                return self._update_health_from_buffer(
                                    symbol=symbol,
                                    broker_symbol=broker_sym,
                                    timeframe=timeframe,
                                    requested_count=target_count,
                                    source="MT5_DIRECT",
                                    retry_count=retries_used,
                                    failure_reason=None,
                                )
                        else:
                            last_err = f"MT5 copy_rates_from_pos returned {0 if rates is None else len(rates)} bars for {broker_sym} {timeframe.value}"
                except Exception as e:
                    last_err = f"MT5 error: {e}"

            # 2. Fallback to TCP Bridge get_rates if available
            if bridge is not None and hasattr(bridge, "get_rates"):
                try:
                    tf_arg = timeframe.value if hasattr(timeframe, "value") else str(timeframe)
                    raw_res = bridge.get_rates(broker_sym, tf_arg, count=fetch_count - 1)
                    if (
                        isinstance(raw_res, dict)
                        and raw_res.get("status") != "SUCCESS"
                        and broker_sym != symbol
                    ):
                        raw_res = bridge.get_rates(symbol, tf_arg, count=fetch_count - 1)
                    elif not raw_res and broker_sym != symbol:
                        raw_res = bridge.get_rates(symbol, tf_arg, count=fetch_count - 1)

                    bridge_candles: List[Candle] = []
                    if isinstance(raw_res, list):
                        bridge_candles = raw_res
                    elif isinstance(raw_res, dict) and raw_res.get("status") == "SUCCESS":
                        data_items = raw_res.get("data", [])
                        if isinstance(data_items, list) and len(data_items) > 0:
                            # Ensure chronological ordering and mark latest bar complete status
                            sorted_items = sorted(
                                data_items,
                                key=lambda x: int(x.get("time_utc", x.get("time", 0))),
                            )
                            total_items = len(sorted_items)
                            for idx, item in enumerate(sorted_items):
                                ts_val = int(item.get("time_utc", item.get("time", 0)))
                                is_comp = bool(item.get("complete", idx < total_items - 1))
                                bridge_candles.append(
                                    Candle(
                                        time=datetime.fromtimestamp(ts_val, tz=timezone.utc),
                                        open=float(item["open"]),
                                        high=float(item["high"]),
                                        low=float(item["low"]),
                                        close=float(item["close"]),
                                        tick_volume=int(item.get("tick_volume", 0)),
                                        spread=int(item.get("spread", 0)),
                                        complete=is_comp,
                                    )
                                )

                    if bridge_candles and len(bridge_candles) > 0:
                        if incremental:
                            for c in bridge_candles:
                                self.append_candle(symbol, timeframe, c)
                        else:
                            self.set_candles(symbol, timeframe, bridge_candles)
                        return self._update_health_from_buffer(
                            symbol=symbol,
                            broker_symbol=broker_sym,
                            timeframe=timeframe,
                            requested_count=target_count,
                            source="BRIDGE_RATES",
                            retry_count=retries_used,
                            failure_reason=None,
                        )
                    else:
                        last_err = f"Bridge RATES returned 0 bars for {broker_sym} {timeframe.value}"
                except Exception as e:
                    last_err = f"Bridge error: {e}"

            if attempt < max_retries - 1:
                time.sleep(0.12 * (2 ** attempt))

        return self._update_health_from_buffer(
            symbol=symbol,
            broker_symbol=broker_sym,
            timeframe=timeframe,
            requested_count=target_count,
            source="FAILED",
            retry_count=retries_used,
            failure_reason=last_err or "History retrieval failed",
        )

    def sync_symbol_history(
        self,
        symbol: str,
        timeframes: Optional[List[Timeframe]] = None,
        bridge: Any = None,
        max_retries: int = 3,
        force_full: bool = False,
    ) -> Dict[Timeframe, DataHealthStatus]:
        tfs = timeframes or list(STANDARD_SCAN_TIMEFRAMES)
        results: Dict[Timeframe, DataHealthStatus] = {}
        for tf in tfs:
            results[tf] = self.sync_symbol_timeframe(
                symbol=symbol,
                timeframe=tf,
                bridge=bridge,
                max_retries=max_retries,
                force_full=force_full,
            )
        return results

    def initialize_and_warmup(
        self,
        bridge: Any = None,
        timeframes: Optional[List[Timeframe]] = None,
        max_retries: int = 3,
    ) -> Dict[str, Dict[str, Any]]:
        """
        Synchronizes full historical candles across all configured symbols and timeframes
        before live scanning begins.
        """
        tfs = timeframes or list(STANDARD_SCAN_TIMEFRAMES)
        for sym in self.symbols:
            self.sync_symbol_history(
                symbol=sym,
                timeframes=tfs,
                bridge=bridge,
                max_retries=max_retries,
                force_full=True,
            )
        return self.get_all_health_statuses()

    def load_from_mt5(self, mt5_connector=None, count: int = 500) -> bool:
        """Loads historical rates from live MT5 instance for all symbols and timeframes."""
        try:
            any_success = False
            for sym in self.symbols:
                for tf in STANDARD_SCAN_TIMEFRAMES:
                    st = self.sync_symbol_timeframe(
                        symbol=sym,
                        timeframe=tf,
                        count=count,
                        max_retries=2,
                        force_full=True,
                    )
                    if st.received_count >= 20:
                        any_success = True
            return any_success
        except Exception as e:
            print(f"[MarketDataEngine] Failed to load from MT5: {e}")
            return False

    def get_live_tick(self, symbol: str) -> Dict[str, Any]:
        """
        Fetches the latest live Bid/Ask/Spread/Timestamp tick for `symbol` (resolving broker .m suffix),
        or falls back to the latest buffered SymbolInfo / forming / completed candle.
        """
        info = self.get_symbol_info(symbol)
        broker_sym = self.resolve_broker_symbol(symbol)
        if self.ensure_mt5_initialized():
            try:
                import MetaTrader5 as mt5
                mt5.symbol_select(broker_sym, True)
                tick = mt5.symbol_info_tick(broker_sym)
                s_info = mt5.symbol_info(broker_sym)
                if tick is not None and float(getattr(tick, "bid", 0.0)) > 0:
                    bid = float(tick.bid)
                    ask = float(tick.ask) if float(getattr(tick, "ask", 0.0)) > 0 else bid
                    pt = float(getattr(s_info, "point", info.point)) if s_info else info.point
                    if pt <= 0:
                        pt = 0.00001
                    spread_pts = round((ask - bid) / pt, 1) if ask >= bid else float(info.spread)
                    ts = datetime.fromtimestamp(int(getattr(tick, "time", time.time())), tz=timezone.utc)
                    info.bid = bid
                    info.ask = ask
                    info.spread = spread_pts
                    info.last_tick_time = ts
                    self.register_symbol_info(info)
                    return {
                        "symbol": symbol,
                        "broker_symbol": broker_sym,
                        "bid": bid,
                        "ask": ask,
                        "spread_points": spread_pts,
                        "timestamp": ts,
                        "available": True,
                        "source": "MT5_LIVE_TICK",
                    }
            except Exception:
                pass

        # Fallback to buffered M1/M5/M15 candle + SymbolInfo
        for tf in (Timeframe.M1, Timeframe.M5, Timeframe.M15):
            forming = self.get_forming_candle(symbol, tf)
            if forming is not None:
                pt = info.point if info.point > 0 else 0.00001
                sp = float(forming.spread if forming.spread > 0 else info.spread)
                bid = forming.close
                ask = round(bid + sp * pt, info.digits)
                return {
                    "symbol": symbol,
                    "broker_symbol": broker_sym,
                    "bid": bid,
                    "ask": ask,
                    "spread_points": sp,
                    "timestamp": forming.time,
                    "available": True,
                    "source": f"FORMING_{tf.value}",
                }
            closed = self.get_candles(symbol, tf)
            if closed:
                last_c = closed[-1]
                pt = info.point if info.point > 0 else 0.00001
                sp = float(last_c.spread if last_c.spread > 0 else info.spread)
                bid = last_c.close
                ask = round(bid + sp * pt, info.digits)
                return {
                    "symbol": symbol,
                    "broker_symbol": broker_sym,
                    "bid": bid,
                    "ask": ask,
                    "spread_points": sp,
                    "timestamp": last_c.time,
                    "available": True,
                    "source": f"CLOSED_{tf.value}",
                }

        return {
            "symbol": symbol,
            "broker_symbol": broker_sym,
            "bid": info.bid,
            "ask": info.ask,
            "spread_points": float(info.spread),
            "timestamp": datetime.now(timezone.utc),
            "available": info.bid > 0,
            "source": "SYMBOL_INFO_FALLBACK",
        }
