"""
Step 2: Standalone Telegram Connectivity Verification.
Loads configuration from .env and sends one message ('Bot connected').
Outputs OK or FAILED with HTTP status code and error details.
"""

import sys
from pathlib import Path

# Ensure project root in sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent))

from src.notifications.telegram_config import TelegramConfig
from src.notifications.telegram_transport import TelegramTransport


def test_telegram_connection() -> int:
    print("=" * 70)
    print("  STEP 2: TELEGRAM BOT INTEGRATION CONNECTIVITY TEST")
    print("=" * 70)

    cfg = TelegramConfig()
    print(f"Loaded Configuration from .env:")
    print(f"  TELEGRAM_BOT_TOKEN: {'*' * 8 + cfg.bot_token[-6:] if cfg.bot_token else 'MISSING'}")
    print(f"  TELEGRAM_CHAT_ID:   {cfg.chat_id if cfg.chat_id else 'MISSING'}")
    print(f"  TELEGRAM_ENABLED:   {cfg.enabled}")
    print(f"  TELEGRAM_PROXY:     {cfg.proxy if cfg.proxy else 'None (Direct)'}")

    if not cfg.bot_token:
        print("\n[-] FAILED: TELEGRAM_BOT_TOKEN is not configured.")
        return 1

    if not cfg.chat_id:
        print("\n[-] FAILED: TELEGRAM_CHAT_ID is not configured and auto-discovery failed.")
        return 1

    transport = TelegramTransport(bot_token=cfg.bot_token, proxy=cfg.proxy)
    print("\nDispatching test message ('Bot connected') to Telegram...")

    payload = {
        "chat_id": cfg.chat_id,
        "text": "Bot connected",
        "parse_mode": "HTML"
    }

    status, data, raw_text = transport.request("POST", "sendMessage", payload=payload, timeout=10.0)

    if status == 200 and data.get("ok"):
        result = data.get("result", {})
        msg_id = result.get("message_id")
        recipient = result.get("chat", {}).get("first_name", "User")
        print(f"\n[+] OK: HTTP {status} - Message delivered successfully!")
        print(f"    Message ID: #{msg_id}")
        print(f"    Delivered to: {recipient} (Chat ID: {cfg.chat_id})")
        print(f"    Text: '{result.get('text')}'")
        print("=" * 70)
        return 0
    else:
        err_desc = data.get("description", raw_text)
        print(f"\n[-] FAILED: HTTP {status}")
        print(f"    Telegram Error: {err_desc}")
        print("=" * 70)
        return 1


if __name__ == "__main__":
    sys.exit(test_telegram_connection())
