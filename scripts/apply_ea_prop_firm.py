"""
Applies Prop Firm & Multi-Profile Architecture to PythonBridgeEA.mq5:
- Replaces demo-only guard with multi-profile engine (DEMO, LIVE, PROP).
- Adds ENUM_ACCOUNT_PROFILE and ENUM_DRAWDOWN_TYPE.
- Adds inputs: InpAccountProfile, InpAllowedAccount, InpTradingEnabled, InpDrawdownType,
  InpSafetyBufferPercent, InpProfitTargetPercent, InpMinTradingDays, InpDailyResetHour,
  InpDailyResetTzOffset, plus prop firm rule toggles (martingale, grid, hedging, SL required,
  min duration, weekend close, news filter, tick scalping).
- Tracks initial balance, peak equity, daily start equity/balance, trading days count.
- Enforces real-time equity tracking and safety buffer (80%) + hard limit liquidation (95%).
- Economic News filter via MQL5 Calendar API.
- Pre-trade validation of all prop firm rules.
- Adds ENABLE and DISABLE commands.
- Enhanced ACCOUNT and DAILY_SUMMARY responses with challenge tracker metrics.
"""

import os
import shutil

ea_path = r"mql5\Experts\PythonBridgeEA.mq5"
backup_path = r"mql5\Experts\PythonBridgeEA.mq5.bak"

shutil.copyfile(ea_path, backup_path)
print(f"Backed up {ea_path} to {backup_path}")

with open(ea_path, "r", encoding="utf-8") as f:
    content = f.read()

# 1. Update Enums: Add ENUM_ACCOUNT_PROFILE and ENUM_DRAWDOWN_TYPE
old_enums = """enum ENUM_TRAIL_MODE
{
   TRAIL_ATR    = 0, // ATR-Based Trailing
   TRAIL_POINTS = 1, // Fixed Points Trailing
   TRAIL_STEP   = 2, // Stepped Points Trailing
   TRAIL_SWING  = 3  // Swing Low/High Trailing
};"""

new_enums = """enum ENUM_TRAIL_MODE
{
   TRAIL_ATR    = 0, // ATR-Based Trailing
   TRAIL_POINTS = 1, // Fixed Points Trailing
   TRAIL_STEP   = 2, // Stepped Points Trailing
   TRAIL_SWING  = 3  // Swing Low/High Trailing
};

enum ENUM_ACCOUNT_PROFILE
{
   PROFILE_DEMO = 0, // DEMO (Testing environment)
   PROFILE_LIVE = 1, // LIVE (Real capital preservation)
   PROFILE_PROP = 2  // PROP (Strict prop firm challenge / funded rules)
};

enum ENUM_DRAWDOWN_TYPE
{
   DRAWDOWN_STATIC              = 0, // STATIC (from initial balance)
   DRAWDOWN_TRAILING            = 1, // TRAILING (from peak equity)
   DRAWDOWN_DAILY_START_EQUITY  = 2, // From daily start equity
   DRAWDOWN_DAILY_START_BALANCE = 3  // From daily start balance
};"""

assert old_enums in content, "old_enums not found"
content = content.replace(old_enums, new_enums, 1)

# 2. Update Inputs: Replace Live Execution Gate with Account Profile & Prop Firm Rules
old_gate_inputs = """//--- Input Parameters: Live Execution Gate & Risk Controls
input group "=== Live Execution Gate & Hard Limits ==="
input bool                InpAllowLiveTrading      = false;       // Live Trading Master Switch (false = Demo only)
input string              InpLiveConfirmText       = "";          // Type exact confirmation phrase
input long                InpAllowedAccount        = 0;           // Allowed Account Number (0 = Any Demo)
input bool                InpDemoOnly              = true;        // Refuse Live Accounts Strictly
input double              InpMaxLot                = 0.5;         // Hard Cap: Maximum Lot Size
input int                 InpMaxPositions          = 3;           // Hard Cap: Max Open Positions
input double              InpMaxRiskPercent        = 1.0;         // Default Risk % Per Trade
input double              InpMaxRiskPercentPerTrade= 2.0;         // Hard Cap: Max Risk % Per Trade
input double              InpMaxDailyLossPercent   = 2.0;         // Hard Cap: Max Daily Loss %
input double              InpMaxDrawdownPercent    = 5.0;         // Hard Cap: Max Drawdown %
input int                 InpMaxTradesPerDay       = 10;          // Hard Cap: Max Trades Per Day"""

new_gate_inputs = """//--- Input Parameters: Account Profile & Execution Authorization
input group "=== Account Profile & Execution Authorization ==="
input ENUM_ACCOUNT_PROFILE InpAccountProfile          = PROFILE_DEMO;        // Account Profile (DEMO / LIVE / PROP)
input long                 InpAllowedAccount        = 0;                   // Allowed Account Number (0 = Any)
input bool                 InpTradingEnabled        = false;               // Trading Execution Enabled (First install safety)

//--- Input Parameters: Risk Limits & Drawdown Controls
input group "=== Risk Limits & Drawdown Controls ==="
input ENUM_DRAWDOWN_TYPE   InpDrawdownType          = DRAWDOWN_TRAILING;   // Drawdown Mode (STATIC/TRAILING/DAILY)
input double               InpMaxDailyLossPercent   = 4.0;                 // Max Daily Loss % (Hard Limit)
input double               InpMaxTotalDrawdownPercent= 8.0;                // Max Total Drawdown % (Hard Limit)
input double               InpSafetyBufferPercent   = 80.0;                // Safety Buffer % (Stop opening trades at 80% of limit)
input double               InpProfitTargetPercent   = 10.0;                // Profit Target % (Challenge tracking)
input int                  InpMinTradingDays        = 4;                   // Minimum Trading Days (Challenge tracking)
input int                  InpDailyResetHour        = 0;                   // Daily Reset Server Hour (0-23)
input int                  InpDailyResetTzOffset    = 0;                   // Daily Reset Timezone Offset (hours)
input double               InpMaxRiskPercent        = 1.0;                 // Default Risk % Per Trade
input double               InpMaxRiskPercentPerTrade= 2.0;                 // Hard Cap: Max Risk % Per Trade
input double               InpMaxLot                = 0.5;                 // Hard Cap: Maximum Lot Size
input int                  InpMaxPositions          = 3;                   // Hard Cap: Max Open Positions
input int                  InpMaxTradesPerDay       = 10;                  // Hard Cap: Max Trades Per Day
input int                  InpMaxConsecutiveLosses  = 3;                   // Consecutive Losses Pause Trigger

//--- Input Parameters: Prop Firm Rule Toggles
input group "=== Prop Firm Rule Toggles ==="
input bool                 InpNoMartingale          = true;                // Prohibit Martingale (no lot increase after loss)
input bool                 InpNoGridAveraging       = true;                // Prohibit Grid / Averaging down in drawdown
input bool                 InpNoHedging             = true;                // Prohibit Simultaneous Buy & Sell on same symbol
input bool                 InpRequireSL             = true;                // Require Stop Loss on every order strictly
input int                  InpMinTradeDurationSec   = 60;                  // Minimum Trade Duration in seconds (Tick scalping ban)
input bool                 InpNoWeekendHolding      = true;                // Close positions before Friday market close
input int                  InpWeekendCloseHourFriday= 21;                  // Friday close hour (e.g. 21:00 or 22:00)
input bool                 InpNewsFilterEnabled     = true;                // Enable High-Impact Economic News Blackout
input int                  InpNewsBlockMinutesBefore= 15;                  // Minutes to block before news
input int                  InpNewsBlockMinutesAfter = 15;                  // Minutes to block after news
input bool                 InpForbidTickScalping    = true;                // Block ultra-short latency scalping"""

