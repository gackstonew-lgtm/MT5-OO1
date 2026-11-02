"""
Symbol Affordability Scanner.
Classifies configured symbols as TRADABLE, MARGINAL, or BLOCKED based on live capital,
ATR-based structural stops, required margin, and cost-to-reward ratios.
Ranks tradable symbols by lowest transaction cost and smallest capital footprint.
"""

from __future__ import annotations
import os
import json
import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple, Any

from src.core.contracts import AccountInfo, SymbolInfo, AffordabilityStatus
from src.risk.account_tier import AccountTierManager, TierDefinition
from src.risk.position_sizing import PositionSizingEngine

logger = logging.getLogger("AffordabilityScanner")


@dataclass
class SymbolAffordabilityItem:
    symbol: str
    status: AffordabilityStatus
    atr_points: float
    min_lot: float
    min_lot_risk_money: float
    min_lot_risk_pct: float
    margin_required: float
    cost_to_reward_ratio: float
    raw_lots: float
    min_balance_needed: float
    rejection_reason: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "symbol": self.symbol,
            "status": self.status.value,
            "atr_points": self.atr_points,
            "min_lot": self.min_lot,
            "min_lot_risk_money": round(self.min_lot_risk_money, 2),
            "min_lot_risk_pct": round(self.min_lot_risk_pct, 2),
            "margin_required": round(self.margin_required, 2),
            "cost_to_reward_ratio": round(self.cost_to_reward_ratio, 3),
            "raw_lots": round(self.raw_lots, 4),
            "min_balance_needed": round(self.min_balance_needed, 2),
            "rejection_reason": self.rejection_reason,
        }


@dataclass
class AffordabilityScanResult:
    capital_base: float
    tier_name: str
    target_risk_pct: float
    ceiling_risk_pct: float
    items: List[SymbolAffordabilityItem] = field(default_factory=list)
    tradable_symbols: List[str] = field(default_factory=list)
    marginal_symbols: List[str] = field(default_factory=list)
    blocked_symbols: List[str] = field(default_factory=list)
    recommendation: str = ""

    def format_summary(self) -> str:
        lines = [
            f"📊 <b>SYMBOL AFFORDABILITY & CAPITAL FOOTPRINT</b> (Tier: <code>{self.tier_name}</code>, Cap: <code>${self.capital_base:.2f}</code>)",
            f"🎯 Target Risk: <code>{self.target_risk_pct:.2f}%</code> | Ceiling: <code>{self.ceiling_risk_pct:.2f}%</code>",
            "",
        ]
        if self.tradable_symbols:
            lines.append(f"✅ <b>TRADABLE:</b> {', '.join([f'<code>{s}</code>' for s in self.tradable_symbols])}")
        else:
            lines.append("⚠️ <b>TRADABLE:</b> None at standard target risk.")

        if self.marginal_symbols:
            lines.append(f"⚠️ <b>MARGINAL (Min-Lot Only):</b> {', '.join([f'<code>{s}</code>' for s in self.marginal_symbols])}")

        if self.blocked_symbols:
            lines.append(f"⛔ <b>BLOCKED:</b> {', '.join([f'<code>{s}</code>' for s in self.blocked_symbols])}")

        lines.append("")
        for it in self.items:
            status_icon = "✅" if it.status == AffordabilityStatus.TRADABLE else ("⚠️" if it.status == AffordabilityStatus.MARGINAL else "⛔")
            lines.append(
                f"{status_icon} <b>{it.symbol}</b> [{it.status.value}]: MinLot <code>{it.min_lot:.2f}</code> risks "
                f"<code>${it.min_lot_risk_money:.2f}</code> (<code>{it.min_lot_risk_pct:.2f}%</code>) | Margin: <code>${it.margin_required:.2f}</code>"
            )
            if it.status != AffordabilityStatus.TRADABLE and it.rejection_reason:
                lines.append(f"   ↳ <i>Reason:</i> {it.rejection_reason} (MinBal: <code>${it.min_balance_needed:.2f}</code>)")

        if self.recommendation:
            lines.append(f"\n💡 <b>Recommendation:</b> {self.recommendation}")

        return "\n".join(lines)


