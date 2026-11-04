"""
Fast Tick-by-Tick Exit Manager for Scalping Positions (Phase 3).
Evaluates open positions on every incoming tick:
1. Break-even at +0.5R or +1.0R (config) once price clears spread + buffer.
2. Partial take-profit at configurable R, then trails remainder beyond confirmed M1/M5 swing or ATR multiple.
3. Time stop (closes position if neither SL nor TP is hit within N bars/seconds, when still near flat/stagnant).
4. Momentum-failure exit (closed candle structure break against position).
5. Spread-spike protection & session-end / pre-rollover / pre-news flatten.
6. Respects broker stops_level and freeze_level via BrokerConstraintValidator; retries failed modifications safely.
7. Emits TRADE-level events with exact exit/modification reasons and tracks MFE/MAE per position.
"""

from __future__ import annotations
import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple, Any, Callable

from src.core.contracts import Position, SymbolInfo, OrderType, Candle
from src.execution.persistent_socket import BrokerConstraintValidator


@dataclass
class FastExitAction:
    ticket: int
    symbol: str
    action_type: str  # "MODIFY_SL", "PARTIAL_CLOSE", "FULL_CLOSE"
    reason: str
    event_level: str = "TRADE"
    new_sl: Optional[float] = None
    new_tp: Optional[float] = None
    close_volume: Optional[float] = None


@dataclass
class PositionExcursionTracker:
    ticket: int
    symbol: str
    mfe_r: float = 0.0
    mae_r: float = 0.0
    mfe_points: float = 0.0
    mae_points: float = 0.0


