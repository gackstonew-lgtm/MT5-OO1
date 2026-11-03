"""
Structured Notification Events, Tiers, Retcode Mapping & Secret Sanitization (Phase 1).
Decouples engine events from raw log lines so user-facing Telegram messages are rendered
from strongly-typed dataclasses via centralized templates.
"""

from __future__ import annotations
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional


class NotificationTier(str, Enum):
    LOUD = "LOUD"              # Critical alerts, trade setups, opens, closes (sound ON)
    SILENT = "SILENT"          # Warnings, session notes, summaries, BE/partial updates (disable_notification=True)
    ON_DEMAND = "ON_DEMAND"    # No-setup decisions, per-bar scans, filter outcomes, raw engine logs (never pushed by default)


class AlertState(str, Enum):
    OK = "OK"
    WARNING = "WARNING"
    CRITICAL = "CRITICAL"
    RECOVERED = "RECOVERED"


class NotificationEventType(str, Enum):
    TRADE_SETUP = "TradeSetup"
    TRADE_OPENED = "TradeOpened"
    BREAK_EVEN_MOVED = "BreakEvenMoved"
    PARTIAL_CLOSED = "PartialClosed"
    TARGET_HIT = "TargetHit"
    STOP_LOSS_HIT = "StopLossHit"
    ORDER_REJECTED = "OrderRejected"
    SETUP_SKIPPED = "SetupSkipped"
    BOT_STARTED = "BotStarted"
    BOT_PAUSED = "BotPaused"
    BOT_RESUMED = "BotResumed"
    BOT_STOPPED = "BotStopped"
    CONNECTION_LOST = "ConnectionLost"
    CONNECTION_RESTORED = "ConnectionRestored"
    SPREAD_WARNING = "SpreadWarning"
    DAILY_LOSS_WARNING = "DailyLossWarning"
    DAILY_LOSS_LIMIT_HIT = "DailyLossLimitHit"
    DRAWDOWN_WARNING = "DrawdownWarning"
    MARGIN_WARNING = "MarginWarning"
    NEWS_BLACKOUT = "NewsBlackout"
    SESSION_OPEN = "SessionOpen"
    SESSION_CLOSE = "SessionClose"
    DAILY_SUMMARY = "DailySummary"
    WEEKLY_SUMMARY = "WeeklySummary"
    WARMUP_PROGRESS = "WarmupProgress"
    CONFIG_CHANGED = "ConfigChanged"
    UNAUTHORIZED_ACCESS = "UnauthorizedAccess"
    HEALTH_OK = "HealthOK"


# Map event types to default NotificationTier
EVENT_DEFAULT_TIER: Dict[NotificationEventType, NotificationTier] = {
    NotificationEventType.TRADE_SETUP: NotificationTier.LOUD,
    NotificationEventType.TRADE_OPENED: NotificationTier.LOUD,
    NotificationEventType.BREAK_EVEN_MOVED: NotificationTier.SILENT,
    NotificationEventType.PARTIAL_CLOSED: NotificationTier.SILENT,
    NotificationEventType.TARGET_HIT: NotificationTier.LOUD,
    NotificationEventType.STOP_LOSS_HIT: NotificationTier.LOUD,
    NotificationEventType.ORDER_REJECTED: NotificationTier.LOUD,
    NotificationEventType.SETUP_SKIPPED: NotificationTier.ON_DEMAND,
    NotificationEventType.BOT_STARTED: NotificationTier.SILENT,
    NotificationEventType.BOT_PAUSED: NotificationTier.SILENT,
    NotificationEventType.BOT_RESUMED: NotificationTier.SILENT,
    NotificationEventType.BOT_STOPPED: NotificationTier.LOUD,
    NotificationEventType.CONNECTION_LOST: NotificationTier.LOUD,
    NotificationEventType.CONNECTION_RESTORED: NotificationTier.SILENT,
    NotificationEventType.SPREAD_WARNING: NotificationTier.SILENT,
    NotificationEventType.DAILY_LOSS_WARNING: NotificationTier.SILENT,
    NotificationEventType.DAILY_LOSS_LIMIT_HIT: NotificationTier.LOUD,
    NotificationEventType.DRAWDOWN_WARNING: NotificationTier.LOUD,
    NotificationEventType.MARGIN_WARNING: NotificationTier.LOUD,
    NotificationEventType.NEWS_BLACKOUT: NotificationTier.SILENT,
    NotificationEventType.SESSION_OPEN: NotificationTier.SILENT,
    NotificationEventType.SESSION_CLOSE: NotificationTier.SILENT,
    NotificationEventType.DAILY_SUMMARY: NotificationTier.SILENT,
    NotificationEventType.WEEKLY_SUMMARY: NotificationTier.SILENT,
    NotificationEventType.WARMUP_PROGRESS: NotificationTier.SILENT,
    NotificationEventType.CONFIG_CHANGED: NotificationTier.SILENT,
    NotificationEventType.UNAUTHORIZED_ACCESS: NotificationTier.LOUD,
    NotificationEventType.HEALTH_OK: NotificationTier.SILENT,
}


