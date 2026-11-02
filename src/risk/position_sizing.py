"""
Institutional Position Sizing Engine.
Calculates mathematically precise lot sizing based on account equity,
tick value, contract specifications, and structural stop-loss distances.
Enforces strict volume quantization, minimum reward-to-risk, margin limits,
and rejection if risk budgets or hard caps are breached.
"""

from __future__ import annotations
import math
from dataclasses import dataclass
from typing import Optional, Tuple

from src.core.contracts import AccountInfo, SymbolInfo, OrderType


@dataclass
class PositionSizeResult:
    is_approved: bool
    volume: float
    monetary_risk: float
    risk_percent_actual: float
    sl_points: float
    sl_pips: float
    loss_per_lot: float
    margin_required: float
    reward_to_risk: float = 0.0
    tp_money: float = 0.0
    rejection_reason: Optional[str] = None


class PositionSizingEngine:
    def __init__(
        self,
        default_risk_percent: float = 1.0,
        max_risk_percent_per_trade: float = 2.0,
        min_rr: float = 2.0,
        reduced_history_factor: float = 0.5,
        max_margin_percent_of_free: float = 50.0,
        max_lot_cap: float = 0.5
    ):
        self.default_risk_percent = default_risk_percent
        self.max_risk_percent_per_trade = max_risk_percent_per_trade
        self.min_rr = min_rr
        self.reduced_history_factor = reduced_history_factor
        self.max_margin_percent_of_free = max_margin_percent_of_free
        self.max_lot_cap = max_lot_cap

    def calculate_lot_size(
        self,
        account: AccountInfo,
        symbol_info: SymbolInfo,
        entry_price: float,
        stop_loss: float,
        take_profit: Optional[float] = None,
        risk_percent: Optional[float] = None,
        is_reduced_history: bool = False,
        sizing_reference: str = "CONSERVATIVE"
    ) -> PositionSizeResult:
        """
        Calculates normalized trading lot size.
        Sizing reference:
        - "EQUITY": Uses live account equity.
        - "BALANCE": Uses live account balance.
        - "CONSERVATIVE": Uses min(balance, equity).
        """
        base_cap = self.max_risk_percent_per_trade
        if is_reduced_history:
            base_cap *= self.reduced_history_factor

        req_pct = risk_percent if risk_percent is not None else self.default_risk_percent
        pct = min(req_pct, base_cap)

        # Determine capital base
        if sizing_reference.upper() == "BALANCE":
            capital_base = account.balance
        elif sizing_reference.upper() == "EQUITY":
            capital_base = account.equity
        else:
            capital_base = min(account.balance, account.equity)

        if capital_base <= 0:
            return PositionSizeResult(
                is_approved=False,
                volume=0.0,
                monetary_risk=0.0,
                risk_percent_actual=0.0,
                sl_points=0.0,
                sl_pips=0.0,
                loss_per_lot=0.0,
                margin_required=0.0,
                rejection_reason="Insufficient or negative capital base."
            )

        target_risk_amount = capital_base * (pct / 100.0)

        # SL distance in points and ticks
        sl_distance = abs(entry_price - stop_loss)
        if sl_distance <= 0:
            return PositionSizeResult(
                is_approved=False,
                volume=0.0,
                monetary_risk=0.0,
                risk_percent_actual=0.0,
                sl_points=0.0,
                sl_pips=0.0,
                loss_per_lot=0.0,
                margin_required=0.0,
                rejection_reason="Stop loss distance must be greater than zero."
            )

        tick_size = symbol_info.tick_size if symbol_info.tick_size > 0 else symbol_info.point
        tick_value = symbol_info.tick_value if symbol_info.tick_value > 0 else 1.0

        num_ticks = sl_distance / tick_size
        loss_per_lot = num_ticks * tick_value

        if loss_per_lot <= 0:
            return PositionSizeResult(
                is_approved=False,
                volume=0.0,
                monetary_risk=0.0,
                risk_percent_actual=0.0,
                sl_points=0.0,
                sl_pips=0.0,
                loss_per_lot=0.0,
                margin_required=0.0,
                rejection_reason="Calculated loss per lot is non-positive."
            )

        # Raw volume
        raw_volume = target_risk_amount / loss_per_lot

        # Dynamic lot cap: accounts under $500 default no larger than min_lot * 3
        effective_max_lot = min(symbol_info.max_lot, self.max_lot_cap)
        if capital_base < 500.0:
            effective_max_lot = min(effective_max_lot, symbol_info.min_lot * 3.0)

        # Quantize volume strictly down to the nearest permitted step (never round up)
        step = symbol_info.lot_step if symbol_info.lot_step > 0 else 0.01
        steps_count = math.floor(round(raw_volume / step, 6))
        quantized_volume = round(steps_count * step, 2)

        # Check against broker minimum lot
        min_lot_loss = symbol_info.min_lot * loss_per_lot
        min_lot_risk_pct = (min_lot_loss / capital_base) * 100.0

        if quantized_volume < symbol_info.min_lot:
            # If minimum lot already risks more than allowed cap, skip and report!
            if min_lot_risk_pct > base_cap:
                return PositionSizeResult(
                    is_approved=False,
                    volume=0.0,
                    monetary_risk=min_lot_loss,
                    risk_percent_actual=min_lot_risk_pct,
                    sl_points=sl_distance / symbol_info.point,
                    sl_pips=sl_distance / (symbol_info.point * 10),
                    loss_per_lot=loss_per_lot,
                    margin_required=0.0,
                    rejection_reason=(
                        f"Min lot {symbol_info.min_lot:.2f} risks ${min_lot_loss:.2f} "
                        f"({min_lot_risk_pct:.2f}% of capital ${capital_base:.2f}), "
                        f"exceeding max risk cap ({base_cap:.2f}%{' [REDUCED_HIST]' if is_reduced_history else ''})"
                    )
                )
            else:
                quantized_volume = symbol_info.min_lot

        # Clamp against maximum lot
        if quantized_volume > effective_max_lot:
            quantized_volume = effective_max_lot

        actual_monetary_risk = quantized_volume * loss_per_lot
        actual_risk_percent = (actual_monetary_risk / capital_base) * 100.0

        # Spread & Commission Cost Estimation
        spread_cost = (symbol_info.spread * symbol_info.point / tick_size) * tick_value * quantized_volume
        commission_est = quantized_volume * 7.0
        total_sl_money = actual_monetary_risk + spread_cost + commission_est

        # Reward-to-Risk check if TP is provided
        rr = 0.0
        total_tp_money = 0.0
        if take_profit is not None and take_profit > 0.0:
            tp_distance = abs(take_profit - entry_price)
            tp_num_ticks = tp_distance / tick_size
            gain_per_lot = tp_num_ticks * tick_value
            gross_tp_money = quantized_volume * gain_per_lot
            total_tp_money = max(0.0, gross_tp_money - spread_cost - commission_est)
            rr = (total_tp_money / total_sl_money) if total_sl_money > 0 else 0.0

            if rr < self.min_rr:
                return PositionSizeResult(
                    is_approved=False,
                    volume=quantized_volume,
                    monetary_risk=round(total_sl_money, 2),
                    risk_percent_actual=round(actual_risk_percent, 3),
                    sl_points=round(sl_distance / symbol_info.point, 1),
                    sl_pips=round(sl_distance / (symbol_info.point * 10), 1),
                    loss_per_lot=round(loss_per_lot, 2),
                    margin_required=0.0,
                    reward_to_risk=round(rr, 2),
                    tp_money=round(total_tp_money, 2),
                    rejection_reason=f"RR {rr:.2f} below minimum {self.min_rr:.1f} (SL -${total_sl_money:.2f} vs TP +${total_tp_money:.2f})"
                )

        # Margin requirement validation
        estimated_margin = quantized_volume * symbol_info.margin_required
        max_margin = account.free_margin * (self.max_margin_percent_of_free / 100.0)
        if estimated_margin > max_margin:
            return PositionSizeResult(
                is_approved=False,
                volume=quantized_volume,
                monetary_risk=round(total_sl_money, 2),
                risk_percent_actual=round(actual_risk_percent, 3),
                sl_points=round(sl_distance / symbol_info.point, 1),
                sl_pips=round(sl_distance / (symbol_info.point * 10), 1),
                loss_per_lot=round(loss_per_lot, 2),
                margin_required=round(estimated_margin, 2),
                reward_to_risk=round(rr, 2),
                tp_money=round(total_tp_money, 2),
                rejection_reason=f"Required margin ${estimated_margin:.2f} exceeds {self.max_margin_percent_of_free:.0f}% of free margin (${max_margin:.2f} / ${account.free_margin:.2f})"
            )

        return PositionSizeResult(
            is_approved=True,
            volume=quantized_volume,
            monetary_risk=round(total_sl_money, 2),
            risk_percent_actual=round(actual_risk_percent, 3),
            sl_points=round(sl_distance / symbol_info.point, 1),
            sl_pips=round(sl_distance / (symbol_info.point * 10), 1),
            loss_per_lot=round(loss_per_lot, 2),
            margin_required=round(estimated_margin, 2),
            reward_to_risk=round(rr, 2),
            tp_money=round(total_tp_money, 2),
            rejection_reason=None
        )
