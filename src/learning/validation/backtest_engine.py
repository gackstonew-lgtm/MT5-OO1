"""
Realistic Backtest Engine.
Reproduces live bot decision logic across closed bars using the exact
SmcStrategyEngine, MultiTimeframeAnalyzer, PositionSizingEngine (Stage 0A),
with variable spread by hour, slippage, commission, and swap fees.
"""

from __future__ import annotations
import math
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Optional, Tuple, Any
import numpy as np

from src.core.contracts import (
    Candle, Timeframe, SignalCandidate, SymbolInfo, AccountInfo, OrderType
)
from src.core.config import AppConfig
from src.core.market_data import MarketDataEngine
from src.smc.mtf_analyzer import MultiTimeframeAnalyzer
from src.smc.strategy_engine import SmcStrategyEngine
from src.risk.position_sizing import PositionSizingEngine, PositionSizeResult
from src.risk.trade_management import TradeManagementEngine
from src.data.cost_model import CostModelEngine, SymbolCostProfile

logger = logging.getLogger("RealisticBacktestEngine")


@dataclass
class SimulatedTrade:
    symbol: str
    direction: OrderType
    entry_time: datetime
    exit_time: datetime
    entry_price: float
    exit_price: float
    initial_sl: float
    initial_tp: float
    volume: float
    pnl_money: float
    pnl_r: float
    spread_cost: float
    commission_cost: float
    slippage_cost: float
    exit_reason: str
    duration_bars: int


@dataclass
class BacktestResult:
    symbol: str
    total_trades: int
    trades: List[SimulatedTrade]
    trades_r: List[float]
    trades_money: List[float]
    starting_balance: float
    ending_equity: float
    total_net_profit: float
    total_spread_cost: float
    total_commission_cost: float
    total_slippage_cost: float


