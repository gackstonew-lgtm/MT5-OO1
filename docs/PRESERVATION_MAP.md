# PRESERVATION MAP & ARCHITECTURAL INVENTORY
**Project**: MT5 Institutional Algorithmic Day-Trading Engine Upgrade  
**Baseline Git Tag**: `baseline-pre-upgrade` (Commit `4b7127e4d5c918f1daf902c243f9882e842ebaed`)  
**Target Branch**: `feature/best-practice-upgrade`  
**Governing Standard**: Preservation Contract (Additive Architecture / Strangler Pattern)

---

## 1. Executive Summary & Preservation Guarantee

This document defines the strict inventory, preservation boundaries, and hook architecture for advancing the institutional trading engine. The core principle is **zero degradation and zero disruption**:
1. **The existing bot is the product**: No existing file, class, method, function signature, Telegram command, MQL5 input, configuration key, or database schema will be renamed, deleted, moved, or altered in behavior.
2. **Strangler Pattern**: All new capabilities (Broker Adapter, Feature Pipeline, Strategy Plugins, SCBF Engine, Chart Visuals, Shadow Harness, Meta-labeling ONNX, and Validation Gates) reside exclusively in **NEW modules and files**.
3. **Master Switch & Feature Flags**: A master configuration switch (`upgrade.enabled`, default `false`) plus per-feature flags (all default `false`, including `strategies.scbf.enabled`) guarantee that when flags are OFF, the system produces **100% bit-for-bit identical decisions and orders** to the baseline.
4. **Hard Limits Primacy**: Tier risk ceilings, drawdown stops, lot caps, min R:R floors, spread filters, profile guards, and the risk-fit engine stop window always win. New code can only be more conservative.

---

## 2. Inventory of Existing Codebase vs Upgrade Stages

| Existing File / Component | Architectural Role | Touched? | Action Category | Planned Hook & Stage | Risk Level |
| :--- | :--- | :--- | :--- | :--- | :--- |
| `src/core/contracts.py` | Data contracts, enums, dataclasses | NO | ALREADY EXISTS | Additive imports in new modules | ZERO |
| `src/core/config.py` | Central application configuration | EXTEND | EXTEND | Hook 1 (Stage 0/1): Add `upgrade` config schema (default `False`) | LOW |
| `src/core/market_data.py` | Candle cache and market data store | NO | ALREADY EXISTS | Wrapped by Broker Adapter (Stage 1) | ZERO |
| `src/core/news_filter.py` | High-impact economic news filter | NO | ALREADY EXISTS | Consumed by Feature Pipeline (Stage 2) | ZERO |
| `src/core/sessions.py` | ICT/SMC session ranges & overlap | NO | ALREADY EXISTS | Consumed by Feature Pipeline (Stage 2) | ZERO |
| `src/core/volatility.py` | ATR & regime volatility computation | NO | ALREADY EXISTS | Consumed by Feature Pipeline (Stage 2) | ZERO |
| `src/core/orchestrator.py` | Master cycle orchestration | EXTEND | EXTEND | Hook 2 (Stage 1/3/6): Conditional delegation when `upgrade.enabled=True` | LOW |
| `src/data/cost_model.py` | Spread, commission, and slippage | NO | ALREADY EXISTS | Extended in SimBroker (Stage 1) | ZERO |
| `src/data/historical_loader.py` | JSON candle loading | NO | ALREADY EXISTS | Extended in parquet cache (Stage 2) | ZERO |
| `src/execution/bridge_client.py` | TCP socket bridge IPC client | NO | ALREADY EXISTS | Wrapped by LiveMT5Broker (Stage 1) | ZERO |
| `src/execution/bridge_server.py` | Multi-connection bridge daemon | EXTEND | EXTEND | Hook 3 (Stage 8): Extended handshake & order schemas | LOW |
| `src/execution/order_manager.py` | Order execution & position guards | NO | ALREADY EXISTS | Kept intact as champion executor | ZERO |
| `src/execution/active_mode.py` | Active scalp frequency & prohibitions | NO | ALREADY EXISTS | Kept intact | ZERO |
| `src/risk/fit_engine.py` | Risk-fit feasibility window | NO | ALREADY EXISTS | Hard constraints imported by SCBF | ZERO |
| `src/risk/position_sizing.py` | Sizing calculation & risk caps | NO | ALREADY EXISTS | Plugged into SizingModule adapter (Stage 3) | ZERO |
| `src/risk/account_tier.py` | Tier risk ceilings & floors | NO | ALREADY EXISTS | Universal constraint layer | ZERO |
| `src/risk/portfolio_risk.py` | Drawdown and daily loss guards | NO | ALREADY EXISTS | Universal constraint layer | ZERO |
| `src/risk/trade_management.py` | Break-even and trailing stops | NO | ALREADY EXISTS | Plugged into ExitModule adapter (Stage 3) | ZERO |
| `src/risk/state_manager.py` | Atomic JSON state persistence | NO | ALREADY EXISTS | Kept intact | ZERO |
| `src/smc/market_structure.py` | Swings, BOS, CHoCH detection | NO | ALREADY EXISTS | Preserved intact for baseline strategies | ZERO |
| `src/smc/fvg_ob.py` | Fair value gaps & order blocks | NO | ALREADY EXISTS | Preserved intact for baseline strategies | ZERO |
| `src/smc/liquidity.py` | BSL/SSL pools & sweep detection | NO | ALREADY EXISTS | Preserved intact for baseline strategies | ZERO |
| `src/smc/mtf_analyzer.py` | Multi-timeframe trend alignment | NO | ALREADY EXISTS | Preserved intact for baseline strategies | ZERO |
| `src/smc/strategy_engine.py` | 4 SMC setups (Asia, London, NY) | NO | ALREADY EXISTS | Wrapped into SignalModule adapter (Stage 3) | ZERO |
| `src/notifications/telegram_service.py` | Telegram bot feed & commands | EXTEND | EXTEND | Hook 4 (Stage 5): Additive `/setups` and `/structure` commands | LOW |
| `mql5/Experts/PythonBridgeEA.mq5` | Real MetaTrader 5 Expert Advisor | EXTEND | EXTEND | Hook 5 (Stage 5/7): Chart visualization (`PBE_` objects) & ONNX | LOW |

