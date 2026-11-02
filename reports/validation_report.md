# Stage 1 Quantitative Validation Report
*Generated: 2026-11-02T08:59:27.843821+00:00 UTC*

## Executive Summary & Strict Verdicts
- **EURUSD.m**: `edge not demonstrated`
- **XAUUSD.m**: `edge not demonstrated`
- **BTCUSD.m**: `edge not demonstrated`

---
## Symbol: `EURUSD.m`
**Validation Verdict:** `edge not demonstrated`

### 1. Quantitative Performance & Deflated Sharpe
- **Total Trades:** 21 (Wins: 6, Losses: 15)
- **Win Rate:** 28.6%
- **Profit Factor:** 0.93
- **Expectancy in R:** -0.042 R (95% CI: [-0.688, +0.604])
- **Annualized Sharpe:** -0.24
- **Deflated Sharpe Ratio (DSR):** 0.0075 (Benchmark: 0.0, Trials: 10)
- **Probability of Backtest Overfitting (PBO):** 28.6%
- **Max Drawdown:** 5.20% | **Time Under Water:** 7 bars

### 2. Purged & Embargoed Walk-Forward Splits
| Window | In-Sample Trades | In-Sample Expectancy (R) | OOS Trades | OOS Expectancy (R) | OOS Win Rate | OOS Profit Factor | OOS Max DD % |
|---|---|---|---|---|---|---|---|
| #1 | 10 | +0.01 R | 4 | +0.69 R | 50.0% | 2.29 | 0.78% |
| #2 | 10 | +0.69 R | 4 | -1.01 R | 0.0% | 0.00 | 3.15% |
| #3 | 10 | +0.01 R | 2 | -1.01 R | 0.0% | 0.00 | 1.50% |

### 3. Monte Carlo Ruin & Breach Analysis (5,000 Simulations)
- **Probability of Breaching DEMO Drawdown (10%):** 62.64%
- **Probability of Breaching LIVE Drawdown (6%):** 92.88%
- **Probability of Breaching PROP Drawdown (4%):** 99.54%
- **Median Max Drawdown:** 11.51% (P95: 21.40%, P99: 25.59%)

| Starting Balance | Probability of Ruin (<$5) | Median Ending Equity | 5th Percentile Equity | Worst Drawdown % |
|---|---|---|---|---|
| $5.00 | 96.16% | $4.90 | $4.90 | 32.40% |
| $25.00 | 0.00% | $23.94 | $16.76 | 54.14% |
| $100.00 | 0.00% | $99.47 | $73.87 | 44.70% |
| $500.00 | 0.00% | $482.27 | $407.46 | 35.20% |

### 4. Cost Friction & Slippage Stress Sensitivity
| Scenario | Multiplier | Expectancy (R) | Profit Factor | Win Rate | Sharpe | Max DD % | Edge Survives? |
|---|---|---|---|---|---|---|---|
| Baseline | 1.0x | -0.042 R | 0.93 | 28.6% | -0.03 | 5.20% | NO |
| Stress 1.5x | 1.5x | -0.082 R | 0.91 | 28.6% | -0.05 | 5.30% | NO |
| Stress 2.0x | 2.0x | -0.122 R | 0.89 | 28.6% | -0.08 | 5.39% | NO |

**Cost Resilience Status:** `NO EDGE`

---
## Symbol: `XAUUSD.m`
**Validation Verdict:** `edge not demonstrated`

### 1. Quantitative Performance & Deflated Sharpe
- **Total Trades:** 70 (Wins: 22, Losses: 48)
- **Win Rate:** 31.4%
- **Profit Factor:** 1.00
- **Expectancy in R:** +0.089 R (95% CI: [-0.258, +0.436])
- **Annualized Sharpe:** 0.92
- **Deflated Sharpe Ratio (DSR):** 0.0270 (Benchmark: 0.0, Trials: 10)
- **Probability of Backtest Overfitting (PBO):** 71.4%
- **Max Drawdown:** 5.39% | **Time Under Water:** 27 bars

### 2. Purged & Embargoed Walk-Forward Splits
| Window | In-Sample Trades | In-Sample Expectancy (R) | OOS Trades | OOS Expectancy (R) | OOS Win Rate | OOS Profit Factor | OOS Max DD % |
|---|---|---|---|---|---|---|---|
| #1 | 20 | -0.13 R | 14 | +0.49 R | 42.9% | 1.85 | 1.13% |
| #2 | 20 | +0.04 R | 14 | -0.01 R | 28.6% | 0.88 | 3.72% |
| #3 | 20 | +0.21 R | 14 | -0.26 R | 21.4% | 0.66 | 4.62% |
| #4 | 20 | +0.04 R | 7 | +0.98 R | 57.1% | 3.29 | 0.76% |

