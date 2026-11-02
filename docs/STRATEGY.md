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

## 3. Confluence Scoring System (Threshold >= 75 / 100)
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
