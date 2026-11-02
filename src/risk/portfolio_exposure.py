"""
Portfolio Exposure, Currency Netting, Correlation, and PCA Concentration Engine.
Enforces:
1. Micro/Mini Account Rule: strictly max 1 open position across entire account.
2. Net exposure limits per underlying currency/asset (USD, EUR, GBP, JPY, XAU, BTC).
3. Rolling correlation cross-pair gating (rejects high correlation redundancy > 0.70).
4. PCA concentration measure (eigenvalue dominance of first principal component > 0.80).
"""

from __future__ import annotations
import math
import re
from dataclasses import dataclass
from typing import List, Dict, Optional, Tuple, Set
import numpy as np

from src.core.contracts import Position, OrderType, SignalCandidate


@dataclass(frozen=True)
class CurrencyExposure:
    currency: str
    net_lots: float
    net_direction: str  # 'LONG', 'SHORT', 'NEUTRAL'


@dataclass(frozen=True)
class ExposureCheckResult:
    allowed: bool
    rejection_reason: Optional[str]
    net_exposures: Dict[str, float]
    pca_top_component_ratio: float
    max_pair_correlation: float


class PortfolioExposureEngine:
    def __init__(
        self,
        max_net_lots_per_currency: float = 5.0,
        max_pairwise_correlation: float = 0.70,
        pca_concentration_threshold: float = 0.80
    ):
        self.max_net_lots_per_currency = max_net_lots_per_currency
        self.max_pairwise_correlation = max_pairwise_correlation
        self.pca_threshold = pca_concentration_threshold

    @staticmethod
    def parse_currency_pair(symbol: str) -> Tuple[str, str]:
        """Extracts base and quote currency from symbol, ignoring .m or broker suffixes."""
        clean = re.sub(r'[^A-Z]', '', symbol.upper())
        if "BTC" in clean:
            return "BTC", clean.replace("BTC", "")
        if "XAU" in clean or "GOLD" in clean:
            return "XAU", "USD"
        if len(clean) >= 6:
            return clean[:3], clean[3:6]
        return clean, "USD"

    def check_open_position_limits(
        self,
        tier_name: str,
        current_open_positions: List[Position]
    ) -> Tuple[bool, Optional[str]]:
        """
        Stage 0A / Stage 4 Rule: MICRO and MINI tiers allow strictly MAX 1 open position.
        """
        tier_upper = tier_name.upper()
        if tier_upper in ("MICRO", "MINI", "BELOW_FLOOR"):
            if len(current_open_positions) >= 1:
                return False, f"Tier {tier_upper} strictly allows max 1 open position (currently {len(current_open_positions)} open)."
        return True, None

    def calculate_net_currency_exposures(
        self,
        positions: List[Position]
    ) -> Dict[str, float]:
        """Calculates net lots per underlying currency (+ for long base / short quote)."""
        net_lots: Dict[str, float] = {}

        for pos in positions:
            base, quote = self.parse_currency_pair(pos.symbol)
            is_long = pos.order_type in (OrderType.BUY, OrderType.BUY_LIMIT, OrderType.BUY_STOP)
            sign = 1.0 if is_long else -1.0
            vol = pos.volume

            net_lots[base] = net_lots.get(base, 0.0) + (sign * vol)
            net_lots[quote] = net_lots.get(quote, 0.0) - (sign * vol)

        return {curr: round(lots, 4) for curr, lots in net_lots.items()}

    def check_candidate_exposure(
        self,
        candidate_symbol: str,
        candidate_direction: OrderType,
        candidate_volume: float,
        current_positions: List[Position],
        returns_history: Optional[Dict[str, List[float]]] = None,
        tier_name: str = "STANDARD"
    ) -> ExposureCheckResult:
        """
        Runs complete portfolio exposure screening:
        1. Single position check for MICRO/MINI
        2. Net currency exposure limits
        3. Rolling correlation gating against existing positions
        4. PCA concentration calculation
        """
        # 1. Tier position count gate
        tier_ok, tier_reason = self.check_open_position_limits(tier_name, current_positions)
        if not tier_ok:
            return ExposureCheckResult(
                allowed=False,
                rejection_reason=tier_reason,
                net_exposures=self.calculate_net_currency_exposures(current_positions),
                pca_top_component_ratio=0.0,
                max_pair_correlation=0.0
            )

        # 2. Simulate new position
        base, quote = self.parse_currency_pair(candidate_symbol)
        is_long = candidate_direction in (OrderType.BUY, OrderType.BUY_LIMIT, OrderType.BUY_STOP)
        sign = 1.0 if is_long else -1.0

        current_net = self.calculate_net_currency_exposures(current_positions)
        simulated_net = dict(current_net)
        simulated_net[base] = simulated_net.get(base, 0.0) + (sign * candidate_volume)
        simulated_net[quote] = simulated_net.get(quote, 0.0) - (sign * candidate_volume)

        # Check currency limit breaches
        for curr, exposure in simulated_net.items():
            if abs(exposure) > self.max_net_lots_per_currency:
                return ExposureCheckResult(
                    allowed=False,
                    rejection_reason=(
                        f"Net currency exposure breach on {curr}: {abs(exposure):.2f} lots "
                        f"exceeds cap of {self.max_net_lots_per_currency:.2f} lots."
                    ),
                    net_exposures=simulated_net,
                    pca_top_component_ratio=0.0,
                    max_pair_correlation=0.0
                )

        # 3. Rolling correlation check
        max_corr = 0.0
        if returns_history and len(current_positions) > 0:
            cand_rets = returns_history.get(candidate_symbol)
            if cand_rets and len(cand_rets) >= 20:
                cand_arr = np.array(cand_rets[-30:], dtype=np.float64)
                for pos in current_positions:
                    pos_rets = returns_history.get(pos.symbol)
                    if pos_rets and len(pos_rets) >= 20:
                        pos_arr = np.array(pos_rets[-30:], dtype=np.float64)
                        corr_mat = np.corrcoef(cand_arr, pos_arr)
                        corr = float(corr_mat[0, 1]) if not np.isnan(corr_mat[0, 1]) else 0.0
                        if abs(corr) > abs(max_corr):
                            max_corr = corr

                        # If same direction and correlation > max_pairwise_correlation: redundant risk!
                        pos_is_long = pos.order_type in (OrderType.BUY, OrderType.BUY_LIMIT, OrderType.BUY_STOP)
                        same_direction = (is_long == pos_is_long)

                        if same_direction and corr > self.max_pairwise_correlation:
                            return ExposureCheckResult(
                                allowed=False,
                                rejection_reason=(
                                    f"Excessive positive correlation ({corr:.2f} > {self.max_pairwise_correlation:.2f}) "
                                    f"between candidate {candidate_symbol} and open position {pos.symbol} in same direction."
                                ),
                                net_exposures=simulated_net,
                                pca_top_component_ratio=0.0,
                                max_pair_correlation=max_corr
                            )

        # 4. PCA concentration calculation
        pca_ratio = self.calculate_pca_concentration(returns_history, [p.symbol for p in current_positions] + [candidate_symbol])
        if pca_ratio > self.pca_threshold and len(current_positions) >= 2:
            return ExposureCheckResult(
                allowed=False,
                rejection_reason=(
                    f"Portfolio concentration too high: PC1 explains {pca_ratio:.1%} "
                    f"of return variance (threshold: {self.pca_threshold:.1%})."
                ),
                net_exposures=simulated_net,
                pca_top_component_ratio=pca_ratio,
                max_pair_correlation=max_corr
            )

        return ExposureCheckResult(
            allowed=True,
            rejection_reason=None,
            net_exposures=simulated_net,
            pca_top_component_ratio=pca_ratio,
            max_pair_correlation=max_corr
        )

    def calculate_pca_concentration(
        self,
        returns_history: Optional[Dict[str, List[float]]],
        symbols: List[str]
    ) -> float:
        """
        Computes the ratio of variance explained by the 1st principal component (PC1).
        lambda_1 / sum(lambda).
        """
        unique_symbols = list(set(symbols))
        if not returns_history or len(unique_symbols) < 2:
            return 1.0  # Single asset trivially has 100% variance in 1 component

        series_list = []
        min_len = 9999
        for sym in unique_symbols:
            rets = returns_history.get(sym)
            if not rets or len(rets) < 20:
                return 0.5  # Neutral default if missing data
            min_len = min(min_len, len(rets))

        for sym in unique_symbols:
            series_list.append(returns_history[sym][-min_len:])

        arr = np.array(series_list, dtype=np.float64)
        corr_matrix = np.corrcoef(arr)
        if np.isnan(corr_matrix).any():
            return 0.5

        eigenvalues = np.linalg.eigvalsh(corr_matrix)
        eigenvalues = np.sort(eigenvalues)[::-1]  # Descending
        total_var = float(np.sum(eigenvalues))
        if total_var <= 0:
            return 1.0
        return float(eigenvalues[0] / total_var)
