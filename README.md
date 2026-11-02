# Institutional Multi-Timeframe MT5 Algorithmic Day-Trading Bot

[![Platform](https://img.shields.io/badge/Platform-MetaTrader%205%20%7C%20Python%203.10%2B-blue.svg)]()
[![Architecture](https://img.shields.io/badge/Architecture-Modular%20SMC%2FICT%20IPC-orange.svg)]()
[![Tests](https://img.shields.io/badge/Tests-All%20Passing-brightgreen.svg)]()
[![Build](https://img.shields.io/badge/MQL5%20Build-0%20Errors%20%7C%200%20Warnings-success.svg)]()

Institutional-grade, multi-timeframe algorithmic trading system combining MetaTrader 5 (MQL5) with a high-performance Python algorithmic engine. The bot utilizes Smart Money Concepts (SMC), Inner Circle Trader (ICT) liquidity mechanics, multi-timeframe cascading analysis (W1 -> D1 -> H4 -> H1 -> M15), mathematically normalized position sizing, dynamic trade management, continuous Telegram activity feed with sound dispatch, and strict prop-firm / institutional risk controls.

---

## 1. System Architecture

```
                    ┌──────────────────────────────────────────────┐
                    │               MetaTrader 5                   │
                    │  ┌────────────────────────────────────────┐  │
                    │  │           PythonBridgeEA.mq5           │  │
                    │  │  - Single chart attachment (e.g. M15)  │  │
                    │  │  - Chart labels & visual levels (PWH)  │  │
                    │  │  - Local execution & order safety gate │  │
                    │  │  - WebRequest fallback notifications   │  │
                    │  └──────────────────┬─────────────────────┘  │
                    └─────────────────────┼────────────────────────┘
                                          │ TCP Socket IPC (Localhost)
                                          │ Port 5555 (Data & Trade)
                                          │ Port 5556 (Admin/Monitor)
                    ┌─────────────────────┴────────────────────────┐
                    │            Python Algorithmic Engine         │
                    │  ┌────────────────────────────────────────┐  │
                    │  │           src/core/bridge_server       │  │
                    │  │  - Asynchronous event dispatcher       │  │
                    │  │  - Heartbeat & connection supervisor   │  │
                    │  └──────────────────┬─────────────────────┘  │
                    │                     │                        │
                    │  ┌──────────────────┴─────────────────────┐  │
                    │  │      src/smc/ & src/risk/              │  │
                    │  │  - Weekly / Daily liquidity sweeps     │  │
                    │  │  - MTF cascading structure (W1->M15)   │  │
                    │  │  - Exact tick-value lot sizing         │  │
                    │  │  - Reward-to-Risk (min 1:2) filter     │  │
                    │  └──────────────────┬─────────────────────┘  │
                    │                     │                        │
                    │  ┌──────────────────┴─────────────────────┐  │
                    │  │      src/notifications/                │  │
                    │  │  - 6-tier continuous activity feed     │  │
                    │  │  - 10s silent batch digest queue       │  │
                    │  │  - Immediate loud alerts (SIGNAL/TRADE)│  │
                    │  │  - Telegram 429 rate-limit backoff     │  │
                    │  │  - Interactive bot command handler     │  │
                    │  └──────────────────┬─────────────────────┘  │
                    └─────────────────────┼────────────────────────┘
                                          │ HTTPS (Proxy Supported)
                                          ▼
                                   Telegram Bot API
```

### Directory Structure
```
├── mql5/
│   ├── Experts/PythonBridgeEA.mq5   # Native MT5 Expert Advisor bridge
│   └── Sounds/                     # Native audio cues (signal.wav, trade.wav, alert.wav)
├── src/
│   ├── core/                       # TCP bridge server, data engine, session manager
│   ├── smc/                        # Liquidity levels, market structure, FVG/OB, MTF analyzer
│   ├── risk/                       # Mathematical position sizing, trade lifecycle, portfolio caps
│   ├── execution/                  # 10-point pre-trade checklist, order dispatcher
│   ├── monitoring/                 # CLI dashboard, audio alert manager (winsound)
│   ├── notifications/              # Telegram feed manager, transport, commands, formatting
│   └── learning/                   # Champion/challenger model evaluator, walk-forward testing
├── config/
│   ├── account_profiles.json       # DEMO, LIVE, and PROP firm parameters
│   ├── risk_config.json            # Max daily loss, max trade risk %, drawdown caps
│   ├── sessions.json               # London, NY, Asian session definitions & filters
│   ├── symbols.json                # Per-symbol minimum history and parameters
│   └── strategy_config.json        # SMC/ICT threshold configurations
├── scripts/
│   ├── run_bridge_server.py        # Starts standalone IPC bridge server
│   ├── start_bot.py                # Launches end-to-end trading pipeline
│   ├── run_backtest.py             # Historical backtesting runner
│   ├── validate_readiness.py       # Pre-flight environment validation
│   ├── verify_all.py               # Comprehensive end-to-end regression suite
│   └── backup.py                   # Automated snapshot & journal archive script
├── tests/                          # 17+ automated unit & integration test suites
├── docs/                           # ARCHITECTURE.md, STRATEGY.md, SECURITY.md
├── CHANGELOG.md                    # Detailed version history and defect resolutions
├── requirements.txt                # Pinned Python package dependencies
└── README.md                       # Complete deployment & operations manual
```

---

## 2. Prerequisites & Requirements

- **Operating System:** Windows 10, Windows 11, or Windows Server 2019/2022.
- **MetaTrader 5:** Build 4150 or newer with 64-bit `metaeditor64.exe`.
- **Python:** Python 3.10 or newer (tested on Python 3.12 64-bit).
- **Network / Proxy (Optional):** SOCKS5 / HTTP proxy supported via `.env` for environments requiring routed connections.

### Python Dependencies
Install required packages via `pip`:
```bash
pip install -r requirements.txt
```
Key libraries:
- `MetaTrader5` (Official MT5 Python IPC library)
- `python-dotenv` (Secure environment credential loader)
- `numpy` (Fast vector calculations for liquidity and volatility)
- `requests` (HTTP transport fallback)

---

## 3. Installation & Setup

### Step 1: Copy and Compile the MQL5 EA
1. Open MetaTrader 5 and press `F4` to open **MetaEditor**.
2. Copy `mql5/Experts/PythonBridgeEA.mq5` into your MetaTrader 5 terminal directory:
   `<MT5_DATA_FOLDER>/MQL5/Experts/PythonBridgeEA.mq5`
3. Copy audio files from `mql5/Sounds/` into `<MT5_DATA_FOLDER>/MQL5/Sounds/`.
4. In MetaEditor, open `PythonBridgeEA.mq5` and click **Compile** (`F7`).
   *Ensure the compiler outputs: `0 errors, 0 warnings`.*

### Step 2: Configure MetaTrader 5 Terminal Permissions
1. In MetaTrader 5, navigate to **Tools** > **Options** (`Ctrl+O`) > **Expert Advisors**.
2. Check **Allow Algo Trading**.
3. Check **Allow WebRequest for listed URL** and add:
   ```
   https://api.telegram.org
   ```
4. Attach `PythonBridgeEA` to **ONE chart only** (recommended: `EURUSD` or `XAUUSD`, timeframe `M15`).

### Step 3: Configure Environment Secrets (.env)
Copy the template `.env.example` to `.env`:
```bash
copy .env.example .env
```
Edit `.env` with your secure credentials:
```ini
# Telegram Bot Token from @BotFather
TELEGRAM_BOT_TOKEN=123456789:ABCdefGhIJKlmNoPQRsTUVwxyZ_example
# Authorized numeric Telegram Chat ID
TELEGRAM_CHAT_ID=123456789
TELEGRAM_ENABLED=true
# Optional proxy URL (e.g. socks5h://127.0.0.1:1080)
TELEGRAM_PROXY=

# Socket Bridge Settings
BRIDGE_HOST=127.0.0.1
BRIDGE_PORT=5555

# Execution Mode: DEMO | LIVE | PROP
EXECUTION_MODE=DEMO
```

> [!WARNING]
> Never commit `.env` or any file containing live tokens to git. `.env` is explicitly ignored in `.gitignore`.

---

## 4. Running the System

### Option A: Running the Standalone Bridge Server
To run the IPC bridge server that connects MT5 to Telegram and logs events:
```bash
python scripts/run_bridge_server.py
```

### Option B: Running the Full Automated Strategy Pipeline
```bash
python scripts/start_bot.py
```

---

## 5. Account Profiles & Risk Controls

The system supports three strict risk execution profiles configured in `config/account_profiles.json`:

| Parameter | DEMO | LIVE | PROP (FTMO / MFF / Funded) |
|---|---|---|---|
| **MaxRiskPercentPerTrade** | 2.0% | 1.0% | 0.5% |
| **MaxDailyLossPercent** | 5.0% | 3.0% | 4.0% |
| **MaxTotalDrawdownPercent** | 10.0% | 6.0% | 8.0% |
| **MinRewardToRisk** | 1:2.0 | 1:2.0 | 1:2.5 |
| **ForceMinLotRiskFloor** | Active | Active | Active |

### Mathematical Position Sizing
Lot sizes are calculated strictly according to account balance and broker contract specifications:
$$\text{Loss Per Lot} = \frac{|\text{Entry} - \text{SL}|}{\text{Tick Size}} \times \text{Tick Value}$$
$$\text{Calculated Lots} = \left\lfloor \frac{\text{Balance} \times \text{Risk \%}}{\text{Loss Per Lot} \times \text{Volume Step}} \right\rfloor \times \text{Volume Step}$$

- **No Round-Up Risk Inflation:** Volumes are rounded *down* to `SYMBOL_VOLUME_STEP` and clamped between `SYMBOL_VOLUME_MIN` and `SYMBOL_VOLUME_MAX`.
- **Minimum Lot Safety Floor:** If the minimum tradeable volume (`0.01` lots) risks more than `MaxRiskPercentPerTrade`, the trade is **automatically skipped** and logged with the exact risk percentage.
- **Unified Profit Calculation:** Stop-loss risk and take-profit gains are computed using `OrderCalcProfit` through a shared code path including commission and spread estimates.

---

## 6. Startup Warm-Up & History Validation

Before any trade setup is evaluated, `PythonBridgeEA` executes a 7-point pre-flight checklist:
1. **Bridge Socket Connection:** Verifies TCP ping-pong response.
2. **Symbol Selection:** Ensures all enabled symbols are selected in Market Watch.
3. **History Depth Check:** Asynchronously verifies and force-loads bars:
   - W1: 52 bars
   - D1: 200 bars
   - H4: 300 bars
   - H1: 500 bars
   - M15: 500 bars
4. **Hard Floor Indicator Enforcement:** Calculates mathematical floor from longest active indicator periods. Trading is permanently blocked if bars fall below this floor.
5. **Broker UTC Offset Detection:** Synchronizes server clock to UTC.
6. **Spread & ATR Readability:** Validates tick data and market liquidity.
7. **Single-Instance Mutex:** Prevents duplicate EA instances on the same account.

---

## 7. Telegram Activity Feed & Commands

The Telegram feed operates on an institutional 6-tier logging hierarchy:
- `CRITICAL`: Immediate push notification with loud alert sound.
- `TRADE`: Executions, fills, break-even moves, SL/TP modifications. Loud sound.
- `SIGNAL`: High-probability setups discovered on closed candles. Loud sound.
- `DECISION`: Trade rejections, filter outcomes (spread/news/hours), R:R calculations.
- `INFO`: Warm-up progress, bar close scans, status updates.
- `DEBUG`: Verbose internal metrics and calculation details.

### Notification Batching & Rate Limiting
- **Silent Digest Queue:** `INFO` and `DECISION` lines are queued and dispatched every 10 seconds (`FeedIntervalSec = 10`) in batches up to 4096 characters with `disable_notification=true`.
- **Immediate Dispatch:** `CRITICAL`, `TRADE`, and `SIGNAL` messages bypass the queue and send immediately with audible notification.
- **Telegram 429 Compliance:** Built-in `retry_after` parser prevents rate-limit penalties and ensures network retries never delay execution.

### Telegram Remote Commands
*Commands are strictly restricted to the authorized `TELEGRAM_CHAT_ID` configured in `.env`.*

| Command | Arguments | Description |
|---|---|---|
| `/verbose` | `on` \| `off` | Toggles detailed debug logging in the feed. |
| `/level` | `info` \| `debug` \| `decision` | Sets the event filter threshold for batch digests. |
| `/feed` | `on` \| `off` | Enables or pauses periodic digest broadcasts. |
| `/mute` | `<minutes>` | Mutes periodic notifications for $N$ minutes (0 to unmute). |
| `/last` | `[N]` | Retrieves the last $N$ lines from the log ring buffer (default 20). |
| `/status` | *(none)* | Displays comprehensive system health, symbol history, and metrics. |
| `/allowreduced`| `<SYMBOL>` | Authorizes trading on `<SYMBOL>` with reduced history (above hard floor). |
| `/blocksymbol` | `<SYMBOL>` | Blocks trading logic for `<SYMBOL>`. |

---

## 8. Testing & Quality Verification

### Run Unit and Integration Tests
```bash
python -m unittest discover -s tests -v
```

### Run Comprehensive Deep Regression Suite
```bash
python scripts/verify_deep.py
```

### Run Risk Sizing and Noise Suppression Verification
```bash
python scripts/verify_risk_and_noise.py
```

### Validate EA Compilation
From the command line using MetaEditor 64:
```powershell
& "C:\Program Files\JustMarkets MetaTrader 5\metaeditor64.exe" /compile:"mql5\Experts\PythonBridgeEA.mq5" /log:"compile.log"
Get-Content compile.log
```
*Expected: `0 errors, 0 warnings`.*

---

## 9. Production Deployment as Windows Service

To run the Python bridge daemon continuously in the background on a Windows VPS:

### Using NSSM (Non-Sucking Service Manager)
```cmd
nssm install MT5TradingBridge "C:\Users\<USER>\AppData\Local\Programs\Python\Python312\python.exe" "C:\path\to\MT5 001\scripts\run_bridge_server.py"
nssm set MT5TradingBridge AppDirectory "C:\path\to\MT5 001"
nssm set MT5TradingBridge Start SERVICE_AUTO_START
nssm start MT5TradingBridge
```

### Using Windows Task Scheduler
Create a task triggered "At startup" or "At user logon" executing `launch_mt5_config.bat`.

---

## 10. Adding New Symbols or Strategy Logic

1. **Add Symbol to Configuration:**
   Add entry to `config/symbols.json` with appropriate digits, tick value, and minimum history lookbacks.
2. **Implement Strategy Logic:**
   Subclass the strategy engine in `src/smc/strategy_engine.py` or implement custom entry triggers adhering to the closed-candle M15 protocol.
3. **Register in Pipeline:**
   Include the new strategy module in `src/execution/order_manager.py` and run `scripts/verify_deep.py` to confirm zero regressions.

---

## 11. Risk Disclaimer

> [!CAUTION]
> **HIGH RISK INVESTMENT WARNING:**
> Algorithmic trading in financial instruments, foreign exchange (Forex), commodities, and contracts for difference (CFDs) carries a high level of risk and may not be suitable for all investors. The high degree of leverage can work against you as well as for you. Before deciding to trade foreign exchange or CFDs, you should carefully consider your investment objectives, level of experience, and risk appetite.
>
> Past performance is no guarantee of future results. No software or automated algorithm can guarantee profits or eliminate the risk of loss. You should never trade with borrowed capital or money that you cannot afford to lose. The developers and contributors of this software assume no liability for financial losses incurred through its deployment.