assert old_gate_inputs in content, "old_gate_inputs not found"
content = content.replace(old_gate_inputs, new_gate_inputs, 1)

# Remove duplicate InpMaxConsecutiveLosses further down if present
old_loss_streak = "input int                 InpMaxConsecutiveLosses  = 3;           // Consecutive Losses Pause Trigger\n"
if old_loss_streak in content:
    content = content.replace(old_loss_streak, "", 1)

# 3. Add Global State Variables for Prop Firm & Challenge Tracking
old_globals = """// Daily Risk Metrics
datetime       g_current_day = 0;
double         g_day_start_equity = 0.0;
double         g_daily_loss = 0.0;
int            g_trades_today = 0;
int            g_consecutive_losses = 0;
bool           g_risk_halted = false;
string         g_risk_halt_reason = "";"""

new_globals = """// Dynamic Trading State & Prop Tracking
bool           g_trading_enabled = false;
double         g_initial_balance = 0.0;
double         g_peak_equity = 0.0;
double         g_day_start_balance = 0.0;
int            g_trading_days_count = 0;
bool           g_buffer_halted = false;
bool           g_hard_locked_today = false;
bool           g_buffer_alerted_today = false;

// Daily Risk Metrics
datetime       g_current_day = 0;
double         g_day_start_equity = 0.0;
double         g_daily_loss = 0.0;
int            g_trades_today = 0;
int            g_consecutive_losses = 0;
bool           g_risk_halted = false;
string         g_risk_halt_reason = "";"""

assert old_globals in content, "old_globals not found"
content = content.replace(old_globals, new_globals, 1)

# Update forward declarations
old_fwd = """bool   ValidateLiveGate(string &reason);
bool   ValidatePreTrade(const string sym, const double volume, string &reason);"""

new_fwd = """bool   ValidatePreTrade(const string sym, const double volume, const double sl, const double tp, const string type_str, string &reason);
void   EvaluateRealTimeRisk();
int    CloseAllPositions();
bool   IsNewsBlackoutActive(const string sym, string &news_details);
void   UpdateDashboardVisuals();"""

assert old_fwd in content, "old_fwd not found"
content = content.replace(old_fwd, new_fwd, 1)

# 4. Update OnInit: Remove ValidateLiveGate check, add Account Mode detection & startup notification
old_oninit_risk = """   // 3. Execution Gate Check
   string gate_err = "";
   if(!ValidateLiveGate(gate_err))
   {
      PrintFormat("[PythonBridgeEA] [GATE] Execution gate note: %s", gate_err);
   }

   g_trade.SetExpertMagicNumber(InpMagicNumber);
   g_trade.SetMarginMode();
   ConfigureFillingMode(_Symbol);

   // 4. Initialize Indicators for Adaptive Modes & Spread Filter
   g_atr_handle_m5     = iATR(_Symbol, PERIOD_M5, InpATRPeriod);
   g_atr_handle_h1     = iATR(_Symbol, PERIOD_H1, InpATRPeriod);
   g_adx_handle_m5     = iADX(_Symbol, PERIOD_M5, InpATRPeriod);
   g_spread_atr_handle = iATR(_Symbol, InpSpreadATRTimeframe, InpSpreadATRPeriod);

   // 5. Daily Risk Tracker Calibration
   g_current_day = TimeCurrent() / 86400 * 86400;
   g_day_start_equity = AccountInfoDouble(ACCOUNT_EQUITY);
   g_daily_loss = 0.0;
   g_trades_today = 0;
   g_consecutive_losses = 0;
   g_risk_halted = false;
   g_last_heartbeat_time = TimeCurrent();"""

