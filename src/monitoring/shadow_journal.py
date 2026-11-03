"""
Paper Shadow Mode and Outcome Tracking Journal.
Logs EVERY setup (taken, rejected by BOS gate, rejected by replay gate,
rejected by checklist) and tracks hypothetical forward outcomes (SL/TP/time-exit)
to empirically validate whether filters improve out-of-sample edge.
"""

from __future__ import annotations
import os
import json
import logging
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Dict, List, Optional, Any

from src.core.contracts import TradePlan, Candle, OrderType

logger = logging.getLogger("ShadowJournal")


@dataclass
class ShadowEntry:
    setup_id: str
    symbol: str
    direction: str  # "BUY" or "SELL"
    entry_price: float
    stop_loss: float
    take_profit: float
    reward_to_risk: float
    decision: str  # "TAKEN", "REJECTED_BOS", "REJECTED_REPLAY", "REJECTED_CHECKLIST"
    rejection_reasons: List[str] = field(default_factory=list)
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    status: str = "PENDING"  # "PENDING", "HIT_TP", "HIT_SL", "TIME_EXIT"
    outcome_r: float = 0.0
    bars_tracked: int = 0
    max_tracking_bars: int = 40  # ~10 hours on M15

    def to_dict(self) -> Dict[str, Any]:
        return {
            "setup_id": self.setup_id,
            "symbol": self.symbol,
            "direction": self.direction,
            "entry_price": self.entry_price,
            "stop_loss": self.stop_loss,
            "take_profit": self.take_profit,
            "reward_to_risk": self.reward_to_risk,
            "decision": self.decision,
            "rejection_reasons": self.rejection_reasons,
            "timestamp": self.timestamp.isoformat(),
            "status": self.status,
            "outcome_r": round(self.outcome_r, 2),
            "bars_tracked": self.bars_tracked
        }


