"""
Strongly-typed data contracts for the Multi-Timeframe Algorithmic Trading Bot.
Provides immutable and validated models for market data, signals, orders, and risk.
"""

from __future__ import annotations
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import List, Optional, Dict, Any
from datetime import datetime, timezone


class Timeframe(str, Enum):
    W1 = "W1"
    D1 = "D1"
    H4 = "H4"
    H1 = "H1"
    M15 = "M15"
    M5 = "M5"
    M1 = "M1"

    @property
    def minutes(self) -> int:
        mapping = {
            Timeframe.W1: 10080,
            Timeframe.D1: 1440,
            Timeframe.H4: 240,
            Timeframe.H1: 60,
            Timeframe.M15: 15,
            Timeframe.M5: 5,
            Timeframe.M1: 1,
        }
        return mapping[self]


class OrderType(str, Enum):
    BUY = "BUY"
    SELL = "SELL"
    BUY_LIMIT = "BUY_LIMIT"
    SELL_LIMIT = "SELL_LIMIT"
    BUY_STOP = "BUY_STOP"
    SELL_STOP = "SELL_STOP"


class TrendBias(str, Enum):
    STRONG_BULLISH = "STRONG_BULLISH"
    WEAK_BULLISH = "WEAK_BULLISH"
    RANGING = "RANGING"
    WEAK_BEARISH = "WEAK_BEARISH"
    STRONG_BEARISH = "STRONG_BEARISH"
    TRANSITIONAL = "TRANSITIONAL"


class MarketStructureType(str, Enum):
    BOS = "BOS"          # Break of Structure (trend continuation)
    CHOCH = "CHOCH"      # Change of Character (early trend reversal)
    MSS = "MSS"          # Market Structure Shift (displacement + confirmation)
    SWING_HIGH = "SWING_HIGH"
    SWING_LOW = "SWING_LOW"


class SessionType(str, Enum):
    ASIAN = "ASIAN"
    LONDON = "LONDON"
    NEW_YORK = "NEW_YORK"
    LONDON_NY_OVERLAP = "LONDON_NY_OVERLAP"
    NEW_YORK_PM = "NEW_YORK_PM"
    OFF_HOURS = "OFF_HOURS"


class VolatilityRegime(str, Enum):
    LOW = "LOW"
    NORMAL = "NORMAL"
    HIGH = "HIGH"
    EXTREME = "EXTREME"


class ExecutionMode(str, Enum):
    BACKTEST = "BACKTEST"
    SIMULATION = "SIMULATION"
    DEMO = "DEMO"
    REAL = "REAL"
    PROP = "PROP"


class AccountProfile(str, Enum):
    DEMO = "DEMO"
    LIVE = "LIVE"
    PROP = "PROP"


class AccountTier(str, Enum):
    BELOW_FLOOR = "BELOW_FLOOR"
    MICRO = "MICRO"
    MINI = "MINI"
    SMALL = "SMALL"
    STANDARD = "STANDARD"
    LARGE = "LARGE"


class AffordabilityStatus(str, Enum):
    TRADABLE = "TRADABLE"
    MARGINAL = "MARGINAL"
    BLOCKED = "BLOCKED"


class DrawdownType(str, Enum):
    STATIC = "STATIC"
    TRAILING = "TRAILING"
    DAILY_START_EQUITY = "DAILY_START_EQUITY"
    DAILY_START_BALANCE = "DAILY_START_BALANCE"


class SetupType(str, Enum):
    WEEKLY_REVERSAL = "WEEKLY_REVERSAL"
    WEEKLY_CONTINUATION = "WEEKLY_CONTINUATION"
    DAILY_REVERSAL = "DAILY_REVERSAL"
    DAILY_CONTINUATION = "DAILY_CONTINUATION"
    M15_SMC_OTE = "M15_SMC_OTE"
    SESSION_SWEEP = "SESSION_SWEEP"


@dataclass(frozen=True)
class Candle:
    time: datetime
    open: float
    high: float
    low: float
    close: float
    tick_volume: int = 0
    spread: int = 0
    complete: bool = True

    @property
    def range(self) -> float:
        return self.high - self.low

    @property
    def body(self) -> float:
        return abs(self.close - self.open)

    @property
    def is_bullish(self) -> bool:
        return self.close > self.open

    @property
    def is_bearish(self) -> bool:
        return self.close < self.open

    @property
    def upper_wick(self) -> float:
        return self.high - max(self.open, self.close)

    @property
    def lower_wick(self) -> float:
        return min(self.open, self.close) - self.low

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d['time'] = self.time.isoformat()
        return d


@dataclass
class SymbolInfo:
    symbol: str
    digits: int = 5
    point: float = 0.00001
    tick_size: float = 0.00001
    tick_value: float = 1.0
    contract_size: float = 100000.0
    min_lot: float = 0.01
    max_lot: float = 100.0
    lot_step: float = 0.01
    margin_required: float = 1000.0
    spread: float = 1.2
    ask: float = 0.0
    bid: float = 0.0

    def round_price(self, price: float) -> float:
        return round(price, self.digits)

    def normalize_volume(self, volume: float) -> float:
        step = self.lot_step
        rounded = round(volume / step) * step
        # Always clamp within min and max
        if rounded < self.min_lot:
            return 0.0
        return min(round(rounded, 2), self.max_lot)


