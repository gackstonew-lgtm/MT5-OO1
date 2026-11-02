"""
Weekly Execution Cost Reporting Engine.
Formats comprehensive TCA summaries for Telegram and Markdown reporting.
"""

from __future__ import annotations
from typing import Dict, Any, List

from src.execution.analytics.tca import TCAEngine, OrderExecutionAudit


class TCAReporter:
    def __init__(self, tca_engine: TCAEngine):
        self.engine = tca_engine

    def format_telegram_summary(self) -> str:
        """Generates compact Telegram-friendly execution quality summary."""
        stats = self.engine.generate_summary()
        if stats["total_trades"] == 0:
            return "📊 <b>Weekly Execution Quality (TCA)</b>\nNo executions recorded this period."

        msg = (
            f"📊 <b>Weekly Execution Quality (TCA)</b>\n"
            f"• Executions: {stats['total_trades']}\n"
            f"• Avg Slippage: {stats['mean_slippage_points']:+.1f} pts (${stats['total_slippage_usd']:+.2f})\n"
            f"• Avg Spread: {stats['mean_spread_pts']:.1f} pts\n"
            f"• Avg Latency: {stats['mean_latency_ms']:.1f}ms (p95: {stats['p95_latency_ms']:.1f}ms)\n"
            f"• Total Friction Drag: <b>${stats['total_execution_drag_usd']:.2f}</b>\n"
        )
        return msg

    def format_markdown_report(self) -> str:
        """Generates structured Markdown report."""
        stats = self.engine.generate_summary()
        md = f"""# Weekly Transaction Cost Analysis (TCA) Report

## Aggregate Execution Statistics
| Metric | Value |
| :--- | :--- |
| **Total Executions** | {stats['total_trades']} |
| **Mean Slippage** | {stats['mean_slippage_points']:+.2f} points |
| **Cumulative Slippage** | ${stats['total_slippage_usd']:+.2f} |
| **Average Spread at Fill** | {stats['mean_spread_pts']:.1f} points |
| **Mean Execution Latency** | {stats['mean_latency_ms']:.1f} ms |
| **95th Percentile Latency** | {stats['p95_latency_ms']:.1f} ms |
| **Total Transaction Drag** | **${stats['total_execution_drag_usd']:.2f}** |

## Audit Log
"""
        if not self.engine.records:
            md += "\n*No execution events recorded during this audit cycle.*\n"
        else:
            md += "| Timestamp (UTC) | Request ID | Symbol | Type | Vol | Intended | Filled | Slip (pts) | Latency |\n"
            md += "| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |\n"
            for r in self.engine.records[-20:]:  # Last 20 records
                md += (
                    f"| {r.timestamp_utc[:19]} | `{r.request_id}` | {r.symbol} | {r.order_type} | "
                    f"{r.volume:.2f} | {r.intended_price:.5f} | {r.filled_price:.5f} | "
                    f"{r.slippage_points:+.1f} | {r.latency_send_to_confirm_ms:.1f}ms |\n"
                )

        return md
