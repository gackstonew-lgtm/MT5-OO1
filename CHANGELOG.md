# Changelog

All notable changes to the Institutional Multi-Timeframe Algorithmic Trading Bot (`MT5-OO1`) are documented in this file.

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
  - Deal reconciler auditing MT5 deal history against bot orders for price drift, volume mismatches, and rogue orders.
  - LLM trade reviewer module generating structured review prompt templates; silent when unconfigured.

- **Stage 7: CI and Repository Hygiene**:
  - GitHub Actions CI workflow (`.github/workflows/ci.yml`) validating unittests, secret scans, and baseline verification.
  - Git pre-commit hook preventing commits containing `.env`, tokens, passwords, data caches, or raw reports.
  - Comprehensive documentation updates across `README.md`, `docs/ARCHITECTURE.md`, `docs/STRATEGY.md`, and `CHANGELOG.md`.

## [2.1.0] - 2026-11-02

### Added
- **Startup Warm-Up & Self-Inspection Checklist**:
  - Implemented 7-point validation sequence evaluating IPC socket bridge connectivity, symbol selection, multi-timeframe history depth, broker UTC offset detection, real-time spread/ATR accessibility, account profile & risk caps, and single-instance mutex locks.
  - Added configurable warmup inputs: `InpWarmupBars` (default 300) and `InpSkipMinutesAfterOpen` (default 5 min).
  - Enforced closed-bar trade evaluations only on signal timeframe (M15), completely preventing spurious signals on forming candles.

- **Asynchronous History Force-Loading Engine**:
  - Implemented non-blocking history loading loop with retry backoff (`InpHistoryLoadTimeoutSec = 30`, `InpHistoryRetryWaitMs = 500`).
  - Added realistic, per-timeframe required bar thresholds: W1 (52 bars), D1 (200 bars), H4 (300 bars), H1 (500 bars), M15 (500 bars).
  - Computed hard mathematical floor from indicator periods (ATR, ADX, structure levels) to permanently block trading on insufficiently warmed symbols regardless of override settings.
  - Implemented `InpOnShortHistory` policy modes: `ASK`, `REDUCE`, `BLOCK_SYMBOL`.

- **Continuous Telegram Activity Feed & Digest Engine**:
  - Structured 6-tier logging hierarchy: `CRITICAL`, `TRADE`, `SIGNAL`, `DECISION`, `INFO`, `DEBUG`.
  - Configured 10-second batched digest queue (`FeedIntervalSec = 10.0`) with 4096-character auto-splitting and `disable_notification=true` for silent delivery.
  - Configured immediate, loud push notifications for `CRITICAL`, `TRADE`, and `SIGNAL` events.
  - Implemented Telegram 429 `retry_after` backoff handling ensuring network delays never block low-latency trade execution.
  - Added interactive bot commands restricted strictly to authorized `TELEGRAM_CHAT_ID`: `/verbose on|off`, `/level info|debug|decision`, `/feed on|off`, `/mute <min>`, `/last <N>`.

- **Multi-Platform Audio Alerts**:
  - Native Windows `winsound.Beep` integration in Python bridge and `.wav` audio triggers in MQL5 EA for priority events.

- **Institutional Risk Management & Position Sizing**:
  - Dynamic lot sizing derived mathematically from structural stop-loss distance, `SYMBOL_TRADE_TICK_VALUE`, `SYMBOL_TRADE_TICK_SIZE`, and `SYMBOL_TRADE_CONTRACT_SIZE`.
  - Clamped volume to `SYMBOL_VOLUME_MIN`/`MAX` with strict downward rounding to `SYMBOL_VOLUME_STEP` (preventing round-up risk inflation).
  - Hard trade skip if minimum lot size exceeds `MaxRiskPercentPerTrade` (default 2% of equity/balance).
  - Unified `OrderCalcProfit` dollar calculations for both stop-loss risk and take-profit gain, including spread and commission estimates.
  - Strict Reward-to-Risk ratio filter enforcing a minimum 1:2 R:R floor prior to order dispatch.
  - Dual-layer risk enforcement: Pre-check on Python strategy engine and pre-flight validation on MQL5 EA prior to `OrderSend`.

- **Heartbeat Noise Suppression**:
  - Throttled market structure scanning heartbeats to 1 digest entry per closed bar evaluation or significant market structure state changes, eliminating tick-level log flooding.

### Security
- Comprehensive secret sanitization: Cleaned all live bot tokens and account logins from source files and test fixtures.
- Hardened `.gitignore` and `.env.example` templates covering all environment credentials, logs, and compiled binaries.
