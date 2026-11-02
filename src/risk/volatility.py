"""
Advanced Volatility Modeling and Dynamic Volatility-Targeting Engine.
Supports:
1. GARCH(1,1) conditional variance forecasting
2. RiskMetrics EWMA (exponentially weighted moving average, lambda=0.94)
3. HAR-RV (Heterogeneous Autoregressive model of Realized Volatility)
4. Standard ATR fallback
5. Dynamic stop widening / sizing compression
6. Volatility-targeted sizing multiplier (subject to tier ceiling invariants)
"""

from __future__ import annotations
import math
from dataclasses import dataclass
from typing import List, Tuple, Optional
import numpy as np


@dataclass(frozen=True)
class VolatilityForecast:
    symbol: str
    annualized_vol: float       # In annualized percentage (e.g. 0.12 = 12%)
    daily_vol: float            # In daily percentage
    garch_vol: Optional[float]  # GARCH(1,1) estimate
    ewma_vol: float             # EWMA estimate
    atr_vol_equivalent: float   # ATR expressed as annualized vol
    method_used: str            # 'GARCH', 'EWMA', 'HAR_RV', or 'ATR_FALLBACK'
    recommended_stop_scale: float  # Multiplier for stop distance (>= 1.0)
    recommended_size_scale: float  # Multiplier for position size (<= 1.0)


class GarchModel:
    """
    Standard GARCH(1,1) model:
    sigma_t^2 = omega + alpha * epsilon_{t-1}^2 + beta * sigma_{t-1}^2
    Stationarity constraint: alpha + beta < 1.0, omega > 0, alpha >= 0, beta >= 0.
    """
    def __init__(self, omega: float = 1e-6, alpha: float = 0.08, beta: float = 0.90):
        if alpha + beta >= 1.0:
            raise ValueError(f"Stationarity violated: alpha + beta ({alpha + beta}) must be < 1.0")
        self.omega = omega
        self.alpha = alpha
        self.beta = beta

    def fit_or_update(self, returns: np.ndarray) -> Tuple[float, float, float]:
        """
        Updates parameters via variance-targeting heuristics for robust offline execution.
        Unconditional variance: sigma_bar^2 = omega / (1 - alpha - beta).
        """
        if len(returns) < 30:
            return self.omega, self.alpha, self.beta

        # De-mean returns
        demeaned = returns - np.mean(returns)
        uncond_var = float(np.var(demeaned, ddof=1))
        if uncond_var <= 0:
            uncond_var = 1e-6

        # Variance targeting: fix alpha=0.08, beta=0.90, set omega = uncond_var * (1 - alpha - beta)
        alpha = 0.08
        beta = 0.90
        omega = uncond_var * (1.0 - alpha - beta)
        self.omega = max(1e-9, omega)
        self.alpha = alpha
        self.beta = beta
        return self.omega, self.alpha, self.beta

    def forecast_variance(self, returns: np.ndarray) -> float:
        """
        Iterates over historical squared returns to compute forward 1-step conditional variance.
        """
        if len(returns) < 5:
            return float(np.var(returns)) if len(returns) > 1 else 1e-4

        demeaned = returns - np.mean(returns)
        var_t = float(np.var(demeaned, ddof=1))
        for r in demeaned:
            var_t = self.omega + self.alpha * (r ** 2) + self.beta * var_t
        return max(1e-9, var_t)


