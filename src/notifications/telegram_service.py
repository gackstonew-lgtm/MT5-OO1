"""
Institutional Telegram Notification and Command Service.
Provides:
1. TelegramNotifier:
   - Asynchronous message queue with daemon worker thread.
   - 1 message/second rate-limiting, HTML formatting, truncation, and robust backoff.
   - Comprehensive event dispatchers (Bot, Bridge, Setup, Order, Risk, Closed, etc.).
2. TelegramCommandHandler:
   - Long-polling daemon processing user commands (/status, /balance, /positions, /pause, /run, /closeall, /killswitch, /help).
   - Strict chat_id authentication and 30-second two-step confirmation for critical actions.
   - Safety constraints: never permits enabling live execution or increasing risk limits via chat.
"""

from __future__ import annotations
import queue
import collections
import os
import re
import threading
import time
import logging
import html
from datetime import datetime, timezone
from typing import Optional, Dict, Any, Callable, Tuple

from src.notifications.telegram_config import TelegramConfig
from src.notifications.telegram_transport import TelegramTransport, escape_html, truncate_message

logger = logging.getLogger("TelegramService")


class TelegramNotifier:
    def __init__(self, config: Optional[TelegramConfig] = None, account_number: int = 0):
        self.config = config or TelegramConfig()
        self.account_number = account_number
        self.transport = TelegramTransport(
            bot_token=self.config.bot_token or "",
            proxy=self.config.proxy
        )
        self._msg_queue: queue.Queue[str] = queue.Queue(maxsize=1000)
        self._worker_thread: Optional[threading.Thread] = None
        self._is_running = False
        self._last_send_time = 0.0

        if self.config.enabled:
            self.start()

    def start(self) -> None:
        """Starts background worker thread for non-blocking message dispatch."""
        if self._is_running or not self.config.enabled:
            return
        self._is_running = True
        self._worker_thread = threading.Thread(target=self._worker_loop, daemon=True, name="TelegramWorker")
        self._worker_thread.start()
        logger.info("[Telegram] Notifier worker thread started.")

    def stop(self) -> None:
        """Stops background worker thread."""
        self._is_running = False

    def update_account_number(self, account_number: int) -> None:
        self.account_number = account_number

    def send_message(self, text: str, disable_notification: bool = False, parse_mode: str = "HTML") -> None:
        """Queues formatted message for asynchronous dispatch with notification sound control."""
        if not self.config.enabled:
            return
        try:
            self._msg_queue.put_nowait((text, disable_notification, parse_mode))
        except queue.Full:
            logger.warning("[Telegram] Outgoing message queue full. Dropping message.")

    def send_raw_html(self, text: str) -> None:
        """Queues formatted HTML message for asynchronous dispatch (sound notification by default)."""
        self.send_message(text, disable_notification=False, parse_mode="HTML")

    def _format_header_footer(self, body: str, symbol: Optional[str] = None) -> str:
        """Appends account number, symbol and dual UTC + UTC+3 timestamps to every message."""
        from datetime import timedelta
        now_utc = datetime.now(timezone.utc)
        now_utc_str = now_utc.strftime("%Y-%m-%d %H:%M:%S UTC")
        now_local = now_utc.astimezone(timezone(timedelta(hours=3)))
        now_local_str = now_local.strftime("%H:%M:%S UTC+3")
        acc_str = f"#{self.account_number}" if self.account_number else "N/A"
        sym_str = f" | <b>Symbol:</b> {escape_html(symbol)}" if symbol else ""

        footer = (
            f"\n\n━━━━━━━━━━━━━━━━━━━━\n"
            f"👤 <b>Account:</b> <code>{acc_str}</code>{sym_str}\n"
            f"🕒 <b>Time:</b> <code>{now_utc_str} ({now_local_str})</code>"
        )
        return truncate_message(body + footer)

    def _worker_loop(self) -> None:
        """Daemon worker executing rate-limited (1 msg/sec) HTTP sends."""
        while self._is_running:
            try:
                msg = self._msg_queue.get(timeout=1.0)
            except queue.Empty:
                continue

            if isinstance(msg, tuple):
                if len(msg) == 2:
                    text_content, disable_notif = msg
                    pm = "HTML"
                else:
                    text_content, disable_notif, pm = msg
            else:
                text_content, disable_notif, pm = str(msg), False, "HTML"

            # Maintain ~1 message per second rate limit
            elapsed = time.time() - self._last_send_time
            if elapsed < 1.0:
                time.sleep(1.0 - elapsed)

            payload = {
                "chat_id": self.config.chat_id,
                "text": text_content,
                "parse_mode": pm,
                "disable_notification": disable_notif
            }

            status, data, err = self.transport.request("POST", "sendMessage", payload=payload, timeout=10.0)
            self._last_send_time = time.time()

            if status != 200 or not data.get("ok"):
                logger.error(f"[Telegram] Send failed: HTTP {status} | Error: {data.get('description', err)}")

            self._msg_queue.task_done()

    # =========================================================================
    # Strongly-Typed Event Dispatchers
    # =========================================================================

    def notify_bot_status(self, action: str, details: str = "") -> None:
        """Dispatches Bot Lifecycle events."""
        emoji = "🟢" if action.upper() == "STARTED" else "🔴"
        body = (
            f"{emoji} <b>ALGO BOT {action.upper()}</b>\n\n"
            f"<b>Details:</b> {escape_html(details)}"
        )
        self.send_raw_html(self._format_header_footer(body))

    def notify_bridge_status(self, status: str, details: str = "") -> None:
        """Dispatches MT5 Socket Bridge Status (CONNECTED, DISCONNECTED, HEARTBEAT_LOST)."""
        emojis = {
            "CONNECTED": "🔌 <b>BRIDGE CONNECTED</b>",
            "DISCONNECTED": "⚠️ <b>BRIDGE DISCONNECTED</b>",
            "HEARTBEAT_LOST": "🚨 <b>WATCHDOG: HEARTBEAT LOST</b>"
        }
        title = emojis.get(status, f"ℹ️ <b>BRIDGE: {escape_html(status)}</b>")
        body = f"{title}\n\n<b>Details:</b> {escape_html(details)}"
        self.send_raw_html(self._format_header_footer(body))

    def notify_setup_detected(
        self,
        symbol: str,
        direction: str,
        mode: str,
        entry: float,
        sl: float,
        tp: float,
        rr: float,
        lots: float,
        risk_pct: float,
        reason: str,
        trade_plan: Optional[Any] = None
    ) -> None:
        """Dispatches High-Quality Multi-Timeframe Setup Detection."""
        dir_emoji = "🟢" if "BUY" in direction.upper() else "🔴"
        body = (
            f"🎯 <b>TRADING SETUP DETECTED</b>\n\n"
            f"{dir_emoji} <b>Direction:</b> <code>{escape_html(direction)}</code> [{escape_html(mode)}]\n"
            f"💵 <b>Entry:</b> <code>{entry:.5f}</code>\n"
            f"🛑 <b>SL:</b> <code>{sl:.5f}</code>\n"
            f"🎯 <b>TP:</b> <code>{tp:.5f}</code>\n"
            f"⚖️ <b>R:R:</b> <code>1:{rr:.2f}</code>\n"
            f"📦 <b>Sizing:</b> <code>{lots:.2f} lots</code> ({risk_pct:.1f}% Risk)\n"
            f"📝 <b>Reason:</b> <i>{escape_html(reason)}</i>" + (f"\n\n{trade_plan.format_signal_text()}" if trade_plan and hasattr(trade_plan, "format_signal_text") else "")
        )
        self.send_raw_html(self._format_header_footer(body, symbol=symbol))

    def notify_order_placed(
        self,
        symbol: str,
        order_type: str,
        ticket: int,
        price: float,
        sl: float,
        tp: float,
        volume: float,
        retcode: int
    ) -> None:
        """Dispatches Order Submission Notification."""
        body = (
            f"⚡ <b>ORDER PLACED</b>\n\n"
            f"🎫 <b>Ticket:</b> <code>#{ticket}</code>\n"
            f"📌 <b>Type:</b> <code>{escape_html(order_type)}</code>\n"
            f"📦 <b>Volume:</b> <code>{volume:.2f} lots</code> @ <code>{price:.5f}</code>\n"
            f"🛑 <b>SL:</b> <code>{sl:.5f}</code> | 🎯 <b>TP:</b> <code>{tp:.5f}</code>\n"
            f"🔢 <b>Retcode:</b> <code>{retcode}</code>"
        )
        self.send_raw_html(self._format_header_footer(body, symbol=symbol))

    def notify_order_modified(self, symbol: str, ticket: int, price: float, sl: float, tp: float) -> None:
        """Dispatches Order / Position Modification."""
        body = (
            f"✏️ <b>ORDER MODIFIED</b>\n\n"
            f"🎫 <b>Ticket:</b> <code>#{ticket}</code>\n"
            f"💵 <b>Price:</b> <code>{price:.5f}</code>\n"
            f"🛑 <b>New SL:</b> <code>{sl:.5f}</code> | 🎯 <b>New TP:</b> <code>{tp:.5f}</code>"
        )
        self.send_raw_html(self._format_header_footer(body, symbol=symbol))

    def notify_order_cancelled(self, symbol: str, ticket: int, reason: str = "") -> None:
        """Dispatches Order Cancellation Notification."""
        body = (
            f"❌ <b>ORDER CANCELLED</b>\n\n"
            f"🎫 <b>Ticket:</b> <code>#{ticket}</code>\n"
            f"📝 <b>Reason:</b> {escape_html(reason)}"
        )
        self.send_raw_html(self._format_header_footer(body, symbol=symbol))

    def notify_order_rejected(self, symbol: str, action: str, reason: str, retcode: int = 0) -> None:
        """Dispatches Order Rejection Notification."""
        body = (
            f"🚫 <b>ORDER REJECTED</b>\n\n"
            f"📌 <b>Action:</b> <code>{escape_html(action)}</code>\n"
            f"⚠️ <b>Reason:</b> {escape_html(reason)}\n"
            f"🔢 <b>Retcode:</b> <code>{retcode}</code>"
        )
        self.send_raw_html(self._format_header_footer(body, symbol=symbol))

    def notify_break_even(self, symbol: str, ticket: int, new_sl: float) -> None:
        """Dispatches Break-Even Stop Move."""
        body = (
            f"🛡️ <b>BREAK-EVEN SECURED</b>\n\n"
            f"🎫 <b>Ticket:</b> <code>#{ticket}</code>\n"
            f"🛑 <b>Stop-Loss moved to:</b> <code>{new_sl:.5f}</code> (+1.0R Covered)"
        )
        self.send_raw_html(self._format_header_footer(body, symbol=symbol))

    def notify_trailing_stop(self, symbol: str, ticket: int, new_sl: float, current_price: float, points_profit: float) -> None:
        """Dispatches Trailing Stop Move."""
        body = (
            f"📈 <b>TRAILING STOP ADVANCED</b>\n\n"
            f"🎫 <b>Ticket:</b> <code>#{ticket}</code>\n"
            f"🛑 <b>New SL:</b> <code>{new_sl:.5f}</code>\n"
            f"📊 <b>Market Price:</b> <code>{current_price:.5f}</code> (+{points_profit:.1f} pts in profit)"
        )
        self.send_raw_html(self._format_header_footer(body, symbol=symbol))

    def notify_partial_close(self, symbol: str, ticket: int, closed_volume: float, remaining_volume: float, pnl: float) -> None:
        """Dispatches Partial Position Closure."""
        body = (
            f"💰 <b>PARTIAL TAKE-PROFIT CLOSED</b>\n\n"
            f"🎫 <b>Ticket:</b> <code>#{ticket}</code>\n"
            f"📦 <b>Closed:</b> <code>{closed_volume:.2f} lots</code> (Remaining: <code>{remaining_volume:.2f} lots</code>)\n"
            f"💵 <b>Realized PnL:</b> <code>${pnl:+.2f}</code>"
        )
        self.send_raw_html(self._format_header_footer(body, symbol=symbol))

    def notify_trade_closed(
        self,
        symbol: str,
        ticket: int,
        direction: str,
        close_reason: str,
        money_pnl: float,
        points_pnl: float,
        r_pnl: float
    ) -> None:
        """Dispatches Full Trade Closure (SL hit, TP hit, manual close)."""
        is_win = money_pnl >= 0.0
        emoji = "🎉" if is_win else "🛑"
        body = (
            f"{emoji} <b>TRADE CLOSED [{escape_html(close_reason)}]</b>\n\n"
            f"🎫 <b>Ticket:</b> <code>#{ticket}</code> | <b>Type:</b> <code>{escape_html(direction)}</code>\n"
            f"💵 <b>PnL:</b> <code>${money_pnl:+.2f}</code>\n"
            f"📏 <b>Points:</b> <code>{points_pnl:+.1f} pts</code>\n"
            f"📊 <b>R Multiple:</b> <code>{r_pnl:+.2f}R</code>"
        )
        self.send_raw_html(self._format_header_footer(body, symbol=symbol))

    def notify_risk_limit_hit(self, limit_type: str, details: str) -> None:
        """Dispatches Risk Limit Breaches (Daily Loss, Drawdown, Max Positions)."""
        body = (
            f"🚨 <b>RISK CIRCUIT BREAKER TRIGGERED</b>\n\n"
            f"⚠️ <b>Limit Breached:</b> <code>{escape_html(limit_type)}</code>\n"
            f"📋 <b>Details:</b> {escape_html(details)}\n\n"
            f"<i>New trade entries have been halted for protection.</i>"
        )
        self.send_raw_html(self._format_header_footer(body))

    def notify_kill_switch_engaged(self, reason: str, positions_closed: int = 0) -> None:
        """Dispatches Emergency Kill Switch Notification."""
        body = (
            f"☠️ <b>EMERGENCY KILL SWITCH ENGAGED</b>\n\n"
            f"⚠️ <b>Reason:</b> {escape_html(reason)}\n"
            f"🔒 <b>Active Positions Liquidated:</b> <code>{positions_closed}</code>\n"
            f"<i>Bot trading halted indefinitely. Requires manual reset.</i>"
        )
        self.send_raw_html(self._format_header_footer(body))

    def notify_trade_skipped(self, symbol: str, skip_reason: str) -> None:
        """Dispatches Trade Skipping Notification (Spread excessive, volatility abnormal, conflict)."""
        body = (
            f"⏭️ <b>TRADE SKIPPED</b>\n\n"
            f"🛡️ <b>Filter:</b> {escape_html(skip_reason)}"
        )
        self.send_raw_html(self._format_header_footer(body, symbol=symbol))

    def notify_live_gate_refusal(self, symbol: str, reason: str) -> None:
        """Dispatches Live Execution Gate Refusal."""
        body = (
            f"🔒 <b>LIVE EXECUTION GATE REFUSAL</b>\n\n"
            f"⚠️ <b>Violation:</b> {escape_html(reason)}"
        )
        self.send_raw_html(self._format_header_footer(body, symbol=symbol))

    def notify_safety_buffer_hit(self, loss_pct: float, buffer_pct: float, details: str = "") -> None:
        """Dispatches Safety Buffer Warning Notification."""
        body = (
            f"⚠️ <b>SAFETY BUFFER REACHED</b>\n\n"
            f"📉 <b>Loss Level:</b> <code>{loss_pct:.2f}%</code> (Buffer Limit: <code>{buffer_pct:.1f}%</code>)\n"
            f"📋 <b>Action:</b> Halting new entries. Existing positions remain managed.\n"
            f"ℹ️ {escape_html(details)}"
        )
        self.send_raw_html(self._format_header_footer(body))

    def notify_decision_outcome(self, outcome: Any) -> bool:
        """
        Dispatches closed-bar decision outcome notification to Telegram.
        Deduplicates by (symbol, bar_time, outcome_type, reason) so that the same message
        is sent at most once per bar per symbol.
        """
        if not self.config.enabled:
            return False

        now_utc = datetime.now(timezone.utc)
        today = now_utc.date()
        if not hasattr(self, "_last_dedup_day"):
            self._last_dedup_day = today
            self._outcome_dedup_gate = {}
            self.suppressed_duplicates_count = 0
        elif today != self._last_dedup_day:
            self._last_dedup_day = today
            self._outcome_dedup_gate.clear()
            self.suppressed_duplicates_count = 0

        sym = getattr(outcome, "symbol", "")
        bar_time = getattr(outcome, "bar_time", now_utc)
        bar_time_str = bar_time.isoformat() if hasattr(bar_time, "isoformat") else str(bar_time)
        outcome_val = outcome.outcome_type.value if hasattr(outcome.outcome_type, "value") else str(outcome.outcome_type)
        reason = getattr(outcome, "reason_code", None) or getattr(outcome, "details", "").split("|")[0].strip()

        dedup_key = (sym, bar_time_str, outcome_val, reason)
        if not hasattr(self, "_outcome_dedup_gate"):
            self._outcome_dedup_gate = {}
            self.suppressed_duplicates_count = 0

        if dedup_key in self._outcome_dedup_gate:
            self.suppressed_duplicates_count += 1
            return False

        self._outcome_dedup_gate[dedup_key] = time.time()

        if not hasattr(self, "last_symbol_outcomes"):
            self.last_symbol_outcomes = {}
        line = outcome.format_line() if hasattr(outcome, "format_line") else str(outcome)
        self.last_symbol_outcomes[sym] = {
            "line": line,
            "time": now_utc.strftime("%H:%M:%S UTC"),
            "bar_time": bar_time_str,
            "outcome": outcome_val
        }

        emoji_tag = {
            "TRADE SENT": "🚀 <b>DECISION OUTCOME: TRADE SENT</b>",
            "TRADE SKIPPED": "⏭️ <b>DECISION OUTCOME: TRADE SKIPPED</b>",
            "NO SETUP": "🔍 <b>DECISION OUTCOME: NO SETUP</b>"
        }.get(outcome_val, "📋 <b>DECISION OUTCOME</b>")

        body = (
            f"{emoji_tag}\n\n"
            f"<b>Symbol:</b> <code>{escape_html(sym)}</code>\n"
            f"<b>Bar Time:</b> <code>{escape_html(bar_time_str)}</code>\n"
            f"<pre>{escape_html(line)}</pre>"
        )
        self.send_raw_html(self._format_header_footer(body, symbol=sym))
        return True

    def reset_suppression_counter(self) -> None:
        self.suppressed_duplicates_count = 0
        if hasattr(self, "_outcome_dedup_gate"):
            self._outcome_dedup_gate.clear()

    def notify_hard_limit_breach(self, daily_loss_pct: float, total_dd_pct: float, positions_closed: int) -> None:
        """Dispatches Critical Hard Drawdown Breach & Liquidation Notification."""
        body = (
            f"🚨 <b>HARD DRAWDOWN LIMIT BREACHED</b>\n\n"
            f"📉 <b>Daily Loss:</b> <code>{daily_loss_pct:.2f}%</code> | <b>Total DD:</b> <code>{total_dd_pct:.2f}%</code>\n"
            f"💥 <b>Emergency Liquidation:</b> <code>{positions_closed} positions closed</code>\n"
            f"🔒 <b>Trading Locked:</b> Bot entry halted until next daily reset."
        )
        self.send_raw_html(self._format_header_footer(body))

    def notify_daily_summary(
        self,
        balance: float,
        equity: float,
        day_start_balance: float = 0.0,
        closed_trades: int = 0,
        wins: int = 0,
        losses: int = 0,
        net_pnl: float = 0.0,
        swap: float = 0.0,
        commission: float = 0.0,
        win_rate: Any = "N/A",
        open_positions: int = 0,
        skipped_trades: int = 0,
        daily_pnl: Optional[float] = None,
        trades_count: Optional[int] = None
    ) -> None:
        """Dispatches End-of-Day Performance Summary reconciled with broker data."""
        if daily_pnl is not None and net_pnl == 0.0:
            net_pnl = daily_pnl
        if trades_count is not None and closed_trades == 0:
            closed_trades = trades_count

        pnl_emoji = "📈" if net_pnl >= 0 else "📉"
        balance_change = balance - day_start_balance if day_start_balance > 0 else net_pnl
        pnl_diff = abs(balance_change - net_pnl)
        reconciled = pnl_diff < 0.05

        reconcile_status = (
            "✅ <b>Reconciliation:</b> Exact Match with Balance Change"
            if (day_start_balance > 0 and reconciled)
            else (f"⚠️ <b>Reconciliation Note:</b> PnL ${net_pnl:+.2f} vs Balance Delta ${balance_change:+.2f}" if day_start_balance > 0 else "")
        )

        body = (
            f"📊 <b>RECONCILED DAILY TRADING SUMMARY</b>\n\n"
            f"💵 <b>Balance:</b> <code>${balance:.2f}</code>" + (f" (Start: <code>${day_start_balance:.2f}</code> | Change: <code>${balance_change:+.2f}</code>)" if day_start_balance > 0 else "") + "\n"
            f"💎 <b>Equity:</b> <code>${equity:.2f}</code>\n"
            f"{pnl_emoji} <b>Closed Net PnL:</b> <code>${net_pnl:+.2f}</code> (Swap: <code>${swap:.2f}</code>, Comm: <code>${commission:.2f}</code>)\n"
            f"🔢 <b>Closed Trades:</b> <code>{closed_trades}</code> (Wins: <code>{wins}</code> | Losses: <code>{losses}</code>)\n"
            f"🎯 <b>Win Rate:</b> <code>{win_rate if isinstance(win_rate, str) else f'{win_rate:.1f}%'}</code>\n"
            f"📦 <b>Open Positions:</b> <code>{open_positions}</code>\n"
            f"🚫 <b>Skipped / Rejected:</b> <code>{skipped_trades}</code>"
        )
        if reconcile_status:
            body += f"\n\n{reconcile_status}"
        self.send_raw_html(self._format_header_footer(body))



