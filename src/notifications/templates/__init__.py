"""
Centralized Telegram HTML Notification Templates (Phase 1).
All wording, number formatting, HTML escaping, and 4096-character splitting live here.
"""

from src.notifications.templates.default_templates import (
    TemplateRenderer,
    fmt_price,
    fmt_money,
    fmt_r,
    fmt_pct,
    format_timestamp_footer,
    split_message_safely,
    build_sample_events,
)

__all__ = [
    "TemplateRenderer",
    "fmt_price",
    "fmt_money",
    "fmt_r",
    "fmt_pct",
    "format_timestamp_footer",
    "split_message_safely",
    "build_sample_events",
]
