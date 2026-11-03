"""
System Configuration Engine.
Loads, validates, and manages configuration files and environment overrides.
"""

from __future__ import annotations
import json
import os
from pathlib import Path
from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional
from src.core.contracts import ExecutionMode, AccountProfile, DrawdownType


@dataclass
class PropFirmRulesConfig:
    no_martingale: bool = True
    no_grid_averaging: bool = True
    no_hedging: bool = True
    require_sl: bool = True
    min_trade_duration_seconds: int = 60
    max_lot_cap: float = 0.5
    no_weekend_holding: bool = True
    weekend_close_hour_friday: int = 22
    news_filter_enabled: bool = True
    news_block_minutes_before: int = 15
    news_block_minutes_after: int = 15
    forbid_tick_scalping: bool = True


@dataclass
class AccountProfileConfig:
    profile_name: AccountProfile = AccountProfile.DEMO
    drawdown_type: DrawdownType = DrawdownType.TRAILING
    risk_percent_per_trade: float = 1.0
    max_total_open_risk_percent: float = 2.0
    max_open_positions: int = 3
    max_daily_loss_percent: float = 5.0
    max_total_drawdown_percent: float = 10.0
    safety_buffer_percent: float = 80.0  # Stop opening trades at 80% of limit
    profit_target_percent: float = 10.0
    min_trading_days: int = 4
    daily_reset_hour: int = 0
    daily_reset_tz_offset: int = 0
    loss_streak_pause: int = 3
    max_trades_per_day: int = 10
    max_spread_pct_of_sl: float = 5.0
    rules: PropFirmRulesConfig = field(default_factory=PropFirmRulesConfig)


def get_default_account_profiles() -> Dict[str, AccountProfileConfig]:
    return {
        "DEMO": AccountProfileConfig(
            profile_name=AccountProfile.DEMO,
            drawdown_type=DrawdownType.STATIC,
            risk_percent_per_trade=1.0,
            max_total_open_risk_percent=3.0,
            max_open_positions=3,
            max_daily_loss_percent=5.0,
            max_total_drawdown_percent=10.0,
            safety_buffer_percent=90.0,
            profit_target_percent=10.0,
            min_trading_days=4,
            max_trades_per_day=10,
            rules=PropFirmRulesConfig(
                no_martingale=True,
                no_grid_averaging=True,
                no_hedging=False,
                require_sl=True,
                min_trade_duration_seconds=0,
                max_lot_cap=1.0,
                no_weekend_holding=False,
                news_filter_enabled=False
            )
        ),
        "LIVE": AccountProfileConfig(
            profile_name=AccountProfile.LIVE,
            drawdown_type=DrawdownType.TRAILING,
            risk_percent_per_trade=1.0,
            max_total_open_risk_percent=2.0,
            max_open_positions=3,
            max_daily_loss_percent=3.0,
            max_total_drawdown_percent=6.0,
            safety_buffer_percent=85.0,
            profit_target_percent=0.0,
            min_trading_days=0,
            max_trades_per_day=8,
            rules=PropFirmRulesConfig(
                no_martingale=True,
                no_grid_averaging=True,
                no_hedging=True,
                require_sl=True,
                min_trade_duration_seconds=30,
                max_lot_cap=0.5,
                no_weekend_holding=True,
                weekend_close_hour_friday=22,
                news_filter_enabled=True,
                news_block_minutes_before=15,
                news_block_minutes_after=15
            )
        ),
        "PROP": AccountProfileConfig(
            profile_name=AccountProfile.PROP,
            drawdown_type=DrawdownType.TRAILING,
            risk_percent_per_trade=0.5,
            max_total_open_risk_percent=1.5,
            max_open_positions=2,
            max_daily_loss_percent=4.0,
            max_total_drawdown_percent=8.0,
            safety_buffer_percent=80.0,
            profit_target_percent=10.0,
            min_trading_days=4,
            max_trades_per_day=6,
            rules=PropFirmRulesConfig(
                no_martingale=True,
                no_grid_averaging=True,
                no_hedging=True,
                require_sl=True,
                min_trade_duration_seconds=60,
                max_lot_cap=0.5,
                no_weekend_holding=True,
                weekend_close_hour_friday=21,
                news_filter_enabled=True,
                news_block_minutes_before=15,
                news_block_minutes_after=15,
                forbid_tick_scalping=True
            )
        )
    }


