"""
System Configuration Engine.
Loads, validates, and manages configuration files and environment overrides.
Supports both Institutional SMC and Continuous Adaptive Scalping modes.
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
    news_filter_enabled: bool = False
    news_block_minutes_before: int = 0
    news_block_minutes_after: int = 0
    forbid_tick_scalping: bool = False


@dataclass
class AccountProfileConfig:
    profile_name: AccountProfile = AccountProfile.DEMO
    drawdown_type: DrawdownType = DrawdownType.TRAILING
    risk_percent_per_trade: float = 1.0
    max_total_open_risk_percent: float = 2.0
    max_open_positions: int = 3
    max_daily_loss_percent: float = 5.0
    max_total_drawdown_percent: float = 10.0
    safety_buffer_percent: float = 80.0
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
                news_filter_enabled=False,
                forbid_tick_scalping=False
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
                news_filter_enabled=False,
                news_block_minutes_before=0,
                news_block_minutes_after=0,
                forbid_tick_scalping=False
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
                news_filter_enabled=False,
                news_block_minutes_before=0,
                news_block_minutes_after=0,
                forbid_tick_scalping=False
            )
        )
    }


@dataclass
class RiskConfig:
    risk_profile: str = "ADAPTIVE_SCALPING"
    risk_percentage: float = 0.5
    sizing_reference: str = "EQUITY"
    max_daily_loss_enabled: bool = True
    max_daily_loss_percent: float = 2.0
    max_weekly_loss_percent: float = 4.0
    max_drawdown_protection_enabled: bool = True
    max_aggregate_open_risk_percent: float = 1.5
    max_simultaneous_positions: int = 3
    max_trades_per_session: int = 2
    max_consecutive_losses: int = 3
    min_risk_reward_ratio: float = 1.5
    scalping_min_risk_reward_ratio: float = 1.2
    max_spread_pct_of_sl_scalping: float = 20.0
    max_spread_pct_of_sl: float = 5.0
    max_spread_points_absolute: float = 2000.0
    # 0 => fall back to the account profile's max_open_positions for that symbol.
    max_positions_per_symbol: int = 0
    # Temporary per-symbol pause after N consecutive losses on that symbol.
    consecutive_loss_cooldown_minutes: int = 15
    consecutive_loss_trigger: int = 2
    # Risk multiplier applied while a consecutive-loss cooldown is active.
    consecutive_loss_risk_reduction: float = 0.5
    # Correlation guard: max simultaneous positions sharing a correlation group.
    max_correlated_positions: int = 2
    enable_break_even: bool = True
    break_even_at_r: float = 1.0
    scalp_break_even_at_r: float = 0.5
    break_even_buffer_pips: float = 1.0
    enable_partial_tp: bool = True
    partial_tp_at_r: float = 1.5
    scalp_partial_tp_at_r: float = 1.0
    partial_tp_ratio: float = 0.5
    trailing_stop_enabled: bool = True
    trailing_stop_atr_multiplier: float = 1.5
    dynamic_take_profit: bool = True
    enable_time_stop: bool = False
    time_stop_max_bars: int = 32
    enable_session_end_flatten: bool = False
    enable_partial_at_1r: bool = False
    enable_swing_trailing: bool = False
    enable_profit_management_5usd: bool = True
    min_net_profit_target_usd: float = 5.0
    profit_floor_lock_usd: float = 5.0
    strong_momentum_profit_extension: bool = True
    profit_extension_lock_ratio: float = 0.65
    enable_structural_reversal_exit: bool = True


@dataclass
class SessionConfig:
    broker_utc_offset_hours: int = 2
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
    min_confluence_score: float = 75.0
    scalping_min_confluence_score: float = 55.0
    scalping_min_rr: float = 1.5
    allow_countertrend: bool = False
    countertrend_risk_multiplier: float = 0.5
    require_mss_close_confirmation: bool = True
    fvg_min_pips: float = 2.0
    ote_fib_entry_min: float = 0.62
    ote_fib_entry_max: float = 0.79
    ote_fib_invalidation: float = 1.00
    scalp_require_htf_agreement: bool = True
    continuous_scalp_require_htf_agreement: bool = False
    adaptive_entry_filters: bool = True
    swing_lookback_bars: int = 5
    require_close_break: bool = True
    require_lower_high: bool = True
    choch_timeout_bars: int = 20
    allow_choch_only_entries: bool = True
    failed_choch_action: str = "RESET_AND_LOG_TRAP"
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
    trading_mode: str = "CONTINUOUS_SCALPING"
    execution_timeframes: List[str] = field(default_factory=lambda: ["M1", "M5"])
    context_timeframes: List[str] = field(default_factory=lambda: ["M15", "H1"])
    news_filter_enabled: bool = False
    continuous_scanning: bool = True
    adaptive_entry_filters: bool = True
    dynamic_take_profit: bool = True
    break_even_enabled: bool = True
    trailing_stop_enabled: bool = True
    partial_profit_enabled: bool = True
    auto_rescan_after_closure: bool = True
    multi_symbol_execution: bool = True
    risk_profile: str = "ADAPTIVE_SCALPING"
    max_daily_loss_enabled: bool = True
    max_drawdown_protection_enabled: bool = True

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

    # Quant Best-Practice Upgrade Flags (Strangler Pattern, default False)
    upgrade_enabled: bool = False
    broker_adapter_enabled: bool = False
    feature_pipeline_enabled: bool = False
    modular_strategy_enabled: bool = False
    scbf_enabled: bool = False
    chart_visualization_enabled: bool = False
    ab_harness_enabled: bool = False
    meta_labeling_enabled: bool = False
    bridge_extensions_enabled: bool = False
    validation_gates_enabled: bool = False

    def get_magic_for_symbol(self, symbol: str) -> int:
        if symbol in self.symbol_magic_offsets:
            return self.magic_number + self.symbol_magic_offsets[symbol]
        base = symbol[:-2] if symbol.endswith(".m") else f"{symbol}.m"
        if base in self.symbol_magic_offsets:
            return self.magic_number + self.symbol_magic_offsets[base]
        offset = (abs(hash(symbol)) % 900) + 10
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
                with open(settings_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if "execution_mode" in data:
                        cfg.execution_mode = ExecutionMode(data["execution_mode"])
                    if "TRADING_MODE" in data:
                        cfg.trading_mode = str(data["TRADING_MODE"])
                    if "EXECUTION_TIMEFRAMES" in data and isinstance(data["EXECUTION_TIMEFRAMES"], list):
                        cfg.execution_timeframes = [str(x) for x in data["EXECUTION_TIMEFRAMES"]]
                    if "CONTEXT_TIMEFRAMES" in data and isinstance(data["CONTEXT_TIMEFRAMES"], list):
                        cfg.context_timeframes = [str(x) for x in data["CONTEXT_TIMEFRAMES"]]
                    cfg.news_filter_enabled = False  # Permanently disabled
                    for upper_k, attr_k in [
                        ("CONTINUOUS_SCANNING", "continuous_scanning"),
                        ("ADAPTIVE_ENTRY_FILTERS", "adaptive_entry_filters"),
                        ("DYNAMIC_TAKE_PROFIT", "dynamic_take_profit"),
                        ("BREAK_EVEN_ENABLED", "break_even_enabled"),
                        ("TRAILING_STOP_ENABLED", "trailing_stop_enabled"),
                        ("PARTIAL_PROFIT_ENABLED", "partial_profit_enabled"),
                        ("AUTO_RESCAN_AFTER_CLOSURE", "auto_rescan_after_closure"),
                        ("MULTI_SYMBOL_EXECUTION", "multi_symbol_execution"),
                        ("MAX_DAILY_LOSS_ENABLED", "max_daily_loss_enabled"),
                        ("MAX_DRAWDOWN_PROTECTION_ENABLED", "max_drawdown_protection_enabled"),
                    ]:
                        if upper_k in data:
                            setattr(cfg, attr_k, bool(data[upper_k]))
                    if "RISK_PROFILE" in data:
                        cfg.risk_profile = str(data["RISK_PROFILE"])
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
                    if "upgrade" in data and isinstance(data["upgrade"], dict):
                        ud = data["upgrade"]
                        if "enabled" in ud:
                            cfg.upgrade_enabled = bool(ud["enabled"])
                        for k in ["broker_adapter_enabled", "feature_pipeline_enabled", "modular_strategy_enabled", "scbf_enabled", "chart_visualization_enabled", "ab_harness_enabled", "meta_labeling_enabled", "bridge_extensions_enabled", "validation_gates_enabled"]:
                            if k in ud and hasattr(cfg, k):
                                setattr(cfg, k, bool(ud[k]))
            except Exception as e:
                print(f"[Config] Error loading settings.json: {e}")

        strat_file = config_path / "strategy_config.json"
        if strat_file.exists():
            try:
                with open(strat_file, "r", encoding="utf-8") as f:
                    sdata = json.load(f)
                    if "scalp_require_htf_agreement" in sdata:
                        cfg.strategy.scalp_require_htf_agreement = bool(sdata["scalp_require_htf_agreement"])
                    elif "ict_smc" in sdata and "scalp_require_htf_agreement" in sdata["ict_smc"]:
                        cfg.strategy.scalp_require_htf_agreement = bool(sdata["ict_smc"]["scalp_require_htf_agreement"])
                    for k in ["continuous_scalp_require_htf_agreement", "scalping_min_confluence_score", "scalping_min_rr", "adaptive_entry_filters"]:
                        if k in sdata and hasattr(cfg.strategy, k):
                            setattr(cfg.strategy, k, sdata[k])
                    ict_data = sdata.get("ict_smc", sdata)
                    for k in ["require_close_break", "require_lower_high", "choch_timeout_bars", "allow_choch_only_entries", "failed_choch_action", "swing_lookback_bars"]:
                        if k in ict_data and hasattr(cfg.strategy, k):
                            setattr(cfg.strategy, k, ict_data[k])
            except Exception as e:
                print(f"[Config] Error loading strategy_config.json: {e}")

        risk_file = config_path / "risk_config.json"
        if risk_file.exists():
            try:
                with open(risk_file, "r", encoding="utf-8") as f:
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
