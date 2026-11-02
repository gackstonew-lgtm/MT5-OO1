"""
Comprehensive patch script for PythonBridgeEA.mq5:
Part 1: Startup checklist, Indicator warm-up (WarmupBars=300), closed-bar evaluation only, countdown logs, SkipMinutesAfterOpen.
Part 2: Continuous activity feed, log levels (CRITICAL, TRADE, SIGNAL, DECISION, INFO, DEBUG), dual UTC / UTC+3 timestamps, numbers included, heartbeat line, daily log file with rotation.
Part 3: Embedded sound resources (signal.wav, trade.wav, alert.wav), PlaySound, Alert, SendNotification (MT5 push rate-limited).
"""

import os
import re

ea_path = os.path.abspath("mql5/Experts/PythonBridgeEA.mq5")

with open(ea_path, "r", encoding="utf-8") as f:
    ea_text = f.read()

# 1. Add #resource directives at top
resource_directives = """#property description "Institutional MT5-Python TCP Bridge with Full Lifecycle, Single-Instance Guard & Adaptive Management"

//--- Sound Resources
#resource "\\\\Sounds\\\\signal.wav"
#resource "\\\\Sounds\\\\trade.wav"
#resource "\\\\Sounds\\\\alert.wav"
"""

if '#resource "\\\\Sounds\\\\signal.wav"' not in ea_text:
    ea_text = ea_text.replace(
        '#property description "Institutional MT5-Python TCP Bridge with Full Lifecycle, Single-Instance Guard & Adaptive Management"',
        resource_directives
    )
    print("Added #resource directives.")

# 2. Add Warm-up & Sound Input Groups
warmup_inputs = """//--- Input Parameters: Warm-Up & Signal Evaluation
input group "=== Startup Warm-Up & Signal Timing ==="
input int                 InpWarmupBars            = 300;         // Indicator Warm-up Minimum Closed Bars
input int                 InpSkipMinutesAfterOpen  = 0;           // Skip Minutes After Bar Open (Wide-Spread Avoidance)
input ENUM_TIMEFRAMES     InpSignalTimeframe       = PERIOD_M15;  // Signal Evaluation Timeframe
input int                 InpFeedIntervalSec       = 10;          // Telegram Digest Feed Interval (seconds)
input int                 InpHeartbeatMinutes      = 5;           // Heartbeat Interval (minutes)

//--- Input Parameters: Sound & Audio Alerts
input group "=== Sound & Push Notifications ==="
input bool                InpSoundEnabled          = true;        // Enable Audio Sounds (PC MT5)
input string              InpSignalSound           = "signal.wav";// Sound for SIGNAL event (.wav)
input string              InpTradeSound            = "trade.wav"; // Sound for TRADE event (.wav)
input string              InpAlertSound            = "alert.wav"; // Sound for CRITICAL event (.wav)
input bool                InpAlertOnSignal         = true;        // Show MT5 Alert popup on SIGNAL
"""

if "InpWarmupBars" not in ea_text:
    target_group = '//--- Input Parameters: Dynamic Spread Filter (replaces fixed InpMaxSpreadPoints)'
    ea_text = ea_text.replace(target_group, warmup_inputs + "\n" + target_group)
    print("Added Warmup & Sound input parameters.")

# 3. Add global state variables for feed, warm-up, and sounds
feed_globals = """// Dynamic Activity Feed, Warm-Up & Sound State
bool           g_warmup_checklist_passed = false;
bool           g_warmup_complete = false;
datetime       g_last_closed_bar_time = 0;
int            g_last_countdown_logged_minute = -1;
datetime       g_last_heartbeat_feed_time = 0;
datetime       g_last_push_notification_time = 0;
datetime       g_last_fallback_feed_flush = 0;

// Fallback feed queue (when Python bridge is offline)
#define MAX_FALLBACK_QUEUE 100
string         g_fallback_queue[MAX_FALLBACK_QUEUE];
int            g_fallback_queue_count = 0;
"""

if "g_warmup_checklist_passed" not in ea_text:
    target_globals = "// Indicator handles"
    ea_text = ea_text.replace(target_globals, feed_globals + "\n" + target_globals)
    print("Added Feed & Warm-up global variables.")

