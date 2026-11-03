# HOOK POINTS REGISTER (STRANGLER PATTERN)
**Project**: MT5 Institutional Algorithmic Day-Trading Engine Upgrade  
**Master Switch**: `config.upgrade.enabled` (default `False`)

This document lists every explicit hook point into existing code. In accordance with the Preservation Contract:
- Existing code is touched ONLY at minimal, explicit delegation points.
- Every hook is protected by a boolean gate that defaults to `False`.
- With all flags `False`, execution flows through the 100% original baseline code path.

---

## Registered Hooks

### Hook 1: Master Upgrade Configuration Schema
- **Target File**: `src/core/config.py`
- **Location**: Class `AppConfig`
- **Purpose**: Appends `UpgradeConfig` dataclass defining `enabled` (master switch), `broker_adapter_enabled`, `feature_pipeline_enabled`, `strategy_plugins_enabled`, `scbf_enabled`, `chart_visuals_enabled`, `shadow_harness_enabled`, `meta_labeling_enabled`, `onnx_inference_enabled`.
- **Default Value**: All boolean flags default to `False`.
- **Risk Assessment**: ZERO. Simply defines configuration fields without altering existing configuration keys or parsing logic.

### Hook 2: Orchestrator Broker & Strategy Delegation
- **Target File**: `src/core/orchestrator.py`
- **Location**: Inside `TradingBotOrchestrator.__init__` and `run_cycle_for_symbol`
- **Purpose**: When `self.config.upgrade.enabled` is `True`, optionally delegates broker queries to `BrokerAdapter` and registers `StrategyPluginRegistry`.
- **Fallback / Baseline Path**: When `False`, instantiates and uses existing `MarketDataEngine`, `BridgeClient`, and `SmcStrategyEngine` identically.
- **Risk Assessment**: LOW. Tested via Golden Master comparison suite.

### Hook 3: Multi-Connection Bridge Server Extended Schemas
- **Target File**: `src/execution/bridge_server.py`
- **Location**: Command routing loop (`_handle_ipc_client` / `forward_request_to_ea`)
- **Purpose**: Adds handling for optional extended schemas (versioned request payloads, chart object updates) while passing all existing commands (`PING`, `ACCOUNT`, `POSITIONS`, `RATES`, `TRADE`, `MODIFY`, `CANCEL`, `KILL_SWITCH`, `STATUS`) untouched.
- **Risk Assessment**: ZERO. Existing actions remain untouched in their exact prior blocks.

### Hook 4: Telegram Service Command Additions
- **Target File**: `src/notifications/telegram_service.py`
- **Location**: Command dispatcher loop
- **Purpose**: Adds handling for `/setups` and `/structure` commands without modifying `/status`, `/help`, `/pause`, `/resume`, `/killswitch`, or `/closeall`.
- **Risk Assessment**: ZERO. Non-destructive command routing.

### Hook 5: PythonBridgeEA Chart Visuals & Appended Inputs
- **Target File**: `mql5/Experts/PythonBridgeEA.mq5`
- **Location**: Appended inputs section at the bottom of input declarations; `OnChartEvent()` / `OnTimer()` hook.
- **Purpose**: Renders `PBE_` structure objects and parses extended visualization commands.
- **Defaults**: All new inputs default to `false`. Existing `.set` files and chart parameters maintain exact compatibility.
- **Risk Assessment**: LOW. Verified with MetaEditor64 real compiler (target 0 errors, 0 warnings).

### Hook 6: Pattern Engine Integration Hook
- **Target File**: `src/smc/strategy_engine.py`
- **Location**: `SmcStrategyEngine.scan_for_opportunities()`
- **Purpose**: When `config/patterns_config.json` has `"enabled": true`, scans for pattern confluence and creates `PATTERN_CONFLUENCE` setups.
- **Defaults**: `"enabled": false`. With switch off, returns None and preserves original SMC setups.
- **Risk Assessment**: ZERO. Verified via Golden Master 8,640 replay test.

### Hook 7: Session Layer Pre-Trade Checklist Gates
- **Target File**: `src/execution/order_manager.py`
- **Location**: `OrderManager.run_pre_trade_checklist()`
- **Purpose**: When `config/sessions.json` has `"enabled": true`, evaluates Checklist items #13 (Session active), #14 (Affinity check), #15 (Handover/rollover/cash-open blackout), and #16 (Session trade/loss budgets).
- **Defaults**: `"enabled": false`. When false, items #13–#16 are bypassed and 100% legacy pre-check decisions are preserved.
- **Risk Assessment**: ZERO. Verified via Golden Master 8,640 replay test.

### Hook 8: Session Telegram Commands
- **Target File**: `src/notifications/telegram_service.py`
- **Location**: Command dispatcher loop
- **Purpose**: Adds handling for `/session`, `/sessionstats`, `/sessionmode`, and `/sessionprofile`.
- **Defaults**: Non-destructive command additions.
- **Risk Assessment**: ZERO.

### Hook 9: Fast Mode Pre-Trade Speed & Reliability Guardrails
- **Target File**: `src/execution/order_manager.py`
- **Location**: `OrderManager.run_pre_trade_checklist()`
- **Purpose**: When `config/fast_mode.json` has `"enabled": true` (or candidate is `SetupType.FAST_SCALP`), runs `FastWatchdog.can_open_new_trade()` and `FastRiskGuardEngine.validate_trade_candidate()`.
- **Defaults**: `"enabled": false`. When false, baseline 16-point checklist runs with 100% bit-for-bit parity.
- **Risk Assessment**: ZERO. Verified via Golden Master 8,640 replay test.

### Hook 10: Fast Mode Telegram Remote Controls & EA Filling Auto-Detection
- **Target Files**: `src/notifications/telegram_service.py`, `mql5/Experts/PythonBridgeEA.mq5`
- **Location**: Command dispatcher loop (`/fastmode`, `/flatten`, `/fastquality`, `/margin`) and `ExecuteTradeCommand()` filling mode selection (`FOK`, `IOC`, `RETURN`).
- **Defaults**: Non-destructive additions; `SetTypeFillingBySymbol` preserves broker default filling behavior.
- **Risk Assessment**: ZERO. Verified with MetaEditor64 (0 errors, 0 warnings).
