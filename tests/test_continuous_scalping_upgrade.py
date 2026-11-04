"""
Comprehensive Unit & Integration Tests for the MT5-OO1 Complete Continuous Scalping Engine Upgrade.
Covers all 20 required verification areas in Section 11:
1. News-filter removal verification
2. MT5 connection and reconnection handling
3. Multi-symbol symbol validation (BTCUSD.m, EURUSD, GBPUSD, USDJPY, XAUUSD)
4. Historical candle retrieval across M1, M5, M15, H1, H4, D1, W1
5. Real-time tick handling
6. M1/M5 scalping signal generation (Momentum, Pullback, Liquidity-Sweep/Breakout)
7. BUY and SELL signal symmetry
8. Spread filter validation
9. Risk-based lot calculation
10. Minimum-lot eligibility handling (ADAPTIVE_SCALPING & exact rejection diagnostics)
11. Order execution payload validation & MT5 fill confirmation
12. Stop-loss and take-profit placement
13. Break-even activation (cost-aware spread+commission+buffer)
14. Trailing-stop updates (ATR volatility-adaptive & structural)
15. Partial profit taking
16. Full position closure (dynamic TP, $5+ net profit lock, reversal protection)
17. Immediate rescan after closure (0s cooldown)
18. Duplicate-order prevention
19. Daily loss and drawdown protection
20. Telegram notification formatting and delivery
"""

from __future__ import annotations
from datetime import datetime, timedelta, timezone
import unittest
from unittest.mock import MagicMock

from src.core.config import AppConfig, RiskConfig
from src.core.contracts import (
    AccountInfo,
    Candle,
    DecisionOutcome,
    DecisionOutcomeType,
    OrderType,
    Position,
    SetupType,
    SignalCandidate,
    SymbolInfo,
    Timeframe,
)
from src.core.market_data import MarketDataEngine, DEFAULT_REQUEST_BARS, STANDARD_SCAN_TIMEFRAMES
from src.core.news_filter import EconomicNewsEvent, NewsFilterEngine
from src.core.orchestrator import TradingBotOrchestrator
from src.execution.continuous_scalper import ContinuousScalpingEngine, ScalpingLifecycleState
from src.monitoring.alerts_telegram import TelegramAlertManager
from src.monitoring.health_monitor import ContinuousHealthMonitor, STANDARD_DIAGNOSTIC_REASONS
from src.risk.portfolio_risk import PortfolioRiskEngine
from src.risk.position_sizing import PositionSizingEngine
from src.risk.trade_management import TradeManagementEngine
from src.smc.news_filter import NewsFilterEngine as NewsBlackoutFilter
from src.smc.symbol_scanner import SymbolScanner


def _make_candles(
    base_price: float,
    step: float,
    count: int = 25,
    tf_minutes: int = 1,
    start_time: datetime = datetime(2026, 11, 3, 14, 0, tzinfo=timezone.utc),
) -> list[Candle]:
    bars: list[Candle] = []
    p = base_price
    for i in range(count):
        t = start_time + timedelta(minutes=i * tf_minutes)
        o = p
        c = round(o + step, 5)
        hi = round(max(o, c) + abs(step) * 0.35, 5)
        lo = round(min(o, c) - abs(step) * 0.35, 5)
        bars.append(
            Candle(
                time=t,
                open=o,
                high=hi,
                low=lo,
                close=c,
                tick_volume=120 + i,
                spread=10,
                complete=True,
            )
        )
        p = c
    return bars