# 4. Add Helper Functions (PlayAudioAlert, SendPushAlert, WriteDailyLog, EscapeJsonString, QueueFallbackFeed, FlushFallbackFeed, LogEvent, RunStartupChecklist, CheckIndicatorWarmup, CheckBarTimingAndEvaluate, EvaluateClosedBarSetup)
helpers_code = '''
//+------------------------------------------------------------------+
//| Audio & Push Notification Dispatchers                            |
//+------------------------------------------------------------------+
void PlayAudioAlert(const string sound_file)
{
   if(!InpSoundEnabled) return;
   if(sound_file == "" || sound_file == "none") return;

   // Try playing embedded resource first
   string res_path = "::Sounds\\\\" + sound_file;
   if(!PlaySound(res_path))
   {
      // Fallback to terminal Sounds directory
      PlaySound(sound_file);
   }
}

void SendPushAlert(const string msg)
{
   if(!InpSendNotifications) return;
   datetime now = TimeCurrent();
   if(now - g_last_push_notification_time < 3) return; // rate limit: 1 per 3 sec
   g_last_push_notification_time = now;
   SendNotification(msg);
}

void WriteDailyLog(const string line)
{
   datetime now_utc = TimeGMT();
   string date_str = TimeToString(now_utc, TIME_DATE);
   StringReplace(date_str, ".", ""); // YYYYMMDD
   string filename = "logs\\\\activity_" + date_str + ".log";
   int handle = FileOpen(filename, FILE_READ|FILE_WRITE|FILE_TXT|FILE_SHARE_READ|FILE_ANSI);
   if(handle != INVALID_HANDLE)
   {
      FileSeek(handle, 0, SEEK_END);
      FileWriteString(handle, line + "\\r\\n");
      FileClose(handle);
   }
}

string EscapeJsonString(const string src)
{
   string res = src;
   StringReplace(res, "\\\\", "\\\\\\\\");
   StringReplace(res, "\\\"", "\\\\\\\"");
   StringReplace(res, "\\r", "");
   StringReplace(res, "\\n", "\\\\n");
   StringReplace(res, "\\t", " ");
   return res;
}

void QueueFallbackFeed(const string level, const string line)
{
   if(!InpTelegramFallbackEnabled) return;
   if(level == "SIGNAL" || level == "TRADE" || level == "CRITICAL")
   {
      string alert_text = StringFormat("<b>[%s]</b>\\n<code>%s</code>", level, line);
      SendTelegramFallbackAlert(alert_text);
      return;
   }
   if(g_fallback_queue_count < MAX_FALLBACK_QUEUE)
   {
      g_fallback_queue[g_fallback_queue_count++] = line;
   }
}

void FlushFallbackFeed()
{
   if(!InpTelegramFallbackEnabled || g_fallback_queue_count == 0) return;
   datetime now = TimeCurrent();
   if(now - g_last_fallback_feed_flush < InpFeedIntervalSec) return;
   g_last_fallback_feed_flush = now;

   string digest = StringFormat("📋 <b>EA FALLBACK DIGEST (%d events)</b>\\n<pre>", g_fallback_queue_count);
   for(int i = 0; i < g_fallback_queue_count; i++)
   {
      if(StringLen(digest) + StringLen(g_fallback_queue[i]) > 3800) break;
      digest += g_fallback_queue[i] + "\\n";
   }
   digest += "</pre>";
   g_fallback_queue_count = 0;
   SendTelegramFallbackAlert(digest);
}

//+------------------------------------------------------------------+
//| Unified Multi-Level Activity Event Logger                        |
//| Mirror of real EA actions across Experts, Daily File, & Telegram |
//+------------------------------------------------------------------+
void LogEvent(const string level, const string sym, const string tf_str, const string msg, const bool immediate_sound = true)
{
   datetime now_gmt = TimeGMT();
   datetime now_utc3 = now_gmt + 3 * 3600; // Local user timezone is UTC+3

   string time_utc  = TimeToString(now_gmt, TIME_DATE|TIME_SECONDS);
   string time_utc3 = TimeToString(now_utc3, TIME_DATE|TIME_SECONDS);

   // Exact standard format: Timestamp (UTC and UTC+3), Level, Symbol, Timeframe, Numbers used
   string line = StringFormat("[%s UTC | %s UTC+3] [%s] [%s, %s] %s",
                              time_utc, time_utc3, level, sym, tf_str, msg);

   // 1. Output to Experts Tab
   Print(line);

   // 2. Append to Daily Log File with Rotation
   WriteDailyLog(line);

   // 3. Audio / Alert / Push Notification dispatch on priority events
   if(immediate_sound)
   {
      if(level == "SIGNAL")
      {
         PlayAudioAlert(InpSignalSound);
         if(InpAlertOnSignal) Alert(line);
         SendPushAlert(line);
      }
      else if(level == "TRADE")
      {
         PlayAudioAlert(InpTradeSound);
         SendPushAlert(line);
      }
      else if(level == "CRITICAL")
      {
         PlayAudioAlert(InpAlertSound);
         SendPushAlert(line);
      }
   }

   // 4. Dispatch over TCP socket to Python Bridge
   if(g_socket != INVALID_HANDLE && SocketIsConnected(g_socket))
   {
      string json_event = StringFormat(
         "{\\\"action\\\":\\\"FEED_EVENT\\\",\\\"level\\\":\\\"%s\\\",\\\"symbol\\\":\\\"%s\\\",\\\"tf\\\":\\\"%s\\\",\\\"line\\\":\\\"%s\\\",\\\"time_utc\\\":\\\"%s\\\",\\\"time_local\\\":\\\"%s\\\"}\\n",
         level, sym, tf_str, EscapeJsonString(line), time_utc, time_utc3
      );
      char data[];
      StringToCharArray(json_event, data, 0, StringLen(json_event));
      SocketSend(g_socket, data, ArraySize(data));
   }
   else
   {
      QueueFallbackFeed(level, line);
   }
}

//+------------------------------------------------------------------+
//| Startup Warm-Up Checklist                                        |
//+------------------------------------------------------------------+
bool RunStartupChecklist()
{
   bool all_pass = true;
   string items[7];
   bool pass[7];

   // 1. Bridge Connected
   pass[0] = (g_socket != INVALID_HANDLE && SocketIsConnected(g_socket));
   items[0] = StringFormat("Bridge connected: %s (Host: %s:%d)", pass[0] ? "PASS" : "FAIL", InpServerHost, InpServerPort);
   if(!pass[0]) all_pass = false;

   // 2. Symbols Selected
   bool sym_sel = SymbolSelect(_Symbol, true);
   pass[1] = sym_sel && (bool)SymbolInfoInteger(_Symbol, SYMBOL_SELECT);
   items[1] = StringFormat("Symbols selected: %s (Symbol: %s, InMarketWatch: YES)", pass[1] ? "PASS" : "FAIL", _Symbol);
   if(!pass[1]) all_pass = false;

   // 3. History Loaded (at least 500 bars per timeframe: W1, D1, H4, H1, M15)
   int b_w1  = iBars(_Symbol, PERIOD_W1);
   int b_d1  = iBars(_Symbol, PERIOD_D1);
   int b_h4  = iBars(_Symbol, PERIOD_H4);
   int b_h1  = iBars(_Symbol, PERIOD_H1);
   int b_m15 = iBars(_Symbol, PERIOD_M15);
   pass[2] = (b_w1 >= 500 && b_d1 >= 500 && b_h4 >= 500 && b_h1 >= 500 && b_m15 >= 500);
   items[2] = StringFormat("History loaded: %s (W1: %d, D1: %d, H4: %d, H1: %d, M15: %d | Min: 500)",
                           pass[2] ? "PASS" : "FAIL", b_w1, b_d1, b_h4, b_h1, b_m15);
   if(!pass[2]) all_pass = false;

   // 4. Broker UTC Offset Detected
   g_broker_utc_offset = (int)(TimeTradeServer() - TimeGMT());
   pass[3] = true;
   items[3] = StringFormat("Broker UTC offset detected: %s (Offset: %+d sec, %+.1f hrs)", "PASS", g_broker_utc_offset, (double)g_broker_utc_offset / 3600.0);

   // 5. Spread/ATR Readable
   double pt = SymbolInfoDouble(_Symbol, SYMBOL_POINT);
   if(pt <= 0.0) pt = 0.01;
   int cur_spread = (int)SymbolInfoInteger(_Symbol, SYMBOL_SPREAD);
   double atr_test[];
   ArraySetAsSeries(atr_test, true);
   bool atr_read = (g_atr_handle_m5 != INVALID_HANDLE && CopyBuffer(g_atr_handle_m5, 0, 0, 1, atr_test) > 0);
   pass[4] = (cur_spread >= 0 && atr_read);
   items[4] = StringFormat("Spread/ATR readable: %s (Spread: %d pts, ATR_M5: %.2f)",
                           pass[4] ? "PASS" : "FAIL", cur_spread, atr_read ? (atr_test[0]/pt) : 0.0);
   if(!pass[4]) all_pass = false;

   // 6. Account Profile & Risk Limits Loaded
   long login = AccountInfoInteger(ACCOUNT_LOGIN);
   double bal = AccountInfoDouble(ACCOUNT_BALANCE);
   pass[5] = (login > 0 && bal > 0);
   string profile_str = (InpAccountProfile == PROFILE_DEMO ? "DEMO" : (InpAccountProfile == PROFILE_LIVE ? "LIVE" : "PROP"));
   items[5] = StringFormat("Account profile and risk limits loaded: %s (Login: #%d, Profile: %s, Balance: $%.2f, DailyCap: %.1f%%, TotalDD: %.1f%%)",
                           pass[5] ? "PASS" : "FAIL", (int)login, profile_str, bal, InpMaxDailyLossPercent, InpMaxTotalDrawdownPercent);
   if(!pass[5]) all_pass = false;

   // 7. Single-Instance Lock Held
   string lock_key = StringFormat("SMC_LOCK_%d_%d", InpMagicNumber, (int)login);
   pass[6] = GlobalVariableCheck(lock_key);
   items[6] = StringFormat("Single-instance lock held: %s (Key: %s)", pass[6] ? "PASS" : "FAIL", lock_key);
   if(!pass[6]) all_pass = false;

   // Log to Experts Tab & Daily Log
   LogEvent("INFO", _Symbol, EnumToString(InpSignalTimeframe), "=================== STARTUP WARM-UP CHECKLIST ===================");
   for(int i = 0; i < 7; i++)
   {
      LogEvent(pass[i] ? "INFO" : "CRITICAL", _Symbol, EnumToString(InpSignalTimeframe), StringFormat("Checklist Item [%d/7] %s", i+1, items[i]));
   }
   LogEvent("INFO", _Symbol, EnumToString(InpSignalTimeframe), StringFormat("Checklist Status: %s", all_pass ? "ALL PASS" : "PENDING / RETRYING"));

   // Send to Telegram
   string tg_report = "🔍 <b>STARTUP WARM-UP CHECKLIST</b>\\n════════════════════════════════════\\n";
   for(int i = 0; i < 7; i++)
   {
      tg_report += StringFormat("%s %s\\n", pass[i] ? "✅" : "❌", items[i]);
   }
   tg_report += StringFormat("════════════════════════════════════\\n<b>Result:</b> %s",
                             all_pass ? "✅ <b>CHECKLIST PASSED</b>" : "⚠️ <b>PENDING ITEMS</b>");
   SendTelegramFallbackAlert(tg_report);

   return all_pass;
}

//+------------------------------------------------------------------+
//| Indicator Warm-Up Verification                                   |
//+------------------------------------------------------------------+
bool CheckIndicatorWarmup()
{
   int calc_m5 = BarsCalculated(g_atr_handle_m5);
   int calc_h1 = BarsCalculated(g_atr_handle_h1);
   int calc_adx = BarsCalculated(g_adx_handle_m5);
   int calc_spread_atr = BarsCalculated(g_spread_atr_handle);

   if(calc_m5 < InpWarmupBars || calc_h1 < InpWarmupBars || calc_adx < InpWarmupBars || calc_spread_atr < InpWarmupBars)
   {
      LogEvent("INFO", _Symbol, EnumToString(InpSignalTimeframe),
               StringFormat("Indicator Warm-up: ATR_M5=%d/%d, ATR_H1=%d/%d, ADX_M5=%d/%d, SpreadATR=%d/%d (Awaiting %d closed bars)",
                            calc_m5, InpWarmupBars, calc_h1, InpWarmupBars, calc_adx, InpWarmupBars, calc_spread_atr, InpWarmupBars, InpWarmupBars));
      return false;
   }
   return true;
}

//+------------------------------------------------------------------+
//| Bar-Close Signal Evaluation & Countdown                          |
//+------------------------------------------------------------------+
void EvaluateClosedBarSetup(const datetime closed_bar_time)
{
   // 1. Indicator Values on CLOSED bar (shift 1)
   double atr_m5_arr[], atr_h1_arr[], adx_arr[];
   ArraySetAsSeries(atr_m5_arr, true);
   ArraySetAsSeries(atr_h1_arr, true);
   ArraySetAsSeries(adx_arr, true);

   CopyBuffer(g_atr_handle_m5, 0, 1, 1, atr_m5_arr);
   CopyBuffer(g_atr_handle_h1, 0, 1, 1, atr_h1_arr);
   CopyBuffer(g_adx_handle_m5, 0, 1, 1, adx_arr);

   double pt = SymbolInfoDouble(_Symbol, SYMBOL_POINT);
   if(pt <= 0.0) pt = 0.01;
   double atr_m5_pts = (ArraySize(atr_m5_arr) > 0) ? (atr_m5_arr[0] / pt) : 0.0;
   double atr_h1_pts = (ArraySize(atr_h1_arr) > 0) ? (atr_h1_arr[0] / pt) : 0.0;
   double adx_m5_val = (ArraySize(adx_arr) > 0) ? adx_arr[0] : 0.0;
   int cur_spread_pts = (int)SymbolInfoInteger(_Symbol, SYMBOL_SPREAD);
   double closed_price = iClose(_Symbol, InpSignalTimeframe, 1);

   // Trends per timeframe on closed bars
   string trend_m15 = (iClose(_Symbol, PERIOD_M15, 1) >= iOpen(_Symbol, PERIOD_M15, 1)) ? "BULLISH" : "BEARISH";
   string trend_h1  = (iClose(_Symbol, PERIOD_H1, 1) >= iOpen(_Symbol, PERIOD_H1, 1)) ? "BULLISH" : "BEARISH";
   string trend_h4  = (iClose(_Symbol, PERIOD_H4, 1) >= iOpen(_Symbol, PERIOD_H4, 1)) ? "BULLISH" : "BEARISH";

   LogEvent("INFO", _Symbol, EnumToString(InpSignalTimeframe),
            StringFormat("Bar closed @ %.5f | ATR_M5=%.1f pts, ATR_H1=%.1f pts, ADX_M5=%.1f, Spread=%d pts | Trends: M15=%s, H1=%s, H4=%s",
                         closed_price, atr_m5_pts, atr_h1_pts, adx_m5_val, cur_spread_pts, trend_m15, trend_h1, trend_h4));

   // 2. Mode Choice
   string mode_reason = "";
   ENUM_STRATEGY_MODE mode = ClassifyAdaptiveMode(_Symbol, mode_reason);
   string mode_str = (mode == MODE_SCALP ? "SCALP" : (mode == MODE_HOLD ? "HOLD" : "NO TRADE"));
   LogEvent("DECISION", _Symbol, EnumToString(InpSignalTimeframe),
            StringFormat("Mode choice: %s | Reason: %s", mode_str, mode_reason));

   // 3. Filter Results
   string spread_msg = "";
   double test_sl_dist = (mode == MODE_SCALP ? (atr_m5_pts * InpScalpSL_ATRMult * pt) : (atr_h1_pts * InpHoldSL_ATRMult * pt));
   bool spread_pass = ValidateSpreadFilter(_Symbol, closed_price, closed_price - test_sl_dist, mode, spread_msg);

   string news_details = "";
   bool news_pass = !IsNewsBlackoutActive(_Symbol, news_details);

   bool hours_pass = true;
   if(InpNoWeekendHolding)
   {
      MqlDateTime dt;
      TimeToStruct(TimeCurrent(), dt);
      if(dt.day_of_week == 5 && dt.hour >= InpWeekendCloseHourFriday) hours_pass = false;
   }

   bool risk_pass = (!g_risk_halted && !g_buffer_halted && !g_hard_locked_today);
   double cur_eq = AccountInfoDouble(ACCOUNT_EQUITY);
   double dd_pct = (g_peak_equity > 0 ? (g_peak_equity - cur_eq) / g_peak_equity * 100.0 : 0.0);

   LogEvent("DECISION", _Symbol, EnumToString(InpSignalTimeframe),
            StringFormat("Filter results: Spread=%s (%s), News=%s%s, Hours=%s, RiskLimits=%s (DailyLoss=%.2f%%, DD=%.2f%%)",
                         spread_pass ? "PASS" : "FAIL", spread_pass ? "OK" : spread_msg,
                         news_pass ? "PASS" : "BLOCKED", news_pass ? "" : " [" + news_details + "]",
                         hours_pass ? "PASS" : "WEEKEND_CLOSE",
                         risk_pass ? "PASS" : "HALTED",
                         g_daily_loss, dd_pct));
}

void CheckBarTimingAndEvaluate()
{
   // 1. Startup Warm-up checklist gating
   if(!g_warmup_checklist_passed)
   {
      if(EnsureConnection())
      {
         g_warmup_checklist_passed = RunStartupChecklist();
      }
      return;
   }

   // 2. Indicator Warm-up gating
   if(!g_warmup_complete)
   {
      if(CheckIndicatorWarmup())
      {
         g_warmup_complete = true;
         string msg = "WARM-UP COMPLETE: trading logic active";
         LogEvent("INFO", _Symbol, EnumToString(InpSignalTimeframe), msg);
         SendTelegramFallbackAlert(StringFormat("🚀 <b>%s</b>\\n\\nAll indicators warmed up (%d closed bars). Real-time signal evaluation active.", msg, InpWarmupBars));
      }
      else
      {
         return;
      }
   }

   // 3. Bar Timing Logic: evaluate only on CLOSED bars
   datetime current_bar_open = iTime(_Symbol, InpSignalTimeframe, 0);
   if(current_bar_open == 0) return;

   datetime now_server = TimeTradeServer();
   int tf_sec = PeriodSeconds(InpSignalTimeframe);
   datetime next_bar_time = current_bar_open + tf_sec;
   int sec_left = (int)(next_bar_time - now_server);
   if(sec_left < 0) sec_left = 0;
   int min_left = (sec_left + 59) / 60;

   if(g_last_closed_bar_time == 0)
   {
      g_last_closed_bar_time = current_bar_open;
      return;
   }

   // Has the bar closed?
   if(current_bar_open > g_last_closed_bar_time)
   {
      datetime just_closed_time = g_last_closed_bar_time;
      g_last_closed_bar_time = current_bar_open;
      g_last_countdown_logged_minute = -1;

      // 4. Optional SkipMinutesAfterOpen
      if(InpSkipMinutesAfterOpen > 0)
      {
         int elapsed_sec = (int)(now_server - current_bar_open);
         if(elapsed_sec < InpSkipMinutesAfterOpen * 60)
         {
            LogEvent("DECISION", _Symbol, EnumToString(InpSignalTimeframe),
                     StringFormat("SkipMinutesAfterOpen active (%d sec < %d min). Postponing setup evaluation.",
                                  elapsed_sec, InpSkipMinutesAfterOpen));
            return;
         }
      }

      // Bar closed evaluation
      EvaluateClosedBarSetup(just_closed_time);
   }
   else
   {
      // Candle is forming! Never act on forming candle.
      // Log countdown periodically (on each minute change)
      if(min_left != g_last_countdown_logged_minute)
      {
         g_last_countdown_logged_minute = min_left;
         LogEvent("INFO", _Symbol, EnumToString(InpSignalTimeframe),
                  StringFormat("Waiting for bar close, %d minutes left", min_left));
      }
   }
}
'''

