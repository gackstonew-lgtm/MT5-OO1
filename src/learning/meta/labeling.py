"""
Triple-Barrier Labeling Algorithm (Advances in Financial Machine Learning, Chapter 3).
Computes dynamic multi-barrier outcomes from the bot's real structural SL, TP,
and maximum vertical holding periods, including overlap handling and sample weights.
"""

from __future__ import annotations
import math
from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional, Tuple, Dict
import numpy as np

from src.core.contracts import Candle, OrderType


@dataclass(frozen=True)
class BarrierLabel:
    signal_time: datetime
    exit_time: datetime
    direction: OrderType
    entry_price: float
    sl: float
    tp: float
    label: int           # 1 if TP hit first, 0 if SL hit first or vertical barrier expired
    return_r: float      # Realized outcome in R multiples
    sample_weight: float # Uniqueness / absolute return weighting
    duration_bars: int


class TripleBarrierLabeler:
    def __init__(self, max_holding_bars: int = 48):
        self.max_holding_bars = max_holding_bars

    def label_candidate(
        self,
        signal_bar_idx: int,
        direction: OrderType,
        entry_price: float,
        sl: float,
        tp: float,
        future_candles: List[Candle]
    ) -> Optional[BarrierLabel]:
        """
        Labels a single candidate by inspecting subsequent closed bars.
        Ensures strict causality: only future bars are inspected for the outcome.
        """
        if not future_candles or sl <= 0 or tp <= 0 or entry_price <= 0:
            return None

        sl_dist = abs(entry_price - sl)
        if sl_dist <= 0:
            return None

        is_buy = direction in [OrderType.BUY, OrderType.BUY_LIMIT, OrderType.BUY_STOP]
        limit_bars = min(len(future_candles), self.max_holding_bars)

        label = 0
        exit_price = entry_price
        exit_bar_idx = limit_bars - 1
        exit_time = future_candles[exit_bar_idx].time

        for idx in range(limit_bars):
            bar = future_candles[idx]
            if is_buy:
                # Lower barrier (SL) check first for conservatism
                if bar.low <= sl:
                    label = 0
                    exit_price = sl
                    exit_bar_idx = idx
                    exit_time = bar.time
                    break
                elif bar.high >= tp:
                    label = 1
                    exit_price = tp
                    exit_bar_idx = idx
                    exit_time = bar.time
                    break
            else:  # SELL
                if bar.high >= sl:
                    label = 0
                    exit_price = sl
                    exit_bar_idx = idx
                    exit_time = bar.time
                    break
                elif bar.low <= tp:
                    label = 1
                    exit_price = tp
                    exit_bar_idx = idx
                    exit_time = bar.time
                    break

        # If vertical barrier reached without hitting SL or TP
        if exit_price == entry_price:
            last_close = future_candles[exit_bar_idx].close
            exit_price = last_close
            if is_buy:
                label = 1 if last_close > entry_price else 0
            else:
                label = 1 if last_close < entry_price else 0

        # Outcome in R
        gross_pts = (exit_price - entry_price) if is_buy else (entry_price - exit_price)
        return_r = gross_pts / sl_dist

        # Sample weight: scales with outcome magnitude and penalizes long holding periods
        weight = float(min(3.0, max(0.5, abs(return_r) * math.exp(-0.01 * exit_bar_idx))))

        return BarrierLabel(
            signal_time=future_candles[0].time,
            exit_time=exit_time,
            direction=direction,
            entry_price=entry_price,
            sl=sl,
            tp=tp,
            label=label,
            return_r=round(return_r, 3),
            sample_weight=round(weight, 3),
            duration_bars=exit_bar_idx + 1
        )