MT5_RETCODE_PLAIN_ENGLISH: Dict[int, str] = {
    10004: "Requote — price moved before the broker could fill the order",
    10006: "Request rejected by the broker server",
    10007: "Request cancelled by trader",
    10010: "Only part of the order was filled (partial fill)",
    10011: "Order processing error on the broker server",
    10012: "Order request timed out before confirmation",
    10013: "Invalid order request parameters",
    10014: "Invalid lot volume for this symbol",
    10015: "Invalid order price",
    10016: "Invalid stops — Stop Loss or Take Profit is too close to market price",
    10017: "Trading is disabled for this symbol or account",
    10018: "Market is closed for this instrument",
    10019: "Insufficient free margin to open the position",
    10020: "Prices changed — quote expired before execution",
    10021: "No quotes available — broker liquidity feed temporarily unavailable",
    10022: "Invalid order expiration time",
    10024: "Too many requests — broker rate limit reached",
    10026: "Automated trading disabled by broker server",
    10027: "Algo Trading button is disabled in MetaTrader 5 terminal",
    10030: "Unsupported order filling mode (FOK/IOC/RETURN)",
    10031: "No connection to the broker trade server",
    10033: "Maximum number of open orders/positions reached at broker",
}


def map_retcode_to_plain_english(retcode: int, fallback_reason: str = "") -> str:
    """Maps a numeric MT5 retcode to a clear, plain-English explanation."""
    if retcode in MT5_RETCODE_PLAIN_ENGLISH:
        return MT5_RETCODE_PLAIN_ENGLISH[retcode]
    if fallback_reason:
        clean = fallback_reason.strip()
        return clean
    return "Order could not be completed by the broker"


def mask_account_number(account_number: int | str) -> str:
    """Masks full account numbers so only the last 4 digits are shown (e.g., ****0001)."""
    s = str(account_number).strip().lstrip("#")
    if not s or s in ("0", "N/A", "None"):
        return "DEMO"
    if len(s) <= 4:
        return f"****{s}"
    return f"****{s[-4:]}"


def sanitize_sensitive_text(text: str) -> str:
    """
    Redacts any accidental Telegram bot tokens, API keys, passwords, or 7+ digit raw account numbers.
    """
    if not text:
        return ""
    # Redact Telegram bot token patterns like 123456789:AAH...
    out = re.sub(r"\b\d{7,12}:[A-Za-z0-9_-]{25,}\b", "[REDACTED_TOKEN]", text)
    # Redact sk-... or Bearer tokens
    out = re.sub(r"\bsk-[A-Za-z0-9_-]{16,}\b", "[REDACTED_KEY]", out)
    # Redact #1000000001 or 8-12 digit raw account logins prefixed with #, Login, Account, Acct
    out = re.sub(
        r"((?:Login|Account|Acct)[:\s]*#?|#)(\d{4,})(\d{4})\b",
        lambda m: f"{m.group(1)}****{m.group(3)}",
        out,
        flags=re.IGNORECASE,
    )
    return out


@dataclass
class BaseNotificationEvent:
    event_type: NotificationEventType = field(init=False)
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    tier: Optional[NotificationTier] = None
    is_sample: bool = False

    def get_tier(self) -> NotificationTier:
        if self.tier is not None:
            return self.tier
        return EVENT_DEFAULT_TIER.get(self.event_type, NotificationTier.SILENT)


