# Changelog

All notable changes to the Institutional Multi-Timeframe Algorithmic Trading Bot (`MT5-OO1`) are documented in this file.

## [3.3.0] - 2026-11-03

### Added - 6-Phase Advanced Quantitative Execution Subsystems
- **Phase 1: CHoCH-Warning -> BOS-Confirmation State Machine (`src/smc/structure_confirmation.py`)**:
  - Deterministic closed-candle state machine (`IDLE -> CHOCH_WARNING -> PULLBACK -> BOS_CONFIRMED -> FAILED_TRAP`).
  - Gated entries behind BOS confirmation; anchored SL behind confirmed LH/HL.
- **Phase 2: Locked Immutable Trade Plan (`src/core/contracts.py`, `src/execution/order_manager.py`)**:
  - Immutable `TradePlan` dataclass binding entry, SL, TP, lots, risk %, R:R, and structural state.
  - Plan lifecycle shifts (break-even, trailing, partials) logged as explicit immutable `PlanAmendment` records.
- **Phase 3: Monte Carlo "Time Machine" Replay Gate (`src/risk/replay_gate.py`)**:
  - Vectorized block-bootstrap simulation over 10,000 M15 price paths (<2s runtime, deterministic trade seed).
  - Pre-trade gating on $P(\text{target}) > P(\text{stop}) + \text{edge}$ and minimum expected R.
  - Monte Carlo trade-result sequence drawdown estimation in `scripts/run_backtest.py`.
- **Phase 4: Human Confirm Gate & Paper Shadow Journal (`src/monitoring/shadow_journal.py`, `src/notifications/telegram_service.py`)**:
  - Interactive operator confirmation (120s timeout) for LIVE/PROP modes with `/confirm_mode on|off`.
  - DEMO mode auto-executes; unauthorized chats rejected.
  - Forward tracking of taken and rejected setups; `/shadow_report` command.
- **Phase 5: Multi-Symbol Opportunity Scanner & News Blackout (`src/smc/symbol_scanner.py`, `src/smc/news_filter.py`)**:
  - 5-factor scoring model ranking symbols on M15 close: Structure, HTF alignment, Spread/ATR, Session liquidity, News proximity.
  - High-impact macroeconomic calendar blackout window (15m before/after).
- **Phase 6: Exit Machine (`src/risk/trade_management.py`, `config/risk_config.json`)**:
  - Configurable optional exit rules (default OFF): Time Stop (X bars), End-of-Session Flatten, 1.0R Partial + BE, Structure-Based Swing Trailing.
  - All exit actions logged at `[TRADE]` level.
- **Verification & Test Coverage**:
  - 152 automated tests passing (`Ran 152 tests in 78s. OK`).
  - Golden Master 8,640 decisions 100% bit-for-bit identical.
  - Deep verification 12/12 suites passing (100%).
  - MetaEditor: 0 errors, 0 warnings.
  - Secret scan: 0 secrets found.

## [3.2.0] - 2026-11-03

### Added - CHoCH-Warning -> BOS-Confirmation State Machine & Structure Lifecycle
- **Market Structure Confirmation Module (`src/smc/structure_confirmation.py`)**:
  - Deterministic state machine tracking `IDLE -> CHOCH_WARNING -> PULLBACK -> BOS_CONFIRMED -> FAILED_TRAP`.
  - **CHoCH = Warning Only**: Candle close confirmation required (`require_close_break: true`); wick breaks strictly ignored. CHoCH alone cannot trigger trades.
  - **Pullback & Retracement Validation**: Requires confirmed Lower High (bearish) or Higher Low (bullish) relative to pre-CHoCH extreme before BOS eligibility.
  - **BOS Confirmation**: Unlocks trade entry only after candle close breaks the post-CHoCH impulse extreme.
  - **Trap Invalidation**: Closes beyond pre-CHoCH extreme are classified as `FAILED_TRAP`, reset prior trend, and recorded.
  - **Configurable Timeouts**: Warnings expire back to `IDLE` after `choch_timeout_bars` (default 20).
- **Strategy Engine Gating (`src/smc/strategy_engine.py`)**:
  - Gated candidate execution when `allow_choch_only_entries: false`.
  - Rejections logged at `DECISION` level with clear diagnostics (`"CHoCH only, awaiting BOS"`, `"CHoCH failed - trapped"`).
  - Stop loss anchored to the confirmed Lower High (shorts) or Higher Low (longs) for structural invalidation protection.
- **Pre-Trade Checklist Item #11 (`src/execution/order_manager.py`)**:
  - Validates "Structure confirmed (BOS after CHoCH)" before sending order requests.
- **Telegram Multi-Tier Notifications (`src/notifications/telegram_service.py`)**:
  - CHoCH detected $\rightarrow$ `DECISION`/`INFO` (silent digest queue).
  - BOS confirmed + valid setup $\rightarrow$ `SIGNAL` (immediate loud alert).
  - Failed CHoCH / Trap $\rightarrow$ `DECISION` (silent digest queue).
- **MQL5 EA Chart Visuals (`mql5/Experts/PythonBridgeEA.mq5`)**:
  - Optional inputs to draw CHoCH (dashed line + label) and BOS (solid line + label) on MT5 charts, with failed CHoCH lines rendered in gray (`clrDarkGray`).
  - Native MetaEditor compilation verified: 0 errors, 0 warnings.
- **Automated Test Coverage (`tests/test_structure_confirmation.py`)**:
  - Full synthetic test suite covering bearish/bullish confirmation sequences, trap invalidations, wick-only rejections, and timeout expirations.