new_oninit_risk = """   // 3. Account Mode & Profile Identification
   ENUM_ACCOUNT_TRADE_MODE trade_mode = (ENUM_ACCOUNT_TRADE_MODE)AccountInfoInteger(ACCOUNT_TRADE_MODE);
   string mode_str = (trade_mode == ACCOUNT_TRADE_MODE_DEMO ? "DEMO" :
                     (trade_mode == ACCOUNT_TRADE_MODE_REAL ? "REAL" : "CONTEST"));
   long login = AccountInfoInteger(ACCOUNT_LOGIN);
   string server = AccountInfoString(ACCOUNT_SERVER);
   string profile_str = (InpAccountProfile == PROFILE_DEMO ? "DEMO" :
                        (InpAccountProfile == PROFILE_LIVE ? "LIVE" : "PROP"));

   // Initialize trading enabled state
   string enabled_key = StringFormat("SMC_ENABLED_%d_%d", InpMagicNumber, (int)login);
   if(GlobalVariableCheck(enabled_key))
      g_trading_enabled = (GlobalVariableGet(enabled_key) > 0.5);
   else
   {
      // If DEMO, default to InpTradingEnabled or true if demo; LIVE/PROP strictly respect InpTradingEnabled
      g_trading_enabled = (InpAccountProfile == PROFILE_DEMO) ? (InpTradingEnabled || true) : InpTradingEnabled;
      GlobalVariableSet(enabled_key, g_trading_enabled ? 1.0 : 0.0);
   }

   // Initialize baseline balance and peak equity
   string init_bal_key = StringFormat("SMC_INITBAL_%d_%d", InpMagicNumber, (int)login);
   if(GlobalVariableCheck(init_bal_key))
      g_initial_balance = GlobalVariableGet(init_bal_key);
   else
   {
      g_initial_balance = AccountInfoDouble(ACCOUNT_BALANCE);
      GlobalVariableSet(init_bal_key, g_initial_balance);
   }

   string peak_eq_key = StringFormat("SMC_PEAKEQ_%d_%d", InpMagicNumber, (int)login);
   if(GlobalVariableCheck(peak_eq_key))
      g_peak_equity = GlobalVariableGet(peak_eq_key);
   else
   {
      g_peak_equity = MathMax(g_initial_balance, AccountInfoDouble(ACCOUNT_EQUITY));
      GlobalVariableSet(peak_eq_key, g_peak_equity);
   }

   string days_key = StringFormat("SMC_DAYS_%d_%d", InpMagicNumber, (int)login);
   if(GlobalVariableCheck(days_key))
      g_trading_days_count = (int)GlobalVariableGet(days_key);
   else
   {
      g_trading_days_count = 0;
      GlobalVariableSet(days_key, 0.0);
   }

   PrintFormat("[PythonBridgeEA] [STARTUP] Account: %d (%s) | Server: %s | Profile: %s | TradingAuthorized: %s | InitialBal: $%.2f | PeakEq: $%.2f",
               login, mode_str, server, profile_str, (g_trading_enabled ? "YES" : "NO (Awaiting Enable)"),
               g_initial_balance, g_peak_equity);

   if(InpTelegramFallbackEnabled)
   {
      string start_alert = StringFormat(
         "🚀 <b>BOT INITIALIZED</b>\\n"
         "━━━━━━━━━━━━━━━━━━━━\\n"
         "👤 <b>Account:</b> <code>#%d</code> (%s)\\n"
         "🏢 <b>Server:</b> <code>%s</code>\\n"
         "📋 <b>Profile:</b> <code>%s</code>\\n"
         "🚦 <b>Trading Authorized:</b> <code>%s</code>\\n"
         "📉 <b>Daily Loss Cap:</b> <code>%.1f%%</code> (Buffer: <code>%.0f%%</code>)\\n"
         "🛡️ <b>Drawdown Cap:</b> <code>%.1f%%</code> (%s)\\n"
         "🎯 <b>Target:</b> <code>%.1f%%</code> | <b>Min Days:</b> <code>%d</code>",
         login, mode_str, server, profile_str,
         (g_trading_enabled ? "YES" : "NO (Awaiting Authorization)"),
         InpMaxDailyLossPercent, InpSafetyBufferPercent,
         InpMaxTotalDrawdownPercent,
         (InpDrawdownType == DRAWDOWN_TRAILING ? "TRAILING" : "STATIC"),
         InpProfitTargetPercent, InpMinTradingDays
      );
      SendTelegramFallbackAlert(start_alert);
   }

   g_trade.SetExpertMagicNumber(InpMagicNumber);
   g_trade.SetMarginMode();
   ConfigureFillingMode(_Symbol);

   // 4. Initialize Indicators for Adaptive Modes & Spread Filter
   g_atr_handle_m5     = iATR(_Symbol, PERIOD_M5, InpATRPeriod);
   g_atr_handle_h1     = iATR(_Symbol, PERIOD_H1, InpATRPeriod);
   g_adx_handle_m5     = iADX(_Symbol, PERIOD_M5, InpATRPeriod);
   g_spread_atr_handle = iATR(_Symbol, InpSpreadATRTimeframe, InpSpreadATRPeriod);

   // 5. Daily Risk Tracker Calibration
   g_current_day = TimeCurrent() / 86400 * 86400;
   g_day_start_equity = AccountInfoDouble(ACCOUNT_EQUITY);
   g_day_start_balance = AccountInfoDouble(ACCOUNT_BALANCE);
   g_daily_loss = 0.0;
   g_trades_today = 0;
   g_consecutive_losses = 0;
   g_risk_halted = false;
   g_buffer_halted = false;
   g_hard_locked_today = false;
   g_buffer_alerted_today = false;
   g_last_heartbeat_time = TimeCurrent();"""

assert old_oninit_risk in content, "old_oninit_risk not found"
content = content.replace(old_oninit_risk, new_oninit_risk, 1)

# 5. Update OnTimer: add daily reset tracking, EvaluateRealTimeRisk, Friday close, and UpdateDashboardVisuals
old_ontimer_reset = """   // 1. Midnight reset for daily risk
   datetime today = TimeCurrent() / 86400 * 86400;
   if(today != g_current_day)
   {
      g_current_day = today;
      g_day_start_equity = AccountInfoDouble(ACCOUNT_EQUITY);
      g_daily_loss = 0.0;
      g_trades_today = 0;
      g_risk_halted = false;
      g_risk_halt_reason = "";
      Print("[PythonBridgeEA] [RISK] New trading day initialized. Daily risk trackers reset.");
   }"""

new_ontimer_reset = """   // 1. Midnight / Configurable Reset for Daily Risk & Challenge Tracking
   datetime today = TimeCurrent() / 86400 * 86400;
   if(today != g_current_day)
   {
      if(g_trades_today > 0)
      {
         g_trading_days_count++;
         string days_key = StringFormat("SMC_DAYS_%d_%d", InpMagicNumber, (int)AccountInfoInteger(ACCOUNT_LOGIN));
         GlobalVariableSet(days_key, (double)g_trading_days_count);
      }

      g_current_day = today;
      g_day_start_equity = AccountInfoDouble(ACCOUNT_EQUITY);
      g_day_start_balance = AccountInfoDouble(ACCOUNT_BALANCE);
      g_daily_loss = 0.0;
      g_trades_today = 0;
      g_buffer_halted = false;
      g_hard_locked_today = false;
      g_buffer_alerted_today = false;
      g_risk_halted = false;
      g_risk_halt_reason = "";
      PrintFormat("[PythonBridgeEA] [RISK] New trading day initialized. Equity baseline: $%.2f | Balance: $%.2f | Completed Trading Days: %d",
                  g_day_start_equity, g_day_start_balance, g_trading_days_count);
   }

   // 1b. Real-Time Prop Firm Equity & Drawdown Tracking
   EvaluateRealTimeRisk();

   // 1c. Friday Market Close Flattening (No Weekend Holding)
   if(InpNoWeekendHolding)
   {
      MqlDateTime dt;
      TimeToStruct(TimeCurrent(), dt);
      if(dt.day_of_week == 5 && dt.hour >= InpWeekendCloseHourFriday)
      {
         int closed = CloseAllPositions();
         if(closed > 0)
         {
            PrintFormat("[PythonBridgeEA] [WEEKEND] Liquidated %d positions before Friday market close.", closed);
            SendTelegramFallbackAlert(StringFormat("🛑 <b>WEEKEND FLATTENING</b>: Closed %d open positions before Friday close.", closed));
         }
      }
   }"""

assert old_ontimer_reset in content, "old_ontimer_reset not found"
content = content.replace(old_ontimer_reset, new_ontimer_reset, 1)

old_draw_levels = """   // 5. Chart Visualization
   DrawWeeklyDailyLevels();"""

