"""
Institutional Session Risk and Correlation Guard.
Enforces:
1. Per-session trade count ceiling (e.g. max 2 trades per session).
2. Per-session loss cap (% of balance) and consecutive loss circuit breaker.
3. Per-session risk multipliers (e.g. 0.5x in Asia, 1.0x in London/NY).
   CRITICAL: These can only tighten global risk limits, never loosen them.
4. Cross-pair session correlation exposure guard.
"""

from __future__ import annotations
import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple, Set

from src.core.contracts import SessionType, Position, OrderType, SignalCandidate, AccountInfo


@dataclass
class SessionRiskTracker:
    session: SessionType
    trades_executed: int = 0
    consecutive_losses: int = 0
    realized_loss_usd: float = 0.0
    start_balance: float = 10000.0


class SessionRiskEngine:
    def __init__(
        self,
        config_path: str = "config/risk_config.json",
        max_trades_per_session: int = 2,
        max_concurrent_session_positions: int = 2,
        max_consecutive_losses: int = 2,
        session_loss_cap_percent: float = 1.0
    ):
        self.config_path = config_path
        self.max_trades = max_trades_per_session
        self.max_concurrent = max_concurrent_session_positions
        self.max_consecutive_losses = max_consecutive_losses
        self.session_loss_cap_pct = session_loss_cap_percent
        
        self._current_tracker: Optional[SessionRiskTracker] = None
        self._last_session: Optional[SessionType] = None
        
        # Correlated pair clusters
        self._correlated_clusters = [
            {"EURUSD", "GBPUSD"},
            {"AUDUSD", "NZDUSD"},
            {"USDJPY", "EURJPY", "GBPJPY"},
            {"NAS100", "US30", "SPX500"}
        ]

    def _get_risk_multiplier(self, session: SessionType) -> float:
        """Returns risk scaling factor by session (cannot exceed 1.0x)."""
        if session == SessionType.ASIAN:
            return 0.50
        elif session in (SessionType.LONDON, SessionType.NEW_YORK, SessionType.BEST_WINDOW, SessionType.LONDON_NY_OVERLAP):
            return 1.00
        return 0.25

    def reset_or_update_session(self, active_session: SessionType, balance: float) -> None:
        """Resets tracker upon session transition."""
        if self._last_session != active_session:
            self._current_tracker = SessionRiskTracker(
                session=active_session,
                start_balance=balance if balance > 0 else 10000.0
            )
            self._last_session = active_session

    def evaluate_session_risk_permission(
        self,
        candidate: SignalCandidate,
        active_session: SessionType,
        account: AccountInfo,
        active_positions: List[Position]
    ) -> Tuple[bool, float, Optional[str]]:
        """
        Validates whether trade candidate complies with session risk budgets.
        Returns: (allowed: bool, adjusted_risk_multiplier: float, rejection_reason: Optional[str])
        """
        self.reset_or_update_session(active_session, account.balance)
        tracker = self._current_tracker

        risk_mult = self._get_risk_multiplier(active_session)

        # 1. Consecutive losses cap
        if tracker and tracker.consecutive_losses >= self.max_consecutive_losses:
            return False, 0.0, f"Session consecutive loss limit reached ({tracker.consecutive_losses} losses in {active_session.value})"

        # 2. Session loss budget
        if tracker and tracker.start_balance > 0:
            loss_pct = (tracker.realized_loss_usd / tracker.start_balance) * 100.0
            if loss_pct >= self.session_loss_cap_pct:
                return False, 0.0, f"Session loss cap exceeded ({loss_pct:.2f}% >= {self.session_loss_cap_pct:.2f}% in {active_session.value})"

        # 3. Trade count cap
        if tracker and tracker.trades_executed >= self.max_trades:
            return False, 0.0, f"Session trade cap reached ({tracker.trades_executed}/{self.max_trades} trades in {active_session.value})"

        # 4. Concurrent session positions cap
        if len(active_positions) >= self.max_concurrent:
            return False, 0.0, f"Max concurrent session positions limit reached ({len(active_positions)}/{self.max_concurrent})"

        # 5. Correlation guard (e.g. EURUSD + GBPUSD in same direction)
        clean_cand_sym = candidate.symbol.replace(".m", "").replace("m", "").upper()
        cand_dir = candidate.direction

        for pos in active_positions:
            clean_pos_sym = pos.symbol.replace(".m", "").replace("m", "").upper()
            if pos.order_type == cand_dir:
                for cluster in self._correlated_clusters:
                    if clean_cand_sym in cluster and clean_pos_sym in cluster and clean_cand_sym != clean_pos_sym:
                        return False, 0.0, f"Correlation guard: Already holding correlated {clean_pos_sym} in direction {cand_dir.value}"

        return True, risk_mult, None

    def record_trade_opened(self) -> None:
        if self._current_tracker:
            self._current_tracker.trades_executed += 1

    def record_trade_closed(self, pnl_usd: float) -> None:
        if self._current_tracker:
            if pnl_usd < 0:
                self._current_tracker.consecutive_losses += 1
                self._current_tracker.realized_loss_usd += abs(pnl_usd)
            else:
                self._current_tracker.consecutive_losses = 0
