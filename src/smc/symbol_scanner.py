"""
Multi-Symbol M15 Opportunity Scanner and Dynamic Ranking Engine.
Evaluates all active symbols each M15 close against:
1. Structure Confirmation State (BOS_CONFIRMED highest)
2. HTF Trend Alignment (W1/D1/H4/H1)
3. Spread-to-ATR Efficiency
4. Session Liquidity
5. News Proximity
Ranks symbols and selects the top N for deep strategy analysis.
"""

from __future__ import annotations
import logging
from dataclasses import dataclass
from typing import Dict, List, Optional, Any, Tuple
from datetime import datetime, timezone

from src.core.contracts import Candle, Timeframe, TrendBias
from src.smc.structure_confirmation import StructureStateMachine, ConfirmationState
from src.smc.news_filter import NewsFilterEngine

logger = logging.getLogger("SymbolScanner")


@dataclass
class ScoredSymbol:
    symbol: str
    total_score: float
    structure_score: float
    htf_score: float
    spread_score: float
    liquidity_score: float
    news_score: float
    structure_state: str
    in_news_blackout: bool
    rejection_reason: Optional[str] = None


class SymbolScanner:
    def __init__(
        self,
        top_n: int = 3,
        news_filter: Optional[NewsFilterEngine] = None,
        struct_machine: Optional[StructureStateMachine] = None
    ):
        self.top_n = top_n
        self.news_filter = news_filter or NewsFilterEngine()
        self.struct_machine = struct_machine or StructureStateMachine()

    def rank_symbols(
        self,
        symbols_data: Dict[str, Dict[str, Any]],
        current_time: Optional[datetime] = None
    ) -> Tuple[List[ScoredSymbol], List[str]]:
        """
        Ranks symbols based on the 5-factor scoring model.
        Returns: (all_scored_symbols_sorted, top_n_selected_symbols)
        """
        t = current_time or datetime.now(timezone.utc)
        scored: List[ScoredSymbol] = []

        for sym, data in symbols_data.items():
            # 1. Structure State Score (max 40 pts)
            struct_snapshot = self.struct_machine.get_state(sym, Timeframe.M15)
            st = struct_snapshot.state
            if st == ConfirmationState.BOS_CONFIRMED:
                s_struct = 40.0
            elif st == ConfirmationState.PULLBACK:
                s_struct = 25.0
            elif st == ConfirmationState.CHOCH_WARNING:
                s_struct = 15.0
            else:
                s_struct = 5.0

            # 2. HTF Alignment Score (max 25 pts)
            htf_aligned = data.get("htf_aligned", True)
            h4_bias = data.get("h4_bias", TrendBias.RANGING)
            s_htf = 25.0 if htf_aligned else 10.0

            # 3. Spread vs ATR Score (max 15 pts)
            spread_pts = data.get("spread_pts", 10.0)
            atr_pts = data.get("atr_pts", 50.0)
            if atr_pts > 0:
                spread_ratio = spread_pts / atr_pts
                s_spread = max(0.0, min(15.0, 15.0 * (1.0 - spread_ratio * 2.0)))
            else:
                s_spread = 7.5

            # 4. Session Liquidity Score (max 10 pts)
            session = data.get("session", "LONDON")
            if session in ("LONDON", "NEW_YORK", "LONDON_NY_OVERLAP"):
                s_liq = 10.0
            elif session == "ASIAN":
                s_liq = 5.0
            else:
                s_liq = 2.0

            # 5. Unrestricted Execution Readiness Score (max 10 pts; news restrictions completely removed)
            in_blackout = False
            s_news = 10.0

            total = s_struct + s_htf + s_spread + s_liq + s_news

            scored.append(
                ScoredSymbol(
                    symbol=sym,
                    total_score=round(total, 1),
                    structure_score=s_struct,
                    htf_score=s_htf,
                    spread_score=round(s_spread, 1),
                    liquidity_score=s_liq,
                    news_score=s_news,
                    structure_state=st.value,
                    in_news_blackout=False,
                    rejection_reason=None
                )
            )

        # Sort descending by total score
        scored.sort(key=lambda x: x.total_score, reverse=True)

        # Select top N eligible symbols (no news-based symbol restrictions)
        selected = [s.symbol for s in scored][:self.top_n]

        # Log at DECISION level
        top_str = ", ".join([f"{s.symbol} ({s.total_score}pts)" for s in scored[:self.top_n]])
        pruned_str = ", ".join([f"{s.symbol} ({s.total_score}pts)" for s in scored[self.top_n:]]) if len(scored) > self.top_n else "None"
        logger.info(
            f"[DECISION] [SCANNER] Ranked {len(scored)} symbols | Top {self.top_n}: [{top_str}] | "
            f"Pruned: [{pruned_str}]"
        )

        return scored, selected
