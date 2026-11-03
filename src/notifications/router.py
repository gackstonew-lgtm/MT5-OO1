"""
Notification Noise Policy, State-Transition Router, Quiet Hours & Live Trade Threading (Phases 2 & 3).
"""

from __future__ import annotations
import json
import os
import re
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple

from src.notifications.events import (
    AlertState,
    BaseNotificationEvent,
    BreakEvenMovedEvent,
    NotificationEventType,
    NotificationTier,
    PartialClosedEvent,
    SpreadWarningEvent,
    StopLossHitEvent,
    TargetHitEvent,
    TradeOpenedEvent,
    TradeSetupEvent,
    WarmupProgressEvent,
)
from src.notifications.telegram_transport import (
    TelegramTransport,
    build_status_inline_keyboard,
)
from src.notifications.templates.default_templates import TemplateRenderer, esc


@dataclass
class AlertTransitionRecord:
    key: str
    state: AlertState = AlertState.OK
    last_sent_ts: float = 0.0
    first_repeat_ts: float = 0.0
    repeat_count: int = 0
    last_summary: str = ""


@dataclass
class LiveTradeThread:
    trade_id: str
    symbol: str
    direction: str
    setup_message_id: Optional[int] = None
    card_message_id: Optional[int] = None
    ticket: Optional[int] = None
    base_card_html: str = ""
    timeline_lines: List[str] = field(default_factory=list)
    closed: bool = False