if "void LogEvent(" not in ea_text:
    target_insert = "//+------------------------------------------------------------------+\n//| Expert initialization function"
    ea_text = ea_text.replace(target_insert, helpers_code + "\n" + target_insert)
    print("Added helper functions, LogEvent, and Warm-up methods.")

# 5. Update OnTimer to run Warm-up, CheckBarTimingAndEvaluate, Heartbeat line, and FlushFallbackFeed
old_ontimer_start = "void OnTimer()\n{"
new_ontimer_start = """void OnTimer()
{
   // Update single-instance heartbeat
   string lock_key = StringFormat("SMC_LOCK_%d_%d", InpMagicNumber, (int)AccountInfoInteger(ACCOUNT_LOGIN));
   GlobalVariableSet(lock_key, (double)TimeCurrent());

   // Periodic Heartbeat activity line every InpHeartbeatMinutes
   datetime now_timer = TimeCurrent();
   if(now_timer - g_last_heartbeat_feed_time >= InpHeartbeatMinutes * 60)
   {
      g_last_heartbeat_feed_time = now_timer;
      LogEvent("INFO", _Symbol, EnumToString(InpSignalTimeframe),
               StringFormat("Heartbeat: Equity=$%.2f | Balance=$%.2f | Positions=%d | DailyLoss=%.2f%% | PeakEq=$%.2f | Bridge=%s",
                            AccountInfoDouble(ACCOUNT_EQUITY), AccountInfoDouble(ACCOUNT_BALANCE),
                            PositionsTotal(), g_daily_loss, g_peak_equity,
                            (g_socket != INVALID_HANDLE && SocketIsConnected(g_socket) ? "CONNECTED" : "DISCONNECTED")),
               false);
   }"""