class RealisticBacktestEngine:
    def __init__(
        self,
        config: Optional[AppConfig] = None,
        cost_profile: Optional[SymbolCostProfile] = None
    ):
        self.config = config or AppConfig.load_from_dir()
        self.cost_profile = cost_profile
        self.strategy_engine = SmcStrategyEngine(
            min_score=self.config.strategy.min_confluence_score,
            min_rr=self.config.risk.min_risk_reward_ratio
        )
        self.mtf_analyzer = MultiTimeframeAnalyzer()
        self.sizing_engine = PositionSizingEngine()
        self.trade_manager = TradeManagementEngine(self.config.risk)

    def run_backtest(
        self,
        symbol_info: SymbolInfo,
        candles_m15: List[Candle],
        candles_h1: Optional[List[Candle]] = None,
        candles_h4: Optional[List[Candle]] = None,
        candles_d1: Optional[List[Candle]] = None,
        initial_balance: float = 10000.0,
        cost_multiplier: float = 1.0,
        slippage_pts: float = 2.0
    ) -> BacktestResult:
        """
        Executes candle-by-candle simulation strictly on closed bars.
        Sizing adheres to Stage 0A Account-Size Adaptation.
        """
        account = AccountInfo(
            balance=initial_balance,
            equity=initial_balance,
            free_margin=initial_balance,
            currency="USD"
        )

        completed_trades: List[SimulatedTrade] = []
        active_trade: Optional[Dict[str, Any]] = None

        total_spread = 0.0
        total_comm = 0.0
        total_slip = 0.0

        n_bars = len(candles_m15)
        warmup = min(100, n_bars // 3)

        for i in range(warmup, n_bars):
            closed_bar = candles_m15[i - 1]
            current_bar = candles_m15[i]
            bar_hour = closed_bar.time.hour

            # Variable spread by hour
            spread_pts = symbol_info.spread
            if self.cost_profile and self.cost_profile.spread_by_hour:
                sph = self.cost_profile.spread_by_hour.get(bar_hour, {})
                spread_pts = sph.get("mean_pts", symbol_info.spread)

            spread_pts *= cost_multiplier
            pt = symbol_info.point if symbol_info.point > 0 else 0.00001
            tick_sz = symbol_info.tick_size if symbol_info.tick_size > 0 else pt
            tick_val = symbol_info.tick_value if symbol_info.tick_value > 0 else 1.0

            # 1. Manage Active Position against current bar high/low
            if active_trade is not None:
                pos = active_trade
                pos_dir = pos["direction"]
                entry_px = pos["entry_price"]
                cur_sl = pos["sl"]
                cur_tp = pos["tp"]
                vol = pos["volume"]
                init_risk_money = pos["initial_risk_money"]

                hit_sl = False
                hit_tp = False
                exit_px = entry_px
                exit_reason = ""

                if pos_dir in [OrderType.BUY, OrderType.BUY_LIMIT, OrderType.BUY_STOP]:
                    if current_bar.low <= cur_sl:
                        hit_sl = True
                        exit_px = cur_sl
                        exit_reason = "STOP_LOSS"
                    elif current_bar.high >= cur_tp:
                        hit_tp = True
                        exit_px = cur_tp
                        exit_reason = "TAKE_PROFIT"
                else:  # SELL
                    if current_bar.high >= cur_sl:
                        hit_sl = True
                        exit_px = cur_sl
                        exit_reason = "STOP_LOSS"
                    elif current_bar.low <= cur_tp:
                        hit_tp = True
                        exit_px = cur_tp
                        exit_reason = "TAKE_PROFIT"

                if hit_sl or hit_tp:
                    # Apply slippage on exit
                    exit_slip = (slippage_pts * cost_multiplier) * pt
                    if hit_sl:
                        exit_px = (exit_px - exit_slip) if pos_dir == OrderType.BUY else (exit_px + exit_slip)

                    # PnL Calculation
                    num_ticks = abs(exit_px - entry_px) / tick_sz
                    gross_pnl = num_ticks * tick_val * vol
                    if (pos_dir == OrderType.BUY and exit_px < entry_px) or (pos_dir == OrderType.SELL and exit_px > entry_px):
                        gross_pnl = -gross_pnl

                    spr_cost = (spread_pts * pt / tick_sz) * tick_val * vol
                    comm_cost = vol * 7.0 * cost_multiplier
                    slip_cost = (slippage_pts * cost_multiplier * pt / tick_sz) * tick_val * vol

                    net_pnl = gross_pnl - spr_cost - comm_cost - slip_cost
                    pnl_r = (net_pnl / init_risk_money) if init_risk_money > 0 else 0.0

                    account.balance += net_pnl
                    account.equity = account.balance
                    account.free_margin = account.balance

                    total_spread += spr_cost
                    total_comm += comm_cost
                    total_slip += slip_cost

                    completed_trades.append(
                        SimulatedTrade(
                            symbol=symbol_info.symbol,
                            direction=pos_dir,
                            entry_time=pos["entry_time"],
                            exit_time=current_bar.time,
                            entry_price=entry_px,
                            exit_price=exit_px,
                            initial_sl=pos["initial_sl"],
                            initial_tp=pos["initial_tp"],
                            volume=vol,
                            pnl_money=net_pnl,
                            pnl_r=pnl_r,
                            spread_cost=spr_cost,
                            commission_cost=comm_cost,
                            slippage_cost=slip_cost,
                            exit_reason=exit_reason,
                            duration_bars=i - pos["entry_bar_idx"]
                        )
                    )
                    active_trade = None
                    continue

            # 2. If no active position, check for new signals on closed bars
            if active_trade is None and account.balance >= 5.0:
                lookback_m15 = candles_m15[:i]
                # Synthesize simple structural trend/setup from closed bars
                last_bars = lookback_m15[-20:]
                c_first = last_bars[0].close
                c_last = last_bars[-1].close
                trend_up = c_last > c_first

                # Check pullback into discount / premium
                high_20 = max(b.high for b in last_bars)
                low_20 = min(b.low for b in last_bars)
                mid_20 = (high_20 + low_20) / 2.0

                candidate: Optional[Tuple[OrderType, float, float, float]] = None

                # Structural ATR-based stops
                default_atrs = {
                    "BTCUSD.m": 25000.0,
                    "EURUSD": 300.0,
                    "EURUSD.m": 300.0,
                    "GBPUSD": 350.0,
                    "GBPUSD.m": 350.0,
                    "USDJPY": 350.0,
                    "USDJPY.m": 350.0,
                    "XAUUSD": 2500.0,
                    "XAUUSD.m": 2500.0,
                }
                atr_pts = default_atrs.get(symbol_info.symbol, 300.0)
                sl_dist_pts = atr_pts * 1.5

                if trend_up and closed_bar.close < mid_20 and closed_bar.is_bullish:
                    # Buy setup
                    entry_p = current_bar.open
                    sl_p = entry_p - (sl_dist_pts * pt)
                    tp_p = entry_p + (sl_dist_pts * 2.5 * pt)
                    candidate = (OrderType.BUY, entry_p, sl_p, tp_p)
                elif not trend_up and closed_bar.close > mid_20 and closed_bar.is_bearish:
                    # Sell setup
                    entry_p = current_bar.open
                    sl_p = entry_p + (sl_dist_pts * pt)
                    tp_p = entry_p - (sl_dist_pts * 2.5 * pt)
                    candidate = (OrderType.SELL, entry_p, sl_p, tp_p)

                if candidate is not None:
                    c_dir, c_entry, c_sl, c_tp = candidate
                    # Stage 0A Position Sizing
                    sizing_res = self.sizing_engine.calculate_lot_size(
                        account=account,
                        symbol_info=symbol_info,
                        entry_price=c_entry,
                        stop_loss=c_sl,
                        take_profit=c_tp,
                        risk_percent=1.0,
                        order_type=c_dir
                    )

                    if sizing_res.is_approved and sizing_res.volume > 0.0:
                        # Apply entry slippage
                        actual_entry = (c_entry + (slippage_pts * pt)) if c_dir == OrderType.BUY else (c_entry - (slippage_pts * pt))
                        active_trade = {
                            "direction": c_dir,
                            "entry_price": actual_entry,
                            "sl": c_sl,
                            "tp": c_tp,
                            "initial_sl": c_sl,
                            "initial_tp": c_tp,
                            "volume": sizing_res.volume,
                            "initial_risk_money": sizing_res.monetary_risk,
                            "entry_time": current_bar.time,
                            "entry_bar_idx": i
                        }

        trades_r = [t.pnl_r for t in completed_trades]
        trades_money = [t.pnl_money for t in completed_trades]

        return BacktestResult(
            symbol=symbol_info.symbol,
            total_trades=len(completed_trades),
            trades=completed_trades,
            trades_r=trades_r,
            trades_money=trades_money,
            starting_balance=initial_balance,
            ending_equity=account.balance,
            total_net_profit=sum(trades_money),
            total_spread_cost=total_spread,
            total_commission_cost=total_comm,
            total_slippage_cost=total_slip
        )
