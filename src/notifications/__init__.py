"""
Notifications Module.
Provides Telegram Alert Dispatcher, Command Handler, and Activity Feed Manager.
"""

from src.notifications.telegram_config import TelegramConfig, load_env_file, save_env_value
from src.notifications.telegram_transport import TelegramTransport, escape_html, truncate_message
from src.notifications.telegram_service import TelegramNotifier, TelegramCommandHandler, TelegramFeedManager

__all__ = [
    "TelegramConfig",
    "load_env_file",
    "save_env_value",
    "TelegramTransport",
    "escape_html",
    "truncate_message",
    "TelegramNotifier",
    "TelegramCommandHandler",
    "TelegramFeedManager"
]
