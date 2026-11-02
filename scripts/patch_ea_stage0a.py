"""
Stage 0A MQL5 EA Patcher & Compiler.
Patches PythonBridgeEA.mq5 with Stage 0A Account-Size Adaptation,
and compiles via MetaEditor64.exe ensuring 0 errors, 0 warnings.
"""

import os
import subprocess
import sys

EA_PATH = "mql5/Experts/PythonBridgeEA.mq5"
METAEDITOR_PATH = r"C:\Program Files\MetaTrader 5\MetaEditor64.exe"
COMPILE_LOG = "compile_stage0a.log"


def patch_ea():
    with open(EA_PATH, "r", encoding="utf-8") as f:
        content = f.read()

    # 1. Inputs patch
    target_inputs = """input int                  InpDailyResetHour        = 0;                   // Daily Reset Server Hour (0-23)
input int                  InpDailyResetTzOffset    = 0;                   // Daily Reset Timezone Offset (hours)
input double               InpMaxRiskPercent        = 1.0;                 // Default Risk % Per Trade"""

    replacement_inputs = """input int                  InpDailyResetHour        = 0;                   // Daily Reset Server Hour (0-23)
input int                  InpDailyResetTzOffset    = 0;                   // Daily Reset Timezone Offset (hours)
input double               InpMinCapitalFloor       = 5.0;                 // Minimum Operating Capital Floor ($)
input bool                 InpMicroAccountMinLotAllowed = false;          // Allow trading min lot on micro accounts (default false)
input bool                 InpAcknowledgedHighRiskMicroaccount = false;   // Explicit operator risk acknowledgment (default false)
input double               InpAbsoluteRiskCeiling   = 5.0;                 // Absolute maximum risk ceiling %
input double               InpMaxRiskPercent        = 1.0;                 // Default Risk % Per Trade"""

    if target_inputs in content:
        content = content.replace(target_inputs, replacement_inputs)
        print("[OK] Inputs patched.")
    else:
        print("[INFO] Inputs already patched or target modified.")

    # 2. CalculateLotSize patch
    target_calc = """double CalculateLotSize(const string sym, const double risk_pct, const double sl_distance_points, string &sizing_log)
{
   double balance = AccountInfoDouble(ACCOUNT_BALANCE);
   double equity  = AccountInfoDouble(ACCOUNT_EQUITY);
   double capital_base = MathMin(balance, equity);
   if(capital_base <= 0.0)
   {
      sizing_log = "Invalid capital base (<= 0)";
      return 0.0;
   }

   // Check reduced history factor
   double allowed_cap = InpMaxRiskPercentPerTrade;
   if(GetSymbolState(sym) == SYM_STATE_READY_REDUCED)
   {
      allowed_cap *= InpReducedHistoryRiskFactor;
   }

   double target_risk_pct = MathMin(risk_pct, allowed_cap);
   double target_risk_amount = capital_base * (target_risk_pct / 100.0);

   double tick_size  = SymbolInfoDouble(sym, SYMBOL_TRADE_TICK_SIZE);
   double tick_value = SymbolInfoDouble(sym, SYMBOL_TRADE_TICK_VALUE);
   double pt         = SymbolInfoDouble(sym, SYMBOL_POINT);
   if(pt <= 0.0) pt = 0.01;
   if(tick_size <= 0.0) tick_size = pt;

   if(sl_distance_points <= 0 || tick_value <= 0)
   {
      sizing_log = "Invalid SL distance or tick value";
      return 0.0;
   }

   double num_ticks = (sl_distance_points * pt) / tick_size;
   double loss_per_lot = num_ticks * tick_value;
   if(loss_per_lot <= 0)
   {
      sizing_log = "Loss per lot calculation non-positive";
      return 0.0;
   }

   double raw_lots = target_risk_amount / loss_per_lot;
   double step     = SymbolInfoDouble(sym, SYMBOL_VOLUME_STEP);
   double min_lot  = SymbolInfoDouble(sym, SYMBOL_VOLUME_MIN);

   // Hard lot cap: accounts under $500 default no larger than min_lot * 3
   double dynamic_max_lot = (capital_base < 500.0) ? MathMin(min_lot * 3.0, InpMaxLot) : InpMaxLot;
   double max_lot  = MathMin(SymbolInfoDouble(sym, SYMBOL_VOLUME_MAX), dynamic_max_lot);

   double min_lot_risk = min_lot * loss_per_lot;
   double min_lot_risk_pct = (min_lot_risk / capital_base) * 100.0;

   // If minimum lot already risks more than allowed cap, skip and report!
   if(min_lot_risk_pct > allowed_cap)
   {
      sizing_log = StringFormat("Min lot %.2f risks $%.2f (%.2f%% of capital $%.2f), exceeding max risk cap (%.2f%%%s)",
                                min_lot, min_lot_risk, min_lot_risk_pct, capital_base, allowed_cap,
                                GetSymbolState(sym) == SYM_STATE_READY_REDUCED ? " [REDUCED_HIST]" : "");
      PrintFormat("[PythonBridgeEA] [SIZING] %s", sizing_log);
      return 0.0;
   }

   // Strictly round down to volume step (never round up!)
   double steps = MathFloor(raw_lots / step + 1e-8);
   double quantized = steps * step;

   if(quantized < min_lot) quantized = min_lot;
   if(quantized > max_lot) quantized = max_lot;

   double final_lots = NormalizeDouble(quantized, 2);
   double actual_risk_pct = ((final_lots * loss_per_lot) / capital_base) * 100.0;
   sizing_log = StringFormat("Sized %.2f lots: risks $%.2f (%.2f%% of capital $%.2f with SL %.1f pts)",
                             final_lots, final_lots * loss_per_lot, actual_risk_pct, capital_base, sl_distance_points);
   PrintFormat("[PythonBridgeEA] [SIZING] %s", sizing_log);
   return final_lots;
}"""

    replacement_calc = """double CalculateLotSize(const string sym, const double risk_pct, const double sl_distance_points, string &sizing_log)
{
   double balance = AccountInfoDouble(ACCOUNT_BALANCE);
   double equity  = AccountInfoDouble(ACCOUNT_EQUITY);
   string cur = AccountInfoString(ACCOUNT_CURRENCY);
   bool is_cent = (cur == "USC" || cur == "EUC" || cur == "GBX" || cur == "JPC");
   double raw_bal = balance;
   double raw_eq  = equity;
   if(is_cent)
   {
      balance /= 100.0;
      equity  /= 100.0;
   }
   double capital_base = MathMin(balance, equity);
   if(capital_base < InpMinCapitalFloor)
   {
      sizing_log = StringFormat("Account capital $%.2f is below the $%.2f minimum operating floor. Trading disabled.",
                                capital_base, InpMinCapitalFloor);
      PrintFormat("[PythonBridgeEA] [SIZING] %s", sizing_log);
      return 0.0;
   }

   // Check reduced history factor
   double allowed_cap = InpMaxRiskPercentPerTrade;
   if(GetSymbolState(sym) == SYM_STATE_READY_REDUCED)
   {
      allowed_cap *= InpReducedHistoryRiskFactor;
   }

   double target_risk_pct = MathMin(risk_pct, allowed_cap);
   double target_risk_amount = capital_base * (target_risk_pct / 100.0);

   double tick_size  = SymbolInfoDouble(sym, SYMBOL_TRADE_TICK_SIZE);
   double tick_value = SymbolInfoDouble(sym, SYMBOL_TRADE_TICK_VALUE);
   double pt         = SymbolInfoDouble(sym, SYMBOL_POINT);
   if(pt <= 0.0) pt = 0.01;
   if(tick_size <= 0.0) tick_size = pt;

   if(sl_distance_points <= 0 || tick_value <= 0)
   {
      sizing_log = "Invalid SL distance or tick value";
      return 0.0;
   }

   double num_ticks = (sl_distance_points * pt) / tick_size;
   double loss_per_lot = num_ticks * tick_value;

   // OrderCalcProfit verification
   double calc_loss = 0.0;
   double ask = SymbolInfoDouble(sym, SYMBOL_ASK);
   if(ask > 0 && OrderCalcProfit(ORDER_TYPE_BUY, sym, 1.0, ask, ask - (sl_distance_points * pt), calc_loss))
   {
      if(calc_loss < 0.0) loss_per_lot = MathAbs(calc_loss);
   }

   if(loss_per_lot <= 0)
   {
      sizing_log = "Loss per lot calculation non-positive";
      return 0.0;
   }

   double raw_lots = target_risk_amount / loss_per_lot;
   double step     = SymbolInfoDouble(sym, SYMBOL_VOLUME_STEP);
   double min_lot  = SymbolInfoDouble(sym, SYMBOL_VOLUME_MIN);

   // Dynamic lot cap: accounts under $500 default no larger than min_lot * 3
   double dynamic_max_lot = (capital_base < 500.0) ? MathMin(min_lot * 3.0, InpMaxLot) : InpMaxLot;
   double max_lot  = MathMin(SymbolInfoDouble(sym, SYMBOL_VOLUME_MAX), dynamic_max_lot);

   double min_lot_risk = min_lot * loss_per_lot;
   double min_lot_risk_pct = (min_lot_risk / capital_base) * 100.0;
   double min_bal_needed = min_lot_risk / (target_risk_pct / 100.0);

   // Strictly round down to volume step (never round up!)
   double steps = MathFloor(raw_lots / step + 1e-8);
   double quantized = steps * step;

   if(quantized < min_lot)
   {
      if(InpMicroAccountMinLotAllowed && InpAcknowledgedHighRiskMicroaccount && min_lot_risk_pct <= InpAbsoluteRiskCeiling)
      {
         quantized = min_lot;
         PrintFormat("[PythonBridgeEA] [SIZING] Micro-Account Exception Granted: MinLot %.2f risks $%.2f (%.2f%% <= Ceiling %.2f%%)",
                     min_lot, min_lot_risk, min_lot_risk_pct, InpAbsoluteRiskCeiling);
      }
      else
      {
         sizing_log = StringFormat("Min lot %.2f risks $%.2f (%.2f%% of capital $%.2f), exceeding target risk %.2f%% (Ceiling: %.2f%%). Raw lot: %.4f. Min bal needed: $%.2f",
                                   min_lot, min_lot_risk, min_lot_risk_pct, capital_base, target_risk_pct, InpAbsoluteRiskCeiling, raw_lots, min_bal_needed);
         PrintFormat("[PythonBridgeEA] [SIZING] %s", sizing_log);
         return 0.0;
      }
   }

   if(quantized > max_lot) quantized = max_lot;

   double final_lots = NormalizeDouble(quantized, 2);
   double actual_risk_pct = ((final_lots * loss_per_lot) / capital_base) * 100.0;
   string unit_info = is_cent ? StringFormat("Bal: %.0f USC ($%.2f USD)", raw_bal, balance) : StringFormat("Bal: $%.2f %s", balance, cur);
   sizing_log = StringFormat("Sized %.2f lots: risks $%.2f (%.2f%% of %s with SL %.1f pts)",
                             final_lots, final_lots * loss_per_lot, actual_risk_pct, unit_info, sl_distance_points);
   PrintFormat("[PythonBridgeEA] [SIZING] %s", sizing_log);
   return final_lots;
}"""

    if target_calc in content:
        content = content.replace(target_calc, replacement_calc)
        print("[OK] CalculateLotSize patched.")
    else:
        print("[INFO] CalculateLotSize already patched or target modified.")

    with open(EA_PATH, "w", encoding="utf-8") as f:
        f.write(content)
    print(f"[OK] {EA_PATH} written successfully.")


