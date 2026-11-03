"""
Market Structure CHoCH-Warning -> BOS-Confirmation State Machine.
Implements the multi-stage structural validation lifecycle:
1. CHoCH = Warning Only (candle close confirmation, wicks ignored).
2. Pullback with confirmed Lower High (bearish) / Higher Low (bullish).
3. BOS confirmation closing beyond post-CHoCH extreme before allowing trade entry.
4. Trap Invalidation (failed CHoCH closing beyond pre-CHoCH extreme) and timeout expiry.
Strictly causal: operates sequentially on closed candles with zero lookahead.
"""

from __future__ import annotations
from enum import Enum
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple, Any

from src.core.contracts import Candle, Timeframe, OrderType, TrendBias


class ConfirmationState(str, Enum):
    IDLE = "IDLE"
    CHOCH_WARNING = "CHOCH_WARNING"
    PULLBACK = "PULLBACK"
    BOS_CONFIRMED = "BOS_CONFIRMED"
    FAILED_TRAP = "FAILED_TRAP"


class CycleDirection(str, Enum):
    BULLISH = "BULLISH"
    BEARISH = "BEARISH"


@dataclass
class StructureStateSnapshot:
    symbol: str
    timeframe: Timeframe
    state: ConfirmationState = ConfirmationState.IDLE
    direction: Optional[CycleDirection] = None
    broken_level: float = 0.0
    pre_choch_extreme: float = 0.0      # High that preceded bearish CHoCH, or Low that preceded bullish CHoCH
    post_choch_extreme: float = 0.0     # Lowest low after bearish CHoCH, or highest high after bullish CHoCH
    confirmed_retrace_extreme: float = 0.0  # Lower high for bearish, higher low for bullish
    choch_bar_index: int = -1
    choch_timestamp: Optional[datetime] = None
    bars_since_warning: int = 0
    is_confirmed: bool = False
    is_trapped: bool = False
    message: str = "State IDLE"
    stop_loss_reference: float = 0.0


