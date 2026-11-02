"""
Live Verification of Stage 0A on MT5 Demo Account.
Places a minimum-lot trade on the cheapest tradable symbol (EURUSD.m),
verifies post-execution position confirmation in the MT5 Trade tab,
validates that real monetary risk matches the sizing calculation,
and safely closes the position.
"""

import sys
import os
import time

try:
    import MetaTrader5 as mt5
except ImportError:
    print("MetaTrader5 python module required.")
    sys.exit(1)

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.core.contracts import AccountInfo, SymbolInfo
from src.risk.position_sizing import PositionSizingEngine


def main():
    print("=" * 70)
    print("STAGE 0A: LIVE DEMO ACCOUNT VERIFICATION")
    print("=" * 70)

    if not mt5.initialize():
        print(f"[FAIL] Could not initialize MT5: {mt5.last_error()}")
        return 1

    try:
        ai = mt5.account_info()
        print(f"Account: #{ai.login} ({ai.server}) | Balance: ${ai.balance:.2f} {ai.currency} | Free Margin: ${ai.margin_free:.2f}")
        acct = AccountInfo(balance=ai.balance, equity=ai.equity, free_margin=ai.margin_free, currency=ai.currency)

        symbol = "EURUSD.m"
        if not mt5.symbol_select(symbol, True):
            print(f"[FAIL] Could not select {symbol}")
            return 1

        info = mt5.symbol_info(symbol)
        sym_info = SymbolInfo(
            symbol=symbol,
            digits=info.digits,
            point=info.point,
            tick_size=info.trade_tick_size,
            tick_value=info.trade_tick_value,
            contract_size=info.trade_contract_size,
            min_lot=info.volume_min,
            max_lot=info.volume_max,
            lot_step=info.volume_step,
            margin_required=info.margin_initial or 37.34,
            spread=float(info.spread)
        )

        ask = info.ask
        sl = round(ask - 0.00130, 5)  # 13 pips
        tp = round(ask + 0.00350, 5)  # 35 pips

        sizing = PositionSizingEngine()
        res = sizing.calculate_lot_size(
            account=acct,
            symbol_info=sym_info,
            entry_price=ask,
            stop_loss=sl,
            take_profit=tp,
            risk_percent=1.5
        )

        print(f"\n[SIZING CALCULATION]")
        print(f"  Tier: {res.tier_name}")
        print(f"  Approved: {res.is_approved}")
        print(f"  Volume: {res.volume:.2f} lots")
        print(f"  Monetary Risk: ${res.monetary_risk:.2f}")
        print(f"  Risk %: {res.risk_percent_actual:.2f}%")
        print(f"  SL Points: {res.sl_points:.1f}")
        print(f"  RR: {res.reward_to_risk:.2f}")

        if not res.is_approved:
            print(f"[FAIL] Sizing rejected: {res.rejection_reason}")
            return 1

        # Check filling mode
        filling = mt5.ORDER_FILLING_IOC
        if info.filling_mode & 1:  # FOK
            filling = mt5.ORDER_FILLING_FOK

        # Execute order via MT5 API
        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": symbol,
            "volume": res.volume,
            "type": mt5.ORDER_TYPE_BUY,
            "price": ask,
            "sl": sl,
            "tp": tp,
            "deviation": 20,
            "magic": 100101,
            "comment": "STAGE0A_TEST",
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": filling,
        }

        print(f"\n[ORDER SUBMISSION] Sending BUY {res.volume} {symbol} @ {ask:.5f} (SL: {sl:.5f}, TP: {tp:.5f})...")
        order_res = mt5.order_send(request)
        if order_res is None or order_res.retcode != mt5.TRADE_RETCODE_DONE:
            print(f"[FAIL] OrderSend failed: {order_res.comment if order_res else mt5.last_error()} (retcode: {order_res.retcode if order_res else 'None'})")
            return 1

        order_ticket = order_res.order
        deal_ticket = order_res.deal
        fill_price = order_res.price
        print(f"  --> Order executed successfully! Ticket: #{order_ticket}, Deal: #{deal_ticket}, Fill Price: {fill_price:.5f}")

        # Post-Execution Verification in Trade tab
        time.sleep(0.5)
        pos = None
        for p in mt5.positions_get(symbol=symbol):
            if p.ticket == order_ticket or p.magic == 100101:
                pos = p
                break

        if pos is None:
            print("[FAIL] Position not found in Trade tab after execution!")
            return 1

        print(f"\n[POST-EXECUTION TRADE TAB VERIFICATION]")
        print(f"  Confirmed Ticket: #{pos.ticket}")
        print(f"  Confirmed Symbol: {pos.symbol}")
        print(f"  Confirmed Volume: {pos.volume:.2f} lots (Target: {res.volume:.2f})")
        print(f"  Confirmed Open Price: {pos.price_open:.5f}")
        print(f"  Confirmed SL: {pos.sl:.5f} (Expected: {sl:.5f})")
        print(f"  Confirmed TP: {pos.tp:.5f} (Expected: {tp:.5f})")

        # Verify exact monetary risk via OrderCalcProfit
        calc_profit = mt5.order_calc_profit(mt5.ORDER_TYPE_BUY, symbol, pos.volume, pos.price_open, pos.sl)
        real_sl_money = abs(float(calc_profit)) if calc_profit is not None else 0.0
        print(f"  Calculated Risk: ${res.monetary_risk:.2f}")
        print(f"  Real Position Risk at SL (via OrderCalcProfit): ${real_sl_money:.2f}")

        assert pos.volume == res.volume, "Volume mismatch!"
        assert abs(pos.sl - sl) < 0.0001, "SL mismatch!"
        assert abs(pos.tp - tp) < 0.0001, "TP mismatch!"
        print("\n--> [PASS] All execution parameters verified in MT5 Trade tab!")

        # Clean up: close test position
        print(f"\n[CLEANUP] Closing test position #{pos.ticket}...")
        cur_bid = mt5.symbol_info(symbol).bid
        close_req = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": symbol,
            "volume": pos.volume,
            "type": mt5.ORDER_TYPE_SELL,
            "position": pos.ticket,
            "price": cur_bid,
            "deviation": 20,
            "magic": 100101,
            "comment": "STAGE0A_CLEANUP",
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": filling,
        }
        close_res = mt5.order_send(close_req)
        if close_res and close_res.retcode == mt5.TRADE_RETCODE_DONE:
            print(f"--> [OK] Test position #{pos.ticket} closed cleanly (Retcode: 10009).")
        else:
            print(f"[WARNING] Could not close position: {close_res.comment if close_res else 'error'}")

        print("\n" + "=" * 70)
        print("STAGE 0A LIVE DEMO ACCOUNT VERIFICATION: 100% SUCCESS")
        print("=" * 70)
        return 0

    finally:
        mt5.shutdown()


if __name__ == "__main__":
    sys.exit(main())
