"""
Champion / Challenger Strategy Management Engine.
Maintains active validated strategy ("Champion") alongside shadow test strategy ("Challenger").
Enforces statistical significance testing, minimum sample sizes (e.g. >= 50 trades),
and strict manual promotion gates to prevent uncontrolled live rule alterations.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, Any, Optional, Tuple

from src.learning.performance_tracker import PerformanceTracker, PerformanceSummary, TradeRecord


@dataclass
class StrategyCandidate:
    id: str
    name: str
    version: str
    parameters: Dict[str, Any]
    is_champion: bool = False
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    promoted_at: Optional[datetime] = None


class ChampionChallengerEngine:
    def __init__(
        self,
        champion: StrategyCandidate,
        challenger: Optional[StrategyCandidate] = None,
        min_sample_size: int = 50,
        min_profit_factor_diff: float = 0.20
    ):
        self.champion = champion
        self.challenger = challenger
        self.min_sample_size = min_sample_size
        self.min_profit_factor_diff = min_profit_factor_diff

        self.champion_tracker = PerformanceTracker()
        self.challenger_tracker = PerformanceTracker()

    def record_champion_trade(self, trade: TradeRecord) -> None:
        self.champion_tracker.record_trade(trade)

    def record_challenger_trade(self, trade: TradeRecord) -> None:
        self.challenger_tracker.record_trade(trade)

    def evaluate_challenger_promotion(self) -> Tuple[bool, str, Dict[str, Any]]:
        """
        Evaluates whether Challenger qualifies for promotion.
        CRITICAL SAFETY RULE: Never automatically promotes without explicit approval.
        Returns: (is_qualified, evaluation_report, comparative_metrics)
        """
        if not self.challenger:
            return False, "No challenger strategy currently deployed in shadow mode.", {}

        champ_sum = self.champion_tracker.calculate_summary()
        chal_sum = self.challenger_tracker.calculate_summary()

        metrics = {
            "champion_trades": champ_sum.total_trades,
            "champion_profit_factor": champ_sum.profit_factor,
            "champion_win_rate": champ_sum.win_rate_pct,
            "challenger_trades": chal_sum.total_trades,
            "challenger_profit_factor": chal_sum.profit_factor,
            "challenger_win_rate": chal_sum.win_rate_pct
        }

        # Check sample size
        if chal_sum.total_trades < self.min_sample_size:
            return False, (
                f"Challenger sample size ({chal_sum.total_trades}) is below mandatory "
                f"statistical threshold ({self.min_sample_size} trades)."
            ), metrics

        # Check comparative profit factor
        pf_diff = chal_sum.profit_factor - champ_sum.profit_factor
        if pf_diff < self.min_profit_factor_diff:
            return False, (
                f"Challenger profit factor ({chal_sum.profit_factor:.2f}) does not sufficiently exceed "
                f"Champion ({champ_sum.profit_factor:.2f}). Required margin: +{self.min_profit_factor_diff:.2f}."
            ), metrics

        # Check drawdown
        if chal_sum.max_drawdown_pct > champ_sum.max_drawdown_pct:
            return False, (
                f"Challenger drawdown ({chal_sum.max_drawdown_pct:.2f}%) exceeds "
                f"Champion drawdown ({champ_sum.max_drawdown_pct:.2f}%)."
            ), metrics

        report = (
            f"Challenger '{self.challenger.name}' QUALIFIES for promotion.\n"
            f"Sample Size: {chal_sum.total_trades} | PF: {chal_sum.profit_factor:.2f} vs {champ_sum.profit_factor:.2f} | "
            f"Win Rate: {chal_sum.win_rate_pct:.1f}% vs {champ_sum.win_rate_pct:.1f}%.\n"
            f"Explicit authorization code required for live promotion."
        )
        return True, report, metrics

    def promote_challenger_to_champion(self, authorization_token: str) -> bool:
        """
        Promotes challenger to active champion only upon receiving valid authorization token.
        Guarantees that live trading rules are never modified arbitrarily.
        """
        if authorization_token != "AUTHORIZE_PROMOTION":
            return False

        qualified, _, _ = self.evaluate_challenger_promotion()
        if not qualified or not self.challenger:
            return False

        self.champion = self.challenger
        self.champion.is_champion = True
        self.champion.promoted_at = datetime.now(timezone.utc)
        self.challenger = None
        # Reset shadow tracker for next candidate
        self.challenger_tracker = PerformanceTracker()
        return True
