"""
Account Tier Manager & Hysteresis Controller.
Enforces balance-adaptive risk tiers from $5 upwards, hysteresis boundary buffers,
and hard risk ceilings.
"""

from __future__ import annotations
import os
import json
import logging
from dataclasses import dataclass
from typing import Dict, Optional, Tuple, Any

from src.core.contracts import AccountTier, AccountInfo

logger = logging.getLogger("AccountTierManager")

DEFAULT_TIERS_CONFIG_PATH = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "config", "account_tiers.json")
)
DEFAULT_STRATEGY_CONFIG_PATH = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "config", "strategy_config.json")
)


@dataclass(frozen=True)
class TierDefinition:
    name: str
    min_balance: float
    max_balance: float
    target_risk_pct: float
    absolute_ceiling_pct: float
    max_open_positions: int
    max_total_open_risk_pct: float
    max_trades_per_day: int
    daily_loss_limit_money: float
    daily_loss_limit_pct: float
    min_reward_to_risk: float
    min_expected_net_to_cost_ratio: float
    consecutive_loss_limit: int
    max_margin_percent_of_free: float
    trading_allowed: bool


class AccountTierManager:
    def __init__(
        self,
        tiers_config_path: str = DEFAULT_TIERS_CONFIG_PATH,
        strategy_config_path: str = DEFAULT_STRATEGY_CONFIG_PATH
    ):
        self.tiers_config_path = tiers_config_path
        self.strategy_config_path = strategy_config_path
        self._current_tier: AccountTier = AccountTier.STANDARD
        self.tiers: Dict[str, TierDefinition] = {}
        self.hysteresis_pct: float = 5.0
        self.acknowledged_high_risk_microaccount: bool = False
        self.micro_account_min_lot_allowed: bool = False
        self.reload_config()

    def reload_config(self) -> None:
        """Loads tier definitions and operator risk flags from disk."""
        if not os.path.exists(self.tiers_config_path):
            raise FileNotFoundError(f"Account tiers configuration missing: {self.tiers_config_path}")

        with open(self.tiers_config_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        self.hysteresis_pct = float(data.get("hysteresis_pct", 5.0))
        self.acknowledged_high_risk_microaccount = bool(data.get("acknowledged_high_risk_microaccount", False))

        raw_tiers = data.get("tiers", {})
        loaded_tiers = {}
        for name, t in raw_tiers.items():
            loaded_tiers[name] = TierDefinition(
                name=name,
                min_balance=float(t["min_balance"]),
                max_balance=float(t["max_balance"]),
                target_risk_pct=float(t["target_risk_pct"]),
                absolute_ceiling_pct=float(t["absolute_ceiling_pct"]),
                max_open_positions=int(t["max_open_positions"]),
                max_total_open_risk_pct=float(t["max_total_open_risk_pct"]),
                max_trades_per_day=int(t["max_trades_per_day"]),
                daily_loss_limit_money=float(t["daily_loss_limit_money"]),
                daily_loss_limit_pct=float(t["daily_loss_limit_pct"]),
                min_reward_to_risk=max(2.0, float(t["min_reward_to_risk"])),
                min_expected_net_to_cost_ratio=float(t["min_expected_net_to_cost_ratio"]),
                consecutive_loss_limit=int(t["consecutive_loss_limit"]),
                max_margin_percent_of_free=float(t.get("max_margin_percent_of_free", 50.0)),
                trading_allowed=bool(t["trading_allowed"])
            )
        self.tiers = loaded_tiers

        # Read strategy config for micro_account_min_lot_allowed flag
        if os.path.exists(self.strategy_config_path):
            try:
                with open(self.strategy_config_path, "r", encoding="utf-8") as f:
                    strat_data = json.load(f)
                flags = strat_data.get("feature_flags", {})
                self.micro_account_min_lot_allowed = bool(flags.get("micro_account_min_lot_allowed", False))
            except Exception as e:
                logger.warning(f"Could not read strategy config flags: {e}")

    @property
    def current_tier(self) -> AccountTier:
        return self._current_tier

    def get_tier_def(self, tier_name: str) -> TierDefinition:
        if tier_name in self.tiers:
            return self.tiers[tier_name]
        return self.tiers.get("STANDARD", list(self.tiers.values())[0])

    def evaluate_account(self, account: AccountInfo) -> Tuple[TierDefinition, bool, str]:
        """
        Evaluates capital base = min(balance, equity).
        Applies hysteresis buffer around boundaries to avoid flapping.
        Returns: (TierDefinition, changed: bool, change_message: str)
        """
        capital = min(account.balance, account.equity)
        current_name = self._current_tier.value
        old_tier = self._current_tier

        # Below floor rule: capital < $5.00 always disables trading immediately without hysteresis delay
        if capital < 5.0:
            new_tier = AccountTier.BELOW_FLOOR
        else:
            # Check if capital stays within current tier bounds considering hysteresis
            curr_def = self.tiers.get(current_name)
            stay_in_current = False
            if curr_def and curr_def.name != "BELOW_FLOOR":
                buf = (self.hysteresis_pct / 100.0)
                low_bound = curr_def.min_balance * (1.0 - buf)
                high_bound = curr_def.max_balance * (1.0 + buf)
                if low_bound <= capital < high_bound:
                    stay_in_current = True

            if stay_in_current:
                new_tier = self._current_tier
            else:
                # Find matching tier
                matched = None
                for name in ["BELOW_FLOOR", "MICRO", "MINI", "SMALL", "STANDARD", "LARGE"]:
                    t = self.tiers.get(name)
                    if t and t.min_balance <= capital < t.max_balance:
                        matched = AccountTier(name)
                        break
                new_tier = matched or (AccountTier.LARGE if capital >= 5000.0 else AccountTier.BELOW_FLOOR)

        changed = (new_tier != old_tier)
        if changed:
            self._current_tier = new_tier
            tier_def = self.get_tier_def(new_tier.value)
            msg = (
                f"[TIER_TRANSITION] Account moved from {old_tier.value} to {new_tier.value} "
                f"(Capital: ${capital:.2f} | TargetRisk: {tier_def.target_risk_pct}% | Ceiling: {tier_def.absolute_ceiling_pct}%)"
            )
            logger.info(msg)
            return tier_def, True, msg

        tier_def = self.get_tier_def(self._current_tier.value)
        return tier_def, False, ""
