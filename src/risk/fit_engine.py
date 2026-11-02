"""
Universal Risk Fit Engine.
Adapts stop distance, timeframe, lot size, risk and reward-to-risk to ANY instrument
and ANY balance from $5 upward, respecting institutional hard ceilings.

Implements:
- Stage 1: Feasibility Solver & Usable Stop Window [min_stop, max_stop]
- Stage 2: Dynamic Reward-to-Risk, Reachability Ceiling & Expectancy Filter
- Stage 3: Data-Driven Instrument & Timeframe Adaptation, Time-of-Day Filter & Fit Scoring
- Stage 4: Visibility, State-Change Alerts, /fit & /status Reporting
"""

from __future__ import annotations
import os
import json
import math
import logging
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple, Any

try:
    import MetaTrader5 as mt5
    MT5_AVAILABLE = True
except ImportError:
    mt5 = None
    MT5_AVAILABLE = False

from src.core.contracts import (
    AccountInfo,
    SymbolInfo,
    OrderType,
    AccountTier,
    AffordabilityStatus
)
from src.risk.account_tier import AccountTierManager, TierDefinition
from src.execution.analytics.tca import TCAEngine

logger = logging.getLogger("RiskFitEngine")

DEFAULT_FIT_CONFIG_PATH = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "config", "risk_fit.json")
)


@dataclass
class FeasibilityWindow:
    min_stop_price: float
    max_stop_price: float
    min_stop_points: float
    max_stop_points: float
    min_stop_money: float
    max_stop_money: float
    is_feasible: bool
    rejection_reason: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "min_stop_price": round(self.min_stop_price, 5),
            "max_stop_price": round(self.max_stop_price, 5),
            "min_stop_points": round(self.min_stop_points, 1),
            "max_stop_points": round(self.max_stop_points, 1),
            "min_stop_money": round(self.min_stop_money, 2),
            "max_stop_money": round(self.max_stop_money, 2),
            "is_feasible": self.is_feasible,
            "rejection_reason": self.rejection_reason,
        }


@dataclass
class DynamicRRResult:
    required_rr: float
    min_rr_floor: float
    reachable_tp_price: float
    reachable_tp_points: float
    reachable_rr: float
    cost_in_r: float
    expected_value_in_r: float
    is_approved: bool
    rejection_reason: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "required_rr": round(self.required_rr, 2),
            "min_rr_floor": round(self.min_rr_floor, 2),
            "reachable_tp_price": round(self.reachable_tp_price, 5),
            "reachable_tp_points": round(self.reachable_tp_points, 1),
            "reachable_rr": round(self.reachable_rr, 2),
            "cost_in_r": round(self.cost_in_r, 4),
            "expected_value_in_r": round(self.expected_value_in_r, 4),
            "is_approved": self.is_approved,
            "rejection_reason": self.rejection_reason,
        }


@dataclass
class RiskFitDecision:
    symbol: str
    status: str  # TRADABLE, MARGINAL, BLOCKED_BY_BALANCE, SKIPPED
    timeframe: str
    balance: float
    equity: float
    tier_name: str
    risk_budget: float
    target_risk_pct: float
    ceiling_risk_pct: float
    window: FeasibilityWindow
    chosen_stop_price: float
    chosen_stop_points: float
    chosen_stop_money: float
    lot: float
    raw_lot: float
    real_risk_money: float
    real_risk_pct: float
    rr_result: DynamicRRResult
    cost_in_r: float
    expected_value_in_r: float
    balance_needed: float
    fit_score: float
    is_feasible: bool
    is_micro_exception: bool = False
    log_line: str = ""
    rejection_reason: Optional[str] = None
    candidate_evaluations: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "symbol": self.symbol,
            "status": self.status,
            "timeframe": self.timeframe,
            "balance": round(self.balance, 2),
            "equity": round(self.equity, 2),
            "tier_name": self.tier_name,
            "risk_budget": round(self.risk_budget, 2),
            "target_risk_pct": round(self.target_risk_pct, 2),
            "ceiling_risk_pct": round(self.ceiling_risk_pct, 2),
            "window": self.window.to_dict(),
            "chosen_stop_price": round(self.chosen_stop_price, 5),
            "chosen_stop_points": round(self.chosen_stop_points, 1),
            "chosen_stop_money": round(self.chosen_stop_money, 2),
            "lot": round(self.lot, 4),
            "raw_lot": round(self.raw_lot, 6),
            "real_risk_money": round(self.real_risk_money, 2),
            "real_risk_pct": round(self.real_risk_pct, 2),
            "rr_result": self.rr_result.to_dict(),
            "cost_in_r": round(self.cost_in_r, 4),
            "expected_value_in_r": round(self.expected_value_in_r, 4),
            "balance_needed": round(self.balance_needed, 2),
            "fit_score": round(self.fit_score, 3),
            "is_feasible": self.is_feasible,
            "is_micro_exception": self.is_micro_exception,
            "log_line": self.log_line,
            "rejection_reason": self.rejection_reason,
            "candidate_evaluations": self.candidate_evaluations,
        }