new_draw_levels = """   // 5. Chart Visualization & Dashboard
   DrawWeeklyDailyLevels();
   UpdateDashboardVisuals();"""

assert old_draw_levels in content, "old_draw_levels not found"
content = content.replace(old_draw_levels, new_draw_levels, 1)

# 6. Update ProcessCommand:
# - Add ENABLE & DISABLE commands
# - Update ACCOUNT with full challenge & drawdown metrics
# - In TRADE -> CLOSE check InpMinTradeDurationSec
# - In TRADE -> entry remove ValidateLiveGate and invoke ValidatePreTrade

old_account_cmd = """   // 2. ACCOUNT (includes broker UTC offset and server times)
   if(action == "ACCOUNT")
   {
      double bal = AccountInfoDouble(ACCOUNT_BALANCE);
      double eq  = AccountInfoDouble(ACCOUNT_EQUITY);
      double mg  = AccountInfoDouble(ACCOUNT_MARGIN);
      double fm  = AccountInfoDouble(ACCOUNT_MARGIN_FREE);
      double ml  = AccountInfoDouble(ACCOUNT_MARGIN_LEVEL);
      long   log = AccountInfoInteger(ACCOUNT_LOGIN);
      long   lev = AccountInfoInteger(ACCOUNT_LEVERAGE);
      string cur = AccountInfoString(ACCOUNT_CURRENCY);
      string srv = AccountInfoString(ACCOUNT_SERVER);
      bool   trade_allowed = (bool)AccountInfoInteger(ACCOUNT_TRADE_ALLOWED);
      bool   connected = (bool)TerminalInfoInteger(TERMINAL_CONNECTED);

      return StringFormat("{\\"status\\":\\"SUCCESS\\",\\"request_id\\":\\"%s\\",\\"data\\":{\\"login\\":%d,\\"server\\":\\"%s\\",\\"balance\\":%.2f,\\"equity\\":%.2f,\\"margin\\":%.2f,\\"free_margin\\":%.2f,\\"margin_level\\":%.2f,\\"currency\\":\\"%s\\",\\"leverage\\":%d,\\"trade_allowed\\":%s,\\"terminal_connected\\":%s,\\"broker_utc_offset\\":%d,\\"server_time\\":%d,\\"utc_time\\":%d}}",
                          req_id, log, srv, bal, eq, mg, fm, ml, cur, lev,
                          trade_allowed ? "true" : "false", connected ? "true" : "false",
                          g_broker_utc_offset, (int)TimeTradeServer(), (int)TimeGMT());
   }"""

new_account_cmd = """   // 1b. ENABLE / AUTHORIZE TRADING
   if(action == "ENABLE")
   {
      g_trading_enabled = true;
      string enabled_key = StringFormat("SMC_ENABLED_%d_%d", InpMagicNumber, (int)AccountInfoInteger(ACCOUNT_LOGIN));
      GlobalVariableSet(enabled_key, 1.0);
      Print("[PythonBridgeEA] [COMMAND] Trading execution ENABLED.");
      return StringFormat("{\\"status\\":\\"SUCCESS\\",\\"request_id\\":\\"%s\\",\\"message\\":\\"Trading enabled\\",\\"trading_enabled\\":true}", req_id);
   }

   // 1c. DISABLE TRADING
   if(action == "DISABLE")
   {
      g_trading_enabled = false;
      string enabled_key = StringFormat("SMC_ENABLED_%d_%d", InpMagicNumber, (int)AccountInfoInteger(ACCOUNT_LOGIN));
      GlobalVariableSet(enabled_key, 0.0);
      Print("[PythonBridgeEA] [COMMAND] Trading execution DISABLED.");
      return StringFormat("{\\"status\\":\\"SUCCESS\\",\\"request_id\\":\\"%s\\",\\"message\\":\\"Trading disabled\\",\\"trading_enabled\\":false}", req_id);
   }

   // 2. ACCOUNT (includes multi-profile metadata, risk & challenge tracking)
   if(action == "ACCOUNT")
   {
      double bal = AccountInfoDouble(ACCOUNT_BALANCE);
      double eq  = AccountInfoDouble(ACCOUNT_EQUITY);
      double mg  = AccountInfoDouble(ACCOUNT_MARGIN);
      double fm  = AccountInfoDouble(ACCOUNT_MARGIN_FREE);
      double ml  = AccountInfoDouble(ACCOUNT_MARGIN_LEVEL);
      long   log = AccountInfoInteger(ACCOUNT_LOGIN);
      long   lev = AccountInfoInteger(ACCOUNT_LEVERAGE);
      string cur = AccountInfoString(ACCOUNT_CURRENCY);
      string srv = AccountInfoString(ACCOUNT_SERVER);
      bool   trade_allowed = (bool)AccountInfoInteger(ACCOUNT_TRADE_ALLOWED);
      bool   connected = (bool)TerminalInfoInteger(TERMINAL_CONNECTED);

      ENUM_ACCOUNT_TRADE_MODE trade_mode = (ENUM_ACCOUNT_TRADE_MODE)AccountInfoInteger(ACCOUNT_TRADE_MODE);
      string mode_str = (trade_mode == ACCOUNT_TRADE_MODE_DEMO ? "DEMO" :
                        (trade_mode == ACCOUNT_TRADE_MODE_REAL ? "REAL" : "CONTEST"));
      string profile_str = (InpAccountProfile == PROFILE_DEMO ? "DEMO" :
                           (InpAccountProfile == PROFILE_LIVE ? "LIVE" : "PROP"));

      double day_ref = (InpDrawdownType == DRAWDOWN_DAILY_START_BALANCE) ? g_day_start_balance : g_day_start_equity;
      if(day_ref <= 0.0) day_ref = eq;
      double daily_loss_money = (day_ref > eq) ? (day_ref - eq) : 0.0;
      double daily_loss_pct = (day_ref > 0.0) ? (daily_loss_money / day_ref) * 100.0 : 0.0;

      double dd_pct = 0.0;
      if(InpDrawdownType == DRAWDOWN_STATIC)
         dd_pct = (g_initial_balance > 0.0 && g_initial_balance > eq) ? ((g_initial_balance - eq) / g_initial_balance) * 100.0 : 0.0;
      else if(InpDrawdownType == DRAWDOWN_TRAILING)
         dd_pct = (g_peak_equity > 0.0 && g_peak_equity > eq) ? ((g_peak_equity - eq) / g_peak_equity) * 100.0 : 0.0;
      else
         dd_pct = daily_loss_pct;

      double max_daily_loss_money = day_ref * (InpMaxDailyLossPercent / 100.0);
      double rem_daily = MathMax(0.0, max_daily_loss_money - daily_loss_money);

      double dd_ref = (InpDrawdownType == DRAWDOWN_STATIC) ? g_initial_balance : g_peak_equity;
      double max_dd_money = dd_ref * (InpMaxTotalDrawdownPercent / 100.0);
      double current_dd_money = MathMax(0.0, dd_ref - eq);
      double rem_dd = MathMax(0.0, max_dd_money - current_dd_money);

      double profit_target_money = g_initial_balance * (InpProfitTargetPercent / 100.0);
      double current_profit = eq - g_initial_balance;
      double target_prog = (profit_target_money > 0.0) ? MathMax(0.0, (current_profit / profit_target_money) * 100.0) : 0.0;

      return StringFormat(
         "{\\"status\\":\\"SUCCESS\\",\\"request_id\\":\\"%s\\",\\"data\\":{\\"login\\":%d,\\"server\\":\\"%s\\",\\"trade_mode\\":\\"%s\\",\\"profile\\":\\"%s\\",\\"trading_enabled\\":%s,\\"balance\\":%.2f,\\"equity\\":%.2f,\\"peak_equity\\":%.2f,\\"initial_balance\\":%.2f,\\"daily_loss_pct\\":%.2f,\\"drawdown_pct\\":%.2f,\\"remaining_daily_loss\\":%.2f,\\"remaining_drawdown\\":%.2f,\\"profit_target_pct\\":%.2f,\\"trading_days\\":%d,\\"margin\\":%.2f,\\"free_margin\\":%.2f,\\"margin_level\\":%.2f,\\"currency\\":\\"%s\\",\\"leverage\\":%d,\\"trade_allowed\\":%s,\\"terminal_connected\\":%s,\\"broker_utc_offset\\":%d,\\"server_time\\":%d,\\"utc_time\\":%d}}",
         req_id, log, srv, mode_str, profile_str, (g_trading_enabled ? "true" : "false"),
         bal, eq, g_peak_equity, g_initial_balance, daily_loss_pct, dd_pct,
         rem_daily, rem_dd, target_prog, g_trading_days_count,
         mg, fm, ml, cur, lev,
         trade_allowed ? "true" : "false", connected ? "true" : "false",
         g_broker_utc_offset, (int)TimeTradeServer(), (int)TimeGMT()
      );
   }"""

