"""
Centralized Telegram HTML Templates & Number Formatting (Phase 1).
Turns strongly-typed notification events into clean, user-friendly 3-8 line HTML cards.
"""

from __future__ import annotations
import html
import json
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from src.notifications.events import (
    BaseNotificationEvent,
    BotPausedEvent,
    BotResumedEvent,
    BotStartedEvent,
    BotStoppedEvent,
    BreakEvenMovedEvent,
    ConfigChangedEvent,
    ConnectionLostEvent,
    ConnectionRestoredEvent,
    DailyLossLimitHitEvent,
    DailyLossWarningEvent,
    DailySummaryEvent,
    DrawdownWarningEvent,
    HealthOKEvent,
    MarginWarningEvent,
    NewsBlackoutEvent,
    NotificationEventType,
    OrderRejectedEvent,
    PartialClosedEvent,
    SessionCloseEvent,
    SessionOpenEvent,
    SetupSkippedEvent,
    SpreadWarningEvent,
    StopLossHitEvent,
    TargetHitEvent,
    TradeOpenedEvent,
    TradeSetupEvent,
    UnauthorizedAccessEvent,
    WarmupProgressEvent,
    WeeklySummaryEvent,
    map_retcode_to_plain_english,
    mask_account_number,
    sanitize_sensitive_text,
)


def esc(value: Any) -> str:
    """Escapes dynamic text and sanitizes sensitive tokens/logins."""
    if value is None:
        return ""
    return html.escape(sanitize_sensitive_text(str(value)))


def fmt_price(price: float, digits: int = 2) -> str:
    """Formats price to exact symbol digits."""
    d = max(0, min(int(digits), 8))
    return f"{float(price):.{d}f}"


def fmt_money(amount: float, signed: bool = False) -> str:
    """Formats USD monetary amounts with thousands separators."""
    val = float(amount)
    if signed:
        sign = "+" if val >= 0 else "-"
        return f"{sign}${abs(val):,.2f}"
    if val < 0:
        return f"-${abs(val):,.2f}"
    return f"${val:,.2f}"


def fmt_r(r_val: float, signed: bool = True) -> str:
    """Formats R multiples consistently (e.g., +2.00R)."""
    val = float(r_val)
    if signed:
        return f"{val:+.2f}R"
    return f"{val:.2f}R"


def fmt_pct(pct_val: float, signed: bool = False) -> str:
    """Formats percentages consistently (e.g., 0.50%)."""
    val = float(pct_val)
    if signed:
        return f"{val:+.2f}%"
    return f"{val:.2f}%"


def format_timestamp_footer(
    dt: Optional[datetime] = None,
    tz_offset_hours: float = 3.0,
    tz_label: str = "UTC+3",
    account_number: int | str = "DEMO"
) -> str:
    """
    Renders a single consistent timestamp footer with UTC and configurable display timezone.
    Example: 🕒 2026-11-03 14:30:00 UTC (17:30:00 UTC+3) • Acct ****0001
    """
    now_utc = (dt or datetime.now(timezone.utc)).astimezone(timezone.utc)
    local_tz = timezone(timedelta(hours=float(tz_offset_hours)))
    now_local = now_utc.astimezone(local_tz)
    utc_str = now_utc.strftime("%Y-%m-%d %H:%M:%S UTC")
    local_str = now_local.strftime(f"%H:%M:%S {tz_label}")
    masked_acc = mask_account_number(account_number)
    return (
        f"\n\n━━━━━━━━━━━━━━━━━━━━\n"
        f"🕒 <code>{esc(utc_str)} ({esc(local_str)})</code> • Acct <code>{esc(masked_acc)}</code>"
    )


def split_message_safely(text: str, max_chars: int = 4096) -> List[str]:
    """
    Splits a message into chunks <= max_chars at newline boundaries so HTML tags
    are never sliced mid-line.
    """
    if len(text) <= max_chars:
        return [text]

    lines = text.split("\n")
    chunks: List[str] = []
    current: List[str] = []
    cur_len = 0

    for line in lines:
        if len(line) > max_chars:
            if current:
                chunks.append("\n".join(current))
                current = []
                cur_len = 0
            for i in range(0, len(line), max_chars):
                chunks.append(line[i : i + max_chars])
            continue

        add_len = len(line) + (1 if current else 0)
        if cur_len + add_len > max_chars:
            chunks.append("\n".join(current))
            current = [line]
            cur_len = len(line)
        else:
            current.append(line)
            cur_len += add_len

    if current:
        chunks.append("\n".join(current))

    return chunks