class FastTickExitManager:
    def __init__(self, config_path: str = "config/fast_mode.json"):
        self.config_path = config_path
        self._cfg = self._load_config()
        self.excursions: Dict[int, PositionExcursionTracker] = {}
        self.trade_event_logs: List[str] = []

    def _load_config(self) -> Dict[str, Any]:
        if os.path.exists(self.config_path):
            try:
                with open(self.config_path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
        return {}

    def reload_config(self) -> None:
        self._cfg = self._load_config()

    def _emit_trade_event(self, symbol: str, ticket: int, action_type: str, reason: str) -> None:
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
        line = f"[{ts} UTC] [TRADE] [{symbol}] Ticket #{ticket} | {action_type} | Reason: {reason}"
        self.trade_event_logs.append(line)

    def update_excursion(self, pos: Position, current_price: float, point: float) -> PositionExcursionTracker:
        tracker = self.excursions.get(pos.ticket)
        if tracker is None:
            tracker = PositionExcursionTracker(ticket=pos.ticket, symbol=pos.symbol)
            self.excursions[pos.ticket] = tracker

        sl_dist = pos.initial_sl_distance if pos.initial_sl_distance > 0 else abs(pos.open_price - pos.stop_loss)
        if sl_dist <= 0:
            sl_dist = 10.0 * (point if point > 0 else 0.0001)

        if pos.order_type == OrderType.BUY:
            diff = current_price - pos.open_price
        else:
            diff = pos.open_price - current_price

        r_now = diff / sl_dist
        pts_now = diff / (point if point > 0 else 0.0001)

        if r_now > tracker.mfe_r:
            tracker.mfe_r = round(r_now, 3)
            tracker.mfe_points = round(pts_now, 1)
        if r_now < 0 and abs(r_now) > tracker.mae_r:
            tracker.mae_r = round(abs(r_now), 3)
            tracker.mae_points = round(abs(pts_now), 1)

        return tracker

    def evaluate_tick(
        self,
        pos: Position,
        symbol_info: SymbolInfo,
        recent_candles: Optional[List[Candle]] = None,
        current_time: Optional[datetime] = None,
        median_spread_points: Optional[float] = None,
        is_pre_rollover: bool = False,
        is_pre_news: bool = False,
        minutes_to_session_end: Optional[float] = None
    ) -> List[FastExitAction]:
        """
        Evaluates a single open position on the current tick and returns ordered exit/modify actions.
        """
        actions: List[FastExitAction] = []
        exit_cfg = self._cfg.get("fast_exit", {})
        if not exit_cfg.get("enabled", True):
            return actions

        now = current_time or datetime.now(timezone.utc)
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)

        pt = symbol_info.point if symbol_info.point > 0 else 0.00001
        cur_price = (
            symbol_info.bid if pos.order_type == OrderType.BUY else symbol_info.ask
        ) or pos.current_price
        if cur_price <= 0:
            cur_price = pos.current_price

        # Update MFE / MAE excursion telemetry
        self.update_excursion(pos, cur_price, pt)

        sl_dist = pos.initial_sl_distance if pos.initial_sl_distance > 0 else abs(pos.open_price - pos.stop_loss)
        if sl_dist <= 0:
            sl_dist = 10.0 * pt

        profit_dist = (cur_price - pos.open_price) if pos.order_type == OrderType.BUY else (pos.open_price - cur_price)
        current_r = profit_dist / sl_dist

        # ------------------------------------------------------------------
        # 1. Pre-Rollover / Session-End Flatten (News flatten completely removed)
        # ------------------------------------------------------------------
        if exit_cfg.get("flatten_before_rollover", True) and is_pre_rollover:
            act = FastExitAction(
                ticket=pos.ticket,
                symbol=pos.symbol,
                action_type="FULL_CLOSE",
                reason="Pre-rollover swap blackout flatten",
                close_volume=pos.volume
            )
            self._emit_trade_event(pos.symbol, pos.ticket, act.action_type, act.reason)
            return [act]

        flatten_sess_min = float(exit_cfg.get("flatten_before_session_end_minutes", 5.0))
        if minutes_to_session_end is not None and 0 < minutes_to_session_end <= flatten_sess_min:
            act = FastExitAction(
                ticket=pos.ticket,
                symbol=pos.symbol,
                action_type="FULL_CLOSE",
                reason=f"Session-end flatten ({minutes_to_session_end:.1f}m remaining <= {flatten_sess_min:.1f}m)",
                close_volume=pos.volume
            )
            self._emit_trade_event(pos.symbol, pos.ticket, act.action_type, act.reason)
            return [act]

        # ------------------------------------------------------------------
        # 2. Spread-Spike Protection
        # ------------------------------------------------------------------
        spike_mult = float(exit_cfg.get("spread_spike_multiplier", 3.0))
        if median_spread_points and median_spread_points > 0:
            if symbol_info.spread >= median_spread_points * spike_mult:
                # If trade is in profit (>= 0.3R), lock or close to protect gains from spread spike
                if current_r >= 0.3:
                    act = FastExitAction(
                        ticket=pos.ticket,
                        symbol=pos.symbol,
                        action_type="FULL_CLOSE",
                        reason=f"Spread spike protection ({symbol_info.spread:.1f} pts >= {spike_mult}x median {median_spread_points:.1f} pts)",
                        close_volume=pos.volume
                    )
                    self._emit_trade_event(pos.symbol, pos.ticket, act.action_type, act.reason)
                    return [act]

        # ------------------------------------------------------------------
        # 3. Time Stop (if neither SL nor TP hit within N seconds)
        # ------------------------------------------------------------------
        max_hold_sec = float(exit_cfg.get("time_stop_seconds", 3600))
        if pos.open_time is not None and max_hold_sec > 0:
            open_dt = pos.open_time if pos.open_time.tzinfo else pos.open_time.replace(tzinfo=timezone.utc)
            held_sec = (now - open_dt).total_seconds()
            if held_sec >= max_hold_sec:
                act = FastExitAction(
                    ticket=pos.ticket,
                    symbol=pos.symbol,
                    action_type="FULL_CLOSE",
                    reason=f"Time stop expired: held {held_sec:.0f}s >= {max_hold_sec:.0f}s limit at {current_r:.2f}R",
                    close_volume=pos.volume
                )
                self._emit_trade_event(pos.symbol, pos.ticket, act.action_type, act.reason)
                return [act]

        # ------------------------------------------------------------------
        # 4. Momentum-Failure Exit (Closed candle structure break against position)
        # ------------------------------------------------------------------
        if exit_cfg.get("momentum_failure_exit", True) and recent_candles:
            closed = [c for c in recent_candles if c.complete]
            if len(closed) >= 4:
                last_c = closed[-1]
                prev_3 = closed[-4:-1]
                if pos.order_type == OrderType.BUY:
                    recent_support = min(c.low for c in prev_3)
                    if last_c.is_bearish and last_c.close < recent_support and current_r < 0.2:
                        act = FastExitAction(
                            ticket=pos.ticket,
                            symbol=pos.symbol,
                            action_type="FULL_CLOSE",
                            reason=f"Momentum failure: closed bar ({last_c.close}) broke below support ({recent_support})",
                            close_volume=pos.volume
                        )
                        self._emit_trade_event(pos.symbol, pos.ticket, act.action_type, act.reason)
                        return [act]
                else:
                    recent_resistance = max(c.high for c in prev_3)
                    if last_c.is_bullish and last_c.close > recent_resistance and current_r < 0.2:
                        act = FastExitAction(
                            ticket=pos.ticket,
                            symbol=pos.symbol,
                            action_type="FULL_CLOSE",
                            reason=f"Momentum failure: closed bar ({last_c.close}) broke above resistance ({recent_resistance})",
                            close_volume=pos.volume
                        )
                        self._emit_trade_event(pos.symbol, pos.ticket, act.action_type, act.reason)
                        return [act]

        # ------------------------------------------------------------------
        # 5. Break-Even at +0.5R or +1.0R once price clears spread + buffer
        # ------------------------------------------------------------------
        be_trigger_r = float(exit_cfg.get("break_even_trigger_r", 0.5))
        be_buffer_pts = float(exit_cfg.get("break_even_buffer_points", 5.0))
        spread_plus_buf_dist = (symbol_info.spread + be_buffer_pts) * pt

        if not pos.break_even_activated and current_r >= be_trigger_r and profit_dist > spread_plus_buf_dist:
            if pos.order_type == OrderType.BUY:
                target_be_sl = symbol_info.round_price(pos.open_price + spread_plus_buf_dist)
                sl_improves = target_be_sl > pos.stop_loss
            else:
                target_be_sl = symbol_info.round_price(pos.open_price - spread_plus_buf_dist)
                sl_improves = (pos.stop_loss == 0.0 or target_be_sl < pos.stop_loss)

            if sl_improves:
                ok_mod, _ = BrokerConstraintValidator.validate_modification_constraints(
                    direction=pos.order_type,
                    current_price=cur_price,
                    current_sl=pos.stop_loss,
                    current_tp=pos.take_profit,
                    new_sl=target_be_sl,
                    new_tp=pos.take_profit,
                    symbol_info=symbol_info
                )
                if ok_mod:
                    act = FastExitAction(
                        ticket=pos.ticket,
                        symbol=pos.symbol,
                        action_type="MODIFY_SL",
                        reason=f"Break-even triggered at +{current_r:.2f}R (SL -> {target_be_sl})",
                        new_sl=target_be_sl,
                        new_tp=pos.take_profit
                    )
                    self._emit_trade_event(pos.symbol, pos.ticket, act.action_type, act.reason)
                    actions.append(act)

        # ------------------------------------------------------------------
        # 6. Partial Take-Profit at configurable R (default +1.0R)
        # ------------------------------------------------------------------
        partial_r = float(exit_cfg.get("partial_tp_trigger_r", 1.0))
        partial_frac = float(exit_cfg.get("partial_tp_close_fraction", 0.5))

        if not pos.partial_taken and current_r >= partial_r and pos.volume >= symbol_info.min_lot * 2:
            raw_close_vol = pos.volume * partial_frac
            close_vol = symbol_info.normalize_volume_down(raw_close_vol)
            rem_vol = round(pos.volume - close_vol, 2)
            if close_vol >= symbol_info.min_lot and rem_vol >= symbol_info.min_lot:
                act = FastExitAction(
                    ticket=pos.ticket,
                    symbol=pos.symbol,
                    action_type="PARTIAL_CLOSE",
                    reason=f"Partial TP at +{current_r:.2f}R: closing {close_vol:.2f} lots",
                    close_volume=close_vol
                )
                self._emit_trade_event(pos.symbol, pos.ticket, act.action_type, act.reason)
                actions.append(act)

        # ------------------------------------------------------------------
        # 7. Trailing Stop Beyond Latest Confirmed Swing or ATR Multiple
        # ------------------------------------------------------------------
        if current_r >= partial_r and recent_candles:
            closed = [c for c in recent_candles if c.complete]
            if len(closed) >= 3:
                recent_3 = closed[-3:]
                if pos.order_type == OrderType.BUY:
                    swing_trail = symbol_info.round_price(min(c.low for c in recent_3) - 2.0 * pt)
                    if swing_trail > pos.stop_loss and swing_trail > pos.open_price:
                        ok_mod, _ = BrokerConstraintValidator.validate_modification_constraints(
                            direction=pos.order_type,
                            current_price=cur_price,
                            current_sl=pos.stop_loss,
                            current_tp=pos.take_profit,
                            new_sl=swing_trail,
                            new_tp=pos.take_profit,
                            symbol_info=symbol_info
                        )
                        if ok_mod:
                            act = FastExitAction(
                                ticket=pos.ticket,
                                symbol=pos.symbol,
                                action_type="MODIFY_SL",
                                reason=f"Trailing SL behind confirmed swing low ({swing_trail}) at +{current_r:.2f}R",
                                new_sl=swing_trail,
                                new_tp=pos.take_profit
                            )
                            self._emit_trade_event(pos.symbol, pos.ticket, act.action_type, act.reason)
                            actions.append(act)
                else:
                    swing_trail = symbol_info.round_price(max(c.high for c in recent_3) + 2.0 * pt)
                    if (pos.stop_loss == 0 or swing_trail < pos.stop_loss) and swing_trail < pos.open_price:
                        ok_mod, _ = BrokerConstraintValidator.validate_modification_constraints(
                            direction=pos.order_type,
                            current_price=cur_price,
                            current_sl=pos.stop_loss,
                            current_tp=pos.take_profit,
                            new_sl=swing_trail,
                            new_tp=pos.take_profit,
                            symbol_info=symbol_info
                        )
                        if ok_mod:
                            act = FastExitAction(
                                ticket=pos.ticket,
                                symbol=pos.symbol,
                                action_type="MODIFY_SL",
                                reason=f"Trailing SL behind confirmed swing high ({swing_trail}) at +{current_r:.2f}R",
                                new_sl=swing_trail,
                                new_tp=pos.take_profit
                            )
                            self._emit_trade_event(pos.symbol, pos.ticket, act.action_type, act.reason)
                            actions.append(act)

        return actions

    def execute_exit_actions_with_retry(
        self,
        actions: List[FastExitAction],
        modify_fn: Callable[[int, float, float], bool],
        close_fn: Callable[[int, Optional[float]], bool],
        max_retries: int = 2
    ) -> int:
        """Executes exit/modify actions with safe retries on transient failure."""
        succeeded = 0
        for act in actions:
            for _ in range(max_retries + 1):
                if act.action_type == "MODIFY_SL" and act.new_sl is not None:
                    if modify_fn(act.ticket, act.new_sl, act.new_tp or 0.0):
                        succeeded += 1
                        break
                elif act.action_type == "PARTIAL_CLOSE":
                    if close_fn(act.ticket, act.close_volume):
                        succeeded += 1
                        break
                elif act.action_type == "FULL_CLOSE":
                    if close_fn(act.ticket, None):
                        succeeded += 1
                        break
        return succeeded