assert old_account_cmd in content, "old_account_cmd not found"
content = content.replace(old_account_cmd, new_account_cmd, 1)

# Check Minimum Trade Duration on trade_action == CLOSE
old_close_act = """      if(trade_act == "CLOSE")
      {
         ulong ticket = (ulong)ExtractJsonInteger(json, "ticket", 0);
         double vol = ExtractJsonDouble(json, "volume", 0.0);
         if(ticket > 0 && g_pos_info.SelectByTicket(ticket))
         {"""

new_close_act = """      if(trade_act == "CLOSE")
      {
         ulong ticket = (ulong)ExtractJsonInteger(json, "ticket", 0);
         double vol = ExtractJsonDouble(json, "volume", 0.0);
         if(ticket > 0 && g_pos_info.SelectByTicket(ticket))
         {
            if(InpMinTradeDurationSec > 0 && !InpKillSwitch && !g_hard_locked_today)
            {
               datetime pos_tm = (datetime)g_pos_info.Time();
               int duration_sec = (int)(TimeCurrent() - pos_tm);
               if(duration_sec < InpMinTradeDurationSec)
               {
                  return StringFormat("{\\"status\\":\\"ERROR\\",\\"request_id\\":\\"%s\\",\\"message\\":\\"Prop Rule Violation: Position held for only %ds < minimum %ds (Tick scalping prohibited)\\"}",
                                      req_id, duration_sec, InpMinTradeDurationSec);
               }
            }"""

assert old_close_act in content, "old_close_act not found"
content = content.replace(old_close_act, new_close_act, 1)

# Check in action == TRADE: replace ValidateLiveGate with early pre-check
old_trade_livegate = """      // Check Live Execution Gate
      string gate_reason = "";
      if(!ValidateLiveGate(gate_reason))
      {
         string err = StringFormat("{\\"status\\":\\"ERROR\\",\\"message\\":\\"LIVE GATE REJECTED: %s\\"}", gate_reason);
         if(req_id != "") CacheRequestResult(req_id, err);
         return err;
      }"""

new_trade_livegate = """      // Pre-trade gate and safety validation
      string gate_reason = "";
      if(InpAllowedAccount > 0 && AccountInfoInteger(ACCOUNT_LOGIN) != InpAllowedAccount)
      {
         string err = StringFormat("{\\"status\\":\\"ERROR\\",\\"message\\":\\"Account %d does not match InpAllowedAccount %d\\"}",
                                   AccountInfoInteger(ACCOUNT_LOGIN), InpAllowedAccount);
         if(req_id != "") CacheRequestResult(req_id, err);
         return err;
      }

      if(!g_trading_enabled)
      {
         string err = StringFormat("{\\"status\\":\\"ERROR\\",\\"message\\":\\"Trading execution is disabled for %s profile. Authorize via /enable or inputs.\\"}",
                                   (InpAccountProfile == PROFILE_DEMO ? "DEMO" : (InpAccountProfile == PROFILE_LIVE ? "LIVE" : "PROP")));
         if(req_id != "") CacheRequestResult(req_id, err);
         return err;
      }"""

assert old_trade_livegate in content, "old_trade_livegate not found"
content = content.replace(old_trade_livegate, new_trade_livegate, 1)

# Update Pre-trade validation call inside action == TRADE
old_val_pretrade = """      // Pre-trade general validation (account trade allowed, max positions, quota)
      string val_reason = "";
      if(!ValidatePreTrade(sym, volume, val_reason))
      {
         string err = StringFormat("{\\"status\\":\\"ERROR\\",\\"message\\":\\"Pre-trade validation failed: %s\\"}", val_reason);
         AppendJournal(sym, (mode == MODE_SCALP ? "SCALP" : "HOLD"), type_str, 0.0, sl, tp, volume, 0, 0, val_reason);
         if(req_id != "") CacheRequestResult(req_id, err);
         return err;
      }"""

new_val_pretrade = """      // Pre-trade general validation (account trade allowed, max positions, quota, and prop firm rules)
      string val_reason = "";
      if(!ValidatePreTrade(sym, volume, sl, tp, type_str, val_reason))
      {
         string err = StringFormat("{\\"status\\":\\"ERROR\\",\\"message\\":\\"Pre-trade validation failed: %s\\"}", val_reason);
         AppendJournal(sym, (mode == MODE_SCALP ? "SCALP" : "HOLD"), type_str, 0.0, sl, tp, volume, 0, 0, val_reason);
         if(req_id != "") CacheRequestResult(req_id, err);
         return err;
      }"""