class TemplateRenderer:
    def __init__(
        self,
        config_path: str = "config/notifications.json",
        account_number: int | str = "DEMO",
        tz_offset_hours: Optional[float] = None,
        tz_label: Optional[str] = None,
    ):
        self.config_path = config_path
        self.account_number = account_number
        self._cfg = self._load_config()
        if tz_offset_hours is not None:
            self._cfg["display_timezone_offset_hours"] = float(tz_offset_hours)
        if tz_label is not None:
            self._cfg["display_timezone_label"] = str(tz_label)

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

    @property
    def tz_offset_hours(self) -> float:
        return float(self._cfg.get("display_timezone_offset_hours", 3.0))

    @property
    def tz_label(self) -> str:
        return str(self._cfg.get("display_timezone_label", "UTC+3"))

    def _wrap(self, title: str, lines: List[str], event: BaseNotificationEvent) -> str:
        sample_prefix = "🧪 <b>[SAMPLE]</b> " if event.is_sample else ""
        body = f"{sample_prefix}{title}\n\n" + "\n".join(lines)
        footer = format_timestamp_footer(
            dt=event.timestamp,
            tz_offset_hours=self.tz_offset_hours,
            tz_label=self.tz_label,
            account_number=self.account_number
        )
        return sanitize_sensitive_text(body + footer)

    def render(self, event: BaseNotificationEvent) -> str:
        et = event.event_type
        if isinstance(event, TradeSetupEvent) or et == NotificationEventType.TRADE_SETUP:
            return self._render_trade_setup(event)  # type: ignore[arg-type]
        if isinstance(event, TradeOpenedEvent) or et == NotificationEventType.TRADE_OPENED:
            return self._render_trade_opened(event)  # type: ignore[arg-type]
        if isinstance(event, BreakEvenMovedEvent) or et == NotificationEventType.BREAK_EVEN_MOVED:
            return self._render_break_even(event)  # type: ignore[arg-type]
        if isinstance(event, PartialClosedEvent) or et == NotificationEventType.PARTIAL_CLOSED:
            return self._render_partial_closed(event)  # type: ignore[arg-type]
        if isinstance(event, TargetHitEvent) or et == NotificationEventType.TARGET_HIT:
            return self._render_target_hit(event)  # type: ignore[arg-type]
        if isinstance(event, StopLossHitEvent) or et == NotificationEventType.STOP_LOSS_HIT:
            return self._render_stop_loss_hit(event)  # type: ignore[arg-type]
        if isinstance(event, OrderRejectedEvent) or et == NotificationEventType.ORDER_REJECTED:
            return self._render_order_rejected(event)  # type: ignore[arg-type]
        if isinstance(event, SetupSkippedEvent) or et == NotificationEventType.SETUP_SKIPPED:
            return self._render_setup_skipped(event)  # type: ignore[arg-type]
        if isinstance(event, BotStartedEvent) or et == NotificationEventType.BOT_STARTED:
            return self._render_bot_started(event)  # type: ignore[arg-type]
        if isinstance(event, BotPausedEvent) or et == NotificationEventType.BOT_PAUSED:
            return self._render_bot_paused(event)  # type: ignore[arg-type]
        if isinstance(event, BotResumedEvent) or et == NotificationEventType.BOT_RESUMED:
            return self._render_bot_resumed(event)  # type: ignore[arg-type]
        if isinstance(event, BotStoppedEvent) or et == NotificationEventType.BOT_STOPPED:
            return self._render_bot_stopped(event)  # type: ignore[arg-type]
        if isinstance(event, ConnectionLostEvent) or et == NotificationEventType.CONNECTION_LOST:
            return self._render_connection_lost(event)  # type: ignore[arg-type]
        if isinstance(event, ConnectionRestoredEvent) or et == NotificationEventType.CONNECTION_RESTORED:
            return self._render_connection_restored(event)  # type: ignore[arg-type]
        if isinstance(event, SpreadWarningEvent) or et == NotificationEventType.SPREAD_WARNING:
            return self._render_spread_warning(event)  # type: ignore[arg-type]
        if isinstance(event, DailyLossWarningEvent) or et == NotificationEventType.DAILY_LOSS_WARNING:
            return self._render_daily_loss_warning(event)  # type: ignore[arg-type]
        if isinstance(event, DailyLossLimitHitEvent) or et == NotificationEventType.DAILY_LOSS_LIMIT_HIT:
            return self._render_daily_loss_limit_hit(event)  # type: ignore[arg-type]
        if isinstance(event, DrawdownWarningEvent) or et == NotificationEventType.DRAWDOWN_WARNING:
            return self._render_drawdown_warning(event)  # type: ignore[arg-type]
        if isinstance(event, MarginWarningEvent) or et == NotificationEventType.MARGIN_WARNING:
            return self._render_margin_warning(event)  # type: ignore[arg-type]
        if isinstance(event, NewsBlackoutEvent) or et == NotificationEventType.NEWS_BLACKOUT:
            return self._render_news_blackout(event)  # type: ignore[arg-type]
        if isinstance(event, SessionOpenEvent) or et == NotificationEventType.SESSION_OPEN:
            return self._render_session_open(event)  # type: ignore[arg-type]
        if isinstance(event, SessionCloseEvent) or et == NotificationEventType.SESSION_CLOSE:
            return self._render_session_close(event)  # type: ignore[arg-type]
        if isinstance(event, DailySummaryEvent) or et == NotificationEventType.DAILY_SUMMARY:
            return self._render_daily_summary(event)  # type: ignore[arg-type]
        if isinstance(event, WeeklySummaryEvent) or et == NotificationEventType.WEEKLY_SUMMARY:
            return self._render_weekly_summary(event)  # type: ignore[arg-type]
        if isinstance(event, WarmupProgressEvent) or et == NotificationEventType.WARMUP_PROGRESS:
            return self._render_warmup_progress(event)  # type: ignore[arg-type]
        if isinstance(event, ConfigChangedEvent) or et == NotificationEventType.CONFIG_CHANGED:
            return self._render_config_changed(event)  # type: ignore[arg-type]
        if isinstance(event, UnauthorizedAccessEvent) or et == NotificationEventType.UNAUTHORIZED_ACCESS:
            return self._render_unauthorized_access(event)  # type: ignore[arg-type]
        if isinstance(event, HealthOKEvent) or et == NotificationEventType.HEALTH_OK:
            return self._render_health_ok(event)  # type: ignore[arg-type]
        return self._wrap("ℹ️ <b>NOTIFICATION</b>", [f"• Event: <code>{esc(et.value)}</code>"], event)

    def _render_trade_setup(self, e: TradeSetupEvent) -> str:
        dir_icon = "🟢" if "BUY" in e.direction.upper() else "🔴"
        title = f"🎯 <b>TRADE SETUP — {esc(e.symbol)} {esc(e.direction.upper())}</b>"
        lines = [
            f"{dir_icon} <b>Entry:</b> <code>{esc(e.direction.upper())} @ {fmt_price(e.entry_price, e.digits)}</code>",
            f"🛑 <b>Stop Loss:</b> <code>{fmt_price(e.stop_loss, e.digits)}</code> (<code>{e.sl_distance_pips:.1f} {esc(e.distance_unit)}</code>)",
            f"🎯 <b>Take Profit:</b> <code>{fmt_price(e.take_profit, e.digits)}</code> (<code>{e.tp_distance_pips:.1f} {esc(e.distance_unit)}</code>) • <b>R:R:</b> <code>1:{e.rr_ratio:.2f}</code>",
            f"📦 <b>Size & Risk:</b> <code>{e.lots:.2f} lots</code> • <code>{fmt_money(e.risk_usd)} ({fmt_pct(e.risk_pct)})</code>",
            f"🧭 <b>Context:</b> <code>{esc(e.session)}</code> • HTF Bias: <code>{esc(e.htf_bias)}</code> • Score: <code>{e.confidence_score:.0f}/100</code>",
            f"💡 <b>Reason:</b> {esc(e.reason)}",
        ]
        return self._wrap(title, lines, e)

    def _render_trade_opened(self, e: TradeOpenedEvent) -> str:
        title = f"⚡ <b>TRADE OPENED — {esc(e.symbol)} #{e.ticket}</b>"
        lines = [
            f"📌 <b>Position:</b> <code>{esc(e.direction.upper())} {e.lots:.2f} lots</code> @ <code>{fmt_price(e.fill_price, e.digits)}</code>",
            f"📏 <b>Slippage:</b> <code>{e.slippage_points:+.1f} pts</code> (Req: <code>{fmt_price(e.requested_price or e.fill_price, e.digits)}</code>)",
            f"🛡️ <b>Protection:</b> SL <code>{fmt_price(e.stop_loss, e.digits)}</code> • TP <code>{fmt_price(e.take_profit, e.digits)}</code>",
            f"💵 <b>At Risk:</b> <code>{fmt_money(e.risk_usd)} ({fmt_pct(e.risk_pct)})</code> • Ticket <code>#{e.ticket}</code>",
        ]
        return self._wrap(title, lines, e)

    def _render_break_even(self, e: BreakEvenMovedEvent) -> str:
        title = f"🛡️ <b>PROTECTED (BREAK-EVEN) — {esc(e.symbol)} #{e.ticket}</b>"
        lines = [
            f"🔒 <b>Action:</b> Stop Loss moved to Break-Even (+buffer)",
            f"🛑 <b>New SL:</b> <code>{fmt_price(e.new_sl, e.digits)}</code> (Entry: <code>{fmt_price(e.entry_price, e.digits)}</code>)",
            f"📊 <b>Locked Gain:</b> <code>{fmt_r(e.locked_r)}</code> risk-free on Ticket <code>#{e.ticket}</code>",
        ]
        return self._wrap(title, lines, e)

    def _render_partial_closed(self, e: PartialClosedEvent) -> str:
        title = f"💰 <b>PARTIAL TP BANKED — {esc(e.symbol)} #{e.ticket}</b>"
        sl_note = f" • Runner SL at <code>{fmt_price(e.new_sl, e.digits)}</code>" if e.new_sl else ""
        lines = [
            f"📦 <b>Volume:</b> Closed <code>{e.closed_lots:.2f} lots</code> • Remaining <code>{e.remaining_lots:.2f} lots</code>",
            f"💵 <b>Realized P&L:</b> <code>{fmt_money(e.realized_pnl_usd, signed=True)}</code> (<code>{fmt_r(e.realized_r)}</code>)",
            f"🏃 <b>Runner Status:</b> Active on Ticket <code>#{e.ticket}</code>{sl_note}",
        ]
        return self._wrap(title, lines, e)

    def _render_target_hit(self, e: TargetHitEvent) -> str:
        title = f"🎉 <b>TARGET HIT — {esc(e.symbol)} #{e.ticket}</b>"
        lines = [
            f"✅ <b>Result:</b> Closed <code>{esc(e.direction.upper())}</code> @ <code>{fmt_price(e.close_price, e.digits)}</code>",
            f"💵 <b>Net P&L:</b> <code>{fmt_money(e.pnl_usd, signed=True)}</code> (<code>{fmt_r(e.pnl_r)}</code>)",
            f"⏱️ <b>Duration:</b> <code>{esc(e.duration_str)}</code>",
            f"📈 <b>Today's Running Total:</b> <code>{fmt_money(e.daily_running_pnl_usd, signed=True)}</code> (<code>{fmt_r(e.daily_running_r)}</code>)",
        ]
        return self._wrap(title, lines, e)

    def _render_stop_loss_hit(self, e: StopLossHitEvent) -> str:
        title = f"🛑 <b>STOP LOSS HIT — {esc(e.symbol)} #{e.ticket}</b>"
        lines = [
            f"📌 <b>Result:</b> Closed <code>{esc(e.direction.upper())}</code> @ <code>{fmt_price(e.close_price, e.digits)}</code>",
            f"📉 <b>Realized Loss:</b> <code>{fmt_money(e.loss_usd, signed=True)}</code> (<code>{fmt_r(e.loss_r)}</code>) • Duration: <code>{esc(e.duration_str)}</code>",
            f"🛡️ <b>Daily Loss Used:</b> <code>{fmt_pct(e.daily_loss_used_pct)}</code> of <code>{fmt_pct(e.daily_loss_cap_pct)}</code> daily cap",
        ]
        return self._wrap(title, lines, e)

    def _render_order_rejected(self, e: OrderRejectedEvent) -> str:
        plain = map_retcode_to_plain_english(e.retcode, e.reason)
        title = f"🚫 <b>ORDER REJECTED — {esc(e.symbol)} {esc(e.direction.upper())}</b>"
        lines = [
            f"⚠️ <b>Why:</b> {esc(plain)}",
            f"🤖 <b>Bot Action:</b> {esc(e.bot_action)}",
            f"🛡️ <b>Open Trades Affected:</b> <code>{'YES' if e.open_trades_affected else 'NO'}</code>",
        ]
        return self._wrap(title, lines, e)

    def _render_setup_skipped(self, e: SetupSkippedEvent) -> str:
        title = f"⏭️ <b>SETUP SKIPPED — {esc(e.symbol)} {esc(e.direction.upper())}</b>"
        lines = [
            f"🛡️ <b>Why Blocked:</b> {esc(e.reason)}",
            f"📦 <b>Min Lot Risk:</b> <code>{e.min_lot:.2f} lots</code> risks <code>{fmt_pct(e.risk_pct_at_min_lot)}</code> vs <code>{fmt_pct(e.risk_cap_pct)}</code> cap",
            f"💵 <b>Balance Needed:</b> ~<code>{fmt_money(e.required_balance_usd)}</code> (Current: <code>{fmt_money(e.current_balance_usd)}</code>) or use a Cent/Micro account",
        ]
        return self._wrap(title, lines, e)

    def _render_bot_started(self, e: BotStartedEvent) -> str:
        title = "🟢 <b>BOT STARTED & READY</b>"
        syms = ", ".join(e.symbols[:6])
        fast_str = "ON" if e.fast_mode_enabled else "OFF"
        lines = [
            f"🎮 <b>Account Mode:</b> <code>{esc(e.mode)}</code> • Profile: <code>{esc(e.profile)}</code>",
            f"📊 <b>Watched Symbols:</b> <code>{esc(syms)}</code>",
            f"⏱️ <b>Active Session:</b> <code>{esc(e.session_name)}</code> • Fast Mode: <code>{fast_str}</code>",
            f"✅ <b>Status:</b> All risk guardrails and pre-trade checks active",
        ]
        return self._wrap(title, lines, e)

    def _render_bot_paused(self, e: BotPausedEvent) -> str:
        title = "⏸️ <b>BOT PAUSED</b>"
        lines = [
            f"📋 <b>Reason:</b> {esc(e.reason)}",
            f"🤖 <b>Bot Action:</b> New trade entries are paused",
            f"🛡️ <b>Open Positions ({e.open_positions_count}):</b> Remain protected by server-side SL/TP and exit manager",
        ]
        return self._wrap(title, lines, e)

    def _render_bot_resumed(self, e: BotResumedEvent) -> str:
        title = "▶️ <b>BOT RESUMED</b>"
        lines = [
            f"✅ <b>Status:</b> Market scanning and trade execution active",
            f"🎮 <b>Mode:</b> <code>{esc(e.mode)}</code> • Session: <code>{esc(e.active_session)}</code>",
            f"📦 <b>Open Positions:</b> <code>{e.open_positions_count}</code>",
        ]
        return self._wrap(title, lines, e)

    def _render_bot_stopped(self, e: BotStoppedEvent) -> str:
        title = "🔴 <b>BOT STOPPED (SAFE SHUTDOWN)</b>"
        lines = [
            f"📋 <b>Reason:</b> {esc(e.reason)}",
            f"🧹 <b>Pending Orders Cancelled:</b> <code>{e.cancelled_pendings}</code>",
            f"🛡️ <b>Open Positions ({e.open_positions_protected}):</b> Left protected by server-side SL/TP on MT5",
        ]
        return self._wrap(title, lines, e)

    def _render_connection_lost(self, e: ConnectionLostEvent) -> str:
        title = "🚨 <b>CRITICAL — MT5 CONNECTION LOST</b>"
        lines = [
            f"⚠️ <b>Impact:</b> {esc(e.details)}",
            f"🛡️ <b>Protected:</b> Server-side SL/TP remains active on all <code>{e.open_positions_count}</code> open position(s)",
            f"🤖 <b>Bot Action:</b> Entered Safe State — new trade entries halted; auto-reconnect running",
            f"👤 <b>Action Needed:</b> {esc(e.user_action_required)}",
        ]
        return self._wrap(title, lines, e)

    def _render_connection_restored(self, e: ConnectionRestoredEvent) -> str:
        title = "✅ <b>RECOVERED — MT5 CONNECTION RESTORED</b>"
        sync_label = "IN SYNC" if e.in_sync else "DISCREPANCY DETECTED"
        lines = [
            f"🔌 <b>Downtime:</b> <code>{e.downtime_seconds:.1f}s</code>",
            f"🔄 <b>Reconciliation:</b> <code>{sync_label}</code> ({e.reconciled_positions} open position(s) verified)",
            f"📋 <b>Details:</b> {esc(e.discrepancy_note)}",
        ]
        return self._wrap(title, lines, e)

    def _render_spread_warning(self, e: SpreadWarningEvent) -> str:
        repeat_str = f" (occurred {e.repeat_count}x in {e.repeat_window_min}m)" if e.repeat_count > 1 else ""
        title = f"⚠️ <b>SPREAD WARNING — {esc(e.symbol)}{esc(repeat_str)}</b>"
        lines = [
            f"📏 <b>What Happened:</b> Spread widened to <code>{e.current_spread_pts:.1f} pts</code> (normal: <code>{e.normal_spread_pts:.1f} pts</code>)",
            f"🤖 <b>Bot Action:</b> {esc(e.bot_action)}",
            f"🛡️ <b>Open Trades:</b> {esc(e.open_trades_affected)}",
        ]
        return self._wrap(title, lines, e)

    def _render_daily_loss_warning(self, e: DailyLossWarningEvent) -> str:
        title = "⚠️ <b>DAILY LOSS WARNING (75% OF CAP)</b>"
        lines = [
            f"📉 <b>What Happened:</b> Daily loss reached <code>{fmt_pct(e.daily_loss_used_pct)}</code> (<code>{fmt_money(e.daily_loss_usd, signed=True)}</code>) of <code>{fmt_pct(e.daily_loss_cap_pct)}</code> cap",
            f"💵 <b>Remaining Daily Buffer:</b> <code>{fmt_money(e.remaining_budget_usd)}</code>",
            f"🤖 <b>Bot Action:</b> {esc(e.bot_action)}",
            f"🛡️ <b>Open Trades:</b> {esc(e.open_trades_affected)}",
        ]
        return self._wrap(title, lines, e)

    def _render_daily_loss_limit_hit(self, e: DailyLossLimitHitEvent) -> str:
        title = "🚨 <b>CRITICAL — DAILY LOSS LIMIT REACHED</b>"
        lines = [
            f"🛑 <b>Impact:</b> Daily loss hit <code>{fmt_pct(e.daily_loss_used_pct)}</code> (<code>{fmt_money(e.daily_loss_usd, signed=True)}</code>) vs <code>{fmt_pct(e.daily_loss_cap_pct)}</code> cap",
            f"🛡️ <b>Protected:</b> Liquidated <code>{e.positions_closed}</code> position(s); server-side SL/TP active",
            f"🤖 <b>Bot Action:</b> New trade entries locked for the remainder of the UTC trading day",
            f"👤 <b>Action Needed:</b> {esc(e.user_action_required)}",
        ]
        return self._wrap(title, lines, e)

    def _render_drawdown_warning(self, e: DrawdownWarningEvent) -> str:
        icon = "🚨" if e.is_hard_breach else "⚠️"
        label = "CRITICAL — MAX DRAWDOWN BREACHED" if e.is_hard_breach else "DRAWDOWN WARNING"
        title = f"{icon} <b>{label}</b>"
        lines = [
            f"📉 <b>Drawdown:</b> <code>{fmt_pct(e.current_dd_pct)}</code> used of <code>{fmt_pct(e.max_dd_cap_pct)}</code> max limit",
            f"💎 <b>Equity vs Peak:</b> <code>{fmt_money(e.equity_usd)}</code> / Peak <code>{fmt_money(e.peak_equity_usd)}</code>",
            f"🤖 <b>Bot Action:</b> {esc(e.bot_action)}",
            f"👤 <b>Action Needed:</b> {esc(e.user_action_required)}",
        ]
        return self._wrap(title, lines, e)

    def _render_margin_warning(self, e: MarginWarningEvent) -> str:
        is_crit = e.margin_level_pct <= e.critical_floor_pct
        title = "🚨 <b>CRITICAL — LOW MARGIN FLOOR</b>" if is_crit else "⚠️ <b>MARGIN LEVEL WARNING</b>"
        lines = [
            f"⚖️ <b>Margin Level:</b> <code>{fmt_pct(e.margin_level_pct)}</code> (Entry Floor: <code>{fmt_pct(e.entry_floor_pct)}</code> • Critical: <code>{fmt_pct(e.critical_floor_pct)}</code>)",
            f"💵 <b>Used / Free Margin:</b> <code>{fmt_money(e.used_margin_usd)}</code> / <code>{fmt_money(e.free_margin_usd)}</code>",
            f"🤖 <b>Bot Action:</b> {esc(e.bot_action)}",
            f"🛡️ <b>Positions Flattened:</b> <code>{e.flattened_positions}</code>",
        ]
        return self._wrap(title, lines, e)

    def _render_news_blackout(self, e: NewsBlackoutEvent) -> str:
        title = "📰 <b>HIGH-IMPACT NEWS WINDOW</b>"
        syms = ", ".join(e.affected_symbols[:6])
        lines = [
            f"📅 <b>Event:</b> <code>{esc(e.event_title)}</code> (<code>{esc(e.window_str)}</code>)",
            f"🎯 <b>Affected Symbols:</b> <code>{esc(syms)}</code>",
            f"🤖 <b>Bot Action:</b> {esc(e.bot_action)}",
            f"🛡️ <b>Open Trades:</b> {esc(e.open_trades_affected)}",
        ]
        return self._wrap(title, lines, e)

    def _render_session_open(self, e: SessionOpenEvent) -> str:
        title = f"🔔 <b>SESSION OPEN — {esc(e.session_name.upper())}</b>"
        syms = ", ".join(e.watched_symbols[:8])
        lines = [
            f"🕒 <b>Window:</b> <code>{esc(e.window_utc)}</code>",
            f"🎯 <b>Watched Symbols:</b> <code>{esc(syms)}</code>",
        ]
        if e.asia_high is not None and e.asia_low is not None:
            rng_str = f" ({e.asia_range_pips:.1f} pips)" if e.asia_range_pips is not None else ""
            lines.append(
                f"📐 <b>Asia Range ({esc(e.asia_symbol)}):</b> High <code>{fmt_price(e.asia_high, e.digits)}</code> • Low <code>{fmt_price(e.asia_low, e.digits)}</code>{esc(rng_str)}"
            )
        return self._wrap(title, lines, e)

    def _render_session_close(self, e: SessionCloseEvent) -> str:
        title = f"🏁 <b>SESSION CLOSE — {esc(e.session_name.upper())}</b>"
        lines = [
            f"📊 <b>Session Result:</b> <code>{e.session_trades}</code> trade(s) • <code>{fmt_money(e.session_pnl_usd, signed=True)}</code> (<code>{fmt_r(e.session_pnl_r)}</code>)",
            f"📦 <b>Open Positions Carried:</b> <code>{e.open_positions_count}</code>",
            f"⏭️ <b>Next Session:</b> <code>{esc(e.next_session_name)}</code> starts at <code>{esc(e.next_session_start_utc)}</code>",
        ]
        return self._wrap(title, lines, e)

    def _render_daily_summary(self, e: DailySummaryEvent) -> str:
        title = f"📊 <b>DAILY TRADING SUMMARY — {esc(e.date_str)}</b>"
        filt_parts = [f"{k}: {v}" for k, v in e.blocked_by_filters.items() if v > 0]
        filt_str = ", ".join(filt_parts) if filt_parts else "None"
        lines = [
            f"🔢 <b>Trades:</b> <code>{e.trades_count}</code> (Wins: <code>{e.wins}</code> • Losses: <code>{e.losses}</code> • Win Rate: <code>{fmt_pct(e.win_rate_pct)}</code>)",
            f"💵 <b>Net P&L:</b> <code>{fmt_money(e.net_pnl_usd, signed=True)}</code> (<code>{fmt_r(e.net_pnl_r)}</code>)",
            f"🏆 <b>Best / Worst Trade:</b> <code>{fmt_r(e.best_r)}</code> / <code>{fmt_r(e.worst_r)}</code>",
            f"📉 <b>Max Drawdown:</b> <code>{fmt_pct(e.max_dd_pct)}</code> • Equity: <code>{fmt_money(e.equity_usd)}</code>",
            f"🛡️ <b>Blocked by Risk Filters:</b> <code>{esc(filt_str)}</code>",
        ]
        return self._wrap(title, lines, e)

    def _render_weekly_summary(self, e: WeeklySummaryEvent) -> str:
        title = f"📅 <b>WEEKLY PERFORMANCE SUMMARY — {esc(e.week_label)}</b>"
        filt_parts = [f"{k}: {v}" for k, v in e.blocked_by_filters.items() if v > 0]
        filt_str = ", ".join(filt_parts) if filt_parts else "None"
        lines = [
            f"🔢 <b>Total Trades:</b> <code>{e.trades_count}</code> (Wins: <code>{e.wins}</code> • Losses: <code>{e.losses}</code> • Win Rate: <code>{fmt_pct(e.win_rate_pct)}</code>)",
            f"💵 <b>Weekly Net P&L:</b> <code>{fmt_money(e.net_pnl_usd, signed=True)}</code> (<code>{fmt_r(e.net_pnl_r)}</code>) • PF: <code>{e.profit_factor:.2f}</code>",
            f"🏆 <b>Best / Worst Trade:</b> <code>{fmt_r(e.best_r)}</code> / <code>{fmt_r(e.worst_r)}</code> • Max DD: <code>{fmt_pct(e.max_dd_pct)}</code>",
            f"🛡️ <b>Blocked by Risk Filters:</b> <code>{esc(filt_str)}</code>",
        ]
        return self._wrap(title, lines, e)

    def _render_warmup_progress(self, e: WarmupProgressEvent) -> str:
        if e.is_ready or e.loaded_bars >= e.required_bars:
            title = f"✅ <b>HISTORY READY — {esc(e.symbol)}</b>"
            lines = [
                f"📊 <b>Timeframe:</b> <code>{esc(e.timeframe)}</code> synchronized (<code>{e.loaded_bars}/{e.required_bars} bars</code>)",
                f"🟢 <b>Status:</b> Warm-up complete — scanning for qualified setups",
            ]
        else:
            title = f"⏳ <b>WARMING UP HISTORY — {esc(e.symbol)}</b>"
            lines = [
                f"📊 <b>Loading {esc(e.symbol)} ({esc(e.timeframe)}) history:</b> <code>{e.loaded_bars}/{e.required_bars} bars</code>",
                f"ℹ️ <b>Note:</b> Bot will notify once when history is ready (no repeat alerts)",
            ]
        return self._wrap(title, lines, e)

    def _render_config_changed(self, e: ConfigChangedEvent) -> str:
        title = "⚙️ <b>CONFIGURATION UPDATED</b>"
        lines = [
            f"🔧 <b>Setting:</b> <code>{esc(e.setting_name)}</code>",
            f"🔄 <b>Change:</b> <code>{esc(e.old_value)}</code> → <code>{esc(e.new_value)}</code>",
            f"👤 <b>Source:</b> <code>{esc(e.changed_by)}</code>",
        ]
        return self._wrap(title, lines, e)

    def _render_unauthorized_access(self, e: UnauthorizedAccessEvent) -> str:
        title = "🔒 <b>SECURITY ALERT — UNAUTHORIZED ACCESS BLOCKED</b>"
        lines = [
            f"🚫 <b>Rejected Command:</b> <code>{esc(e.attempted_command)}</code>",
            f"👤 <b>Sender:</b> <code>{esc(e.sender_name)}</code> (Chat <code>{esc(e.masked_sender_id)}</code>)",
            f"🛡️ <b>Status:</b> Command ignored; only authorized operator Chat ID is permitted",
        ]
        return self._wrap(title, lines, e)

    def _render_health_ok(self, e: HealthOKEvent) -> str:
        title = "🟢 <b>RUNNING SMOOTHLY — PERIODIC CHECK</b>"
        bridge_str = "Connected" if e.bridge_connected else "Standby"
        lines = [
            f"🔌 <b>Bridge:</b> <code>{bridge_str}</code> • Uptime: <code>{esc(e.uptime_str)}</code>",
            f"📦 <b>Open Positions:</b> <code>{e.open_positions_count}</code> • Session: <code>{esc(e.active_session)}</code>",
            f"💵 <b>Today's P&L:</b> <code>{fmt_money(e.today_pnl_usd, signed=True)}</code> (<code>{fmt_r(e.today_pnl_r)}</code>)",
        ]
        return self._wrap(title, lines, e)

    def render_pinned_status_card(self, status_data: Dict[str, Any]) -> str:
        """
        Renders the clean pinned Status Card (edited in place).
        No raw dumps or stack traces.
        """
        state = str(status_data.get("state", "RUNNING")).upper()
        bridge_conn = "Connected" if status_data.get("bridge_connected", True) else "Disconnected"
        mode = str(status_data.get("trade_mode", "DEMO"))
        profile = str(status_data.get("profile", "DEMO"))
        open_count = int(status_data.get("open_positions", 0))
        trades_today = int(status_data.get("trades_today", 0))
        today_pnl = float(status_data.get("today_pnl_usd", 0.0))
        today_r = float(status_data.get("today_pnl_r", 0.0))
        balance = float(status_data.get("balance", 10000.0))
        equity = float(status_data.get("equity", balance))
        daily_loss_pct = float(status_data.get("daily_loss_pct", 0.0))
        max_daily_pct = float(status_data.get("max_daily_loss_percent", 4.0))
        dd_pct = float(status_data.get("drawdown_pct", 0.0))
        max_dd_pct = float(status_data.get("max_total_drawdown_percent", 8.0))
        active_session = str(status_data.get("active_session", "London / NY"))
        next_session = str(status_data.get("next_session", "New York (12:30 UTC)"))
        fast_mode_str = str(status_data.get("fast_mode_state", "OFF"))

        state_emoji = "🟢" if state == "RUNNING" else ("⏸️" if "PAUSE" in state else "🟡")
        lines = [
            f"{state_emoji} <b>SYSTEM STATUS CARD — {esc(state)}</b>\n",
            f"🔌 <b>Bridge:</b> <code>{bridge_conn}</code> • <b>Mode:</b> <code>{esc(mode)} ({esc(profile)})</code> • <b>Fast Mode:</b> <code>{esc(fast_mode_str)}</code>",
            f"💵 <b>Balance / Equity:</b> <code>{fmt_money(balance)}</code> / <code>{fmt_money(equity)}</code>",
            f"📦 <b>Open Positions:</b> <code>{open_count}</code> • <b>Trades Today:</b> <code>{trades_today}</code>",
            f"📈 <b>Today's P&L:</b> <code>{fmt_money(today_pnl, signed=True)}</code> (<code>{fmt_r(today_r)}</code>)",
            f"🛡️ <b>Risk Used:</b> Daily <code>{fmt_pct(daily_loss_pct)}/{fmt_pct(max_daily_pct)}</code> • DD <code>{fmt_pct(dd_pct)}/{fmt_pct(max_dd_pct)}</code>",
            f"⏱️ <b>Session:</b> <code>{esc(active_session)}</code> • <b>Next:</b> <code>{esc(next_session)}</code>",
        ]
        footer = format_timestamp_footer(
            tz_offset_hours=self.tz_offset_hours,
            tz_label=self.tz_label,
            account_number=self.account_number
        )
        return sanitize_sensitive_text("\n".join(lines) + footer)