if "string lock_key = StringFormat(\"SMC_LOCK_%d_%d\", InpMagicNumber, (int)AccountInfoInteger(ACCOUNT_LOGIN));\n   GlobalVariableSet(lock_key, (double)TimeCurrent());" in ea_text:
    old_block = """void OnTimer()
{
   // Update single-instance heartbeat
   string lock_key = StringFormat("SMC_LOCK_%d_%d", InpMagicNumber, (int)AccountInfoInteger(ACCOUNT_LOGIN));
   GlobalVariableSet(lock_key, (double)TimeCurrent());"""
    ea_text = ea_text.replace(old_block, new_ontimer_start)
    print("Updated OnTimer start with heartbeat activity line.")

# In OnTimer tail, call CheckBarTimingAndEvaluate() and FlushFallbackFeed()
old_ontimer_end = """   // 5. Chart Visualization & Dashboard
   DrawWeeklyDailyLevels();
   UpdateDashboardVisuals();
}"""

new_ontimer_end = """   // 5. Chart Visualization & Dashboard
   DrawWeeklyDailyLevels();
   UpdateDashboardVisuals();

   // 6. Warm-Up, Bar-Close Evaluation & Countdown
   CheckBarTimingAndEvaluate();

   // 7. Flush Fallback Feed (if native Telegram active and queue has lines)
   FlushFallbackFeed();
}"""