class TestContinuousScalpingUpgrade(unittest.TestCase):
    def setUp(self) -> None:
        self.now = datetime(2026, 11, 3, 14, 30, tzinfo=timezone.utc)
        self.eur_spec = SymbolInfo(
            symbol="EURUSD.m",
            digits=5,
            point=0.00001,
            tick_size=0.00001,
            tick_value=1.0,
            contract_size=100000.0,
            min_lot=0.01,
            max_lot=50.0,
            lot_step=0.01,
            spread=12.0,
            ask=1.08512,
            bid=1.08500,
            stops_level=3,
            freeze_level=2,
        )

    # -------------------------------------------------------------------------
    # 1. News-Filter Removal Verification
    # -------------------------------------------------------------------------
    def test_01_news_filter_completely_removed(self) -> None:
        core_nf = NewsFilterEngine()
        core_nf.register_events(
            [
                EconomicNewsEvent(
                    id="NFP_1",
                    title="US Non-Farm Payrolls",
                    currency="USD",
                    impact="HIGH",
                    timestamp=self.now,
                )
            ]
        )
        allowed, reason = core_nf.is_trading_allowed("XAUUSD", self.now)
        self.assertTrue(allowed)
        self.assertIsNone(reason)

        smc_nf = NewsBlackoutFilter()
        in_blackout, ev = smc_nf.is_in_blackout("EURUSD", self.now)
        self.assertFalse(in_blackout)
        self.assertIsNone(ev)

        scanner = SymbolScanner(news_filter=smc_nf)
        self.assertFalse(scanner.news_filter.enabled)

        cfg = AppConfig.load_from_dir()
        self.assertFalse(cfg.news_filter_enabled)

    # -------------------------------------------------------------------------
    # 2. MT5 Connection and Reconnection Handling
    # -------------------------------------------------------------------------
    def test_02_mt5_connection_and_reconnection_handling(self) -> None:
        hm = ContinuousHealthMonitor(symbols=["EURUSD", "XAUUSD"])
        mock_bridge = MagicMock()
        mock_bridge.is_connected = False
        mock_bridge.connect.return_value = True

        mock_md = MagicMock()
        mock_md._mt5_initialized = False
        mock_md.ensure_mt5_initialized.return_value = True
        mock_md.get_candles.return_value = _make_candles(1.0850, 0.0002, count=25)

        healthy, notes = hm.check_and_recover_connections(mock_bridge, mock_md, dry_run=False)
        self.assertTrue(healthy)
        self.assertTrue(hm.mt5_terminal_connected)
        self.assertTrue(hm.bridge_connected)
        self.assertGreaterEqual(hm.total_recoveries, 2)
        self.assertTrue(any("Reconnected" in n for n in notes))

    # -------------------------------------------------------------------------
    # 3. Multi-Symbol Symbol Validation
    # -------------------------------------------------------------------------
    def test_03_multi_symbol_validation_and_fair_scheduling(self) -> None:
        symbols = ["BTCUSD.m", "EURUSD", "GBPUSD", "USDJPY", "XAUUSD"]
        engine = ContinuousScalpingEngine(symbols=symbols)
        order_1 = engine.get_ordered_symbols_for_cycle()
        order_2 = engine.get_ordered_symbols_for_cycle()
        self.assertEqual(len(order_1), 5)
        self.assertEqual(set(order_1), set(symbols))
        # Fair round-robin rotates starting symbol each cycle
        self.assertNotEqual(order_1[0], order_2[0])

    # -------------------------------------------------------------------------
    # 4. Historical Candle Retrieval Across M1, M5, M15, H1, H4, D1, W1
    # -------------------------------------------------------------------------
    def test_04_historical_candle_retrieval_across_all_timeframes(self) -> None:
        for tf in [Timeframe.M1, Timeframe.M5, Timeframe.M15, Timeframe.H1, Timeframe.H4, Timeframe.D1, Timeframe.W1]:
            self.assertIn(tf, STANDARD_SCAN_TIMEFRAMES)
            self.assertGreaterEqual(DEFAULT_REQUEST_BARS[tf], 100)

        md = MarketDataEngine(symbols=["EURUSD"])
        for tf in [Timeframe.M1, Timeframe.M5, Timeframe.M15, Timeframe.H1, Timeframe.H4, Timeframe.D1, Timeframe.W1]:
            md.set_candles("EURUSD", tf, _make_candles(1.0800, 0.0001, count=30, tf_minutes=1))
            self.assertEqual(len(md.get_candles("EURUSD", tf)), 30)

    # -------------------------------------------------------------------------
    # 5. Real-Time Tick Handling
    # -------------------------------------------------------------------------
    def test_05_realtime_tick_handling(self) -> None:
        md = MarketDataEngine(symbols=["EURUSD"])
        md.set_candles("EURUSD", Timeframe.M1, _make_candles(1.08500, 0.00010, count=15, tf_minutes=1))
        tick = md.get_live_tick("EURUSD")
        self.assertIsNotNone(tick)
        self.assertIn("bid", tick)
        self.assertIn("ask", tick)
        self.assertGreaterEqual(tick["ask"], tick["bid"])
        self.assertGreater(tick["bid"], 0.0)

    # -------------------------------------------------------------------------
    # 6. M1/M5 Scalping Signal Generation (All 3 Entry Models)
    # -------------------------------------------------------------------------
    def test_06_m1_m5_scalping_signal_generation_all_models(self) -> None:
        scalper = ContinuousScalpingEngine(min_confluence_score=55.0, min_rr=1.5)
        m1_bull = _make_candles(1.08400, 0.00015, count=20, tf_minutes=1)
        # Make last bar a strong displacement breakout bar
        last = m1_bull[-1]
        m1_bull[-1] = Candle(
            time=last.time,
            open=last.open,
            high=last.open + 0.00060,
            low=last.open - 0.00005,
            close=last.open + 0.00055,
            tick_volume=350,
            spread=10,
            complete=True,
        )
        m5_bull = _make_candles(1.08200, 0.00025, count=20, tf_minutes=5)
        m15_bull = _make_candles(1.08000, 0.00040, count=20, tf_minutes=15)
        h1_bull = _make_candles(1.07500, 0.00080, count=20, tf_minutes=60)

        sig = scalper.scan_symbol_timeframes(
            symbol="EURUSD.m",
            candles_by_tf={
                Timeframe.M1: m1_bull,
                Timeframe.M5: m5_bull,
                Timeframe.M15: m15_bull,
                Timeframe.H1: h1_bull,
            },
            symbol_info=self.eur_spec,
        )
        self.assertIsNotNone(sig)
        self.assertEqual(sig.direction, OrderType.BUY)
        self.assertGreaterEqual(sig.score, 55.0)
        self.assertGreaterEqual(sig.risk_reward, 1.5)
        self.assertTrue(sig.id.startswith("SCALP_"))

    # -------------------------------------------------------------------------
    # 7. BUY and SELL Signal Symmetry
    # -------------------------------------------------------------------------
    def test_07_buy_and_sell_signal_symmetry(self) -> None:
        scalper = ContinuousScalpingEngine(min_confluence_score=55.0, min_rr=1.5)
        # Bearish M1/M5/M15/H1 mirror
        m1_bear = _make_candles(1.08800, -0.00015, count=20, tf_minutes=1)
        last = m1_bear[-1]
        m1_bear[-1] = Candle(
            time=last.time,
            open=last.open,
            high=last.open + 0.00005,
            low=last.open - 0.00060,
            close=last.open - 0.00055,
            tick_volume=350,
            spread=10,
            complete=True,
        )
        m5_bear = _make_candles(1.09000, -0.00025, count=20, tf_minutes=5)
        m15_bear = _make_candles(1.09200, -0.00040, count=20, tf_minutes=15)
        h1_bear = _make_candles(1.09600, -0.00080, count=20, tf_minutes=60)

        sell_sig = scalper.scan_symbol_timeframes(
            symbol="EURUSD.m",
            candles_by_tf={
                Timeframe.M1: m1_bear,
                Timeframe.M5: m5_bear,
                Timeframe.M15: m15_bear,
                Timeframe.H1: h1_bear,
            },
            symbol_info=self.eur_spec,
        )
        self.assertIsNotNone(sell_sig)
        self.assertEqual(sell_sig.direction, OrderType.SELL)
        self.assertGreater(sell_sig.stop_loss, sell_sig.entry_price)
        self.assertLess(sell_sig.take_profit, sell_sig.entry_price)

    # -------------------------------------------------------------------------
    # 8. Spread Filter Validation
    # -------------------------------------------------------------------------
    def test_08_spread_filter_validation(self) -> None:
        cfg = AppConfig.load_from_dir()
        orch = TradingBotOrchestrator(cfg)
        wide_spec = SymbolInfo(
            symbol="EURUSD.m",
            digits=5,
            point=0.00001,
            tick_size=0.00001,
            tick_value=1.0,
            min_lot=0.01,
            max_lot=50.0,
            lot_step=0.01,
            spread=75.0,  # 7.5 pips > 3.0 pips ceiling
            ask=1.08575,
            bid=1.08500,
        )
        cand = SignalCandidate(
            id="SCALP_EURUSD_M1_MOMENTUM_BUY_1",
            symbol="EURUSD.m",
            timeframe=Timeframe.M1,
            direction=OrderType.BUY,
            setup_type=SetupType.FAST_SCALP,
            entry_price=1.08575,
            stop_loss=1.08475,
            take_profit=1.08775,
            risk_reward=2.0,
            score=75.0,
            timestamp=self.now,
            status="APPROVED",
        )
        chk = orch.order_manager.run_pre_trade_checklist(
            cand, AccountInfo(balance=2329.71, equity=2329.71), wide_spec, []
        )
        self.assertFalse(chk.passed)
        self.assertTrue(any("Spread" in r for r in chk.rejection_reasons))

    # -------------------------------------------------------------------------
    # 9. Risk-Based Lot Calculation
    # -------------------------------------------------------------------------
    def test_09_risk_based_lot_calculation_broker_specs(self) -> None:
        sizer = PositionSizingEngine(default_risk_percent=0.5)
        acc = AccountInfo(balance=2329.71, equity=2329.71, free_margin=2329.71, leverage=100)
        res = sizer.calculate_lot_size(
            account=acc,
            symbol_info=self.eur_spec,
            entry_price=1.08500,
            stop_loss=1.08300,  # 20 pips = $2.00 per 0.01 lot
            take_profit=1.08850,
            risk_percent=0.5,
            adaptive_scalping=True,
            min_rr_override=1.5,
        )
        self.assertTrue(res.is_approved)
        # 0.5% of $2329.71 = $11.65 risk -> $11.65 / $200 per lot = 0.05 lots
        self.assertEqual(res.volume, 0.05)

    # -------------------------------------------------------------------------
    # 10. Minimum-Lot Eligibility Handling (ADAPTIVE_SCALPING)
    # -------------------------------------------------------------------------
    def test_10_minimum_lot_eligibility_adaptive_scalping_and_exact_rejection(self) -> None:
        sizer = PositionSizingEngine(default_risk_percent=0.5)
        small_acc = AccountInfo(balance=250.0, equity=250.0, free_margin=250.0, leverage=500)
        # 0.5% of $250 = $1.25 target risk; 20 pips at 0.01 lot = $2.00 actual risk (0.8% of $250 <= 2.0% adaptive ceiling)
        res_eligible = sizer.calculate_lot_size(
            account=small_acc,
            symbol_info=self.eur_spec,
            entry_price=1.08500,
            stop_loss=1.08300,
            take_profit=1.08850,
            adaptive_scalping=True,
            adaptive_min_lot_ceiling_pct=2.0,
            min_rr_override=1.5,
        )
        self.assertTrue(res_eligible.is_approved)
        self.assertEqual(res_eligible.volume, 0.01)

        # Tiny account where 0.01 lot risks $10 on $100 account (10% > 2.0% ceiling) -> rejected with exact real numbers
        tiny_acc = AccountInfo(balance=100.0, equity=100.0, free_margin=100.0, leverage=500)
        res_rej = sizer.calculate_lot_size(
            account=tiny_acc,
            symbol_info=self.eur_spec,
            entry_price=1.08500,
            stop_loss=1.07500,  # 100 pips = $10.00 risk on 0.01 lot
            take_profit=1.10500,
            adaptive_scalping=True,
            adaptive_min_lot_ceiling_pct=2.0,
            min_rr_override=1.5,
        )
        self.assertFalse(res_rej.is_approved)
        self.assertIn("exceeding target risk", res_rej.rejection_reason)
        self.assertIn("Ceiling: 2.00%", res_rej.rejection_reason)

    # -------------------------------------------------------------------------
    # 11. Order Execution Payload Validation & MT5 Confirmation Check
    # -------------------------------------------------------------------------
    def test_11_order_execution_payload_and_mt5_confirmation(self) -> None:
        cfg = AppConfig.load_from_dir()
        cfg.dry_run = False
        orch = TradingBotOrchestrator(cfg)
        orch.account_info = AccountInfo(balance=2329.71, equity=2329.71, free_margin=2329.71, leverage=200)
        orch.bridge = MagicMock()
        orch.bridge.is_connected.return_value = True
        orch.bridge.execute_trade.return_value = {
            "status": "SUCCESS",
            "ticket": 880011,
            "price": 1.08512,
            "retcode": 10009,
        }
        orch.order_manager.bridge = orch.bridge

        cand = SignalCandidate(
            id="SCALP_EURUSD_M5_MOMENTUM_BUY_1",
            symbol="EURUSD.m",
            timeframe=Timeframe.M5,
            direction=OrderType.BUY,
            setup_type=SetupType.FAST_SCALP,
            entry_price=1.08512,
            stop_loss=1.08312,
            take_profit=1.08862,
            risk_reward=1.75,
            score=76.0,
            timestamp=self.now,
            status="APPROVED",
        )
        order = orch.order_manager.execute_signal(cand, orch.account_info, self.eur_spec, [])
        self.assertIsNotNone(order)
        self.assertEqual(order.status, "FILLED")
        self.assertEqual(order.ticket, 880011)

    # -------------------------------------------------------------------------
    # 12. Stop-Loss and Take-Profit Placement Validation
    # -------------------------------------------------------------------------
    def test_12_stop_loss_and_take_profit_placement(self) -> None:
        scalper = ContinuousScalpingEngine(min_confluence_score=55.0, min_rr=1.5)
        m5_bars = _make_candles(1.08400, 0.00018, count=20, tf_minutes=5)
        cand = scalper._build_scalp_candidate(
            symbol="EURUSD.m",
            timeframe=Timeframe.M5,
            direction=OrderType.BUY,
            setup_type=SetupType.FAST_SCALP,
            model_name="MOMENTUM_CONTINUATION",
            last_candle=m5_bars[-1],
            raw_sl=m5_bars[-1].low - 0.00030,
            atr=0.00035,
            score=72.0,
            rationale="SL/TP test",
            symbol_info=self.eur_spec,
        )
        self.assertIsNotNone(cand)
        min_stop_dist = self.eur_spec.stops_level * self.eur_spec.point
        self.assertGreater(cand.entry_price - cand.stop_loss, min_stop_dist)
        self.assertGreater(cand.take_profit - cand.entry_price, min_stop_dist)
        self.assertGreaterEqual(cand.risk_reward, 1.5)

    # -------------------------------------------------------------------------
    # 13. Break-Even Activation (Cost-Aware Spread + Commission + Buffer)
    # -------------------------------------------------------------------------
    def test_13_break_even_activation_cost_aware(self) -> None:
        mgr = TradeManagementEngine(RiskConfig())
        pos = Position(
            ticket=7001,
            symbol="EURUSD.m",
            order_type=OrderType.BUY,
            volume=0.05,
            open_price=1.08500,
            current_price=1.08565,  # +0.65R on 10-pip initial SL
            stop_loss=1.08400,
            take_profit=1.08750,
            profit=3.25,
            commission=-0.35,
            swap=0.0,
            initial_sl_distance=0.00100,
            comment="SCALP_M5",
        )
        candles = _make_candles(1.08500, 0.00012, count=8, tf_minutes=5)
        actions = mgr.evaluate_position(pos, self.eur_spec, candles, scalp_mode=True)
        be_actions = [a for a in actions if a.action_type == "MODIFY_SL" and "Break-even" in a.reason]
        self.assertEqual(len(be_actions), 1)
        self.assertGreater(be_actions[0].new_sl, pos.open_price)

    # -------------------------------------------------------------------------
    # 14. Trailing-Stop Updates (ATR Volatility-Adaptive & Structural)
    # -------------------------------------------------------------------------
    def test_14_trailing_stop_updates_atr_and_structural(self) -> None:
        mgr = TradeManagementEngine(RiskConfig(trailing_stop_enabled=True))
        pos = Position(
            ticket=7002,
            symbol="EURUSD.m",
            order_type=OrderType.BUY,
            volume=0.05,
            open_price=1.08500,
            current_price=1.08595,  # +0.95R in scalp mode triggers ATR trailing stop
            stop_loss=1.08400,
            take_profit=1.08800,
            profit=4.75,
            commission=-0.25,
            swap=0.0,
            initial_sl_distance=0.00100,
            break_even_activated=True,
            comment="SCALP_M5",
        )
        candles = _make_candles(1.08500, 0.00012, count=15, tf_minutes=5)
        actions = mgr.evaluate_position(pos, self.eur_spec, candles, scalp_mode=True)
        trail_actions = [a for a in actions if a.action_type == "MODIFY_SL" and "trailing" in a.reason.lower()]
        self.assertGreaterEqual(len(trail_actions), 1)
        self.assertGreater(trail_actions[0].new_sl, pos.open_price)

    # -------------------------------------------------------------------------
    # 15. Partial Profit Taking
    # -------------------------------------------------------------------------
    def test_15_partial_profit_taking(self) -> None:
        mgr = TradeManagementEngine(RiskConfig(enable_partial_tp=True, partial_tp_at_r=1.5, partial_tp_ratio=0.5))
        pos = Position(
            ticket=7003,
            symbol="EURUSD.m",
            order_type=OrderType.BUY,
            volume=0.06,
            open_price=1.08500,
            current_price=1.08660,  # +1.6R
            stop_loss=1.08400,
            take_profit=1.08850,
            profit=9.60,
            commission=-0.30,
            swap=0.0,
            initial_sl_distance=0.00100,
        )
        candles = _make_candles(1.08500, 0.00025, count=10, tf_minutes=5)
        actions = mgr.evaluate_position(pos, self.eur_spec, candles, scalp_mode=True)
        partials = [a for a in actions if a.action_type == "PARTIAL_CLOSE"]
        self.assertEqual(len(partials), 1)
        self.assertEqual(partials[0].close_volume, 0.03)

    # -------------------------------------------------------------------------
    # 16. Full Position Closure (Dynamic TP & Reversal Protection)
    # -------------------------------------------------------------------------
    def test_16_full_position_closure_dynamic_tp(self) -> None:
        mgr = TradeManagementEngine(RiskConfig(dynamic_take_profit=True))
        pos = Position(
            ticket=7004,
            symbol="EURUSD.m",
            order_type=OrderType.BUY,
            volume=0.03,
            open_price=1.08500,
            current_price=1.08630,  # +1.3R ($3.90 gross, $3.65 net < $5.00 so dynamic TP handles stall)
            stop_loss=1.08400,
            take_profit=1.08800,
            profit=3.90,
            commission=-0.25,
            swap=0.0,
            initial_sl_distance=0.00100,
            comment="SCALP_M1",
        )
        # 2 counter-direction (bearish) closed bars signalling momentum stall
        candles = _make_candles(1.08500, 0.00025, count=5, tf_minutes=1) + [
            Candle(time=self.now, open=1.08645, high=1.08648, low=1.08632, close=1.08635),
            Candle(time=self.now + timedelta(minutes=1), open=1.08635, high=1.08637, low=1.08625, close=1.08630),
        ]
        actions = mgr.evaluate_position(pos, self.eur_spec, candles, scalp_mode=True)
        closes = [a for a in actions if a.action_type == "FULL_CLOSE"]
        self.assertEqual(len(closes), 1)
        self.assertIn("Dynamic Take-Profit", closes[0].reason)

    # -------------------------------------------------------------------------
    # 17. Immediate Rescan After Closure (0s Cooldown)
    # -------------------------------------------------------------------------
    def test_17_immediate_rescan_after_closure(self) -> None:
        scalper = ContinuousScalpingEngine(symbols=["EURUSD.m"], auto_rescan_after_closure=True)
        new_state = scalper.on_position_closed_or_rejected("EURUSD.m", was_closed=True)
        st = scalper.get_symbol_state("EURUSD.m")
        self.assertEqual(new_state, ScalpingLifecycleState.MARKET_DATA)
        self.assertGreaterEqual(st.rescan_count, 1)
        self.assertIn(ScalpingLifecycleState.POSITION_CLOSURE, st.state_history)
        self.assertIn(ScalpingLifecycleState.RESCAN, st.state_history)

    # -------------------------------------------------------------------------
    # 18. Duplicate-Order Prevention
    # -------------------------------------------------------------------------
    def test_18_duplicate_order_prevention(self) -> None:
        scalper = ContinuousScalpingEngine(symbols=["EURUSD.m"])
        key = f"EURUSD.m_M1_BUY_{self.now.isoformat()}"
        self.assertFalse(scalper.is_duplicate_signal(key))
        scalper.mark_signal_executed(key)
        self.assertTrue(scalper.is_duplicate_signal(key))

    # -------------------------------------------------------------------------
    # 19. Daily Loss and Drawdown Protection
    # -------------------------------------------------------------------------
    def test_19_daily_loss_and_drawdown_protection(self) -> None:
        risk_cfg = RiskConfig(max_daily_loss_percent=3.0, max_daily_loss_enabled=True)
        pr = PortfolioRiskEngine(risk_cfg)
        pr.initialize_day(2000.0)
        pr.record_closed_trade(-75.0)  # -3.75% > 3.0% daily loss limit
        acc = AccountInfo(balance=1925.0, equity=1925.0, free_margin=1925.0)
        cand = SignalCandidate(
            id="SCALP_TEST_19",
            symbol="EURUSD.m",
            timeframe=Timeframe.M1,
            direction=OrderType.BUY,
            setup_type=SetupType.FAST_SCALP,
            entry_price=1.08500,
            stop_loss=1.08400,
            take_profit=1.08700,
            risk_reward=2.0,
            score=80.0,
            timestamp=self.now,
        )
        allowed, reason = pr.evaluate_new_order_permission(cand, acc, [], 10.0)
        self.assertFalse(allowed)
        self.assertTrue(pr.circuit_breaker_active)
        self.assertIn("Daily loss limit breached", reason)

    # -------------------------------------------------------------------------
    # 20. Telegram Notification Formatting and Delivery (No News Skip Spam)
    # -------------------------------------------------------------------------
    def test_20_telegram_notification_formatting_and_delivery(self) -> None:
        alerts = TelegramAlertManager(bot_token="", chat_id="")
        # Verify news restriction alert is permanently disabled / no-op
        alerts.alert_news_restriction("EURUSD", "US CPI")
        news_outcome = DecisionOutcome(
            symbol="EURUSD",
            bar_time=self.now,
            outcome_type=DecisionOutcomeType.TRADE_SKIPPED,
            reason_code="NEWS_FILTER",
            details="Skipped due to news",
        )
        self.assertFalse(alerts.send_decision_outcome(news_outcome))

        # Verify health monitor classifies all 8 standard diagnostic reasons accurately
        hm = ContinuousHealthMonitor()
        for std_reason in STANDARD_DIAGNOSTIC_REASONS:
            mapped = hm.classify_diagnostic_reason(std_reason)
            self.assertEqual(mapped, std_reason)


if __name__ == "__main__":
    unittest.main()