assert old_val_pretrade in content, "old_val_pretrade not found"
content = content.replace(old_val_pretrade, new_val_pretrade, 1)

# 7. Replace ValidateLiveGate and ValidatePreTrade implementations with full Prop Firm Engine
old_gate_func = """//+------------------------------------------------------------------+
//| Live Execution Gate Validation                                   |
//+------------------------------------------------------------------+
bool ValidateLiveGate(string &reason)
{
   ENUM_ACCOUNT_TRADE_MODE trade_mode = (ENUM_ACCOUNT_TRADE_MODE)AccountInfoInteger(ACCOUNT_TRADE_MODE);

   if(trade_mode == ACCOUNT_TRADE_MODE_DEMO)
   {
      return true; // Demo trading always permitted
   }

   // On Real Live Account: Strictly enforce all 4 gates
   if(InpDemoOnly)
   {
      reason = "InpDemoOnly is true. EA refuses real-money accounts.";
      return false;
   }

   if(!InpAllowLiveTrading)
   {
      reason = "InpAllowLiveTrading is false.";
      return false;
   }

   if(InpLiveConfirmText != "I ACCEPT THE RISK OF REAL TRADING")
   {
      reason = "LiveConfirmText does not match required confirmation phrase.";
      return false;
   }

   if(InpAllowedAccount > 0 && AccountInfoInteger(ACCOUNT_LOGIN) != InpAllowedAccount)
   {
      reason = StringFormat("Account %d does not match InpAllowedAccount %d.",
                            AccountInfoInteger(ACCOUNT_LOGIN), InpAllowedAccount);
      return false;
   }

   return true;
}

//+------------------------------------------------------------------+
//| Pre-Trade General Validation                                     |
//+------------------------------------------------------------------+
bool ValidatePreTrade(const string sym, const double volume, string &reason)
{
   if(!TerminalInfoInteger(TERMINAL_CONNECTED))
   {
      reason = "Terminal is not connected to broker server.";
      return false;
   }

   if(!AccountInfoInteger(ACCOUNT_TRADE_ALLOWED))
   {
      reason = "Automated trading is disabled in terminal settings or account permissions.";
      return false;
   }

   if(PositionsTotal() >= InpMaxPositions)
   {
      reason = StringFormat("Max open positions limit (%d) reached.", InpMaxPositions);
      return false;
   }

   if(g_trades_today >= InpMaxTradesPerDay)
   {
      reason = StringFormat("Max daily trade quota (%d) reached.", InpMaxTradesPerDay);
      return false;
   }

   double min_lot = SymbolInfoDouble(sym, SYMBOL_VOLUME_MIN);
   double max_lot = SymbolInfoDouble(sym, SYMBOL_VOLUME_MAX);
   if(volume < min_lot || volume > MathMin(max_lot, InpMaxLot))
   {
      reason = StringFormat("Volume %.2f is outside permitted range [%.2f, %.2f].", volume, min_lot, MathMin(max_lot, InpMaxLot));
      return false;
   }

   return true;
}"""