### 3. Monte Carlo Ruin & Breach Analysis (5,000 Simulations)
- **Probability of Breaching DEMO Drawdown (10%):** 61.32%
- **Probability of Breaching LIVE Drawdown (6%):** 94.52%
- **Probability of Breaching PROP Drawdown (4%):** 99.72%
- **Median Max Drawdown:** 11.13% (P95: 21.15%, P99: 25.56%)

| Starting Balance | Probability of Ruin (<$5) | Median Ending Equity | 5th Percentile Equity | Worst Drawdown % |
|---|---|---|---|---|
| $5.00 | 92.24% | $4.90 | $4.90 | 37.79% |
| $25.00 | 0.00% | $27.46 | $18.26 | 55.64% |
| $100.00 | 0.00% | $107.75 | $79.85 | 42.15% |
| $500.00 | 0.00% | $527.49 | $428.99 | 35.32% |

### 4. Cost Friction & Slippage Stress Sensitivity
| Scenario | Multiplier | Expectancy (R) | Profit Factor | Win Rate | Sharpe | Max DD % | Edge Survives? |
|---|---|---|---|---|---|---|---|
| Baseline | 1.0x | +0.089 R | 1.00 | 31.4% | 0.06 | 5.39% | NO |
| Stress 1.5x | 1.5x | +0.049 R | 0.96 | 31.4% | 0.03 | 5.59% | NO |
| Stress 2.0x | 2.0x | +0.009 R | 0.93 | 31.4% | 0.01 | 5.87% | NO |

**Cost Resilience Status:** `NO EDGE`

---
## Symbol: `BTCUSD.m`
**Validation Verdict:** `edge not demonstrated`

### 1. Quantitative Performance & Deflated Sharpe
- **Total Trades:** 167 (Wins: 48, Losses: 119)
- **Win Rate:** 28.7%
- **Profit Factor:** 0.94
- **Expectancy in R:** -0.034 R (95% CI: [-0.236, +0.207])
- **Annualized Sharpe:** -0.50
- **Deflated Sharpe Ratio (DSR):** 0.0046 (Benchmark: 0.0, Trials: 10)
- **Probability of Backtest Overfitting (PBO):** 100.0%
- **Max Drawdown:** 16.36% | **Time Under Water:** 161 bars

### 2. Purged & Embargoed Walk-Forward Splits
| Window | In-Sample Trades | In-Sample Expectancy (R) | OOS Trades | OOS Expectancy (R) | OOS Win Rate | OOS Profit Factor | OOS Max DD % |
|---|---|---|---|---|---|---|---|
| #1 | 49 | -0.18 R | 33 | -0.29 R | 21.2% | 0.62 | 9.57% |
| #2 | 49 | -0.04 R | 33 | +0.22 R | 36.4% | 1.34 | 3.31% |
| #3 | 49 | +0.17 R | 33 | -0.19 R | 24.2% | 0.75 | 10.43% |
| #4 | 49 | -0.04 R | 17 | +0.58 R | 47.1% | 2.08 | 1.35% |

### 3. Monte Carlo Ruin & Breach Analysis (5,000 Simulations)
- **Probability of Breaching DEMO Drawdown (10%):** 97.92%
- **Probability of Breaching LIVE Drawdown (6%):** 100.00%
- **Probability of Breaching PROP Drawdown (4%):** 100.00%
- **Median Max Drawdown:** 21.89% (P95: 38.26%, P99: 44.59%)

| Starting Balance | Probability of Ruin (<$5) | Median Ending Equity | 5th Percentile Equity | Worst Drawdown % |
|---|---|---|---|---|
| $5.00 | 98.20% | $4.90 | $4.90 | 52.54% |
| $25.00 | 0.02% | $21.96 | $11.43 | 81.09% |
| $100.00 | 0.00% | $94.29 | $56.33 | 66.23% |
| $500.00 | 0.00% | $463.25 | $331.78 | 52.58% |

### 4. Cost Friction & Slippage Stress Sensitivity
| Scenario | Multiplier | Expectancy (R) | Profit Factor | Win Rate | Sharpe | Max DD % | Edge Survives? |
|---|---|---|---|---|---|---|---|
| Baseline | 1.0x | -0.034 R | 0.94 | 28.7% | -0.02 | 16.36% | NO |
| Stress 1.5x | 1.5x | -0.074 R | 0.92 | 28.7% | -0.05 | 17.20% | NO |
| Stress 2.0x | 2.0x | -0.114 R | 0.89 | 28.7% | -0.07 | 18.93% | NO |

**Cost Resilience Status:** `NO EDGE`