class AffordabilityScanner:
    def __init__(
        self,
        tier_manager: Optional[AccountTierManager] = None,
        sizing_engine: Optional[PositionSizingEngine] = None
    ):
        self.tier_manager = tier_manager or AccountTierManager()
        self.sizing_engine = sizing_engine or PositionSizingEngine(tier_manager=self.tier_manager)

    def scan(
        self,
        account: AccountInfo,
        symbol_specs: Dict[str, SymbolInfo],
        atr_estimates: Optional[Dict[str, float]] = None
    ) -> AffordabilityScanResult:
        """
        Scans all configured symbols against live account facts.
        Uses ATR-based stop estimates to compute real minimum lot risk.
        """
        capital = min(account.balance, account.equity)
        tier_def, _, _ = self.tier_manager.evaluate_account(account)

        # Default ATR estimates in points if not supplied
        default_atrs = {
            "BTCUSD.m": 25000.0,
            "EURUSD": 350.0,
            "EURUSD.m": 350.0,
            "GBPUSD": 450.0,
            "GBPUSD.m": 450.0,
            "USDJPY": 400.0,
            "USDJPY.m": 400.0,
            "XAUUSD": 2800.0,
            "XAUUSD.m": 2800.0,
        }

        atrs = atr_estimates or {}
        items: List[SymbolAffordabilityItem] = []
        tradable: List[str] = []
        marginal: List[str] = []
        blocked: List[str] = []

        for sym, spec in symbol_specs.items():
            atr_pts = atrs.get(sym, default_atrs.get(sym, 500.0))
            point = spec.point if spec.point > 0 else 0.00001
            tick_sz = spec.tick_size if spec.tick_size > 0 else point
            tick_val = spec.tick_value if spec.tick_value > 0 else 1.0

            # Structural stop distance = 1.5 * ATR
            sl_distance_pts = atr_pts * 1.5
            sl_distance_price = sl_distance_pts * point
            min_lot = spec.min_lot if spec.min_lot > 0 else 0.01

            # Loss per lot calculation
            num_ticks = sl_distance_pts * point / tick_sz
            gross_loss_per_lot = num_ticks * tick_val
            spread_pts = spec.spread if spec.spread > 0 else 1.0
            spread_cost_per_lot = (spread_pts * point / tick_sz) * tick_val
            commission_per_lot = 6.0
            total_loss_per_lot = gross_loss_per_lot + spread_cost_per_lot + commission_per_lot

            # Min lot risk
            min_lot_risk_money = min_lot * total_loss_per_lot
            min_lot_risk_pct = (min_lot_risk_money / capital) * 100.0 if capital > 0 else 999.0

            # Target risk money and raw lots
            target_risk_money = capital * (tier_def.target_risk_pct / 100.0) if capital > 0 else 0.0
            raw_lots = (target_risk_money / total_loss_per_lot) if total_loss_per_lot > 0 else 0.0
            min_bal_needed = min_lot_risk_money / (tier_def.target_risk_pct / 100.0) if tier_def.target_risk_pct > 0 else 9999.0

            # Margin requirement
            req_margin = min_lot * (spec.margin_required if spec.margin_required > 0 else 1000.0)
            max_free_margin = account.free_margin * (tier_def.max_margin_percent_of_free / 100.0)

            # Cost to reward ratio (assuming 2.5R target)
            tp_ticks = (sl_distance_pts * 2.5) * point / tick_sz
            gross_tp = min_lot * tp_ticks * tick_val
            total_costs = min_lot * (spread_cost_per_lot + commission_per_lot)
            cost_to_reward = (total_costs / gross_tp) if gross_tp > 0 else 1.0

            # Classification
            reason = None
            if capital < 5.0:
                status = AffordabilityStatus.BLOCKED
                reason = "Capital below $5.00 floor."
            elif req_margin > max_free_margin and max_free_margin > 0:
                status = AffordabilityStatus.BLOCKED
                reason = f"Margin ${req_margin:.2f} exceeds {tier_def.max_margin_percent_of_free:.0f}% of free margin (${max_free_margin:.2f})."
            elif raw_lots >= min_lot:
                status = AffordabilityStatus.TRADABLE
            elif min_lot_risk_pct <= tier_def.absolute_ceiling_pct:
                status = AffordabilityStatus.MARGINAL
                reason = f"Raw lot {raw_lots:.4f} < min lot {min_lot:.2f}. Tradable only under micro-account flags."
            else:
                status = AffordabilityStatus.BLOCKED
                reason = f"Min lot risks {min_lot_risk_pct:.2f}% > tier ceiling {tier_def.absolute_ceiling_pct:.2f}%."

            item = SymbolAffordabilityItem(
                symbol=sym,
                status=status,
                atr_points=round(atr_pts, 1),
                min_lot=min_lot,
                min_lot_risk_money=min_lot_risk_money,
                min_lot_risk_pct=min_lot_risk_pct,
                margin_required=req_margin,
                cost_to_reward_ratio=cost_to_reward,
                raw_lots=raw_lots,
                min_balance_needed=min_bal_needed,
                rejection_reason=reason
            )
            items.append(item)

            if status == AffordabilityStatus.TRADABLE:
                tradable.append(sym)
            elif status == AffordabilityStatus.MARGINAL:
                marginal.append(sym)
            else:
                blocked.append(sym)

        # Rank tradable symbols by lowest transaction cost and smallest risk money
        items_tradable = [it for it in items if it.status == AffordabilityStatus.TRADABLE]
        items_tradable.sort(key=lambda x: (x.cost_to_reward_ratio, x.min_lot_risk_money))
        tradable = [it.symbol for it in items_tradable]

        # Recommendation synthesis
        rec = ""
        if capital < 5.0:
            rec = "Deposit funds to reach the $5.00 minimum operating floor."
        elif not tradable and not marginal:
            rec = (
                f"No instruments tradable on standard units at ${capital:.2f}. "
                "Recommend opening a CENT account (e.g. 500 USC = $5.00) or enabling micro_account_min_lot_allowed."
            )
        elif not tradable and marginal:
            cheapest = min(marginal, key=lambda s: next(it.min_lot_risk_money for it in items if it.symbol == s))
            rec = (
                f"Instruments are only tradable via micro-account flags. Cheapest: {cheapest}. "
                "Or switch to a CENT account for institutional 1:1 risk fidelity."
            )
        else:
            rec = f"Optimal instruments ranked by lowest execution drag: {', '.join(tradable[:3])}."

        return AffordabilityScanResult(
            capital_base=capital,
            tier_name=tier_def.name,
            target_risk_pct=tier_def.target_risk_pct,
            ceiling_risk_pct=tier_def.absolute_ceiling_pct,
            items=items,
            tradable_symbols=tradable,
            marginal_symbols=marginal,
            blocked_symbols=blocked,
            recommendation=rec
        )
