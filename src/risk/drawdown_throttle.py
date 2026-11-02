"""
Drawdown Throttle and Conditional Value at Risk (CVaR) Engine.
Progressively scales down risk in discrete tiers as drawdown increases towards hard limits.
Triggers hard pause before touching daily or total drawdown boundaries.
"""

from __future__ import annotations
import math
from dataclasses import dataclass
from typing import List, Tuple, Optional
import numpy as np


@dataclass(frozen=True)
class ThrottleStatus:
    risk_multiplier: float        # Multiplier applied to base risk (0.0 to 1.0)
    daily_drawdown_pct: float     # Current daily DD %
    total_drawdown_pct: float     # Current total DD % from peak equity
    daily_limit_pct: float        # Daily DD limit %
    total_limit_pct: float        # Total DD limit %
    is_paused: bool               # True if buffer breached
    cvar_99_pct: float            # 99% Expected Shortfall (CVaR) in %
    cvar_alert: bool              # True if CVaR tail risk exceeds remaining buffer
    throttle_tier: str            # 'NORMAL', 'MILD_THROTTLE', 'HEAVY_THROTTLE', 'BUFFER_PAUSED'
    message: str


class DrawdownThrottle:
    def __init__(
        self,
        daily_loss_limit_pct: float = 4.0,
        total_loss_limit_pct: float = 8.0,
        buffer_fraction: float = 0.80  # Pause at 80% of limit to preserve cushion
    ):
        self.daily_loss_limit_pct = daily_loss_limit_pct
        self.total_loss_limit_pct = total_loss_limit_pct
        self.buffer_fraction = buffer_fraction

    def compute_cvar_99(self, trade_returns_pct: List[float]) -> float:
        """
        Computes historical 99% Conditional Value at Risk (Expected Shortfall).
        CVaR = E[loss | loss >= VaR_99].
        """
        if len(trade_returns_pct) < 10:
            return 2.0  # Conservative default if small sample

        arr = np.array(trade_returns_pct, dtype=np.float64)
        losses = -arr[arr < 0]  # Positive loss values
        if len(losses) == 0:
            return 0.5

        # 99th percentile loss
        var_99 = float(np.percentile(losses, 95)) if len(losses) < 100 else float(np.percentile(losses, 99))
        tail_losses = losses[losses >= var_99]
        if len(tail_losses) == 0:
            return var_99
        return float(np.mean(tail_losses))

    def evaluate(
        self,
        daily_starting_equity: float,
        current_equity: float,
        peak_equity: float,
        trade_returns_pct: Optional[List[float]] = None
    ) -> ThrottleStatus:
        """
        Evaluates current drawdown against daily and peak thresholds and computes throttle multiplier.
        """
        # Daily drawdown
        if daily_starting_equity > 0:
            daily_dd_pct = max(0.0, (daily_starting_equity - current_equity) / daily_starting_equity * 100.0)
        else:
            daily_dd_pct = 0.0

        # Peak drawdown
        eff_peak = max(peak_equity, current_equity)
        if eff_peak > 0:
            total_dd_pct = max(0.0, (eff_peak - current_equity) / eff_peak * 100.0)
        else:
            total_dd_pct = 0.0

        # Compute utilization of daily and total limits
        daily_util = daily_dd_pct / self.daily_loss_limit_pct if self.daily_loss_limit_pct > 0 else 0.0
        total_util = total_dd_pct / self.total_loss_limit_pct if self.total_loss_limit_pct > 0 else 0.0
        max_util = max(daily_util, total_util)

        # Multiplier determination
        if max_util >= self.buffer_fraction or daily_dd_pct >= self.daily_loss_limit_pct or total_dd_pct >= self.total_loss_limit_pct:
            multiplier = 0.0
            is_paused = True
            tier = "BUFFER_PAUSED"
            msg = f"Drawdown buffer reached ({max_util:.1%} of limit). Trading paused to protect capital."
        elif max_util >= 0.60:
            multiplier = 0.40  # 40% normal risk
            is_paused = False
            tier = "HEAVY_THROTTLE"
            msg = f"Heavy drawdown throttle active ({max_util:.1%} of limit). Risk cut to 40%."
        elif max_util >= 0.30:
            multiplier = 0.70  # 70% normal risk
            is_paused = False
            tier = "MILD_THROTTLE"
            msg = f"Mild drawdown throttle active ({max_util:.1%} of limit). Risk cut to 70%."
        else:
            multiplier = 1.00
            is_paused = False
            tier = "NORMAL"
            msg = f"Drawdown within safe regime ({max_util:.1%} of limit). Full tier risk active."

        # CVaR check
        cvar_99 = self.compute_cvar_99(trade_returns_pct or [])
        remaining_daily_cushion = self.daily_loss_limit_pct - daily_dd_pct
        cvar_alert = cvar_99 > remaining_daily_cushion and remaining_daily_cushion > 0

        if cvar_alert:
            msg += f" [WARNING: 99% CVaR ({cvar_99:.2f}%) exceeds remaining daily buffer ({remaining_daily_cushion:.2f}%)]"

        return ThrottleStatus(
            risk_multiplier=multiplier,
            daily_drawdown_pct=round(daily_dd_pct, 3),
            total_drawdown_pct=round(total_dd_pct, 3),
            daily_limit_pct=self.daily_loss_limit_pct,
            total_limit_pct=self.total_loss_limit_pct,
            is_paused=is_paused,
            cvar_99_pct=round(cvar_99, 2),
            cvar_alert=cvar_alert,
            throttle_tier=tier,
            message=msg
        )