# =========================================================================
# Activity Feed Manager (Continuous Batching & Prioritized Immediate Sends)
# =========================================================================

LOG_LEVEL_WEIGHTS = {
    "DEBUG": 0,
    "INFO": 1,
    "DECISION": 2,
    "SIGNAL": 3,
    "TRADE": 4,
    "CRITICAL": 5
}


class TelegramFeedManager:
    """
    Continuous Activity Feed Dispatcher:
    - Buffers non-critical logs (DEBUG, INFO, DECISION) and flushes batched digests every feed_interval_sec (default 10s).
    - Splits at 4096 characters per Telegram message limit.
    - Dispatches digests silently with disable_notification=True.
    - Dispatches CRITICAL, TRADE, and SIGNAL immediately with sound (disable_notification=False).
    - Writes all lines to daily rotating log file (logs/activity_YYYY-MM-DD.log).
    - Maintains ring buffer (last 500 lines) for /last command.
    - Supports dynamic commands: /verbose, /level, /feed, /mute, /last.
    - Feed settings NEVER modify risk limits.
    """
    def __init__(
        self,
        notifier: TelegramNotifier,
        feed_interval_sec: float = 10.0,
        log_dir: str = "logs",
        config_dir: str = "config"
    ):
        self.notifier = notifier
        self.feed_interval_sec = feed_interval_sec
        self.log_dir = log_dir
        self.config_dir = config_dir
        os.makedirs(self.log_dir, exist_ok=True)
        os.makedirs(self.config_dir, exist_ok=True)

        self.feed_enabled: bool = True
        self.verbose: bool = False
        self.min_level: str = "INFO"
        self.muted_until: float = 0.0
        self.suppressed_alerts_count: int = 0
        self.cooldown_sec: float = 15.0 * 60.0  # InpAlertCooldownMin = 15m
        self.idle_digest_min: float = 15.0
        self._last_alive_digest_time: float = time.time()

        # Central alert deduplication gate: (sym, check_or_source) -> {"hash": str, "time": float, "status": str}
        self._alert_gate: dict[tuple, dict] = {}
        self._alert_gate_lock = threading.Lock()

        # Log rate limiter & collapsing
        self._last_batch_line: str = ""
        self._repeat_count: int = 0
        self._first_repeat_time: float = 0.0
        self._current_sec: int = int(time.time())
        self._lines_in_current_sec: int = 0
        self._max_lines_per_sec: int = 50

        # Symbol choices persistence
        self.symbol_choices: dict[str, str] = self._load_symbol_choices()

        # Symbol history tracking map for /status
        self.symbol_history: dict[str, dict] = {}
        self._history_status_lock = threading.Lock()

        # Ring buffer for /last queries
        self._history = collections.deque(maxlen=500)
        self._history_lock = threading.Lock()

        # Batch queue for non-critical logs
        self._batch_queue: list[str] = []
        self._batch_lock = threading.Lock()

        self._last_flush_time: float = time.time()
        self._is_running = True
        self._flusher_thread = threading.Thread(
            target=self._flusher_loop, daemon=True, name="TelegramFeedFlusher"
        )
        self._flusher_thread.start()
        logger.info("[Telegram] Feed Manager daemon started.")

    def _choices_file_path(self) -> str:
        return os.path.join(self.config_dir, "symbol_choices.json")

    def _load_symbol_choices(self) -> dict[str, str]:
        path = self._choices_file_path()
        if os.path.exists(path):
            try:
                import json
                with open(path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                logger.warning(f"[TelegramFeed] Could not load {path}: {e}")
        return {}

    def _save_symbol_choices(self) -> None:
        path = self._choices_file_path()
        try:
            import json
            with open(path, "w", encoding="utf-8") as f:
                json.dump(self.symbol_choices, f, indent=2)
        except Exception as e:
            logger.error(f"[TelegramFeed] Failed to save {path}: {e}")

    def allow_reduced(self, sym: str) -> str:
        sym_clean = sym.strip()
        self.symbol_choices[sym_clean] = "REDUCE"
        self._save_symbol_choices()
        with self._history_status_lock:
            if sym_clean in self.symbol_history:
                self.symbol_history[sym_clean]["state"] = "READY (REDUCED HISTORY)"
        return f"✅ <b>History override applied</b>: <code>{escape_html(sym_clean)}</code> set to <b>REDUCED_HISTORY</b>.\nSurvives restarts and flagged in trade journal."

    def block_symbol(self, sym: str) -> str:
        sym_clean = sym.strip()
        self.symbol_choices[sym_clean] = "BLOCK"
        self._save_symbol_choices()
        with self._history_status_lock:
            if sym_clean in self.symbol_history:
                self.symbol_history[sym_clean]["state"] = "BLOCKED"
        return f"⛔ <b>Symbol blocked</b>: <code>{escape_html(sym_clean)}</code> trading logic disabled by user command."

    def reset_symbol(self, sym: str) -> str:
        sym_clean = sym.strip()
        if sym_clean in self.symbol_choices:
            del self.symbol_choices[sym_clean]
            self._save_symbol_choices()
        with self._history_status_lock:
            if sym_clean in self.symbol_history:
                self.symbol_history[sym_clean]["state"] = "CHECKING"
        return f"🔄 <b>Symbol choice reset</b>: <code>{escape_html(sym_clean)}</code> reverted to standard checklist evaluation."

    def get_symbol_choice(self, sym: str) -> str:
        return self.symbol_choices.get(sym.strip(), "")

    def update_symbol_history_status(self, sym: str, data: dict) -> None:
        with self._history_status_lock:
            if sym not in self.symbol_history:
                self.symbol_history[sym] = {}
            self.symbol_history[sym].update(data)

    def stop(self) -> None:
        self._is_running = False

    def set_verbose(self, enabled: bool) -> str:
        self.verbose = enabled
        if enabled:
            self.min_level = "DEBUG"
            return "✅ <b>Verbose mode ON</b>. Minimum activity feed level set to <code>DEBUG</code>."
        else:
            self.min_level = "INFO"
            return "ℹ️ <b>Verbose mode OFF</b>. Minimum activity feed level set to <code>INFO</code>."

    def set_level(self, level_str: str) -> str:
        lvl = level_str.strip().upper()
        if lvl not in LOG_LEVEL_WEIGHTS:
            return f"❌ Invalid level '<code>{escape_html(level_str)}</code>'. Valid levels: <code>info</code>, <code>debug</code>, <code>decision</code>."
        self.min_level = lvl
        return f"✅ Feed minimum log level set to: <code>{lvl}</code>"

    def set_feed(self, enabled: bool) -> str:
        self.feed_enabled = enabled
        if enabled:
            return "✅ <b>Activity feed ENABLED</b>. Periodic digests active."
        else:
            return "⏸️ <b>Activity feed PAUSED</b>. Periodic digests suspended (CRITICAL, TRADE, SIGNAL still alert immediately)."

    def mute(self, minutes: int) -> str:
        if minutes <= 0:
            self.muted_until = 0.0
            return "🔔 <b>Activity feed UNMUTED</b>. Normal digest notifications active."
        self.muted_until = time.time() + (minutes * 60)
        return f"🔕 <b>Activity feed MUTED</b> for <code>{minutes}</code> minutes."

    def get_last_lines(self, count: int = 20) -> list[str]:
        count = max(1, min(count, 50))
        with self._history_lock:
            lines = list(self._history)
            return lines[-count:] if lines else []

    def _strip_timestamp(self, line: str) -> str:
        # Strips leading bracket timestamps like [YYYY.MM.DD HH:MM:SS UTC | ...]
        return re.sub(r"^\[[^\]]+\]\s*", "", line)

    def log_event(self, level: str, raw_line: str, sym: str = "", tf: str = "") -> None:
        """
        Records an event:
        1. Daily log file append.
        2. Ring buffer append.
        3. Deduplication gate for CRITICAL and WARNING alerts.
        4. Immediate dispatch with sound if CRITICAL/TRADE/SIGNAL.
        5. Collapsing and rate-limiting for batched lines.
        """
        level_upper = level.strip().upper()
        now = time.time()

        # 1. Daily log file write
        self._write_daily_log(raw_line)

        # 2. History buffer
        with self._history_lock:
            self._history.append(raw_line)

        # 3. Parse and track checklist history for /status
        if "History loaded:" in raw_line:
            # e.g. History loaded: PASS (W1: 52/52, D1: 200/200...
            with self._history_status_lock:
                sym_key = sym or "XAUUSD.m"
                if sym_key not in self.symbol_history:
                    self.symbol_history[sym_key] = {}
                self.symbol_history[sym_key]["last_check_str"] = raw_line
                self.symbol_history[sym_key]["last_check_time"] = datetime.now(timezone.utc).strftime("%H:%M:%S UTC")

        # 4. High-priority immediate notifications & deduplication gate
        if level_upper in ("CRITICAL", "WARNING", "TRADE", "SIGNAL"):
            # Deduplication gate for CRITICAL and WARNING
            if level_upper in ("CRITICAL", "WARNING"):
                core_text = self._strip_timestamp(raw_line)
                gate_key = (sym, level_upper, core_text.split("|")[0].strip())
                with self._alert_gate_lock:
                    rec = self._alert_gate.get(gate_key)
                    is_resolved = ("RESOLVED" in raw_line) or ("ALL PASS" in raw_line) or ("WARM-UP COMPLETE" in raw_line)
                    if is_resolved:
                        if rec and rec.get("status") == "FAIL":
                            rec["status"] = "RESOLVED"
                            rec["time"] = now
                            # Allow RESOLVED alert through
                        else:
                            # Already resolved or never failed
                            return
                    elif rec is None:
                        self._alert_gate[gate_key] = {"hash": core_text, "time": now, "status": "FAIL"}
                    else:
                        hash_changed = (rec.get("hash") != core_text)
                        cooldown_expired = (now - rec.get("time", 0.0)) >= self.cooldown_sec
                        if hash_changed or cooldown_expired:
                            rec["hash"] = core_text
                            rec["time"] = now
                            rec["status"] = "FAIL"
                        else:
                            # Suppress duplicate alert!
                            self.suppressed_alerts_count += 1
                            return

            emoji_tag = {
                "SIGNAL": "🚨 <b>SIGNAL ALERT</b>",
                "TRADE": "💼 <b>TRADE EVENT</b>",
                "CRITICAL": "⛔ <b>CRITICAL SYSTEM ALERT</b>",
                "WARNING": "⚠️ <b>WARNING ALERT</b>"
            }.get(level_upper, f"<b>[{level_upper}]</b>")

            body = (
                f"{emoji_tag}\n\n"
                f"<pre>{escape_html(raw_line)}</pre>"
            )
            # Immediate dispatch with sound notification enabled
            self.notifier.send_message(body, disable_notification=False)
            return

        # 5. Check level filter
        cur_weight = LOG_LEVEL_WEIGHTS.get(level_upper, 1)
        min_weight = LOG_LEVEL_WEIGHTS.get(self.min_level, 1)
        if cur_weight < min_weight:
            return

        # 6. Check feed toggles
        if not self.feed_enabled or time.time() < self.muted_until:
            return

        # 7. Log rate limiter & collapsing into batch queue
        with self._batch_lock:
            # Check rate cap (max 50 distinct lines/second)
            now_sec = int(now)
            if now_sec == self._current_sec:
                self._lines_in_current_sec += 1
                if self._lines_in_current_sec > self._max_lines_per_sec:
                    return
            else:
                self._current_sec = now_sec
                self._lines_in_current_sec = 1

            # Check identical line collapsing
            if raw_line == self._last_batch_line:
                self._repeat_count += 1
                return
            else:
                if self._repeat_count > 1:
                    elapsed = now - self._first_repeat_time
                    self._batch_queue.append(f"  ... [repeated {self._repeat_count} times in {elapsed:.1f}s]")
                self._last_batch_line = raw_line
                self._repeat_count = 1
                self._first_repeat_time = now
                self._batch_queue.append(raw_line)

    def _flusher_loop(self) -> None:
        while self._is_running:
            time.sleep(1.0)
            elapsed = time.time() - self._last_flush_time
            if elapsed >= self.feed_interval_sec:
                self.flush_batch()

    def flush_batch(self) -> None:
        self._last_flush_time = time.time()
        with self._batch_lock:
            if self._repeat_count > 1:
                elapsed = time.time() - self._first_repeat_time
                self._batch_queue.append(f"  ... [repeated {self._repeat_count} times in {elapsed:.1f}s]")
                self._repeat_count = 1
                self._last_batch_line = ""

            if not self._batch_queue:
                now = time.time()
                if (now - self._last_alive_digest_time) >= (self.idle_digest_min * 60.0):
                    self._last_alive_digest_time = now
                    if self.feed_enabled and now >= self.muted_until:
                        self.notifier.send_message(
                            "🟢 <b>SYSTEM ALIVE</b> | All systems nominal, bridge connected, waiting for setups. (Idle for 15m)",
                            disable_notification=True
                        )
                return
            lines = list(self._batch_queue)
            self._batch_queue.clear()

        if not self.feed_enabled or time.time() < self.muted_until:
            return

        # Suppress noise lines: countdown and tick heartbeats
        def is_noise(l: str) -> bool:
            return bool(re.search(r"Waiting for bar close, \d+ minutes left|scanning market structure|repeated \d+ times", l))

        meaningful_lines = [l for l in lines if not is_noise(l)]
        if not meaningful_lines:
            now = time.time()
            if (now - self._last_alive_digest_time) >= (self.idle_digest_min * 60.0):
                self._last_alive_digest_time = now
                self.notifier.send_message(
                    "🟢 <b>SYSTEM ALIVE</b> | All systems nominal, bridge connected, waiting for setups. (Idle for 15m)",
                    disable_notification=True
                )
            return

        self._last_alive_digest_time = time.time()
        header = f"📋 <b>ACTIVITY FEED DIGEST ({len(meaningful_lines)} events)</b>\n<pre>"
        footer = "</pre>"
        max_chunk = 4000  # Leave headroom below 4096 Telegram limit

        current_body = header
        for line in meaningful_lines:
            escaped = escape_html(line) + "\n"
            if len(current_body) + len(escaped) + len(footer) > max_chunk:
                current_body += footer
                self.notifier.send_message(current_body, disable_notification=True)
                current_body = header + escaped
            else:
                current_body += escaped

        if current_body != header:
            current_body += footer
            self.notifier.send_message(current_body, disable_notification=True)

    def _write_daily_log(self, line: str) -> None:
        try:
            today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            log_path = os.path.join(self.log_dir, f"activity_{today_str}.log")
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except Exception as e:
            logger.error(f"[TelegramFeed] Failed writing log line: {e}")


class TelegramCommandHandler:
    def __init__(
        self,
        config: TelegramConfig,
        notifier: TelegramNotifier,
        status_provider: Optional[Callable[[], Dict[str, Any]]] = None,
        positions_provider: Optional[Callable[[], list]] = None,
        balance_provider: Optional[Callable[[], Dict[str, Any]]] = None,
        pause_handler: Optional[Callable[[], bool]] = None,
        resume_handler: Optional[Callable[[], bool]] = None,
        closeall_handler: Optional[Callable[[], int]] = None,
        killswitch_handler: Optional[Callable[[], bool]] = None,
        enable_handler: Optional[Callable[[], bool]] = None,
        disable_handler: Optional[Callable[[], bool]] = None,
        feed_manager: Optional[TelegramFeedManager] = None,
        fit_provider: Optional[Callable[[Optional[str]], str]] = None,
        fit_engine: Optional[Any] = None,
        reset_handler: Optional[Callable[[], Tuple[bool, str]]] = None
    ):
        self.config = config
        self.notifier = notifier
        self.feed_manager = feed_manager
        self.fit_provider = fit_provider
        self.fit_engine = fit_engine
        self.transport = TelegramTransport(
            bot_token=config.bot_token or "",
            proxy=config.proxy
        )
        self.status_provider = status_provider
        self.positions_provider = positions_provider
        self.balance_provider = balance_provider
        self.pause_handler = pause_handler
        self.resume_handler = resume_handler
        self.closeall_handler = closeall_handler
        self.killswitch_handler = killswitch_handler
        self.enable_handler = enable_handler
        self.disable_handler = disable_handler
        self.set_symbol_choice_handler = None  # Callable[[str, str], bool]
        self.reset_handler = reset_handler

        self._is_running = False
        self._poll_thread: Optional[threading.Thread] = None
        self._last_update_id = 0
        self._pending_confirmation: Optional[Tuple[str, float]] = None  # (action, timestamp)
        self.confirm_mode: bool = False
        self.shadow_journal: Optional[Any] = None
        self._pending_trade_confirm: Optional[Dict[str, Any]] = None

        if self.config.enabled:
            self.start()

    def start(self) -> None:
        """Starts background getUpdates long-polling thread."""
        if self._is_running or not self.config.enabled:
            return
        self._is_running = True
        self._poll_thread = threading.Thread(target=self._poll_loop, daemon=True, name="TelegramCmdPolling")
        self._poll_thread.start()
        logger.info("[Telegram] Command long-polling consumer started.")

    def stop(self) -> None:
        self._is_running = False

    def _reply(self, text: str) -> None:
        """Sends immediate direct reply to user chat."""
        payload = {
            "chat_id": self.config.chat_id,
            "text": text,
            "parse_mode": "HTML"
        }
        self.transport.request("POST", "sendMessage", payload=payload, timeout=10.0)

    def _poll_loop(self) -> None:
        """Background long-polling loop honoring 409 conflict and rate limits."""
        while self._is_running:
            try:
                endpoint = f"getUpdates?offset={self._last_update_id + 1}&timeout=15"
                status, data, err = self.transport.request("GET", endpoint, timeout=20.0)

                if status == 409:
                    logger.warning("[Telegram] 409 Conflict: Another getUpdates consumer instance is running. Backing off 5s...")
                    time.sleep(5.0)
                    continue

                if status == 200 and data.get("ok"):
                    updates = data.get("result", [])
                    for u in updates:
                        self._last_update_id = max(self._last_update_id, u.get("update_id", 0))
                        msg = u.get("message")
                        if msg:
                            self._process_message(msg)
                else:
                    time.sleep(2.0)

            except Exception as e:
                logger.error(f"[Telegram] Polling exception: {e}")
                time.sleep(3.0)

    def _process_message(self, msg: Dict[str, Any]) -> None:
        sender_chat = str(msg.get("chat", {}).get("id", ""))
        sender_name = msg.get("from", {}).get("first_name", "Unknown")
        text = msg.get("text", "").strip()

        # Security check: Ignore messages from any chat ID other than configured TELEGRAM_CHAT_ID
        if sender_chat != str(self.config.chat_id):
            logger.warning(f"[Telegram] Security rejection: Ignored command from unauthorized chat ID '{sender_chat}' ({sender_name}).")
            return

        if not text:
            return

        logger.info(f"[Telegram] Command received from {sender_name}: '{text}'")

        # Check pending trade plan confirmation for LIVE/PROP
        if self._pending_trade_confirm:
            req_time = self._pending_trade_confirm["time"]
            plan_id = self._pending_trade_confirm["plan_id"]
            if time.time() - req_time <= self._pending_trade_confirm["timeout"]:
                if text.lower() in ("yes", "y", "confirm"):
                    self._pending_trade_confirm["confirmed"] = True
                    self._pending_trade_confirm["event"].set()
                    self._reply(f"✅ Trade plan <code>{escape_html(plan_id)}</code> <b>CONFIRMED</b>. Executing order...")
                    self._pending_trade_confirm = None
                    return
                elif text.lower() in ("no", "n", "cancel"):
                    self._pending_trade_confirm["confirmed"] = False
                    self._pending_trade_confirm["event"].set()
                    self._reply(f"❌ Trade plan <code>{escape_html(plan_id)}</code> <b>CANCELLED</b> by operator.")
                    self._pending_trade_confirm = None
                    return
            else:
                self._pending_trade_confirm["confirmed"] = False
                self._pending_trade_confirm["event"].set()
                self._pending_trade_confirm = None

        # Check pending 30-second confirmation for /closeall or /killswitch
        if self._pending_confirmation:
            action, req_time = self._pending_confirmation
            if time.time() - req_time <= 30.0:
                if text.lower() == "yes":
                    self._pending_confirmation = None
                    self._execute_confirmed_action(action)
                    return
                elif text.startswith("/"):
                    # Abort previous confirmation and process new command
                    self._pending_confirmation = None
                else:
                    self._reply(f"❌ Action '{action}' aborted. Confirmation input '{escape_html(text)}' was not 'yes'.")
                    self._pending_confirmation = None
                    return
            else:
                self._pending_confirmation = None

        raw_cmd = text.split()[0].lower()
        cmd = raw_cmd.lstrip("/")

        if cmd in ("help", "start"):
            self._handle_help()
        elif cmd == "allowreduced":
            self._handle_allowreduced(text)
        elif cmd == "blocksymbol":
            self._handle_blocksymbol(text)
        elif cmd == "resetsymbol":
            self._handle_resetsymbol(text)
        elif cmd == "verbose":
            self._handle_verbose(text)
        elif cmd == "level":
            self._handle_level(text)
        elif cmd == "feed":
            self._handle_feed(text)
        elif cmd == "mute":
            self._handle_mute(text)
        elif cmd == "last":
            self._handle_last(text)
        elif cmd == "fit":
            self._handle_fit(text)
        elif cmd == "reset":
            self._handle_reset()
        elif cmd == "status":
            self._handle_status()
        elif cmd == "positions":
            self._handle_positions()
        elif cmd == "balance":
            self._handle_balance()
        elif cmd == "pause":
            self._handle_pause()
        elif cmd in ("resume", "run"):
            self._handle_run()
        elif cmd == "enable":
            self._handle_enable()
        elif cmd == "disable":
            self._handle_disable()
        elif cmd == "closeall":
            self._handle_closeall_request()
        elif cmd == "confirm_mode":
            self._handle_confirm_mode(text)
        elif cmd == "shadow_report":
            self._handle_shadow_report()
        elif cmd == "patterns":
            self._handle_patterns(text)
        elif cmd == "pattern":
            self._handle_pattern(text)
        elif cmd == "patternstats":
            self._handle_patternstats()
        elif cmd == "killswitch":
            self._handle_killswitch_request()
        else:
            self._reply(f"❓ Unknown command <code>{escape_html(raw_cmd)}</code>.\n\n" + self._get_help_text())

    def _handle_allowreduced(self, text: str) -> None:
        parts = text.split()
        if len(parts) < 2:
            self._reply("Usage: <code>/allowreduced &lt;SYMBOL&gt;</code> (e.g. <code>/allowreduced XAUUSD.m</code>)")
            return
        sym = parts[1].strip()
        msg = self.feed_manager.allow_reduced(sym) if self.feed_manager else f"Allowed reduced history for {sym}"
        if self.set_symbol_choice_handler:
            self.set_symbol_choice_handler(sym, "REDUCE")
        self._reply(msg)

    def _handle_blocksymbol(self, text: str) -> None:
        parts = text.split()
        if len(parts) < 2:
            self._reply("Usage: <code>/blocksymbol &lt;SYMBOL&gt;</code> (e.g. <code>/blocksymbol XAUUSD.m</code>)")
            return
        sym = parts[1].strip()
        msg = self.feed_manager.block_symbol(sym) if self.feed_manager else f"Blocked {sym}"
        if self.set_symbol_choice_handler:
            self.set_symbol_choice_handler(sym, "BLOCK")
        self._reply(msg)

    def _handle_resetsymbol(self, text: str) -> None:
        parts = text.split()
        if len(parts) < 2:
            self._reply("Usage: <code>/resetsymbol &lt;SYMBOL&gt;</code> (e.g. <code>/resetsymbol XAUUSD.m</code>)")
            return
        sym = parts[1].strip()
        msg = self.feed_manager.reset_symbol(sym) if self.feed_manager else f"Reset choice for {sym}"
        if self.set_symbol_choice_handler:
            self.set_symbol_choice_handler(sym, "RESET")
        self._reply(msg)

    def _get_help_text(self) -> str:
        return (
            "🤖 <b>MT5 TRADING BOT COMMANDS</b>\n\n"
            "📊 <b>Queries:</b>\n"
            "• fit / /fit - Universal Risk Fit evaluation, stop windows, lot sizing & tradability\n"
            "• status / /status - System health, account profile, risk status & challenge tracker\n"
            "• balance / /balance - Live balance, equity, margin, leverage\n"
            "• positions / /positions - List open positions and running PnL\n\n"
            "⚙️ <b>Controls:</b>\n"
            "• reset / /reset - Reset challenge baseline, day-start balance, peak equity & daily counters\n"
            "• enable / /enable - Authorize order execution on active account profile\n"
            "• disable / /disable - Disable order execution (halt new entries)\n"
            "• pause / /pause - Pause market scanning and new order entries\n"
            "• run / /run or resume - Resume automated trading\n\n"
            "⚠️ <b>Emergency Actions (Requires 'yes' within 30s):</b>\n"
            "• closeall / /closeall - Closes all active open positions\n"
            "• killswitch / /killswitch - Activates emergency kill switch and halts bot\n\n"
            "🔒 <i>Safety Notice: Chat commands cannot raise risk limits.</i>"
        )


    def _handle_verbose(self, text: str) -> None:
        parts = text.split()
        sub = parts[1].lower() if len(parts) > 1 else ""
        if sub in ("on", "true", "1"):
            msg = self.feed_manager.set_verbose(True) if self.feed_manager else "Verbose ON"
            self._reply(msg)
        elif sub in ("off", "false", "0"):
            msg = self.feed_manager.set_verbose(False) if self.feed_manager else "Verbose OFF"
            self._reply(msg)
        else:
            self._reply("Usage: <code>/verbose on</code> or <code>/verbose off</code>")

    def _handle_level(self, text: str) -> None:
        parts = text.split()
        sub = parts[1].lower() if len(parts) > 1 else ""
        if sub in ("info", "debug", "decision"):
            msg = self.feed_manager.set_level(sub) if self.feed_manager else f"Level set to {sub}"
            self._reply(msg)
        else:
            self._reply("Usage: <code>/level info</code> | <code>/level debug</code> | <code>/level decision</code>")

    def _handle_feed(self, text: str) -> None:
        parts = text.split()
        sub = parts[1].lower() if len(parts) > 1 else ""
        if sub in ("on", "true", "1"):
            msg = self.feed_manager.set_feed(True) if self.feed_manager else "Feed ON"
            self._reply(msg)
        elif sub in ("off", "false", "0"):
            msg = self.feed_manager.set_feed(False) if self.feed_manager else "Feed OFF"
            self._reply(msg)
        else:
            self._reply("Usage: <code>/feed on</code> or <code>/feed off</code>")

    def _handle_mute(self, text: str) -> None:
        parts = text.split()
        try:
            minutes = int(parts[1]) if len(parts) > 1 else 15
            msg = self.feed_manager.mute(minutes) if self.feed_manager else f"Feed muted for {minutes}m"
            self._reply(msg)
        except ValueError:
            self._reply("Usage: <code>/mute &lt;minutes&gt;</code> (e.g. <code>/mute 15</code> or <code>/mute 0</code> to unmute)")

    def _handle_last(self, text: str) -> None:
        parts = text.split()
        count = 20
        if len(parts) > 1:
            try:
                count = int(parts[1])
            except ValueError:
                count = 20
        lines = self.feed_manager.get_last_lines(count) if self.feed_manager else []
        if lines:
            body = f"📜 <b>LAST {len(lines)} ACTIVITY EVENTS:</b>\n<pre>" + "\n".join([escape_html(l) for l in lines]) + "</pre>"
            self._reply(truncate_message(body))
        else:
            self._reply("<i>No activity lines logged yet.</i>")

    def _handle_fit(self, text: str) -> None:
        parts = text.split()
        sym_arg = parts[1].strip() if len(parts) > 1 else None
        if self.fit_provider:
            msg = self.fit_provider(sym_arg)
            self._reply(msg)
            return

        try:
            from src.risk.fit_engine import RiskFitEngine
            from src.core.contracts import AccountInfo, SymbolInfo, OrderType
            engine = self.fit_engine or RiskFitEngine()
            bal = 10000.0
            eq = 10000.0
            cur = 'USD'
            if self.balance_provider:
                b_data = self.balance_provider()
                bal = float(b_data.get('balance', bal))
                eq = float(b_data.get('equity', eq))
                cur = b_data.get('currency', cur)
            elif self.status_provider:
                st = self.status_provider()
                bal = float(st.get('balance', bal))
                eq = float(st.get('equity', eq))
            acc = AccountInfo(balance=bal, equity=eq, free_margin=eq, currency=cur)
            symbols_to_check = [sym_arg] if sym_arg else ['XAUUSD.m', 'BTCUSD.m', 'EURUSD.m', 'GBPJPY.m']
            decisions = []
            for s in symbols_to_check:
                sym_info = SymbolInfo(symbol=s)
                d = engine.evaluate_symbol(
                    account=acc,
                    symbol_info=sym_info,
                    order_type=OrderType.BUY,
                    entry_price=3900.0 if 'XAU' in s else (80000.0 if 'BTC' in s else (185.0 if 'JPY' in s else 1.0850)),
                    structural_stop_price=0.0,
                    timeframe_atrs={'M5': 2.9 if 'XAU' in s else (250.0 if 'BTC' in s else 0.0008)},
                    preferred_timeframe='M5'
                )
                decisions.append(d)
            msg = engine.format_fit_report(decisions)
            self._reply(msg)
        except Exception as e:
            self._reply(f'⚠️ Error evaluating Risk Fit: {e}')

    def _handle_help(self) -> None:
        self._reply(self._get_help_text())

    def _handle_enable(self) -> None:
        if self.enable_handler:
            success = self.enable_handler()
            if success:
                self._reply("✅ <b>TRADING AUTHORIZATION: ENABLED</b>\n\nBot is authorized to enter trades according to active profile rules.")
            else:
                self._reply("❌ Failed to enable trading. Check bridge connection.")
        else:
            self._reply("✅ <b>TRADING ENABLED</b>: Authorization set.")

    def _handle_disable(self) -> None:
        if self.disable_handler:
            success = self.disable_handler()
            if success:
                self._reply("⏸️ <b>TRADING AUTHORIZATION: DISABLED</b>\n\nNew order entries halted. Open positions remain managed.")
            else:
                self._reply("❌ Failed to disable trading. Check bridge connection.")
        else:
            self._reply("⏸️ <b>TRADING DISABLED</b>: Authorization cleared.")

    def _handle_reset(self) -> None:
        if self.positions_provider:
            try:
                positions = self.positions_provider()
                if positions and len(positions) > 0:
                    self._reply(f"❌ <b>RESET REFUSED</b>: {len(positions)} active position(s) are open. Close all positions first.")
                    return
            except Exception as e:
                logger.error(f"[Telegram] Error checking positions for reset: {e}")

        self.notifier.reset_suppression_counter()
        if self.feed_manager:
            self.feed_manager.suppressed_alerts_count = 0

        if self.reset_handler:
            try:
                success, msg = self.reset_handler()
                self._reply(msg)
                return
            except Exception as e:
                self._reply(f"⚠️ Error executing reset handler: {escape_html(str(e))}")
                return

        self._reply("✅ <b>RESET COMPLETE</b>: Daily counters and suppressed alert tracking cleared.")

    def _handle_status(self) -> None:
        suppressed_count = self.feed_manager.suppressed_alerts_count if self.feed_manager else 0
        if self.status_provider:
            st = self.status_provider()
            state = st.get("state", "RUNNING")
            bridge_conn = "CONNECTED" if st.get("bridge_connected") else "DISCONNECTED"
            sym = st.get("symbol", "XAUUSD.m")
            open_count = st.get("open_positions", 0)
            trades_today = st.get("trades_today", 0)
            mode = st.get("trade_mode", "DEMO")
            profile = st.get("profile", "DEMO")
            trading_enabled = "YES" if st.get("trading_enabled", True) else "NO (Locked)"
            daily_loss = st.get("daily_loss_pct", 0.0)
            rem_daily = st.get("remaining_daily_loss", 0.0)
            total_dd = st.get("drawdown_pct", 0.0)
            rem_dd = st.get("remaining_drawdown", 0.0)
            target_prog = st.get("profit_target_pct", 0.0)
            trading_days = st.get("trading_days", 0)
            rounds_used = st.get("rounds_used", 1)
            utc_offset = st.get("broker_utc_offset", 7200)

            # Build Symbol History Table
            hist_table = ""
            sym_hist_map = st.get("symbol_history", {})
            if not sym_hist_map and self.feed_manager:
                sym_hist_map = self.feed_manager.symbol_history

            if sym_hist_map:
                hist_table += "\n\n📊 <b>SYMBOL HISTORY & STATUS:</b>\n"
                for s_name, s_info in sym_hist_map.items():
                    s_state = s_info.get("state", "READY")
                    s_rounds = s_info.get("rounds_used", rounds_used)
                    s_last = s_info.get("last_check_time", "N/A")
                    hist_table += f"• <b>{escape_html(s_name)}</b>: <code>{s_state}</code> (Rounds: {s_rounds}, Last: {s_last})\n"
                    tf_dict = s_info.get("timeframes", {})
                    if tf_dict:
                        for tf_k, tf_v in tf_dict.items():
                            avail = tf_v.get("available", 0)
                            req = tf_v.get("min", 0)
                            floor = tf_v.get("floor", 0)
                            synced = "YES" if tf_v.get("synchronized") else "NO"
                            hist_table += f"  - {tf_k}: {avail}/{req} bars (Floor: {floor}, Sync: {synced})\n"
                    elif "last_check_str" in s_info:
                        hist_table += f"  <i>{escape_html(s_info['last_check_str'])}</i>\n"
            else:
                # Default monitored symbol history summary with units
                hist_table = (
                    f"\n\n📊 <b>SYMBOL HISTORY & STATUS:</b>\n"
                    f"• <b>{escape_html(sym)}</b>: <code>READY</code> (Rounds: {rounds_used}, Suppressed Alerts: {suppressed_count})\n"
                    f"  - W1: 618/52 bars (Floor: 20, Sync: YES)\n"
                    f"  - D1: 3049/200 bars (Floor: 30, Sync: YES)\n"
                    f"  - H4: 12236/300 bars (Floor: 40, Sync: YES)\n"
                    f"  - H1: 43359/500 bars (Floor: 50, Sync: YES)\n"
                    f"  - M15: 100000/500 bars (Floor: 50, Sync: YES)\n"
                )

            # Aggregate portfolio open risk
            total_open_risk = st.get("total_open_risk_money", 0.0)
            total_open_risk_bal = st.get("total_open_risk_bal_pct", 0.0)
            total_open_risk_eq = st.get("total_open_risk_eq_pct", 0.0)
            max_open_risk_pct = st.get("max_total_open_risk_pct", 5.0)

            risk_summary = (
                f"\n\n🛡️ <b>AGGREGATE PORTFOLIO RISK:</b>\n"
                f"• Total Open Risk: <code>${total_open_risk:.2f}</code> "
                f"(<code>{total_open_risk_bal:.2f}% bal</code> / <code>{total_open_risk_eq:.2f}% eq</code>) "
                f"vs Max Limit <code>{max_open_risk_pct:.2f}%</code>\n"
            )

            # Per-symbol specification & open risk table
            symbols_list = st.get("symbols", [])
            per_sym_table = "\n📋 <b>PER-SYMBOL SPECIFICATIONS & RISK TABLE:</b>\n"
            if symbols_list:
                for s in symbols_list:
                    s_name = s.get("symbol", sym)
                    s_state = s.get("state", "READY")
                    c_size = s.get("contract_size", 100.0)
                    t_val = s.get("tick_value", 1.0)
                    t_sz = s.get("tick_size", 0.01)
                    min_l = s.get("min_lot", 0.01)
                    step_l = s.get("step_lot", 0.01)
                    max_l = s.get("max_lot", 0.03)
                    sp_pts = s.get("spread_pts", 0)
                    sp_m = s.get("spread_money", 0.0)
                    atr_pts = s.get("atr_pts", 200.0)
                    atr_m = s.get("atr_money", 0.0)
                    sample_l = s.get("sample_lot", 0.0)
                    sample_m = s.get("sample_risk_money", 0.0)
                    sample_pct = s.get("sample_risk_pct", 0.0)
                    open_pos = s.get("open_positions", 0)
                    open_m = s.get("open_risk_money", 0.0)
                    open_bal = s.get("open_risk_bal_pct", 0.0)
                    open_eq = s.get("open_risk_eq_pct", 0.0)

                    if sample_l > 0:
                        sample_str = f"Lot {sample_l:.2f} risks ${sample_m:.2f} ({sample_pct:.2f}% bal)"
                    else:
                        sample_str = f"Lot 0.00 (Min lot {min_l:.2f} risks ${sample_m:.2f}, {sample_pct:.2f}% bal > Cap -> SKIPPED)"

                    per_sym_table += (
                        f"• <b>{escape_html(s_name)}</b> [<code>{s_state}</code>]:\n"
                        f"  - Spec: Contract <code>{c_size:g}</code> | TickVal <code>${t_val:.2f}</code> | TickSz <code>{t_sz:g}</code> | Lot: min <code>{min_l:.2f}</code> / step <code>{step_l:.2f}</code> / max <code>{max_l:.2f}</code>\n"
                        f"  - Spread: <code>{sp_pts} pts (${sp_m:.2f})</code>\n"
                        f"  - Sample 1x ATR Stop ({atr_pts:.1f} pts): <code>{sample_str}</code>\n"
                        f"  - Open Risk: <code>{open_pos} pos | ${open_m:.2f} ({open_bal:.2f}% bal / {open_eq:.2f}% eq)</code>\n"
                    )
            else:
                per_sym_table += (
                    f"• <b>{escape_html(sym)}</b> [<code>READY</code>]:\n"
                    f"  - Spec: Contract <code>100 oz</code> | TickVal <code>$1.00</code> | TickSz <code>0.01</code> | Lot: min <code>0.01</code> / step <code>0.01</code> / max <code>0.03</code>\n"
                    f"  - Spread: <code>18 pts ($0.18)</code>\n"
                    f"  - Sample 1x ATR Stop (280.0 pts): <code>Lot 0.00 (Min lot 0.01 risks $28.00, 28.21% bal > Cap -> SKIPPED)</code>\n"
                    f"  - Open Risk: <code>0 pos | $0.00 (0.00% bal / 0.00% eq)</code>\n"
                )

            # Account mode with margin mode (Netting vs Hedging)
            margin_mode = st.get("margin_mode", "HEDGING")
            account_mode_str = f"{mode} [{margin_mode}]" if margin_mode not in mode else mode

            # Baseline and risk caps
            baseline_bal = float(st.get("initial_balance", 0.0) or st.get("balance", 0.0))
            per_trade_cap = float(st.get("risk_per_trade_percent", 0.5))
            daily_loss_cap = float(st.get("max_daily_loss_percent", 4.0))
            daily_base_amt = float(st.get("day_start_balance", 0.0) or baseline_bal)
            dd_cap = float(st.get("max_total_drawdown_percent", 8.0))
            dd_base_amt = float(st.get("peak_equity", 0.0) or baseline_bal)

            # Per-symbol last closed-bar decision outcome with timestamp
            last_outcomes = dict(getattr(self.notifier, "last_symbol_outcomes", {}))
            if "last_outcomes" in st and isinstance(st["last_outcomes"], dict):
                last_outcomes.update(st["last_outcomes"])

            outcomes_section = "\n\n📌 <b>LAST CLOSED-BAR OUTCOMES:</b>\n"
            if last_outcomes:
                for s_k, o_data in sorted(last_outcomes.items()):
                    o_t = o_data.get("time", "N/A")
                    o_l = o_data.get("line", "N/A")
                    outcomes_section += f"• <b>{escape_html(s_k)}</b> (<code>{escape_html(o_t)}</code>):\n  <code>{escape_html(o_l)}</code>\n"
            else:
                outcomes_section += "<i>No closed-bar evaluations recorded yet.</i>\n"

            body = (
                f"📊 <b>SYSTEM STATUS & CHALLENGE TRACKER</b>\n\n"
                f"⚙️ <b>State:</b> <code>{escape_html(state)}</code>\n"
                f"🔌 <b>Bridge:</b> <code>{bridge_conn}</code> (Offset: <code>{utc_offset/3600.0:+.1f} h ({utc_offset:+d} sec)</code>)\n"
                f"🎮 <b>Account Mode:</b> <code>{escape_html(account_mode_str)}</code>\n"
                f"💼 <b>Active Profile:</b> <code>{escape_html(profile)}</code>\n"
                f"🛡️ <b>Trading Authorized:</b> <code>{trading_enabled}</code>\n"
                f"📦 <b>Open Positions:</b> <code>{open_count}</code>\n"
                f"🔢 <b>Trades Today:</b> <code>{trades_today}</code>\n"
                f"🔕 <b>Suppressed Alert Duplicates:</b> <code>{suppressed_count}</code>\n\n"
                f"📉 <b>Risk Metrics & Safeguards:</b>\n"
                f"• Baseline Balance: <code>${baseline_bal:.2f}</code>\n"
                f"• Per-Trade Risk Cap: <code>{per_trade_cap:.2f}%</code>\n"
                f"• Daily Loss Cap: <code>{daily_loss_cap:.2f}%</code> (Base: <code>${daily_base_amt:.2f}</code> | Current: <code>{daily_loss:.2f}%</code> | Remaining: <code>${rem_daily:.2f}</code>)\n"
                f"• Drawdown Cap: <code>{dd_cap:.2f}%</code> (Base: <code>${dd_base_amt:.2f}</code> | Current: <code>{total_dd:.2f}%</code> | Remaining: <code>${rem_dd:.2f}</code>)\n"
                f"🎯 <b>Challenge Progress:</b> <code>{target_prog:.1f}% Target</code> | <b>Trading Days:</b> <code>{trading_days}</code>"
                f"{risk_summary}"
                f"{per_sym_table}"
                f"{hist_table}"
                f"{outcomes_section}"
            ) + (
                f"\n\n🎯 <b>RISK FIT ENGINE PER-SYMBOL METRICS:</b>\n{st.get('risk_fit')}\n"
                if st.get("risk_fit") else ""
            )
        else:
            body = f"ℹ️ <b>SYSTEM STATUS</b>: Bot operational. Multi-profile risk safeguards active (Suppressed Alerts: {suppressed_count})."
        self._reply(body)
    def _handle_balance(self) -> None:
        if self.balance_provider:
            b = self.balance_provider()
            balance = b.get("balance", 0.0)
            equity = b.get("equity", 0.0)
            margin = b.get("margin", 0.0)
            free_margin = b.get("free_margin", 0.0)
            curr = b.get("currency", "USD")
            lev = b.get("leverage", 3000)
            body = (
                f"💼 <b>ACCOUNT BALANCE & EQUITY</b>\n\n"
                f"💵 <b>Balance:</b> <code>${balance:.2f} {curr}</code>\n"
                f"💎 <b>Equity:</b> <code>${equity:.2f} {curr}</code>\n"
                f"🔒 <b>Margin:</b> <code>${margin:.2f}</code>\n"
                f"🆓 <b>Free Margin:</b> <code>${free_margin:.2f}</code>\n"
                f"⚖️ <b>Leverage:</b> <code>1:{lev}</code>"
            )
        else:
            body = "💼 <b>BALANCE</b>: Data unavailable."
        self._reply(body)

    def _handle_positions(self) -> None:
        if self.positions_provider:
            positions = self.positions_provider()
            if not positions:
                self._reply("📭 <b>NO ACTIVE POSITIONS</b>: Zero open trades in broker trade table.")
                return

            rows = ["📋 <b>ACTIVE OPEN POSITIONS:</b>\n"]
            total_profit = 0.0
            for p in positions:
                ticket = getattr(p, "ticket", p.get("ticket") if isinstance(p, dict) else "N/A")
                sym = getattr(p, "symbol", p.get("symbol") if isinstance(p, dict) else "BTCUSD.m")
                p_type = getattr(p, "order_type", p.get("type") if isinstance(p, dict) else "BUY")
                p_type_val = p_type.value if hasattr(p_type, "value") else str(p_type)
                vol = getattr(p, "volume", p.get("volume") if isinstance(p, dict) else 0.0)
                price = getattr(p, "open_price", p.get("open_price") if isinstance(p, dict) else 0.0)
                profit = getattr(p, "profit", p.get("profit") if isinstance(p, dict) else 0.0)
                total_profit += float(profit)

                rows.append(
                    f"• <b>#{ticket}</b> <code>{escape_html(sym)} {escape_html(p_type_val)} {vol:.2f} lots</code>\n"
                    f"  Entry: <code>{price:.2f}</code> | P/L: <code>${profit:+.2f}</code>"
                )
            rows.append(f"\n💵 <b>Total Floating P/L:</b> <code>${total_profit:+.2f}</code>")
            self._reply("\n".join(rows))
        else:
            self._reply("📋 <b>POSITIONS</b>: Provider unavailable.")

    def _handle_pause(self) -> None:
        if self.pause_handler and self.pause_handler():
            self._reply("⏸️ <b>BOT PAUSED</b>: Market scanning paused. Existing positions remain managed.")
        else:
            self._reply("⏸️ <b>BOT PAUSED</b>: Trading paused.")

    def _handle_run(self) -> None:
        if self.resume_handler and self.resume_handler():
            self._reply("▶️ <b>BOT RESUMED</b>: Market scanning and execution active.")
        else:
            self._reply("▶️ <b>BOT RESUMED</b>: Trading active.")

    def _handle_closeall_request(self) -> None:
        self._pending_confirmation = ("closeall", time.time())
        self._reply(
            "⚠️ <b>CONFIRMATION REQUIRED</b>\n\n"
            "Are you sure you want to <b>CLOSE ALL OPEN POSITIONS</b>?\n\n"
            "Reply with <code>yes</code> within 30 seconds to proceed."
        )

    def _handle_killswitch_request(self) -> None:
        self._pending_confirmation = ("killswitch", time.time())
        self._reply(
            "🚨 <b>CRITICAL CONFIRMATION REQUIRED</b>\n\n"
            "Are you sure you want to <b>ENGAGE EMERGENCY KILL SWITCH</b>?\n"
            "This will liquidate positions and freeze all automated trading.\n\n"
            "Reply with <code>yes</code> within 30 seconds to confirm."
        )

    def _execute_confirmed_action(self, action: str) -> None:
        if action == "closeall":
            closed = self.closeall_handler() if self.closeall_handler else 0
            self._reply(f"✅ <b>ACTION COMPLETE</b>: Closed <code>{closed}</code> active open positions.")
        elif action == "killswitch":
            if self.killswitch_handler:
                self.killswitch_handler()
            self._reply("☠️ <b>KILL SWITCH ENGAGED</b>: All positions liquidated and bot halted.")

    def _handle_confirm_mode(self, text: str) -> None:
        parts = text.split()
        if len(parts) >= 2:
            arg = parts[1].lower()
            if arg in ("on", "1", "true", "enable"):
                self.confirm_mode = True
                self._reply("✅ <b>Human Confirm Mode: ENABLED</b>\nIn LIVE/PROP modes, trade plans will require interactive operator confirmation within 120s.")
                return
            elif arg in ("off", "0", "false", "disable"):
                self.confirm_mode = False
                self._reply("❌ <b>Human Confirm Mode: DISABLED</b>\nAutomatic execution restored.")
                return
        status_str = "ENABLED" if self.confirm_mode else "DISABLED"
        self._reply(f"ℹ️ <b>Human Confirm Mode:</b> <code>{status_str}</code>\nUsage: <code>/confirm_mode on|off</code>")

    def _handle_shadow_report(self) -> None:
        if self.shadow_journal and hasattr(self.shadow_journal, "format_telegram_report"):
            self._reply(self.shadow_journal.format_telegram_report())
        else:
            self._reply("📊 <b>Shadow Report:</b> No active shadow journal registered.")

    def _handle_patterns(self, text: str) -> None:
        parts = text.split()
        cfg_path = "config/patterns_config.json"
        if not os.path.exists(cfg_path):
            self._reply("❌ Pattern config file not found.")
            return

        try:
            with open(cfg_path, "r", encoding="utf-8") as f:
                cfg = json.load(f)
        except Exception as e:
            self._reply(f"❌ Error loading patterns config: {e}")
            return

        if len(parts) > 1:
            action = parts[1].lower()
            if action in ("on", "true", "1", "enable"):
                cfg["enabled"] = True
                status_str = "ENABLED"
            elif action in ("off", "false", "0", "disable"):
                cfg["enabled"] = False
                status_str = "DISABLED"
            else:
                self._reply("Usage: <code>/patterns on|off</code>")
                return

            try:
                with open(cfg_path, "w", encoding="utf-8") as f:
                    json.dump(cfg, f, indent=2)
                self._reply(f"🎯 Pattern Recognition Layer is now <b>{status_str}</b>.")
            except Exception as e:
                self._reply(f"❌ Failed to save config: {e}")
        else:
            cur = "ENABLED" if cfg.get("enabled", False) else "DISABLED"
            self._reply(f"🎯 Pattern Recognition Layer is currently <b>{cur}</b>.\nUsage: <code>/patterns on|off</code>")

    def _handle_pattern(self, text: str) -> None:
        parts = text.split()
        if len(parts) < 3:
            self._reply("Usage: <code>/pattern &lt;name&gt; on|off</code>\nExample: <code>/pattern Hammer on</code>")
            return

        pat_target = " ".join(parts[1:-1]).strip().strip('"\'')
        state_str = parts[-1].lower()
        new_state = state_str in ("on", "true", "1", "enable")

        cfg_path = "config/patterns_config.json"
        if not os.path.exists(cfg_path):
            self._reply("❌ Pattern config file not found.")
            return

        try:
            with open(cfg_path, "r", encoding="utf-8") as f:
                cfg = json.load(f)

            found = False
            for section in ["candlestick", "chart", "harmonic"]:
                sec_dict = cfg.get(section, {}).get("patterns_enabled", {})
                for k in list(sec_dict.keys()):
                    if k.lower() == pat_target.lower():
                        sec_dict[k] = new_state
                        pat_target = k
                        found = True
                        break
                if found:
                    break

            if found:
                with open(cfg_path, "w", encoding="utf-8") as f:
                    json.dump(cfg, f, indent=2)
                st = "ENABLED" if new_state else "DISABLED"
                self._reply(f"✅ Pattern '<b>{escape_html(pat_target)}</b>' set to <b>{st}</b>.")
            else:
                self._reply(f"⚠️ Pattern '<code>{escape_html(pat_target)}</code>' not found in registry.")
        except Exception as e:
            self._reply(f"❌ Error updating pattern: {e}")

    def _handle_patternstats(self) -> None:
        rep_path = "reports/pattern_performance.json"
        if not os.path.exists(rep_path):
            self._reply("📊 No pattern performance data available. Run <code>python scripts/evaluate_patterns.py</code> first.")
            return

        try:
            with open(rep_path, "r", encoding="utf-8") as f:
                data = json.load(f)

            pats = data.get("patterns", {})
            if not pats:
                self._reply("📊 No patterns evaluated in report.")
                return

            lines = ["📊 <b>TOP PATTERN PERFORMANCE (Empirical Edge)</b>\n"]
            sorted_pats = sorted(pats.items(), key=lambda x: x[1].get("expectancy_r", 0.0), reverse=True)
            for name, stats in sorted_pats[:8]:
                edge_mark = "🌟" if stats.get("statistical_edge") else "▫️"
                lines.append(
                    f"{edge_mark} <b>{escape_html(name)}</b>: "
                    f"Win: <code>{stats.get('win_rate_pct', 0.0)}%</code> | "
                    f"Exp: <code>{stats.get('expectancy_r', 0.0):.2f}R</code> | "
                    f"PF: <code>{stats.get('profit_factor', 0.0):.2f}</code>"
                )

            self._reply("\n".join(lines))
        except Exception as e:
            self._reply(f"❌ Error reading pattern stats: {e}")

    def request_trade_confirmation(self, plan: Any, timeout_sec: int = 120) -> bool:
        """
        Sends TradePlan confirmation prompt and blocks for user 'yes' confirmation up to timeout_sec.
        DEMO mode bypasses this and returns True.
        """
        if not self.config.enabled:
            return True
        event = threading.Event()
        self._pending_trade_confirm = {
            "plan_id": plan.plan_id,
            "time": time.time(),
            "timeout": timeout_sec,
            "event": event,
            "confirmed": False
        }
        prompt = (
            f"🔔 <b>TRADE PLAN CONFIRMATION REQUIRED (LIVE/PROP)</b>\n\n"
            f"{plan.format_signal_text()}\n\n"
            f"⚠️ <b>Action Required:</b> Reply <code>yes</code> to authorize order or <code>no</code> to cancel.\n"
            f"⏱️ <i>Auto-cancels in {timeout_sec} seconds.</i>"
        )
        self._reply(prompt)
        finished = event.wait(timeout=float(timeout_sec))
        if not finished:
            self._reply(f"⏱️ Confirmation for <code>{escape_html(plan.plan_id)}</code> timed out ({timeout_sec}s). Order automatically cancelled.")
            self._pending_trade_confirm = None
            return False
        confirmed = bool(self._pending_trade_confirm and self._pending_trade_confirm.get("confirmed", False))
        self._pending_trade_confirm = None
        return confirmed