if old_ontimer_end in ea_text:
    ea_text = ea_text.replace(old_ontimer_end, new_ontimer_end)
    print("Updated OnTimer end with CheckBarTimingAndEvaluate and FlushFallbackFeed.")

# 6. Update OnTick to call CheckBarTimingAndEvaluate()
old_ontick = """void OnTick()
{
   if(g_socket != INVALID_HANDLE && SocketIsConnected(g_socket))
   {
      ProcessIncomingData();
   }
   ManageActivePositions();
}"""

new_ontick = """void OnTick()
{
   if(g_socket != INVALID_HANDLE && SocketIsConnected(g_socket))
   {
      ProcessIncomingData();
   }
   ManageActivePositions();
   CheckBarTimingAndEvaluate();
}"""

if old_ontick in ea_text:
    ea_text = ea_text.replace(old_ontick, new_ontick)
    print("Updated OnTick with CheckBarTimingAndEvaluate.")

# 7. Add action == "SIGNAL" and action == "TEST_LOG" to ProcessCommand
signal_cmd = '''
   // 1d. SIGNAL (Audio & push notification trigger)
   if(action == "SIGNAL")
   {
      string sym = ExtractJsonString(json, "symbol", _Symbol);
      string dir = ExtractJsonString(json, "direction", "BUY");
      double entry = ExtractJsonDouble(json, "entry", 0.0);
      double sl = ExtractJsonDouble(json, "sl", 0.0);
      double tp = ExtractJsonDouble(json, "tp", 0.0);
      double rr = ExtractJsonDouble(json, "rr", 2.0);
      string mode_req = ExtractJsonString(json, "mode", "SCALP");

      LogEvent("SIGNAL", sym, EnumToString(InpSignalTimeframe),
               StringFormat("Signal found: %s @ %.5f | SL=%.5f, TP=%.5f, RR=%.2f, Mode=%s",
                            dir, entry, sl, tp, rr, mode_req));

      return StringFormat("{\\\"status\\\":\\\"SUCCESS\\\",\\\"request_id\\\":\\\"%s\\\",\\\"message\\\":\\\"Signal alert processed\\\"}", req_id);
   }

   // 1e. TEST_LOG (Inject test log event)
   if(action == "TEST_LOG")
   {
      string lvl = ExtractJsonString(json, "level", "INFO");
      string lmsg = ExtractJsonString(json, "message", "Test log event");
      string sym = ExtractJsonString(json, "symbol", _Symbol);
      LogEvent(lvl, sym, EnumToString(InpSignalTimeframe), lmsg);
      return StringFormat("{\\\"status\\\":\\\"SUCCESS\\\",\\\"request_id\\\":\\\"%s\\\",\\\"message\\\":\\\"Log event injected\\\"}", req_id);
   }
'''

