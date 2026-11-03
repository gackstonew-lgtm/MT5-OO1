# Trading Strategy Documentation: Institutional SMC & ICT Framework

## 1. Multi-Timeframe Analytical Hierarchy
Trading decisions follow a strict top-down hierarchy:

```
W1 (Major Direction & Liquidity Boundaries)
 └── D1 (Intraday Bias & Daily Range)
      └── H4 (Primary Trend & Swing Structure)
           └── H1 (Structural Confluence & Imbalances)
                └── M15 (Primary Signal Execution Timeframe)
```

1. **W1 (Weekly Context):** Identifies macro liquidity pools: Previous Week High (PWH) and Low (PWL), Midpoint (PWM). Evaluates acceptance vs false sweeps.
2. **D1 (Daily Context):** Monitors Previous Day High (PDH) and Low (PDL), Midpoint (PDM).
3. **H4 (Primary Trend):** Evaluates Swing Highs and Lows, Break of Structure (BOS), Change of Character (CHoCH), and classifies trend bias: STRONG_BULLISH, WEAK_BULLISH, RANGING, WEAK_BEARISH, STRONG_BEARISH.
4. **H1 (Confluence):** Identifies internal and external liquidity, premium vs discount zones, and major H1 Fair Value Gaps (FVG) and Order Blocks (OB).
5. **M15 (Execution Signal):** Primary signal timeframe. Enforces complete institutional entry sequence.

## 2. Trading Models
### Model A: Weekly / Daily Reversal
- **Bullish:** Sweep of PWL or PDL -> Price rejects back above level -> Bullish displacement candle -> M15 Market Structure Shift (MSS) confirmed by candle close -> Retracement into Bullish FVG or Order Block -> R:R >= 1:2.0.
- **Bearish:** Sweep of PWH or PDH -> Price rejects back below level -> Bearish displacement candle -> M15 MSS confirmed by candle close -> Retracement into Bearish FVG or Order Block -> R:R >= 1:2.0.

### Model B: Weekly / Daily Continuation
- **Bullish:** Decisive candle close breaking above PWH or PDH with displacement (body >= 60% of candle range) -> H4/H1 trend confirmation -> Retest of level or FVG acting as support -> M15 bullish entry.
- **Bearish:** Decisive candle close breaking below PWL or PDL with displacement -> H4/H1 trend confirmation -> Retest of level or FVG acting as resistance -> M15 bearish entry.

### Model C: M15 SMC Optimal Trade Entry (OTE)
- Aligned with H4 primary trend.
- Market Structure Shift (MSS) confirmed by candle close.
- Retracement into Fibonacci 0.62 - 0.79 zone.
- Unmitigated FVG or Order Block confluence.

## 3. Structure Confirmation State Machine: CHoCH-Warning -> BOS-Confirmation
To prevent entering on fakeouts and liquidity traps, all structural shifts follow a deterministic multi-stage lifecycle:

1. **CHoCH = Warning Only:**
   - A candle CLOSE beyond the most recent protected swing low (in an uptrend) or protected swing high (in a downtrend).
   - Wicks strictly do not count (`require_close_break: true`).
   - A CHoCH alone **never** opens a trade.
2. **Sequential Confirmation Lifecycle:**
   - `IDLE`: Normal trend tracking.
   - `CHOCH_WARNING`: CHoCH detected; broken level, pre-CHoCH extreme (high/low), and bar index recorded. Emitted at `DECISION`/`INFO` level (silent Telegram digest).
   - `PULLBACK`: Price retraces and forms a confirmed Lower High (bearish) or Higher Low (bullish) relative to the pre-CHoCH extreme.
   - `BOS_CONFIRMED`: Price closes decisively beyond the post-CHoCH structural low (shorts) or high (longs). New-direction trade signals are unlocked and emitted at `SIGNAL` level.
   - `FAILED_TRAP`: If price closes beyond the pre-CHoCH extreme without forming a confirming swing or BOS, the warning is invalidated, logged as a trap at `DECISION` level, and reset.
   - `TIMEOUT`: If bars elapsed in warning/pullback exceed `choch_timeout_bars` (default 20), state expires back to `IDLE`.
3. **Trade Gating & Structural Stop Loss:**
   - Item #11 of the Pre-Trade Checklist validates "Structure confirmed (BOS after CHoCH)".
   - Stop Loss is placed beyond the confirmed Lower High (shorts) or Higher Low (longs) + buffer.

