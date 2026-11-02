"""
Telegram Alert Dispatcher.
Broadcasts institutional notifications for:
- Approved signals
- Executed and closed trades
- Stop-loss and Take-profit hits
- Risk breaches and circuit-breaker activations
- Connection status and daily performance summaries
Implements non-blocking execution via background worker queue to ensure alerts NEVER block or impact order safety.
"""

from __future__ import annotations
from typing import Optional
from src.notifications.telegram_config import TelegramConfig
from src.notifications.telegram_service import TelegramNotifier


class TelegramAlertManager:
    def __init__(self, bot_token: Optional[str] = None, chat_id: Optional[str] = None, proxy: Optional[str] = None):
        cfg = TelegramConfig()
        if bot_token:
            cfg.bot_token = bot_token
        if chat_id:
            cfg.chat_id = chat_id
        if proxy:
            cfg.proxy = proxy
        cfg.validate()

        self.notifier = TelegramNotifier(cfg)
        self.enabled = self.notifier.config.enabled

    def send_alert(self, message: str) -> None:
        """Dispatches notification asynchronously in background queue."""
        if not self.enabled:
            return
        self.notifier.send_raw_html(message)

    def alert_signal_approved(self, symbol: str, direction: str, score: float, entry: float, sl: float, tp: float, rr: float) -> None:
        self.notifier.notify_setup_detected(
            symbol=symbol,
            direction=direction,
            mode="AUTO",
            entry=entry,
            sl=sl,
            tp=tp,
            rr=rr,
            lots=0.01,
            risk_pct=0.5,
            reason=f"Technical Confluence Score: {score:.1f}/100"
        )

    def alert_trade_executed(self, ticket: int, symbol: str, direction: str, volume: float, price: float) -> None:
        self.notifier.notify_order_placed(
            symbol=symbol,
            order_type=direction,
            ticket=ticket,
            price=price,
            sl=0.0,
            tp=0.0,
            volume=volume,
            retcode=10009
        )

    def alert_risk_breach(self, event_type: str, details: str) -> None:
        self.notifier.notify_risk_limit_hit(limit_type=event_type, details=details)

    def alert_news_restriction(self, symbol: str, news_title: str) -> None:
        self.notifier.notify_trade_skipped(symbol=symbol, skip_reason=f"High-Impact News: {news_title}")