class RiskFitEngine:
    def __init__(
        self,
        config_path: str = DEFAULT_FIT_CONFIG_PATH,
        tier_manager: Optional[AccountTierManager] = None,
        tca_engine: Optional[TCAEngine] = None,
    ):
        self.config_path = config_path
        self.tier_manager = tier_manager or AccountTierManager()
        self.tca_engine = tca_engine
        self.config: Dict[str, Any] = {}
        self.profiles: Dict[str, Dict[str, Any]] = {}
        self._previous_states: Dict[str, str] = {}
        self.reload_config()
        self._load_cached_profiles()

    def reload_config(self) -> None:
        """Loads risk fit configuration from disk."""
        if not os.path.exists(self.config_path):
            logger.warning(f"Risk fit config not found at {self.config_path}, using defaults.")
            self.config = {
                "max_cost_fraction": {"SCALP": 0.08, "HOLD": 0.05, "DEFAULT": 0.05},
                "noise_multiplier": {"SCALP": 1.5, "HOLD": 2.0, "DEFAULT": 1.8},
                "broker_buffer_points": 5.0,
                "candidate_timeframes": ["M5", "M15", "M30", "H1", "H4"],
                "min_rr_floor": 1.5,
                "default_rr": 2.0,
                "safety_margin_rr": 0.3,
                "reach_multiplier": 4.0,
                "spread_to_atr_max_ratio": 0.20,
                "default_expected_slippage_points": 2.0,
                "default_commission_per_lot": 6.0,
                "profile_cache_path": "data/cache/instrument_profiles.json",
                "hysteresis_pct": 5.0,
                "fit_score_weights": {
                    "feasibility": 0.35,
                    "cost_efficiency": 0.25,
                    "liquidity": 0.20,
                    "low_correlation": 0.20
                },
                "rollover_window_utc": {
                    "start_hour": 21,
                    "start_minute": 55,
                    "end_hour": 22,
                    "end_minute": 15
                },
                "news_filter_buffer_minutes": 15
            }
        else:
            with open(self.config_path, "r", encoding="utf-8") as f:
                self.config = json.load(f)

    def _get_cache_path(self) -> str:
        cache_rel = self.config.get("profile_cache_path", "data/cache/instrument_profiles.json")
        return os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", cache_rel))

    def _load_cached_profiles(self) -> None:
        path = self._get_cache_path()
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    self.profiles = json.load(f)
            except Exception as e:
                logger.warning(f"Failed to load profile cache: {e}")
                self.profiles = {}

    def _save_cached_profiles(self) -> None:
        path = self._get_cache_path()
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                json.dump(self.profiles, f, indent=2)
        except Exception as e:
            logger.warning(f"Failed to save profile cache: {e}")

    # -------------------------------------------------------------------------
    # STAGE 1: Feasibility Solver
    # -------------------------------------------------------------------------
    def calculate_loss_per_price_unit(
        self,
        symbol_info: SymbolInfo,
        order_type: OrderType,
        entry_price: float,
        volume: Optional[float] = None,
        account: Optional[AccountInfo] = None,
    ) -> float:
        """
        Calculates loss in account currency per 1.0 unit of price movement
        for the given volume (default minimum lot), using OrderCalcProfit when live.
        """
        vol = volume if volume is not None and volume > 0 else symbol_info.min_lot
        if vol <= 0:
            vol = 0.01

        # Directional test delta (1.0 price unit)
        is_buy = order_type in [OrderType.BUY, OrderType.BUY_LIMIT, OrderType.BUY_STOP]
        test_delta = 1.0
        p_loss = (entry_price - test_delta) if is_buy else (entry_price + test_delta)

        if MT5_AVAILABLE and mt5.terminal_info() is not None:
            try:
                term_acc = mt5.account_info()
                if term_acc is not None and (account is None or account.currency == term_acc.currency):
                    mt5_type = mt5.ORDER_TYPE_BUY if is_buy else mt5.ORDER_TYPE_SELL
                    prof = mt5.order_calc_profit(mt5_type, symbol_info.symbol, vol, entry_price, p_loss)
                    if prof is not None and prof != 0.0:
                        return abs(float(prof)) / test_delta
            except Exception:
                pass

        # Exact formulaic fallback
        tick_sz = symbol_info.tick_size if symbol_info.tick_size > 0 else symbol_info.point
        tick_val = symbol_info.tick_value if symbol_info.tick_value > 0 else 1.0
        if tick_sz <= 0:
            tick_sz = 0.00001
        loss_per_price_unit = (1.0 / tick_sz) * tick_val * (vol / 1.0)
        return max(1e-7, loss_per_price_unit)

    def calculate_margin_required(
        self,
        symbol_info: SymbolInfo,
        order_type: OrderType,
        volume: float,
        price: float,
        account: Optional[AccountInfo] = None,
    ) -> float:
        """Calculates margin requirement via OrderCalcMargin or formula fallback."""
        if volume <= 0:
            return 0.0
        is_buy = order_type in [OrderType.BUY, OrderType.BUY_LIMIT, OrderType.BUY_STOP]
        if MT5_AVAILABLE and mt5.terminal_info() is not None:
            try:
                term_acc = mt5.account_info()
                if term_acc is not None and (account is None or account.currency == term_acc.currency):
                    mt5_type = mt5.ORDER_TYPE_BUY if is_buy else mt5.ORDER_TYPE_SELL
                    calc_m = mt5.order_calc_margin(mt5_type, symbol_info.symbol, volume, price)
                    if calc_m is not None:
                        return float(calc_m)
            except Exception:
                pass

        base_margin = symbol_info.margin_required if symbol_info.margin_required > 0 else 1000.0
        return base_margin * volume

    def solve_timeframe_feasibility(
        self,
        account: AccountInfo,
        symbol_info: SymbolInfo,
        order_type: OrderType,
        entry_price: float,
        atr_price: float,
        structural_stop_price: float,
        timeframe: str,
        trading_mode: str = "HOLD",
        spread_override_pts: Optional[float] = None,
        stops_level_pts: Optional[int] = None,
        freeze_level_pts: Optional[int] = None,
        expected_slippage_pts: Optional[float] = None,
        commission_per_lot: Optional[float] = None,
        risk_percent_override: Optional[float] = None,
    ) -> Tuple[FeasibilityWindow, Dict[str, Any]]:
        """
        Solves the feasibility window [min_stop, max_stop] on a single timeframe.
        """
        self.tier_manager.reload_config()
        self.reload_config()

        capital_base = min(account.balance, account.equity)
        tier_def, _, _ = self.tier_manager.evaluate_account(account)
        target_risk_pct = risk_percent_override if risk_percent_override is not None else tier_def.target_risk_pct
        ceiling_risk_pct = tier_def.absolute_ceiling_pct

        # 1. Risk budget
        risk_budget = capital_base * (target_risk_pct / 100.0)

        # Point and tick definitions
        pt = symbol_info.point if symbol_info.point > 0 else 0.00001
        min_lot = symbol_info.min_lot if symbol_info.min_lot > 0 else 0.01

        # 2. loss_per_price_unit at minimum lot
        loss_per_price_unit_min = self.calculate_loss_per_price_unit(
            symbol_info, order_type, entry_price, volume=min_lot, account=account
        )

        # 3. max_stop = risk_budget / loss_per_price_unit_at_min_lot
        if loss_per_price_unit_min > 0 and risk_budget > 0:
            max_stop_price = risk_budget / loss_per_price_unit_min
        else:
            max_stop_price = 0.0

        max_stop_points = max_stop_price / pt if pt > 0 else 0.0
        max_stop_money = risk_budget

        # 4. Total transaction cost in price
        cur_spread_pts = spread_override_pts if spread_override_pts is not None else float(symbol_info.spread)
        spread_price = cur_spread_pts * pt

        if commission_per_lot is not None:
            comm_per_lot = commission_per_lot
        elif account and account.is_cent_account:
            comm_per_lot = 0.0
        else:
            comm_per_lot = float(self.config.get("default_commission_per_lot", 6.0))
        comm_at_min_lot = comm_per_lot * min_lot
        comm_price = comm_at_min_lot / loss_per_price_unit_min if loss_per_price_unit_min > 0 else 0.0

        if expected_slippage_pts is not None:
            slip_pts = expected_slippage_pts
        elif self.tca_engine and self.tca_engine.records:
            summary = self.tca_engine.generate_summary()
            slip_pts = max(0.0, summary.get("mean_slippage_points", 0.0))
        else:
            slip_pts = float(self.config.get("default_expected_slippage_points", 2.0))
        slippage_price = slip_pts * pt

        total_cost_price = spread_price + comm_price + slippage_price

        # 5. min_stop components
        broker_buf_pts = float(self.config.get("broker_buffer_points", 5.0))
        stops_pts = float(stops_level_pts) if stops_level_pts is not None else 0.0
        freeze_pts = float(freeze_level_pts) if freeze_level_pts is not None else 0.0
        stop_component_a = (stops_pts + freeze_pts + broker_buf_pts) * pt

        cost_frac_map = self.config.get("max_cost_fraction", {"SCALP": 0.08, "HOLD": 0.05, "DEFAULT": 0.05})
        cost_frac = float(cost_frac_map.get(trading_mode.upper(), cost_frac_map.get("DEFAULT", 0.05)))
        stop_component_b = (total_cost_price / cost_frac) if cost_frac > 0 else 0.0

        noise_map = self.config.get("noise_multiplier", {"SCALP": 1.5, "HOLD": 2.0, "DEFAULT": 1.8})
        noise_mult = float(noise_map.get(trading_mode.upper(), noise_map.get("DEFAULT", 1.8)))
        stop_component_c = noise_mult * atr_price

        stop_component_d = abs(structural_stop_price)

        min_stop_price = max(stop_component_a, stop_component_b, stop_component_c, stop_component_d)
        min_stop_points = min_stop_price / pt if pt > 0 else 0.0
        min_stop_money = min_stop_price * loss_per_price_unit_min

        # 6. Feasibility check
        is_feasible = (min_stop_price <= max_stop_price) and (risk_budget > 0)
        rejection_reason = None
        if not is_feasible:
            rejection_reason = (
                f"min_stop ({min_stop_points:.1f} pts / ${min_stop_money:.2f}) > "
                f"max_stop ({max_stop_points:.1f} pts / ${max_stop_money:.2f}) "
                f"[Budget: ${risk_budget:.2f} ({target_risk_pct:.2f}%)]"
            )

        window = FeasibilityWindow(
            min_stop_price=min_stop_price,
            max_stop_price=max_stop_price,
            min_stop_points=min_stop_points,
            max_stop_points=max_stop_points,
            min_stop_money=min_stop_money,
            max_stop_money=max_stop_money,
            is_feasible=is_feasible,
            rejection_reason=rejection_reason,
        )

        metadata = {
            "timeframe": timeframe,
            "cost_price": total_cost_price,
            "cost_in_money": total_cost_price * loss_per_price_unit_min,
            "spread_points": cur_spread_pts,
            "atr_price": atr_price,
            "atr_points": atr_price / pt if pt > 0 else 0.0,
            "noise_mult": noise_mult,
            "cost_frac": cost_frac,
            "component_a_broker": stop_component_a / pt if pt > 0 else 0.0,
            "component_b_cost": stop_component_b / pt if pt > 0 else 0.0,
            "component_c_noise": stop_component_c / pt if pt > 0 else 0.0,
            "component_d_struct": stop_component_d / pt if pt > 0 else 0.0,
            "loss_per_price_unit_min": loss_per_price_unit_min,
        }

        return window, metadata

    def solve_multi_timeframe(
        self,
        account: AccountInfo,
        symbol_info: SymbolInfo,
        order_type: OrderType,
        entry_price: float,
        timeframe_atrs: Dict[str, float],
        structural_stop_price: float,
        preferred_timeframe: str = "M5",
        trading_mode: str = "HOLD",
        spread_override_pts: Optional[float] = None,
        stops_level_pts: Optional[int] = None,
        freeze_level_pts: Optional[int] = None,
        expected_slippage_pts: Optional[float] = None,
        commission_per_lot: Optional[float] = None,
        risk_percent_override: Optional[float] = None,
    ) -> Tuple[str, FeasibilityWindow, List[Dict[str, Any]]]:
        """
        Iterates candidate timeframes (M5, M15, M30, H1, H4) in order of suitability.
        Selects the lowest viable timeframe where cost ratio is met and window opens.
        """
        candidate_tfs = self.config.get("candidate_timeframes", ["M5", "M15", "M30", "H1", "H4"])
        if preferred_timeframe in candidate_tfs:
            ordered_tfs = [preferred_timeframe] + [t for t in candidate_tfs if t != preferred_timeframe]
        else:
            ordered_tfs = list(candidate_tfs)

        evaluations: List[Dict[str, Any]] = []
        chosen_tf = ordered_tfs[0]
        chosen_window: Optional[FeasibilityWindow] = None

        pt = symbol_info.point if symbol_info.point > 0 else 0.00001
        for tf in ordered_tfs:
            # Look up ATR for timeframe, or extrapolate if not explicitly passed
            if tf in timeframe_atrs and timeframe_atrs[tf] > 0:
                atr_tf = timeframe_atrs[tf]
            else:
                base_atr = timeframe_atrs.get(preferred_timeframe, 40.0 * pt)
                # Scale ATR roughly by sqrt of timeframe minutes ratio
                scale_map = {"M5": 1.0, "M15": 1.73, "M30": 2.45, "H1": 3.46, "H4": 6.92}
                ratio = scale_map.get(tf, 1.0) / scale_map.get(preferred_timeframe, 1.0)
                atr_tf = base_atr * ratio

            win, meta = self.solve_timeframe_feasibility(
                account=account,
                symbol_info=symbol_info,
                order_type=order_type,
                entry_price=entry_price,
                atr_price=atr_tf,
                structural_stop_price=structural_stop_price,
                timeframe=tf,
                trading_mode=trading_mode,
                spread_override_pts=spread_override_pts,
                stops_level_pts=stops_level_pts,
                freeze_level_pts=freeze_level_pts,
                expected_slippage_pts=expected_slippage_pts,
                commission_per_lot=commission_per_lot,
                risk_percent_override=risk_percent_override,
            )

            reach_mult = float(self.config.get("reach_multiplier", 5.0))
            min_rr_floor = float(self.config.get("min_rr_floor", 1.5))
            reachable_rr = (reach_mult * atr_tf) / win.min_stop_price if win.min_stop_price > 0 else 0.0

            default_rr = float(self.config.get("default_rr", 2.0))

            eval_dict = {
                "timeframe": tf,
                "feasible": win.is_feasible,
                "reachable_rr": round(reachable_rr, 2),
                "min_stop_pts": win.min_stop_points,
                "max_stop_pts": win.max_stop_points,
                "min_stop_money": win.min_stop_money,
                "cost_money": meta["cost_in_money"],
                "rejection_reason": win.rejection_reason,
            }
            evaluations.append(eval_dict)

            # Active-trading selection: lowest timeframe where window is feasible and reachable R:R meets target RR
            if win.is_feasible and reachable_rr >= default_rr and chosen_window is None:
                chosen_tf = tf
                chosen_window = win

        # Fallback 1: if none met default_rr, pick lowest timeframe meeting min_rr_floor
        if chosen_window is None:
            for tf_ev in evaluations:
                if tf_ev["feasible"] and tf_ev["reachable_rr"] >= min_rr_floor:
                    chosen_tf = tf_ev["timeframe"]
                    atr_tf = timeframe_atrs.get(chosen_tf, 40.0 * pt)
                    chosen_window, _ = self.solve_timeframe_feasibility(
                        account=account,
                        symbol_info=symbol_info,
                        order_type=order_type,
                        entry_price=entry_price,
                        atr_price=atr_tf,
                        structural_stop_price=structural_stop_price,
                        timeframe=chosen_tf,
                        trading_mode=trading_mode,
                        spread_override_pts=spread_override_pts,
                        stops_level_pts=stops_level_pts,
                        freeze_level_pts=freeze_level_pts,
                        expected_slippage_pts=expected_slippage_pts,
                        commission_per_lot=commission_per_lot,
                        risk_percent_override=risk_percent_override,
                    )
                    break

        # Fallback 2: if no timeframe met R:R floor, choose first feasible timeframe
        if chosen_window is None:
            for tf_ev in evaluations:
                if tf_ev["feasible"]:
                    chosen_tf = tf_ev["timeframe"]
                    atr_tf = timeframe_atrs.get(chosen_tf, 40.0 * pt)
                    chosen_window, _ = self.solve_timeframe_feasibility(
                        account=account,
                        symbol_info=symbol_info,
                        order_type=order_type,
                        entry_price=entry_price,
                        atr_price=atr_tf,
                        structural_stop_price=structural_stop_price,
                        timeframe=chosen_tf,
                        trading_mode=trading_mode,
                        spread_override_pts=spread_override_pts,
                        stops_level_pts=stops_level_pts,
                        freeze_level_pts=freeze_level_pts,
                        expected_slippage_pts=expected_slippage_pts,
                        commission_per_lot=commission_per_lot,
                        risk_percent_override=risk_percent_override,
                    )
                    break

        if chosen_window is None:
            # None feasible across all candidate timeframes
            chosen_tf = ordered_tfs[0]
            chosen_window, _ = self.solve_timeframe_feasibility(
                account=account,
                symbol_info=symbol_info,
                order_type=order_type,
                entry_price=entry_price,
                atr_price=timeframe_atrs.get(chosen_tf, 40.0 * pt),
                structural_stop_price=structural_stop_price,
                timeframe=chosen_tf,
                trading_mode=trading_mode,
                spread_override_pts=spread_override_pts,
                stops_level_pts=stops_level_pts,
                freeze_level_pts=freeze_level_pts,
                expected_slippage_pts=expected_slippage_pts,
                commission_per_lot=commission_per_lot,
                risk_percent_override=risk_percent_override,
            )

        return chosen_tf, chosen_window, evaluations

    def compute_balance_needed(
        self,
        symbol_info: SymbolInfo,
        order_type: OrderType,
        entry_price: float,
        timeframe_atrs: Dict[str, float],
        target_risk_pct: float,
        trading_mode: str = "HOLD",
        spread_override_pts: Optional[float] = None,
        stops_level_pts: Optional[int] = None,
        freeze_level_pts: Optional[int] = None,
        expected_slippage_pts: Optional[float] = None,
        commission_per_lot: Optional[float] = None,
        account: Optional[AccountInfo] = None,
    ) -> float:
        """
        Computes the minimum balance at which the feasibility window opens
        at the cheapest timeframe, at the tier's target risk %.
        """
        if target_risk_pct <= 0:
            return float("inf")

        candidate_tfs = self.config.get("candidate_timeframes", ["M5", "M15", "M30", "H1", "H4"])
        pt = symbol_info.point if symbol_info.point > 0 else 0.00001
        min_lot = symbol_info.min_lot if symbol_info.min_lot > 0 else 0.01

        loss_per_price_unit_min = self.calculate_loss_per_price_unit(
            symbol_info, order_type, entry_price, volume=min_lot, account=account
        )

        min_stop_moneys = []
        for tf in candidate_tfs:
            atr_tf = timeframe_atrs.get(tf, 40.0 * pt)
            # Dummy account to compute min_stop
            dummy_acc = account or AccountInfo(balance=10000.0, equity=10000.0)
            win, _ = self.solve_timeframe_feasibility(
                account=dummy_acc,
                symbol_info=symbol_info,
                order_type=order_type,
                entry_price=entry_price,
                atr_price=atr_tf,
                structural_stop_price=0.0,
                timeframe=tf,
                trading_mode=trading_mode,
                spread_override_pts=spread_override_pts,
                stops_level_pts=stops_level_pts,
                freeze_level_pts=freeze_level_pts,
                expected_slippage_pts=expected_slippage_pts,
                commission_per_lot=commission_per_lot,
                risk_percent_override=target_risk_pct,
            )
            min_stop_moneys.append(win.min_stop_money)

        cheapest_min_stop_money = min(min_stop_moneys) if min_stop_moneys else (50.0 * pt * loss_per_price_unit_min)
        balance_needed = cheapest_min_stop_money / (target_risk_pct / 100.0)
        return max(5.0, balance_needed)

    # -------------------------------------------------------------------------
    # STAGE 2: Dynamic Reward-to-Risk & Expectancy Filter
    # -------------------------------------------------------------------------
    def evaluate_dynamic_rr(
        self,
        chosen_stop_price: float,
        total_cost_price: float,
        atr_price: float,
        target_tp_price: Optional[float] = None,
        hit_rate: Optional[float] = None,
        sample_count: int = 0,
    ) -> DynamicRRResult:
        """
        Calculates required R:R based on win-rate lower confidence bound + safety margin + cost,
        verifies reachability ceiling (TP <= reach_mult * ATR), positive EV, and min_rr_floor.
        """
        min_rr_floor = float(self.config.get("min_rr_floor", 1.5))
        default_rr = float(self.config.get("default_rr", 2.0))
        safety_margin_rr = float(self.config.get("safety_margin_rr", 0.3))
        reach_mult = float(self.config.get("reach_multiplier", 4.0))

        cost_in_r = (total_cost_price / chosen_stop_price) if chosen_stop_price > 0 else 0.0

        # Win rate lower confidence bound (Wilson score interval)
        if hit_rate is not None and sample_count >= 30 and 0.0 < hit_rate < 1.0:
            z = 1.645  # 90% confidence
            p_hat = hit_rate
            n = float(sample_count)
            denominator = 1.0 + (z**2 / n)
            center = p_hat + (z**2 / (2 * n))
            margin = z * math.sqrt((p_hat * (1 - p_hat) / n) + (z**2 / (4 * n**2)))
            p_lcb = max(0.05, (center - margin) / denominator)
            # Break-even R:R = (1 - p) / p
            be_rr = (1.0 - p_lcb) / p_lcb
            required_rr = max(min_rr_floor, be_rr + safety_margin_rr + cost_in_r)
            effective_p = hit_rate
        else:
            required_rr = max(min_rr_floor, default_rr + cost_in_r)
            effective_p = hit_rate if hit_rate is not None and hit_rate > 0 else 0.50

        # Reachable TP ceiling: reach_mult x ATR
        reachable_tp_distance = reach_mult * atr_price
        reachable_rr = reachable_tp_distance / chosen_stop_price if chosen_stop_price > 0 else 0.0

        if target_tp_price is not None and target_tp_price > 0:
            actual_tp_distance = min(target_tp_price, reachable_tp_distance)
            actual_rr = actual_tp_distance / chosen_stop_price if chosen_stop_price > 0 else 0.0
        else:
            actual_tp_distance = reachable_tp_distance
            actual_rr = reachable_rr

        # Expected Value in R: p * RR - (1 - p) * 1.0 - cost_in_R
        ev_in_r = (effective_p * actual_rr) - ((1.0 - effective_p) * 1.0) - cost_in_r

        is_approved = True
        rejection_reason = None

        if actual_rr < min_rr_floor:
            is_approved = False
            rejection_reason = f"Actual R:R ({actual_rr:.2f}) is below min floor ({min_rr_floor:.2f})"
        elif actual_rr < required_rr:
            is_approved = False
            rejection_reason = f"Reachable R:R ({actual_rr:.2f}) < Required R:R ({required_rr:.2f}) [Cost={cost_in_r:.3f}R]"
        elif ev_in_r <= 0:
            is_approved = False
            rejection_reason = f"Expected Value in R ({ev_in_r:.3f}R) is non-positive after costs"

        return DynamicRRResult(
            required_rr=required_rr,
            min_rr_floor=min_rr_floor,
            reachable_tp_price=actual_tp_distance,
            reachable_tp_points=0.0,
            reachable_rr=actual_rr,
            cost_in_r=cost_in_r,
            expected_value_in_r=ev_in_r,
            is_approved=is_approved,
            rejection_reason=rejection_reason,
        )

    # -------------------------------------------------------------------------
    # STAGE 3: Adaptation, Profile Management & Fit Scoring
    # -------------------------------------------------------------------------
    def update_instrument_profile(
        self,
        symbol: str,
        rates_by_timeframe: Dict[str, List[Dict[str, Any]]],
        open_positions_symbols: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """
        Generates and caches an instrument profile:
        - spread to ATR ratio by timeframe and hour
        - typical session liquidity
        - correlation group
        - cost per unit risk
        """
        profile = self.profiles.get(symbol, {
            "symbol": symbol,
            "spread_to_atr_by_tf": {},
            "spread_to_atr_by_hour": {},
            "session_liquidity": {},
            "correlation_group": self._assign_correlation_group(symbol),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        })

        for tf, candles in rates_by_timeframe.items():
            if not candles:
                continue
            spreads = [c.get("spread", 0) for c in candles if "spread" in c]
            ranges = [abs(c.get("high", 0) - c.get("low", 0)) for c in candles]
            volumes = [c.get("tick_volume", 0) for c in candles]

            mean_spread = float(sum(spreads) / len(spreads)) if spreads else 0.0
            mean_range = float(sum(ranges) / len(ranges)) if ranges else 1.0

            ratio = (mean_spread / mean_range) if mean_range > 0 else 0.10
            profile["spread_to_atr_by_tf"][tf] = round(ratio, 4)

            # Hour breakdown
            for c in candles:
                t = c.get("time", 0)
                if isinstance(t, (int, float)):
                    hour = datetime.fromtimestamp(t, tz=timezone.utc).hour
                    if str(hour) not in profile["spread_to_atr_by_hour"]:
                        profile["spread_to_atr_by_hour"][str(hour)] = []
                    spr = c.get("spread", mean_spread)
                    rng = abs(c.get("high", 0) - c.get("low", 0))
                    if rng > 0:
                        profile["spread_to_atr_by_hour"][str(hour)].append(spr / rng)

            # Session liquidity
            profile["session_liquidity"][tf] = int(sum(volumes) / len(volumes)) if volumes else 100

        # Compress hour averages
        for h, ratios in list(profile["spread_to_atr_by_hour"].items()):
            if isinstance(ratios, list) and ratios:
                profile["spread_to_atr_by_hour"][h] = round(float(sum(ratios) / len(ratios)), 4)

        profile["updated_at"] = datetime.now(timezone.utc).isoformat()
        self.profiles[symbol] = profile
        self._save_cached_profiles()
        return profile

    def _assign_correlation_group(self, symbol: str) -> str:
        s = symbol.upper()
        if "XAU" in s or "GOLD" in s or "XAG" in s:
            return "METALS"
        if "BTC" in s or "ETH" in s or "CRYPTO" in s:
            return "CRYPTO"
        if any(idx in s for idx in ["30", "100", "500", "NAS", "SPX", "DOW", "GER40"]):
            return "INDICES"
        if "JPY" in s:
            return "FOREX_JPY"
        return "FOREX_MAJORS"

    def is_time_favourable(self, symbol: str, current_time_utc: Optional[datetime] = None) -> Tuple[bool, str]:
        """
        Checks rollover window and spread-to-ATR favourability.
        """
        now = current_time_utc or datetime.now(timezone.utc)
        rollover = self.config.get("rollover_window_utc", {
            "start_hour": 21, "start_minute": 55, "end_hour": 22, "end_minute": 15
        })

        # Check rollover window
        h, m = now.hour, now.minute
        s_h, s_m = rollover.get("start_hour", 21), rollover.get("start_minute", 55)
        e_h, e_m = rollover.get("end_hour", 22), rollover.get("end_minute", 15)

        current_min = h * 60 + m
        start_min = s_h * 60 + s_m
        end_min = e_h * 60 + e_m

        if start_min <= current_min <= end_min:
            return False, f"Inside broker rollover / swap window ({s_h:02d}:{s_m:02d}-{e_h:02d}:{e_m:02d} UTC)"

        # Check hour spread ratio
        prof = self.profiles.get(symbol, {})
        hour_map = prof.get("spread_to_atr_by_hour", {})
        h_ratio = hour_map.get(str(h))
        max_ratio = float(self.config.get("spread_to_atr_max_ratio", 0.20))
        if h_ratio is not None and h_ratio > max_ratio * 1.5:
            return False, f"Spread-to-ATR ratio ({h_ratio:.2f}) excessively elevated at hour {h:02d} UTC"

        return True, "Trading window favourable"

    def compute_fit_score(
        self,
        decision: RiskFitDecision,
        open_positions: Optional[List[Dict[str, Any]]] = None
    ) -> float:
        """
        Ranks symbols by fit score:
        - Feasibility & window headroom (35%)
        - Cost efficiency / low friction (25%)
        - Session liquidity (20%)
        - Low correlation to currently open positions (20%)
        """
        weights = self.config.get("fit_score_weights", {
            "feasibility": 0.35, "cost_efficiency": 0.25, "liquidity": 0.20, "low_correlation": 0.20
        })

        if not decision.is_feasible:
            return 0.0

        # Feasibility headroom: how comfortably min_stop fits in max_stop
        headroom = min(1.0, (decision.window.max_stop_price / max(1e-7, decision.window.min_stop_price)) / 3.0)
        feasibility_score = headroom * 100.0

        # Cost efficiency: lower cost in R is better
        cost_score = max(0.0, 100.0 * (1.0 - (decision.cost_in_r / 0.10)))

        # Liquidity score from profile
        prof = self.profiles.get(decision.symbol, {})
        liq_map = prof.get("session_liquidity", {})
        tf_liq = liq_map.get(decision.timeframe, 150)
        liquidity_score = min(100.0, (tf_liq / 300.0) * 100.0)

        # Correlation score: penalty if open positions are in same correlation group
        corr_group = self._assign_correlation_group(decision.symbol)
        open_groups = []
        if open_positions:
            for p in open_positions:
                sym = p.get("symbol", "")
                open_groups.append(self._assign_correlation_group(sym))

        group_overlap_count = open_groups.count(corr_group)
        corr_score = max(20.0, 100.0 - (group_overlap_count * 40.0))

        fit_score = (
            (weights["feasibility"] * feasibility_score) +
            (weights["cost_efficiency"] * cost_score) +
            (weights["liquidity"] * liquidity_score) +
            (weights["low_correlation"] * corr_score)
        )
        return round(fit_score, 2)

    # -------------------------------------------------------------------------
    # MAIN EVALUATION ENTRYPOINT
    # -------------------------------------------------------------------------
    def evaluate_symbol(
        self,
        account: AccountInfo,
        symbol_info: SymbolInfo,
        order_type: OrderType,
        entry_price: float,
        structural_stop_price: float,
        timeframe_atrs: Dict[str, float],
        target_tp_price: Optional[float] = None,
        preferred_timeframe: str = "M5",
        trading_mode: str = "HOLD",
        spread_override_pts: Optional[float] = None,
        stops_level_pts: Optional[int] = None,
        freeze_level_pts: Optional[int] = None,
        expected_slippage_pts: Optional[float] = None,
        commission_per_lot: Optional[float] = None,
        risk_percent_override: Optional[float] = None,
        hit_rate: Optional[float] = None,
        sample_count: int = 0,
        open_positions: Optional[List[Dict[str, Any]]] = None,
    ) -> RiskFitDecision:
        """
        Unified evaluation pipeline across Stages 1, 2, and 3.
        """
        self.tier_manager.reload_config()
        self.reload_config()

        capital_base = min(account.balance, account.equity)
        tier_def, _, _ = self.tier_manager.evaluate_account(account)
        tier_name = tier_def.name
        target_risk_pct = risk_percent_override if risk_percent_override is not None else tier_def.target_risk_pct
        ceiling_risk_pct = tier_def.absolute_ceiling_pct

        pt = symbol_info.point if symbol_info.point > 0 else 0.00001
        min_lot = symbol_info.min_lot if symbol_info.min_lot > 0 else 0.01
        step_lot = symbol_info.lot_step if symbol_info.lot_step > 0 else 0.01
        broker_max_lot = symbol_info.max_lot if symbol_info.max_lot > 0 else 100.0

        # Stage 1: Solve feasibility across timeframes
        chosen_tf, window, eval_list = self.solve_multi_timeframe(
            account=account,
            symbol_info=symbol_info,
            order_type=order_type,
            entry_price=entry_price,
            timeframe_atrs=timeframe_atrs,
            structural_stop_price=structural_stop_price,
            preferred_timeframe=preferred_timeframe,
            trading_mode=trading_mode,
            spread_override_pts=spread_override_pts,
            stops_level_pts=stops_level_pts,
            freeze_level_pts=freeze_level_pts,
            expected_slippage_pts=expected_slippage_pts,
            commission_per_lot=commission_per_lot,
            risk_percent_override=risk_percent_override,
        )

        atr_chosen = timeframe_atrs.get(chosen_tf, 40.0 * pt)
        balance_needed = self.compute_balance_needed(
            symbol_info=symbol_info,
            order_type=order_type,
            entry_price=entry_price,
            timeframe_atrs=timeframe_atrs,
            target_risk_pct=target_risk_pct,
            trading_mode=trading_mode,
            spread_override_pts=spread_override_pts,
            stops_level_pts=stops_level_pts,
            freeze_level_pts=freeze_level_pts,
            expected_slippage_pts=expected_slippage_pts,
            commission_per_lot=commission_per_lot,
            account=account,
        )

        risk_budget = capital_base * (target_risk_pct / 100.0)

        # Infeasible branch
        if not window.is_feasible:
            # Check micro-account opt-in exception
            loss_per_price_unit_min = self.calculate_loss_per_price_unit(
                symbol_info, order_type, entry_price, volume=min_lot, account=account
            )
            min_lot_risk_money = window.min_stop_price * loss_per_price_unit_min
            min_lot_risk_pct = (min_lot_risk_money / capital_base) * 100.0 if capital_base > 0 else 100.0

            can_micro_exception = (
                self.tier_manager.acknowledged_high_risk_microaccount and
                self.tier_manager.micro_account_min_lot_allowed and
                min_lot_risk_pct <= ceiling_risk_pct and
                capital_base >= 5.0
            )

            if not can_micro_exception:
                status = "BLOCKED_BY_BALANCE"
                rejection_reason = (
                    f"BLOCKED_BY_BALANCE: min_stop ({window.min_stop_points:.1f} pts / ${window.min_stop_money:.2f}) > "
                    f"max_stop ({window.max_stop_points:.1f} pts / ${window.max_stop_money:.2f}). "
                    f"Balance needed: ${balance_needed:.2f} (Current: ${capital_base:.2f})"
                )
                dummy_rr = DynamicRRResult(
                    required_rr=2.0, min_rr_floor=1.5, reachable_tp_price=0.0,
                    reachable_tp_points=0.0, reachable_rr=0.0, cost_in_r=0.0,
                    expected_value_in_r=0.0, is_approved=False, rejection_reason=rejection_reason
                )
                log_line = (
                    f"[RISK_FIT] [{symbol_info.symbol}] {status} | TF={chosen_tf} | Bal=${capital_base:.2f} | "
                    f"Window=[{window.min_stop_points:.1f}, {window.max_stop_points:.1f}] pts | "
                    f"Needed=${balance_needed:.2f} | Reason={rejection_reason}"
                )
                logger.info(log_line)
                return RiskFitDecision(
                    symbol=symbol_info.symbol,
                    status=status,
                    timeframe=chosen_tf,
                    balance=account.balance,
                    equity=account.equity,
                    tier_name=tier_name,
                    risk_budget=risk_budget,
                    target_risk_pct=target_risk_pct,
                    ceiling_risk_pct=ceiling_risk_pct,
                    window=window,
                    chosen_stop_price=window.min_stop_price,
                    chosen_stop_points=window.min_stop_points,
                    chosen_stop_money=window.min_stop_money,
                    lot=0.0,
                    raw_lot=0.0,
                    real_risk_money=0.0,
                    real_risk_pct=0.0,
                    rr_result=dummy_rr,
                    cost_in_r=0.0,
                    expected_value_in_r=0.0,
                    balance_needed=balance_needed,
                    fit_score=0.0,
                    is_feasible=False,
                    is_micro_exception=False,
                    log_line=log_line,
                    rejection_reason=rejection_reason,
                    candidate_evaluations=eval_list,
                )

        # Window is feasible (or micro exception permitted)
        # Choose stop from structure and clamp strictly into [min_stop, max_stop]
        struct_stop = abs(structural_stop_price)
        if struct_stop <= 0:
            struct_stop = window.min_stop_price

        chosen_stop_price = max(window.min_stop_price, min(window.max_stop_price, struct_stop))
        chosen_stop_points = chosen_stop_price / pt if pt > 0 else 0.0

        # Loss per 1.0 lot at chosen stop
        loss_per_price_unit_1lot = self.calculate_loss_per_price_unit(
            symbol_info, order_type, entry_price, volume=1.0, account=account
        )
        loss_per_lot_at_chosen_stop = chosen_stop_price * loss_per_price_unit_1lot

        # Lot calculation: floor(risk_budget / loss_per_lot / step) * step
        raw_lot = (risk_budget / loss_per_lot_at_chosen_stop) if loss_per_lot_at_chosen_stop > 0 else 0.0
        steps = math.floor(round(raw_lot / step_lot, 6))
        quantized_lot = round(steps * step_lot, 4)

        # Dynamic max lot cap based on capital
        tier_lot_cap = 0.5
        if capital_base < 500.0:
            tier_lot_cap = min(tier_lot_cap, min_lot * 3.0)
        lot_cap = min(broker_max_lot, tier_lot_cap)

        final_lot = min(quantized_lot, lot_cap)
        is_micro_exc = False

        if final_lot < min_lot:
            # Check micro account opt-in exception
            min_lot_loss = chosen_stop_price * self.calculate_loss_per_price_unit(
                symbol_info, order_type, entry_price, volume=min_lot, account=account
            )
            min_lot_pct = (min_lot_loss / capital_base) * 100.0 if capital_base > 0 else 100.0

            if (
                self.tier_manager.acknowledged_high_risk_microaccount and
                self.tier_manager.micro_account_min_lot_allowed and
                min_lot_pct <= ceiling_risk_pct and
                capital_base >= 5.0
            ):
                final_lot = min_lot
                is_micro_exc = True
            else:
                # Lot is zero
                final_lot = 0.0

        # Margin and free margin verification
        margin_req = self.calculate_margin_required(symbol_info, order_type, final_lot, entry_price, account=account)
        max_margin_pct = tier_def.max_margin_percent_of_free
        margin_limit = account.free_margin * (max_margin_pct / 100.0)

        if final_lot > 0 and margin_req > margin_limit and account.free_margin > 0:
            # Scale lot down to stay within margin limit
            max_allowed_lot_by_margin = math.floor(
                round((final_lot * (margin_limit / margin_req)) / step_lot, 6)
            ) * step_lot
            final_lot = max(0.0, max_allowed_lot_by_margin)

        # Real monetary risk and risk %
        loss_unit_at_final_lot = self.calculate_loss_per_price_unit(
            symbol_info, order_type, entry_price, volume=final_lot, account=account
        )
        real_risk_money = chosen_stop_price * loss_unit_at_final_lot
        real_risk_pct = (real_risk_money / capital_base) * 100.0 if capital_base > 0 else 0.0

        # Stage 2: Dynamic R:R
        cur_spread = spread_override_pts if spread_override_pts is not None else float(symbol_info.spread)
        if commission_per_lot is not None:
            comm_lot = commission_per_lot
        elif account and account.is_cent_account:
            comm_lot = 0.0
        else:
            comm_lot = float(self.config.get("default_commission_per_lot", 6.0))
        loss_unit_min = self.calculate_loss_per_price_unit(symbol_info, order_type, entry_price, volume=min_lot, account=account)
        comm_price = (comm_lot * min_lot) / loss_unit_min if loss_unit_min > 0 else 0.0
        slip_pts = expected_slippage_pts if expected_slippage_pts is not None else float(self.config.get("default_expected_slippage_points", 2.0))
        total_cost_price = (cur_spread * pt) + comm_price + (slip_pts * pt)

        rr_result = self.evaluate_dynamic_rr(
            chosen_stop_price=chosen_stop_price,
            total_cost_price=total_cost_price,
            atr_price=atr_chosen,
            target_tp_price=target_tp_price,
            hit_rate=hit_rate,
            sample_count=sample_count,
        )

        cost_in_r = rr_result.cost_in_r
        expected_value_in_r = rr_result.expected_value_in_r

        # Determine status
        rejection = None
        if final_lot <= 0:
            status = "BLOCKED_BY_BALANCE" if not is_micro_exc else "SKIPPED"
            rejection = f"Calculated volume {raw_lot:.4f} lots below min {min_lot:.2f}"
        elif not rr_result.is_approved:
            status = "SKIPPED"
            rejection = rr_result.rejection_reason
        elif is_micro_exc:
            status = "MARGINAL"
        else:
            status = "TRADABLE"

        decision = RiskFitDecision(
            symbol=symbol_info.symbol,
            status=status,
            timeframe=chosen_tf,
            balance=account.balance,
            equity=account.equity,
            tier_name=tier_name,
            risk_budget=risk_budget,
            target_risk_pct=target_risk_pct,
            ceiling_risk_pct=ceiling_risk_pct,
            window=window,
            chosen_stop_price=chosen_stop_price,
            chosen_stop_points=chosen_stop_points,
            chosen_stop_money=chosen_stop_price * loss_unit_min,
            lot=final_lot,
            raw_lot=raw_lot,
            real_risk_money=real_risk_money,
            real_risk_pct=real_risk_pct,
            rr_result=rr_result,
            cost_in_r=cost_in_r,
            expected_value_in_r=expected_value_in_r,
            balance_needed=balance_needed,
            fit_score=0.0,
            is_feasible=window.is_feasible,
            is_micro_exception=is_micro_exc,
            rejection_reason=rejection,
            candidate_evaluations=eval_list,
        )

        decision.fit_score = self.compute_fit_score(decision, open_positions)

        log_line = (
            f"[RISK_FIT] [{symbol_info.symbol}] {status} | TF={chosen_tf} | Stop={chosen_stop_points:.1f} pts "
            f"(${decision.chosen_stop_money:.2f}) | Lot={final_lot:.2f} | Risk={real_risk_pct:.2f}% (${real_risk_money:.2f}) | "
            f"Window=[{window.min_stop_points:.1f}, {window.max_stop_points:.1f}] pts | "
            f"RR={rr_result.reachable_rr:.2f} | Cost={cost_in_r:.3f}R | EV={expected_value_in_r:.3f}R | "
            f"Score={decision.fit_score:.1f}"
        )
        if rejection:
            log_line += f" | Rejection: {rejection}"
        decision.log_line = log_line
        logger.info(log_line)

        return decision

    # -------------------------------------------------------------------------
    # STAGE 4: Visibility & Reporting
    # -------------------------------------------------------------------------
    def format_fit_report(self, decisions: List[RiskFitDecision]) -> str:
        """
        Formats comprehensive /fit report with exact units and status.
        """
        if not decisions:
            return "ℹ️ No symbol evaluations available."

        d0 = decisions[0]
        lines = [
            f"🎯 <b>UNIVERSAL RISK FIT ENGINE REPORT</b>",
            f"💰 <b>Capital:</b> <code>${d0.balance:.2f}</code> (Equity: <code>${d0.equity:.2f}</code>) | <b>Tier:</b> <code>{d0.tier_name}</code>",
            f"🛡️ <b>Target Risk:</b> <code>{d0.target_risk_pct:.2f}%</code> (Cap: <code>{d0.ceiling_risk_pct:.2f}%</code>) | <b>Budget:</b> <code>${d0.risk_budget:.2f}</code>",
            "",
            "📊 <b>PER-SYMBOL FEASIBILITY & RISK SIZING:</b>"
        ]

        # Sort: TRADABLE first (by fit_score desc), then MARGINAL, then BLOCKED_BY_BALANCE
        status_priority = {"TRADABLE": 0, "MARGINAL": 1, "SKIPPED": 2, "BLOCKED_BY_BALANCE": 3}
        sorted_decisions = sorted(
            decisions,
            key=lambda d: (status_priority.get(d.status, 9), -d.fit_score)
        )

        for d in sorted_decisions:
            if d.status == "TRADABLE":
                icon = "✅"
            elif d.status == "MARGINAL":
                icon = "⚠️"
            else:
                icon = "⛔"

            lines.append(f"{icon} <b>{d.symbol}</b> [{d.status}] (TF: <code>{d.timeframe}</code>, FitScore: <code>{d.fit_score:.1f}</code>):")
            lines.append(
                f"   • Stop Window: <code>[{d.window.min_stop_points:.1f}, {d.window.max_stop_points:.1f}] pts</code> "
                f"(<code>${d.window.min_stop_money:.2f}</code> - <code>${d.window.max_stop_money:.2f}</code>)"
            )
            lines.append(
                f"   • Sizing: Stop <code>{d.chosen_stop_points:.1f} pts</code> | Lot: <code>{d.lot:.2f}</code> | "
                f"Risk: <code>${d.real_risk_money:.2f}</code> (<code>{d.real_risk_pct:.2f}%</code>)"
            )
            lines.append(
                f"   • Expectancy: R:R <code>{d.rr_result.reachable_rr:.2f}</code> | "
                f"Cost: <code>{d.cost_in_r:.3f}R</code> | EV: <code>{d.expected_value_in_r:+.3f}R</code>"
            )
            if d.status == "BLOCKED_BY_BALANCE":
                lines.append(f"   ↳ <i>Blocked:</i> Needs balance of <code>${d.balance_needed:.2f}</code> (Deficit: <code>${max(0.0, d.balance_needed - d.balance):.2f}</code>)")
            elif d.rejection_reason:
                lines.append(f"   ↳ <i>Note:</i> {d.rejection_reason}")
            lines.append("")

        suggestions = self.format_suggestions(decisions)
        if suggestions:
            lines.append(suggestions)

        return "\n".join(lines)

    def format_suggestions(self, decisions: List[RiskFitDecision]) -> str:
        """
        Suggestions for the user: cheapest tradable instruments now,
        or cent account / deposit needed.
        """
        tradable = [d for d in decisions if d.status == "TRADABLE"]
        marginal = [d for d in decisions if d.status == "MARGINAL"]
        blocked = [d for d in decisions if d.status == "BLOCKED_BY_BALANCE"]

        if tradable:
            best = sorted(tradable, key=lambda d: -d.fit_score)[0]
            cheapest = sorted(tradable, key=lambda d: d.window.min_stop_money)[0]
            return (
                f"💡 <b>Recommendation:</b> Best fit is <code>{best.symbol}</code> (Score: {best.fit_score:.1f}). "
                f"Cheapest friction: <code>{cheapest.symbol}</code> (${cheapest.window.min_stop_money:.2f} min risk)."
            )
        elif marginal:
            return (
                "💡 <b>Recommendation:</b> No standard setups fit target risk. "
                f"Micro min-lot trading is permitted on: {', '.join([f'<code>{d.symbol}</code>' for d in marginal])}."
            )
        else:
            min_needed = min([d.balance_needed for d in blocked]) if blocked else 250.0
            deficit = max(0.0, min_needed - (decisions[0].balance if decisions else 0.0))
            return (
                f"💡 <b>Recommendation:</b> Current balance cannot safely absorb micro lots on these symbols.\n"
                f"   • Deposit needed: <code>+${deficit:.2f}</code> to reach <code>${min_needed:.2f}</code>.\n"
                f"   • Or switch to a <b>Cent Account (USC)</b> where <code>${decisions[0].balance if decisions else 0.0:.2f}</code> = "
                f"<code>{int((decisions[0].balance if decisions else 0.0) * 100):,} cents</code>."
            )

    def detect_state_changes(self, new_decisions: List[RiskFitDecision]) -> List[str]:
        """
        Detects transitions in symbol tradability (e.g. BLOCKED -> TRADABLE after deposit).
        Deduplicates repeat states.
        """
        notifications: List[str] = []
        for d in new_decisions:
            prev = self._previous_states.get(d.symbol)
            curr = d.status
            if prev is not None and prev != curr:
                if curr == "TRADABLE":
                    notifications.append(
                        f"🟢 <b>FIT ENGINE UPDATE:</b> <code>{d.symbol}</code> is now <b>TRADABLE</b> "
                        f"(TF: {d.timeframe}, MinStop: {d.window.min_stop_points:.1f} pts, Lot: {d.lot:.2f})"
                    )
                elif curr == "BLOCKED_BY_BALANCE":
                    notifications.append(
                        f"🔴 <b>FIT ENGINE UPDATE:</b> <code>{d.symbol}</code> is now <b>BLOCKED_BY_BALANCE</b> "
                        f"(Needs: ${d.balance_needed:.2f})"
                    )
                elif curr == "MARGINAL":
                    notifications.append(
                        f"🟡 <b>FIT ENGINE UPDATE:</b> <code>{d.symbol}</code> is now <b>MARGINAL</b> (Micro lot only)"
                    )
                else:
                    notifications.append(
                        f"🟡 <b>FIT ENGINE UPDATE:</b> <code>{d.symbol}</code> transitioned to <b>{curr}</b>"
                    )
            self._previous_states[d.symbol] = curr
        return notifications