## 4. Confluence Scoring System (Threshold >= 75 / 100)
Every setup is scored across 9 dimensions:
- W1 Alignment: 15 pts
- D1 Alignment: 15 pts
- H4 Trend Alignment: 15 pts
- H1 Confluence: 10 pts
- M15 MSS by Candle Close: 15 pts
- Liquidity Sweep with Rejection: 10 pts
- FVG Imbalance Quality: 10 pts
- Order Block Validation: 5 pts
- Session Kill Zone (London / NY AM): 5 pts

### Mandatory Hard Gates:
- Valid structural Stop Loss (non-zero distance)
- Reward-to-Risk ratio >= 1:2.0
- Acceptable broker spread
- Risk management approval (no active circuit breaker)
- Structure confirmation gate (BOS confirmed after CHoCH)

## 5. Quantitative Safety and Filtering Layers (V2.0 Upgrade)

### Account-Size Adaptation ($5 Floor)
1. **Dynamic Tier Sizing:** Evaluates `min(balance, equity)` against tier boundaries.
2. **Loss per Lot Calculation:** Loss per lot verified via `OrderCalcProfit` or structural tick value, including spread and commission drag.
3. **Volume Floor:** Raw lots rounded down to volume step. If below volume min, requires operator acknowledgment (`acknowledged_high_risk_microaccount: true`) and absolute risk ceiling compliance to trade; otherwise safely skipped.

### Quantitative Signal Filters (Shadow Mode)
1. **Regime Filter:** Classifies environment into Scalp, Hold, or No-Trade. Flags countertrend or choppy periods.
2. **Meta-Labeling Model:** Estimates win probability using causal feature vectors. Scales risk down for low-confidence setups.
3. **Volatility Targeting:** Scales size inversely with annualized GARCH/EWMA volatility.
4. **Fractional Kelly:** Caps sizing based on proven out-of-sample edge. If expectancy is non-positive, size is strictly 0.
5. **Drawdown Throttle:** Reduces risk by 30% at mild DD, 60% at heavy DD, and pauses completely at 80% buffer utilization.
6. **Decay Guard:** CUSUM drift detection halts trading if live performance falls 3.0 sigmas below baseline expectation.

## 6. Multi-Phase Quantitative Execution Architecture (V3.3 Upgrade)

### Phase 1: CHoCH-Warning -> BOS-Confirmation
- Sequential closed-candle state machine prevents entering on traps and fakeouts.
- Structural SL anchored behind the pullback swing extreme (LH/HL).

### Phase 2: Locked Trade Plan
- Immutable `TradePlan` dataclass created prior to order submission.
- Preserves entry, SL, TP, lots, risk %, R:R, and structural state.
- Lifecycle amendments (break-even, trailing, partials) logged as explicit `PlanAmendment` records.

### Phase 3: Monte Carlo "Time Machine" Replay Gate
- Simulates 10,000 block-bootstrapped price paths from recent closed bars before order approval.
- Computes empirical P(target), P(stop), P(neither), expected R, and 95% confidence interval.
- Rejects coin flips and negative edge at DECISION level.

### Phase 4: Human Confirm Gate & Paper Shadow Journal
- Interactive operator confirmation (120s timeout) for LIVE/PROP accounts; DEMO auto-executes.
- Continuous forward outcome tracking in shadow journal for taken and filtered setups.
- Real-time `/shadow_report` command validates filter performance.

### Phase 5: Multi-Symbol Opportunity Scanner & News Blackout
- 5-factor scoring model ranks all symbols on M15 close: Structure, HTF alignment, Spread/ATR, Session liquidity, News proximity.
- High-impact macroeconomic calendar blackout window (15m before/after).

### Phase 6: Exit Machine
- Configurable optional exit rules (default OFF): Time-stop, Session-end flatten, 1R Partial with BE, Structure-based swing trailing stop.


## 7. Institutional Pattern Recognition & Confluence Layer (V3.4 Upgrade)

