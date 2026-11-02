"""
Update script for src/execution/bridge_server.py
"""
import os

target_path = os.path.abspath("src/execution/bridge_server.py")

with open(target_path, "r", encoding="utf-8") as f:
    code = f.read()

# 1. Add Telegram imports
if "from src.notifications.telegram_service import" not in code:
    code = code.replace(
        "from typing import Dict, Any, Optional\n",
        "from typing import Dict, Any, Optional\nfrom src.notifications.telegram_config import TelegramConfig\nfrom src.notifications.telegram_service import TelegramNotifier, TelegramCommandHandler, TelegramFeedManager\n"
    )

# 2. Add attributes in __init__
init_target = "        self._pending_lock = threading.Lock()"
init_replacement = """        self._pending_lock = threading.Lock()

        # Integrated Telegram Notifications & Feed
        self.notifier: Optional[TelegramNotifier] = None
        self.feed_manager: Optional[TelegramFeedManager] = None
        self.cmd_handler: Optional[TelegramCommandHandler] = None"""

if init_target in code and "self.feed_manager" not in code:
    code = code.replace(init_target, init_replacement)

# 3. Add telegram startup in start()
start_target = "        threading.Thread(target=self._ipc_listener_loop, daemon=True, name=\"IPCListener\").start()"
start_replacement = """        threading.Thread(target=self._ipc_listener_loop, daemon=True, name="IPCListener").start()

        # 4. Start Telegram Activity Feed and Command Long-Polling
        try:
            tg_cfg = TelegramConfig()
            if tg_cfg.enabled and tg_cfg.bot_token and tg_cfg.chat_id:
                self.notifier = TelegramNotifier(tg_cfg)
                self.feed_manager = TelegramFeedManager(notifier=self.notifier, feed_interval_sec=10.0)
                self.cmd_handler = TelegramCommandHandler(
                    config=tg_cfg,
                    notifier=self.notifier,
                    status_provider=self._get_status_dict,
                    enable_handler=self._enable_trading_action,
                    disable_handler=self._disable_trading_action,
                    feed_manager=self.feed_manager
                )
                logger.info("[BridgeServer] Telegram Activity Feed & Command Handler daemon active.")
        except Exception as e:
            logger.warning(f"[BridgeServer] Failed to initialize Telegram integration: {e}")"""

if start_target in code and "Start Telegram Activity Feed" not in code:
    code = code.replace(start_target, start_replacement)

# 4. Add cleanup in stop()
stop_target = "        logger.info(\"Bridge server stopped cleanly.\")"
stop_replacement = """        if self.feed_manager:
            try:
                self.feed_manager.stop()
            except Exception:
                pass
        if self.cmd_handler:
            try:
                self.cmd_handler.stop()
            except Exception:
                pass
        if self.notifier:
            try:
                self.notifier.stop()
            except Exception:
                pass
        logger.info("Bridge server stopped cleanly.")"""

if stop_target in code and "self.feed_manager.stop()" not in code:
    code = code.replace(stop_target, stop_replacement)

# 5. Handle FEED_EVENT in _dispatch_ea_message
dispatch_target = """    def _dispatch_ea_message(self, data: Dict[str, Any]) -> None:
        \"\"\"Routes message from EA to waiting request or records handshake.\"\"\"
        # Check if greeting handshake"""

dispatch_replacement = """    def _dispatch_ea_message(self, data: Dict[str, Any]) -> None:
        \"\"\"Routes message from EA to waiting request or records handshake.\"\"\"
        # Intercept Activity Feed Events from MT5 EA
        if data.get("action") == "FEED_EVENT" or data.get("type") == "FEED_EVENT":
            lvl = data.get("level", "INFO")
            line = data.get("line", "")
            sym = data.get("symbol", "")
            tf = data.get("tf", "")

            # Windows PC audible notification on SIGNAL
            if lvl.upper() == "SIGNAL" and sys.platform.startswith("win"):
                try:
                    import winsound
                    winsound.Beep(1000, 250)
                except Exception:
                    pass

            if self.feed_manager:
                self.feed_manager.log_event(lvl, line, sym, tf)
            return

        # Check if greeting handshake"""

if dispatch_target in code and "Intercept Activity Feed Events" not in code:
    code = code.replace(dispatch_target, dispatch_replacement)

# 6. Add helper methods and handle IPC FEED_LOG
ipc_target = """                    if text:
                        try:
                            req = json.loads(text)
                            resp = self.forward_request_to_ea(req)"""

ipc_replacement = """                    if text:
                        try:
                            req = json.loads(text)
                            if req.get("action") == "FEED_LOG":
                                lvl = req.get("level", "INFO")
                                line = req.get("line", "")
                                sym = req.get("symbol", "")
                                tf = req.get("tf", "")
                                if self.feed_manager:
                                    self.feed_manager.log_event(lvl, line, sym, tf)
                                resp = {"status": "SUCCESS", "message": "Log processed"}
                            elif req.get("action") == "FEED_GET_LAST":
                                count = int(req.get("count", 20))
                                lines = self.feed_manager.get_last_lines(count) if self.feed_manager else []
                                resp = {"status": "SUCCESS", "lines": lines}
                            elif req.get("action") == "TRIGGER_SIGNAL":
                                # Trigger local sound and feed alert
                                if sys.platform.startswith("win"):
                                    try:
                                        import winsound
                                        winsound.Beep(1000, 250)
                                    except Exception:
                                        pass
                                line = req.get("line", f"[SIGNAL] Test Signal triggered on {req.get('symbol', 'BTCUSD.m')}")
                                if self.feed_manager:
                                    self.feed_manager.log_event("SIGNAL", line, req.get("symbol", ""), req.get("tf", "M15"))
                                resp = self.forward_request_to_ea(req) if self.is_ea_connected() else {"status": "SUCCESS", "message": "Local signal triggered"}
                            else:
                                resp = self.forward_request_to_ea(req)"""

if ipc_target in code and "req.get(\"action\") == \"FEED_LOG\"" not in code:
    code = code.replace(ipc_target, ipc_replacement)

# 7. Add helper callback methods
callbacks_code = """
    def _get_status_dict(self) -> Dict[str, Any]:
        \"\"\"Provides current system and account status for Telegram /status command.\"\"\"
        st: Dict[str, Any] = {
            "bridge_connected": self.is_ea_connected(),
            "state": "RUNNING" if self.is_ea_connected() else "STANDBY"
        }
        if self.is_ea_connected():
            try:
                acc_resp = self.forward_request_to_ea({"action": "ACCOUNT"}, timeout_sec=3.0)
                if acc_resp.get("status") == "SUCCESS":
                    d = acc_resp.get("data", {})
                    st.update(d)
            except Exception as e:
                logger.debug(f"Account query failed in status callback: {e}")
        return st

    def _enable_trading_action(self) -> bool:
        if not self.is_ea_connected():
            return False
        try:
            res = self.forward_request_to_ea({"action": "ENABLE"}, timeout_sec=5.0)
            return res.get("status") == "SUCCESS"
        except Exception:
            return False

    def _disable_trading_action(self) -> bool:
        if not self.is_ea_connected():
            return False
        try:
            res = self.forward_request_to_ea({"action": "DISABLE"}, timeout_sec=5.0)
            return res.get("status") == "SUCCESS"
        except Exception:
            return False
"""

if "_get_status_dict" not in code:
    main_marker = 'if __name__ == "__main__":'
    code = code.replace(main_marker, callbacks_code + "\n\n" + main_marker)

with open(target_path, "w", encoding="utf-8") as f:
    f.write(code)

print("Successfully updated src/execution/bridge_server.py!")
