"""
Stage 0A Position Sizing Matrix Generator.
Computes lot sizing, real risk %, margin requirement, and execution decision
for balances $5, $10, $25, $50, $100, $500, $5,000, $50,000 across all symbols,
with micro-account flags both OFF and ON.
"""

import sys
import os
import json
from datetime import datetime, timezone

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.core.contracts import AccountInfo, SymbolInfo, Timeframe
from src.risk.account_tier import AccountTierManager
from src.risk.position_sizing import PositionSizingEngine
from src.data.cost_model import CostModelEngine
from src.data.historical_loader import HistoricalDataLoader


def get_symbol_specs(symbols_cfg: dict, loader: HistoricalDataLoader) -> dict:
    specs = {}
    cost_engine = CostModelEngine(loader=loader)
    for sym in symbols_cfg.keys():
        profile = cost_engine.profile_symbol(sym)
        if profile:
            specs[sym] = SymbolInfo(
                symbol=profile.broker_symbol,
                digits=profile.digits,
                point=profile.point,
                tick_size=profile.tick_size,
                tick_value=profile.tick_value,
                contract_size=profile.contract_size,
                min_lot=profile.volume_min,
                max_lot=profile.volume_max,
                lot_step=profile.volume_step,
                margin_required=profile.margin_required_1_lot,
                spread=float(profile.current_spread_pts)
            )
        else:
            # Fallback to configured defaults
            c = symbols_cfg[sym]
            specs[sym] = SymbolInfo(
                symbol=sym,
                digits=c.get("digits", 5),
                point=c.get("point", 0.00001),
                tick_size=c.get("tick_size", 0.00001),
                tick_value=c.get("tick_value", 1.0),
                contract_size=c.get("contract_size", 100000.0),
                min_lot=c.get("min_lot", 0.01),
                max_lot=c.get("max_lot", 100.0),
                lot_step=c.get("lot_step", 0.01),
                margin_required=100.0,
                spread=10.0
            )
    return specs


def run_matrix(flag_micro: bool, symbols_cfg: dict, specs: dict) -> list:
    tier_mgr = AccountTierManager()
    tier_mgr.micro_account_min_lot_allowed = flag_micro
    tier_mgr.acknowledged_high_risk_microaccount = flag_micro

    sizing = PositionSizingEngine(tier_manager=tier_mgr)

    # Typical stop distances (points)
    sl_points = {
        "BTCUSD.m": 35000.0,  # $350 stop on BTC
        "EURUSD": 300.0,      # 30 pips
        "EURUSD.m": 300.0,    # 30 pips
        "GBPUSD": 350.0,      # 35 pips
        "GBPUSD.m": 350.0,    # 35 pips
        "USDJPY": 350.0,      # 35 pips
        "USDJPY.m": 350.0,    # 35 pips
        "XAUUSD": 2500.0,     # $25.00 stop on Gold
        "XAUUSD.m": 2500.0,   # $25.00 stop on Gold
    }

    balances = [5.0, 10.0, 25.0, 50.0, 100.0, 500.0, 5000.0, 50000.0]
    rows = []

    for bal in balances:
        acct = AccountInfo(balance=bal, equity=bal, free_margin=bal, currency="USD")
        for sym, spec in specs.items():
            pts = sl_points.get(sym, 300.0)
            entry = 1.08500 if "EUR" in sym else (3900.0 if "XAU" in sym else (90000.0 if "BTC" in sym else 155.0))
            sl = entry - (pts * spec.point)

            res = sizing.calculate_lot_size(
                account=acct,
                symbol_info=spec,
                entry_price=entry,
                stop_loss=sl
            )

            decision = "APPROVED" if res.is_approved else ("MICRO_ALLOWED" if res.is_micro_exception else "SKIPPED")
            rows.append({
                "balance": bal,
                "symbol": sym,
                "tier": res.tier_name,
                "target_risk_pct": res.target_risk_pct,
                "lots": res.volume,
                "real_risk_pct": res.risk_percent_actual,
                "monetary_risk": res.monetary_risk,
                "margin_required": res.margin_required,
                "decision": decision,
                "reason": res.rejection_reason or "OK",
            })
    return rows


def main():
    loader = HistoricalDataLoader()
    loader.initialize_mt5()

    with open("config/symbols.json", "r", encoding="utf-8") as f:
        symbols_cfg = json.load(f)

    specs = get_symbol_specs(symbols_cfg, loader)
    loader.shutdown_mt5()

    rows_off = run_matrix(flag_micro=False, symbols_cfg=symbols_cfg, specs=specs)
    rows_on = run_matrix(flag_micro=True, symbols_cfg=symbols_cfg, specs=specs)

    md = [
        "# Stage 0A Position Sizing Matrix Across Account Balances",
        f"*Generated: {datetime.now(timezone.utc).isoformat()} UTC*",
        "",
        "## 1. Micro-Account Flags OFF (Default Conservative)",
        "| Balance ($) | Tier | Symbol | Lots | Real Risk % | Risk ($) | Margin ($) | Decision | Reason |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for r in rows_off:
        reason_short = (r['reason'][:40] + "...") if len(r['reason']) > 40 else r['reason']
        md.append(
            f"| ${r['balance']:,.2f} | `{r['tier']}` | `{r['symbol']}` | {r['lots']:.2f} | "
            f"{r['real_risk_pct']:.2f}% | ${r['monetary_risk']:.2f} | ${r['margin_required']:.2f} | "
            f"**{r['decision']}** | {reason_short} |"
        )

    md.extend([
        "",
        "## 2. Micro-Account Flags ON (Operator High-Risk Exception Acknowledged)",
        "| Balance ($) | Tier | Symbol | Lots | Real Risk % | Risk ($) | Margin ($) | Decision | Reason |",
        "|---|---|---|---|---|---|---|---|---|",
    ])
    for r in rows_on:
        reason_short = (r['reason'][:40] + "...") if len(r['reason']) > 40 else r['reason']
        md.append(
            f"| ${r['balance']:,.2f} | `{r['tier']}` | `{r['symbol']}` | {r['lots']:.2f} | "
            f"{r['real_risk_pct']:.2f}% | ${r['monetary_risk']:.2f} | ${r['margin_required']:.2f} | "
            f"**{r['decision']}** | {reason_short} |"
        )

    out_path = "reports/sizing_matrix.md"
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(md))

    print(f"[OK] Sizing table generated at {out_path} ({len(rows_off) + len(rows_on)} test scenarios evaluated).")
    # Print sample of the table
    print("\nSAMPLE OUTPUT (Balances $5, $25, $500, $50,000 on EURUSD.m, XAUUSD.m, BTCUSD.m):")
    sample_bals = [5.0, 25.0, 500.0, 50000.0]
    sample_syms = ["EURUSD", "XAUUSD.m", "BTCUSD.m"]
    print(f"{'Balance':<10} {'Symbol':<10} {'Tier':<10} {'Lots':<8} {'RealRisk%':<10} {'Risk$':<10} {'Decision':<14}")
    print("-" * 75)
    for r in rows_off:
        if r['balance'] in sample_bals and r['symbol'] in sample_syms:
            print(f"${r['balance']:<9.2f} {r['symbol']:<10} {r['tier']:<10} {r['lots']:<8.2f} {r['real_risk_pct']:<10.2f} ${r['monetary_risk']:<9.2f} {r['decision']:<14}")


if __name__ == "__main__":
    main()