if 'action == "SIGNAL"' not in ea_text:
    target_cmd = '   // 1b. ENABLE / AUTHORIZE TRADING'
    ea_text = ea_text.replace(target_cmd, signal_cmd + "\n" + target_cmd)
    print("Added SIGNAL and TEST_LOG command handlers.")

# 8. Update CheckBreakEven, CheckTrailingStop, CheckScalpTimeStop to log TRADE events
old_be_mod = "      if(g_trade.PositionModify(ticket, new_sl, tp))\n      {\n         PrintFormat(\"[PythonBridgeEA] [BE] Position #%d (%s) SL moved to Break-Even + Buffer: %.5f\","
new_be_mod = "      if(g_trade.PositionModify(ticket, new_sl, tp))\n      {\n         LogEvent(\"TRADE\", sym, \"M15\", StringFormat(\"Break-Even moved: Ticket #%d | New SL=%.5f (Entry=%.5f, CurPrice=%.5f)\", ticket, new_sl, open_price, cur_price));\n         PrintFormat(\"[PythonBridgeEA] [BE] Position #%d (%s) SL moved to Break-Even + Buffer: %.5f\","

if old_be_mod in ea_text:
    ea_text = ea_text.replace(old_be_mod, new_be_mod)
    print("Updated CheckBreakEven with LogEvent TRADE.")

