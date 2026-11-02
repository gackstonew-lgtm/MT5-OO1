"""
Script to safely update src/notifications/telegram_service.py:
1. Adds disable_notification support to TelegramNotifier.send_message.
2. Adds TelegramFeedManager class for 10s batch digests (silent) & immediate priority alerts (sound).
3. Adds /verbose, /level, /feed, /mute, /last commands to TelegramCommandHandler.
4. Protects risk settings from feed alterations.
"""

import re
import os

target_path = os.path.abspath("src/notifications/telegram_service.py")

with open(target_path, "r", encoding="utf-8") as f:
    code = f.read()

# 1. Update imports
if "import collections" not in code:
    code = code.replace("import queue\n", "import queue\nimport collections\nimport os\n")

# 2. Update TelegramNotifier to support send_message with disable_notification
old_send_raw = """    def send_raw_html(self, text: str) -> None:
        \"\"\"Queues formatted HTML message for asynchronous dispatch.\"\"\"
        if not self.config.enabled:
            return
        try:
            self._msg_queue.put_nowait(text)
        except queue.Full:
            logger.warning("[Telegram] Outgoing message queue full. Dropping message.")"""

new_send_methods = """    def send_message(self, text: str, disable_notification: bool = False, parse_mode: str = "HTML") -> None:
        \"\"\"Queues formatted message for asynchronous dispatch with notification sound control.\"\"\"
        if not self.config.enabled:
            return
        try:
            self._msg_queue.put_nowait((text, disable_notification, parse_mode))
        except queue.Full:
            logger.warning("[Telegram] Outgoing message queue full. Dropping message.")

    def send_raw_html(self, text: str) -> None:
        \"\"\"Queues formatted HTML message for asynchronous dispatch (sound notification by default).\"\"\"
        self.send_message(text, disable_notification=False, parse_mode="HTML")"""

if old_send_raw in code:
    code = code.replace(old_send_raw, new_send_methods)
else:
    print("WARNING: Could not find old_send_raw")

# 3. Update TelegramNotifier._worker_loop to unpack disable_notification
old_worker_unpack = """            # Maintain ~1 message per second rate limit
            elapsed = time.time() - self._last_send_time
            if elapsed < 1.0:
                time.sleep(1.0 - elapsed)

            payload = {
                "chat_id": self.config.chat_id,
                "text": msg,
                "parse_mode": "HTML"
            }"""

new_worker_unpack = """            if isinstance(msg, tuple):
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
            }"""

if old_worker_unpack in code:
    code = code.replace(old_worker_unpack, new_worker_unpack)
else:
    print("WARNING: Could not find old_worker_unpack")

# 4. Insert TelegramFeedManager class before TelegramCommandHandler
feed_manager_class = '''
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
        log_dir: str = "logs"
    ):
        self.notifier = notifier
        self.feed_interval_sec = feed_interval_sec
        self.log_dir = log_dir
        os.makedirs(self.log_dir, exist_ok=True)

        self.feed_enabled: bool = True
        self.verbose: bool = False
        self.min_level: str = "INFO"
        self.muted_until: float = 0.0

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

    def log_event(self, level: str, raw_line: str, sym: str = "", tf: str = "") -> None:
        """
        Records an event:
        1. Daily log file append.
        2. Ring buffer append.
        3. Immediate dispatch with sound if CRITICAL/TRADE/SIGNAL.
        4. Queued digest if DEBUG/INFO/DECISION (silent, batched).
        """
        level_upper = level.strip().upper()

        # 1. Daily log file write
        self._write_daily_log(raw_line)

        # 2. History buffer
        with self._history_lock:
            self._history.append(raw_line)

        # 3. High-priority immediate notifications
        if level_upper in ("CRITICAL", "TRADE", "SIGNAL"):
            emoji_tag = {
                "SIGNAL": "🚨 <b>SIGNAL ALERT</b>",
                "TRADE": "💼 <b>TRADE EVENT</b>",
                "CRITICAL": "⛔ <b>CRITICAL SYSTEM ALERT</b>"
            }.get(level_upper, f"<b>[{level_upper}]</b>")

            body = (
                f"{emoji_tag}\\n\\n"
                f"<pre>{escape_html(raw_line)}</pre>"
            )
            # Immediate dispatch with sound notification enabled
            self.notifier.send_message(body, disable_notification=False)
            return

        # 4. Check level filter
        cur_weight = LOG_LEVEL_WEIGHTS.get(level_upper, 1)
        min_weight = LOG_LEVEL_WEIGHTS.get(self.min_level, 1)
        if cur_weight < min_weight:
            return

        # 5. Check feed toggles
        if not self.feed_enabled or time.time() < self.muted_until:
            return

        with self._batch_lock:
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
            if not self._batch_queue:
                return
            lines = list(self._batch_queue)
            self._batch_queue.clear()

        if not self.feed_enabled or time.time() < self.muted_until:
            return

        header = f"📋 <b>ACTIVITY FEED DIGEST ({len(lines)} events)</b>\\n<pre>"
        footer = "</pre>"
        max_chunk = 4000  # Leave headroom below 4096 Telegram limit

        current_body = header
        for line in lines:
            escaped = escape_html(line) + "\\n"
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
                f.write(line + "\\n")
        except Exception as e:
            logger.error(f"[TelegramFeed] Failed writing log line: {e}")

'''