class StructureStateMachine:
    """
    State machine supervisor tracking the CHoCH -> Pullback (LH/HL) -> BOS sequence
    per symbol and timeframe.
    """

    def __init__(
        self,
        swing_lookback: int = 5,
        require_close_break: bool = True,
        require_lower_high: bool = True,
        choch_timeout_bars: int = 20,
        allow_choch_only_entries: bool = False,
        failed_choch_action: str = "RESET_AND_LOG_TRAP"
    ):
        self.swing_lookback = swing_lookback
        self.require_close_break = require_close_break
        self.require_lower_high = require_lower_high
        self.choch_timeout_bars = choch_timeout_bars
        self.allow_choch_only_entries = allow_choch_only_entries
        self.failed_choch_action = failed_choch_action

        # State storage keyed by (symbol, timeframe)
        self._states: Dict[Tuple[str, Timeframe], StructureStateSnapshot] = {}
        self._last_processed_idx: Dict[Tuple[str, Timeframe], int] = {}
        self._trapped_events_count: int = 0

    @property
    def trapped_events_count(self) -> int:
        return self._trapped_events_count

    def get_state(self, symbol: str, timeframe: Timeframe) -> StructureStateSnapshot:
        key = (symbol, timeframe)
        if key not in self._states:
            self._states[key] = StructureStateSnapshot(symbol=symbol, timeframe=timeframe)
        return self._states[key]

    def reset_state(self, symbol: str, timeframe: Timeframe, reason: str = "") -> None:
        key = (symbol, timeframe)
        self._states[key] = StructureStateSnapshot(
            symbol=symbol,
            timeframe=timeframe,
            state=ConfirmationState.IDLE,
            message=f"Reset: {reason}"
        )

    def _find_confirmed_swings(self, candles: List[Candle], up_to_bar: int) -> Tuple[List[Tuple[int, float]], List[Tuple[int, float]]]:
        """
        Finds confirmed swings up to a specific closed bar index with strictly no lookahead.
        A swing at index j is confirmed at bar i if i >= j + k.
        Returns: (swing_highs, swing_lows) as [(index, price), ...]
        """
        k = self.swing_lookback
        highs: List[Tuple[int, float]] = []
        lows: List[Tuple[int, float]] = []

        if up_to_bar < 2 * k:
            return highs, lows

        for j in range(k, up_to_bar - k + 1):
            c = candles[j]
            is_high = all(c.high > candles[m].high for m in range(j - k, j)) and \
                      all(c.high >= candles[m].high for m in range(j + 1, j + k + 1))
            if is_high:
                highs.append((j, c.high))

            is_low = all(c.low < candles[m].low for m in range(j - k, j)) and \
                     all(c.low <= candles[m].low for m in range(j + 1, j + k + 1))
            if is_low:
                lows.append((j, c.low))

        return highs, lows

    def update(
        self,
        symbol: str,
        timeframe: Timeframe,
        candles: List[Candle]
    ) -> StructureStateSnapshot:
        """
        Advances the state machine sequentially bar-by-bar across closed candles.
        Guarantees deterministic behavior regardless of batch size or call frequency.
        """
        key = (symbol, timeframe)
        current_state = self.get_state(symbol, timeframe)

        n = len(candles)
        k = self.swing_lookback
        if n < 2 * k + 1:
            return current_state

        last_proc = self._last_processed_idx.get(key, -1)
        # If dataset was reloaded or truncated, restart from beginning
        if last_proc >= n:
            last_proc = -1
            current_state = StructureStateSnapshot(symbol=symbol, timeframe=timeframe)

        start_idx = max(2 * k, last_proc + 1)

        for bar_idx in range(start_idx, n):
            current_state = self._process_bar(key, current_state, candles, bar_idx)
            self._last_processed_idx[key] = bar_idx

        self._states[key] = current_state
        return current_state

    def _process_bar(
        self,
        key: Tuple[str, Timeframe],
        state: StructureStateSnapshot,
        candles: List[Candle],
        bar_idx: int
    ) -> StructureStateSnapshot:
        symbol, timeframe = key
        current_bar = candles[bar_idx]
        swing_highs, swing_lows = self._find_confirmed_swings(candles, bar_idx)

        # -------------------------------------------------------------
        # STATE: IDLE -> Check for initial CHoCH Warning
        # -------------------------------------------------------------
        if state.state in (ConfirmationState.IDLE, ConfirmationState.FAILED_TRAP):
            if not swing_highs or not swing_lows:
                return state

            last_sh_idx, last_sh_price = swing_highs[-1]
            last_sl_idx, last_sl_price = swing_lows[-1]

            # 1. Bearish CHoCH Check (close below protected swing low)
            bearish_choch = (current_bar.close < last_sl_price) if self.require_close_break else (current_bar.low < last_sl_price)
            if bearish_choch:
                pre_highs = [p for (idx, p) in swing_highs if idx < last_sl_idx]
                pre_high = pre_highs[-1] if pre_highs else last_sh_price
                if pre_high <= last_sl_price:
                    pre_high = max(last_sh_price, max(c.high for c in candles[max(0, last_sl_idx - 10):last_sl_idx + 1]))

                return StructureStateSnapshot(
                    symbol=symbol,
                    timeframe=timeframe,
                    state=ConfirmationState.CHOCH_WARNING,
                    direction=CycleDirection.BEARISH,
                    broken_level=last_sl_price,
                    pre_choch_extreme=pre_high,
                    post_choch_extreme=current_bar.low,
                    choch_bar_index=bar_idx,
                    choch_timestamp=current_bar.time,
                    bars_since_warning=0,
                    is_confirmed=self.allow_choch_only_entries,
                    is_trapped=False,
                    stop_loss_reference=pre_high,
                    message=f"CHoCH Warning: Bearish break below {last_sl_price:.5f} (pre-high {pre_high:.5f})"
                )

            # 2. Bullish CHoCH Check (close above protected swing high)
            bullish_choch = (current_bar.close > last_sh_price) if self.require_close_break else (current_bar.high > last_sh_price)
            if bullish_choch:
                pre_lows = [p for (idx, p) in swing_lows if idx < last_sh_idx]
                pre_low = pre_lows[-1] if pre_lows else last_sl_price
                if pre_low >= last_sh_price:
                    pre_low = min(last_sl_price, min(c.low for c in candles[max(0, last_sh_idx - 10):last_sh_idx + 1]))

                return StructureStateSnapshot(
                    symbol=symbol,
                    timeframe=timeframe,
                    state=ConfirmationState.CHOCH_WARNING,
                    direction=CycleDirection.BULLISH,
                    broken_level=last_sh_price,
                    pre_choch_extreme=pre_low,
                    post_choch_extreme=current_bar.high,
                    choch_bar_index=bar_idx,
                    choch_timestamp=current_bar.time,
                    bars_since_warning=0,
                    is_confirmed=self.allow_choch_only_entries,
                    is_trapped=False,
                    stop_loss_reference=pre_low,
                    message=f"CHoCH Warning: Bullish break above {last_sh_price:.5f} (pre-low {pre_low:.5f})"
                )

            return state

        # Elapsed bars since CHoCH warning was emitted
        bars_elapsed = bar_idx - state.choch_bar_index
        state.bars_since_warning = bars_elapsed

        # -------------------------------------------------------------
        # TIMEOUT CHECK (for warnings and pullbacks)
        # -------------------------------------------------------------
        if bars_elapsed > self.choch_timeout_bars and state.state != ConfirmationState.BOS_CONFIRMED:
            return StructureStateSnapshot(
                symbol=symbol,
                timeframe=timeframe,
                state=ConfirmationState.IDLE,
                message=f"Timeout expired ({bars_elapsed} bars > {self.choch_timeout_bars})"
            )

        # -------------------------------------------------------------
        # STATE: CHOCH_WARNING
        # -------------------------------------------------------------
        if state.state == ConfirmationState.CHOCH_WARNING:
            if state.direction == CycleDirection.BEARISH:
                if current_bar.low < state.post_choch_extreme:
                    state.post_choch_extreme = current_bar.low

                # TRAP CHECK: close above pre-CHoCH high
                if current_bar.close > state.pre_choch_extreme:
                    self._trapped_events_count += 1
                    state.state = ConfirmationState.FAILED_TRAP
                    state.is_trapped = True
                    state.is_confirmed = False
                    state.message = f"Failed CHoCH: Bullish Trap, closed above pre-high {state.pre_choch_extreme:.5f}"
                    return state

                # Check if a confirmed Lower High has formed after choch_bar_index
                new_highs = [(idx, p) for (idx, p) in swing_highs if idx > state.choch_bar_index]
                if new_highs:
                    last_new_idx, last_new_high = new_highs[-1]
                    if not self.require_lower_high or last_new_high < state.pre_choch_extreme:
                        state.state = ConfirmationState.PULLBACK
                        state.confirmed_retrace_extreme = last_new_high
                        state.stop_loss_reference = last_new_high
                        state.message = f"Pullback formed Lower High at {last_new_high:.5f}"
                        return state

            elif state.direction == CycleDirection.BULLISH:
                if current_bar.high > state.post_choch_extreme:
                    state.post_choch_extreme = current_bar.high

                # TRAP CHECK: close below pre-CHoCH low
                if current_bar.close < state.pre_choch_extreme:
                    self._trapped_events_count += 1
                    state.state = ConfirmationState.FAILED_TRAP
                    state.is_trapped = True
                    state.is_confirmed = False
                    state.message = f"Failed CHoCH: Bearish Trap, closed below pre-low {state.pre_choch_extreme:.5f}"
                    return state

                # Check if a confirmed Higher Low has formed after choch_bar_index
                new_lows = [(idx, p) for (idx, p) in swing_lows if idx > state.choch_bar_index]
                if new_lows:
                    last_new_idx, last_new_low = new_lows[-1]
                    if not self.require_lower_high or last_new_low > state.pre_choch_extreme:
                        state.state = ConfirmationState.PULLBACK
                        state.confirmed_retrace_extreme = last_new_low
                        state.stop_loss_reference = last_new_low
                        state.message = f"Pullback formed Higher Low at {last_new_low:.5f}"
                        return state

            return state

        # -------------------------------------------------------------
        # STATE: PULLBACK
        # -------------------------------------------------------------
        if state.state == ConfirmationState.PULLBACK:
            if state.direction == CycleDirection.BEARISH:
                invalidation = state.pre_choch_extreme
                if current_bar.close > invalidation:
                    self._trapped_events_count += 1
                    state.state = ConfirmationState.FAILED_TRAP
                    state.is_trapped = True
                    state.is_confirmed = False
                    state.message = f"Failed CHoCH: Invalidation, closed above {invalidation:.5f}"
                    return state

                # BOS CONFIRMATION: close below post-CHoCH low
                if current_bar.close < state.post_choch_extreme:
                    state.state = ConfirmationState.BOS_CONFIRMED
                    state.is_confirmed = True
                    state.message = f"BOS Confirmed: Closed below post-CHoCH low {state.post_choch_extreme:.5f}"
                    return state

            elif state.direction == CycleDirection.BULLISH:
                invalidation = state.pre_choch_extreme
                if current_bar.close < invalidation:
                    self._trapped_events_count += 1
                    state.state = ConfirmationState.FAILED_TRAP
                    state.is_trapped = True
                    state.is_confirmed = False
                    state.message = f"Failed CHoCH: Invalidation, closed below {invalidation:.5f}"
                    return state

                # BOS CONFIRMATION: close above post-CHoCH high
                if current_bar.close > state.post_choch_extreme:
                    state.state = ConfirmationState.BOS_CONFIRMED
                    state.is_confirmed = True
                    state.message = f"BOS Confirmed: Closed above post-CHoCH high {state.post_choch_extreme:.5f}"
                    return state

            return state

        # -------------------------------------------------------------
        # STATE: BOS_CONFIRMED
        # -------------------------------------------------------------
        if state.state == ConfirmationState.BOS_CONFIRMED:
            if state.direction == CycleDirection.BEARISH and current_bar.close > state.stop_loss_reference:
                return StructureStateSnapshot(symbol=symbol, timeframe=timeframe, state=ConfirmationState.IDLE, message="Trend invalidated above structural LH")
            elif state.direction == CycleDirection.BULLISH and current_bar.close < state.stop_loss_reference:
                return StructureStateSnapshot(symbol=symbol, timeframe=timeframe, state=ConfirmationState.IDLE, message="Trend invalidated below structural HL")

        return state