old_trail_mod = "      if(g_trade.PositionModify(ticket, new_sl, tp))\n      {\n         PrintFormat(\"[PythonBridgeEA] [TRAIL] Position #%d (%s) Trailing SL adjusted to %.5f\","
new_trail_mod = "      if(g_trade.PositionModify(ticket, new_sl, tp))\n      {\n         LogEvent(\"TRADE\", sym, \"M15\", StringFormat(\"Trailing SL adjusted: Ticket #%d | New SL=%.5f (CurPrice=%.5f, Step=%.1f pts)\", ticket, new_sl, cur_price, InpTrailStep));\n         PrintFormat(\"[PythonBridgeEA] [TRAIL] Position #%d (%s) Trailing SL adjusted to %.5f\","

if old_trail_mod in ea_text:
    ea_text = ea_text.replace(old_trail_mod, new_trail_mod)
    print("Updated CheckTrailingStop with LogEvent TRADE.")

old_time_stop = "         if(g_trade.PositionClose(ticket))\n         {\n            PrintFormat(\"[PythonBridgeEA] [TIME-STOP] Scalp position #%d (%s) closed after %d bars\","
new_time_stop = "         if(g_trade.PositionClose(ticket))\n         {\n            LogEvent(\"TRADE\", g_pos_info.Symbol(), \"M5\", StringFormat(\"Scalp time-stop exit: Ticket #%d | Closed after %d bars\", ticket, bars_elapsed));\n            PrintFormat(\"[PythonBridgeEA] [TIME-STOP] Scalp position #%d (%s) closed after %d bars\","

if old_time_stop in ea_text:
    ea_text = ea_text.replace(old_time_stop, new_time_stop)
    print("Updated CheckScalpTimeStop with LogEvent TRADE.")

# Write patched file
with open(ea_path, "w", encoding="utf-8") as f:
    f.write(ea_text)

print("Patch applied to PythonBridgeEA.mq5 successfully!")