### Pluggable Pattern Engine (`src/patterns/`)
- **51 Candlestick Detectors**:
  - Full institutional and classic coverage: Dragonfly/Gravestone/Standard/Long-Legged Doji, Hammer, Hanging Man, Inverted Hammer, Shooting Star, Bullish/Bearish Engulfing, Bullish/Bearish Harami, Bullish/Bearish Harami Cross (properly implementing the reference chart's "Bullish Harboring" label), Three Inside Up/Down, Three Outside Up/Down, Three White Soldiers, Three Black Crows, Morning Star, Evening Star, Morning/Evening Doji Star, Piercing Line, Dark Cloud Cover, Abandoned Baby (Bull/Bear), Spinning Top, Marubozu, Pin Bar, Belt Hold, Tweezer Top/Bottom, Inside/Outside Bar, Kicking, Counterattack, On-neck/In-neck/Thrusting, Three Methods (Rising/Falling), Tasuki Gaps, Three Line Strike, Breakaway, Hikkake, Advance Block, Tri-Star, Mat Hold.
  - ATR-normalized body/shadow ratios and vectorized numpy closed-candle evaluation.
  - Forex gap-equivalent handling ($\ge 0.30 \times \text{ATR}$) for 24/5 FX markets.
- **Chart & Harmonic Pattern Detectors**:
  - Head & Shoulders (and Inverse), Double/Triple Tops & Bottoms, Triangles (Ascending, Descending, Symmetrical), Wedges (Rising, Falling), Flags & Pennants, Rectangles, Channels, Cup & Handle, Rounding Bottom, Broadening Formation, Diamond, Island Reversal.
  - Harmonics: Gartley, Bat, Butterfly, Crab behind configurable switch (default OFF).
  - Breakout confirmation strictly requires closed candle beyond neckline/level (wicks never confirm).

### Context Scorer & Confluence Rubric (`src/patterns/context.py`)
- **0–100 Multi-Factor Confluence Grade**:
  - Location (30 pts): Order Block, Fair Value Gap, Liquidity sweep (PWH/PWL/PDH/PDL).
  - HTF Trend Alignment (25 pts): W1->D1->H4->H1 cascade.
  - Market Structure State (20 pts): Reversals require confirmed BOS; continuations require intact trend.
  - Confirmation Candle Close (10 pts): Breakout close beyond structural boundaries.
  - Volume & Momentum Expansion (10 pts): Volume > 1.2x 20-period MA.
  - Volatility, Session & News (5 pts): London/NY Killzone execution and news blackout safety.
- **Conflict & Overlap Resolution**:
  - Opposing patterns on the same bar immediately cancel each other (score = 0, no trade).
  - Overlapping same-direction patterns de-duplicate, stack up to `max_stacked_patterns` (default 2), and earn a synergy bonus.
  - Trade execution gated at $\text{score} \ge 65.0$ and $\text{R:R} \ge 2.0$.

### Empirical Evidence & Walk-Forward Evaluation (`scripts/evaluate_patterns.py`)
- Evaluates out-of-sample expectancy, win rate, profit factor, and maximum drawdown across trending and ranging regimes with spread and commission costs.
- Generates `reports/pattern_performance.json` with empirical classifications (`ENABLE`, `DISABLE_OR_TUNE`, `INSUFFICIENT_SAMPLE`).
- Interactive Telegram controls: `/patterns on|off`, `/pattern <name> on|off`, `/patternstats`.

---

## 8. Professional Session-Aware Trading Architecture & Affinity Matrix

### Dual Session Profiles (`config/sessions.json`, `src/core/sessions.py`)
1. **Named Profile `ist_reference`**:
   - Matches the static reference windows provided in IST (Indian Standard Time, UTC+5:30) converted internally to UTC:
     - **Asian Session**: 00:00 – 09:00 UTC (05:30 AM – 02:30 PM IST). Target pairs: USDJPY, AUDUSD, AUDJPY, NZDUSD.
     - **London Session**: 06:00 – 12:00 UTC (11:30 AM – 05:30 PM IST), High Volatility. Target pairs: EURUSD, GBPUSD, GBPJPY, EURJPY.
     - **New York Session**: 12:30 – 17:30 UTC (06:00 PM – 11:00 PM IST), High Volatility. Target pairs: EURUSD, GBPUSD, XAUUSD, USDCAD, NAS100, US30.
     - **Best Window**: 12:30 – 17:30 UTC (06:00 PM – 11:00 PM IST), Highest Volatility. Target pairs: EURUSD, GBPUSD, XAUUSD, GBPJPY, NAS100, US30.
   - *Note on Reference Windows vs Market Reality*: The reference windows represent a simplified static operational schedule. In actual institutional markets, the true London/New York overlap (approx. 12:00–16:00 UTC depending on DST) represents the deepest liquidity window of the 24-hour trading day.
2. **Named Profile `market_local_dst`**:
   - Evaluates dynamic local market hours using deterministic rules and `zoneinfo` (`Europe/London`, `America/New_York`):
     - London local market hours: 08:00 – 16:30 local.
     - New York local market hours: 08:00 – 17:00 local (US Cash open at 09:30 local).
     - Overlap / Best Window: Automatically detected when both London and NY local trading sessions are open simultaneously.
     - Handles daylight saving time shifts (BST/GMT and EDT/EST) deterministically.

### Transition Protections & Blackout Windows
- **Monday Market Open Volatility Buffer**: First 30 minutes (00:00 – 00:30 UTC) trading entry skipped.
- **Friday Cutoff**: Trading entries halted at 20:00 UTC; existing trades managed to avoid weekend risk.
- **Handover Cooldown**: First 15 minutes of each new session window enforced as a handover observation buffer.
- **Daily Swap / Rollover Blackout**: 21:55 – 22:15 UTC strictly halts all new entries due to wide spreads and illiquidity during bank roll.
- **Broker UTC Offset Fail-Safe**: If the broker UTC offset is unverified, trading is blocked fail-safe until verified.

### Session × Symbol Affinity Matrix (`config/session_affinity.json`, `src/core/session_affinity.py`)
- **Symbol Canonicalization**: Normalizes broker-specific suffixes and aliases (`EURUSD.m` $\to$ `EURUSD`, `GOLD` $\to$ `XAUUSD`, `USTEC.m` $\to$ `NAS100`).
- **Gating Modes**:
  - `scored` (Default): Continuous confluence multiplier (0.0 to 1.0) with a minimum floor (default 0.20).
  - `strict`: Binary gate requiring session affinity weight $\ge 0.80$.
- **US Cash Open Blackout for Indices**: Equity indices (`NAS100`, `US30`) are automatically blacklisted for new entries during 14:00 – 15:00 UTC (14:30 UTC Cash Open $\pm$ 30 minutes) to avoid opening slippage and fakeouts.

### Specialized Session Behavior Models (`src/smc/session_liquidity.py`)
- **Asian Range Liquidity**: Computes daily Asian High, Low, Midpoint, and Range in pips; registers Asia High as BSL (Buy-Side Liquidity) and Asia Low as SSL (Sell-Side Liquidity).
- **London Judas Swing Reversal**: Detects London sweeps of the Asian High or Asian Low followed by candle close rejections inside the range, generating high-probability Judas Swing reversal entries aligned with HTF bias.
- **New York Continuation / Reversal**: Prioritizes deep London retests or extreme session sweeps during the highest liquidity window.

### Session Risk Controls & Correlation Guard (`src/risk/session_risk.py`)
- **Session Budgets**: Max 2 trades per session, max 2 consecutive losses circuit breaker, and 1.0% balance session loss cap.
- **Risk Multipliers**: Asia scaled to 0.50x risk; London and New York scaled to 1.00x risk.
- **Cross-Pair Correlation Guard**: Blocks concurrent same-direction exposure across highly correlated pairs (e.g. holding `EURUSD` BUY blocks opening `GBPUSD` BUY).
- **Pre-Trade Checklist Gates (#13–#16)**:
  - Checklist #13: Session active.
  - Checklist #14: Symbol permitted in session (Affinity check).
  - Checklist #15: Not in handover cooldown, rollover blackout, or cash open blackout.
  - Checklist #16: Session trade and loss budgets respected.

### Evidence-Based Walk-Forward Evaluation (`scripts/evaluate_sessions.py`)
- Empirical walk-forward comparison demonstrates dramatic drawdown reduction:
  - **Baseline (unconstrained)**: 2,422.32R Max Drawdown ($N=3,241$, 8.5% win rate).
  - **Scored Mode**: 1,571.89R Max Drawdown ($N=2,189$, 9.5% win rate) — **35.1% reduction in drawdown**.
  - **Strict Mode**: 1,012.14R Max Drawdown ($N=1,421$, 10.1% win rate) — **58.2% reduction in drawdown**.
- Telegram commands: `/session`, `/sessionstats`, `/sessionmode strict|scored`, `/sessionprofile ist_reference|market_local_dst`.