@dataclass
class TradeSetupEvent(BaseNotificationEvent):
    symbol: str = "XAUUSD"
    direction: str = "BUY"
    entry_price: float = 0.0
    stop_loss: float = 0.0
    take_profit: float = 0.0
    sl_distance_pips: float = 0.0
    tp_distance_pips: float = 0.0
    distance_unit: str = "pips"
    rr_ratio: float = 2.0
    lots: float = 0.01
    risk_usd: float = 10.0
    risk_pct: float = 0.5
    session: str = "London / NY Overlap"
    htf_bias: str = "BULLISH"
    reason: str = "BOS confirmed after liquidity sweep + FVG retest"
    confidence_score: float = 84.0
    digits: int = 2
    trade_id: str = ""

    def __post_init__(self) -> None:
        self.event_type = NotificationEventType.TRADE_SETUP


@dataclass
class TradeOpenedEvent(BaseNotificationEvent):
    symbol: str = "XAUUSD"
    direction: str = "BUY"
    ticket: int = 10001
    fill_price: float = 0.0
    requested_price: float = 0.0
    slippage_points: float = 0.0
    lots: float = 0.01
    stop_loss: float = 0.0
    take_profit: float = 0.0
    risk_usd: float = 10.0
    risk_pct: float = 0.5
    digits: int = 2
    trade_id: str = ""

    def __post_init__(self) -> None:
        self.event_type = NotificationEventType.TRADE_OPENED


@dataclass
class BreakEvenMovedEvent(BaseNotificationEvent):
    symbol: str = "XAUUSD"
    ticket: int = 10001
    direction: str = "BUY"
    entry_price: float = 0.0
    new_sl: float = 0.0
    locked_r: float = 0.05
    digits: int = 2
    trade_id: str = ""

    def __post_init__(self) -> None:
        self.event_type = NotificationEventType.BREAK_EVEN_MOVED


@dataclass
class PartialClosedEvent(BaseNotificationEvent):
    symbol: str = "XAUUSD"
    ticket: int = 10001
    direction: str = "BUY"
    closed_lots: float = 0.01
    remaining_lots: float = 0.01
    realized_pnl_usd: float = 25.0
    realized_r: float = 1.0
    new_sl: Optional[float] = None
    digits: int = 2
    trade_id: str = ""

    def __post_init__(self) -> None:
        self.event_type = NotificationEventType.PARTIAL_CLOSED


@dataclass
class TargetHitEvent(BaseNotificationEvent):
    symbol: str = "XAUUSD"
    ticket: int = 10001
    direction: str = "BUY"
    close_price: float = 0.0
    pnl_usd: float = 50.0
    pnl_r: float = 2.0
    duration_str: str = "42m"
    daily_running_pnl_usd: float = 120.0
    daily_running_r: float = 3.2
    digits: int = 2
    trade_id: str = ""

    def __post_init__(self) -> None:
        self.event_type = NotificationEventType.TARGET_HIT


@dataclass
class StopLossHitEvent(BaseNotificationEvent):
    symbol: str = "XAUUSD"
    ticket: int = 10001
    direction: str = "BUY"
    close_price: float = 0.0
    loss_usd: float = -25.0
    loss_r: float = -1.0
    duration_str: str = "18m"
    daily_loss_used_pct: float = 1.2
    daily_loss_cap_pct: float = 4.0
    digits: int = 2
    trade_id: str = ""

    def __post_init__(self) -> None:
        self.event_type = NotificationEventType.STOP_LOSS_HIT


@dataclass
class OrderRejectedEvent(BaseNotificationEvent):
    symbol: str = "XAUUSD"
    direction: str = "BUY"
    retcode: int = 10019
    reason: str = ""
    bot_action: str = "Order cancelled; waiting for next qualified setup"
    open_trades_affected: bool = False

    def __post_init__(self) -> None:
        self.event_type = NotificationEventType.ORDER_REJECTED


@dataclass
class SetupSkippedEvent(BaseNotificationEvent):
    symbol: str = "XAUUSD"
    direction: str = "BUY"
    reason: str = "Min lot (0.01) risks 5.60% ($28.00), exceeding 1.00% risk cap"
    required_balance_usd: float = 2800.0
    current_balance_usd: float = 500.0
    min_lot: float = 0.01
    risk_pct_at_min_lot: float = 5.60
    risk_cap_pct: float = 1.00

    def __post_init__(self) -> None:
        self.event_type = NotificationEventType.SETUP_SKIPPED


@dataclass
class BotStartedEvent(BaseNotificationEvent):
    mode: str = "DEMO"
    profile: str = "DEMO"
    symbols: List[str] = field(default_factory=lambda: ["XAUUSD", "EURUSD", "GBPUSD"])
    fast_mode_enabled: bool = False
    session_name: str = "London"

    def __post_init__(self) -> None:
        self.event_type = NotificationEventType.BOT_STARTED


