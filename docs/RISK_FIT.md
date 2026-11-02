# Universal Risk Fit Engine: Mathematical Specifications & Architectural Limits

## 1. Overview & Core Philosophy

The **Universal Risk Fit Engine** (`src/risk/fit_engine.py`) adaptively determines stop distance, timeframe, lot size, risk %, and reward-to-risk for any financial instrument (Forex, Crypto, Commodities, Indices) and any balance scale from **$5.00 upwards**.

The fundamental quantitative doctrine is:
> **Hard ceilings always win.** The engine only operates *strictly inside* operator-defined limits and never raises risk, widens a stop beyond the risk ceiling, or inflates lot size to force trade feasibility. If no mathematically valid trade exists, the engine skips execution and provides an explicit root-cause explanation.

---

## 2. Mathematical Formulations

### 2.1 Risk Budget Calculation
For an account with balance $B$ and equity $E$, the risk capital base $C$ is computed conservatively as:
$$C = \min(B, E)$$

The monetary risk budget $R_{\text{budget}}$ for target risk percentage $r_{\text{target}}$ is:
$$R_{\text{budget}} = C \times \left( \frac{r_{\text{target}}}{100} \right)$$

*Micro-Account Opt-in Guard:* If the account is in the `MICRO` tier ($5.00–$25.00), trading at minimum lot is allowed **only** if the two operator flags are both `true`:
- `acknowledged_high_risk_microaccount` (in `config/account_tiers.json`)
- `micro_account_min_lot_allowed` (in `config/strategy_config.json`)
And the resulting risk must not exceed the tier's absolute risk ceiling $r_{\text{ceiling}}$.

---

### 2.2 Usable Stop Window: $[SL_{\min}, SL_{\max}]$

#### Maximum Usable Stop ($SL_{\max}$)
The maximum stop distance is the widest stop that minimum volume ($V_{\min}$) can afford without breaching the risk budget:
$$SL_{\max} = \frac{R_{\text{budget}}}{L_{\text{price\_unit}}(V_{\min})}$$
where $L_{\text{price\_unit}}(V_{\min})$ is the monetary loss per price unit at minimum lot, verified dynamically via `OrderCalcProfit` for the exact trade direction.

In broker points:
$$SL_{\max}^{\text{pts}} = \frac{SL_{\max}}{\text{Point}}$$

#### Minimum Usable Stop ($SL_{\min}$)
The minimum stop is enforced to protect against broker execution boundaries, friction drag, and market noise:
$$SL_{\min} = \max \Big( SL_{\text{broker}}, \; SL_{\text{friction}}, \; SL_{\text{noise}}, \; SL_{\text{structure}} \Big)$$

1. **Broker Boundary Constraint:**
   $$SL_{\text{broker}} = \big(\text{StopsLevel} + \text{FreezeLevel} + \text{Buffer}_{\text{pts}}\big) \times \text{Point}$$
2. **Friction / Cost Ceiling Constraint:**
   Total transaction friction in price:
   $$\text{Cost} = \text{Spread}_{\text{price}} + \text{Commission}_{\text{price}} + \text{Slippage}_{\text{price}}$$
   The stop must be wide enough that transaction friction does not exceed fraction $f_{\text{cost}}$ (defaults: `SCALP = 0.08R`, `HOLD = 0.05R`):
   $$SL_{\text{friction}} = \frac{\text{Cost}}{f_{\text{cost}}}$$
3. **Volatility Noise Floor:**
   $$SL_{\text{noise}} = M_{\text{noise}} \times \text{ATR}(\text{Timeframe})$$
   where $M_{\text{noise}} \in [1.5, 2.0]$.
4. **Structural Invalidation:**
   $$SL_{\text{structure}} = |P_{\text{entry}} - P_{\text{invalidation}}|$$

#### Feasibility Criterion
A candidate trade is **FEASIBLE** on a given timeframe if and only if:
$$SL_{\min} \le SL_{\max}$$

If feasible, the usable window is $[SL_{\min}, SL_{\max}]$. The structural stop $SL_{\text{struct}}$ is clamped strictly within this window:
$$SL_{\text{chosen}} = \max\Big(SL_{\min}, \; \min\big(SL_{\max}, \; SL_{\text{struct}}\big)\Big)$$
The stop is never placed outside $[SL_{\min}, SL_{\max}]$.

---

### 2.3 Timeframe Progression & Balance Needed

If $SL_{\min} > SL_{\max}$ on the initial timeframe, the engine iterates through candidate timeframes in order of suitability:
$$\mathcal{T} = \{\text{M5}, \text{M15}, \text{M30}, \text{H1}, \text{H4}\}$$
As timeframe increases, ATR, friction ratios, and market structure adjust. If no timeframe provides a feasible window, the symbol is marked **`BLOCKED_BY_BALANCE`**.

The minimum balance needed for the instrument to become tradable at the tier target risk is computed at the cheapest timeframe:
$$B_{\text{needed}} = \frac{SL_{\min}^{\text{cheapest}} \times L_{\text{price\_unit}}(V_{\min})}{\frac{r_{\text{target}}}{100}}$$

