"""
Unit Tests for Telegram Notifications, Config, and Command Handling.
"""

import unittest
import time
from unittest.mock import MagicMock, patch
from src.notifications.telegram_config import TelegramConfig
from src.notifications.telegram_transport import escape_html, truncate_message, TelegramTransport
from src.notifications.telegram_service import TelegramNotifier, TelegramCommandHandler


class TestTelegramModule(unittest.TestCase):
    def test_escape_html(self):
        raw = "BTC & ETH < 5000 > 'test'"
        escaped = escape_html(raw)
        self.assertEqual(escaped, "BTC &amp; ETH &lt; 5000 &gt; &#x27;test&#x27;")

    def test_truncate_message(self):
        long_text = "A" * 5000
        truncated = truncate_message(long_text, max_chars=4096)
        self.assertLessEqual(len(truncated), 4096)
        self.assertTrue(truncated.endswith("<i>[Message truncated...]</i>"))

    def test_missing_config_disables_gracefully(self):
        with patch.dict("os.environ", {"TELEGRAM_BOT_TOKEN": "", "TELEGRAM_CHAT_ID": ""}, clear=True):
            cfg = TelegramConfig(env_path="nonexistent.env")
            self.assertFalse(cfg.enabled)
            # Notifier should not crash even if disabled
            notifier = TelegramNotifier(cfg)
            notifier.notify_bot_status("STARTED", "Testing with Telegram disabled")
            self.assertFalse(notifier._is_running)

    def test_event_formatting(self):
        cfg = TelegramConfig()
        cfg.bot_token = "12345:TEST_TOKEN"
        cfg.chat_id = "8649330717"
        cfg.enabled = True

        notifier = TelegramNotifier(cfg, account_number=1000000001)
        # Mock transport
        mock_transport = MagicMock()
        mock_transport.request.return_value = (200, {"ok": True}, "OK")
        notifier.transport = mock_transport

        # Test setup event formatting
        notifier.notify_setup_detected(
            symbol="BTCUSD.m",
            direction="BUY",
            mode="SCALP",
            entry=77600.0,
            sl=76800.0,
            tp=79100.0,
            rr=1.88,
            lots=0.02,
            risk_pct=0.5,
            reason="M15 Liquidity Sweep of PDL"
        )

        # Allow worker to dequeue
        time.sleep(1.2)
        notifier.stop()

        self.assertTrue(mock_transport.request.called)
        call_args = mock_transport.request.call_args[1]
        payload = call_args.get("payload", {})
        text = payload.get("text", "")

        self.assertIn("TRADING SETUP DETECTED", text)
        self.assertIn("BTCUSD.m", text)
        self.assertIn("****0001", text)
        self.assertNotIn("1000000001", text)
        self.assertIn("M15 Liquidity Sweep of PDL", text)

    def test_command_security_filter(self):
        cfg = TelegramConfig()
        cfg.bot_token = "12345:TEST_TOKEN"
        cfg.chat_id = "8649330717"
        cfg.enabled = True

        notifier = TelegramNotifier(cfg)
        notifier.stop()

        cmd_handler = TelegramCommandHandler(cfg, notifier)
        cmd_handler._reply = MagicMock()

        # 1. Message from intruder/different chat ID
        intruder_msg = {
            "chat": {"id": 999999999},
            "from": {"first_name": "Intruder"},
            "text": "/killswitch"
        }
        cmd_handler._process_message(intruder_msg)
        # Verify no reply sent and no confirmation set
        self.assertFalse(cmd_handler._reply.called)
        self.assertIsNone(cmd_handler._pending_confirmation)

        # 2. Message from authorized user
        auth_msg = {
            "chat": {"id": 8649330717},
            "from": {"first_name": "Jetsky"},
            "text": "/help"
        }
        cmd_handler._process_message(auth_msg)
        self.assertTrue(cmd_handler._reply.called)

    def test_confirmation_requirement_for_critical_commands(self):
        cfg = TelegramConfig()
        cfg.bot_token = "12345:TEST_TOKEN"
        cfg.chat_id = "8649330717"
        cfg.enabled = True

        notifier = TelegramNotifier(cfg)
        notifier.stop()

        mock_close = MagicMock(return_value=2)
        cmd_handler = TelegramCommandHandler(cfg, notifier, closeall_handler=mock_close)
        cmd_handler._reply = MagicMock()

        # Step 1: User sends /closeall
        cmd_handler._process_message({
            "chat": {"id": 8649330717},
            "from": {"first_name": "Jetsky"},
            "text": "/closeall"
        })

        # Handler must request confirmation and set pending
        self.assertIsNotNone(cmd_handler._pending_confirmation)
        self.assertEqual(cmd_handler._pending_confirmation[0], "closeall")
        self.assertFalse(mock_close.called)

        # Step 2: User confirms with 'yes'
        cmd_handler._process_message({
            "chat": {"id": 8649330717},
            "from": {"first_name": "Jetsky"},
            "text": "yes"
        })

        # Handler executes closeall
        self.assertTrue(mock_close.called)
        self.assertIsNone(cmd_handler._pending_confirmation)


if __name__ == "__main__":
    unittest.main()
