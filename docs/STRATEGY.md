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