---

### 2.4 Volume Quantization & Margin Verification

Lot size is calculated and truncated downwards (never rounded up):
$$\text{RawLots} = \frac{R_{\text{budget}}}{SL_{\text{chosen}} \times L_{\text{price\_unit}}(1.0)}$$
$$V = \left\lfloor \frac{\text{RawLots}}{V_{\text{step}}} \right\rfloor \times V_{\text{step}}$$
clamped to:
$$V_{\text{final}} = \min\big(V, \; V_{\text{broker\_max}}, \; \text{Cap}_{\text{profile}}, \; \text{Cap}_{\text{tier}}\big)$$

Margin requirement is verified via `OrderCalcMargin`:
$$\text{MarginRequired}(V_{\text{final}}) \le \text{FreeMargin} \times \left( \frac{\text{MaxMarginPct}}{100} \right)$$
If margin is insufficient, volume is scaled down proportionally to remain within margin safety limits.

---

### 2.5 Dynamic Reward-to-Risk & Expectancy Filter

1. **Required R:R:**
   When empirical trade sample size $n \ge 30$, the lower confidence bound $p_{\text{lcb}}$ of the hit rate $\hat{p}$ is computed via the Wilson score interval:
   $$p_{\text{lcb}} = \frac{\hat{p} + \frac{z^2}{2n} - z \sqrt{\frac{\hat{p}(1-\hat{p})}{n} + \frac{z^2}{4n^2}}}{1 + \frac{z^2}{n}}$$
   The break-even reward-to-risk ratio is:
   $$\text{RR}_{\text{be}} = \frac{1 - p_{\text{lcb}}}{p_{\text{lcb}}}$$
   The required R:R includes a safety margin $\Delta_{\text{margin}}$ (default 0.3R) and friction drag $\text{Cost}_R$:
   $$\text{RR}_{\text{required}} = \max\big(\text{RR}_{\text{floor}}, \; \text{RR}_{\text{be}} + \Delta_{\text{margin}} + \text{Cost}_R\big)$$
   where $\text{Cost}_R = \frac{\text{Cost}}{SL_{\text{chosen}}}$. When data is short ($n < 30$), $\text{RR}_{\text{required}} = \max(\text{RR}_{\text{floor}}, \; 2.0 + \text{Cost}_R)$.

2. **Reachable Take Profit Ceiling:**
   Take profit must be reachable within the trade's expected horizon:
   $$\text{TP}_{\text{reachable}} \le M_{\text{reach}} \times \text{ATR}(\text{Timeframe})$$
   $$\text{RR}_{\text{reachable}} = \frac{\text{TP}_{\text{reachable}}}{SL_{\text{chosen}}}$$

3. **Expectancy Filter:**
   Net expected value in R after transaction friction must be strictly positive:
   $$\mathbb{E}[R] = \big(p \times \text{RR}_{\text{actual}}\big) - \big((1 - p) \times 1.0\big) - \text{Cost}_R > 0$$
   If $\text{RR}_{\text{reachable}} < \text{RR}_{\text{required}}$ or $\mathbb{E}[R] \le 0$, the trade is skipped.

---

## 3. Configuration & Parameter Architecture

All thresholds are centralized in `config/risk_fit.json` and hot-reloaded automatically:

| Key | Default | Description |
|:---|:---|:---|
| `max_cost_fraction.SCALP` | `0.08` | Max allowed friction fraction of stop for scalps (8% of R) |
| `max_cost_fraction.HOLD` | `0.05` | Max allowed friction fraction of stop for hold/swing (5% of R) |
| `noise_multiplier.HOLD` | `1.5` | Multiplier on ATR for noise floor |
| `broker_buffer_points` | `5.0` | Broker stops level safety buffer |
| `min_rr_floor` | `1.5` | Absolute floor for reward-to-risk |
| `default_rr` | `2.0` | Default baseline reward-to-risk when hit rate is unmeasured |
| `reach_multiplier` | `5.0` | Multiplier on ATR defining reachable take profit ceiling |
| `spread_to_atr_max_ratio` | `0.20` | Max spread/ATR ratio for active-trading mode |
| `default_expected_slippage_points` | `2.0` | Default slippage when empirical TCA records are absent |
| `default_commission_per_lot` | `6.0` | Standard ECN/raw institutional round-turn commission ($/lot) |
| `profile_cache_path` | `data/cache/instrument_profiles.json` | Path to persistent instrument profile cache |
| `rollover_window_utc` | `21:55 - 22:15` | Daily broker swap/rollover blackout window |

---

## 4. Visibility & Operator Controls

- **Telegram `/fit [symbol]`:** Generates live feasibility windows, lot sizing, expected value in R, and required balance.
- **Telegram `/status`:** Displays per-symbol specifications, risk metrics, and fit engine status.
- **State Change Alerts:** Deduplicated notifications emitted when symbols transition (e.g. `BLOCKED_BY_BALANCE` $\to$ `TRADABLE` after deposit).
