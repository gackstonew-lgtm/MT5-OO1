# Institutional Quantitative Trading System Architecture

## 1. System Overview
The Multi-Timeframe Algorithmic Day-Trading System is designed as a decoupled, event-driven trading platform connecting a Python quantitative engine to MetaTrader 5 via high-speed IPC (ZeroMQ / TCP Sockets).

```mermaid
flowchart TD
    subgraph MetaTrader5 ["MetaTrader 5 Client Terminal"]
        BridgeEA["PythonBridgeEA.mq5\n(JSON Socket / ZeroMQ Server)"]
        ChartUI["Chart Visualizer\n(PWH, PWL, PDH, PDL, FVG, OB, Orders)"]
    end

    subgraph PythonEngine ["Python Quantitative Engine"]
        Orchestrator["Master Orchestrator\n(src/core/orchestrator.py)"]
        MarketData["Market Data Engine\n(W1, D1, H4, H1, M15)"]
        WeeklyDaily["Weekly & Daily Liquidity Engine\n(PWH, PWL, PDH, PDL)"]
        MTF["Multi-Timeframe Analyzer\n(Cascading Confluence)"]
        SMC["ICT / SMC Strategy Engine\n(FVG, OB, Sweeps, MSS)"]
        Scorer["Confluence Scorer (>= 75/100)"]
        Risk["Risk & Position Sizing Engine\n(0.5% Equity Budget, SL/TP)"]
        Execution["Execution & Order Manager\n(10-Point Pre-Trade Checklist)"]
        Manager["Trade Lifecycle Manager\n(+1.0R BE, +1.5R Partial, Trailing)"]
        Learning["Champion/Challenger & Genetic Optimizer"]
        Monitor["Real-Time Dashboard & Telegram Alerts"]
    end

    BridgeEA <-->|JSON IPC| Orchestrator
    Orchestrator --> MarketData
    MarketData --> WeeklyDaily & MTF
    WeeklyDaily & MTF --> SMC
    SMC --> Scorer
    Scorer --> Risk
    Risk --> Execution
    Execution --> BridgeEA
    BridgeEA --> ChartUI
    Execution --> Manager
    Manager --> Execution
    Execution --> Learning
    Orchestrator --> Monitor
```

## 2. Directory Layout & Boundaries
- `src/core/`: Strongly-typed contracts, config loaders, market data buffers, session clocks, volatility classification, news filter.
- `src/smc/`: Smart Money Concepts & Inner Circle Trader engines: W1/D1 liquidity levels, market structure (BOS, CHoCH, MSS), FVG/OB detection, MTF bias aggregator, confluence scoring.
- `src/risk/`: Capital preservation core: mathematically normalized lot sizing, portfolio risk bounds, circuit breakers, lifecycle position management.
- `src/learning/`: Statistical validation: performance analytics, champion/challenger gating, walk-forward splits, Monte Carlo permutations, genetic parameter optimizer.
- `src/execution/`: Transport layer: ZeroMQ/Socket bridge client, 10-point pre-trade checklist, order submission, simulation engine.
- `src/monitoring/`: Observability: CLI/terminal dashboard, Telegram alerts, report generation.
- `mql5/`: MetaTrader 5 Expert Advisor (`PythonBridgeEA.mq5`) with visual chart indicators.
- `config/`: JSON configuration templates with zero hardcoded credentials.
- `scripts/`: Operational tools (`start_bot.py`, `run_backtest.py`, `validate_readiness.py`, `backup.py`).
- `tests/`: Automated unit and integration test suite.
