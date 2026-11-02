# Security and Risk Management Protocol

## 1. Zero Credential Exposure
- No account numbers, broker passwords, API keys, or Telegram bot tokens are committed to source code or git.
- All secrets are loaded through environment variables or secure local files (`.env`).

## 2. Quantitative Capital Preservation Rules
- **Default Risk per Trade:** 0.5% of live account equity.
- **Strict Volume Quantization:** Order volume is rounded strictly down to the broker's lot step.
- **Minimum Lot Breach Rejection:** If broker's minimum lot exceeds the allowed monetary risk budget, the trade is rejected immediately.
- **Maximum Aggregate Open Risk:** 1.5% total across all positions.
- **Maximum Simultaneous Positions:** 3 positions max.
- **Maximum Daily Loss Limit:** 2.0% of starting equity.
- **Maximum Weekly Loss Limit:** 4.0% of starting equity.
- **Max Consecutive Losses:** 3 consecutive losing trades.

## 3. Circuit Breaker Mechanism
- When daily, weekly, or consecutive loss limits are breached, the circuit breaker triggers automatically.
- New order entries are locked out immediately.
- An alert is sent to Telegram with full event details.
- Manual reset requires administrative token authorization (`AUTHORIZE_SYSTEM_RESET`).

## 4. Operational Safety Modes
- `BACKTEST`: Runs offline over historical datasets.
- `SIMULATION`: Default mode. Simulates fills, spread, and commissions with zero broker risk.
- `DEMO`: Connects to MT5 demo account via IPC bridge.
- `REAL`: Requires explicit environment flag `EXECUTION_MODE=REAL`, identity verification, and passing readiness audit.