class NotificationRouter:
    """
    Centralizes:
    1. 3-tier routing (LOUD, SILENT, ON_DEMAND)
    2. State-transition alerting (OK -> WARNING -> CRITICAL -> RECOVERED) with cooldowns, repeat merging & escalation
    3. Quiet hours filtering (only CRITICAL and live trade events pass during quiet hours)
    4. Single warm-up progress message + single ready message per symbol
    5. Pinned status card edited in place + optional hourly market-watch summary
    6. Live trade card editing-in-place and reply threading (reply_to_message_id) with fallback on edit failure
    """

    QUIET_HOURS_ALLOWED_EVENTS = {
        NotificationEventType.TRADE_SETUP,
        NotificationEventType.TRADE_OPENED,
        NotificationEventType.TARGET_HIT,
        NotificationEventType.STOP_LOSS_HIT,
        NotificationEventType.ORDER_REJECTED,
        NotificationEventType.CONNECTION_LOST,
        NotificationEventType.DAILY_LOSS_LIMIT_HIT,
        NotificationEventType.DRAWDOWN_WARNING,
        NotificationEventType.MARGIN_WARNING,
        NotificationEventType.BOT_STOPPED,
        NotificationEventType.UNAUTHORIZED_ACCESS,
    }

    def __init__(
        self,
        renderer: Optional[TemplateRenderer] = None,
        transport: Optional[TelegramTransport] = None,
        chat_id: str | int = "",
        config_path: str = "config/notifications.json"
    ):
        self.config_path = config_path
        self._cfg = self._load_config()
        self.renderer = renderer or TemplateRenderer(config_path=config_path)
        self.transport = transport
        self.chat_id = str(chat_id)

        self._lock = threading.Lock()
        self.verbose: bool = False

        # State-change tracking: key -> AlertTransitionRecord
        self._transitions: Dict[str, AlertTransitionRecord] = {}
        self.suppressed_alerts_count: int = 0

        # Warm-up deduplication: (symbol, tf) -> {"notified_warming": bool, "notified_ready": bool, "last_bars": int}
        self._warmup_state: Dict[Tuple[str, str], Dict[str, Any]] = {}

        # Pinned status card tracking
        self.pinned_status_message_id: Optional[int] = None
        self.last_status_card_html: str = ""
        self.last_running_smoothly_ts: float = time.time()

        # Market watch tracking
        self.marketwatch_enabled: bool = bool(self._cfg.get("marketwatch_enabled", False))
        self._marketwatch_scans: int = 0
        self._marketwatch_notes: Dict[str, str] = {}
        self.last_marketwatch_ts: float = time.time()

        # Quiet hours state
        qh = self._cfg.get("quiet_hours", {})
        self.quiet_hours_enabled: bool = bool(qh.get("enabled", False))
        self.quiet_start_hour_local: int = int(qh.get("start_hour_local", 23))
        self.quiet_end_hour_local: int = int(qh.get("end_hour_local", 6))
        self.quiet_until_ts: float = 0.0

        # Live trade threads: keyed by trade_id and ticket
        self._trade_threads_by_id: Dict[str, LiveTradeThread] = {}
        self._trade_threads_by_ticket: Dict[int, LiveTradeThread] = {}
        self._latest_thread_by_symbol: Dict[str, LiveTradeThread] = {}

        # Outgoing capture list for testing / inspection
        self.dispatched_records: List[Dict[str, Any]] = []

    def _load_config(self) -> Dict[str, Any]:
        if os.path.exists(self.config_path):
            try:
                with open(self.config_path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
        return {}

    def save_config(self) -> None:
        self._cfg["marketwatch_enabled"] = self.marketwatch_enabled
        self._cfg["quiet_hours"] = {
            "enabled": self.quiet_hours_enabled,
            "start_hour_local": self.quiet_start_hour_local,
            "end_hour_local": self.quiet_end_hour_local,
        }
        try:
            os.makedirs(os.path.dirname(self.config_path) or ".", exist_ok=True)
            with open(self.config_path, "w", encoding="utf-8") as f:
                json.dump(self._cfg, f, indent=2)
        except Exception:
            pass

    @property
    def cooldown_seconds(self) -> float:
        return float(self._cfg.get("alert_cooldown_seconds", 600.0))

    @property
    def escalation_threshold(self) -> int:
        return int(self._cfg.get("escalation_repeat_threshold", 5))

    def set_quiet_hours(
        self,
        hours_duration: Optional[float] = None,
        start_hour: Optional[int] = None,
        end_hour: Optional[int] = None,
        enabled: bool = True,
        now_ts: Optional[float] = None
    ) -> str:
        """Configures quiet hours either for N hours from now or a daily local window (e.g. 23-06)."""
        ts = now_ts if now_ts is not None else time.time()
        if not enabled or (hours_duration is not None and hours_duration <= 0):
            self.quiet_hours_enabled = False
            self.quiet_until_ts = 0.0
            self.save_config()
            return "🔔 <b>Quiet Hours DISABLED</b> — Standard notification routing restored."

        if start_hour is not None and end_hour is not None:
            self.quiet_hours_enabled = True
            self.quiet_start_hour_local = int(start_hour) % 24
            self.quiet_end_hour_local = int(end_hour) % 24
            self.save_config()
            return (
                f"🌙 <b>Quiet Hours ENABLED</b>: <code>{self.quiet_start_hour_local:02d}:00 - "
                f"{self.quiet_end_hour_local:02d}:00 ({self.renderer.tz_label})</code>\n"
                f"Only <b>CRITICAL</b> alerts and live <b>TRADE</b> events will notify during this window."
            )

        dur_h = float(hours_duration or 8.0)
        self.quiet_until_ts = ts + dur_h * 3600.0
        return (
            f"🌙 <b>Quiet Hours ACTIVE for {dur_h:g}h</b>\n"
            f"Only <b>CRITICAL</b> alerts and live <b>TRADE</b> events will notify during this window."
        )

    def is_quiet_hours_active(self, now_dt: Optional[datetime] = None, now_ts: Optional[float] = None) -> bool:
        ts = now_ts if now_ts is not None else time.time()
        if self.quiet_until_ts > ts:
            return True
        if not self.quiet_hours_enabled:
            return False

        dt_utc = (now_dt or datetime.now(timezone.utc)).astimezone(timezone.utc)
        local_tz = timezone(timedelta(hours=self.renderer.tz_offset_hours))
        local_hour = dt_utc.astimezone(local_tz).hour

        start_h = self.quiet_start_hour_local
        end_h = self.quiet_end_hour_local
        if start_h <= end_h:
            return start_h <= local_hour < end_h
        # Overnight window (e.g. 23:00 to 06:00)
        return local_hour >= start_h or local_hour < end_h

    def is_quiet_hours(self, now_dt: Optional[datetime] = None, now_ts: Optional[float] = None) -> bool:
        return self.is_quiet_hours_active(now_dt=now_dt, now_ts=now_ts)

    def set_quiet_for_hours(self, hours: float, now_ts: Optional[float] = None) -> str:
        return self.set_quiet_hours(hours_duration=hours, enabled=(hours > 0), now_ts=now_ts)

    def set_marketwatch(self, enabled: bool) -> str:
        self.marketwatch_enabled = bool(enabled)
        self.save_config()
        state_str = "ENABLED" if self.marketwatch_enabled else "DISABLED"
        return f"Hourly Market-Watch Summary is now {state_str}."

    def evaluate_state_transition(
        self,
        key: str,
        new_state: AlertState,
        summary: str = "",
        now_ts: Optional[float] = None
    ) -> Tuple[bool, int, AlertState]:
        """
        Evaluates whether a state-based alert should be sent.
        Returns `(should_send, repeat_count_in_window, effective_state)`.
        - Alerts immediately on transition: OK -> WARNING, OK/WARNING -> CRITICAL, WARNING/CRITICAL -> RECOVERED.
        - Suppresses identical state within cooldown_seconds while counting repeats.
        - Escalates WARNING -> CRITICAL if repeats >= escalation_threshold.
        """
        ts = now_ts if now_ts is not None else time.time()
        with self._lock:
            rec = self._transitions.get(key)
            if rec is None:
                rec = AlertTransitionRecord(key=key, state=AlertState.OK)
                self._transitions[key] = rec

            if new_state in (AlertState.OK, AlertState.RECOVERED):
                if rec.state in (AlertState.WARNING, AlertState.CRITICAL):
                    prev_repeats = rec.repeat_count
                    rec.state = AlertState.OK
                    rec.last_sent_ts = ts
                    rec.repeat_count = 0
                    rec.last_summary = summary
                    return True, max(1, prev_repeats), AlertState.RECOVERED
                return False, 0, AlertState.OK

            # new_state is WARNING or CRITICAL
            if rec.state != new_state:
                # State transition (e.g., OK -> WARNING or WARNING -> CRITICAL)
                rec.state = new_state
                rec.last_sent_ts = ts
                rec.first_repeat_ts = ts
                rec.repeat_count = 1
                rec.last_summary = summary
                return True, 1, new_state

            # Same state repeated
            rec.repeat_count += 1
            elapsed = ts - rec.last_sent_ts

            # Escalation check: if WARNING repeated >= escalation_threshold, escalate to CRITICAL
            if rec.state == AlertState.WARNING and rec.repeat_count >= self.escalation_threshold:
                rec.state = AlertState.CRITICAL
                rec.last_sent_ts = ts
                repeats = rec.repeat_count
                return True, repeats, AlertState.CRITICAL

            if elapsed >= self.cooldown_seconds:
                repeats = rec.repeat_count
                rec.last_sent_ts = ts
                rec.first_repeat_ts = ts
                rec.repeat_count = 1
                rec.last_summary = summary
                return True, repeats, rec.state

            # Suppressed within cooldown
            self.suppressed_alerts_count += 1
            return False, rec.repeat_count, rec.state

    def handle_warmup_bars(
        self,
        symbol: str,
        timeframe: str,
        loaded_bars: int,
        required_bars: int,
        timestamp: Optional[datetime] = None
    ) -> Optional[WarmupProgressEvent]:
        """
        Deduplicates warm-up notifications so "Insufficient candle history" sends:
        - Exactly ONE progress message while warming up (`loaded_bars < required_bars`).
        - Exactly ONE ready message once `loaded_bars >= required_bars` (if it previously warmed up).
        """
        key = (symbol.upper(), timeframe.upper())
        with self._lock:
            state = self._warmup_state.setdefault(
                key, {"notified_warming": False, "notified_ready": False}
            )
            if loaded_bars < required_bars:
                if not state["notified_warming"]:
                    state["notified_warming"] = True
                    state["notified_ready"] = False
                    ev = WarmupProgressEvent(
                        symbol=symbol,
                        timeframe=timeframe,
                        loaded_bars=loaded_bars,
                        required_bars=required_bars,
                        is_ready=False,
                    )
                    if timestamp:
                        ev.timestamp = timestamp
                    return ev
                self.suppressed_alerts_count += 1
                return None
            else:
                if state["notified_warming"] and not state["notified_ready"]:
                    state["notified_ready"] = True
                    state["notified_warming"] = False
                    ev = WarmupProgressEvent(
                        symbol=symbol,
                        timeframe=timeframe,
                        loaded_bars=loaded_bars,
                        required_bars=required_bars,
                        is_ready=True,
                    )
                    if timestamp:
                        ev.timestamp = timestamp
                    return ev
                return None

    def record_market_watch_scan(self, symbol: str, status_note: str = "no qualified setup") -> None:
        with self._lock:
            self._marketwatch_scans += 1
            self._marketwatch_notes[symbol] = status_note

    def build_market_watch_digest(self) -> str:
        with self._lock:
            scans = self._marketwatch_scans
            notes = dict(self._marketwatch_notes)
            self._marketwatch_scans = 0
            self._marketwatch_notes.clear()

        note_items = [f"<code>{esc(sym)}</code>: {esc(n)}" for sym, n in sorted(notes.items())]
        notes_str = "\n".join(f"• {item}" for item in note_items[:6]) if note_items else "• All monitored symbols flat; waiting for structure confirmation"
        return (
            f"🔭 <b>HOURLY MARKET WATCH SUMMARY</b>\n\n"
            f"📊 <b>Closed-Bar Scans:</b> <code>{scans}</code> (no qualified setup triggered)\n"
            f"{notes_str}"
        )

    def route_and_dispatch(
        self,
        event: BaseNotificationEvent,
        now_ts: Optional[float] = None,
        reply_markup: Optional[Dict[str, Any]] = None
    ) -> Tuple[bool, Optional[int], str]:
        """
        Routes a structured event through tier policy, quiet hours, and live trade threading.
        Returns `(sent, message_id, rendered_html)`.
        """
        tier = event.get_tier()
        if tier == NotificationTier.ON_DEMAND and not self.verbose and not event.is_sample:
            return False, None, ""

        # Quiet hours check (samples bypass quiet hours)
        if not event.is_sample and self.is_quiet_hours_active(now_dt=event.timestamp, now_ts=now_ts):
            if event.event_type not in self.QUIET_HOURS_ALLOWED_EVENTS and tier != NotificationTier.LOUD:
                self.suppressed_alerts_count += 1
                return False, None, ""

        # State-transition gating for warnings
        if not event.is_sample and isinstance(event, SpreadWarningEvent):
            ok_send, repeats, eff_state = self.evaluate_state_transition(
                key=f"spread:{event.symbol}",
                new_state=AlertState.WARNING,
                summary=f"{event.current_spread_pts} pts",
                now_ts=now_ts,
            )
            if not ok_send:
                return False, None, ""
            event.repeat_count = repeats
            if eff_state == AlertState.CRITICAL:
                tier = NotificationTier.LOUD

        # Live Trade Card & Threading (Phase 3) for non-sample trade lifecycle events
        if not event.is_sample and event.event_type in (
            NotificationEventType.TRADE_SETUP,
            NotificationEventType.TRADE_OPENED,
            NotificationEventType.BREAK_EVEN_MOVED,
            NotificationEventType.PARTIAL_CLOSED,
            NotificationEventType.TARGET_HIT,
            NotificationEventType.STOP_LOSS_HIT,
        ):
            return self._dispatch_threaded_trade_event(event, tier)

        rendered = self.renderer.render(event)
        disable_notif = (tier == NotificationTier.SILENT)
        msg_id = self._send_raw(
            text=rendered,
            disable_notification=disable_notif,
            reply_markup=reply_markup,
            event_type=event.event_type.value,
        )
        return True, msg_id, rendered

    def _dispatch_threaded_trade_event(
        self,
        event: BaseNotificationEvent,
        tier: NotificationTier
    ) -> Tuple[bool, Optional[int], str]:
        """
        Handles Phase 3 live trade card editing-in-place and reply threading:
        - TradeSetupEvent: posts initial TRADE SETUP message and creates LiveTradeThread.
        - TradeOpened / BreakEvenMoved / PartialClosed: edits the trade card in-place with an appended
          live timeline AND posts a threaded reply (`reply_to_message_id=setup_message_id`).
          Falls back gracefully to a new message if edit fails or message was deleted.
        - TargetHit / StopLossHit: updates card to CLOSED and sends a separate LOUD message
          replying to the setup thread with the final P&L result.
        """
        rendered = self.renderer.render(event)
        disable_notif = (tier == NotificationTier.SILENT)

        if isinstance(event, TradeSetupEvent):
            tid = event.trade_id or f"{event.symbol}_{int(time.time() * 1000)}"
            msg_id = self._send_raw(
                text=rendered,
                disable_notification=False,
                event_type=event.event_type.value,
            )
            thread = LiveTradeThread(
                trade_id=tid,
                symbol=event.symbol,
                direction=event.direction,
                setup_message_id=msg_id,
                card_message_id=msg_id,
                base_card_html=rendered,
            )
            with self._lock:
                self._trade_threads_by_id[tid] = thread
                self._latest_thread_by_symbol[event.symbol] = thread
            return True, msg_id, rendered

        # Find matching thread by trade_id, ticket, or symbol
        ticket = getattr(event, "ticket", None)
        tid = getattr(event, "trade_id", "")
        sym = getattr(event, "symbol", "")

        thread: Optional[LiveTradeThread] = None
        with self._lock:
            if tid and tid in self._trade_threads_by_id:
                thread = self._trade_threads_by_id[tid]
            elif ticket and ticket in self._trade_threads_by_ticket:
                thread = self._trade_threads_by_ticket[ticket]
            elif sym and sym in self._latest_thread_by_symbol:
                thread = self._latest_thread_by_symbol[sym]

            if thread and ticket:
                thread.ticket = int(ticket)
                self._trade_threads_by_ticket[int(ticket)] = thread

        if thread is None:
            # No prior setup message found — send standalone and start thread
            msg_id = self._send_raw(
                text=rendered,
                disable_notification=disable_notif,
                event_type=event.event_type.value,
            )
            if sym:
                new_t = LiveTradeThread(
                    trade_id=tid or f"{sym}_{ticket or 0}",
                    symbol=sym,
                    direction=getattr(event, "direction", "BUY"),
                    setup_message_id=msg_id,
                    card_message_id=msg_id,
                    ticket=ticket,
                    base_card_html=rendered,
                )
                with self._lock:
                    self._latest_thread_by_symbol[sym] = new_t
                    if ticket:
                        self._trade_threads_by_ticket[int(ticket)] = new_t
            return True, msg_id, rendered

        # Build concise timeline badge for the live card
        ts_short = event.timestamp.astimezone(timezone.utc).strftime("%H:%M:%S UTC")
        if isinstance(event, TradeOpenedEvent):
            thread.timeline_lines.append(
                f"⚡ <code>{ts_short}</code> Opened <code>#{event.ticket}</code> @ <code>{event.fill_price:.{event.digits}f}</code> (slip <code>{event.slippage_points:+.1f} pts</code>)"
            )
        elif isinstance(event, BreakEvenMovedEvent):
            thread.timeline_lines.append(
                f"🛡️ <code>{ts_short}</code> Protected: SL moved to Break-Even (<code>{event.new_sl:.{event.digits}f}</code>)"
            )
        elif isinstance(event, PartialClosedEvent):
            thread.timeline_lines.append(
                f"💰 <code>{ts_short}</code> Partial TP: Closed <code>{event.closed_lots:.2f}</code> lots (<code>+${event.realized_pnl_usd:,.2f}</code>)"
            )
        elif isinstance(event, TargetHitEvent):
            thread.closed = True
            thread.timeline_lines.append(
                f"🎉 <code>{ts_short}</code> CLOSED (TARGET HIT): <code>+${event.pnl_usd:,.2f} ({event.pnl_r:+.2f}R)</code>"
            )
        elif isinstance(event, StopLossHitEvent):
            thread.closed = True
            thread.timeline_lines.append(
                f"🛑 <code>{ts_short}</code> CLOSED (STOP LOSS): <code>-${abs(event.loss_usd):,.2f} ({event.loss_r:+.2f}R)</code>"
            )

        updated_card_html = (
            thread.base_card_html
            + "\n\n📌 <b>LIVE TRADE TIMELINE:</b>\n"
            + "\n".join(f"• {l}" for l in thread.timeline_lines)
        )

        # Edit live trade card in-place (with fallback to new message if deleted/failed)
        if thread.card_message_id is not None and self.transport and self.chat_id:
            ok_edit, should_fallback, _ = self.transport.edit_message_text(
                chat_id=self.chat_id,
                message_id=thread.card_message_id,
                text=updated_card_html,
            )
            if not ok_edit and should_fallback:
                new_card_id = self._send_raw(
                    text=updated_card_html,
                    disable_notification=True,
                    reply_to_message_id=thread.setup_message_id,
                    event_type="TradeCardFallback",
                )
                if new_card_id:
                    thread.card_message_id = new_card_id

        # For TargetHit / StopLossHit / TradeOpened, also send a threaded reply message (LOUD on close/open)
        if event.event_type in (
            NotificationEventType.TRADE_OPENED,
            NotificationEventType.BREAK_EVEN_MOVED,
            NotificationEventType.PARTIAL_CLOSED,
            NotificationEventType.TARGET_HIT,
            NotificationEventType.STOP_LOSS_HIT,
        ):
            is_close_or_open = event.event_type in (
                NotificationEventType.TRADE_OPENED,
                NotificationEventType.TARGET_HIT,
                NotificationEventType.STOP_LOSS_HIT,
            )
            reply_id = self._send_raw(
                text=rendered,
                disable_notification=not is_close_or_open,
                reply_to_message_id=thread.setup_message_id,
                event_type=event.event_type.value,
            )
            return True, reply_id, rendered

        return True, thread.card_message_id, updated_card_html

    def update_pinned_status_card(self, status_data: Dict[str, Any]) -> Tuple[bool, Optional[int], str]:
        """
        Updates the single pinned Status Card in place (or creates & pins it if none exists).
        Handles 'message not modified' and deleted-message fallback automatically.
        """
        card_html = self.renderer.render_pinned_status_card(status_data)
        kb = build_status_inline_keyboard()
        self.last_status_card_html = card_html

        if self.pinned_status_message_id is not None and self.transport and self.chat_id:
            ok_edit, should_fallback, _ = self.transport.edit_message_text(
                chat_id=self.chat_id,
                message_id=self.pinned_status_message_id,
                text=card_html,
                reply_markup=kb,
            )
            if ok_edit:
                return True, self.pinned_status_message_id, card_html
            if not should_fallback:
                return True, self.pinned_status_message_id, card_html

        # Send new status card and pin it
        msg_id = self._send_raw(
            text=card_html,
            disable_notification=True,
            reply_markup=kb,
            event_type="PinnedStatusCard",
        )
        if msg_id is not None:
            self.pinned_status_message_id = msg_id
            if self.transport and self.chat_id and self._cfg.get("pin_status_card", True):
                self.transport.pin_chat_message(self.chat_id, msg_id, disable_notification=True)
        return True, msg_id, card_html

    def _send_raw(
        self,
        text: str,
        disable_notification: bool = False,
        reply_to_message_id: Optional[int] = None,
        reply_markup: Optional[Dict[str, Any]] = None,
        event_type: str = "Generic"
    ) -> Optional[int]:
        rec = {
            "event_type": event_type,
            "text": text,
            "disable_notification": disable_notification,
            "reply_to_message_id": reply_to_message_id,
            "reply_markup": reply_markup,
            "timestamp": time.time(),
        }
        with self._lock:
            self.dispatched_records.append(rec)

        if self.transport and self.chat_id:
            ok, msg_id, _ = self.transport.send_message_ex(
                chat_id=self.chat_id,
                text=text,
                parse_mode="HTML",
                disable_notification=disable_notification,
                reply_to_message_id=reply_to_message_id,
                reply_markup=reply_markup,
            )
            if ok:
                return msg_id
        return int(len(self.dispatched_records))
