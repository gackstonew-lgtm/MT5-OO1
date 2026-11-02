"""
Advanced Live Order Management & Execution Verification Script for Python-MT5 Demo.
Tests:
1. PING & Heartbeat
2. ACCOUNT query (hedging demo account, balance ~100 USD)
3. RATES query for BTCUSD.m
4. Pending Order Placement (BUY_LIMIT) with server-side SL/TP
5. Pending Order Modification (Price, SL, TP)
6. Pending Order Cancellation (CANCEL)
7. Adaptive SCALP Market Execution (0.02 lots BUY)
8. Partial Close (0.01 lot)
9. Full Close (remaining 0.01 lot)
10. Trading Journal CSV Inspection
"""

import sys
import time
from pathlib import Path

# Ensure project root in sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.execution.bridge_client import BridgeClient
from src.core.contracts import OrderType


def run_advanced_verification():
    print("=" * 80)
    print("  PYTHON - MT5 ADVANCED EXECUTION & ORDER MANAGEMENT VERIFICATION")
    print("  Target Symbol: BTCUSD.m | Broker: JustMarkets-Demo3")
    print("=" * 80)

    bridge = BridgeClient(host="127.0.0.1", port=5555, timeout_ms=5000)

    # Step 0: Start TCP Server
    print("\n[Step 0] Starting Python TCP Bridge Server on 127.0.0.1:5555...")
    if not bridge.start_server():
        print("[-] FAILED: Could not bind TCP server to port 5555.")
        return 1
    print("[+] Python TCP Server listening. Awaiting connection from PythonBridgeEA...")

    connected = bridge.wait_for_connection(timeout_seconds=45.0)
    if not connected:
        print("\n[-] TIMEOUT: PythonBridgeEA did not connect within 45 seconds.")
        bridge.disconnect()
        return 1

    # Step 1: PING
    print("\n" + "-" * 60)
    print("[Step 1] PING / Heartbeat Test...")
    t0 = time.time()
    ping_ok = bridge.ping()
    lat = (time.time() - t0) * 1000.0
    if ping_ok:
        print(f"[+] PING SUCCESS! Round-trip latency: {lat:.1f}ms")
    else:
        print("[-] PING FAILED!")
        bridge.disconnect()
        return 1

    # Step 2: ACCOUNT
    print("\n" + "-" * 60)
    print("[Step 2] Querying Account Details...")
    acc = bridge.get_account()
    if acc:
        print(f"[+] ACCOUNT SUCCESS:")
        print(f"    Account Login: {acc.account_number}")
        print(f"    Balance:       ${acc.balance:.2f} {acc.currency}")
        print(f"    Equity:        ${acc.equity:.2f} {acc.currency}")
        print(f"    Free Margin:   ${acc.free_margin:.2f}")
        print(f"    Leverage:      1:{acc.leverage}")
    else:
        print("[-] ACCOUNT query failed!")
        bridge.disconnect()
        return 1

    # Step 3: RATES (BTCUSD.m)
    symbol = "BTCUSD.m"
    print("\n" + "-" * 60)
    print(f"[Step 3] Querying Market Rates for {symbol} (M15)...")
    rates_res = bridge.get_rates(symbol=symbol, timeframe="M15", count=5)
    if rates_res.get("status") == "SUCCESS" and "data" in rates_res:
        rates = rates_res["data"]
        latest_close = rates[0]["close"]
        print(f"[+] RATES SUCCESS: {len(rates)} bars retrieved. Current/Latest Price: ${latest_close:.2f}")
    else:
        print(f"[-] RATES FAILED: {rates_res.get('message')}")
        bridge.disconnect()
        return 1

    # Step 4: PENDING ORDER (BUY_LIMIT)
    print("\n" + "-" * 60)
    limit_price = round(latest_close - 2000.0, 2)
    limit_sl = round(limit_price - 1500.0, 2)
    limit_tp = round(latest_close + 2000.0, 2)
    print(f"[Step 4] Placing BUY_LIMIT Order @ ${limit_price:.2f} (SL: ${limit_sl:.2f}, TP: ${limit_tp:.2f})...")

    limit_res = bridge.execute_trade(
        symbol=symbol,
        order_type=OrderType.BUY_LIMIT,
        volume=0.01,
        price=limit_price,
        stop_loss=limit_sl,
        take_profit=limit_tp,
        mode="AUTO",
        magic=100101,
        comment="DEMO_LIMIT"
    )

    pending_ticket = 0
    if limit_res.get("status") == "SUCCESS":
        pending_ticket = limit_res.get("ticket")
        print(f"[+] BUY_LIMIT PLACED SUCCESS!")
        print(f"    Pending Ticket: #{pending_ticket}")
        print(f"    Price:          ${limit_res.get('price'):.2f}")
        print(f"    Retcode:        {limit_res.get('retcode')}")
    else:
        print(f"[-] BUY_LIMIT FAILED: {limit_res.get('message')}")
        bridge.disconnect()
        return 1

    # Step 5: MODIFY PENDING ORDER
    print("\n" + "-" * 60)
    new_limit_price = round(limit_price - 500.0, 2)
    new_sl = round(new_limit_price - 1500.0, 2)
    new_tp = round(latest_close + 2500.0, 2)
    print(f"[Step 5] Modifying Pending Ticket #{pending_ticket} to Price: ${new_limit_price:.2f}, SL: ${new_sl:.2f}, TP: ${new_tp:.2f}...")

    mod_res = bridge.modify_order(
        ticket=pending_ticket,
        price=new_limit_price,
        sl=new_sl,
        tp=new_tp
    )
    if mod_res.get("status") == "SUCCESS":
        print(f"[+] MODIFY SUCCESS! Pending Ticket #{pending_ticket} modified.")
    else:
        print(f"[-] MODIFY FAILED: {mod_res.get('message')}")

    # Step 6: CANCEL PENDING ORDER
    print("\n" + "-" * 60)
    print(f"[Step 6] Cancelling Pending Order Ticket #{pending_ticket}...")
    cancel_res = bridge.cancel_order(ticket=pending_ticket)
    if cancel_res.get("status") == "SUCCESS":
        print(f"[+] CANCEL SUCCESS! Pending Order #{pending_ticket} deleted cleanly.")
    else:
        print(f"[-] CANCEL FAILED: {cancel_res.get('message')}")

    # Step 7: ADAPTIVE SCALP MARKET ORDER (0.02 lots for partial close test)
    print("\n" + "-" * 60)
    market_sl = round(latest_close - 1000.0, 2)
    market_tp = round(latest_close + 1500.0, 2)
    print(f"[Step 7] Executing Market BUY 0.02 lots [SCALP Mode] (SL: ${market_sl:.2f}, TP: ${market_tp:.2f})...")

    market_res = bridge.execute_trade(
        symbol=symbol,
        order_type=OrderType.BUY,
        volume=0.02,
        stop_loss=market_sl,
        take_profit=market_tp,
        mode="SCALP",
        magic=100101,
        comment="DEMO_SCALP"
    )

    pos_ticket = 0
    if market_res.get("status") == "SUCCESS":
        pos_ticket = market_res.get("ticket")
        deal_ticket = market_res.get("deal")
        exec_price = market_res.get("price")
        print(f"[+] MARKET BUY SUCCESS!")
        print(f"    Order Ticket: #{pos_ticket}")
        print(f"    Deal Ticket:  #{deal_ticket}")
        print(f"    Exec Price:   ${exec_price:.2f}")
        print(f"    Volume:       {market_res.get('volume')} lots")
        print(f"    Mode Tagged:  {market_res.get('mode')}")
    else:
        print(f"[-] MARKET BUY FAILED: {market_res.get('message')}")
        bridge.disconnect()
        return 1

    # Confirm active position
    time.sleep(1.0)
    pos_list = bridge.get_positions()
    found = any(p.ticket == pos_ticket for p in pos_list)
    print(f"[+] Position active in broker table: {found} (Total open: {len(pos_list)})")

    # Step 8: PARTIAL CLOSE (0.01 lot)
    print("\n" + "-" * 60)
    print(f"[Step 8] Executing PARTIAL CLOSE of 0.01 lot on Ticket #{pos_ticket}...")
    part_res = bridge.close_order(ticket=pos_ticket, volume=0.01)
    if part_res.get("status") == "SUCCESS":
        print(f"[+] PARTIAL CLOSE SUCCESS! 0.01 lot closed on Ticket #{pos_ticket}.")
    else:
        print(f"[-] PARTIAL CLOSE FAILED: {part_res.get('message')}")

    time.sleep(1.0)
    pos_after_part = bridge.get_positions()
    for p in pos_after_part:
        if p.ticket == pos_ticket or p.symbol == symbol:
            print(f"[+] Remaining Position: #{p.ticket} {p.symbol} Volume: {p.volume:.2f} lots | P/L: ${p.profit:.2f}")

    # Step 9: FULL CLOSE REMAINDER
    print("\n" + "-" * 60)
    print(f"[Step 9] Closing remainder of Position #{pos_ticket}...")
    # Ticket may be the original or newly assigned if broker splits
    target_close_ticket = pos_ticket
    if pos_after_part:
        for p in pos_after_part:
            if p.symbol == symbol:
                target_close_ticket = p.ticket
                break

    full_res = bridge.close_order(ticket=target_close_ticket)
    if full_res.get("status") == "SUCCESS":
        print(f"[+] FULL CLOSE SUCCESS! Position #{target_close_ticket} closed completely.")
    else:
        print(f"[-] FULL CLOSE FAILED: {full_res.get('message')}")

    time.sleep(1.0)
    final_positions = bridge.get_positions()
    print(f"[+] Final Open Positions Count: {len(final_positions)}")

    # Step 10: TRADING JOURNAL CSV CHECK
    print("\n" + "-" * 60)
    print("[Step 10] Checking Broker Terminal Trading Journal CSV...")
    journal_path = Path("C:/Users/Gackstone_Baraka/AppData/Roaming/MetaQuotes/Terminal/D0E8209F77C8CF37AD8BF550E51FF075/MQL5/Files/TradingJournal_BTCUSD.m.csv")
    if journal_path.exists():
        print(f"[+] Journal Found: {journal_path}")
        lines = journal_path.read_text(encoding='utf-8', errors='ignore').strip().split('\n')
        print(f"    Recorded Rows ({len(lines)} lines):")
        for line in lines[-6:]:
            print(f"      {line}")
    else:
        print(f"[-] Journal file not yet created at {journal_path}")

    print("\n" + "=" * 80)
    print("  ALL ADVANCED EXECUTION & ORDER MANAGEMENT TESTS COMPLETED!")
    print("=" * 80)

    bridge.disconnect()
    return 0


if __name__ == "__main__":
    sys.exit(run_advanced_verification())