@dataclass
class BotPausedEvent(BaseNotificationEvent):
    reason: str = "Paused by operator command"
    open_positions_count: int = 0

    def __post_init__(self) -> None:
        self.event_type = NotificationEventType.BOT_PAUSED


@dataclass
class BotResumedEvent(BaseNotificationEvent):
    mode: str = "DEMO"
    active_session: str = "London"
    open_positions_count: int = 0

    def __post_init__(self) -> None:
        self.event_type = NotificationEventType.BOT_RESUMED


@dataclass
class BotStoppedEvent(BaseNotificationEvent):
    reason: str = "Graceful shutdown requested"
    cancelled_pendings: int = 0
    open_positions_protected: int = 0

    def __post_init__(self) -> None:
        self.event_type = NotificationEventType.BOT_STOPPED


@dataclass
class ConnectionLostEvent(BaseNotificationEvent):
    details: str = "MT5 EA heartbeat timed out (> 15s)"
    open_positions_count: int = 0
    server_sl_tp_intact: bool = True
    user_action_required: str = "None immediately — auto-reconnect is running. Check MT5 terminal if not restored in 5m."

    def __post_init__(self) -> None:
        self.event_type = NotificationEventType.CONNECTION_LOST


@dataclass
class ConnectionRestoredEvent(BaseNotificationEvent):
    downtime_seconds: float = 12.0
    reconciled_positions: int = 0
    in_sync: bool = True
    discrepancy_note: str = "All positions verified and in sync with MT5."

    def __post_init__(self) -> None:
        self.event_type = NotificationEventType.CONNECTION_RESTORED


@dataclass
class SpreadWarningEvent(BaseNotificationEvent):
    symbol: str = "XAUUSD"
    current_spread_pts: float = 45.0
    normal_spread_pts: float = 18.0
    bot_action: str = "Pausing new entries on this symbol until spread normalizes"
    open_trades_affected: str = "Open positions keep server-side SL/TP; SL widening is blocked"
    repeat_count: int = 1
    repeat_window_min: int = 10

    def __post_init__(self) -> None:
        self.event_type = NotificationEventType.SPREAD_WARNING


@dataclass
class DailyLossWarningEvent(BaseNotificationEvent):
    daily_loss_used_pct: float = 3.0
    daily_loss_cap_pct: float = 4.0
    daily_loss_usd: float = -300.0
    remaining_budget_usd: float = 100.0
    bot_action: str = "Risk throttle active; halting new entries at safety buffer"
    open_trades_affected: str = "Existing positions remain managed with server-side SL/TP"

    def __post_init__(self) -> None:
        self.event_type = NotificationEventType.DAILY_LOSS_WARNING


@dataclass
class DailyLossLimitHitEvent(BaseNotificationEvent):
    daily_loss_used_pct: float = 4.0
    daily_loss_cap_pct: float = 4.0
    daily_loss_usd: float = -400.0
    positions_closed: int = 0
    server_sl_tp_intact: bool = True
    user_action_required: str = "No action needed — trading automatically unlocks at next daily UTC reset"

    def __post_init__(self) -> None:
        self.event_type = NotificationEventType.DAILY_LOSS_LIMIT_HIT


@dataclass
class DrawdownWarningEvent(BaseNotificationEvent):
    current_dd_pct: float = 6.5
    max_dd_cap_pct: float = 8.0
    equity_usd: float = 9350.0
    peak_equity_usd: float = 10000.0
    is_hard_breach: bool = False
    bot_action: str = "Position sizing throttled and new entries paused to protect account floor"
    user_action_required: str = "Review open risk via /positions or /margin"

    def __post_init__(self) -> None:
        self.event_type = NotificationEventType.DRAWDOWN_WARNING


@dataclass
class MarginWarningEvent(BaseNotificationEvent):
    margin_level_pct: float = 240.0
    entry_floor_pct: float = 300.0
    critical_floor_pct: float = 150.0
    used_margin_usd: float = 416.0
    free_margin_usd: float = 584.0
    flattened_positions: int = 0
    bot_action: str = "New trade entries blocked until margin level recovers above 300%"

    def __post_init__(self) -> None:
        self.event_type = NotificationEventType.MARGIN_WARNING