@dataclass
class AccountInfo:
    account_number: int = 0
    balance: float = 10000.0
    equity: float = 10000.0
    margin: float = 0.0
    free_margin: float = 10000.0
    margin_level: float = 0.0
    currency: str = "USD"
    leverage: int = 100
    trade_mode: str = "DEMO"  # DEMO, REAL, CONTEST
    profile: str = "DEMO"     # DEMO, LIVE, PROP
    trading_enabled: bool = False
    peak_equity: float = 0.0
    initial_balance: float = 0.0
    daily_loss_pct: float = 0.0
    drawdown_pct: float = 0.0
    remaining_daily_loss: float = 500.0
    remaining_drawdown: float = 1000.0
    profit_target_pct: float = 10.0
    trading_days: int = 0
    # Tier and Cent Account support
    tier: str = "STANDARD"
    is_cent_account: bool = False
    raw_currency: str = "USD"
    raw_balance: float = 10000.0
    raw_equity: float = 10000.0
    margin_mode: int = 0

    def __post_init__(self):
        # Auto-detect CENT accounts: USC, EUC, GBX, etc.
        cent_currencies = {"USC", "EUC", "GBX", "JPC"}
        if self.currency.upper() in cent_currencies or self.raw_currency.upper() in cent_currencies:
            self.is_cent_account = True
            if self.raw_balance == self.balance and self.balance > 0:
                self.balance = self.raw_balance / 100.0
            if self.raw_equity == self.equity and self.equity > 0:
                self.equity = self.raw_equity / 100.0
            if self.free_margin > 0 and self.margin == 0:
                self.free_margin = self.free_margin / 100.0

        if self.profile == "DEMO" and not self.trading_enabled:
            self.trading_enabled = True
        if self.initial_balance <= 0.0:
            self.initial_balance = self.balance
        if self.peak_equity <= 0.0:
            self.peak_equity = max(self.balance, self.equity)


@dataclass
class WeeklyDailyLevels:
    symbol: str
    # Weekly levels
    pwh: float
    pwl: float
    pwo: float
    pwc: float
    pwm: float
    cwo: float
    cwh: float
    cwl: float
    # Daily levels
    pdh: float
    pdl: float
    pdo: float
    pdc: float
    pdm: float
    cdo: float
    cdh: float
    cdl: float
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "symbol": self.symbol,
            "PWH": self.pwh,
            "PWL": self.pwl,
            "PWO": self.pwo,
            "PWC": self.pwc,
            "PWM": self.pwm,
            "CWO": self.cwo,
            "CWH": self.cwh,
            "CWL": self.cwl,
            "PDH": self.pdh,
            "PDL": self.pdl,
            "PDO": self.pdo,
            "PDC": self.pdc,
            "PDM": self.pdm,
            "CDO": self.cdo,
            "CDH": self.cdh,
            "CDL": self.cdl,
            "timestamp": self.timestamp.isoformat()
        }


@dataclass
class LiquidityZone:
    id: str
    symbol: str
    timeframe: Timeframe
    level_type: str  # BSL (Buy-Side Liquidity), SSL (Sell-Side Liquidity), EQH, EQL
    price: float
    high: float
    low: float
    formation_time: datetime
    swept: bool = False
    sweep_time: Optional[datetime] = None
    rejection_confirmed: bool = False


@dataclass
class FairValueGap:
    id: str
    symbol: str
    timeframe: Timeframe
    direction: OrderType  # BUY for bullish FVG, SELL for bearish FVG
    top: float
    bottom: float
    midpoint: float
    formation_time: datetime
    mitigated: bool = False
    mitigation_time: Optional[datetime] = None
    is_inverse: bool = False

    @property
    def size(self) -> float:
        return abs(self.top - self.bottom)


@dataclass
class OrderBlock:
    id: str
    symbol: str
    timeframe: Timeframe
    direction: OrderType  # BUY = Bullish OB (down-candle before up displacement), SELL = Bearish OB
    high: float
    low: float
    open_price: float
    close_price: float
    formation_time: datetime
    mitigated: bool = False
    is_breaker: bool = False
    strength_score: float = 1.0


@dataclass
class MarketStructureEvent:
    id: str
    symbol: str
    timeframe: Timeframe
    event_type: MarketStructureType
    direction: OrderType  # BUY for bullish break/shift, SELL for bearish
    price: float
    timestamp: datetime
    swing_high: Optional[float] = None
    swing_low: Optional[float] = None
    confirmed_by_close: bool = True
    displacement_ratio: float = 1.0


@dataclass
class SignalCandidate:
    id: str
    symbol: str
    timeframe: Timeframe
    direction: OrderType
    setup_type: SetupType
    entry_price: float
    stop_loss: float
    take_profit: float
    risk_reward: float
    score: float
    timestamp: datetime
    status: str = "PENDING"  # PENDING, APPROVED, REJECTED, EXPIRED, EXECUTED
    reasons: List[str] = field(default_factory=list)
    rejection_reasons: List[str] = field(default_factory=list)
    fvg: Optional[FairValueGap] = None
    ob: Optional[OrderBlock] = None
    related_level: Optional[str] = None  # e.g. "PDL_SWEEP", "PWH_BREAKOUT"


@dataclass
class TradeOrder:
    symbol: str
    order_type: OrderType
    volume: float
    entry_price: float
    stop_loss: float
    take_profit: float
    magic: int = 100101
    comment: str = "MT5_SMC_BOT"
    ticket: Optional[int] = None
    status: str = "CREATED"  # CREATED, SENT, FILLED, REJECTED, CANCELLED
    error: Optional[str] = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    executed_at: Optional[datetime] = None


@dataclass
class Position:
    ticket: int
    symbol: str
    order_type: OrderType
    volume: float
    open_price: float
    current_price: float
    stop_loss: float
    take_profit: float
    profit: float
    swap: float = 0.0
    commission: float = 0.0
    magic: int = 100101
    comment: str = ""
    open_time: Optional[datetime] = None
    initial_risk_amount: float = 0.0
    initial_sl_distance: float = 0.0
    break_even_activated: bool = False
    partial_taken: bool = False