def build_sample_events() -> List[BaseNotificationEvent]:
    """Builds 1 sample of every structured event type (labelled is_sample=True) for /preview_alerts."""
    fixed_dt = datetime(2026, 11, 3, 14, 30, 0, tzinfo=timezone.utc)
    return [
        TradeSetupEvent(
            timestamp=fixed_dt, is_sample=True, symbol="XAUUSD", direction="BUY",
            entry_price=2650.50, stop_loss=2645.50, take_profit=2662.50,
            sl_distance_pips=50.0, tp_distance_pips=120.0, distance_unit="pips",
            rr_ratio=2.40, lots=0.10, risk_usd=50.0, risk_pct=0.50,
            session="London / NY Overlap", htf_bias="BULLISH",
            reason="Confirmed BOS after Asian Low sweep + M15 Bullish FVG retest",
            confidence_score=88.0, digits=2
        ),
        TradeOpenedEvent(
            timestamp=fixed_dt, is_sample=True, symbol="XAUUSD", direction="BUY",
            ticket=501024, fill_price=2650.55, requested_price=2650.50,
            slippage_points=0.5, lots=0.10, stop_loss=2645.50, take_profit=2662.50,
            risk_usd=50.0, risk_pct=0.50, digits=2
        ),
        BreakEvenMovedEvent(
            timestamp=fixed_dt, is_sample=True, symbol="XAUUSD", ticket=501024,
            direction="BUY", entry_price=2650.55, new_sl=2650.80, locked_r=0.05, digits=2
        ),
        PartialClosedEvent(
            timestamp=fixed_dt, is_sample=True, symbol="XAUUSD", ticket=501024,
            direction="BUY", closed_lots=0.05, remaining_lots=0.05,
            realized_pnl_usd=25.0, realized_r=1.0, new_sl=2650.80, digits=2
        ),
        TargetHitEvent(
            timestamp=fixed_dt, is_sample=True, symbol="XAUUSD", ticket=501024,
            direction="BUY", close_price=2662.50, pnl_usd=85.0, pnl_r=1.70,
            duration_str="38m", daily_running_pnl_usd=145.0, daily_running_r=2.90, digits=2
        ),
        StopLossHitEvent(
            timestamp=fixed_dt, is_sample=True, symbol="EURUSD", ticket=501029,
            direction="SELL", close_price=1.08650, loss_usd=-50.0, loss_r=-1.00,
            duration_str="21m", daily_loss_used_pct=1.00, daily_loss_cap_pct=4.00, digits=5
        ),
        OrderRejectedEvent(
            timestamp=fixed_dt, is_sample=True, symbol="XAUUSD", direction="BUY",
            retcode=10019, reason="No money"
        ),
        SetupSkippedEvent(
            timestamp=fixed_dt, is_sample=True, symbol="XAUUSD", direction="BUY",
            reason="Min lot (0.01) risks 5.60% ($28.00), exceeding 1.00% risk cap",
            required_balance_usd=2800.0, current_balance_usd=500.0, min_lot=0.01,
            risk_pct_at_min_lot=5.60, risk_cap_pct=1.00
        ),
        BotStartedEvent(timestamp=fixed_dt, is_sample=True),
        BotPausedEvent(timestamp=fixed_dt, is_sample=True),
        BotResumedEvent(timestamp=fixed_dt, is_sample=True),
        BotStoppedEvent(timestamp=fixed_dt, is_sample=True),
        ConnectionLostEvent(timestamp=fixed_dt, is_sample=True),
        ConnectionRestoredEvent(timestamp=fixed_dt, is_sample=True),
        SpreadWarningEvent(timestamp=fixed_dt, is_sample=True),
        DailyLossWarningEvent(timestamp=fixed_dt, is_sample=True),
        DailyLossLimitHitEvent(timestamp=fixed_dt, is_sample=True),
        DrawdownWarningEvent(timestamp=fixed_dt, is_sample=True),
        MarginWarningEvent(timestamp=fixed_dt, is_sample=True),
        NewsBlackoutEvent(timestamp=fixed_dt, is_sample=True),
        SessionOpenEvent(
            timestamp=fixed_dt, is_sample=True, session_name="London",
            window_utc="06:00 - 12:00 UTC", watched_symbols=["EURUSD", "GBPUSD", "XAUUSD"],
            asia_high=1.08620, asia_low=1.08380, asia_range_pips=24.0, asia_symbol="EURUSD", digits=5
        ),
        SessionCloseEvent(timestamp=fixed_dt, is_sample=True),
        DailySummaryEvent(timestamp=fixed_dt, is_sample=True),
        WeeklySummaryEvent(timestamp=fixed_dt, is_sample=True),
        WarmupProgressEvent(timestamp=fixed_dt, is_sample=True),
        ConfigChangedEvent(timestamp=fixed_dt, is_sample=True),
        UnauthorizedAccessEvent(timestamp=fixed_dt, is_sample=True),
        HealthOKEvent(timestamp=fixed_dt, is_sample=True),
    ]
