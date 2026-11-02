# Changelog

All notable changes to the Institutional Multi-Timeframe Algorithmic Trading Bot (`MT5-OO1`) are documented in this file.

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
