"""
Transaction Cost Analysis (TCA) and Per-Order Execution Audit Engine.
Logs:
- Intended vs filled price
- Slippage in broker points and USD
- Spread at signal vs spread at fill
- Latency (Signal -> OrderSend -> MT5 Confirmation)
- Retcode and requote count
- Total transaction friction drag
"""

from __future__ import annotations
import json
import logging
import os
import time
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from typing import List, Dict, Optional, Any
import numpy as np

logger = logging.getLogger("TCA")


@dataclass(frozen=True)
class OrderExecutionAudit:
    request_id: str
    symbol: str
    order_type: str
    volume: float
    intended_price: float
    filled_price: float
    slippage_points: float
    slippage_usd: float
    spread_at_signal_pts: float
    spread_at_fill_pts: float
    latency_send_to_confirm_ms: float
    retcode: int
    requotes: int
    commission_paid: float
    timestamp_utc: str
    execution_drag_usd: float  # Slippage + commission + spread cost


class TCAEngine:
    def __init__(self, log_path: str = "reports/raw/tca_records.jsonl", enabled: bool = False):
        self.log_path = log_path
        self.enabled = enabled
        self.records: List[OrderExecutionAudit] = []
        os.makedirs(os.path.dirname(self.log_path), exist_ok=True)

    def record_execution(
        self,
        request_id: str,
        symbol: str,
        order_type: str,
        volume: float,
        intended_price: float,
        filled_price: float,
        spread_at_signal_pts: float,
        spread_at_fill_pts: float,
        latency_ms: float,
        retcode: int,
        point: float = 0.00001,
        tick_value: float = 1.0,
        commission: float = 0.0,
        requotes: int = 0
    ) -> OrderExecutionAudit:
        """
        Computes slippage and friction metrics and appends to TCA audit trail.
        """
        # Slippage calculation
        # For BUY: filled > intended is adverse (+ slippage in points)
        # For SELL: filled < intended is adverse
        if "BUY" in order_type:
            slippage_pts = (filled_price - intended_price) / point if point > 0 else 0.0
        else:
            slippage_pts = (intended_price - filled_price) / point if point > 0 else 0.0

        # Slippage in USD = slippage_pts * tick_value * (volume / volume_step or volume)
        slippage_usd = slippage_pts * tick_value * (volume / 1.0)
        spread_cost_usd = spread_at_fill_pts * tick_value * volume
        total_drag = max(0.0, slippage_usd) + commission + spread_cost_usd

        audit = OrderExecutionAudit(
            request_id=request_id,
            symbol=symbol,
            order_type=order_type,
            volume=round(volume, 4),
            intended_price=round(intended_price, 5),
            filled_price=round(filled_price, 5),
            slippage_points=round(slippage_pts, 2),
            slippage_usd=round(slippage_usd, 2),
            spread_at_signal_pts=round(spread_at_signal_pts, 1),
            spread_at_fill_pts=round(spread_at_fill_pts, 1),
            latency_send_to_confirm_ms=round(latency_ms, 2),
            retcode=retcode,
            requotes=requotes,
            commission_paid=round(commission, 2),
            timestamp_utc=datetime.now(timezone.utc).isoformat(),
            execution_drag_usd=round(total_drag, 2)
        )

        self.records.append(audit)

        if self.enabled:
            logger.info(
                f"[TCA] {symbol} {order_type} {volume:.2f} lots | "
                f"Slippage: {slippage_pts:+.1f} pts (${slippage_usd:+.2f}) | "
                f"Spread: {spread_at_fill_pts:.1f} pts | Latency: {latency_ms:.1f}ms | Drag: ${total_drag:.2f}"
            )
            try:
                with open(self.log_path, "a", encoding="utf-8") as f:
                    f.write(json.dumps(asdict(audit)) + "\n")
            except Exception as e:
                logger.error(f"Failed to persist TCA record: {e}")

        return audit

    def generate_summary(self) -> Dict[str, Any]:
        """Calculates aggregated execution statistics."""
        if not self.records:
            return {
                "total_trades": 0,
                "mean_slippage_points": 0.0,
                "total_slippage_usd": 0.0,
                "mean_spread_pts": 0.0,
                "mean_latency_ms": 0.0,
                "p95_latency_ms": 0.0,
                "total_execution_drag_usd": 0.0
            }

        slips_pts = [r.slippage_points for r in self.records]
        slips_usd = [r.slippage_usd for r in self.records]
        spreads = [r.spread_at_fill_pts for r in self.records]
        latencies = [r.latency_send_to_confirm_ms for r in self.records]
        drags = [r.execution_drag_usd for r in self.records]

        return {
            "total_trades": len(self.records),
            "mean_slippage_points": round(float(np.mean(slips_pts)), 2),
            "total_slippage_usd": round(float(np.sum(slips_usd)), 2),
            "mean_spread_pts": round(float(np.mean(spreads)), 1),
            "mean_latency_ms": round(float(np.mean(latencies)), 1),
            "p95_latency_ms": round(float(np.percentile(latencies, 95)), 1),
            "total_execution_drag_usd": round(float(np.sum(drags)), 2)
        }