class ShadowJournal:
    """
    In-memory and file-backed paper shadow journal tracking all evaluated setups forward.
    """

    def __init__(self, journal_path: str = "reports/shadow_journal.json"):
        self.journal_path = journal_path
        self.entries: List[ShadowEntry] = []
        self._load_journal()

    def record_setup(
        self,
        setup_id: str,
        symbol: str,
        direction: OrderType,
        entry_price: float,
        stop_loss: float,
        take_profit: float,
        reward_to_risk: float,
        decision: str,
        rejection_reasons: Optional[List[str]] = None,
        timestamp: Optional[datetime] = None
    ) -> ShadowEntry:
        """Records a new setup candidate into the shadow journal."""
        d_str = direction.value if hasattr(direction, "value") else str(direction)
        entry = ShadowEntry(
            setup_id=setup_id,
            symbol=symbol,
            direction=d_str,
            entry_price=entry_price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            reward_to_risk=reward_to_risk,
            decision=decision,
            rejection_reasons=rejection_reasons or [],
            timestamp=timestamp or datetime.now(timezone.utc)
        )
        self.entries.append(entry)
        self._save_journal()
        logger.info(
            f"[SHADOW_JOURNAL] Recorded Setup {setup_id} | {symbol} {d_str} | "
            f"Decision: {decision} | Reasons: {rejection_reasons}"
        )
        return entry

    def update_candle(self, symbol: str, candle: Candle) -> None:
        """
        Advances hypothetical trade paths on incoming closed bar.
        Checks for SL, TP, or time-exit.
        """
        updated = False
        for entry in self.entries:
            if entry.symbol != symbol or entry.status != "PENDING":
                continue

            entry.bars_tracked += 1
            is_buy = "BUY" in entry.direction.upper()
            sl_dist = abs(entry.entry_price - entry.stop_loss)
            if sl_dist <= 0:
                sl_dist = 0.0001

            # Check SL hit
            hit_sl = (candle.low <= entry.stop_loss) if is_buy else (candle.high >= entry.stop_loss)
            # Check TP hit
            hit_tp = (candle.high >= entry.take_profit) if is_buy else (candle.low <= entry.take_profit)

            if hit_sl and hit_tp:
                # Ambiguous intrabar breach, conservative: stop first
                entry.status = "HIT_SL"
                entry.outcome_r = -1.0
                updated = True
            elif hit_sl:
                entry.status = "HIT_SL"
                entry.outcome_r = -1.0
                updated = True
            elif hit_tp:
                entry.status = "HIT_TP"
                entry.outcome_r = entry.reward_to_risk
                updated = True
            elif entry.bars_tracked >= entry.max_tracking_bars:
                # Time exit
                unrealized = (candle.close - entry.entry_price) if is_buy else (entry.entry_price - candle.close)
                entry.status = "TIME_EXIT"
                entry.outcome_r = round(unrealized / sl_dist, 2)
                updated = True

        if updated:
            self._save_journal()

    def generate_report(self) -> Dict[str, Any]:
        """
        Summarizes win rate and average R across TAKEN vs REJECTED categories.
        """
        categories = ["TAKEN", "REJECTED_BOS", "REJECTED_REPLAY", "REJECTED_CHECKLIST"]
        report: Dict[str, Any] = {
            "total_recorded": len(self.entries),
            "pending": len([e for e in self.entries if e.status == "PENDING"]),
            "completed": len([e for e in self.entries if e.status != "PENDING"]),
            "categories": {}
        }

        for cat in categories:
            cat_entries = [e for e in self.entries if e.decision == cat and e.status != "PENDING"]
            count = len(cat_entries)
            if count == 0:
                report["categories"][cat] = {
                    "count": 0, "wins": 0, "losses": 0,
                    "win_rate": 0.0, "avg_r": 0.0
                }
                continue

            wins = len([e for e in cat_entries if e.outcome_r > 0])
            losses = len([e for e in cat_entries if e.outcome_r < 0])
            wr = round((wins / count) * 100.0, 1)
            avg_r = round(sum(e.outcome_r for e in cat_entries) / count, 2)

            report["categories"][cat] = {
                "count": count,
                "wins": wins,
                "losses": losses,
                "win_rate": wr,
                "avg_r": avg_r
            }

        return report

    def format_telegram_report(self) -> str:
        """Formats the shadow comparison report for Telegram /shadow_report command."""
        rep = self.generate_report()
        cats = rep["categories"]

        def cat_line(title: str, key: str) -> str:
            d = cats.get(key, {})
            c = d.get("count", 0)
            if c == 0:
                return f"• <b>{title}:</b> None tracked yet"
            return f"• <b>{title}:</b> {c} setups | WR: <code>{d.get('win_rate', 0.0)}%</code> | Avg R: <code>{d.get('avg_r', 0.0):+.2f}R</code>"

        return (
            "📊 <b>PAPER SHADOW VALIDATION REPORT</b>\n\n"
            f"<b>Total Setups Tracked:</b> {rep['total_recorded']} (Pending: {rep['pending']}, Resolved: {rep['completed']})\n\n"
            f"{cat_line('✅ Executed (Taken)', 'TAKEN')}\n"
            f"{cat_line('🛡️ Filtered by BOS Gate', 'REJECTED_BOS')}\n"
            f"{cat_line('🎲 Filtered by Replay Gate', 'REJECTED_REPLAY')}\n"
            f"{cat_line('📋 Filtered by Checklist', 'REJECTED_CHECKLIST')}\n\n"
            "<i>(Empirical proof: Shows if filtered setups would have underperformed vs executed trades)</i>"
        )

    def _save_journal(self) -> None:
        try:
            os.makedirs(os.path.dirname(self.journal_path), exist_ok=True)
            with open(self.journal_path, "w", encoding="utf-8") as f:
                json.dump([e.to_dict() for e in self.entries], f, indent=2)
        except Exception as e:
            logger.warning(f"[ShadowJournal] Failed saving journal: {e}")

    def _load_journal(self) -> None:
        if not os.path.exists(self.journal_path):
            return
        try:
            with open(self.journal_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                self.entries = [
                    ShadowEntry(
                        setup_id=d["setup_id"],
                        symbol=d["symbol"],
                        direction=d["direction"],
                        entry_price=d["entry_price"],
                        stop_loss=d["stop_loss"],
                        take_profit=d["take_profit"],
                        reward_to_risk=d["reward_to_risk"],
                        decision=d["decision"],
                        rejection_reasons=d.get("rejection_reasons", []),
                        timestamp=datetime.fromisoformat(d["timestamp"]),
                        status=d.get("status", "PENDING"),
                        outcome_r=d.get("outcome_r", 0.0),
                        bars_tracked=d.get("bars_tracked", 0)
                    )
                    for d in data
                ]
        except Exception as e:
            logger.warning(f"[ShadowJournal] Failed loading journal: {e}")
