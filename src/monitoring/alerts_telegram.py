"""
Telegram Alert Dispatcher.
Broadcasts institutional notifications for:
- Bot started & continuous scanning active
- Approved BUY/SELL signals & order submissions
- Executed and closed trades with realized P/L
- Stop-loss, Break-even, Trailing-stop, and Partial profit updates
- Trade rejection with exact reason (never news restriction)
- Connection interruption and recovery
- Risk breaches and circuit-breaker activations
- Concise trading summary
Implements non-blocking execution via background worker queue to ensure alerts NEVER block or impact order safety.
"""

from __future__ import annotations
from typing import Any, Optional
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

    def alert_bot_started(self, details: str = "Continuous M1/M5/M15 Scalping Engine Active") -> None:
        if not self.enabled:
            return
        self.notifier.notify_bot_status("STARTED", details)

    def alert_connection_status(self, status: str, details: str = "") -> None:
        if not self.enabled:
            return
        self.notifier.notify_bridge_status(status, details)

    def alert_signal_approved(
        self,
        symbol: str,
        direction: str,
        score: float,
        entry: float,
        sl: float,
        tp: float,
        rr: float,
        lots: float = 0.01,
        risk_pct: float = 0.5,
        reason: str = "",
    ) -> None:
        if not self.enabled:
            return
        self.notifier.notify_setup_detected(
            symbol=symbol,
            direction=direction,
            mode="CONTINUOUS_SCALPING",
            entry=entry,
            sl=sl,
            tp=tp,
            rr=rr,
            lots=lots,
            risk_pct=risk_pct,
            reason=reason or f"Technical Confluence Score: {score:.1f}/100",
        )

    def alert_order_submitted(
        self,
        symbol: str,
        direction: str,
        volume: float,
        price: float,
        sl: float,
        tp: float,
        request_id: str = "",
    ) -> None:
        if not self.enabled:
            return
        body = (
            f"📤 <b>ORDER SUBMITTED TO MT5</b>\n\n"
            f"💱 <b>Symbol:</b> <code>{symbol}</code>\n"
            f"📌 <b>Direction:</b> <code>{direction}</code>\n"
            f"📦 <b>Volume:</b> <code>{volume:.2f} lots</code> @ <code>{price:.5f}</code>\n"
            f"🛑 <b>SL:</b> <code>{sl:.5f}</code> | 🎯 <b>TP:</b> <code>{tp:.5f}</code>\n"
            f"🆔 <b>Request ID:</b> <code>{request_id}</code>\n"
            f"⏳ <i>Awaiting broker execution confirmation...</i>"
        )
        self.notifier.send_raw_html(self.notifier._format_header_footer(body, symbol=symbol))

    def alert_ipc_timeout(self, symbol: str, request_id: str, details: str = "") -> None:
        if not self.enabled:
            return
        body = (
            f"⏱️ <b>IPC TIMEOUT DETECTED</b>\n\n"
            f"💱 <b>Symbol:</b> <code>{symbol}</code>\n"
            f"🆔 <b>Request ID:</b> <code>{request_id}</code>\n"
            f"⚠️ <b>Status:</b> Bridge response timed out. Order reconciliation triggered in MT5.\n"
            f"📝 <b>Details:</b> {details or 'Awaiting terminal verification.'}"
        )
        self.notifier.send_raw_html(self.notifier._format_header_footer(body, symbol=symbol))

    def alert_trade_executed(
        self,
        ticket: int,
        symbol: str,
        direction: str,
        volume: float,
        price: float,
        sl: float = 0.0,
        tp: float = 0.0,
    ) -> None:
        if not self.enabled:
            return
        self.notifier.notify_order_placed(
            symbol=symbol,
            order_type=direction,
            ticket=ticket,
            price=price,
            sl=sl,
            tp=tp,
            volume=volume,
            retcode=10009,
        )

    def alert_break_even(self, symbol: str, ticket: int, new_sl: float) -> None:
        if not self.enabled:
            return
        self.notifier.notify_break_even(symbol=symbol, ticket=ticket, new_sl=new_sl)

    def alert_trailing_stop(
        self,
        symbol: str,
        ticket: int,
        new_sl: float,
        current_price: float,
        points_profit: float = 0.0,
    ) -> None:
        if not self.enabled:
            return
        self.notifier.notify_trailing_stop(
            symbol=symbol,
            ticket=ticket,
            new_sl=new_sl,
            current_price=current_price,
            points_profit=points_profit,
        )

    def alert_partial_close(
        self,
        symbol: str,
        ticket: int,
        closed_volume: float,
        remaining_volume: float,
        pnl: float,
    ) -> None:
        if not self.enabled:
            return
        self.notifier.notify_partial_close(
            symbol=symbol,
            ticket=ticket,
            closed_volume=closed_volume,
            remaining_volume=remaining_volume,
            pnl=pnl,
        )

    def alert_trade_closed(
        self,
        symbol: str,
        ticket: int,
        direction: str,
        close_reason: str,
        money_pnl: float,
        points_pnl: float = 0.0,
        r_pnl: float = 0.0,
    ) -> None:
        if not self.enabled:
            return
        self.notifier.notify_trade_closed(
            symbol=symbol,
            ticket=ticket,
            direction=direction,
            close_reason=close_reason,
            money_pnl=money_pnl,
            points_pnl=points_pnl,
            r_pnl=r_pnl,
        )

    def alert_order_rejected(self, symbol: str, action: str, reason: str, retcode: int = 0) -> None:
        if not self.enabled:
            return
        self.notifier.notify_order_rejected(symbol=symbol, action=action, reason=reason, retcode=retcode)

    def alert_risk_breach(self, event_type: str, details: str) -> None:
        if not self.enabled:
            return
        self.notifier.notify_risk_limit_hit(limit_type=event_type, details=details)

    def alert_news_restriction(self, symbol: str, news_title: str) -> None:
        """No-op: news-based trading restrictions and skip alerts have been completely removed."""
        return

    def send_decision_outcome(self, outcome: Any) -> bool:
        if not self.enabled:
            return False
        if getattr(outcome, "reason_code", "") == "NEWS_FILTER":
            return False
        return self.notifier.notify_decision_outcome(outcome)
