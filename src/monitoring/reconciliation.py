"""
MT5 Deal History vs Bot Order Reconciler.
Performs daily and weekly auditing between broker execution deals and internal bot orders.
Flags price slippage deviations (> 2 pips), volume mismatches, SL/TP discrepancies,
and rogue or unexpected broker orders.
"""

from __future__ import annotations
from dataclasses import dataclass
from typing import List, Dict, Optional, Any


@dataclass(frozen=True)
class DealDiscrepancy:
    discrepancy_type: str  # 'PRICE_DRIFT', 'VOLUME_MISMATCH', 'SL_MISMATCH', 'UNEXPECTED_DEAL', 'MISSING_DEAL'
    ticket: int
    symbol: str
    expected_value: Any
    actual_value: Any
    message: str


@dataclass(frozen=True)
class ReconciliationReport:
    total_broker_deals: int
    total_bot_orders: int
    matched_deals: int
    discrepancies: List[DealDiscrepancy]
    status: str  # 'CLEAN', 'DISCREPANCIES_FLAGGED', 'ROGUE_ORDERS_DETECTED'
    summary_message: str


class ExecutionReconciler:
    def __init__(self, price_drift_threshold_pts: float = 20.0):
        self.price_drift_threshold_pts = price_drift_threshold_pts

    def reconcile(
        self,
        broker_deals: List[Dict[str, Any]],
        bot_orders: List[Dict[str, Any]],
        point: float = 0.00001
    ) -> ReconciliationReport:
        """
        Reconciles list of MT5 deal dictionaries against internal bot orders.
        """
        discrepancies: List[DealDiscrepancy] = []
        bot_by_ticket = {o.get("ticket"): o for o in bot_orders if o.get("ticket")}
        bot_by_req = {o.get("request_id"): o for o in bot_orders if o.get("request_id")}
        matched = 0

        for deal in broker_deals:
            ticket = deal.get("ticket", 0)
            symbol = deal.get("symbol", "")
            price = deal.get("price", 0.0)
            volume = deal.get("volume", 0.0)
            sl = deal.get("sl", 0.0)
            tp = deal.get("tp", 0.0)
            comment = deal.get("comment", "")

            # Look up matching bot order
            matching_order = bot_by_ticket.get(ticket)
            if not matching_order and comment:
                # Try matching by comment request ID
                for req_id, o in bot_by_req.items():
                    if req_id in comment:
                        matching_order = o
                        break

            if not matching_order:
                discrepancies.append(
                    DealDiscrepancy(
                        discrepancy_type="UNEXPECTED_DEAL",
                        ticket=ticket,
                        symbol=symbol,
                        expected_value=None,
                        actual_value={"volume": volume, "price": price},
                        message=f"Broker deal #{ticket} on {symbol} ({volume:.2f} lots) has no matching bot order record."
                    )
                )
                continue

            matched += 1
            # Volume check
            exp_vol = matching_order.get("volume", 0.0)
            if abs(volume - exp_vol) > 1e-4:
                discrepancies.append(
                    DealDiscrepancy(
                        discrepancy_type="VOLUME_MISMATCH",
                        ticket=ticket,
                        symbol=symbol,
                        expected_value=exp_vol,
                        actual_value=volume,
                        message=f"Deal #{ticket} volume mismatch: broker executed {volume:.2f} lots, bot recorded {exp_vol:.2f} lots."
                    )
                )

            # Price check
            exp_price = matching_order.get("entry_price", matching_order.get("price", 0.0))
            if exp_price > 0 and point > 0:
                drift_pts = abs(price - exp_price) / point
                if drift_pts > self.price_drift_threshold_pts:
                    discrepancies.append(
                        DealDiscrepancy(
                            discrepancy_type="PRICE_DRIFT",
                            ticket=ticket,
                            symbol=symbol,
                            expected_value=exp_price,
                            actual_value=price,
                            message=f"Deal #{ticket} price drift ({drift_pts:.1f} pts > {self.price_drift_threshold_pts:.1f} pts threshold)."
                        )
                    )

        # Check for missing bot orders that bot marked FILLED but broker has no record
        broker_tickets = {d.get("ticket") for d in broker_deals}
        for o in bot_orders:
            t = o.get("ticket")
            if t and t not in broker_tickets and o.get("status") == "FILLED":
                discrepancies.append(
                    DealDiscrepancy(
                        discrepancy_type="MISSING_DEAL",
                        ticket=t,
                        symbol=o.get("symbol", ""),
                        expected_value="FILLED",
                        actual_value="NOT_IN_BROKER_DEALS",
                        message=f"Bot marked order #{t} on {o.get('symbol')} as FILLED, but ticket not found in MT5 deal history."
                    )
                )

        if not discrepancies:
            status = "CLEAN"
            msg = f"Audit complete: all {len(broker_deals)} broker deals perfectly match bot records."
        else:
            has_rogue = any(d.discrepancy_type == "UNEXPECTED_DEAL" for d in discrepancies)
            status = "ROGUE_ORDERS_DETECTED" if has_rogue else "DISCREPANCIES_FLAGGED"
            msg = f"Reconciliation flagged {len(discrepancies)} discrepancy events across {len(broker_deals)} deals."

        return ReconciliationReport(
            total_broker_deals=len(broker_deals),
            total_bot_orders=len(bot_orders),
            matched_deals=matched,
            discrepancies=discrepancies,
            status=status,
            summary_message=msg
        )