---

## 3. Detailed Audit of Existing SMC Code vs Stage 4 SCBF Specification

A rigorous technical comparison reveals key operational differences between the legacy SMC implementation and the formal SCBF rule set:

| Architectural Component | Existing SMC Implementation (`src/smc/`) | Stage 4 SCBF Requirement (`src/strategies/scbf/`) | Handling Strategy |
| :--- | :--- | :--- | :--- |
| **Swing Point Detection** | Fixed $k$-bar lookback ($k=5$) without volatility filter (`MarketStructureEngine.find_swing_points`). | Configurable fractal with left/right bars (default 3) and minimum displacement threshold ($\ge 0.5 \times \text{ATR}_{14}$). | **Separate implementation** in `scbf/detector.py`. Existing code left untouched. |
| **Liquidity Pool Identification** | Identifies BSL/SSL and EQH/EQL within fixed pip tolerance (`LiquidityEngine.identify_liquidity_pools`). | Identifies swing clusters within dynamic ATR tolerance ($\le 0.10 \times \text{ATR}$). Prioritizes unswept pools. | **Separate implementation**. |
| **Liquidity Sweep Rule** | Checks `c.high > pool.price` and `c.close < pool.price` using pip threshold. | Wick must exceed level by $\ge \max(\text{spread} \times 2, 0.05 \times \text{ATR})$. A candle that **closes** beyond level is a Bullish Break (cancels the setup). | **Separate implementation**. SCBF strictly enforces close back inside. |
| **Protected High / Low** | **Does not exist.** The existing code only knows the most recent swing point. | **Core prerequisite.** Tracks the specific swing extreme that initiated the leg into the swept high/low. | **New capability** in SCBF state machine. |
| **Change of Character (CHoCH)** | Flags CHoCH whenever the immediate previous swing is broken against trend bias. | Candle close beyond the **protected level** only. Strictly a warning/readiness marker, **never an entry**. | **Separate implementation**. |
| **Break of Structure (BOS)** | Simple close beyond swing high/low. | After CHoCH, price forms a lower high (for SELL), then closes beyond the post-CHoCH extreme with displacement body $\ge 0.8 \times \text{ATR}$. | **Separate implementation**. |
| **Fair Value Gap (FVG)** | General 3-candle imbalance detected anywhere (`FvgObEngine.detect_fvgs`). | FVG formed **specifically by the BOS displacement leg**. Must meet minimum ATR threshold. | **Separate implementation**. |
| **Retrace Entry Rule** | No retrace entry logic; entries trigger on zone interaction confluence. | Price must pull back into the FVG zone, reject it, and close below (for SELL). Modes: `CLOSE_CONFIRM` and `LIMIT_AT_ZONE`. | **Separate implementation** with deterministic state machine. |
| **Temporal Sequence & Expiry** | Independent confluence scoring ($\ge 75$ points). | Strict temporal progression: `Sweep -> CHoCH -> BOS -> FVG -> Entry` with bar timeouts (expiry). | **State machine architecture** (`scbf/state_machine.py`). |

---

## 4. Hook Points Specification (Strangler Pattern)

Every hook into existing code is gated behind `config.upgrade.enabled`:
```python
if self.config.upgrade.enabled and self.config.upgrade.some_feature_enabled:
    return new_adapter.handle(...)
# Existing baseline path executes untouched
return self._baseline_handle(...)
```
Full hook details, line numbers, and diffs are maintained in `docs/HOOKS.md`.

---

## 5. Verification & Rollback Protocol

- **Baseline Freeze**: Git tag `baseline-pre-upgrade` preserves commit `4b7127e4d5c918f1daf902c243f9882e842ebaed`.
- **Golden Master Tests**: 30 recorded days across all enabled symbols (`EURUSD.m`, `XAUUSD.m`, `BTCUSD.m`) verified on every commit.
- **Rollback**: If any stage fails, the stage commit is reverted or reset to `baseline-pre-upgrade`.