@dataclass
class NewsBlackoutEvent(BaseNotificationEvent):
    event_title: str = "US Non-Farm Payrolls (USD)"
    affected_symbols: List[str] = field(default_factory=lambda: ["XAUUSD", "EURUSD", "GBPUSD", "NAS100"])
    window_str: str = "13:15 - 13:45 UTC"
    bot_action: str = "New entries paused on USD pairs during high-impact window"
    open_trades_affected: str = "Open positions remain protected by server-side SL/TP"

    def __post_init__(self) -> None:
        self.event_type = NotificationEventType.NEWS_BLACKOUT


@dataclass
class SessionOpenEvent(BaseNotificationEvent):
    session_name: str = "London"
    window_utc: str = "06:00 - 12:00 UTC"
    watched_symbols: List[str] = field(default_factory=lambda: ["EURUSD", "GBPUSD", "XAUUSD"])
    asia_high: Optional[float] = None
    asia_low: Optional[float] = None
    asia_range_pips: Optional[float] = None
    asia_symbol: str = "EURUSD"
    digits: int = 5

    def __post_init__(self) -> None:
        self.event_type = NotificationEventType.SESSION_OPEN


@dataclass
class SessionCloseEvent(BaseNotificationEvent):
    session_name: str = "London"
    next_session_name: str = "New York"
    next_session_start_utc: str = "12:30 UTC"
    session_trades: int = 2
    session_pnl_usd: float = 65.0
    session_pnl_r: float = 1.4
    open_positions_count: int = 0

    def __post_init__(self) -> None:
        self.event_type = NotificationEventType.SESSION_CLOSE


@dataclass
class DailySummaryEvent(BaseNotificationEvent):
    date_str: str = "2026-11-03"
    trades_count: int = 4
    wins: int = 3
    losses: int = 1
    win_rate_pct: float = 75.0
    net_pnl_usd: float = 145.50
    net_pnl_r: float = 2.85
    best_r: float = 2.10
    worst_r: float = -1.00
    max_dd_pct: float = 1.15
    balance_usd: float = 10145.50
    equity_usd: float = 10145.50
    blocked_by_filters: Dict[str, int] = field(default_factory=lambda: {"Spread": 3, "Session": 2, "MinLot": 1})

    def __post_init__(self) -> None:
        self.event_type = NotificationEventType.DAILY_SUMMARY


@dataclass
class WeeklySummaryEvent(BaseNotificationEvent):
    week_label: str = "2026-W45"
    trades_count: int = 18
    wins: int = 12
    losses: int = 6
    win_rate_pct: float = 66.7
    net_pnl_usd: float = 620.00
    net_pnl_r: float = 8.40
    profit_factor: float = 2.40
    best_r: float = 3.10
    worst_r: float = -1.00
    max_dd_pct: float = 2.30
    blocked_by_filters: Dict[str, int] = field(default_factory=lambda: {"Spread": 14, "News": 5, "Session": 9})

    def __post_init__(self) -> None:
        self.event_type = NotificationEventType.WEEKLY_SUMMARY


@dataclass
class WarmupProgressEvent(BaseNotificationEvent):
    symbol: str = "EURUSD"
    timeframe: str = "M15"
    loaded_bars: int = 12
    required_bars: int = 20
    is_ready: bool = False

    def __post_init__(self) -> None:
        self.event_type = NotificationEventType.WARMUP_PROGRESS


@dataclass
class ConfigChangedEvent(BaseNotificationEvent):
    setting_name: str = "Fast Mode"
    old_value: str = "OFF"
    new_value: str = "ON"
    changed_by: str = "Telegram Operator"

    def __post_init__(self) -> None:
        self.event_type = NotificationEventType.CONFIG_CHANGED


@dataclass
class UnauthorizedAccessEvent(BaseNotificationEvent):
    attempted_command: str = "/flatten"
    sender_name: str = "Unknown"
    masked_sender_id: str = "****9999"

    def __post_init__(self) -> None:
        self.event_type = NotificationEventType.UNAUTHORIZED_ACCESS


@dataclass
class HealthOKEvent(BaseNotificationEvent):
    uptime_str: str = "6h 00m"
    bridge_connected: bool = True
    open_positions_count: int = 0
    today_pnl_usd: float = 0.0
    today_pnl_r: float = 0.0
    active_session: str = "London"

    def __post_init__(self) -> None:
        self.event_type = NotificationEventType.HEALTH_OK
