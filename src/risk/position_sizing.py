"""
Institutional Position Sizing Engine.
Calculates mathematically precise lot sizing based on account equity,
tick value, contract specifications, and structural stop-loss distances.
Enforces strict volume quantization, minimum reward-to-risk, margin limits,
and Stage 0A Account-Size Adaptation ($5 minimum floor up to institutional scale).
"""

from __future__ import annotations
import math
import logging
from dataclasses import dataclass
from typing import Optional

try:
    import MetaTrader5 as mt5
    MT5_AVAILABLE = True
except ImportError:
    mt5 = None
    MT5_AVAILABLE = False

from src.core.contracts import AccountInfo, SymbolInfo, OrderType
from src.risk.account_tier import AccountTierManager

logger = logging.getLogger("PositionSizingEngine")


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
    tier_name: str = "STANDARD"
    target_risk_pct: float = 1.0
    ceiling_risk_pct: float = 2.0
    min_balance_needed: float = 0.0
    raw_lots: float = 0.0
    is_micro_exception: bool = False
    log_line: str = ""
    rejection_reason: Optional[str] = None


class PositionSizingEngine:
    def __init__(
        self,
        default_risk_percent: float = 1.0,
        max_risk_percent_per_trade: float = 2.0,
        min_rr: float = 2.0,
        reduced_history_factor: float = 0.5,
        max_margin_percent_of_free: float = 50.0,
        max_lot_cap: float = 0.5,
        tier_manager: Optional[AccountTierManager] = None
    ):
        self.default_risk_percent = default_risk_percent
        self.max_risk_percent_per_trade = max_risk_percent_per_trade
        self.min_rr = min_rr
        self.reduced_history_factor = reduced_history_factor
        self.max_margin_percent_of_free = max_margin_percent_of_free
        self.max_lot_cap = max_lot_cap
        self.tier_manager = tier_manager or AccountTierManager()

    def calculate_lot_size(
        self,
        account: AccountInfo,
        symbol_info: SymbolInfo,
        entry_price: float,
        stop_loss: float,
        take_profit: Optional[float] = None,
        risk_percent: Optional[float] = None,
        is_reduced_history: bool = False,
        sizing_reference: str = "CONSERVATIVE",
        order_type: Optional[OrderType] = None,
        adaptive_scalping: bool = False,
        adaptive_min_lot_ceiling_pct: float = 3.5,
        min_rr_override: Optional[float] = None,
    ) -> PositionSizeResult:
        """
        Stage 0A Single Shared Position Sizing Algorithm used by every order path:
        a. risk_money = min(balance, equity) x target_risk_percent.
        b. loss_per_lot = |entry - SL| / tick_size x tick_value (verified with OrderCalcProfit for
           actual symbol, direction, prices, including commission and spread cost).
        c. lots = floor(risk_money / loss_per_lot / volume_step) x volume_step, clamped.
        d. If lots >= volume_min: use it. Never round up.
        e. If lots < volume_min: compute real risk of minimum lot. If <= tier ceiling AND
           (micro_account_min_lot_allowed + acknowledged_high_risk_microaccount OR adaptive_scalping):
           trade min lot. Otherwise skip and report real risk %, needed lot, and minimum balance.
        f. Every decision logs facts with units.
        """
        # Always reload operator flags to reflect live configs
        self.tier_manager.reload_config()

        # 1. Evaluate account facts and cent account detection
        capital_base = min(account.balance, account.equity)
        unit_str = f"USC (${capital_base:.2f} USD)" if account.is_cent_account else f"{account.currency} (${capital_base:.2f})"

        # Evaluate Account Tier
        tier_def, changed, tier_msg = self.tier_manager.evaluate_account(account)
        tier_name = tier_def.name
        if is_reduced_history:
            effective_cap = min(self.max_risk_percent_per_trade, tier_def.absolute_ceiling_pct if tier_def.absolute_ceiling_pct > 0 else self.max_risk_percent_per_trade)
            ceiling_pct = effective_cap * self.reduced_history_factor
            requested_risk = risk_percent if risk_percent is not None else tier_def.target_risk_pct
            target_pct = min(requested_risk, ceiling_pct)
        else:
            ceiling_pct = tier_def.absolute_ceiling_pct
            target_pct = tier_def.target_risk_pct if risk_percent is None else min(risk_percent, tier_def.absolute_ceiling_pct)

        # Hard Floor Guard: < $5.00 disables trading immediately
        if capital_base < 5.0 or tier_name == "BELOW_FLOOR":
            rej = f"Account capital ${capital_base:.2f} is below the $5.00 floor ({tier_name}). Trading disabled."
            log_line = (
                f"[SIZING] REJECTED | Bal: {unit_str} | Tier: {tier_name} | "
                f"TargetRisk: {target_pct:.2f}% | RawLots: 0.00 | FinalLots: 0.00 | Reason: {rej}"
            )
            logger.warning(log_line)
            return PositionSizeResult(
                is_approved=False,
                volume=0.0,
                monetary_risk=0.0,
                risk_percent_actual=0.0,
                sl_points=0.0,
                sl_pips=0.0,
                loss_per_lot=0.0,
                margin_required=0.0,
                tier_name=tier_name,
                target_risk_pct=target_pct,
                ceiling_risk_pct=ceiling_pct,
                min_balance_needed=5.0,
                raw_lots=0.0,
                log_line=log_line,
                rejection_reason=rej
            )

        # 2. Risk Money Calculation
        risk_money = capital_base * (target_pct / 100.0)

        # 3. Structural SL distance
        sl_distance = abs(entry_price - stop_loss)
        if sl_distance <= 0:
            rej = "Stop loss distance must be strictly greater than zero."
            log_line = f"[SIZING] REJECTED | Bal: {unit_str} | Tier: {tier_name} | Reason: {rej}"
            logger.warning(log_line)
            return PositionSizeResult(
                is_approved=False,
                volume=0.0,
                monetary_risk=0.0,
                risk_percent_actual=0.0,
                sl_points=0.0,
                sl_pips=0.0,
                loss_per_lot=0.0,
                margin_required=0.0,
                tier_name=tier_name,
                target_risk_pct=target_pct,
                ceiling_risk_pct=ceiling_pct,
                min_balance_needed=0.0,
                raw_lots=0.0,
                log_line=log_line,
                rejection_reason=rej
            )

        tick_size = symbol_info.tick_size if symbol_info.tick_size > 0 else symbol_info.point
        tick_value = symbol_info.tick_value if symbol_info.tick_value > 0 else 1.0

        # Structural loss per lot calculation (verified via OrderCalcProfit)
        base_loss_per_lot = 0.0
        if MT5_AVAILABLE and mt5.terminal_info() is not None:
            try:
                mt5_order_type = mt5.ORDER_TYPE_BUY if (entry_price > stop_loss) else mt5.ORDER_TYPE_SELL
                if order_type is not None:
                    if order_type in [OrderType.BUY, OrderType.BUY_LIMIT, OrderType.BUY_STOP]:
                        mt5_order_type = mt5.ORDER_TYPE_BUY
                    else:
                        mt5_order_type = mt5.ORDER_TYPE_SELL
                profit_calc = mt5.order_calc_profit(mt5_order_type, symbol_info.symbol, 1.0, entry_price, stop_loss)
                if profit_calc is not None and profit_calc != 0.0:
                    base_loss_per_lot = abs(float(profit_calc))
            except Exception:
                pass

        if base_loss_per_lot <= 0.0:
            num_ticks = sl_distance / tick_size
            base_loss_per_lot = num_ticks * tick_value

        if base_loss_per_lot <= 0:
            rej = "Calculated loss per lot is non-positive."
            log_line = f"[SIZING] REJECTED | Bal: {unit_str} | Tier: {tier_name} | Reason: {rej}"
            logger.warning(log_line)
            return PositionSizeResult(
                is_approved=False,
                volume=0.0,
                monetary_risk=0.0,
                risk_percent_actual=0.0,
                sl_points=0.0,
                sl_pips=0.0,
                loss_per_lot=0.0,
                margin_required=0.0,
                tier_name=tier_name,
                target_risk_pct=target_pct,
                ceiling_risk_pct=ceiling_pct,
                raw_lots=0.0,
                log_line=log_line,
                rejection_reason=rej
            )

        # 4. Raw lots calculation from structural loss
        raw_volume = risk_money / base_loss_per_lot

        # Lot caps
        effective_max_lot = min(symbol_info.max_lot, self.max_lot_cap)
        if capital_base < 500.0:
            effective_max_lot = min(effective_max_lot, symbol_info.min_lot * 3.0)

        # 5. Volume quantization (never round up)
        step = symbol_info.lot_step if symbol_info.lot_step > 0 else 0.01
        steps_count = math.floor(round(raw_volume / step, 6))
        quantized_volume = round(steps_count * step, 2)

        # Spread & Commission Cost Estimation per lot
        spread_pts = symbol_info.spread if symbol_info.spread > 0 else 1.0
        spread_cost_per_lot = (spread_pts * symbol_info.point / tick_size) * tick_value
        commission_per_lot = 7.0  # $7 standard institutional roundturn commission
        total_loss_per_lot = base_loss_per_lot + spread_cost_per_lot + commission_per_lot

        # 6. Check against minimum lot & Micro Account / Adaptive Scalping Adaptation
        min_lot = symbol_info.min_lot if symbol_info.min_lot > 0 else 0.01
        min_lot_loss = min_lot * total_loss_per_lot
        min_lot_risk_pct = (min_lot_loss / capital_base) * 100.0
        min_balance_needed = min_lot_loss / (target_pct / 100.0)

        is_micro_exception = False

        if quantized_volume >= min_lot:
            # Volume meets or exceeds broker minimum
            final_volume = min(quantized_volume, effective_max_lot)
        else:
            # Sized volume rounded down to zero or below min_lot
            flags_active = (
                self.tier_manager.micro_account_min_lot_allowed
                and self.tier_manager.acknowledged_high_risk_microaccount
            )
            effective_ceiling = max(ceiling_pct, adaptive_min_lot_ceiling_pct) if adaptive_scalping else ceiling_pct

            if (flags_active and min_lot_risk_pct <= ceiling_pct) or (
                adaptive_scalping and min_lot_risk_pct <= effective_ceiling and capital_base >= 5.0
            ):
                final_volume = min_lot
                is_micro_exception = True
                ceiling_pct = effective_ceiling
                logger.warning(
                    f"[SIZING] {'Adaptive-Scalping' if adaptive_scalping else 'Micro-Account'} Exception Granted for {symbol_info.symbol}: "
                    f"MinLot {min_lot:.2f} risks ${min_lot_loss:.2f} ({min_lot_risk_pct:.2f}% <= Ceiling {effective_ceiling:.2f}%)"
                )
            else:
                reason = (
                    f"Min lot {min_lot:.2f} risks ${min_lot_loss:.2f} ({min_lot_risk_pct:.2f}% of capital ${capital_base:.2f}), "
                    f"exceeding target risk {target_pct:.2f}% (Ceiling: {effective_ceiling:.2f}%, exceeding max risk cap). "
                    f"Required raw lot: {raw_volume:.4f}. Min balance needed: ${min_balance_needed:.2f}."
                )
                log_line = (
                    f"[SIZING] SKIPPED | Bal: {unit_str} | Tier: {tier_name} | "
                    f"TargetRisk: {target_pct:.2f}% | Loss/Lot: ${total_loss_per_lot:.2f} | "
                    f"RawLots: {raw_volume:.4f} | FinalLots: 0.00 | RealRisk: {min_lot_risk_pct:.2f}% (${min_lot_loss:.2f}) | "
                    f"Reason: {reason}"
                )
                logger.info(log_line)
                return PositionSizeResult(
                    is_approved=False,
                    volume=0.0,
                    monetary_risk=min_lot_loss,
                    risk_percent_actual=min_lot_risk_pct,
                    sl_points=sl_distance / symbol_info.point,
                    sl_pips=sl_distance / (symbol_info.point * 10),
                    loss_per_lot=total_loss_per_lot,
                    margin_required=0.0,
                    tier_name=tier_name,
                    target_risk_pct=target_pct,
                    ceiling_risk_pct=effective_ceiling,
                    min_balance_needed=round(min_balance_needed, 2),
                    raw_lots=round(raw_volume, 4),
                    is_micro_exception=False,
                    log_line=log_line,
                    rejection_reason=reason
                )

        actual_loss_money = final_volume * base_loss_per_lot
        actual_risk_pct = (actual_loss_money / capital_base) * 100.0
        actual_monetary_risk = actual_loss_money + (final_volume * (spread_cost_per_lot + commission_per_lot))

        # Hard Ceiling Guard Check
        if actual_risk_pct > ceiling_pct:
            rej = f"Calculated risk {actual_risk_pct:.2f}% exceeds tier absolute ceiling {ceiling_pct:.2f}%"
            log_line = f"[SIZING] REJECTED | Bal: {unit_str} | Tier: {tier_name} | Reason: {rej}"
            logger.warning(log_line)
            return PositionSizeResult(
                is_approved=False,
                volume=0.0,
                monetary_risk=actual_monetary_risk,
                risk_percent_actual=actual_risk_pct,
                sl_points=sl_distance / symbol_info.point,
                sl_pips=sl_distance / (symbol_info.point * 10),
                loss_per_lot=total_loss_per_lot,
                margin_required=0.0,
                tier_name=tier_name,
                target_risk_pct=target_pct,
                ceiling_risk_pct=ceiling_pct,
                min_balance_needed=round(min_balance_needed, 2),
                raw_lots=round(raw_volume, 4),
                log_line=log_line,
                rejection_reason=rej
            )

        # 7. Reward to Risk check
        rr = 0.0
        total_tp_money = 0.0
        if take_profit is not None and take_profit > 0.0:
            tp_dist = abs(take_profit - entry_price)
            tp_ticks = tp_dist / tick_size
            gain_per_lot = tp_ticks * tick_value
            gross_tp = final_volume * gain_per_lot
            total_tp_money = max(0.0, gross_tp - (final_volume * (spread_cost_per_lot + commission_per_lot)))
            rr = (total_tp_money / actual_monetary_risk) if actual_monetary_risk > 0 else 0.0

            tier_min_rr = min_rr_override if min_rr_override is not None else max(2.0, tier_def.min_reward_to_risk)
            if rr < tier_min_rr:
                rej = f"Reward:Risk ratio {rr:.2f} is below minimum {tier_min_rr:.1f} (SL: -${actual_monetary_risk:.2f}, TP: +${total_tp_money:.2f})"
                log_line = f"[SIZING] REJECTED | Bal: {unit_str} | Tier: {tier_name} | Reason: {rej}"
                return PositionSizeResult(
                    is_approved=False,
                    volume=final_volume,
                    monetary_risk=round(actual_monetary_risk, 2),
                    risk_percent_actual=round(actual_risk_pct, 3),
                    sl_points=round(sl_distance / symbol_info.point, 1),
                    sl_pips=round(sl_distance / (symbol_info.point * 10), 1),
                    loss_per_lot=round(total_loss_per_lot, 2),
                    margin_required=0.0,
                    reward_to_risk=round(rr, 2),
                    tp_money=round(total_tp_money, 2),
                    tier_name=tier_name,
                    target_risk_pct=target_pct,
                    ceiling_risk_pct=ceiling_pct,
                    min_balance_needed=round(min_balance_needed, 2),
                    raw_lots=round(raw_volume, 4),
                    log_line=log_line,
                    rejection_reason=rej
                )

        # 8. Margin check
        margin_per_lot = symbol_info.margin_required if symbol_info.margin_required > 0 else 1000.0
        estimated_margin = final_volume * margin_per_lot
        tier_margin_pct = tier_def.max_margin_percent_of_free
        max_free_margin = account.free_margin * (tier_margin_pct / 100.0)

        if estimated_margin > max_free_margin and max_free_margin > 0:
            rej = f"Required margin ${estimated_margin:.2f} exceeds {tier_margin_pct:.0f}% of free margin (${max_free_margin:.2f} / ${account.free_margin:.2f})"
            log_line = f"[SIZING] REJECTED | Bal: {unit_str} | Tier: {tier_name} | Reason: {rej}"
            return PositionSizeResult(
                is_approved=False,
                volume=final_volume,
                monetary_risk=round(actual_monetary_risk, 2),
                risk_percent_actual=round(actual_risk_pct, 3),
                sl_points=round(sl_distance / symbol_info.point, 1),
                sl_pips=round(sl_distance / (symbol_info.point * 10), 1),
                loss_per_lot=round(total_loss_per_lot, 2),
                margin_required=round(estimated_margin, 2),
                reward_to_risk=round(rr, 2),
                tp_money=round(total_tp_money, 2),
                tier_name=tier_name,
                target_risk_pct=target_pct,
                ceiling_risk_pct=ceiling_pct,
                min_balance_needed=round(min_balance_needed, 2),
                raw_lots=round(raw_volume, 4),
                log_line=log_line,
                rejection_reason=rej
            )

        # 9. Sizing Approval and Structured Audit Log Line
        log_line = (
            f"[SIZING] APPROVED | Bal: {unit_str} | Tier: {tier_name} | "
            f"TargetRisk: {target_pct:.2f}% | Loss/Lot: ${total_loss_per_lot:.2f} | "
            f"RawLots: {raw_volume:.4f} | FinalLots: {final_volume:.2f} | "
            f"RealRisk: {actual_risk_pct:.2f}% (${actual_monetary_risk:.2f}) | "
            f"MarginReq: ${estimated_margin:.2f} | RR: {rr:.2f}"
            f"{' [MICRO_EXCEPTION]' if is_micro_exception else ''}"
        )
        logger.info(log_line)

        return PositionSizeResult(
            is_approved=True,
            volume=final_volume,
            monetary_risk=round(actual_monetary_risk, 2),
            risk_percent_actual=round(actual_risk_pct, 3),
            sl_points=round(sl_distance / symbol_info.point, 1),
            sl_pips=round(sl_distance / (symbol_info.point * 10), 1),
            loss_per_lot=round(total_loss_per_lot, 2),
            margin_required=round(estimated_margin, 2),
            reward_to_risk=round(rr, 2),
            tp_money=round(total_tp_money, 2),
            tier_name=tier_name,
            target_risk_pct=target_pct,
            ceiling_risk_pct=ceiling_pct,
            min_balance_needed=round(min_balance_needed, 2),
            raw_lots=round(raw_volume, 4),
            is_micro_exception=is_micro_exception,
            log_line=log_line,
            rejection_reason=None
        )