class VolatilityEngine:
    def __init__(
        self,
        target_annualized_vol: float = 0.12,  # 12% target volatility
        ewma_decay: float = 0.94,             # RiskMetrics standard
        trading_days_per_year: int = 252
    ):
        self.target_annualized_vol = target_annualized_vol
        self.ewma_decay = ewma_decay
        self.trading_days = trading_days_per_year
        self.annualization_factor = math.sqrt(self.trading_days)
        self.garch = GarchModel()

    def compute_ewma_volatility(self, returns: np.ndarray) -> float:
        """Computes RiskMetrics EWMA daily volatility."""
        if len(returns) < 2:
            return 0.01

        weights = np.array([(1.0 - self.ewma_decay) * (self.ewma_decay ** i) for i in range(len(returns))])
        weights = weights[::-1]  # Oldest to newest
        weights /= np.sum(weights)

        # De-meaned
        demeaned = returns - np.mean(returns)
        variance = float(np.sum(weights * (demeaned ** 2)))
        return math.sqrt(max(1e-9, variance))

    def compute_har_rv_volatility(self, daily_returns: np.ndarray) -> float:
        """
        Computes HAR-RV (Heterogeneous Autoregressive Realized Volatility) proxy:
        Combines 1-day, 5-day (weekly), and 22-day (monthly) realized volatilities.
        """
        n = len(daily_returns)
        if n < 22:
            return float(np.std(daily_returns, ddof=1)) if n > 1 else 0.01

        rv_daily = abs(daily_returns[-1])
        rv_weekly = float(np.mean(np.abs(daily_returns[-5:])))
        rv_monthly = float(np.mean(np.abs(daily_returns[-22:])))

        # Standard HAR coefficients: beta_d=0.35, beta_w=0.40, beta_m=0.25
        har_est = 0.35 * rv_daily + 0.40 * rv_weekly + 0.25 * rv_monthly
        # Convert mean absolute deviation to standard deviation proxy (sqrt(pi/2) ~ 1.2533)
        return float(har_est * 1.2533)

    def forecast(
        self,
        symbol: str,
        closes: List[float],
        highs: Optional[List[float]] = None,
        lows: Optional[List[float]] = None,
        atr_value: Optional[float] = None
    ) -> VolatilityForecast:
        """
        Computes volatility forecast comparing GARCH, EWMA, HAR-RV, and ATR fallback.
        """
        if len(closes) < 10:
            # Fallback when insufficient bars
            fallback_daily = 0.01
            return VolatilityForecast(
                symbol=symbol,
                annualized_vol=fallback_daily * self.annualization_factor,
                daily_vol=fallback_daily,
                garch_vol=None,
                ewma_vol=fallback_daily,
                atr_vol_equivalent=fallback_daily * self.annualization_factor,
                method_used="ATR_FALLBACK",
                recommended_stop_scale=1.0,
                recommended_size_scale=1.0
            )

        prices = np.array(closes, dtype=np.float64)
        returns = np.diff(prices) / prices[:-1]

        # 1. EWMA
        ewma_daily = self.compute_ewma_volatility(returns)

        # 2. GARCH(1,1)
        try:
            self.garch.fit_or_update(returns)
            garch_var = self.garch.forecast_variance(returns)
            garch_daily = math.sqrt(garch_var)
        except Exception:
            garch_daily = ewma_daily

        # 3. HAR-RV
        har_daily = self.compute_har_rv_volatility(returns)

        # Choose forecast: blend GARCH and EWMA
        primary_daily = 0.5 * garch_daily + 0.5 * ewma_daily
        annualized_vol = primary_daily * self.annualization_factor

        # 4. ATR equivalent
        current_price = closes[-1] if closes[-1] > 0 else 1.0
        if atr_value is not None and atr_value > 0:
            atr_pct = atr_value / current_price
            atr_ann = atr_pct * self.annualization_factor
        else:
            atr_ann = annualized_vol

        # 5. Volatility targeting sizing multiplier
        # target_vol / realized_vol. If vol spikes to 24% when target is 12%, size cuts to 0.5x.
        # SAFETY INVARIANT: Size multiplier can NEVER exceed 1.0 (cannot increase risk above base).
        if annualized_vol > 1e-5:
            vol_target_scale = min(1.0, self.target_annualized_vol / annualized_vol)
        else:
            vol_target_scale = 1.0

        # Dynamic stop scale: if forecast is significantly above ATR, widen stops (>= 1.0)
        # to prevent getting whipped out in elevated volatility
        if atr_ann > 1e-5:
            stop_scale = max(1.0, min(1.5, annualized_vol / atr_ann))
        else:
            stop_scale = 1.0

        return VolatilityForecast(
            symbol=symbol,
            annualized_vol=round(float(annualized_vol), 4),
            daily_vol=round(float(primary_daily), 5),
            garch_vol=round(float(garch_daily * self.annualization_factor), 4),
            ewma_vol=round(float(ewma_daily * self.annualization_factor), 4),
            atr_vol_equivalent=round(float(atr_ann), 4),
            method_used="GARCH_EWMA_BLEND",
            recommended_stop_scale=round(float(stop_scale), 2),
            recommended_size_scale=round(float(vol_target_scale), 3)
        )
