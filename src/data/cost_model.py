"""
Cost Modeling & Specification Profiler for MT5.
Captures real broker specifications: spread-by-hour distribution, commission,
swaps, tick sizing/value, margin requirements, stops/freeze levels.
"""

from __future__ import annotations
import os
import json
import logging
from dataclasses import dataclass, field, asdict
from typing import Dict, List, Optional, Any
import numpy as np

try:
    import MetaTrader5 as mt5
    MT5_AVAILABLE = True
except ImportError:
    mt5 = None
    MT5_AVAILABLE = False

from .historical_loader import HistoricalDataLoader
from src.core.contracts import Timeframe

logger = logging.getLogger("CostModelEngine")


@dataclass
class SymbolCostProfile:
    symbol: str
    broker_symbol: str
    currency_base: str
    currency_profit: str
    currency_margin: str
    digits: int
    point: float
    tick_size: float
    tick_value: float
    contract_size: float
    volume_min: float
    volume_step: float
    volume_max: float
    stops_level_pts: int
    freeze_level_pts: int
    swap_long: float
    swap_short: float
    swap_mode: int
    current_spread_pts: int
    current_spread_money: float
    margin_required_1_lot: float
    commission_per_lot_roundturn: float
    spread_by_hour: Dict[int, Dict[str, float]] = field(default_factory=dict)
    history_bars_available: Dict[str, int] = field(default_factory=dict)
    history_status: Dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class CostModelEngine:
    def __init__(self, loader: Optional[HistoricalDataLoader] = None):
        self.loader = loader or HistoricalDataLoader()

    def profile_symbol(
        self,
        symbol: str,
        required_bars: Optional[Dict[str, int]] = None
    ) -> Optional[SymbolCostProfile]:
        """Profiles a symbol with live broker metadata and historical spread distribution."""
        if not MT5_AVAILABLE:
            return None

        resolved = self.loader.resolve_broker_symbol(symbol)
        if not resolved:
            logger.error(f"Cannot resolve symbol {symbol} on broker")
            return None

        info = mt5.symbol_info(resolved)
        if info is None:
            logger.error(f"Failed to query symbol_info for {resolved}")
            return None

        # Ensure symbol is selected in Market Watch
        if not info.select:
            mt5.symbol_select(resolved, True)
            info = mt5.symbol_info(resolved)

        # Calculate required margin for 1.0 lot BUY
        margin_1lot = 0.0
        calc_margin = mt5.order_calc_margin(
            mt5.ORDER_TYPE_BUY,
            resolved,
            1.0,
            info.ask if info.ask > 0 else info.bid
        )
        if calc_margin is not None:
            margin_1lot = float(calc_margin)
        else:
            margin_1lot = float(info.margin_initial) if info.margin_initial > 0 else 1000.0

        # Current spread money calculation
        tick_sz = info.trade_tick_size if info.trade_tick_size > 0 else info.point
        tick_val = info.trade_tick_value if info.trade_tick_value > 0 else 1.0
        current_spread_money = (info.spread * info.point / tick_sz) * tick_val

        # Estimated round-turn commission (default $6-$7/lot if RAW/ECN, 0 if standard markup)
        commission = 0.0
        if "raw" in resolved.lower() or ".m" in resolved.lower() or info.spread < 10:
            commission = 6.0  # conservative default $6/lot standard institutional/ECN

        # Compute spread by hour from M15 history (past 2000 bars)
        spread_by_hour = self._compute_spread_by_hour(resolved)

        # Check history bar counts
        history_counts: Dict[str, int] = {}
        history_status: Dict[str, str] = {}
        tf_check = {
            "W1": (Timeframe.W1, mt5.TIMEFRAME_W1, 2000),
            "D1": (Timeframe.D1, mt5.TIMEFRAME_D1, 5000),
            "H4": (Timeframe.H4, mt5.TIMEFRAME_H4, 10000),
            "H1": (Timeframe.H1, mt5.TIMEFRAME_H1, 20000),
            "M15": (Timeframe.M15, mt5.TIMEFRAME_M15, 50000),
            "M1": (Timeframe.M1, mt5.TIMEFRAME_M1, 50000),
        }

        req = required_bars or {
            "W1": 52,
            "D1": 200,
            "H4": 300,
            "H1": 500,
            "M15": 500,
            "M1": 500,
        }

        for tf_name, (tf_enum, mt5_tf, max_fetch) in tf_check.items():
            # First check if loader already cached candles
            cached_candles = self.loader.load_candles(symbol, tf_enum, count=max_fetch, use_cache=True)
            count = len(cached_candles)
            if count == 0:
                rates = mt5.copy_rates_from_pos(resolved, mt5_tf, 0, max_fetch)
                count = len(rates) if rates is not None else 0
            history_counts[tf_name] = count
            needed = req.get(tf_name, 50)
            if count >= needed:
                history_status[tf_name] = f"SUFFICIENT ({count}/{needed})"
            elif count >= needed // 2:
                history_status[tf_name] = f"SHORT ({count}/{needed})"
            else:
                history_status[tf_name] = f"CRITICAL_SHORT ({count}/{needed})"

        return SymbolCostProfile(
            symbol=symbol,
            broker_symbol=resolved,
            currency_base=info.currency_base,
            currency_profit=info.currency_profit,
            currency_margin=info.currency_margin,
            digits=info.digits,
            point=info.point,
            tick_size=info.trade_tick_size,
            tick_value=info.trade_tick_value,
            contract_size=info.trade_contract_size,
            volume_min=info.volume_min,
            volume_step=info.volume_step,
            volume_max=info.volume_max,
            stops_level_pts=info.trade_stops_level,
            freeze_level_pts=info.trade_freeze_level,
            swap_long=info.swap_long,
            swap_short=info.swap_short,
            swap_mode=info.swap_mode,
            current_spread_pts=info.spread,
            current_spread_money=round(current_spread_money, 4),
            margin_required_1_lot=round(margin_1lot, 2),
            commission_per_lot_roundturn=commission,
            spread_by_hour=spread_by_hour,
            history_bars_available=history_counts,
            history_status=history_status,
        )

    def _compute_spread_by_hour(self, broker_symbol: str) -> Dict[int, Dict[str, float]]:
        rates = mt5.copy_rates_from_pos(broker_symbol, mt5.TIMEFRAME_M15, 0, 3000)
        if rates is None or len(rates) == 0:
            return {h: {"mean": 0.0, "median": 0.0, "p90": 0.0} for h in range(24)}

        hour_spreads: Dict[int, List[float]] = {h: [] for h in range(24)}
        utc_offset = self.loader.detect_broker_utc_offset(broker_symbol)

        for r in rates:
            # Server time -> true UTC hour
            utc_hour = (int(r['time']) - utc_offset) // 3600 % 24
            hour_spreads[utc_hour].append(float(r['spread']))

        result: Dict[int, Dict[str, float]] = {}
        for h in range(24):
            arr = np.array(hour_spreads[h]) if hour_spreads[h] else np.array([0.0])
            result[h] = {
                "mean_pts": round(float(np.mean(arr)), 1),
                "median_pts": round(float(np.median(arr)), 1),
                "p90_pts": round(float(np.percentile(arr, 90)), 1),
                "min_pts": round(float(np.min(arr)), 1),
                "max_pts": round(float(np.max(arr)), 1),
            }
        return result
