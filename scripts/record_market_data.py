"""
Stage 0 Market Data Extraction & Cost Profiler.
Loads MT5 history, detects broker UTC offset, records real per-symbol costs,
computes spread-by-hour distribution, and evaluates history sufficiency.
"""

import sys
import os
import json
from datetime import datetime, timezone

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.data.historical_loader import HistoricalDataLoader
from src.data.cost_model import CostModelEngine
from src.core.contracts import Timeframe


def main():
    print("=" * 70)
    print("STAGE 0: MT5 MARKET DATA EXTRACTION & COST PROFILING")
    print("=" * 70)

    loader = HistoricalDataLoader()
    if not loader.initialize_mt5():
        print("[FAIL] Could not initialize MetaTrader 5.")
        return 1

    try:
        # Load symbols from config
        config_path = "config/symbols.json"
        with open(config_path, "r", encoding="utf-8") as f:
            symbols_cfg = json.load(f)

        engine = CostModelEngine(loader=loader)
        profiles = []

        print(f"Loaded {len(symbols_cfg)} symbols from {config_path}")
        timeframes = [Timeframe.W1, Timeframe.D1, Timeframe.H4, Timeframe.H1, Timeframe.M15, Timeframe.M1]

        offset = loader.detect_broker_utc_offset()
        print(f"[INFO] Detected Broker UTC Offset: {offset}s ({offset/3600:+.1f} hours)\n")

        for sym, cfg in symbols_cfg.items():
            resolved = loader.resolve_broker_symbol(sym)
            print(f"--> Profiling Symbol: {sym} (Broker symbol: {resolved})")
            if not resolved:
                print(f"    [WARNING] Symbol {sym} not found on broker! Skipping.")
                continue

            # Load and cache history across timeframes
            for tf in timeframes:
                candles = loader.load_candles(sym, tf, count=5000, use_cache=False, force_fresh=True)
                print(f"    TF {tf.value}: loaded and cached {len(candles)} UTC candles")

            # Load sample ticks
            ticks = loader.load_ticks(sym, count=2000)
            print(f"    Ticks: loaded {len(ticks)} UTC ticks")

            # Profile costs
            required_bars = cfg.get("min_bars", {})
            profile = engine.profile_symbol(sym, required_bars=required_bars)
            if profile:
                profiles.append(profile)
                print(f"    Costs: Spread={profile.current_spread_pts} pts (${profile.current_spread_money}), "
                      f"Margin/1lot=${profile.margin_required_1_lot}, MinLot={profile.volume_min}")

        # Ensure reports directories exist
        os.makedirs("reports/raw", exist_ok=True)
        raw_json_path = "reports/raw/market_data_profile.json"
        with open(raw_json_path, "w", encoding="utf-8") as f:
            json.dump([p.to_dict() for p in profiles], f, indent=2)
        print(f"\n[OK] Raw profile saved to {raw_json_path}")

        # Generate markdown report
        md_lines = [
            "# Stage 0 Market Data & Real Cost Profile Report",
            f"*Generated: {datetime.now(timezone.utc).isoformat()} UTC*",
            f"*Broker UTC Offset: {offset}s ({offset/3600:+.1f}h)*",
            "",
            "## 1. Symbol Specifications & Execution Parameters",
            "| Symbol | Broker Sym | Digits | Tick Size | Tick Val ($) | Contract | Min Lot | Step | Max Lot | Margin (1 Lot) | Spread (pts) | Spread ($) | Stops Lvl | Swap Long | Swap Short |",
            "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|",
        ]
        for p in profiles:
            md_lines.append(
                f"| `{p.symbol}` | `{p.broker_symbol}` | {p.digits} | {p.tick_size} | ${p.tick_value:.4f} | "
                f"{p.contract_size:,.0f} | {p.volume_min} | {p.volume_step} | {p.volume_max} | "
                f"${p.margin_required_1_lot:,.2f} | {p.current_spread_pts} | ${p.current_spread_money:.2f} | "
                f"{p.stops_level_pts} | {p.swap_long:.2f} | {p.swap_short:.2f} |"
            )

        md_lines.extend([
            "",
            "## 2. Historical Data Availability & Sufficiency Check",
            "| Symbol | W1 Bars | D1 Bars | H4 Bars | H1 Bars | M15 Bars | M1 Bars | Assessment |",
            "|---|---|---|---|---|---|---|---|",
        ])
        for p in profiles:
            status_summary = "SUFFICIENT"
            short_tfs = []
            for tf, status in p.history_status.items():
                if "SHORT" in status:
                    short_tfs.append(f"{tf} ({p.history_bars_available.get(tf, 0)})")
            if short_tfs:
                status_summary = f"SHORT on {', '.join(short_tfs)}"

            b = p.history_bars_available
            md_lines.append(
                f"| `{p.symbol}` | {b.get('W1', 0)} | {b.get('D1', 0)} | {b.get('H4', 0)} | "
                f"{b.get('H1', 0)} | {b.get('M15', 0)} | {b.get('M1', 0)} | {status_summary} |"
            )

        md_lines.extend([
            "",
            "## 3. Spread By Hour Distribution (UTC Sample - Mean Points)",
            "| Symbol | 00:00 | 04:00 | 08:00 (London) | 12:00 | 14:00 (NY) | 18:00 | 21:00 (Roll) | 22:00 | 23:00 |",
            "|---|---|---|---|---|---|---|---|---|---|",
        ])
        for p in profiles:
            sph = p.spread_by_hour
            def get_sp(h):
                val = sph.get(h, {}).get("mean_pts", 0.0)
                return f"{val:.1f}"
            md_lines.append(
                f"| `{p.symbol}` | {get_sp(0)} | {get_sp(4)} | {get_sp(8)} | {get_sp(12)} | {get_sp(14)} | {get_sp(18)} | {get_sp(21)} | {get_sp(22)} | {get_sp(23)} |"
            )

        md_lines.append("")
        report_path = "reports/market_data_profile.md"
        with open(report_path, "w", encoding="utf-8") as f:
            f.write("\n".join(md_lines))
        print(f"[OK] Markdown report generated at {report_path}\n")

        print("=" * 70)
        print("STAGE 0 DATA ACQUISITION COMPLETE: ALL SPECIFICATIONS RECORDED")
        print("=" * 70)
        return 0

    finally:
        loader.shutdown_mt5()


if __name__ == "__main__":
    sys.exit(main())
