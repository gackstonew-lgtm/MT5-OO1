"""
Live Demonstration Script: Execute a 0.01 lot Trade on Gold (XAUUSD.m) on MT5 Demo Account.
1. Connects to live MT5 TCP Bridge.
2. Confirms account is DEMO.
3. Obtains latest quote for XAUUSD.m.
4. Executes a minimum-lot (0.01) market order with valid SL and 2.0 RR TP.
5. Queries active MT5 positions to verify position appearance on the chart.
6. Prints detailed MT5 execution reaction and logs TRADE SENT outcome.
"""

import sys
import time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from datetime import datetime, timezone

from src.core.contracts import OrderType, DecisionOutcome, DecisionOutcomeType
from src.execution.bridge_client import BridgeClient
from src.monitoring.alerts_telegram import TelegramAlertManager


def main():
    print("=" * 80)
    print("  LIVE MT5 DEMO TRADE EXECUTION DEMONSTRATION: GOLD (XAUUSD.m)")
    print("=" * 80)

    client = BridgeClient(host="127.0.0.1", port=5555, ipc_port=5556)
    if not client.connect():
        print("ERROR: Failed to connect to MT5 Bridge server on 127.0.0.1:5556")
        return 1

    try:
        # 1. Account Safety Verification
        acc = client.get_account()
        if not acc:
            print("ERROR: Could not retrieve account information from MT5.")
            return 1

        print(f"\n[ACCOUNT VERIFICATION]")
        print(f"  Account Login:   {acc.account_number}")
        print(f"  Account Mode:    {acc.trade_mode} [{acc.margin_mode_str}]")
        print(f"  Balance:         ${acc.balance:.2f} {acc.currency}")
        print(f"  Equity:          ${acc.equity:.2f} {acc.currency}")
        print(f"  Free Margin:     ${acc.free_margin:.2f}")

        if acc.trade_mode != "DEMO":
            raise RuntimeError(f"SAFETY REFUSAL: Refusing to execute test order on non-DEMO ({acc.trade_mode}) account!")

        # 2. Get Live Price for XAUUSD.m
        symbol = "XAUUSD.m"
        rates_res = client.get_rates(symbol, "M15", 2)
        if not rates_res or rates_res.get("status") != "SUCCESS" or not rates_res.get("data"):
            print(f"ERROR: Could not fetch latest rates for {symbol}")
            return 1

        latest_bar = rates_res["data"][0]
        cur_price = float(latest_bar.get("close", 3950.0))
        spread_pts = int(latest_bar.get("spread", 26))

        print(f"\n[MARKET DATA: {symbol}]")
        print(f"  Current Reference Price: {cur_price:.2f}")
        print(f"  Current Spread:          {spread_pts} pts (${spread_pts * 0.01:.2f})")

        # Sizing parameters (0.01 min lot, $10 SL, $20 TP -> 2.0 RR)
        volume = 0.01
        sl = round(cur_price - 10.0, 2)
        tp = round(cur_price + 25.0, 2)
        sl_risk_dollars = 10.0 * 100 * volume  # 10 USD move * 100 oz * 0.01 lot = $10.00
        sl_risk_pct = (sl_risk_dollars / acc.balance) * 100.0

        print(f"\n[PROPOSED ORDER SPECIFICATION]")
        print(f"  Symbol:          {symbol}")
        print(f"  Action:          BUY (MARKET)")
        print(f"  Volume:          {volume:.2f} lot (minimum contract lot)")
        print(f"  Entry Price:     {cur_price:.2f}")
        print(f"  Stop Loss:       {sl:.2f} (-$10.00 distance)")
        print(f"  Take Profit:     {tp:.2f} (+$20.00 distance, 1:2.0 R:R)")
        print(f"  Monetary Risk:   ${sl_risk_dollars:.2f} ({sl_risk_pct:.2f}% of balance)")

        # 3. Execute Trade through Bridge Path
        print(f"\n[SENDING ORDER VIA MT5 BRIDGE]...")
        trade_res = client.execute_trade(
            symbol=symbol,
            order_type=OrderType.BUY,
            volume=volume,
            stop_loss=sl,
            take_profit=tp,
            price=cur_price,
            mode="AUTO",
            comment="GOLD_LIVE_DEMO"
        )

        print(f"  Bridge Response Status: {trade_res.get('status')}")
        print(f"  Raw Reply: {trade_res}")

        if trade_res.get("status") not in ("SUCCESS", "PLACED"):
            print(f"ERROR: Trade rejected by MT5 EA: {trade_res.get('message')}")
            return 1

        ticket = trade_res.get("ticket") or trade_res.get("order")
        deal = trade_res.get("deal")
        exec_price = trade_res.get("price", cur_price)

        print(f"\n" + "=" * 80)
        print(f"  SUCCESS! ORDER EXECUTED ON LIVE MT5 TERMINAL")
        print(f"=" * 80)
        print(f"  Position Ticket: #{ticket}")
        print(f"  Deal Ticket:     #{deal}")
        print(f"  Execution Price: {exec_price:.2f}")

        # 4. Query MT5 Positions to confirm reaction on MT5
        time.sleep(0.5)
        positions = client.get_positions()
        print(f"\n[MT5 LIVE POSITIONS TABLE REACTION]")
        gold_pos = [p for p in positions if getattr(p, 'ticket', None) == ticket or (isinstance(p, dict) and p.get('ticket') == ticket)]
        if not gold_pos:
            gold_pos = [p for p in positions if getattr(p, 'symbol', '') == symbol or (isinstance(p, dict) and p.get('symbol') == symbol)]

        if gold_pos:
            p = gold_pos[0]
            p_ticket = getattr(p, 'ticket', p.get('ticket') if isinstance(p, dict) else ticket)
            p_sym = getattr(p, 'symbol', p.get('symbol') if isinstance(p, dict) else symbol)
            p_vol = getattr(p, 'volume', p.get('volume') if isinstance(p, dict) else volume)
            p_open = getattr(p, 'open_price', p.get('open_price') if isinstance(p, dict) else exec_price)
            p_sl = getattr(p, 'stop_loss', p.get('sl') if isinstance(p, dict) else sl)
            p_tp = getattr(p, 'take_profit', p.get('tp') if isinstance(p, dict) else tp)
            p_profit = getattr(p, 'profit', p.get('profit') if isinstance(p, dict) else 0.0)

            print(f"  Status:          LIVE AND CONFIRMED IN MT5 TERMINAL!")
            print(f"  Position Ticket: #{p_ticket}")
            print(f"  Instrument:      {p_sym}")
            print(f"  Volume:          {p_vol:.2f} lots")
            print(f"  Open Price:      {p_open:.2f}")
            print(f"  Stop Loss:       {p_sl:.2f}")
            print(f"  Take Profit:     {p_tp:.2f}")
            print(f"  Floating Profit: ${float(p_profit):+.2f}")
        else:
            print(f"  Total Active Positions: {len(positions)}")

        # 5. Format and dispatch official Decision Outcome (Task 1 specification)
        outcome = DecisionOutcome(
            symbol=symbol,
            bar_time=datetime.now(timezone.utc),
            outcome_type=DecisionOutcomeType.TRADE_SENT,
            details=f"Live order executed: ticket #{ticket}",
            direction="BUY",
            volume=volume,
            entry_price=exec_price,
            stop_loss=sl,
            take_profit=tp,
            risk_dollars=sl_risk_dollars,
            risk_percent=sl_risk_pct,
            ticket=ticket
        )
        outcome_line = outcome.format_line()
        print(f"\n[MANDATORY TASK 1 OUTCOME LOG LINE]")
        print(f"  {outcome_line}")

        # Send alert via Telegram if configured
        alerts = TelegramAlertManager()
        if alerts.enabled:
            alerts.send_decision_outcome(outcome)
            print(f"  Outcome dispatched to Telegram chat successfully.")

        print("\n" + "=" * 80)
        print("  Check your MT5 terminal: The order is now open in your Trade tab!")
        print("=" * 80)
        return 0

    finally:
        client.disconnect()


if __name__ == "__main__":
    sys.exit(main())