new_gate_func = """//+------------------------------------------------------------------+
//| Emergency Liquidation of All Active Open Positions               |
//+------------------------------------------------------------------+
int CloseAllPositions()
{
   int closed = 0;
   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      if(g_pos_info.SelectByIndex(i) && g_pos_info.Magic() == InpMagicNumber)
      {
         if(g_trade.PositionClose(g_pos_info.Ticket()))
            closed++;
      }
   }
   return closed;
}

//+------------------------------------------------------------------+
//| Real-Time Equity Tracking & Prop Firm Risk Engine                |
//| Enforces Safety Buffer (80%) and Hard Limit Liquidation (95%)    |
//+------------------------------------------------------------------+
void EvaluateRealTimeRisk()
{
   double cur_balance = AccountInfoDouble(ACCOUNT_BALANCE);
   double cur_equity  = AccountInfoDouble(ACCOUNT_EQUITY);
   long login = AccountInfoInteger(ACCOUNT_LOGIN);

   // Update peak equity if new high made
   if(cur_equity > g_peak_equity)
   {
      g_peak_equity = cur_equity;
      string peak_key = StringFormat("SMC_PEAKEQ_%d_%d", InpMagicNumber, (int)login);
      GlobalVariableSet(peak_key, g_peak_equity);
   }

   // 1. Calculate Daily Loss %
   double day_ref = (InpDrawdownType == DRAWDOWN_DAILY_START_BALANCE) ? g_day_start_balance : g_day_start_equity;
   if(day_ref <= 0.0) day_ref = cur_equity;
   double daily_loss_money = (day_ref > cur_equity) ? (day_ref - cur_equity) : 0.0;
   double daily_loss_pct = (day_ref > 0.0) ? (daily_loss_money / day_ref) * 100.0 : 0.0;

   // 2. Calculate Total Drawdown %
   double total_dd_pct = 0.0;
   if(InpDrawdownType == DRAWDOWN_STATIC)
   {
      double dd_money = (g_initial_balance > cur_equity) ? (g_initial_balance - cur_equity) : 0.0;
      total_dd_pct = (g_initial_balance > 0.0) ? (dd_money / g_initial_balance) * 100.0 : 0.0;
   }
   else if(InpDrawdownType == DRAWDOWN_TRAILING)
   {
      double dd_money = (g_peak_equity > cur_equity) ? (g_peak_equity - cur_equity) : 0.0;
      total_dd_pct = (g_peak_equity > 0.0) ? (dd_money / g_peak_equity) * 100.0 : 0.0;
   }
   else
   {
      total_dd_pct = daily_loss_pct;
   }

   double buffer_ratio = InpSafetyBufferPercent / 100.0;
   double daily_buffer_limit = InpMaxDailyLossPercent * buffer_ratio;
   double total_buffer_limit = InpMaxTotalDrawdownPercent * buffer_ratio;

   // Hard Limit (95% of limit or limit)
   double daily_hard_limit = InpMaxDailyLossPercent * 0.95;
   double total_hard_limit = InpMaxTotalDrawdownPercent * 0.95;

   if((daily_loss_pct >= daily_hard_limit || total_dd_pct >= total_hard_limit) && !g_hard_locked_today)
   {
      g_hard_locked_today = true;
      g_risk_halted = true;
      g_risk_halt_reason = StringFormat("Hard drawdown limit breached! Daily: %.2f%%, DD: %.2f%%", daily_loss_pct, total_dd_pct);
      PrintFormat("[PythonBridgeEA] [CRITICAL] %s. LIQUIDATING ALL POSITIONS!", g_risk_halt_reason);

      int closed = CloseAllPositions();
      SendTelegramFallbackAlert(StringFormat(
         "🚨 <b>HARD DRAWDOWN LIMIT BREACHED</b>\\n"
         "📉 <b>Daily Loss:</b> <code>%.2f%%</code> (Cap: <code>%.2f%%</code>)\\n"
         "📉 <b>Total DD:</b> <code>%.2f%%</code> (Cap: <code>%.2f%%</code>)\\n"
         "💥 <b>Liquidated Positions:</b> <code>%d</code>\\n"
         "🔒 <b>Trading Locked:</b> Bot entry locked until next daily reset.",
         daily_loss_pct, InpMaxDailyLossPercent, total_dd_pct, InpMaxTotalDrawdownPercent, closed
      ));
   }
   else if(daily_loss_pct >= daily_buffer_limit || total_dd_pct >= total_buffer_limit)
   {
      if(!g_buffer_halted)
      {
         g_buffer_halted = true;
         PrintFormat("[PythonBridgeEA] [WARNING] Safety buffer reached: Daily Loss: %.2f%%, DD: %.2f%%. Halting new entries.",
                     daily_loss_pct, total_dd_pct);
         if(!g_buffer_alerted_today)
         {
            g_buffer_alerted_today = true;
            SendTelegramFallbackAlert(StringFormat(
               "⚠️ <b>SAFETY BUFFER REACHED</b>\\n"
               "📉 <b>Loss Level:</b> <code>Daily %.2f%% / Total DD %.2f%%</code>\\n"
               "🛑 <b>Buffer Threshold:</b> <code>%.1f%% of limit</code>\\n"
               "📋 <b>Action:</b> Halting new trade entries. Existing positions remain managed.",
               daily_loss_pct, total_dd_pct, InpSafetyBufferPercent
            ));
         }
      }
   }
   else if(!g_hard_locked_today)
   {
      g_buffer_halted = false;
   }
}

//+------------------------------------------------------------------+
//| High-Impact Economic News Blackout Check (MQL5 Calendar API)     |
//+------------------------------------------------------------------+
bool IsNewsBlackoutActive(const string sym, string &news_details)
{
   if(!InpNewsFilterEnabled) return false;

   string baseCurr   = SymbolInfoString(sym, SYMBOL_CURRENCY_BASE);
   string profitCurr = SymbolInfoString(sym, SYMBOL_CURRENCY_PROFIT);

   datetime now_server = TimeTradeServer();
   datetime from_time  = now_server - (InpNewsBlockMinutesAfter * 60);
   datetime to_time    = now_server + (InpNewsBlockMinutesBefore * 60);

   string currencies[2];
   currencies[0] = baseCurr;
   currencies[1] = profitCurr;

   for(int c = 0; c < 2; c++)
   {
      if(currencies[c] == "") continue;
      MqlCalendarValue values[];
      ResetLastError();
      int count = CalendarValueHistory(values, from_time, to_time, NULL, currencies[c]);
      if(count <= 0) continue;

      for(int i = 0; i < count; i++)
      {
         MqlCalendarEvent event;
         if(CalendarEventById(values[i].event_id, event))
         {
            if(event.time_mode != CALENDAR_TIMEMODE_DATETIME) continue;
            if(event.importance == CALENDAR_IMPORTANCE_HIGH)
            {
               news_details = StringFormat("High-Impact News: [%s] %s @ %s",
                                           currencies[c], event.name,
                                           TimeToString(values[i].time, TIME_MINUTES));
               return true;
            }
         }
      }
   }
   return false;
}

//+------------------------------------------------------------------+
//| Pre-Trade Prop Firm & Risk Validation                            |
//+------------------------------------------------------------------+
bool ValidatePreTrade(const string sym, const double volume, const double sl, const double tp, const string type_str, string &reason)
{
   if(!TerminalInfoInteger(TERMINAL_CONNECTED))
   {
      reason = "Terminal is not connected to broker server.";
      return false;
   }

   if(!AccountInfoInteger(ACCOUNT_TRADE_ALLOWED))
   {
      reason = "Automated trading is disabled in terminal settings or account permissions.";
      return false;
   }

   // 1. Allowed Account Check
   if(InpAllowedAccount > 0 && AccountInfoInteger(ACCOUNT_LOGIN) != InpAllowedAccount)
   {
      reason = StringFormat("Account %d does not match AllowedAccount %d.",
                            AccountInfoInteger(ACCOUNT_LOGIN), InpAllowedAccount);
      return false;
   }

   // 2. Trading Authorization Check (LIVE & PROP require explicit TradingEnabled)
   if(InpAccountProfile == PROFILE_LIVE || InpAccountProfile == PROFILE_PROP)
   {
      if(!g_trading_enabled)
      {
         reason = StringFormat("Trading is disabled for %s profile. Authorize via input or /enable.",
                               (InpAccountProfile == PROFILE_LIVE ? "LIVE" : "PROP"));
         return false;
      }
   }
   else if(!g_trading_enabled)
   {
      reason = "Trading is currently disabled. Authorize via /enable or inputs.";
      return false;
   }

   // 3. Hard Lock & Buffer Check
   if(g_hard_locked_today)
   {
      reason = "Trading locked until daily reset due to hard drawdown breach.";
      return false;
   }
   if(g_buffer_halted)
   {
      reason = StringFormat("Trading paused: Safety buffer reached (%.0f%% limit threshold).", InpSafetyBufferPercent);
      return false;
   }

   // 4. Watchdog & Kill Switch
   if(g_watchdog_halted)
   {
      reason = "Watchdog halted: bridge heartbeat lost.";
      return false;
   }
   if(g_risk_halted || InpKillSwitch)
   {
      reason = StringFormat("Risk Halt / Kill Switch active: %s", g_risk_halt_reason);
      return false;
   }

   // 5. Position and Quota Limits
   if(PositionsTotal() >= InpMaxPositions)
   {
      reason = StringFormat("Max open positions limit (%d) reached.", InpMaxPositions);
      return false;
   }

   if(g_trades_today >= InpMaxTradesPerDay)
   {
      reason = StringFormat("Max daily trade quota (%d) reached.", InpMaxTradesPerDay);
      return false;
   }

   // 6. Hard Lot Cap & Range Check
   double min_lot = SymbolInfoDouble(sym, SYMBOL_VOLUME_MIN);
   double max_lot = SymbolInfoDouble(sym, SYMBOL_VOLUME_MAX);
   if(volume < min_lot || volume > MathMin(max_lot, InpMaxLot))
   {
      reason = StringFormat("Volume %.2f is outside permitted range [%.2f, %.2f].", volume, min_lot, MathMin(max_lot, InpMaxLot));
      return false;
   }

   // 7. Prop Firm Rule: Stop Loss Required
   if(InpRequireSL && sl <= 0.0)
   {
      reason = "Prop/Live Rule Violation: Stop Loss is strictly required on every trade.";
      return false;
   }

   // 8. Prop Firm Rule: No Martingale (no volume increase after losing trade)
   if(InpNoMartingale)
   {
      datetime day_start = TimeCurrent() / 86400 * 86400;
      HistorySelect(day_start, TimeCurrent());
      int total_d = HistoryDealsTotal();
      for(int d = total_d - 1; d >= 0; d--)
      {
         ulong deal = HistoryDealGetTicket(d);
         if(deal > 0 && HistoryDealGetInteger(deal, DEAL_MAGIC) == InpMagicNumber && HistoryDealGetInteger(deal, DEAL_ENTRY) == DEAL_ENTRY_OUT)
         {
            double pnl = HistoryDealGetDouble(deal, DEAL_PROFIT);
            double prev_vol = HistoryDealGetDouble(deal, DEAL_VOLUME);
            if(pnl < 0.0 && volume > prev_vol + 0.0001)
            {
               reason = StringFormat("Prop Rule Violation: Martingale detected (lot %.2f > previous losing lot %.2f).", volume, prev_vol);
               return false;
            }
            break;
         }
      }
   }

   // 9. Prop Firm Rule: No Grid / Averaging Down
   if(InpNoGridAveraging)
   {
      ENUM_POSITION_TYPE req_type = (type_str == "BUY" || type_str == "BUY_LIMIT" || type_str == "BUY_STOP") ? POSITION_TYPE_BUY : POSITION_TYPE_SELL;
      for(int i = 0; i < PositionsTotal(); i++)
      {
         if(g_pos_info.SelectByIndex(i) && g_pos_info.Symbol() == sym && g_pos_info.Magic() == InpMagicNumber)
         {
            if(g_pos_info.PositionType() == req_type && g_pos_info.Profit() < 0.0)
            {
               reason = StringFormat("Prop Rule Violation: Grid/Averaging down prohibited while position #%d is in drawdown ($%.2f).",
                                     g_pos_info.Ticket(), g_pos_info.Profit());
               return false;
            }
         }
      }
   }

   // 10. Prop Firm Rule: No Hedging
   if(InpNoHedging)
   {
      ENUM_POSITION_TYPE req_type = (type_str == "BUY" || type_str == "BUY_LIMIT" || type_str == "BUY_STOP") ? POSITION_TYPE_BUY : POSITION_TYPE_SELL;
      for(int i = 0; i < PositionsTotal(); i++)
      {
         if(g_pos_info.SelectByIndex(i) && g_pos_info.Symbol() == sym && g_pos_info.Magic() == InpMagicNumber)
         {
            if(g_pos_info.PositionType() != req_type)
            {
               reason = StringFormat("Prop Rule Violation: Hedging prohibited (opposite position #%d active on %s).",
                                     g_pos_info.Ticket(), sym);
               return false;
            }
         }
      }
   }

   // 11. Prop Firm Rule: No Weekend Holding
   if(InpNoWeekendHolding)
   {
      MqlDateTime dt;
      TimeToStruct(TimeCurrent(), dt);
      if(dt.day_of_week == 5 && dt.hour >= InpWeekendCloseHourFriday)
      {
         reason = StringFormat("Prop Rule Violation: Weekend holding prohibited after %02d:00 Friday.", InpWeekendCloseHourFriday);
         return false;
      }
   }

   // 12. Economic News Filter
   string news_info = "";
   if(IsNewsBlackoutActive(sym, news_info))
   {
      reason = StringFormat("News Filter: Trading suspended around %s", news_info);
      return false;
   }

   return true;
}

//+------------------------------------------------------------------+
//| Chart Dashboard Visuals                                          |
//+------------------------------------------------------------------+
void UpdateDashboardVisuals()
{
   string obj_pnl = "SMC_DASHBOARD_PANEL";
   ENUM_ACCOUNT_TRADE_MODE trade_mode = (ENUM_ACCOUNT_TRADE_MODE)AccountInfoInteger(ACCOUNT_TRADE_MODE);
   string mode_str = (trade_mode == ACCOUNT_TRADE_MODE_DEMO ? "DEMO" :
                     (trade_mode == ACCOUNT_TRADE_MODE_REAL ? "REAL" : "CONTEST"));
   string profile_str = (InpAccountProfile == PROFILE_DEMO ? "DEMO" :
                        (InpAccountProfile == PROFILE_LIVE ? "LIVE" : "PROP"));

   double cur_eq = AccountInfoDouble(ACCOUNT_EQUITY);
   double day_ref = (InpDrawdownType == DRAWDOWN_DAILY_START_BALANCE) ? g_day_start_balance : g_day_start_equity;
   if(day_ref <= 0.0) day_ref = cur_eq;
   double daily_loss_pct = (day_ref > cur_eq) ? ((day_ref - cur_eq) / day_ref) * 100.0 : 0.0;

   double dd_pct = 0.0;
   if(InpDrawdownType == DRAWDOWN_STATIC)
      dd_pct = (g_initial_balance > 0.0 && g_initial_balance > cur_eq) ? ((g_initial_balance - cur_eq) / g_initial_balance) * 100.0 : 0.0;
   else if(InpDrawdownType == DRAWDOWN_TRAILING)
      dd_pct = (g_peak_equity > 0.0 && g_peak_equity > cur_eq) ? ((g_peak_equity - cur_eq) / g_peak_equity) * 100.0 : 0.0;
   else
      dd_pct = daily_loss_pct;

   string dash_text = StringFormat(
      "[%s | %s] Auth: %s | Bal: $%.2f | Eq: $%.2f | Peak: $%.2f | Daily Loss: %.2f%% (Cap %.1f%%) | DD: %.2f%% (Cap %.1f%%) | Target: %.1f%% | Days: %d/%d",
      mode_str, profile_str, (g_trading_enabled ? "ENABLED" : "LOCKED"),
      AccountInfoDouble(ACCOUNT_BALANCE), cur_eq, g_peak_equity,
      daily_loss_pct, InpMaxDailyLossPercent, dd_pct, InpMaxTotalDrawdownPercent,
      InpProfitTargetPercent, g_trading_days_count, InpMinTradingDays
   );

   color dash_col = (g_hard_locked_today ? clrRed : (g_buffer_halted ? clrOrange : (g_trading_enabled ? clrSpringGreen : clrGold)));
   Comment(dash_text);
}"""

assert old_gate_func in content, "old_gate_func not found"
content = content.replace(old_gate_func, new_gate_func, 1)

with open(ea_path, "w", encoding="utf-8") as f:
    f.write(content)

print(f"Successfully applied prop firm & multi-profile architecture to {ea_path}")