def compile_ea():
    print(f"\nCompiling {EA_PATH} with {METAEDITOR_PATH}...")
    abs_ea = os.path.abspath(EA_PATH)
    abs_log = os.path.abspath(COMPILE_LOG)

    cmd = [
        METAEDITOR_PATH,
        f"/compile:{abs_ea}",
        f"/log:{abs_log}"
    ]
    res = subprocess.run(cmd, capture_output=True, text=True)

    if os.path.exists(abs_log):
        log_content = ""
        for enc in ["utf-16", "utf-8", "cp1252"]:
            try:
                with open(abs_log, "r", encoding=enc) as f:
                    log_content = f.read()
                if log_content:
                    break
            except Exception:
                continue

        print("\n--- MetaEditor Compilation Log Output ---")
        lines = [line.strip() for line in log_content.splitlines() if line.strip()]
        for l in lines[-10:]:
            print("  " + l)

        if "0 errors, 0 warnings" in log_content or "0 error(s), 0 warning(s)" in log_content:
            print("\n[PASS] MetaEditor Compilation SUCCESS: 0 errors, 0 warnings!")
            return 0
        else:
            print("\n[FAIL] Compilation had errors or warnings:")
            print(log_content)
            return 1
    else:
        print(f"[FAIL] Compile log {abs_log} not generated.")
        return 1


if __name__ == "__main__":
    patch_ea()
    code = compile_ea()
    sys.exit(code)