## [3.1.0] - 2026-11-03

### Added - Quant Best-Practice Upgrade (Stages 1-3)
- **Broker Adapter Layer (`src/adapters/`)**:
  - `BrokerAdapter` abstract interface with `LiveMT5Broker` and `SimBroker`.
  - High-fidelity simulation mechanics: half-spread + volatility slippage, institutional commissions, margin verification, and SL/TP boundary triggers.
- **Data & Feature Pipeline (`src/features/`)**:
  - Zero-lookahead feature extraction: ATR(14), EMA(20, 50, 200), RSI(14), ADX(14), Bollinger Bands, fractal swings, and MTF alignment.
- **Modular Strategy Plug-in Architecture (`src/strategies/`)**:
  - `SignalModule`, `FilterModule`, `ExitModule`, and `SizingModule` composable pipeline with `LegacySMCStrategy` adapter.

## [3.0.0] - 2026-11-02

### Added - Modern Quantitative Upgrade & Account Adaptation Suite
- **Stage 0 & 0A: Baseline Data & Account-Size Adaptation ($5 Minimum Floor)**:
  - Account tiers in `config/account_tiers.json`: `BELOW_FLOOR` (<$5), `MICRO` ($5-$25), `MINI` ($25-$100), `SMALL` ($100-$500), `STANDARD` ($500-$5,000), `LARGE` (>$5,000) with 5% boundary hysteresis.
  - Universal Cent Account detection (USC, EUC, GBX) with transparent 100:1 unit conversion and dual-unit logging.
  - Single shared mathematical position sizing algorithm strictly enforcing downward volume step rounding, `OrderCalcProfit` dollar verification, and operator micro-account high risk gating.
  - Symbol Affordability Scanner (`src/risk/affordability.py`) classifying pairs into `TRADABLE`, `MARGINAL`, and `BLOCKED` with automated ranking by transaction drag.
  - Multi-timeframe historical candle loader (`src/data/historical_loader.py`) with automatic broker UTC offset detection and local caching outside git.
  - Real broker cost profiler (`src/data/cost_model.py`) reporting hourly spread distributions, tick values, and swap rates.
  - Property-based tests verifying zero ceiling breaches across random balances, prices, and stop distances.
  - Live demo execution verification on MT5 demo account (#1200057798) placing and closing 0.01 lot EURUSD with verified risk metrics.

- **Stage 1: Quantitative Validation Suite (`src/learning/validation/`)**:
  - Purged and embargoed walk-forward engine eliminating serial correlation and lookahead contamination.
  - Bailey & Lopez de Prado (2014) Deflated Sharpe Ratio (DSR) and Combinatorial Symmetric Cross-Validation (CSCV) Probability of Backtest Overfitting (PBO).
  - 5,000-resample Monte Carlo permutation engine estimating ending equity distributions and ruin/breach probabilities for Demo (10%), Live (6%), and Prop (4%) drawdown limits.
  - Friction stress-testing matrix evaluating performance at 1.0x, 1.5x, and 2.0x transaction drag.

- **Stage 2: Dynamic Regime Detection (`src/regime/`)**:
  - Feature extraction engine: realized volatility, ATR percentiles, ADX, MTF trend slope, session codes, and spread ratios.
  - Gaussian volatility state classifier mapping conditions into SCALP, HOLD, and NO_TRADE.
  - Feature flag `regime_detection` with non-intrusive shadow mode logging and OOS expectancy tracking.

- **Stage 3: Meta-Labeling Engine (`src/learning/meta/`)**:
  - Triple-barrier labeling algorithm (take profit, stop loss, and vertical bar expiration barriers) computing exact return in R.
  - Strictly causal point-in-time feature extractor with runtime timestamp assertions preventing future lookahead.
  - Platt-calibrated regularized classifier with Brier score tracking and SHA-256 model weight registry.
  - Risk scaling invariant: can only downscale risk (<= 1.0x) or skip; never upscales risk.

- **Stage 4: Volatility and Risk Modeling (`src/risk/`)**:
  - GARCH(1,1) conditional variance forecasting, RiskMetrics EWMA ($\lambda=0.94$), HAR-RV, and ATR fallback.
  - Fractional Kelly criterion engine capping allocation strictly by tier target risk, with mathematical zero floor on non-positive expectancy.
  - Multi-tier drawdown throttle (100% -> 70% -> 40% -> 0% pause at 80% buffer utilization) and 99% CVaR tail risk alerting.
  - Portfolio exposure engine: strictly max 1 open position on MICRO/MINI tiers, net currency exposure caps, cross-pair rolling correlation gating (>0.70), and PCA concentration ratio.

- **Stage 5: Execution Quality and Analytics (TCA) (`src/execution/analytics/`)**:
  - Per-order TCA auditing: intended vs filled price, slippage points and USD, latency, retcodes, and transaction drag.
  - Time-of-day spread filter blocking rollover hours (21:00-23:00 UTC) and spreads exceeding 2.0x hourly median.
  - Passive limit order engine with expiration.
  - Idempotent request manager with SHA-256 request IDs preventing duplicate orders on network retries.
  - Automated weekly execution cost reporting for Telegram and Markdown artifacts.

- **Stage 6: Monitoring and Decay Detection (`src/monitoring/`)**:
  - CUSUM strategy decay guard with 3-tier sigma alerts: Warning (2.0s), Reduced Risk (2.5s), Pause (3.0s, requiring operator `/run`).
  - Feature drift detection using Population Stability Index (PSI) and Kolmogorov-Smirnov 2-sample tests over rolling 100 bars.