@dataclass
class RiskConfig:
    risk_percentage: float = 0.5  # 0.5% risk per trade default
    sizing_reference: str = "EQUITY"  # "EQUITY", "BALANCE", or "CONSERVATIVE"
    max_daily_loss_percent: float = 2.0  # 2% max daily loss
    max_weekly_loss_percent: float = 4.0  # 4% max weekly loss
    max_aggregate_open_risk_percent: float = 1.5  # 1.5% total open risk
    max_simultaneous_positions: int = 3
    max_trades_per_session: int = 2
    max_consecutive_losses: int = 3
    min_risk_reward_ratio: float = 2.0  # 1:2 default minimum R:R
    enable_break_even: bool = True
    break_even_at_r: float = 1.0  # Move SL to BE at +1.0R
    break_even_buffer_pips: float = 1.0
    enable_partial_tp: bool = True
    partial_tp_at_r: float = 1.5  # Take 50% partial at +1.5R
    partial_tp_ratio: float = 0.5
    trailing_stop_enabled: bool = True
    trailing_stop_atr_multiplier: float = 1.5


@dataclass
class SessionConfig:
    broker_utc_offset_hours: int = 2  # Default MT5 broker server time (e.g. UTC+2 / UTC+3 in summer)
    asian_start: str = "00:00"
    asian_end: str = "08:00"
    london_start: str = "08:00"
    london_end: str = "16:00"
    london_kill_zone_start: str = "07:00"
    london_kill_zone_end: str = "10:30"
    ny_start: str = "13:00"
    ny_end: str = "21:00"
    ny_kill_zone_start: str = "13:00"
    ny_kill_zone_end: str = "16:00"
    overlap_start: str = "13:00"
    overlap_end: str = "16:00"
    filter_spread_max_pips: float = 3.0


@dataclass
class StrategyConfig:
    min_confluence_score: float = 75.0  # Out of 100
    allow_countertrend: bool = False
    countertrend_risk_multiplier: float = 0.5  # 50% risk if countertrend
    require_mss_close_confirmation: bool = True
    fvg_min_pips: float = 2.0
    ote_fib_entry_min: float = 0.62
    ote_fib_entry_max: float = 0.79
    ote_fib_invalidation: float = 1.00
    scalp_require_htf_agreement: bool = True  # SCALP requires M15, H1, H4 agreement
    active_scalp_mode: bool = True            # Active Trading Mode (Part 6)
    max_trades_per_symbol_per_hour: int = 3
    max_trades_per_day: int = 10
    max_concurrent_positions_per_symbol: int = 1
    max_total_concurrent_positions: int = 3
    min_entry_distance_atr_mult: float = 1.0
    loss_cooldown_seconds: float = 900.0
    safe_margin_level_min_percent: float = 300.0


