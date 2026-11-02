"""
Universal Risk Fit Engine Balance-by-Symbol Matrix Generator.
Evaluates balances: $5, $10, $25, $50, $100, $500, $5,000, $50,000
across: XAUUSD.m, BTCUSD.m, EURUSD.m, GBPJPY.m, US30.std
with micro flags OFF and ON.
"""

from __future__ import annotations
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

try:
    import MetaTrader5 as mt5
    MT5_AVAILABLE = True
except ImportError:
    mt5 = None
    MT5_AVAILABLE = False

from src.core.contracts import SymbolInfo, AccountInfo, OrderType
from src.risk.fit_engine import RiskFitEngine


def generate_matrix():
    if MT5_AVAILABLE:
        mt5.initialize()

    engine = RiskFitEngine()

    balances = [5.0, 10.0, 25.0, 50.0, 100.0, 500.0, 5000.0, 50000.0]
    symbol_names = ["XAUUSD.m", "BTCUSD.m", "EURUSD.m", "GBPJPY.m", "US30.std"]

    # Retrieve live specifications if MT5 is connected
    symbols: dict[str, SymbolInfo] = {}
    atrs: dict[str, dict[str, float]] = {}

    for sym_name in symbol_names:
        if MT5_AVAILABLE and mt5.terminal_info() is not None:
            mt5.symbol_select(sym_name, True)
            info = mt5.symbol_info(sym_name)
            if info:
                # Get last close price
                rates = mt5.copy_rates_from_pos(sym_name, mt5.TIMEFRAME_M5, 0, 50)
                px = float(rates[-1]["close"]) if rates is not None and len(rates) > 0 else (info.bid if info.bid > 0 else 1.0)
                # Compute ATR roughly from last 14 bars
                if rates is not None and len(rates) >= 14:
                    ranges = [r["high"] - r["low"] for r in rates[-14:]]
                    atr_m5 = float(sum(ranges) / len(ranges))
                else:
                    atr_m5 = info.point * 150.0

                sym_obj = SymbolInfo(
                    symbol=sym_name,
                    digits=info.digits,
                    point=info.point,
                    tick_size=info.trade_tick_size if info.trade_tick_size > 0 else info.point,
                    tick_value=info.trade_tick_value if info.trade_tick_value > 0 else 1.0,
                    contract_size=info.trade_contract_size,
                    min_lot=info.volume_min,
                    max_lot=info.volume_max,
                    lot_step=info.volume_step,
                    spread=float(info.spread)
                )
                sym_obj.bid = px
                symbols[sym_name] = sym_obj
                atrs[sym_name] = {
                    "M5": atr_m5,
                    "M15": atr_m5 * 1.73,
                    "M30": atr_m5 * 2.45,
                    "H1": atr_m5 * 3.46,
                    "H4": atr_m5 * 6.92
                }
                continue

        # Offline fallback specifications
        defaults = {
            "XAUUSD.m": (2, 0.01, 1.0, 100.0, 0.01, 50.0, 0.01, 26.0, 2600.0, 2.9),
            "BTCUSD.m": (2, 0.01, 1.0, 1.0, 0.01, 10.0, 0.01, 850.0, 78000.0, 250.0),
            "EURUSD.m": (5, 0.00001, 1.0, 100000.0, 0.01, 100.0, 0.01, 8.0, 1.0850, 0.0008),
            "GBPJPY.m": (3, 0.001, 0.67, 100000.0, 0.01, 100.0, 0.01, 20.0, 195.0, 0.150),
            "US30.std": (1, 0.1, 1.0, 1.0, 0.01, 50.0, 0.01, 20.0, 42000.0, 40.0),
        }
        d = defaults[sym_name]
        sym_obj = SymbolInfo(
            symbol=sym_name, digits=d[0], point=d[1], tick_size=d[1],
            tick_value=d[2], contract_size=d[3], min_lot=d[4], max_lot=d[5],
            lot_step=d[6], spread=d[7]
        )
        sym_obj.bid = d[8]
        symbols[sym_name] = sym_obj
        atrs[sym_name] = {
            "M5": d[9], "M15": d[9] * 1.73, "M30": d[9] * 2.45,
            "H1": d[9] * 3.46, "H4": d[9] * 6.92
        }

    markdown_tables = []

    for micro_flag in [False, True]:
        engine.tier_manager.acknowledged_high_risk_microaccount = micro_flag
        engine.tier_manager.micro_account_min_lot_allowed = micro_flag

        title = f"### Risk Fit Matrix: Micro Account Minimum-Lot Flags = **{'ENABLED' if micro_flag else 'DISABLED'}**"
        header = "| Balance | Symbol | Status | TF | Usable Stop Window | Chosen Stop | Lot | Real Risk % ($) | Reachable R:R | Balance Needed |"
        separator = "|:---|:---|:---|:---|:---|:---|:---|:---|:---|:---|"
        rows = []

        for bal in balances:
            acc = AccountInfo(balance=bal, equity=bal, free_margin=bal, currency="USD")
            for sym_name in symbol_names:
                sym_obj = symbols[sym_name]
                atr_dict = atrs[sym_name]

                dec = engine.evaluate_symbol(
                    account=acc,
                    symbol_info=sym_obj,
                    order_type=OrderType.BUY,
                    entry_price=sym_obj.bid,
                    structural_stop_price=0.0,
                    timeframe_atrs=atr_dict,
                    preferred_timeframe="M5",
                    trading_mode="HOLD",
                )

                win_str = f"[{dec.window.min_stop_points:.0f}, {dec.window.max_stop_points:.0f}] pts"
                stop_str = f"{dec.chosen_stop_points:.0f} pts" if dec.lot > 0 else "-"
                lot_str = f"{dec.lot:.2f}"
                risk_str = f"{dec.real_risk_pct:.2f}% (${dec.real_risk_money:.2f})" if dec.lot > 0 else "$0.00 (0%)"
                rr_str = f"{dec.rr_result.reachable_rr:.2f}" if dec.lot > 0 else "-"
                needed_str = f"${dec.balance_needed:.2f}" if dec.status == "BLOCKED_BY_BALANCE" else "-"

                rows.append(
                    f"| ${bal:,.0f} | `{sym_name}` | **{dec.status}** | `{dec.timeframe}` | {win_str} | {stop_str} | `{lot_str}` | {risk_str} | {rr_str} | {needed_str} |"
                )

        table_str = "\n".join([title, "", header, separator] + rows)
        markdown_tables.append(table_str)

    output = "\n\n".join(markdown_tables)
    print(output)

    # Save to file
    out_path = os.path.abspath("reports/risk_fit_matrix.md")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(output)
    print(f"\n[Matrix successfully saved to {out_path}]")

    if MT5_AVAILABLE:
        mt5.shutdown()


if __name__ == "__main__":
    generate_matrix()