if "class TelegramFeedManager:" not in code:
    target_marker = "class TelegramCommandHandler:"
    code = code.replace(target_marker, feed_manager_class + "\n" + target_marker)
    print("Inserted TelegramFeedManager class.")

# 5. Update TelegramCommandHandler.__init__ to take feed_manager
old_cmd_init = """    def __init__(
        self,
        config: TelegramConfig,
        notifier: TelegramNotifier,
        status_provider: Optional[Callable[[], Dict[str, Any]]] = None,
        positions_provider: Optional[Callable[[], List[Any]]] = None,
        balance_provider: Optional[Callable[[], Dict[str, Any]]] = None,
        pause_handler: Optional[Callable[[], bool]] = None,
        resume_handler: Optional[Callable[[], bool]] = None,
        closeall_handler: Optional[Callable[[], int]] = None,
        killswitch_handler: Optional[Callable[[], bool]] = None,
        enable_handler: Optional[Callable[[], bool]] = None,
        disable_handler: Optional[Callable[[], bool]] = None
    ):
        self.config = config
        self.notifier = notifier"""

new_cmd_init = """    def __init__(
        self,
        config: TelegramConfig,
        notifier: TelegramNotifier,
        status_provider: Optional[Callable[[], Dict[str, Any]]] = None,
        positions_provider: Optional[Callable[[], List[Any]]] = None,
        balance_provider: Optional[Callable[[], Dict[str, Any]]] = None,
        pause_handler: Optional[Callable[[], bool]] = None,
        resume_handler: Optional[Callable[[], bool]] = None,
        closeall_handler: Optional[Callable[[], int]] = None,
        killswitch_handler: Optional[Callable[[], bool]] = None,
        enable_handler: Optional[Callable[[], bool]] = None,
        disable_handler: Optional[Callable[[], bool]] = None,
        feed_manager: Optional[TelegramFeedManager] = None
    ):
        self.config = config
        self.notifier = notifier
        self.feed_manager = feed_manager"""

if old_cmd_init in code:
    code = code.replace(old_cmd_init, new_cmd_init)
    print("Updated TelegramCommandHandler.__init__")
else:
    print("WARNING: Could not find old_cmd_init")

# 6. Update command dispatch in _process_message
old_dispatch = """        if cmd in ("help", "start"):
            self._handle_help()
        elif cmd == "status":
            self._handle_status()"""

new_dispatch = """        if cmd in ("help", "start"):
            self._handle_help()
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
        elif cmd == "status":
            self._handle_status()"""

if old_dispatch in code:
    code = code.replace(old_dispatch, new_dispatch)
    print("Updated command dispatch in _process_message")
else:
    print("WARNING: Could not find old_dispatch")

# 7. Add command handler helper methods to TelegramCommandHandler
cmd_handlers = '''
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
            body = f"📜 <b>LAST {len(lines)} ACTIVITY EVENTS:</b>\\n<pre>" + "\\n".join([escape_html(l) for l in lines]) + "</pre>"
            self._reply(truncate_message(body))
        else:
            self._reply("<i>No activity lines logged yet.</i>")
'''

old_help_def = "    def _handle_help(self) -> None:"
if old_help_def in code:
    code = code.replace(old_help_def, cmd_handlers + "\n" + old_help_def)
    print("Added command handlers before _handle_help")
else:
    print("WARNING: Could not find old_help_def")

# 8. Update help text
feed_help_snippet = """            "📋 <b>Activity Feed:</b>\\n"
            "• /verbose on|off - Toggle verbose debug output\\n"
            "• /level info|debug|decision - Set minimum activity feed level\\n"
            "• /feed on|off - Toggle batch digest feed\\n"
            "• /mute &lt;minutes&gt; - Mute batch digest feed for N minutes\\n"
            "• /last 20 - Show last N log events (default 20)\\n\\n"
"""
target_help_marker = "            \"🛡️ <i>Safety Notice: Chat commands cannot raise risk limits.</i>\""
if target_help_marker in code:
    code = code.replace(target_help_marker, feed_help_snippet + target_help_marker)
    print("Updated help text with activity feed commands.")

# Write back
with open(target_path, "w", encoding="utf-8") as f:
    f.write(code)

print("Successfully updated src/notifications/telegram_service.py!")