@dataclass
class AppConfig:
    execution_mode: ExecutionMode = ExecutionMode.SIMULATION
    symbols: List[str] = field(default_factory=lambda: ["EURUSD", "GBPUSD", "USDJPY", "XAUUSD"])
    primary_signal_timeframe: str = "M15"
    htf_timeframes: List[str] = field(default_factory=lambda: ["W1", "D1", "H4", "H1"])
    bridge_host: str = "127.0.0.1"
    bridge_port: int = 5555
    bridge_sub_port: int = 5556
    magic_number: int = 100101
    symbol_magic_offsets: Dict[str, int] = field(default_factory=lambda: {"BTCUSD.m": 1, "EURUSD": 2, "GBPUSD": 3, "USDJPY": 4, "XAUUSD": 5, "GBPJPY.m": 6})
    dry_run: bool = False
    log_level: str = "INFO"
    telegram_bot_token: Optional[str] = None
    telegram_chat_id: Optional[str] = None
    telegram_enabled: bool = True
    telegram_proxy: Optional[str] = None
    account_profile: AccountProfile = AccountProfile.DEMO
    trading_enabled: bool = False
    allowed_account: int = 0
    account_profiles: Dict[str, AccountProfileConfig] = field(default_factory=get_default_account_profiles)
    risk: RiskConfig = field(default_factory=RiskConfig)
    session: SessionConfig = field(default_factory=SessionConfig)
    strategy: StrategyConfig = field(default_factory=StrategyConfig)

    def get_magic_for_symbol(self, symbol: str) -> int:
        offset = self.symbol_magic_offsets.get(symbol, (abs(hash(symbol)) % 900) + 10)
        return self.magic_number + offset

    @classmethod
    def load_from_dir(cls, config_dir: str = "config") -> AppConfig:
        from src.notifications.telegram_config import load_env_file
        load_env_file()

        config_path = Path(config_dir)
        settings_file = config_path / "settings.json"
        
        cfg = cls()
        if settings_file.exists():
            try:
                with open(settings_file, "r") as f:
                    data = json.load(f)
                    if "execution_mode" in data:
                        cfg.execution_mode = ExecutionMode(data["execution_mode"])
                    if "symbols" in data:
                        cfg.symbols = data["symbols"]
                    if "bridge_host" in data:
                        cfg.bridge_host = data["bridge_host"]
                    if "bridge_port" in data:
                        cfg.bridge_port = int(data["bridge_port"])
                    if "magic_number" in data:
                        cfg.magic_number = int(data["magic_number"])
                    if "symbol_magic_offsets" in data:
                        cfg.symbol_magic_offsets = data["symbol_magic_offsets"]
                    if "dry_run" in data:
                        cfg.dry_run = bool(data["dry_run"])
            except Exception as e:
                print(f"[Config] Error loading settings.json: {e}")

        strat_file = config_path / "strategy_config.json"
        if strat_file.exists():
            try:
                with open(strat_file, "r") as f:
                    sdata = json.load(f)
                    if "scalp_require_htf_agreement" in sdata:
                        cfg.strategy.scalp_require_htf_agreement = bool(sdata["scalp_require_htf_agreement"])
                    elif "ict_smc" in sdata and "scalp_require_htf_agreement" in sdata["ict_smc"]:
                        cfg.strategy.scalp_require_htf_agreement = bool(sdata["ict_smc"]["scalp_require_htf_agreement"])
            except Exception as e:
                print(f"[Config] Error loading strategy_config.json: {e}")

        risk_file = config_path / "risk_config.json"
        if risk_file.exists():
            try:
                with open(risk_file, "r") as f:
                    rdata = json.load(f)
                    for k, v in rdata.items():
                        if hasattr(cfg.risk, k):
                            setattr(cfg.risk, k, v)
            except Exception as e:
                print(f"[Config] Error loading risk_config.json: {e}")

        # Environment variable overrides
        if os.getenv("EXECUTION_MODE"):
            cfg.execution_mode = ExecutionMode(os.getenv("EXECUTION_MODE"))
        if os.getenv("TELEGRAM_BOT_TOKEN"):
            cfg.telegram_bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
        if os.getenv("TELEGRAM_CHAT_ID"):
            cfg.telegram_chat_id = os.getenv("TELEGRAM_CHAT_ID")
        if os.getenv("TELEGRAM_ENABLED"):
            cfg.telegram_enabled = os.getenv("TELEGRAM_ENABLED", "true").lower() in ("true", "1", "yes")
        if os.getenv("TELEGRAM_PROXY"):
            cfg.telegram_proxy = os.getenv("TELEGRAM_PROXY")
        if os.getenv("BRIDGE_HOST"):
            cfg.bridge_host = os.getenv("BRIDGE_HOST")
        if os.getenv("BRIDGE_PORT"):
            cfg.bridge_port = int(os.getenv("BRIDGE_PORT"))

        return cfg
