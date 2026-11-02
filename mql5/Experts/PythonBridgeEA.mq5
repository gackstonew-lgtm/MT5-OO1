//+------------------------------------------------------------------+
//|                                              PythonBridgeEA.mq5  |
//|                  Institutional MT5 Algorithmic Day-Trading Bridge|
//|                                  Copyright 2026, Quant Systems   |
//+------------------------------------------------------------------+
#property copyright "Quant Systems"
#property link      "https://github.com/JBCyberLogics/Trading-Bot"
#property version   "3.10"
#property description "Institutional MT5-Python TCP Bridge with Full Lifecycle, Single-Instance Guard & Adaptive Management"

//--- Sound Resources
#resource "\\Sounds\\signal.wav"
#resource "\\Sounds\\trade.wav"
#resource "\\Sounds\\alert.wav"


#include <Trade\Trade.mqh>
#include <Trade\PositionInfo.mqh>
#include <Trade\OrderInfo.mqh>
#include <Trade\AccountInfo.mqh>
#include <Trade\SymbolInfo.mqh>

//--- Enums
enum ENUM_STRATEGY_MODE
{
   MODE_AUTO  = 0, // AUTO (Dynamic Scalp vs Hold)
   MODE_SCALP = 1, // SCALP (Tight TP/SL, Fast Exits)
   MODE_HOLD  = 2  // HOLD (Swing, Wide Trailing Stop)
};

enum ENUM_TRAIL_MODE
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
};

//--- Input Parameters: Bridge & Network
input group "=== Bridge & Connection ==="
input string              InpServerHost            = "127.0.0.1"; // Python Bridge Server Host
input int                 InpServerPort            = 5555;        // Python Bridge Server Port
input int                 InpMagicNumber           = 100101;      // EA Magic Number
input uint                InpTimeoutMs             = 2000;        // Socket Timeout (ms)
input uint                InpWatchdogTimeoutSec    = 30;          // Heartbeat Watchdog Timeout (sec)

//--- Input Parameters: Adaptive Strategy Mode
input group "=== Strategy Modes ==="
input ENUM_STRATEGY_MODE  InpStrategyMode          = MODE_AUTO;   // Strategy Mode (AUTO/SCALP/HOLD)
input int                 InpATRPeriod             = 14;          // ATR Period
input double              InpADXThreshold          = 22.0;        // ADX Momentum Threshold for Scalp
input double              InpScalpTP_ATRMult       = 0.8;         // Scalp Take Profit (x ATR M5)
input double              InpScalpSL_ATRMult       = 1.2;         // Scalp Stop Loss (x ATR M5)
input int                 InpScalpMaxHoldBars      = 12;          // Scalp Time-Stop Max Bars (M5)
input int                 InpScalpMaxPerHour       = 3;           // Max Scalp Trades Per Hour
input double              InpHoldTP_ATRMult        = 3.0;         // Hold Take Profit (x ATR H1)
input double              InpHoldSL_ATRMult        = 2.0;         // Hold Stop Loss (x ATR H1)
input double              InpHoldPartialRatio      = 0.5;         // Hold Partial TP Ratio (50%)
input double              InpHoldPartialR          = 1.0;         // Hold Partial TP Trigger (R Multiple)

//--- Input Parameters: Break-Even & Trailing Stop
input group "=== Break-Even & Trailing Stop ==="
input bool                InpEnableBE              = true;        // Enable Break-Even Protection
input double              InpBE_TriggerR           = 1.0;         // Break-Even Trigger (R-Multiple)
input double              InpBE_BufferPoints       = 20.0;        // Break-Even Buffer Points above Entry
input ENUM_TRAIL_MODE     InpTrailMode             = TRAIL_ATR;   // Trailing Stop Mode
input double              InpTrailStartR           = 1.5;         // Trailing Start (R-Multiple)
input double              InpTrailPoints           = 150.0;       // Trailing Points Distance
input double              InpTrailStep             = 30.0;        // Trailing Step Points
input double              InpTrailATRMult          = 1.5;         // Trailing Stop ATR Multiplier
input int                 InpSwingBarsLookback     = 5;           // Swing High/Low Lookback Bars

//--- Input Parameters: Account Profile & Execution Authorization
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
input double               InpMinCapitalFloor       = 5.0;                 // Minimum Operating Capital Floor ($)
input bool                 InpMicroAccountMinLotAllowed = false;          // Allow trading min lot on micro accounts (default false)
input bool                 InpAcknowledgedHighRiskMicroaccount = false;   // Explicit operator risk acknowledgment (default false)
input double               InpAbsoluteRiskCeiling   = 5.0;                 // Absolute maximum risk ceiling %
input double               InpMaxRiskPercent        = 1.0;                 // Default Risk % Per Trade
input double               InpMaxRiskPercentPerTrade= 2.0;                 // Hard Cap: Max Risk % Per Trade
input double               InpMinRR                 = 2.0;                 // Hard Cap: Min Reward-to-Risk Ratio
input double               InpReducedHistoryRiskFactor = 0.5;              // Risk Multiplier in Reduced History Mode
input double               InpMaxTotalOpenRiskPercent = 5.0;              // Hard Cap: Max Total Open Risk % Across All Symbols
input double               InpMaxMarginPercentOfFree= 50.0;                // Max Margin Allowed (% of Free Margin)
input double               InpMaxLot                = 0.5;                 // Hard Cap: Maximum Lot Size
input int                  InpMaxPositions          = 3;                   // Hard Cap: Max Open Positions
input int                  InpMaxTradesPerDay       = 10;                  // Hard Cap: Max Trades Per Day
input int                  InpMaxConsecutiveLosses  = 3;                   // Consecutive Losses Pause Trigger
input int                  InpAuditIntervalSec      = 30;                  // Open Position Risk Audit Interval (sec)
input bool                 InpAutoCutOversized      = false;               // Auto-close/cut oversized positions

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
input bool                 InpForbidTickScalping    = true;                // Block ultra-short latency scalping

//--- Input Parameters: Warm-Up, History & Signal Timing
input group "=== Startup Warm-Up, History & Signal Timing ==="
input int                 InpWarmupBars            = 300;         // Indicator Warm-up Minimum Closed Bars
input int                 InpMinBars_W1            = 52;          // Min history bars for W1 (~1 year)
input int                 InpMinBars_D1            = 200;         // Min history bars for D1 (~10 months)
input int                 InpMinBars_H4            = 300;         // Min history bars for H4 (~2 months)
input int                 InpMinBars_H1            = 500;         // Min history bars for H1 (~1 month)
input int                 InpMinBars_M15           = 500;         // Min history bars for M15 (~5 days)
input int                 InpHistoryLoadTimeoutSec = 30;          // History force-load timeout (seconds)
input int                 InpHistoryRetryWaitMs    = 500;         // History check retry wait (ms)
enum ENUM_SHORT_HISTORY_ACTION
{
   SHORT_HIST_ASK         = 0, // ASK (Telegram prompt with /allowreduced and /blocksymbol)
   SHORT_HIST_REDUCE      = 1, // REDUCE (Auto-continue with available bars, flagged REDUCED_HISTORY)
   SHORT_HIST_BLOCK       = 2  // BLOCK_SYMBOL (Block this symbol, reason logged)
};
input ENUM_SHORT_HISTORY_ACTION InpOnShortHistory  = SHORT_HIST_ASK; // Action on short history (>= hard floor)
input int                 InpMaxStartupRounds      = 6;           // Max startup checklist retry rounds
input int                 InpAlertCooldownMin      = 15;          // Alert deduplication cooldown (minutes)
input string              InpSymbolsToMonitor      = "";          // Extra symbols to monitor (comma-separated)
input string              InpSymbolOverrides       = "";          // Per-symbol min bars overrides (e.g. "BTCUSD.m:W1=30,D1=150")
input int                 InpSkipMinutesAfterOpen  = 0;           // Skip Minutes After Bar Open (Wide-Spread Avoidance)
input ENUM_TIMEFRAMES     InpSignalTimeframe       = PERIOD_M15;  // Signal Evaluation Timeframe
input int                 InpFeedIntervalSec       = 10;          // Telegram Digest Feed Interval (seconds)
input int                 InpHeartbeatMinutes      = 5;           // Heartbeat Interval (minutes)
input int                 InpHeartbeatLogSec       = 60;          // Throttled Heartbeat Log Interval (sec)
input int                 InpHeartbeatSec          = 300;         // Periodic Heartbeat Status Interval (sec)
input int                 InpIdleDigestMin         = 15;          // Periodic Alive Digest Interval When Idle (min)

//--- Input Parameters: Sound & Audio Alerts
input group "=== Sound & Push Notifications ==="
input bool                InpSoundEnabled          = true;        // Enable Audio Sounds (PC MT5)
input string              InpSignalSound           = "signal.wav";// Sound for SIGNAL event (.wav)
input string              InpTradeSound            = "trade.wav"; // Sound for TRADE event (.wav)
input string              InpAlertSound            = "alert.wav"; // Sound for CRITICAL event (.wav)
input bool                InpAlertOnSignal         = true;        // Show MT5 Alert popup on SIGNAL

//--- Input Parameters: Dynamic Spread Filter (replaces fixed InpMaxSpreadPoints)
input group "=== Dynamic Spread Filter ==="
input double              InpMaxSpreadPctOfSL      = 5.0;         // Max spread as % of SL distance
input double              InpMaxSpreadAbsPoints    = 2000.0;      // Absolute spread ceiling in points (for BTCUSD.m)
input double              InpMaxSpreadPctOfATR     = 15.0;        // Max spread as % of ATR (for SCALP / no-SL)
input int                 InpSpreadATRPeriod       = 14;          // ATR Period for spread filter
input ENUM_TIMEFRAMES     InpSpreadATRTimeframe    = PERIOD_M15;  // ATR Timeframe for spread filter

input bool                InpKillSwitch            = false;       // Emergency Kill Switch
input bool                InpSendNotifications     = true;        // Enable Mobile Push Notifications

//--- Input Parameters: Telegram Alert Fallback
input group "=== Telegram Fallback Alerts (EA Native) ==="
input bool                InpTelegramFallbackEnabled = true;                                                      // Enable MQL5 Telegram Fallback
input string              InpTelegramBotToken        = "";                                                        // Telegram Bot Token (from BotFather);
input string              InpTelegramChatId          = "";                                                        // Telegram Chat ID;

//--- Input Parameters: Chart Visuals
input group "=== Chart Visuals ==="
input color               InpColorPWH              = clrRed;      // Previous Week High Color
input color               InpColorPWL              = clrGreen;    // Previous Week Low Color
input color               InpColorPDH              = clrOrange;   // Previous Day High Color
input color               InpColorPDL              = clrAqua;     // Previous Day Low Color

//--- Global Objects & State
int            g_socket = INVALID_HANDLE;
datetime       g_last_connect_attempt = 0;
datetime       g_last_logged_failure = 0;
datetime       g_last_heartbeat_time = 0;
bool           g_watchdog_halted = false;
datetime       g_last_telegram_fallback_alert = 0;
string         g_rx_buffer = "";

// Network Exponential Backoff & Jitter
int            g_reconnect_backoff_sec = 1;
datetime       g_next_reconnect_time = 0;

// Broker UTC Synchronization
int            g_broker_utc_offset = 0;

// Dynamic Trading State & Prop Tracking
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
string         g_risk_halt_reason = "";

// Request Deduplication
#define MAX_REQ_CACHE 128
string         g_processed_req_ids[MAX_REQ_CACHE];
string         g_processed_req_results[MAX_REQ_CACHE];
int            g_cache_index = 0;

// Dynamic Activity Feed, Warm-Up & Sound State
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

// Indicator handles
int            g_atr_handle_m5   = INVALID_HANDLE;
int            g_atr_handle_h1   = INVALID_HANDLE;
int            g_adx_handle_m5   = INVALID_HANDLE;
int            g_spread_atr_handle = INVALID_HANDLE;

CTrade         g_trade;
CPositionInfo  g_pos_info;
COrderInfo     g_ord_info;
CAccountInfo   g_acc_info;
CSymbolInfo    g_sym_info;

// Forward Declarations
bool   EnsureConnection();
void   DisconnectSocket();
void   ProcessIncomingData();
void   HandleMessage(const string message);
string ProcessCommand(const string json_request);
bool   IsDuplicateRequest(const string req_id, string &cached_result);
void   CacheRequestResult(const string req_id, const string result_json);
bool   ValidatePreTrade(const string sym, const double volume, const double sl, const double tp, const string type_str, string &reason);
void   EvaluateRealTimeRisk();
int    CloseAllPositions();
bool   IsNewsBlackoutActive(const string sym, string &news_details);
void   UpdateDashboardVisuals();
bool   ValidateSpreadFilter(const string sym, const double entry_price, const double sl, const ENUM_STRATEGY_MODE mode, string &rejection_msg);
bool   ValidateStopDirections(const string sym, const string type_str, const double entry_price, const double sl, const double tp, string &err_msg);
double CalculateLotSize(const string sym, const double risk_pct, const double sl_distance_points, string &sizing_log);
bool   CalculateSLTPMoney(const string sym, const ENUM_ORDER_TYPE order_type, const double volume, const double entry_price, const double sl_price, const double tp_price, double &sl_money, double &tp_money, double &sl_dist_pts, double &tp_dist_pts, double &rr);
double GetTotalOpenRiskMoney();
void   AuditOpenPositionsRisk();
string FormatSymbolStatusJson(const string req_id);
ENUM_ORDER_TYPE_FILLING GetSymbolFillingMode(const string sym);
void   ConfigureFillingMode(const string sym);
void   ManageActivePositions();
void   CheckBreakEven(const ulong ticket, const string sym, const ENUM_POSITION_TYPE type, const double open_price, const double cur_price, double &sl, const double tp, const double r_points);
void   CheckTrailingStop(const ulong ticket, const string sym, const ENUM_POSITION_TYPE type, const double open_price, const double cur_price, double &sl, const double tp, const double r_points, const ENUM_STRATEGY_MODE mode);
void   CheckScalpTimeStop(const ulong ticket, const datetime open_time, const ENUM_STRATEGY_MODE mode);
ENUM_STRATEGY_MODE ClassifyAdaptiveMode(const string sym, string &decision_reason);
void   DrawWeeklyDailyLevels();
void   UpdateChartPositionLabel(const ulong ticket, const string sym, const ENUM_STRATEGY_MODE mode, const double entry_price, const string tag);
void   DeleteAllChartObjects();
void   AppendJournal(const string sym, const string mode, const string action, const double entry, const double sl, const double tp, const double lots, const uint retcode, const ulong ticket, const string reason);
void   NotifyUser(const string msg);
void   SendTelegramFallbackAlert(const string message);
string ExtractJsonString(const string json, const string key, const string def_val = "");
double ExtractJsonDouble(const string json, const string key, const double def_val = 0.0);
long   ExtractJsonInteger(const string json, const string key, const long def_val = 0);
string FormatRatesJson(const string symbol, const string tf_str, const int count, const string req_id = "");
ENUM_TIMEFRAMES StringToTimeframe(const string tf_str);


//+------------------------------------------------------------------+
//| Audio & Push Notification Dispatchers                            |
//+------------------------------------------------------------------+
void PlayAudioAlert(const string sound_file)
{
   if(!InpSoundEnabled) return;
   if(sound_file == "" || sound_file == "none") return;

   // Try playing embedded resource first
   string res_path = "::Sounds\\" + sound_file;
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
   string filename = "logs\\activity_" + date_str + ".log";
   int handle = FileOpen(filename, FILE_READ|FILE_WRITE|FILE_TXT|FILE_SHARE_READ|FILE_ANSI);
   if(handle != INVALID_HANDLE)
   {
      FileSeek(handle, 0, SEEK_END);
      FileWriteString(handle, line + "\r\n");
      FileClose(handle);
   }
}

string EscapeJsonString(const string src)
{
   string res = src;
   StringReplace(res, "\\", "\\\\");
   StringReplace(res, "\"", "\\\"");
   StringReplace(res, "\r", "");
   StringReplace(res, "\n", "\\n");
   StringReplace(res, "\t", " ");
   return res;
}

void QueueFallbackFeed(const string level, const string line)
{
   if(!InpTelegramFallbackEnabled) return;
   if(level == "SIGNAL" || level == "TRADE" || level == "CRITICAL")
   {
      string alert_text = StringFormat("<b>[%s]</b>\n<code>%s</code>", level, line);
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

   string digest = StringFormat("📋 <b>EA FALLBACK DIGEST (%d events)</b>\n<pre>", g_fallback_queue_count);
   for(int i = 0; i < g_fallback_queue_count; i++)
   {
      if(StringLen(digest) + StringLen(g_fallback_queue[i]) > 3800) break;
      digest += g_fallback_queue[i] + "\n";
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
         "{\"action\":\"FEED_EVENT\",\"level\":\"%s\",\"symbol\":\"%s\",\"tf\":\"%s\",\"line\":\"%s\",\"time_utc\":\"%s\",\"time_local\":\"%s\"}\n",
         level, sym, tf_str, EscapeJsonString(line), time_utc, time_utc3
      );
      uchar data[];
      StringToCharArray(json_event, data, 0, StringLen(json_event));
      SocketSend(g_socket, data, StringLen(json_event));
   }
   else
   {
      QueueFallbackFeed(level, line);
   }
}

//+------------------------------------------------------------------+
//| Multi-Symbol State & History Tracking                            |
//+------------------------------------------------------------------+
enum ENUM_SYMBOL_STATE
{
   SYM_STATE_INIT           = 0, // Initializing
   SYM_STATE_CHECKING       = 1, // Running startup rounds
   SYM_STATE_READY          = 2, // Full history satisfied, ready
   SYM_STATE_READY_REDUCED  = 3, // Reduced history authorized, ready
   SYM_STATE_PENDING_USER   = 4, // History < min but >= floor, awaiting user choice
   SYM_STATE_BLOCKED        = 5  // History < floor or blocked by user
};

struct SymbolHistoryTF
{
   ENUM_TIMEFRAMES tf;
   string          tf_name;
   int             bars_available;
   int             bars_min;
   int             bars_floor;
   bool            synchronized;
   datetime        first_date;
   int             stuck_count;
};

struct SymbolHistoryRecord
{
   string              symbol;
   ENUM_SYMBOL_STATE   state;
   string              state_reason;
   int                 rounds_used;
   datetime            last_check_time;
   bool                checklist_passed;
   int                 w1_bars, d1_bars, h4_bars, h1_bars, m15_bars;
   int                 w1_min,  d1_min,  h4_min,  h1_min,  m15_min;
   int                 w1_floor, d1_floor, h4_floor, h1_floor, m15_floor;
   bool                w1_synced, d1_synced, h4_synced, h1_synced, m15_synced;
};

SymbolHistoryRecord g_symbol_history_map[];
int g_suppressed_alerts_count = 0;

// Central alert deduplication gate
struct AlertGateEntry
{
   string   key;
   string   values_hash;
   ulong    last_sent_ms;
   string   last_status;
};
AlertGateEntry g_alert_gate[];

// Backoff schedule for startup retries: 5s, 10s, 20s, 30s, 30s, 30s
const int g_round_wait_sec[6] = {5, 10, 20, 30, 30, 30};
int   g_startup_round = 0;
ulong g_next_checklist_tick = 0;
bool  g_startup_circuit_breaker = false;
bool  g_startup_summary_sent = false;

// Last printed checklist lines for printing ONLY what changed in rounds 2..5
string g_last_printed_items[7];

//+------------------------------------------------------------------+
//| Unit formatting helpers (Points, Price, Offset)                  |
//+------------------------------------------------------------------+
string FormatPtsAndPrice(const string sym, const double pts)
{
   double pt = SymbolInfoDouble(sym, SYMBOL_POINT);
   if(pt <= 0.0) pt = 0.01;
   int digits = (int)SymbolInfoInteger(sym, SYMBOL_DIGITS);
   double price_diff = pts * pt;
   return StringFormat("%.2f pts ($%.*f)", pts, digits, price_diff);
}

string FormatSpreadAndPrice(const string sym, const int spread_pts)
{
   double pt = SymbolInfoDouble(sym, SYMBOL_POINT);
   if(pt <= 0.0) pt = 0.01;
   int digits = (int)SymbolInfoInteger(sym, SYMBOL_DIGITS);
   double price_diff = spread_pts * pt;
   return StringFormat("%d pts ($%.*f)", spread_pts, digits, price_diff);
}

string FormatBrokerOffset(const int offset_sec)
{
   return StringFormat("%+.1f h (%+d sec)", (double)offset_sec / 3600.0, offset_sec);
}

//+------------------------------------------------------------------+
//| Hard floor computed dynamically from indicator periods          |
//+------------------------------------------------------------------+
int GetHardFloorBars(const ENUM_TIMEFRAMES tf)
{
   switch(tf)
   {
      case PERIOD_W1:  return MathMax(InpATRPeriod + 6, 20);       // Minimum 20 closed bars
      case PERIOD_D1:  return MathMax(InpATRPeriod * 2, 30);       // Minimum 30 closed bars
      case PERIOD_H4:  return MathMax(InpATRPeriod * 2 + 12, 40);  // Minimum 40 closed bars
      case PERIOD_H1:  return MathMax(InpATRPeriod * 3, 50);       // Minimum 50 closed bars
      case PERIOD_M15: return MathMax(InpATRPeriod * 3, 50);       // Minimum 50 closed bars
      default:         return MathMax(InpATRPeriod * 2, 30);
   }
}

//+------------------------------------------------------------------+
//| Central Alert Deduplication Gate                                 |
//+------------------------------------------------------------------+
bool CheckAlertGate(const string key, const string status, const string values_str, string &out_alert_msg)
{
   ulong now_ms = GetTickCount64();
   int found_idx = -1;
   for(int i = 0; i < ArraySize(g_alert_gate); i++)
   {
      if(g_alert_gate[i].key == key)
      {
         found_idx = i;
         break;
      }
   }

   if(status == "PASS")
   {
      if(found_idx >= 0 && (g_alert_gate[found_idx].last_status == "FAIL" || g_alert_gate[found_idx].last_status == "WARNING"))
      {
         g_alert_gate[found_idx].last_status = "PASS";
         g_alert_gate[found_idx].last_sent_ms = now_ms;
         out_alert_msg = StringFormat("RESOLVED: Check '%s' is now PASS. Numbers: %s", key, values_str);
         return true;
      }
      return false;
   }

   // Status is FAIL or WARNING
   if(found_idx < 0)
   {
      int sz = ArraySize(g_alert_gate);
      ArrayResize(g_alert_gate, sz + 1);
      g_alert_gate[sz].key = key;
      g_alert_gate[sz].values_hash = values_str;
      g_alert_gate[sz].last_sent_ms = now_ms;
      g_alert_gate[sz].last_status = status;
      out_alert_msg = StringFormat("[%s] Check '%s': %s", status, key, values_str);
      return true;
   }

   ulong elapsed_sec = (now_ms - g_alert_gate[found_idx].last_sent_ms) / 1000;
   bool values_changed = (g_alert_gate[found_idx].values_hash != values_str);
   bool cooldown_expired = (elapsed_sec >= (ulong)InpAlertCooldownMin * 60);

   if(values_changed || cooldown_expired)
   {
      g_alert_gate[found_idx].values_hash = values_str;
      g_alert_gate[found_idx].last_sent_ms = now_ms;
      g_alert_gate[found_idx].last_status = status;
      out_alert_msg = StringFormat("[%s%s] Check '%s': %s", status, cooldown_expired ? " (COOLDOWN_REPEAT)" : "", key, values_str);
      return true;
   }

   g_suppressed_alerts_count++;
   return false;
}

//+------------------------------------------------------------------+
//| Persist and Read User History Choices                            |
//+------------------------------------------------------------------+
string GetChoiceFileName() { return "symbol_history_choices.txt"; }

ENUM_SHORT_HISTORY_ACTION GetPersistedChoice(const string sym)
{
   string gv_name = "SHORTHIST_" + sym;
   if(GlobalVariableCheck(gv_name))
   {
      int val = (int)GlobalVariableGet(gv_name);
      if(val == 1) return SHORT_HIST_REDUCE;
      if(val == 2) return SHORT_HIST_BLOCK;
   }

   int h = FileOpen(GetChoiceFileName(), FILE_READ|FILE_TXT|FILE_ANSI);
   if(h != INVALID_HANDLE)
   {
      while(!FileIsEnding(h))
      {
         string line = FileReadString(h);
         string parts[];
         if(StringSplit(line, '=', parts) == 2)
         {
            if(parts[0] == sym)
            {
               FileClose(h);
               if(parts[1] == "REDUCE") return SHORT_HIST_REDUCE;
               if(parts[1] == "BLOCK")  return SHORT_HIST_BLOCK;
            }
         }
      }
      FileClose(h);
   }
   return InpOnShortHistory;
}

void PersistChoice(const string sym, const ENUM_SHORT_HISTORY_ACTION action)
{
   string gv_name = "SHORTHIST_" + sym;
   GlobalVariableSet(gv_name, (double)action);

   string lines[];
   int count = 0;
   int h = FileOpen(GetChoiceFileName(), FILE_READ|FILE_TXT|FILE_ANSI);
   if(h != INVALID_HANDLE)
   {
      while(!FileIsEnding(h))
      {
         string l = FileReadString(h);
         if(StringLen(l) > 0)
         {
            ArrayResize(lines, count + 1);
            lines[count++] = l;
         }
      }
      FileClose(h);
   }

   string action_str = (action == SHORT_HIST_REDUCE ? "REDUCE" : (action == SHORT_HIST_BLOCK ? "BLOCK" : "ASK"));
   bool updated = false;
   for(int i = 0; i < count; i++)
   {
      string p[];
      if(StringSplit(lines[i], '=', p) == 2 && p[0] == sym)
      {
         lines[i] = sym + "=" + action_str;
         updated = true;
         break;
      }
   }
   if(!updated)
   {
      ArrayResize(lines, count + 1);
      lines[count++] = sym + "=" + action_str;
   }

   h = FileOpen(GetChoiceFileName(), FILE_WRITE|FILE_TXT|FILE_ANSI);
   if(h != INVALID_HANDLE)
   {
      for(int i = 0; i < count; i++)
      {
         FileWriteString(h, lines[i] + "\r\n");
      }
      FileClose(h);
   }
}

void ResetPersistedChoice(const string sym)
{
   string gv_name = "SHORTHIST_" + sym;
   if(GlobalVariableCheck(gv_name)) GlobalVariableDel(gv_name);
}

//+------------------------------------------------------------------+
//| Force-Load History For Timeframe with Synchronization Check      |
//+------------------------------------------------------------------+
void ForceLoadTFHistory(const string sym, const ENUM_TIMEFRAMES tf, const string tf_name, const int target_min,
                        int &out_avail, bool &out_synced, datetime &out_first_date, int &out_floor)
{
   out_floor = GetHardFloorBars(tf);

   long max_chart_bars = TerminalInfoInteger(TERMINAL_MAXBARS);
   if(max_chart_bars < target_min)
   {
      LogEvent("WARNING", sym, tf_name,
               StringFormat("TERMINAL_MAXBARS (%d) is below required %d. Adjust Tools > Options > Charts > 'Max bars in chart'.",
                            (int)max_chart_bars, target_min));
   }

   datetime dt_dummy[];
   ArraySetAsSeries(dt_dummy, true);
   int copied = CopyTime(sym, tf, 0, target_min, dt_dummy);

   out_synced = (bool)SeriesInfoInteger(sym, tf, SERIES_SYNCHRONIZED);
   out_avail  = (int)SeriesInfoInteger(sym, tf, SERIES_BARS_COUNT);
   out_first_date = (datetime)SeriesInfoInteger(sym, tf, SERIES_FIRSTDATE);

   if(copied > out_avail) out_avail = copied;
}

//+------------------------------------------------------------------+
//| Find or Create Symbol History Record                             |
//+------------------------------------------------------------------+
int GetSymbolRecordIdx(const string sym)
{
   for(int i = 0; i < ArraySize(g_symbol_history_map); i++)
   {
      if(g_symbol_history_map[i].symbol == sym) return i;
   }
   int idx = ArraySize(g_symbol_history_map);
   ArrayResize(g_symbol_history_map, idx + 1);
   g_symbol_history_map[idx].symbol = sym;
   g_symbol_history_map[idx].state = SYM_STATE_INIT;
   g_symbol_history_map[idx].rounds_used = 0;
   g_symbol_history_map[idx].checklist_passed = false;
   return idx;
}

ENUM_SYMBOL_STATE GetSymbolState(const string sym)
{
   int idx = GetSymbolRecordIdx(sym);
   return g_symbol_history_map[idx].state;
}

//+------------------------------------------------------------------+
//| Single Round Startup Checklist Execution                         |
//+------------------------------------------------------------------+
bool RunStartupChecklistRound(const int round_num, const bool is_full_print, string &out_summary)
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

   // 3. History Loaded & Force-Load
   int sym_idx = GetSymbolRecordIdx(_Symbol);
   g_symbol_history_map[sym_idx].rounds_used = round_num;
   g_symbol_history_map[sym_idx].last_check_time = TimeCurrent();

   datetime fd_w1, fd_d1, fd_h4, fd_h1, fd_m15;
   int b_w1, b_d1, b_h4, b_h1, b_m15;
   bool s_w1, s_d1, s_h4, s_h1, s_m15;
   int f_w1, f_d1, f_h4, f_h1, f_m15;

   int req_w1 = InpMinBars_W1;
   int req_d1 = InpMinBars_D1;
   int req_h4 = InpMinBars_H4;
   int req_h1 = InpMinBars_H1;
   int req_m15 = InpMinBars_M15;

   // Per-symbol overrides (e.g. BTCUSD.m:W1=30,D1=150)
   if(StringLen(InpSymbolOverrides) > 0)
   {
      string pairs[];
      int p_cnt = StringSplit(InpSymbolOverrides, ';', pairs);
      for(int p = 0; p < p_cnt; p++)
      {
         string kv[];
         if(StringSplit(pairs[p], ':', kv) == 2 && kv[0] == _Symbol)
         {
            string tf_assigns[];
            int a_cnt = StringSplit(kv[1], ',', tf_assigns);
            for(int a = 0; a < a_cnt; a++)
            {
               string tf_kv[];
               if(StringSplit(tf_assigns[a], '=', tf_kv) == 2)
               {
                  int v = (int)StringToInteger(tf_kv[1]);
                  if(tf_kv[0] == "W1") req_w1 = v;
                  else if(tf_kv[0] == "D1") req_d1 = v;
                  else if(tf_kv[0] == "H4") req_h4 = v;
                  else if(tf_kv[0] == "H1") req_h1 = v;
                  else if(tf_kv[0] == "M15") req_m15 = v;
               }
            }
         }
      }
   }

   // Check if market trade is disabled
   long trade_mode = SymbolInfoInteger(_Symbol, SYMBOL_TRADE_MODE);
   if(trade_mode == SYMBOL_TRADE_MODE_DISABLED)
   {
      pass[2] = false;
      all_pass = false;
      items[2] = StringFormat("History loaded: BLOCKED (Symbol %s trade mode is DISABLED)", _Symbol);
      g_symbol_history_map[sym_idx].state = SYM_STATE_BLOCKED;
      g_symbol_history_map[sym_idx].state_reason = "Symbol trade mode is DISABLED";
   }
   else
   {
      ForceLoadTFHistory(_Symbol, PERIOD_W1, "W1", req_w1, b_w1, s_w1, fd_w1, f_w1);
      ForceLoadTFHistory(_Symbol, PERIOD_D1, "D1", req_d1, b_d1, s_d1, fd_d1, f_d1);
      ForceLoadTFHistory(_Symbol, PERIOD_H4, "H4", req_h4, b_h4, s_h4, fd_h4, f_h4);
      ForceLoadTFHistory(_Symbol, PERIOD_H1, "H1", req_h1, b_h1, s_h1, fd_h1, f_h1);
      ForceLoadTFHistory(_Symbol, PERIOD_M15, "M15", req_m15, b_m15, s_m15, fd_m15, f_m15);

      // Log growth if bars grew since last round
      if(round_num > 1 && b_w1 > g_symbol_history_map[sym_idx].w1_bars)
      {
         LogEvent("INFO", _Symbol, "W1", StringFormat("History force-load: count grew from %d to %d bars (Target: %d)",
                  g_symbol_history_map[sym_idx].w1_bars, b_w1, req_w1));
      }
      if(round_num > 1 && b_d1 > g_symbol_history_map[sym_idx].d1_bars)
      {
         LogEvent("INFO", _Symbol, "D1", StringFormat("History force-load: count grew from %d to %d bars (Target: %d)",
                  g_symbol_history_map[sym_idx].d1_bars, b_d1, req_d1));
      }

      g_symbol_history_map[sym_idx].w1_bars = b_w1;
      g_symbol_history_map[sym_idx].d1_bars = b_d1;
      g_symbol_history_map[sym_idx].h4_bars = b_h4;
      g_symbol_history_map[sym_idx].h1_bars = b_h1;
      g_symbol_history_map[sym_idx].m15_bars = b_m15;
      g_symbol_history_map[sym_idx].w1_min = req_w1;
      g_symbol_history_map[sym_idx].d1_min = req_d1;
      g_symbol_history_map[sym_idx].h4_min = req_h4;
      g_symbol_history_map[sym_idx].h1_min = req_h1;
      g_symbol_history_map[sym_idx].m15_min = req_m15;
      g_symbol_history_map[sym_idx].w1_floor = f_w1;
      g_symbol_history_map[sym_idx].d1_floor = f_d1;
      g_symbol_history_map[sym_idx].h4_floor = f_h4;
      g_symbol_history_map[sym_idx].h1_floor = f_h1;
      g_symbol_history_map[sym_idx].m15_floor = f_m15;
      g_symbol_history_map[sym_idx].w1_synced = s_w1;
      g_symbol_history_map[sym_idx].d1_synced = s_d1;
      g_symbol_history_map[sym_idx].h4_synced = s_h4;
      g_symbol_history_map[sym_idx].h1_synced = s_h1;
      g_symbol_history_map[sym_idx].m15_synced = s_m15;

      bool full_met = (b_w1 >= req_w1 && b_d1 >= req_d1 && b_h4 >= req_h4 && b_h1 >= req_h1 && b_m15 >= req_m15);
      bool floor_met = (b_w1 >= f_w1 && b_d1 >= f_d1 && b_h4 >= f_h4 && b_h1 >= f_h1 && b_m15 >= f_m15);

      if(full_met)
      {
         pass[2] = true;
         g_symbol_history_map[sym_idx].state = SYM_STATE_READY;
         items[2] = StringFormat("History loaded: PASS (W1: %d/%d, D1: %d/%d, H4: %d/%d, H1: %d/%d, M15: %d/%d | Floor: %d/%d/%d/%d/%d, Sync: ALL_YES)",
                                 b_w1, req_w1, b_d1, req_d1, b_h4, req_h4, b_h1, req_h1, b_m15, req_m15,
                                 f_w1, f_d1, f_h4, f_h1, f_m15);
      }
      else if(floor_met)
      {
         // Below target minimum but above hard floor! Check user choice
         ENUM_SHORT_HISTORY_ACTION choice = GetPersistedChoice(_Symbol);
         if(choice == SHORT_HIST_REDUCE)
         {
            pass[2] = true;
            g_symbol_history_map[sym_idx].state = SYM_STATE_READY_REDUCED;
            items[2] = StringFormat("History loaded: READY (REDUCED HISTORY) (W1: %d/%d, D1: %d/%d, H4: %d/%d, H1: %d/%d, M15: %d/%d | Floor: %d/%d/%d/%d/%d)",
                                    b_w1, req_w1, b_d1, req_d1, b_h4, req_h4, b_h1, req_h1, b_m15, req_m15,
                                    f_w1, f_d1, f_h4, f_h1, f_m15);
         }
         else if(choice == SHORT_HIST_BLOCK)
         {
            pass[2] = false;
            all_pass = false;
            g_symbol_history_map[sym_idx].state = SYM_STATE_BLOCKED;
            items[2] = StringFormat("History loaded: BLOCKED (User Choice /blocksymbol) (W1: %d/%d, D1: %d/%d, H4: %d/%d, H1: %d/%d, M15: %d/%d)",
                                    b_w1, req_w1, b_d1, req_d1, b_h4, req_h4, b_h1, req_h1, b_m15, req_m15);
         }
         else // SHORT_HIST_ASK
         {
            pass[2] = false;
            all_pass = false;
            g_symbol_history_map[sym_idx].state = SYM_STATE_PENDING_USER;
            items[2] = StringFormat("History loaded: PENDING USER CHOICE (W1: %d/%d, D1: %d/%d, H4: %d/%d, H1: %d/%d, M15: %d/%d | Hard Floor Met: %d/%d/%d/%d/%d)",
                                    b_w1, req_w1, b_d1, req_d1, b_h4, req_h4, b_h1, req_h1, b_m15, req_m15,
                                    f_w1, f_d1, f_h4, f_h1, f_m15);
            // Send one Telegram prompt if not alerted recently
            string prompt_msg = "";
            if(CheckAlertGate(_Symbol + "_SHORT_HIST_ASK", "WARNING", items[2], prompt_msg))
            {
               string tg_ask = StringFormat(
                  "⚠️ <b>SHORT HISTORY DETECTED FOR %s</b>\n\n"
                  "Available vs Required:\n"
                  "• W1:  <code>%d/%d bars</code> (Floor: %d)\n"
                  "• D1:  <code>%d/%d bars</code> (Floor: %d)\n"
                  "• H4:  <code>%d/%d bars</code> (Floor: %d)\n"
                  "• H1:  <code>%d/%d bars</code> (Floor: %d)\n"
                  "• M15: <code>%d/%d bars</code> (Floor: %d)\n\n"
                  "<i>Hard floors are fully met. Select an action:</i>\n"
                  "• <code>/allowreduced %s</code> - Continue with available bars (flagged in journal)\n"
                  "• <code>/blocksymbol %s</code> - Block trading on this symbol only",
                  _Symbol, b_w1, req_w1, f_w1, b_d1, req_d1, f_d1, b_h4, req_h4, f_h4,
                  b_h1, req_h1, f_h1, b_m15, req_m15, f_m15, _Symbol, _Symbol
               );
               SendTelegramFallbackAlert(tg_ask);
            }
         }
      }
      else
      {
         // Below hard floor! Always blocked
         pass[2] = false;
         all_pass = false;
         g_symbol_history_map[sym_idx].state = SYM_STATE_BLOCKED;
         items[2] = StringFormat("History loaded: BLOCKED (Below Hard Floor) (W1: %d/%d [floor %d], D1: %d/%d [floor %d], H4: %d/%d [floor %d], H1: %d/%d [floor %d], M15: %d/%d [floor %d])",
                                 b_w1, req_w1, f_w1, b_d1, req_d1, f_d1, b_h4, req_h4, f_h4, b_h1, req_h1, f_h1, b_m15, req_m15, f_m15);
      }
   }

   // 4. Broker UTC Offset Detected
   g_broker_utc_offset = (int)(TimeTradeServer() - TimeGMT());
   pass[3] = true;
   items[3] = StringFormat("Broker UTC offset detected: %s (Offset: %s)", "PASS", FormatBrokerOffset(g_broker_utc_offset));

   // Ensure indicator handles are initialized
   if(g_atr_handle_m5 == INVALID_HANDLE)     g_atr_handle_m5     = iATR(_Symbol, PERIOD_M5, InpATRPeriod);
   if(g_atr_handle_h1 == INVALID_HANDLE)     g_atr_handle_h1     = iATR(_Symbol, PERIOD_H1, InpATRPeriod);
   if(g_adx_handle_m5 == INVALID_HANDLE)     g_adx_handle_m5     = iADX(_Symbol, PERIOD_M5, InpATRPeriod);
   if(g_spread_atr_handle == INVALID_HANDLE) g_spread_atr_handle = iATR(_Symbol, InpSpreadATRTimeframe, InpSpreadATRPeriod);

   // 5. Spread/ATR Readable with explicit units
   double pt = SymbolInfoDouble(_Symbol, SYMBOL_POINT);
   if(pt <= 0.0) pt = 0.01;
   int cur_spread = (int)SymbolInfoInteger(_Symbol, SYMBOL_SPREAD);
   double atr_test[];
   ArraySetAsSeries(atr_test, true);
   int c_atr = CopyBuffer(g_atr_handle_m5, 0, 0, 1, atr_test);
   if(c_atr <= 0) c_atr = CopyBuffer(g_atr_handle_m5, 0, 1, 1, atr_test);
   bool atr_read = (g_atr_handle_m5 != INVALID_HANDLE && c_atr > 0 && atr_test[0] > 0.0);
   pass[4] = (cur_spread >= 0 && atr_read);
   items[4] = StringFormat("Spread/ATR readable: %s (Spread: %s, ATR_M5: %s)",
                           pass[4] ? "PASS" : "FAIL",
                           FormatSpreadAndPrice(_Symbol, cur_spread),
                           atr_read ? FormatPtsAndPrice(_Symbol, atr_test[0]/pt) : "0.00 pts ($0.00)");
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
   if(!GlobalVariableCheck(lock_key)) GlobalVariableSet(lock_key, (double)TimeCurrent());
   pass[6] = GlobalVariableCheck(lock_key);
   items[6] = StringFormat("Single-instance lock held: %s (Key: %s)", pass[6] ? "PASS" : "FAIL", lock_key);
   if(!pass[6]) all_pass = false;

   // Print Checklist: Item 3/7 and all items use [INFO] so level filters NEVER hide any failing item!
   if(is_full_print)
   {
      LogEvent("INFO", _Symbol, EnumToString(InpSignalTimeframe),
               StringFormat("=================== STARTUP WARM-UP CHECKLIST (Round %d/%d) ===================", round_num, InpMaxStartupRounds));
      for(int i = 0; i < 7; i++)
      {
         LogEvent("INFO", _Symbol, EnumToString(InpSignalTimeframe), StringFormat("Checklist Item [%d/7] %s", i+1, items[i]));
         g_last_printed_items[i] = items[i];
      }
      LogEvent("INFO", _Symbol, EnumToString(InpSignalTimeframe),
               StringFormat("Checklist Status: %s (Round %d/%d)", all_pass ? "ALL PASS" : "PENDING / RETRYING", round_num, InpMaxStartupRounds));
   }
   else
   {
      // Intermediate retry rounds: print only items that are failing or changed!
      bool printed_header = false;
      for(int i = 0; i < 7; i++)
      {
         if(!pass[i] || items[i] != g_last_printed_items[i])
         {
            if(!printed_header)
            {
               LogEvent("INFO", _Symbol, EnumToString(InpSignalTimeframe),
                        StringFormat("--- STARTUP RETRY (Round %d/%d) ---", round_num, InpMaxStartupRounds));
               printed_header = true;
            }
            LogEvent("INFO", _Symbol, EnumToString(InpSignalTimeframe), StringFormat("Checklist Item [%d/7] %s", i+1, items[i]));
            g_last_printed_items[i] = items[i];
         }
      }
   }

   // Central Alert Gate for failing items
   for(int i = 0; i < 7; i++)
   {
      string gate_key = StringFormat("%s_Check_%d", _Symbol, i+1);
      string alert_msg = "";
      if(!pass[i])
      {
         // A failing startup check that is still retrying is WARNING. Only the final circuit breaker is CRITICAL.
         string severity = (round_num >= InpMaxStartupRounds) ? "CRITICAL" : "WARNING";
         if(CheckAlertGate(gate_key, severity, items[i], alert_msg))
         {
            LogEvent(severity, _Symbol, EnumToString(InpSignalTimeframe), alert_msg);
         }
      }
      else
      {
         // If passed, emit RESOLVED if it had previously failed
         if(CheckAlertGate(gate_key, "PASS", items[i], alert_msg))
         {
            LogEvent("INFO", _Symbol, EnumToString(InpSignalTimeframe), alert_msg);
         }
      }
   }

   out_summary = StringFormat("Checklist Round %d: %s", round_num, all_pass ? "PASSED" : "PENDING");
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
   // Verify symbol is in ready state
   ENUM_SYMBOL_STATE sym_state = GetSymbolState(_Symbol);
   if(sym_state != SYM_STATE_READY && sym_state != SYM_STATE_READY_REDUCED)
   {
      LogEvent("INFO", _Symbol, EnumToString(InpSignalTimeframe),
               StringFormat("Evaluation skipped: Symbol state is %s",
                            sym_state == SYM_STATE_BLOCKED ? "BLOCKED" : "PENDING USER CHOICE"));
      return;
   }

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

   LogEvent("DECISION", _Symbol, EnumToString(InpSignalTimeframe),
            StringFormat("Bar closed @ %.5f | ATR_M5=%s, ATR_H1=%s, ADX_M5=%.1f, Spread=%s | Trends: M15=%s, H1=%s, H4=%s%s",
                         closed_price, FormatPtsAndPrice(_Symbol, atr_m5_pts), FormatPtsAndPrice(_Symbol, atr_h1_pts),
                         adx_m5_val, FormatSpreadAndPrice(_Symbol, cur_spread_pts), trend_m15, trend_h1, trend_h4,
                         sym_state == SYM_STATE_READY_REDUCED ? " [REDUCED_HIST]" : ""));

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

//+------------------------------------------------------------------+
//| Startup Warm-Up Checklist & Timing Circuit Breaker               |
//+------------------------------------------------------------------+
void CheckBarTimingAndEvaluate()
{
   ulong now_tick = GetTickCount64();

   // 1. Startup Warm-up checklist with backoff schedule (5s, 10s, 20s, 30s, 30s, 30s)
   if(!g_warmup_checklist_passed && !g_startup_circuit_breaker)
   {
      if(now_tick < g_next_checklist_tick) return; // Honor wait gap strictly!

      if(!EnsureConnection())
      {
         // Wait 5 seconds and retry bridge connection
         g_next_checklist_tick = now_tick + 5000;
         return;
      }

      g_startup_round++;
      bool is_first_round = (g_startup_round == 1);
      bool is_max_round   = (g_startup_round >= InpMaxStartupRounds);
      string round_summary = "";

      // Print full checklist on round 1 and final round
      bool full_print = is_first_round || is_max_round;
      bool passed = RunStartupChecklistRound(g_startup_round, full_print, round_summary);

      if(passed)
      {
         g_warmup_checklist_passed = true;
         if(!is_first_round)
         {
            // Print full checklist once at the end
            string dummy_sum;
            RunStartupChecklistRound(g_startup_round, true, dummy_sum);
         }
         LogEvent("INFO", _Symbol, EnumToString(InpSignalTimeframe), "WARM-UP CHECKLIST PASSED: All 7 items verified.");
      }
      else
      {
         if(is_max_round)
         {
            // Circuit Breaker triggered after InpMaxStartupRounds rounds!
            g_startup_circuit_breaker = true;
            ENUM_SYMBOL_STATE final_state = GetSymbolState(_Symbol);
            string final_state_str = (final_state == SYM_STATE_PENDING_USER ? "PENDING USER CHOICE" : "BLOCKED");

            LogEvent("CRITICAL", _Symbol, "STARTUP",
                     StringFormat("STARTUP CIRCUIT BREAKER TRIGGERED: Completed %d startup rounds without passing. Symbol %s state: %s. Startup retries halted.",
                                  InpMaxStartupRounds, _Symbol, final_state_str));

            if(!g_startup_summary_sent)
            {
               string tg_sum = StringFormat(
                  "⛔ <b>STARTUP CIRCUIT BREAKER</b>\n"
                  "Symbol: <code>%s</code>\n"
                  "Rounds completed: <code>%d/%d</code>\n"
                  "Final Symbol State: <b>%s</b>\n"
                  "Trading logic remains dormant until requirements are satisfied.",
                  _Symbol, InpMaxStartupRounds, InpMaxStartupRounds, final_state_str
               );
               SendTelegramFallbackAlert(tg_sum);
               g_startup_summary_sent = true;
            }
         }
         else
         {
            // Schedule next round with growing wait gaps: 5, 10, 20, 30, 30, 30s
            int wait_sec = g_round_wait_sec[g_startup_round - 1];
            g_next_checklist_tick = now_tick + ((ulong)wait_sec * 1000);
            LogEvent("INFO", _Symbol, EnumToString(InpSignalTimeframe),
                     StringFormat("Checklist retry scheduled: Round %d/%d awaiting %d seconds backoff gap.",
                                  g_startup_round + 1, InpMaxStartupRounds, wait_sec));
         }
      }
      return;
   }

   // 2. Indicator Warm-Up gating
   if(!g_warmup_complete && g_warmup_checklist_passed)
   {
      static ulong last_ind_tick = 0;
      if(now_tick - last_ind_tick < 10000) return;
      last_ind_tick = now_tick;

      if(CheckIndicatorWarmup())
      {
         g_warmup_complete = true;
         string msg = "WARM-UP COMPLETE: trading logic active";
         LogEvent("INFO", _Symbol, EnumToString(InpSignalTimeframe), msg);
         SendTelegramFallbackAlert(StringFormat("🚀 <b>%s</b>\n\nAll indicators warmed up (%d closed bars). Real-time signal evaluation active.", msg, InpWarmupBars));
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
      // Log countdown ONLY at the start of a new bar and once when 1 minute is left!
      int tf_minutes = tf_sec / 60;
      bool is_start_of_bar = (min_left == tf_minutes);
      bool is_one_min_left = (min_left == 1);

      if((is_start_of_bar || is_one_min_left) && min_left != g_last_countdown_logged_minute)
      {
         g_last_countdown_logged_minute = min_left;
         LogEvent("INFO", _Symbol, EnumToString(InpSignalTimeframe),
                  StringFormat("Waiting for bar close, %d minutes left", min_left));
      }
   }
}
//+------------------------------------------------------------------+
//| Expert initialization function                                   |
//+------------------------------------------------------------------+
int OnInit()
{
   PrintFormat("[PythonBridgeEA] [INIT] Initializing Bridge v3.10 on %s | Server %s:%d | Magic %d",
               _Symbol, InpServerHost, InpServerPort, InpMagicNumber);

   // 1. Single Instance Guard (GlobalVariable lock keyed by magic number and terminal login)
   string lock_key = StringFormat("SMC_LOCK_%d_%d", InpMagicNumber, (int)AccountInfoInteger(ACCOUNT_LOGIN));
   if(GlobalVariableCheck(lock_key))
   {
      datetime last_active = (datetime)GlobalVariableGet(lock_key);
      datetime now_cur = TimeCurrent();
      if(now_cur > 0 && now_cur >= last_active && (now_cur - last_active < 10))
      {
         PrintFormat("[PythonBridgeEA] [FATAL] Duplicate instance detected for Magic %d on account %d! Another chart is actively running this EA. Initialization aborted.",
                     InpMagicNumber, (int)AccountInfoInteger(ACCOUNT_LOGIN));
         return INIT_FAILED;
      }
   }
   GlobalVariableSet(lock_key, (double)TimeCurrent());

   // 2. Broker UTC Offset Detection
   g_broker_utc_offset = (int)(TimeTradeServer() - TimeGMT());

   // 3. Account Mode & Profile Identification
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
   g_last_heartbeat_time = TimeCurrent();

   // Clear deduplication cache
   for(int i = 0; i < MAX_REQ_CACHE; i++)
   {
      g_processed_req_ids[i] = "";
      g_processed_req_results[i] = "";
   }
   g_cache_index = 0;

   // 6. Initial socket connect
   g_reconnect_backoff_sec = 1;
   g_next_reconnect_time = 0;
   EnsureConnection();

   // High-precision 1-second timer for sockets & trade lifecycle
   EventSetTimer(1);
   DrawWeeklyDailyLevels();

   return INIT_SUCCEEDED;
}

//+------------------------------------------------------------------+
//| Expert deinitialization function                                 |
//+------------------------------------------------------------------+
void OnDeinit(const int reason)
{
   EventKillTimer();
   DisconnectSocket();

   // Release single-instance lock
   string lock_key = StringFormat("SMC_LOCK_%d_%d", InpMagicNumber, (int)AccountInfoInteger(ACCOUNT_LOGIN));
   GlobalVariableDel(lock_key);

   if(g_atr_handle_m5 != INVALID_HANDLE) IndicatorRelease(g_atr_handle_m5);
   if(g_atr_handle_h1 != INVALID_HANDLE) IndicatorRelease(g_atr_handle_h1);
   if(g_adx_handle_m5 != INVALID_HANDLE) IndicatorRelease(g_adx_handle_m5);
   if(g_spread_atr_handle != INVALID_HANDLE) IndicatorRelease(g_spread_atr_handle);
   DeleteAllChartObjects();
   PrintFormat("[PythonBridgeEA] [DEINIT] Bridge Deinitialized cleanly. Reason: %d (Lock released)", reason);
}

//+------------------------------------------------------------------+
//| Timer event function (bridge polling + lifecycle management)     |
//+------------------------------------------------------------------+
void OnTimer()
{
   // Update single-instance heartbeat
   string lock_key = StringFormat("SMC_LOCK_%d_%d", InpMagicNumber, (int)AccountInfoInteger(ACCOUNT_LOGIN));
   GlobalVariableSet(lock_key, (double)TimeCurrent());

   // 1. Midnight / Configurable Reset for Daily Risk & Challenge Tracking
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
         }
      }
   }

   // 2. Watchdog Heartbeat Check
   if(TimeCurrent() - g_last_heartbeat_time > (datetime)InpWatchdogTimeoutSec)
   {
      g_watchdog_halted = true;
   }
   else
   {
      g_watchdog_halted = false;
   }

   // 3. Socket communication
   if(EnsureConnection())
   {
      ProcessIncomingData();
   }

   // 4. Autonomous Open Position Lifecycle Management (BE, Trailing SL, Time-stop)
   ManageActivePositions();

   // 4b. Scan Open Positions Risk (oversized position audit)
   AuditOpenPositionsRisk();

   // 4c. Periodic Heartbeat Status (throttled to InpHeartbeatSec)
   static datetime last_heartbeat_status_time = 0;
   if(TimeCurrent() - last_heartbeat_status_time >= (datetime)InpHeartbeatSec)
   {
      last_heartbeat_status_time = TimeCurrent();
      LogEvent("INFO", _Symbol, "HEARTBEAT",
               StringFormat("Heartbeat: Terminal online, bridge connected, active positions: %d, spread: %d pts",
                            PositionsTotal(), (int)SymbolInfoInteger(_Symbol, SYMBOL_SPREAD)));
   }

   // 5. Chart Visualization & Dashboard
   DrawWeeklyDailyLevels();
   UpdateDashboardVisuals();

   // 6. Warm-Up, Bar-Close Evaluation & Countdown
   CheckBarTimingAndEvaluate();

   // 7. Flush Fallback Feed (if native Telegram active and queue has lines)
   FlushFallbackFeed();
}

void OnTick()
{
   if(g_socket != INVALID_HANDLE && SocketIsConnected(g_socket))
   {
      ProcessIncomingData();
   }
   ManageActivePositions();
   CheckBarTimingAndEvaluate();
}

//+------------------------------------------------------------------+
//| Ensures native socket connection to Python TCP Bridge Server     |
//| Implements Exponential Backoff (1s up to 30s) with Jitter        |
//+------------------------------------------------------------------+
bool EnsureConnection()
{
   if(g_socket != INVALID_HANDLE && SocketIsConnected(g_socket))
   {
      return true;
   }

   datetime now = TimeCurrent();
   if(now < g_next_reconnect_time)
   {
      return false;
   }

   DisconnectSocket();

   g_socket = SocketCreate();
   if(g_socket == INVALID_HANDLE)
   {
      int err = GetLastError();
      PrintFormat("[PythonBridgeEA] [ERROR] SocketCreate failed: %d", err);
      g_next_reconnect_time = now + 5;
      return false;
   }

   SocketTimeouts(g_socket, InpTimeoutMs, InpTimeoutMs);

   if(!SocketConnect(g_socket, InpServerHost, (uint)InpServerPort, InpTimeoutMs))
   {
      int err = GetLastError();
      g_reconnect_backoff_sec = MathMin(30, (g_reconnect_backoff_sec <= 0 ? 1 : g_reconnect_backoff_sec * 2));
      int jitter = MathRand() % 3;
      int delay = g_reconnect_backoff_sec + jitter;
      g_next_reconnect_time = now + delay;

      if(now - g_last_logged_failure >= 10)
      {
         string err_desc = (err == 5272 ? "ERR_NETSOCKET_CANNOT_CONNECT" :
                           (err == 5273 ? "ERR_NETSOCKET_IO_ERROR" : "SOCKET_CONNECT_FAILED"));
         PrintFormat("[PythonBridgeEA] [DISCONNECTED] Connect to %s:%d failed (Error %d: %s). Next retry in %ds.",
                     InpServerHost, InpServerPort, err, err_desc, delay);
         g_last_logged_failure = now;
      }
      DisconnectSocket();
      return false;
   }

   // Connected successfully
   g_reconnect_backoff_sec = 1;
   g_next_reconnect_time = 0;
   PrintFormat("[PythonBridgeEA] [CONNECTED] Successfully connected to Python Bridge Server on %s:%d",
               InpServerHost, InpServerPort);
   g_last_heartbeat_time = now;
   g_watchdog_halted = false;

   // Send initial identification handshake
   string handshake = StringFormat("{\"client\":\"PythonBridgeEA\",\"symbol\":\"%s\",\"account\":%d,\"magic\":%d,\"broker_utc_offset\":%d}\n",
                                   _Symbol, (int)AccountInfoInteger(ACCOUNT_LOGIN), InpMagicNumber, g_broker_utc_offset);
   uchar send_buffer[];
   StringToCharArray(handshake, send_buffer, 0, StringLen(handshake));
   SocketSend(g_socket, send_buffer, ArraySize(send_buffer));

   g_rx_buffer = "";
   return true;
}

//+------------------------------------------------------------------+
//| Safely closes socket and resets state                            |
//+------------------------------------------------------------------+
void DisconnectSocket()
{
   if(g_socket != INVALID_HANDLE)
   {
      SocketClose(g_socket);
      g_socket = INVALID_HANDLE;
   }
   g_rx_buffer = "";
}

//+------------------------------------------------------------------+
//| Reads incoming data from socket and processes newline-framed JSON|
//| Properly distinguishes idle state (readable==0) from errors      |
//+------------------------------------------------------------------+
void ProcessIncomingData()
{
   if(g_socket == INVALID_HANDLE || !SocketIsConnected(g_socket))
      return;

   uint readable = SocketIsReadable(g_socket);
   if(readable == 0)
   {
      // Idle socket: no data waiting. Normal condition, do NOT report error or disconnect!
      return;
   }

   uchar buffer[];
   ArrayResize(buffer, (int)readable);
   int bytes_read = SocketRead(g_socket, buffer, (int)readable, InpTimeoutMs);

   if(bytes_read > 0)
   {
      string chunk = CharArrayToString(buffer, 0, bytes_read);
      g_rx_buffer += chunk;

      int newline_pos = -1;
      while((newline_pos = StringFind(g_rx_buffer, "\n")) >= 0)
      {
         string line = StringSubstr(g_rx_buffer, 0, newline_pos);
         g_rx_buffer = StringSubstr(g_rx_buffer, newline_pos + 1);

         StringTrimLeft(line);
         StringTrimRight(line);
         if(StringLen(line) > 0)
         {
            HandleMessage(line);
         }
      }
   }
   else if(bytes_read < 0)
   {
      int err = GetLastError();
      string err_desc = (err == 5273 ? "ERR_NETSOCKET_IO_ERROR: Peer connection closed/reset" :
                        (err == 5272 ? "ERR_NETSOCKET_CANNOT_CONNECT" : "SOCKET_READ_ERROR"));
      PrintFormat("[PythonBridgeEA] [ERROR] SocketRead error %d (%s). Resetting connection.", err, err_desc);
      DisconnectSocket();
   }
}

//+------------------------------------------------------------------+
//| Handles a single complete JSON command line and sends response   |
//+------------------------------------------------------------------+
void HandleMessage(const string message)
{
   PrintFormat("[PythonBridgeEA] [REQUEST] %s", message);
   g_last_heartbeat_time = TimeCurrent();
   g_watchdog_halted = false;

   string response = ProcessCommand(message);
   response += "\n";

   PrintFormat("[PythonBridgeEA] [RESPONSE] %s", StringSubstr(response, 0, StringLen(response) - 1));

   uchar send_buffer[];
   StringToCharArray(response, send_buffer, 0, StringLen(response));
   int sent = SocketSend(g_socket, send_buffer, ArraySize(send_buffer));
   if(sent <= 0)
   {
      int err = GetLastError();
      PrintFormat("[PythonBridgeEA] [ERROR] SocketSend failed: %d (%s)",
                  err, (err == 5273 ? "ERR_NETSOCKET_IO_ERROR" : "SEND_ERROR"));
      DisconnectSocket();
   }
}

//+------------------------------------------------------------------+
//| Command Processor: PING, ACCOUNT, POSITIONS, RATES, TRADE, etc.  |
//+------------------------------------------------------------------+
string ProcessCommand(const string json)
{
   string action = ExtractJsonString(json, "action", "");
   string req_id = ExtractJsonString(json, "request_id", "");

   // 1. PING (includes server_time, utc_time, and broker_utc_offset)
   if(action == "PING")
   {
      datetime server_time = TimeTradeServer();
      datetime utc_time    = TimeGMT();
      return StringFormat("{\"status\":\"SUCCESS\",\"request_id\":\"%s\",\"message\":\"PONG\",\"server_time\":%d,\"utc_time\":%d,\"broker_utc_offset\":%d}",
                          req_id, (int)server_time, (int)utc_time, g_broker_utc_offset);
   }


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

      return StringFormat("{\"status\":\"SUCCESS\",\"request_id\":\"%s\",\"message\":\"Signal alert processed\"}", req_id);
   }

   // 1e. TEST_LOG (Inject test log event)
   if(action == "TEST_LOG")
   {
      string lvl = ExtractJsonString(json, "level", "INFO");
      string lmsg = ExtractJsonString(json, "message", "Test log event");
      string sym = ExtractJsonString(json, "symbol", _Symbol);
      LogEvent(lvl, sym, EnumToString(InpSignalTimeframe), lmsg);
      return StringFormat("{\"status\":\"SUCCESS\",\"request_id\":\"%s\",\"message\":\"Log event injected\"}", req_id);
   }

   // 1f. SET_SYMBOL_CHOICE (/allowreduced, /blocksymbol, /resetsymbol)
   if(action == "SET_SYMBOL_CHOICE")
   {
      string sym = ExtractJsonString(json, "symbol", _Symbol);
      string choice = ExtractJsonString(json, "choice", "ASK");
      int s_idx = GetSymbolRecordIdx(sym);

      if(choice == "REDUCE")
      {
         // Verify hard floors are satisfied before allowing reduction!
         int f_w1 = GetHardFloorBars(PERIOD_W1);
         int f_d1 = GetHardFloorBars(PERIOD_D1);
         int f_h4 = GetHardFloorBars(PERIOD_H4);
         int f_h1 = GetHardFloorBars(PERIOD_H1);
         int f_m15 = GetHardFloorBars(PERIOD_M15);

         int b_w1 = g_symbol_history_map[s_idx].w1_bars;
         int b_d1 = g_symbol_history_map[s_idx].d1_bars;
         int b_h4 = g_symbol_history_map[s_idx].h4_bars;
         int b_h1 = g_symbol_history_map[s_idx].h1_bars;
         int b_m15 = g_symbol_history_map[s_idx].m15_bars;

         bool floor_met = (b_w1 >= f_w1 && b_d1 >= f_d1 && b_h4 >= f_h4 && b_h1 >= f_h1 && b_m15 >= f_m15);
         if(floor_met)
         {
            PersistChoice(sym, SHORT_HIST_REDUCE);
            g_symbol_history_map[s_idx].state = SYM_STATE_READY_REDUCED;
            g_warmup_checklist_passed = true;
            LogEvent("INFO", sym, "STARTUP", StringFormat("User choice REDUCE applied for %s. State: READY (REDUCED HISTORY)", sym));
            return StringFormat("{\"status\":\"SUCCESS\",\"symbol\":\"%s\",\"state\":\"READY_REDUCED\",\"message\":\"Reduced history enabled\"}", sym);
         }
         else
         {
            // Below hard floor - choice cannot bypass hard floor!
            g_symbol_history_map[s_idx].state = SYM_STATE_BLOCKED;
            LogEvent("CRITICAL", sym, "STARTUP", StringFormat("Cannot allow reduced history for %s: Bar count is below unlowered hard floor!", sym));
            return StringFormat("{\"status\":\"ERROR\",\"symbol\":\"%s\",\"message\":\"History below unlowered hard floor; remains BLOCKED\"}", sym);
         }
      }
      else if(choice == "BLOCK")
      {
         PersistChoice(sym, SHORT_HIST_BLOCK);
         g_symbol_history_map[s_idx].state = SYM_STATE_BLOCKED;
         LogEvent("INFO", sym, "STARTUP", StringFormat("User choice BLOCK applied for %s. State: BLOCKED", sym));
         return StringFormat("{\"status\":\"SUCCESS\",\"symbol\":\"%s\",\"state\":\"BLOCKED\",\"message\":\"Symbol blocked by user choice\"}", sym);
      }
      else if(choice == "RESET")
      {
         ResetPersistedChoice(sym);
         g_symbol_history_map[s_idx].state = SYM_STATE_CHECKING;
         g_startup_round = 0;
         g_startup_circuit_breaker = false;
         g_warmup_checklist_passed = false;
         g_next_checklist_tick = 0;
         LogEvent("INFO", sym, "STARTUP", StringFormat("User choice RESET for %s. Reverting to checklist evaluation.", sym));
         return StringFormat("{\"status\":\"SUCCESS\",\"symbol\":\"%s\",\"state\":\"CHECKING\",\"message\":\"Choice reset\"}", sym);
      }
   }

   // 1g. GET_HISTORY_STATUS
   if(action == "GET_HISTORY_STATUS")
   {
      string sym = ExtractJsonString(json, "symbol", _Symbol);
      int s_idx = GetSymbolRecordIdx(sym);
      ENUM_SYMBOL_STATE st = g_symbol_history_map[s_idx].state;
      string st_name = (st == SYM_STATE_READY ? "READY" :
                       (st == SYM_STATE_READY_REDUCED ? "READY (REDUCED HISTORY)" :
                       (st == SYM_STATE_PENDING_USER ? "PENDING USER CHOICE" :
                       (st == SYM_STATE_BLOCKED ? "BLOCKED" : "CHECKING"))));

      return StringFormat(
         "{\"status\":\"SUCCESS\",\"symbol\":\"%s\",\"state\":\"%s\",\"rounds_used\":%d,\"w1\":%d,\"d1\":%d,\"h4\":%d,\"h1\":%d,\"m15\":%d}",
         sym, st_name, g_symbol_history_map[s_idx].rounds_used,
         g_symbol_history_map[s_idx].w1_bars, g_symbol_history_map[s_idx].d1_bars,
         g_symbol_history_map[s_idx].h4_bars, g_symbol_history_map[s_idx].h1_bars,
         g_symbol_history_map[s_idx].m15_bars
      );
   }

   // 1b. ENABLE / AUTHORIZE TRADING
   if(action == "ENABLE")
   {
      g_trading_enabled = true;
      string enabled_key = StringFormat("SMC_ENABLED_%d_%d", InpMagicNumber, (int)AccountInfoInteger(ACCOUNT_LOGIN));
      GlobalVariableSet(enabled_key, 1.0);
      Print("[PythonBridgeEA] [COMMAND] Trading execution ENABLED.");
      return StringFormat("{\"status\":\"SUCCESS\",\"request_id\":\"%s\",\"message\":\"Trading enabled\",\"trading_enabled\":true}", req_id);
   }

   // 1c. DISABLE TRADING
   if(action == "DISABLE")
   {
      g_trading_enabled = false;
      string enabled_key = StringFormat("SMC_ENABLED_%d_%d", InpMagicNumber, (int)AccountInfoInteger(ACCOUNT_LOGIN));
      GlobalVariableSet(enabled_key, 0.0);
      Print("[PythonBridgeEA] [COMMAND] Trading execution DISABLED.");
      return StringFormat("{\"status\":\"SUCCESS\",\"request_id\":\"%s\",\"message\":\"Trading disabled\",\"trading_enabled\":false}", req_id);
   }

   // 2b. STATUS (Full symbol specifications, ATR sizing, open risk breakdown)
   if(action == "STATUS" || action == "SYMBOLS_STATUS")
   {
      return FormatSymbolStatusJson(req_id);
   }

   // 2c. CALC_PROFIT
   if(action == "CALC_PROFIT")
   {
      string sym = ExtractJsonString(json, "symbol", _Symbol);
      string type_str = ExtractJsonString(json, "type", "BUY");
      double vol = ExtractJsonDouble(json, "volume", 0.01);
      double p_open = ExtractJsonDouble(json, "price_open", 0.0);
      double p_close = ExtractJsonDouble(json, "price_close", 0.0);
      ENUM_ORDER_TYPE o_type = (type_str == "BUY" ? ORDER_TYPE_BUY : ORDER_TYPE_SELL);
      double profit = 0.0;
      bool ok = OrderCalcProfit(o_type, sym, vol, p_open, p_close, profit);
      return StringFormat("{\"status\":\"%s\",\"request_id\":\"%s\",\"symbol\":\"%s\",\"profit\":%.4f}",
                          ok ? "SUCCESS" : "ERROR", req_id, sym, profit);
   }

   // 2d. CALC_MARGIN
   if(action == "CALC_MARGIN")
   {
      string sym = ExtractJsonString(json, "symbol", _Symbol);
      string type_str = ExtractJsonString(json, "type", "BUY");
      double vol = ExtractJsonDouble(json, "volume", 0.01);
      double price = ExtractJsonDouble(json, "price", 0.0);
      ENUM_ORDER_TYPE o_type = (type_str == "BUY" ? ORDER_TYPE_BUY : ORDER_TYPE_SELL);
      double margin = 0.0;
      bool ok = OrderCalcMargin(o_type, sym, vol, price, margin);
      return StringFormat("{\"status\":\"%s\",\"request_id\":\"%s\",\"symbol\":\"%s\",\"margin\":%.4f}",
                          ok ? "SUCCESS" : "ERROR", req_id, sym, margin);
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
         "{\"status\":\"SUCCESS\",\"request_id\":\"%s\",\"data\":{\"login\":%d,\"server\":\"%s\",\"trade_mode\":\"%s\",\"profile\":\"%s\",\"trading_enabled\":%s,\"balance\":%.2f,\"equity\":%.2f,\"peak_equity\":%.2f,\"initial_balance\":%.2f,\"daily_loss_pct\":%.2f,\"drawdown_pct\":%.2f,\"remaining_daily_loss\":%.2f,\"remaining_drawdown\":%.2f,\"profit_target_pct\":%.2f,\"trading_days\":%d,\"margin\":%.2f,\"free_margin\":%.2f,\"margin_level\":%.2f,\"currency\":\"%s\",\"leverage\":%d,\"trade_allowed\":%s,\"terminal_connected\":%s,\"broker_utc_offset\":%d,\"server_time\":%d,\"utc_time\":%d}}",
         req_id, log, srv, mode_str, profile_str, (g_trading_enabled ? "true" : "false"),
         bal, eq, g_peak_equity, g_initial_balance, daily_loss_pct, dd_pct,
         rem_daily, rem_dd, target_prog, g_trading_days_count,
         mg, fm, ml, cur, lev,
         trade_allowed ? "true" : "false", connected ? "true" : "false",
         g_broker_utc_offset, (int)TimeTradeServer(), (int)TimeGMT()
      );
   }

   // 3. POSITIONS
   if(action == "POSITIONS")
   {
      string pos_array = "[";
      int total = PositionsTotal();
      bool first = true;

      for(int i = 0; i < total; i++)
      {
         if(g_pos_info.SelectByIndex(i))
         {
            if(!first) pos_array += ",";
            first = false;

            ulong tkt = g_pos_info.Ticket();
            string type_str = (g_pos_info.PositionType() == POSITION_TYPE_BUY) ? "BUY" : "SELL";
            bool be_done = (GlobalVariableGet(StringFormat("SMC_BE_%d", tkt)) > 0);
            bool part_done = (GlobalVariableGet(StringFormat("SMC_PART_%d", tkt)) > 0);
            int  mode_val = (int)GlobalVariableGet(StringFormat("SMC_MODE_%d", tkt));
            string mode_str = (mode_val == (int)MODE_SCALP) ? "SCALP" : ((mode_val == (int)MODE_HOLD) ? "HOLD" : "AUTO");

            pos_array += StringFormat("{\"ticket\":%d,\"symbol\":\"%s\",\"type\":\"%s\",\"volume\":%.2f,\"open_price\":%.5f,\"current_price\":%.5f,\"sl\":%.5f,\"tp\":%.5f,\"profit\":%.2f,\"magic\":%d,\"comment\":\"%s\",\"mode\":\"%s\",\"be_activated\":%s,\"partial_taken\":%s}",
                                      tkt, g_pos_info.Symbol(), type_str, g_pos_info.Volume(),
                                      g_pos_info.PriceOpen(), g_pos_info.PriceCurrent(), g_pos_info.StopLoss(),
                                      g_pos_info.TakeProfit(), g_pos_info.Profit(), g_pos_info.Magic(), g_pos_info.Comment(),
                                      mode_str, be_done ? "true" : "false", part_done ? "true" : "false");
         }
      }
      pos_array += "]";
      return StringFormat("{\"status\":\"SUCCESS\",\"request_id\":\"%s\",\"data\":%s}", req_id, pos_array);
   }

   // 4. RATES (includes time_utc for every candle)
   if(action == "RATES")
   {
      string sym = ExtractJsonString(json, "symbol", _Symbol);
      string tf  = ExtractJsonString(json, "timeframe", "M15");
      int count  = (int)ExtractJsonInteger(json, "count", 100);

      return FormatRatesJson(sym, tf, count, req_id);
   }

   // 5. DAILY_SUMMARY (Reconciled broker deals)
   if(action == "DAILY_SUMMARY")
   {
      datetime today_start = TimeCurrent() / 86400 * 86400;
      HistorySelect(today_start, TimeCurrent());
      int total_deals = HistoryDealsTotal();

      int closed_trades = 0;
      int wins = 0;
      int losses = 0;
      double net_pnl = 0.0;
      double total_swap = 0.0;
      double total_comm = 0.0;

      for(int i = 0; i < total_deals; i++)
      {
         ulong deal_ticket = HistoryDealGetTicket(i);
         if(deal_ticket > 0)
         {
            long magic = HistoryDealGetInteger(deal_ticket, DEAL_MAGIC);
            long entry = HistoryDealGetInteger(deal_ticket, DEAL_ENTRY);
            if(magic == InpMagicNumber && entry == DEAL_ENTRY_OUT)
            {
               closed_trades++;
               double profit = HistoryDealGetDouble(deal_ticket, DEAL_PROFIT);
               double swap   = HistoryDealGetDouble(deal_ticket, DEAL_SWAP);
               double comm   = HistoryDealGetDouble(deal_ticket, DEAL_COMMISSION);
               double trade_net = profit + swap + comm;
               net_pnl += trade_net;
               total_swap += swap;
               total_comm += comm;
               if(trade_net > 0.0) wins++;
               else if(trade_net < 0.0) losses++;
            }
         }
      }

      double cur_balance = AccountInfoDouble(ACCOUNT_BALANCE);
      double cur_equity  = AccountInfoDouble(ACCOUNT_EQUITY);
      double bal_change  = cur_balance - g_day_start_equity;
      int open_pos_count = 0;
      for(int p = 0; p < PositionsTotal(); p++)
      {
         if(g_pos_info.SelectByIndex(p) && g_pos_info.Magic() == InpMagicNumber)
            open_pos_count++;
      }

      string win_rate_str = (closed_trades > 0) ? StringFormat("%.1f%%", ((double)wins / closed_trades) * 100.0) : "N/A";

      return StringFormat(
         "{\"status\":\"SUCCESS\",\"request_id\":\"%s\",\"data\":{\"closed_trades\":%d,\"wins\":%d,\"losses\":%d,\"net_pnl\":%.2f,\"swap\":%.2f,\"commission\":%.2f,\"win_rate\":\"%s\",\"open_positions\":%d,\"balance\":%.2f,\"equity\":%.2f,\"day_start_balance\":%.2f,\"balance_change\":%.2f,\"trades_today\":%d}}",
         req_id, closed_trades, wins, losses, net_pnl, total_swap, total_comm, win_rate_str, open_pos_count, cur_balance, cur_equity, g_day_start_equity, bal_change, g_trades_today
      );
   }

   // 6. KILL_SWITCH
   if(action == "KILL_SWITCH")
   {
      bool close_all = (ExtractJsonString(json, "close_all", "false") == "true");
      g_risk_halted = true;
      g_risk_halt_reason = "Manual Kill Switch Activated";
      Print("[PythonBridgeEA] [KILL_SWITCH] EMERGENCY KILL SWITCH TRIGGERED!");

      int closed_count = 0;
      if(close_all)
      {
         for(int i = PositionsTotal() - 1; i >= 0; i--)
         {
            if(g_pos_info.SelectByIndex(i) && g_pos_info.Magic() == InpMagicNumber)
            {
               if(g_trade.PositionClose(g_pos_info.Ticket()))
                  closed_count++;
            }
         }
      }
      NotifyUser(StringFormat("KILL SWITCH ENGAGED! Positions closed: %d", closed_count));
      return StringFormat("{\"status\":\"SUCCESS\",\"request_id\":\"%s\",\"message\":\"Kill switch active\",\"positions_closed\":%d}", req_id, closed_count);
   }

   // 6b. RESUME / UNPAUSE
   if(action == "RESUME" || action == "UNPAUSE")
   {
      g_risk_halted = false;
      g_risk_halt_reason = "";
      Print("[PythonBridgeEA] [RESUME] Trading resumed by command.");
      return StringFormat("{\"status\":\"SUCCESS\",\"request_id\":\"%s\",\"message\":\"Trading resumed\"}", req_id);
   }

   // 7. TRADE (MARKET, LIMIT, STOP entries)
   if(action == "TRADE")
   {
      // Handle trade_action == "CLOSE" gracefully
      string trade_act = ExtractJsonString(json, "trade_action", "");
      if(trade_act == "CLOSE")
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
                  return StringFormat("{\"status\":\"ERROR\",\"request_id\":\"%s\",\"message\":\"Prop Rule Violation: Position held for only %ds < minimum %ds (Tick scalping prohibited)\"}",
                                      req_id, duration_sec, InpMinTradeDurationSec);
               }
            }
            bool close_res = (vol > 0.0 && vol < g_pos_info.Volume()) ? g_trade.PositionClosePartial(ticket, vol) : g_trade.PositionClose(ticket);
            if(close_res)
            {
               NotifyUser(StringFormat("CLOSED position #%d on %s", ticket, g_pos_info.Symbol()));
               return StringFormat("{\"status\":\"SUCCESS\",\"request_id\":\"%s\",\"ticket\":%d}", req_id, ticket);
            }
            else
            {
               return StringFormat("{\"status\":\"ERROR\",\"request_id\":\"%s\",\"message\":\"Close failed: %s\"}", req_id, g_trade.ResultRetcodeDescription());
            }
         }
         return StringFormat("{\"status\":\"ERROR\",\"request_id\":\"%s\",\"message\":\"Position not found\"}", req_id);
      }
      // Deduplication check
      string cached_resp = "";
      if(req_id != "" && IsDuplicateRequest(req_id, cached_resp))
      {
         PrintFormat("[PythonBridgeEA] [WARN] Duplicate request_id '%s' blocked. Returning cached response.", req_id);
         return cached_resp;
      }

      // Pre-trade gate and safety validation
      string gate_reason = "";
      if(InpAllowedAccount > 0 && AccountInfoInteger(ACCOUNT_LOGIN) != InpAllowedAccount)
      {
         string err = StringFormat("{\"status\":\"ERROR\",\"message\":\"Account %d does not match InpAllowedAccount %d\"}",
                                   AccountInfoInteger(ACCOUNT_LOGIN), InpAllowedAccount);
         if(req_id != "") CacheRequestResult(req_id, err);
         return err;
      }

      if(!g_trading_enabled)
      {
         string err = StringFormat("{\"status\":\"ERROR\",\"message\":\"Trading execution is disabled for %s profile. Authorize via /enable or inputs.\"}",
                                   (InpAccountProfile == PROFILE_DEMO ? "DEMO" : (InpAccountProfile == PROFILE_LIVE ? "LIVE" : "PROP")));
         if(req_id != "") CacheRequestResult(req_id, err);
         return err;
      }

      // Check Watchdog and Hard Circuit Breakers
      if(g_watchdog_halted)
      {
         string err = "{\"status\":\"ERROR\",\"message\":\"Order rejected: Watchdog halted (Bridge lost heartbeat)\"}";
         if(req_id != "") CacheRequestResult(req_id, err);
         return err;
      }

      if(g_risk_halted || InpKillSwitch)
      {
         string err = StringFormat("{\"status\":\"ERROR\",\"message\":\"Order rejected: Risk Halt / Kill Switch active: %s\"}", g_risk_halt_reason);
         if(req_id != "") CacheRequestResult(req_id, err);
         return err;
      }

      // Symbol validation
      string sym = ExtractJsonString(json, "symbol", _Symbol);
      if(!SymbolSelect(sym, true))
      {
         string err = StringFormat("{\"status\":\"ERROR\",\"message\":\"Symbol '%s' cannot be selected in Market Watch\"}", sym);
         if(req_id != "") CacheRequestResult(req_id, err);
         return err;
      }

      ConfigureFillingMode(sym);

      string type_str   = ExtractJsonString(json, "type", "BUY");
      double sl         = ExtractJsonDouble(json, "sl", 0.0);
      double tp         = ExtractJsonDouble(json, "tp", 0.0);
      double price_in   = ExtractJsonDouble(json, "price", 0.0);
      double risk_pct   = ExtractJsonDouble(json, "risk_percent", 0.0);
      double req_volume = ExtractJsonDouble(json, "volume", 0.0);
      long   magic      = ExtractJsonInteger(json, "magic", InpMagicNumber);
      string comment    = ExtractJsonString(json, "comment", "MT5_SMC_BOT");

      // Reference execution price
      double pt = SymbolInfoDouble(sym, SYMBOL_POINT);
      if(pt <= 0.0) pt = 0.01;
      double tick_size = SymbolInfoDouble(sym, SYMBOL_TRADE_TICK_SIZE);
      if(tick_size <= 0.0) tick_size = pt;
      int digits = (int)SymbolInfoInteger(sym, SYMBOL_DIGITS);

      double exec_price = 0.0;
      if(type_str == "BUY") exec_price = SymbolInfoDouble(sym, SYMBOL_ASK);
      else if(type_str == "SELL") exec_price = SymbolInfoDouble(sym, SYMBOL_BID);
      else exec_price = price_in; // Pending orders use their entry price

      // Normalize SL and TP to tick size
      if(sl > 0.0) sl = NormalizeDouble(MathRound(sl / tick_size) * tick_size, digits);
      if(tp > 0.0) tp = NormalizeDouble(MathRound(tp / tick_size) * tick_size, digits);
      if(price_in > 0.0) price_in = NormalizeDouble(MathRound(price_in / tick_size) * tick_size, digits);

      // Task 2: Stop / Target Direction Validation
      string dir_err = "";
      if(!ValidateStopDirections(sym, type_str, exec_price, sl, tp, dir_err))
      {
         PrintFormat("[PythonBridgeEA] [REJECT] Stop direction validation failed: %s", dir_err);
         string err = StringFormat("{\"status\":\"ERROR\",\"message\":\"Stop direction validation failed: %s\"}", dir_err);
         NotifyUser(StringFormat("TRADE REJECTED (%s %s): %s", type_str, sym, dir_err));
         AppendJournal(sym, "N/A", type_str, exec_price, sl, tp, req_volume, 10016, 0, dir_err);
         if(req_id != "") CacheRequestResult(req_id, err);
         return err;
      }

      // Task 5: Adaptive Mode Decision (Measured Indicators)
      string req_mode_str = ExtractJsonString(json, "mode", "AUTO");
      ENUM_STRATEGY_MODE mode = InpStrategyMode;
      string mode_reason = "";
      if(InpStrategyMode == MODE_AUTO)
      {
         if(req_mode_str == "SCALP") { mode = MODE_SCALP; mode_reason = "Manual request override: SCALP"; }
         else if(req_mode_str == "HOLD") { mode = MODE_HOLD; mode_reason = "Manual request override: HOLD"; }
         else mode = ClassifyAdaptiveMode(sym, mode_reason);
      }
      PrintFormat("[PythonBridgeEA] [MODE] Symbol %s -> Selected Mode: %s (Reason: %s)",
                  sym, (mode == MODE_SCALP ? "SCALP" : "HOLD"), mode_reason);

      // Task 1: Dynamic Spread Filter (live Ask-Bid vs % of SL or ATR)
      string spread_err = "";
      if(!ValidateSpreadFilter(sym, exec_price, sl, mode, spread_err))
      {
         PrintFormat("[PythonBridgeEA] [SPREAD_FILTER] %s", spread_err);
         string err = StringFormat("{\"status\":\"SKIPPED\",\"message\":\"TRADE SKIPPED: %s\"}", spread_err);
         NotifyUser(StringFormat("TRADE SKIPPED (%s): %s", sym, spread_err));
         SendTelegramFallbackAlert(StringFormat("🛑 <b>TRADE SKIPPED:</b> %s\n%s", sym, spread_err));
         AppendJournal(sym, (mode == MODE_SCALP ? "SCALP" : "HOLD"), type_str, exec_price, sl, tp, req_volume, 0, 0, spread_err);
         if(req_id != "") CacheRequestResult(req_id, err);
         return err;
      }

      // Task 6: Order Type Mapping & Unified SL/TP Monetary Evaluation
      ENUM_ORDER_TYPE order_type_enum = (type_str == "BUY" ? ORDER_TYPE_BUY :
                                        (type_str == "SELL" ? ORDER_TYPE_SELL :
                                        (type_str == "BUY_LIMIT" ? ORDER_TYPE_BUY_LIMIT :
                                        (type_str == "SELL_LIMIT" ? ORDER_TYPE_SELL_LIMIT :
                                        (type_str == "BUY_STOP" ? ORDER_TYPE_BUY_STOP : ORDER_TYPE_SELL_STOP)))));

      double test_sl_money = 0.0, test_tp_money = 0.0, sl_dist_pts = 0.0, tp_dist_pts = 0.0, setup_rr = 0.0;
      CalculateSLTPMoney(sym, order_type_enum, 1.0, exec_price, sl, tp, test_sl_money, test_tp_money, sl_dist_pts, tp_dist_pts, setup_rr);

      // Enforce Minimum Reward-to-Risk (InpMinRR)
      if(sl > 0.0 && tp > 0.0 && setup_rr < InpMinRR)
      {
         string err = StringFormat("{\"status\":\"REJECTED\",\"message\":\"TRADE REJECTED: RR %.2f below minimum %.2f (SL -$%.2f vs TP +$%.2f)\"}",
                                   setup_rr, InpMinRR, test_sl_money, test_tp_money);
         PrintFormat("[PythonBridgeEA] [RR_CHECK] %s", err);
         NotifyUser(StringFormat("TRADE REJECTED (%s): RR %.2f below minimum %.2f", sym, setup_rr, InpMinRR));
         SendTelegramFallbackAlert(StringFormat("🛑 <b>TRADE REJECTED:</b> %s\nRR %.2f below minimum %.2f (SL -$%.2f vs TP +$%.2f)",
                                                sym, setup_rr, InpMinRR, test_sl_money, test_tp_money));
         AppendJournal(sym, (mode == MODE_SCALP ? "SCALP" : "HOLD"), type_str, exec_price, sl, tp, req_volume, 0, 0,
                       StringFormat("RR %.2f < %.2f", setup_rr, InpMinRR));
         if(req_id != "") CacheRequestResult(req_id, err);
         return err;
      }

      // Volume & Risk Sizing
      double volume = req_volume;
      string sizing_log = "";
      double bal = AccountInfoDouble(ACCOUNT_BALANCE);
      double eq  = AccountInfoDouble(ACCOUNT_EQUITY);
      double cap_base = MathMin(bal, eq);

      if(volume <= 0.0 || risk_pct > 0.0)
      {
         double target_risk = (risk_pct > 0.0) ? risk_pct : InpMaxRiskPercent;
         volume = CalculateLotSize(sym, target_risk, sl_dist_pts, sizing_log);
         if(volume <= 0.0)
         {
            string err = StringFormat("{\"status\":\"SKIPPED\",\"message\":\"TRADE SKIPPED: %s\"}", sizing_log);
            NotifyUser(StringFormat("TRADE SKIPPED (%s): %s", sym, sizing_log));
            SendTelegramFallbackAlert(StringFormat("🛑 <b>TRADE SKIPPED:</b> %s\n%s", sym, sizing_log));
            AppendJournal(sym, (mode == MODE_SCALP ? "SCALP" : "HOLD"), type_str, exec_price, sl, tp, 0.0, 0, 0, sizing_log);
            if(req_id != "") CacheRequestResult(req_id, err);
            return err;
         }
      }
      else
      {
         // Fixed volume supplied: compute its real risk % and reject if above allowed risk cap
         double sl_money = 0.0, tp_money = 0.0, dummy_sl_pts = 0.0, dummy_tp_pts = 0.0, actual_rr = 0.0;
         CalculateSLTPMoney(sym, order_type_enum, volume, exec_price, sl, tp, sl_money, tp_money, dummy_sl_pts, dummy_tp_pts, actual_rr);

         double actual_risk_pct = (cap_base > 0.0) ? (sl_money / cap_base) * 100.0 : 0.0;
         double allowed_cap = InpMaxRiskPercentPerTrade;
         if(GetSymbolState(sym) == SYM_STATE_READY_REDUCED)
            allowed_cap *= InpReducedHistoryRiskFactor;

         PrintFormat("[PythonBridgeEA] [SIZING] Fixed volume %.2f risks $%.2f (%.2f%% of capital $%.2f, Cap: %.2f%%)",
                     volume, sl_money, actual_risk_pct, cap_base, allowed_cap);

         if(actual_risk_pct > allowed_cap)
         {
            string err = StringFormat("{\"status\":\"REJECTED\",\"message\":\"TRADE REJECTED: Fixed volume %.2f risks %.2f%% ($%.2f), exceeding MaxRiskPercentPerTrade (%.2f%%%s)\"}",
                                      volume, actual_risk_pct, sl_money, allowed_cap,
                                      GetSymbolState(sym) == SYM_STATE_READY_REDUCED ? " [REDUCED_HIST]" : "");
            NotifyUser(StringFormat("TRADE REJECTED (%s): Fixed risk %.2f%% > limit %.2f%%", sym, actual_risk_pct, allowed_cap));
            SendTelegramFallbackAlert(StringFormat("🛑 <b>TRADE REJECTED:</b> %s\nVolume %.2f risks $%.2f (%.2f%%), exceeding risk cap %.2f%%%s",
                                                   sym, volume, sl_money, actual_risk_pct, allowed_cap,
                                                   GetSymbolState(sym) == SYM_STATE_READY_REDUCED ? " [REDUCED_HIST]" : ""));
            AppendJournal(sym, (mode == MODE_SCALP ? "SCALP" : "HOLD"), type_str, exec_price, sl, tp, volume, 0, 0, "Risk limit exceeded");
            if(req_id != "") CacheRequestResult(req_id, err);
            return err;
         }
      }

      // Total Open Risk Check (across all open positions + new proposed trade)
      double cur_sl_money = 0.0, cur_tp_money = 0.0, d_sl_pts = 0.0, d_tp_pts = 0.0, d_rr = 0.0;
      CalculateSLTPMoney(sym, order_type_enum, volume, exec_price, sl, tp, cur_sl_money, cur_tp_money, d_sl_pts, d_tp_pts, d_rr);
      double total_open_risk = GetTotalOpenRiskMoney();
      double total_risk_after = total_open_risk + cur_sl_money;
      double total_risk_pct = (cap_base > 0.0) ? (total_risk_after / cap_base) * 100.0 : 0.0;
      if(total_risk_pct > InpMaxTotalOpenRiskPercent)
      {
         string err = StringFormat("{\"status\":\"REJECTED\",\"message\":\"TRADE REJECTED: Total open risk would be %.2f%% ($%.2f), exceeding MaxTotalOpenRiskPercent (%.2f%%)\"}",
                                   total_risk_pct, total_risk_after, InpMaxTotalOpenRiskPercent);
         PrintFormat("[PythonBridgeEA] [RISK_CAP] %s", err);
         NotifyUser(StringFormat("TRADE REJECTED (%s): Total risk %.2f%% > limit %.2f%%", sym, total_risk_pct, InpMaxTotalOpenRiskPercent));
         SendTelegramFallbackAlert(StringFormat("🛑 <b>TRADE REJECTED:</b> %s\nTotal open risk would reach %.2f%% ($%.2f), exceeding cap %.2f%%",
                                                sym, total_risk_pct, total_risk_after, InpMaxTotalOpenRiskPercent));
         AppendJournal(sym, (mode == MODE_SCALP ? "SCALP" : "HOLD"), type_str, exec_price, sl, tp, volume, 0, 0, "Total open risk exceeded");
         if(req_id != "") CacheRequestResult(req_id, err);
         return err;
      }

      // Pre-trade general validation (account trade allowed, max positions, quota, and prop firm rules)
      string val_reason = "";
      if(!ValidatePreTrade(sym, volume, sl, tp, type_str, val_reason))
      {
         string err = StringFormat("{\"status\":\"ERROR\",\"message\":\"Pre-trade validation failed: %s\"}", val_reason);
         AppendJournal(sym, (mode == MODE_SCALP ? "SCALP" : "HOLD"), type_str, 0.0, sl, tp, volume, 0, 0, val_reason);
         if(req_id != "") CacheRequestResult(req_id, err);
         return err;
      }

      // Margin Sufficiency Check via OrderCalcMargin (enforcing InpMaxMarginPercentOfFree)
      double margin_req = 0.0;
      if(OrderCalcMargin(order_type_enum, sym, volume, exec_price, margin_req))
      {
         double free_margin = AccountInfoDouble(ACCOUNT_MARGIN_FREE);
         double max_allowed_margin = free_margin * (InpMaxMarginPercentOfFree / 100.0);
         if(margin_req > max_allowed_margin)
         {
            string err = StringFormat("{\"status\":\"REJECTED\",\"message\":\"TRADE REJECTED: Required margin $%.2f exceeds %.0f%% of free margin ($%.2f / $%.2f)\"}",
                                      margin_req, InpMaxMarginPercentOfFree, max_allowed_margin, free_margin);
            PrintFormat("[PythonBridgeEA] [MARGIN] %s", err);
            NotifyUser(StringFormat("TRADE REJECTED (%s): Margin $%.2f > %.0f%% free margin", sym, margin_req, InpMaxMarginPercentOfFree));
            SendTelegramFallbackAlert(StringFormat("🛑 <b>TRADE REJECTED:</b> %s\nMargin $%.2f exceeds %.0f%% of free margin ($%.2f)",
                                                   sym, margin_req, InpMaxMarginPercentOfFree, max_allowed_margin));
            AppendJournal(sym, (mode == MODE_SCALP ? "SCALP" : "HOLD"), type_str, exec_price, sl, tp, volume, 0, 0, "Margin limit exceeded");
            if(req_id != "") CacheRequestResult(req_id, err);
            return err;
         }
      }

      g_trade.SetExpertMagicNumber(magic);

      // Order execution with transient retry loop (up to 3 attempts)
      bool order_success = false;
      uint retcode = 0;
      ulong order_ticket = 0;
      ulong deal_ticket = 0;
      double deal_price = 0.0;
      string last_err = "";

      for(int attempt = 1; attempt <= 3; attempt++)
      {
         double ask = SymbolInfoDouble(sym, SYMBOL_ASK);
         double bid = SymbolInfoDouble(sym, SYMBOL_BID);

         if(type_str == "BUY")
         {
            if(GetSymbolState(sym) == SYM_STATE_READY_REDUCED && StringFind(comment, "[REDUCED_HIST]") < 0) comment += " [REDUCED_HIST]";
      order_success = g_trade.Buy(volume, sym, ask, sl, tp, comment);
         }
         else if(type_str == "SELL")
         {
            order_success = g_trade.Sell(volume, sym, bid, sl, tp, comment);
         }
         else if(type_str == "BUY_LIMIT")
         {
            order_success = g_trade.BuyLimit(volume, price_in, sym, sl, tp, ORDER_TIME_GTC, 0, comment);
         }
         else if(type_str == "SELL_LIMIT")
         {
            order_success = g_trade.SellLimit(volume, price_in, sym, sl, tp, ORDER_TIME_GTC, 0, comment);
         }
         else if(type_str == "BUY_STOP")
         {
            order_success = g_trade.BuyStop(volume, price_in, sym, sl, tp, ORDER_TIME_GTC, 0, comment);
         }
         else if(type_str == "SELL_STOP")
         {
            order_success = g_trade.SellStop(volume, price_in, sym, sl, tp, ORDER_TIME_GTC, 0, comment);
         }

         retcode      = g_trade.ResultRetcode();
         order_ticket = g_trade.ResultOrder();
         deal_ticket  = g_trade.ResultDeal();
         deal_price   = g_trade.ResultPrice();
         last_err     = g_trade.ResultRetcodeDescription();

         if(order_success && (retcode == TRADE_RETCODE_DONE || retcode == TRADE_RETCODE_PLACED))
         {
            break;
         }

         // Transient retcodes: 10004 REQUOTE, 10021 PRICE_CHANGED, 10031 TIMEOUT
         if(retcode == 10004 || retcode == 10021 || retcode == 10031)
         {
            PrintFormat("[PythonBridgeEA] [RETRY] Transient retcode %d (%s) on attempt %d/3. Retrying with fresh quote...",
                        retcode, last_err, attempt);
            Sleep(150);
            continue;
         }
         else
         {
            break;
         }
      }

      // Task 9: Post-Execution Broker-Side Order Verification
      bool confirmed = false;
      double final_open_price = (deal_price > 0.0) ? deal_price : exec_price;
      double final_sl = sl;
      double final_tp = tp;

      if(order_success && (retcode == TRADE_RETCODE_DONE || retcode == TRADE_RETCODE_PLACED))
      {
         if(order_ticket > 0 && PositionSelectByTicket(order_ticket))
         {
            confirmed = true;
            final_open_price = PositionGetDouble(POSITION_PRICE_OPEN);
            final_sl = PositionGetDouble(POSITION_SL);
            final_tp = PositionGetDouble(POSITION_TP);
         }
         else if(order_ticket > 0 && OrderSelect(order_ticket))
         {
            confirmed = true; // Pending order placed
            final_open_price = OrderGetDouble(ORDER_PRICE_OPEN);
            final_sl = OrderGetDouble(ORDER_SL);
            final_tp = OrderGetDouble(ORDER_TP);
         }
         else if(deal_ticket > 0 && HistoryDealSelect(deal_ticket))
         {
            confirmed = true;
            final_open_price = HistoryDealGetDouble(deal_ticket, DEAL_PRICE);
         }
         else if(PositionSelect(sym))
         {
            confirmed = true;
            order_ticket = PositionGetInteger(POSITION_TICKET);
            final_open_price = PositionGetDouble(POSITION_PRICE_OPEN);
            final_sl = PositionGetDouble(POSITION_SL);
            final_tp = PositionGetDouble(POSITION_TP);
         }
         else
         {
            // If neither position nor pending order exists yet, wait briefly and retry confirmation
            Sleep(100);
            if(PositionSelect(sym) || (order_ticket > 0 && OrderSelect(order_ticket)))
               confirmed = true;
         }
      }

      if(confirmed)
      {
         g_trades_today++;

         // Persist position state into GlobalVariables
         double init_sl_pts = (final_sl > 0.0) ? MathAbs(final_open_price - final_sl) / pt : 0.0;
         GlobalVariableSet(StringFormat("SMC_INITSL_%d", order_ticket), init_sl_pts);
         GlobalVariableSet(StringFormat("SMC_MODE_%d", order_ticket), (double)mode);
         GlobalVariableSet(StringFormat("SMC_BE_%d", order_ticket), 0.0);
         GlobalVariableSet(StringFormat("SMC_PART_%d", order_ticket), 0.0);

         // Visual chart label
         string mode_tag = (mode == MODE_SCALP ? "SCALP" : "HOLD");
         UpdateChartPositionLabel(order_ticket, sym, mode, final_open_price, mode_tag);

         // Journal entry
         AppendJournal(sym, mode_tag, type_str, final_open_price, final_sl, final_tp, volume, retcode, order_ticket, "SUCCESS");
         NotifyUser(StringFormat("OPENED %s %s %.2f lots @ %.2f (Ticket #%d) [%s]",
                                 type_str, sym, volume, final_open_price, order_ticket, mode_tag));

         double exec_sl_money = 0.0, exec_tp_money = 0.0, exec_sl_pts = 0.0, exec_tp_pts = 0.0, exec_rr = 0.0;
         CalculateSLTPMoney(sym, order_type_enum, volume, final_open_price, final_sl, final_tp, exec_sl_money, exec_tp_money, exec_sl_pts, exec_tp_pts, exec_rr);
         double bal_pct = (bal > 0.0) ? (exec_sl_money / bal) * 100.0 : 0.0;
         double eq_pct  = (eq > 0.0) ? (exec_sl_money / eq) * 100.0 : 0.0;
         string reduced_tag = (GetSymbolState(sym) == SYM_STATE_READY_REDUCED) ? " [REDUCED_HIST]" : "";
         int digits = (int)SymbolInfoInteger(sym, SYMBOL_DIGITS);

         string trade_log = StringFormat(
            "%s executed: %.2f lots @ %.*f | SL: %.*f (%.1f pts, -$%.2f, %.2f%% bal, %.2f%% eq) | TP: %.*f (%.1f pts, +$%.2f) | RR: %.2f | Mode: %s%s",
            type_str, volume, digits, final_open_price, digits, final_sl, exec_sl_pts, exec_sl_money, bal_pct, eq_pct,
            digits, final_tp, exec_tp_pts, exec_tp_money, exec_rr, mode_tag, reduced_tag
         );
         LogEvent("TRADE", sym, EnumToString(InpSignalTimeframe), trade_log);

         string result_str = StringFormat(
            "{\"status\":\"SUCCESS\",\"request_id\":\"%s\",\"ticket\":%d,\"deal\":%d,\"volume\":%.2f,\"price\":%.5f,\"sl\":%.5f,\"tp\":%.5f,\"retcode\":%d,\"symbol\":\"%s\",\"mode\":\"%s\"}",
            req_id, order_ticket, deal_ticket, volume, final_open_price, final_sl, final_tp, retcode, sym, mode_tag
         );
         if(req_id != "") CacheRequestResult(req_id, result_str);
         return result_str;
      }
      else
      {
         AppendJournal(sym, "N/A", type_str, exec_price, sl, tp, volume, retcode, 0, last_err);
         string result_str = StringFormat(
            "{\"status\":\"ERROR\",\"request_id\":\"%s\",\"message\":\"OrderSend failed: %s (retcode %d)\",\"retcode\":%d}",
            req_id, last_err, retcode, retcode
         );
         if(req_id != "") CacheRequestResult(req_id, result_str);
         return result_str;
      }
   }

   // 8. MODIFY (SL / TP)
   if(action == "MODIFY")
   {
      ulong ticket = (ulong)ExtractJsonInteger(json, "ticket", 0);
      double sl = ExtractJsonDouble(json, "sl", 0.0);
      double tp = ExtractJsonDouble(json, "tp", 0.0);
      double price = ExtractJsonDouble(json, "price", 0.0);

      if(ticket > 0 && g_pos_info.SelectByTicket(ticket))
      {
         if(sl == 0.0) sl = g_pos_info.StopLoss();
         if(tp == 0.0) tp = g_pos_info.TakeProfit();

         bool mod_res = g_trade.PositionModify(ticket, sl, tp);
         uint ret = g_trade.ResultRetcode();
         return mod_res ? StringFormat("{\"status\":\"SUCCESS\",\"ticket\":%d,\"sl\":%.5f,\"tp\":%.5f}", ticket, sl, tp) :
                StringFormat("{\"status\":\"ERROR\",\"message\":\"Modify failed: %s (code %d)\"}", g_trade.ResultRetcodeDescription(), ret);
      }
      else if(ticket > 0 && g_ord_info.Select(ticket))
      {
         // Pending order modification
         if(price <= 0.0) price = g_ord_info.PriceOpen();
         if(sl == 0.0) sl = g_ord_info.StopLoss();
         if(tp == 0.0) tp = g_ord_info.TakeProfit();
         bool mod_res = g_trade.OrderModify(ticket, price, sl, tp, g_ord_info.TypeTime(), g_ord_info.TimeExpiration());
         return mod_res ? StringFormat("{\"status\":\"SUCCESS\",\"ticket\":%d,\"type\":\"PENDING_MODIFIED\"}", ticket) :
                StringFormat("{\"status\":\"ERROR\",\"message\":\"Pending modify failed: %s\"}", g_trade.ResultRetcodeDescription());
      }
      return "{\"status\":\"ERROR\",\"message\":\"Ticket not found\"}";
   }

   // 9. CANCEL (Pending Order)
   if(action == "CANCEL")
   {
      ulong ticket = (ulong)ExtractJsonInteger(json, "ticket", 0);
      if(ticket > 0 && g_ord_info.Select(ticket))
      {
         bool del_res = g_trade.OrderDelete(ticket);
         return del_res ? StringFormat("{\"status\":\"SUCCESS\",\"ticket\":%d,\"message\":\"Pending order cancelled\"}", ticket) :
                StringFormat("{\"status\":\"ERROR\",\"message\":\"OrderDelete failed: %s\"}", g_trade.ResultRetcodeDescription());
      }
      return "{\"status\":\"ERROR\",\"message\":\"Pending order ticket not found\"}";
   }

   // 10. CLOSE (Full or Partial)
   if(action == "CLOSE")
   {
      ulong ticket = (ulong)ExtractJsonInteger(json, "ticket", 0);
      double vol = ExtractJsonDouble(json, "volume", 0.0);

      if(ticket > 0 && g_pos_info.SelectByTicket(ticket))
      {
         bool close_res = false;
         if(vol > 0.0 && vol < g_pos_info.Volume())
            close_res = g_trade.PositionClosePartial(ticket, vol);
         else
            close_res = g_trade.PositionClose(ticket);

         uint ret = g_trade.ResultRetcode();
         if(close_res)
         {
            NotifyUser(StringFormat("CLOSED position #%d on %s", ticket, g_pos_info.Symbol()));
            AppendJournal(g_pos_info.Symbol(), "MANUAL", "CLOSE", g_pos_info.PriceCurrent(), 0, 0, vol, ret, ticket, "Close requested");
            return StringFormat("{\"status\":\"SUCCESS\",\"ticket\":%d}", ticket);
         }
         else
         {
            return StringFormat("{\"status\":\"ERROR\",\"message\":\"Close failed: %s (code %d)\"}", g_trade.ResultRetcodeDescription(), ret);
         }
      }
      return "{\"status\":\"ERROR\",\"message\":\"Position not found\"}";
   }

   // 11. ENABLE (Authorize Trading on LIVE / PROP)
   if(action == "ENABLE")
   {
      g_trading_enabled = true;
      GlobalVariableSet("SMC_ENABLED_" + IntegerToString(InpMagicNumber) + "_" + IntegerToString(AccountInfoInteger(ACCOUNT_LOGIN)), 1.0);
      Print("[PythonBridgeEA] [COMMAND] Trading explicitly ENABLED via command.");
      NotifyUser("Trading explicitly ENABLED.");
      UpdateDashboardVisuals();
      return "{\"status\":\"SUCCESS\",\"message\":\"Trading explicitly ENABLED\",\"trading_enabled\":true}";
   }

   // 12. DISABLE (Halt Trading on LIVE / PROP)
   if(action == "DISABLE")
   {
      g_trading_enabled = false;
      GlobalVariableSet("SMC_ENABLED_" + IntegerToString(InpMagicNumber) + "_" + IntegerToString(AccountInfoInteger(ACCOUNT_LOGIN)), 0.0);
      Print("[PythonBridgeEA] [COMMAND] Trading explicitly DISABLED via command.");
      NotifyUser("Trading explicitly DISABLED.");
      UpdateDashboardVisuals();
      return "{\"status\":\"SUCCESS\",\"message\":\"Trading explicitly DISABLED\",\"trading_enabled\":false}";
   }

   return StringFormat("{\"status\":\"ERROR\",\"message\":\"Unknown command '%s'\"}", action);
}

//+------------------------------------------------------------------+
//| Autonomous Position Lifecycle: Break-Even, Trailing SL, Time-Stop|
//+------------------------------------------------------------------+
void ManageActivePositions()
{
   int total = PositionsTotal();
   for(int i = total - 1; i >= 0; i--)
   {
      if(!g_pos_info.SelectByIndex(i)) continue;
      if(g_pos_info.Magic() != InpMagicNumber) continue;

      ulong  ticket     = g_pos_info.Ticket();
      string sym        = g_pos_info.Symbol();
      ENUM_POSITION_TYPE type = g_pos_info.PositionType();
      double open_price = g_pos_info.PriceOpen();
      double cur_price  = g_pos_info.PriceCurrent();
      double sl         = g_pos_info.StopLoss();
      double tp         = g_pos_info.TakeProfit();
      datetime open_tm  = (datetime)PositionGetInteger(POSITION_TIME);

      double r_points = GlobalVariableGet(StringFormat("SMC_INITSL_%d", ticket));
      if(r_points <= 0.0 && sl > 0.0)
      {
         r_points = MathAbs(open_price - sl) / SymbolInfoDouble(sym, SYMBOL_POINT);
      }
      if(r_points <= 0.0) r_points = 200.0;

      ENUM_STRATEGY_MODE mode = (ENUM_STRATEGY_MODE)(int)GlobalVariableGet(StringFormat("SMC_MODE_%d", ticket));

      // 1. Break-Even Check
      if(InpEnableBE)
      {
         CheckBreakEven(ticket, sym, type, open_price, cur_price, sl, tp, r_points);
      }

      // 2. Trailing Stop Check (strictly tightens in profitable direction)
      CheckTrailingStop(ticket, sym, type, open_price, cur_price, sl, tp, r_points, mode);

      // 3. Scalp Time-Stop Check
      if(mode == MODE_SCALP)
      {
         CheckScalpTimeStop(ticket, open_tm, mode);
      }
   }
}

//+------------------------------------------------------------------+
//| Break-Even Logic                                                 |
//+------------------------------------------------------------------+
void CheckBreakEven(const ulong ticket, const string sym, const ENUM_POSITION_TYPE type,
                    const double open_price, const double cur_price, double &sl, const double tp, const double r_points)
{
   string be_key = StringFormat("SMC_BE_%d", ticket);
   if(GlobalVariableGet(be_key) > 0) return;

   double pt = SymbolInfoDouble(sym, SYMBOL_POINT);
   double profit_dist_pts = (type == POSITION_TYPE_BUY) ? (cur_price - open_price) / pt : (open_price - cur_price) / pt;
   double trigger_pts = InpBE_TriggerR * r_points;

   if(profit_dist_pts >= trigger_pts && trigger_pts > 0)
   {
      double new_sl = (type == POSITION_TYPE_BUY) ?
                      NormalizeDouble(open_price + (InpBE_BufferPoints * pt), (int)SymbolInfoInteger(sym, SYMBOL_DIGITS)) :
                      NormalizeDouble(open_price - (InpBE_BufferPoints * pt), (int)SymbolInfoInteger(sym, SYMBOL_DIGITS));

      bool is_valid = (type == POSITION_TYPE_BUY) ? (new_sl > sl) : (sl == 0.0 || new_sl < sl);
      if(is_valid)
      {
         if(g_trade.PositionModify(ticket, new_sl, tp))
         {
            GlobalVariableSet(be_key, 1.0);
            sl = new_sl;
            PrintFormat("[PythonBridgeEA] [BE] Position #%d advanced to Break-Even at %.5f", ticket, new_sl);
            UpdateChartPositionLabel(ticket, sym, MODE_AUTO, open_price, "BE ACTIVE");
            NotifyUser(StringFormat("Break-Even activated for #%d %s at %.2f", ticket, sym, new_sl));
            AppendJournal(sym, "BE", "MODIFY_SL", open_price, new_sl, tp, g_pos_info.Volume(), 10009, ticket, "Break-even activated");
         }
      }
   }
}

//+------------------------------------------------------------------+
//| Trailing Stop Logic (Never loosens, respects stops/freeze level) |
//+------------------------------------------------------------------+
void CheckTrailingStop(const ulong ticket, const string sym, const ENUM_POSITION_TYPE type,
                       const double open_price, const double cur_price, double &sl, const double tp,
                       const double r_points, const ENUM_STRATEGY_MODE mode)
{
   double pt = SymbolInfoDouble(sym, SYMBOL_POINT);
   int digits = (int)SymbolInfoInteger(sym, SYMBOL_DIGITS);
   double profit_dist_pts = (type == POSITION_TYPE_BUY) ? (cur_price - open_price) / pt : (open_price - cur_price) / pt;

   if(profit_dist_pts < (InpTrailStartR * r_points)) return;

   double trail_distance = InpTrailPoints * pt;
   if(InpTrailMode == TRAIL_ATR && g_atr_handle_m5 != INVALID_HANDLE)
   {
      double atr_val[];
      ArraySetAsSeries(atr_val, true);
      if(CopyBuffer(g_atr_handle_m5, 0, 0, 1, atr_val) > 0)
      {
         double mult = (mode == MODE_SCALP) ? InpScalpSL_ATRMult : InpTrailATRMult;
         trail_distance = atr_val[0] * mult;
      }
   }
   else if(InpTrailMode == TRAIL_SWING)
   {
      MqlRates rates[];
      ArraySetAsSeries(rates, true);
      if(CopyRates(sym, PERIOD_M5, 1, InpSwingBarsLookback, rates) >= InpSwingBarsLookback)
      {
         if(type == POSITION_TYPE_BUY)
         {
            double lowest = rates[0].low;
            for(int k = 1; k < InpSwingBarsLookback; k++) if(rates[k].low < lowest) lowest = rates[k].low;
            trail_distance = cur_price - lowest;
         }
         else
         {
            double highest = rates[0].high;
            for(int k = 1; k < InpSwingBarsLookback; k++) if(rates[k].high > highest) highest = rates[k].high;
            trail_distance = highest - cur_price;
         }
      }
   }

   double new_sl = 0.0;
   if(type == POSITION_TYPE_BUY)
   {
      new_sl = NormalizeDouble(cur_price - trail_distance, digits);
      if(new_sl > sl + (InpTrailStep * pt) && new_sl < cur_price - (SymbolInfoInteger(sym, SYMBOL_TRADE_STOPS_LEVEL) * pt))
      {
         if(g_trade.PositionModify(ticket, new_sl, tp))
         {
            sl = new_sl;
            PrintFormat("[PythonBridgeEA] [TRAIL] Position #%d Trailing SL tightened to %.5f", ticket, new_sl);
            UpdateChartPositionLabel(ticket, sym, mode, open_price, "TRAIL");
         }
      }
   }
   else // SELL
   {
      new_sl = NormalizeDouble(cur_price + trail_distance, digits);
      if((sl == 0.0 || new_sl < sl - (InpTrailStep * pt)) && new_sl > cur_price + (SymbolInfoInteger(sym, SYMBOL_TRADE_STOPS_LEVEL) * pt))
      {
         if(g_trade.PositionModify(ticket, new_sl, tp))
         {
            sl = new_sl;
            PrintFormat("[PythonBridgeEA] [TRAIL] Position #%d Trailing SL tightened to %.5f", ticket, new_sl);
            UpdateChartPositionLabel(ticket, sym, mode, open_price, "TRAIL");
         }
      }
   }
}

//+------------------------------------------------------------------+
//| Scalp Time-Stop Check (Closes stalled scalp trades after N bars) |
//+------------------------------------------------------------------+
void CheckScalpTimeStop(const ulong ticket, const datetime open_time, const ENUM_STRATEGY_MODE mode)
{
   if(mode != MODE_SCALP) return;

   int bars_elapsed = iBarShift(_Symbol, PERIOD_M5, open_time);
   if(bars_elapsed >= InpScalpMaxHoldBars)
   {
      PrintFormat("[PythonBridgeEA] [TIME-STOP] Scalp position #%d stalled for %d bars. Closing at market.",
                  ticket, bars_elapsed);
      g_trade.PositionClose(ticket);
      NotifyUser(StringFormat("Scalp position #%d closed by time-stop (%d bars)", ticket, bars_elapsed));
   }
}

//+------------------------------------------------------------------+
//| Classify Adaptive Mode: SCALP vs HOLD                            |
//| Computes from MEASURED values (Spread, ATR M5/H1, ADX M5)        |
//+------------------------------------------------------------------+
ENUM_STRATEGY_MODE ClassifyAdaptiveMode(const string sym, string &decision_reason)
{
   double atr_m5_val[];
   double atr_h1_val[];
   double adx_val[];
   ArraySetAsSeries(atr_m5_val, true);
   ArraySetAsSeries(atr_h1_val, true);
   ArraySetAsSeries(adx_val, true);

   bool has_atr_m5 = (g_atr_handle_m5 != INVALID_HANDLE && CopyBuffer(g_atr_handle_m5, 0, 0, 1, atr_m5_val) > 0);
   bool has_atr_h1 = (g_atr_handle_h1 != INVALID_HANDLE && CopyBuffer(g_atr_handle_h1, 0, 0, 1, atr_h1_val) > 0);
   bool has_adx    = (g_adx_handle_m5 != INVALID_HANDLE && CopyBuffer(g_adx_handle_m5, 0, 0, 1, adx_val) > 0);

   if(!has_atr_m5 || !has_adx)
   {
      decision_reason = "Indicator buffer unavailable (ATR_M5 or ADX_M5 handle uninitialized); defaulted to HOLD";
      return MODE_HOLD;
   }

   double ask = SymbolInfoDouble(sym, SYMBOL_ASK);
   double bid = SymbolInfoDouble(sym, SYMBOL_BID);
   double pt = SymbolInfoDouble(sym, SYMBOL_POINT);
   if(pt <= 0.0) pt = 0.01;

   double cur_spread_pts = (ask - bid) / pt;
   double atr_m5_pts = atr_m5_val[0] / pt;
   double atr_h1_pts = has_atr_h1 ? (atr_h1_val[0] / pt) : 0.0;
   double adx_val_m5 = adx_val[0];
   double spread_pct_of_atr = (atr_m5_pts > 0) ? (cur_spread_pts / atr_m5_pts) * 100.0 : 100.0;

   // SCALP Condition: Low spread vs M5 ATR (< 25%), strong ADX momentum (>= threshold)
   if(spread_pct_of_atr < 25.0 && adx_val_m5 >= InpADXThreshold)
   {
      decision_reason = StringFormat("Measured: Spread=%.1f pts (%.1f%% of ATR_M5 %.1f pts < 25%%) & ADX_M5=%.1f >= %.1f",
                                     cur_spread_pts, spread_pct_of_atr, atr_m5_pts, adx_val_m5, InpADXThreshold);
      return MODE_SCALP;
   }

   decision_reason = StringFormat("Measured: Spread=%.1f pts (%.1f%% of ATR_M5 %.1f pts), ADX_M5=%.1f (< %.1f), ATR_H1=%.1f pts > ATR_M5",
                                  cur_spread_pts, spread_pct_of_atr, atr_m5_pts, adx_val_m5, InpADXThreshold, atr_h1_pts);
   return MODE_HOLD;
}

//+------------------------------------------------------------------+
//| Dynamic Spread Filter Validation                                 |
//| Computes live spread (Ask-Bid) vs SL distance, ATR & abs ceiling |
//+------------------------------------------------------------------+
bool ValidateSpreadFilter(const string sym, const double entry_price, const double sl, const ENUM_STRATEGY_MODE mode, string &rejection_msg)
{
   double ask = SymbolInfoDouble(sym, SYMBOL_ASK);
   double bid = SymbolInfoDouble(sym, SYMBOL_BID);
   double pt  = SymbolInfoDouble(sym, SYMBOL_POINT);
   if(pt <= 0.0) pt = 0.01;

   double spread_price = ask - bid;
   double spread_pts   = spread_price / pt;

   // 1. Absolute ceiling check
   if(spread_pts > InpMaxSpreadAbsPoints)
   {
      rejection_msg = StringFormat("Spread %.1f pts (%.2f) exceeds absolute ceiling InpMaxSpreadAbsPoints (%.1f pts)",
                                   spread_pts, spread_price, InpMaxSpreadAbsPoints);
      return false;
   }

   // 2. SL distance percentage check
   double sl_dist_price = MathAbs(entry_price - sl);
   double sl_dist_pts   = sl_dist_price / pt;

   if(sl > 0.0 && sl_dist_pts > 0.0)
   {
      double spread_pct_of_sl = (spread_pts / sl_dist_pts) * 100.0;
      if(spread_pct_of_sl > InpMaxSpreadPctOfSL)
      {
         rejection_msg = StringFormat("Spread %.1f pts (%.2f) is %.2f%% of SL distance (%.1f pts / %.2f), exceeding limit %.1f%%",
                                      spread_pts, spread_price, spread_pct_of_sl, sl_dist_pts, sl_dist_price, InpMaxSpreadPctOfSL);
         return false;
      }
   }
   else // No SL supplied OR mode is SCALP -> check against ATR
   {
      double atr_val = 0.0;
      if(g_spread_atr_handle != INVALID_HANDLE)
      {
         double buf[];
         ArraySetAsSeries(buf, true);
         if(CopyBuffer(g_spread_atr_handle, 0, 0, 1, buf) > 0)
         {
            atr_val = buf[0];
         }
      }
      if(atr_val > 0.0)
      {
         double atr_pts = atr_val / pt;
         double spread_pct_of_atr = (spread_pts / atr_pts) * 100.0;
         if(spread_pct_of_atr > InpMaxSpreadPctOfATR)
         {
            rejection_msg = StringFormat("Spread %.1f pts (%.2f) is %.2f%% of ATR (%.1f pts / %.2f), exceeding limit %.1f%%",
                                         spread_pts, spread_price, spread_pct_of_atr, atr_pts, atr_val, InpMaxSpreadPctOfATR);
            return false;
         }
      }
   }

   return true;
}

//+------------------------------------------------------------------+
//| Stop / Target Direction Validation                               |
//| Enforces BUY: SL < Entry < TP | SELL: TP < Entry < SL            |
//| Validates stops_level, freeze_level, and tick size normalization |
//+------------------------------------------------------------------+
bool ValidateStopDirections(const string sym, const string type_str, const double entry_price, const double sl, const double tp, string &err_msg)
{
   double pt = SymbolInfoDouble(sym, SYMBOL_POINT);
   if(pt <= 0.0) pt = 0.01;
   long stops_level = SymbolInfoInteger(sym, SYMBOL_TRADE_STOPS_LEVEL);
   long freeze_level = SymbolInfoInteger(sym, SYMBOL_TRADE_FREEZE_LEVEL);
   double min_dist = MathMax((double)stops_level, (double)freeze_level) * pt;

   if(type_str == "BUY")
   {
      if(sl > 0.0 && sl >= entry_price)
      {
         err_msg = StringFormat("Invalid stops on BUY: SL (%.5f) must be strictly below entry price (%.5f)", sl, entry_price);
         return false;
      }
      if(tp > 0.0 && tp <= entry_price)
      {
         err_msg = StringFormat("Invalid stops on BUY: TP (%.5f) must be strictly above entry price (%.5f)", tp, entry_price);
         return false;
      }
      if(sl > 0.0 && (entry_price - sl) < min_dist)
      {
         err_msg = StringFormat("Invalid stops on BUY: SL distance (%.5f) is less than broker stops/freeze level (%.5f)", entry_price - sl, min_dist);
         return false;
      }
      if(tp > 0.0 && (tp - entry_price) < min_dist)
      {
         err_msg = StringFormat("Invalid stops on BUY: TP distance (%.5f) is less than broker stops/freeze level (%.5f)", tp - entry_price, min_dist);
         return false;
      }
   }
   else if(type_str == "SELL")
   {
      if(sl > 0.0 && sl <= entry_price)
      {
         err_msg = StringFormat("Invalid stops on SELL: SL (%.5f) must be strictly above entry price (%.5f)", sl, entry_price);
         return false;
      }
      if(tp > 0.0 && tp >= entry_price)
      {
         err_msg = StringFormat("Invalid stops on SELL: TP (%.5f) must be strictly below entry price (%.5f)", tp, entry_price);
         return false;
      }
      if(sl > 0.0 && (sl - entry_price) < min_dist)
      {
         err_msg = StringFormat("Invalid stops on SELL: SL distance (%.5f) is less than broker stops/freeze level (%.5f)", sl - entry_price, min_dist);
         return false;
      }
      if(tp > 0.0 && (entry_price - tp) < min_dist)
      {
         err_msg = StringFormat("Invalid stops on SELL: TP distance (%.5f) is less than broker stops/freeze level (%.5f)", entry_price - tp, min_dist);
         return false;
      }
   }
   else if(type_str == "BUY_LIMIT")
   {
      double ask = SymbolInfoDouble(sym, SYMBOL_ASK);
      if(entry_price >= ask)
      {
         err_msg = StringFormat("Invalid BUY_LIMIT: price (%.5f) must be below current Ask (%.5f)", entry_price, ask);
         return false;
      }
      if(sl > 0.0 && sl >= entry_price)
      {
         err_msg = StringFormat("Invalid BUY_LIMIT: SL (%.5f) must be strictly below entry price (%.5f)", sl, entry_price);
         return false;
      }
      if(tp > 0.0 && tp <= entry_price)
      {
         err_msg = StringFormat("Invalid BUY_LIMIT: TP (%.5f) must be strictly above entry price (%.5f)", tp, entry_price);
         return false;
      }
   }
   else if(type_str == "SELL_LIMIT")
   {
      double bid = SymbolInfoDouble(sym, SYMBOL_BID);
      if(entry_price <= bid)
      {
         err_msg = StringFormat("Invalid SELL_LIMIT: price (%.5f) must be above current Bid (%.5f)", entry_price, bid);
         return false;
      }
      if(sl > 0.0 && sl <= entry_price)
      {
         err_msg = StringFormat("Invalid SELL_LIMIT: SL (%.5f) must be strictly above entry price (%.5f)", sl, entry_price);
         return false;
      }
      if(tp > 0.0 && tp >= entry_price)
      {
         err_msg = StringFormat("Invalid SELL_LIMIT: TP (%.5f) must be strictly below entry price (%.5f)", tp, entry_price);
         return false;
      }
   }
   else if(type_str == "BUY_STOP")
   {
      double ask = SymbolInfoDouble(sym, SYMBOL_ASK);
      if(entry_price <= ask)
      {
         err_msg = StringFormat("Invalid BUY_STOP: price (%.5f) must be above current Ask (%.5f)", entry_price, ask);
         return false;
      }
      if(sl > 0.0 && sl >= entry_price)
      {
         err_msg = StringFormat("Invalid BUY_STOP: SL (%.5f) must be below entry price (%.5f)", sl, entry_price);
         return false;
      }
      if(tp > 0.0 && tp <= entry_price)
      {
         err_msg = StringFormat("Invalid BUY_STOP: TP (%.5f) must be above entry price (%.5f)", tp, entry_price);
         return false;
      }
   }
   else if(type_str == "SELL_STOP")
   {
      double bid = SymbolInfoDouble(sym, SYMBOL_BID);
      if(entry_price >= bid)
      {
         err_msg = StringFormat("Invalid SELL_STOP: price (%.5f) must be below current Bid (%.5f)", entry_price, bid);
         return false;
      }
      if(sl > 0.0 && sl <= entry_price)
      {
         err_msg = StringFormat("Invalid SELL_STOP: SL (%.5f) must be above entry price (%.5f)", sl, entry_price);
         return false;
      }
      if(tp > 0.0 && tp >= entry_price)
      {
         err_msg = StringFormat("Invalid SELL_STOP: TP (%.5f) must be below entry price (%.5f)", tp, entry_price);
         return false;
      }
   }
   return true;
}

//+------------------------------------------------------------------+
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
         "🚨 <b>HARD DRAWDOWN LIMIT BREACHED</b>\n"
         "📉 <b>Daily Loss:</b> <code>%.2f%%</code> (Cap: <code>%.2f%%</code>)\n"
         "📉 <b>Total DD:</b> <code>%.2f%%</code> (Cap: <code>%.2f%%</code>)\n"
         "💥 <b>Liquidated Positions:</b> <code>%d</code>\n"
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
               "⚠️ <b>SAFETY BUFFER REACHED</b>\n"
               "📉 <b>Loss Level:</b> <code>Daily %.2f%% / Total DD %.2f%%</code>\n"
               "🛑 <b>Buffer Threshold:</b> <code>%.1f%% of limit</code>\n"
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
}

//+------------------------------------------------------------------+
//| Dynamic Mathematical Position Sizing                             |
//| Enforces MaxRiskPercentPerTrade hard cap & min lot check         |
//+------------------------------------------------------------------+
double CalculateLotSize(const string sym, const double risk_pct, const double sl_distance_points, string &sizing_log)
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
}

//+------------------------------------------------------------------+
//| Configures and returns broker-supported filling mode dynamically |
//+------------------------------------------------------------------+
ENUM_ORDER_TYPE_FILLING GetSymbolFillingMode(const string sym)
{
   uint filling_mode = (uint)SymbolInfoInteger(sym, SYMBOL_FILLING_MODE);
   if((filling_mode & SYMBOL_FILLING_IOC) != 0)
   {
      return ORDER_FILLING_IOC;
   }
   if((filling_mode & SYMBOL_FILLING_FOK) != 0)
   {
      return ORDER_FILLING_FOK;
   }
   return ORDER_FILLING_RETURN;
}

void ConfigureFillingMode(const string sym)
{
   if(!g_trade.SetTypeFillingBySymbol(sym))
   {
      g_trade.SetTypeFilling(GetSymbolFillingMode(sym));
   }
}

//+------------------------------------------------------------------+
//| Request Deduplication helpers                                    |
//+------------------------------------------------------------------+
bool IsDuplicateRequest(const string req_id, string &cached_result)
{
   for(int i = 0; i < MAX_REQ_CACHE; i++)
   {
      if(g_processed_req_ids[i] == req_id)
      {
         cached_result = g_processed_req_results[i];
         return true;
      }
   }
   return false;
}

void CacheRequestResult(const string req_id, const string result_json)
{
   g_processed_req_ids[g_cache_index] = req_id;
   g_processed_req_results[g_cache_index] = result_json;
   g_cache_index = (g_cache_index + 1) % MAX_REQ_CACHE;
}

//+------------------------------------------------------------------+
//| CSV Journal Logging                                              |
//+------------------------------------------------------------------+
void AppendJournal(const string sym, const string mode, const string action,
                   const double entry, const double sl, const double tp,
                   const double lots, const uint retcode, const ulong ticket, const string reason)
{
   string filename = StringFormat("TradingJournal_%s.csv", sym);
   int handle = FileOpen(filename, FILE_CSV | FILE_READ | FILE_WRITE, ',');
   if(handle != INVALID_HANDLE)
   {
      if(FileSize(handle) == 0)
      {
         FileWriteString(handle, "Time,Symbol,Mode,Action,Entry,SL,TP,Lots,Retcode,Ticket,Reason\n");
      }
      FileSeek(handle, 0, SEEK_END);
      string row = StringFormat("%s,%s,%s,%s,%.5f,%.5f,%.5f,%.2f,%d,%d,\"%s\"\n",
                                TimeToString(TimeCurrent(), TIME_DATE | TIME_SECONDS),
                                sym, mode, action, entry, sl, tp, lots, retcode, ticket, reason);
      FileWriteString(handle, row);
      FileClose(handle);
   }
}

//+------------------------------------------------------------------+
//| Push Notifications Dispatcher                                    |
//+------------------------------------------------------------------+
void NotifyUser(const string msg)
{
   if(InpSendNotifications)
   {
      SendNotification("[MT5 BOT] " + msg);
   }

   // Dispatch via EA Telegram fallback when native Python bridge is down or watchdog halted
   if(g_socket == INVALID_HANDLE || !SocketIsConnected(g_socket) || g_watchdog_halted)
   {
      SendTelegramFallbackAlert(msg);
   }
}

//+------------------------------------------------------------------+
//| Native MQL5 Telegram WebRequest Fallback                         |
//+------------------------------------------------------------------+
void SendTelegramFallbackAlert(const string message)
{
   if(!InpTelegramFallbackEnabled) return;
   if(InpTelegramBotToken == "" || InpTelegramChatId == "") return;

   // Enforce rate limit: maximum 1 WebRequest every 5 seconds
   datetime now = TimeCurrent();
   if(now - g_last_telegram_fallback_alert < 5) return;
   g_last_telegram_fallback_alert = now;

   string api_endpoint = "https://api.telegram.org";
   string url = StringFormat("%s/bot%s/sendMessage", api_endpoint, InpTelegramBotToken);
   string json_body = StringFormat(
      "{\"chat_id\":\"%s\",\"text\":\"🚨 <b>[MQL5 EA FALLBACK ALERT]</b>\\n\\n👤 <b>Account:</b> <code>#%d</code>\\n📊 <b>Symbol:</b> <code>%s</code>\\n🕒 <b>Time:</b> <code>%s</code>\\n\\n%s\",\"parse_mode\":\"HTML\"}",
      InpTelegramChatId,
      (int)AccountInfoInteger(ACCOUNT_LOGIN),
      _Symbol,
      TimeToString(now, TIME_DATE | TIME_SECONDS),
      message
   );

   char data[];
   char result[];
   string result_headers;
   StringToCharArray(json_body, data, 0, StringLen(json_body));

   string headers = "Content-Type: application/json\r\n";
   ResetLastError();
   int res = WebRequest("POST", url, headers, 1200, data, result, result_headers);
   if(res == -1)
   {
      int err = GetLastError();
      if(err == 4014)
      {
         Print("[PythonBridgeEA] [TELEGRAM] WebRequest blocked (Error 4014)! Whitelist 'https://api.telegram.org' in Tools > Options > Expert Advisors.");
      }
      else
      {
         PrintFormat("[PythonBridgeEA] [TELEGRAM] WebRequest failed: error %d", err);
      }
   }
   else
   {
      PrintFormat("[PythonBridgeEA] [TELEGRAM] Native fallback alert dispatched successfully (HTTP %d).", res);
   }
}

//+------------------------------------------------------------------+
//| Update Chart Visuals & Position Labels                           |
//+------------------------------------------------------------------+
void UpdateChartPositionLabel(const ulong ticket, const string sym, const ENUM_STRATEGY_MODE mode, const double entry_price, const string tag)
{
   string obj_name = StringFormat("SMC_LABEL_%d", ticket);
   string text = StringFormat("ENTRY %.2f [%s] - %s", entry_price, (mode == MODE_SCALP ? "SCALP" : "HOLD"), tag);

   ObjectDelete(0, obj_name);
   ObjectCreate(0, obj_name, OBJ_TEXT, 0, TimeCurrent(), entry_price);
   ObjectSetString(0, obj_name, OBJPROP_TEXT, text);
   ObjectSetInteger(0, obj_name, OBJPROP_COLOR, (mode == MODE_SCALP ? clrGold : clrDeepSkyBlue));
   ObjectSetInteger(0, obj_name, OBJPROP_FONTSIZE, 10);
}

//+------------------------------------------------------------------+
//| JSON Parsing Helpers                                             |
//+------------------------------------------------------------------+
string ExtractJsonString(const string json, const string key, const string def_val)
{
   string needle = "\"" + key + "\":";
   int idx = StringFind(json, needle);
   if(idx < 0) return def_val;

   int start_q = StringFind(json, "\"", idx + StringLen(needle));
   if(start_q < 0) return def_val;

   int end_q = StringFind(json, "\"", start_q + 1);
   if(end_q < 0) return def_val;

   return StringSubstr(json, start_q + 1, end_q - start_q - 1);
}

double ExtractJsonDouble(const string json, const string key, const double def_val)
{
   string needle = "\"" + key + "\":";
   int idx = StringFind(json, needle);
   if(idx < 0) return def_val;

   int pos = idx + StringLen(needle);
   while(pos < StringLen(json) && (StringGetCharacter(json, pos) == ' ' || StringGetCharacter(json, pos) == '\"'))
      pos++;

   int end_pos = pos;
   while(end_pos < StringLen(json))
   {
      ushort ch = StringGetCharacter(json, end_pos);
      if((ch >= '0' && ch <= '9') || ch == '.' || ch == '-' || ch == '+')
         end_pos++;
      else
         break;
   }
   if(end_pos > pos)
      return StringToDouble(StringSubstr(json, pos, end_pos - pos));

   return def_val;
}

long ExtractJsonInteger(const string json, const string key, const long def_val)
{
   string needle = "\"" + key + "\":";
   int idx = StringFind(json, needle);
   if(idx < 0) return def_val;

   int pos = idx + StringLen(needle);
   while(pos < StringLen(json) && (StringGetCharacter(json, pos) == ' ' || StringGetCharacter(json, pos) == '\"'))
      pos++;

   int end_pos = pos;
   while(end_pos < StringLen(json))
   {
      ushort ch = StringGetCharacter(json, end_pos);
      if((ch >= '0' && ch <= '9') || ch == '-' || ch == '+')
         end_pos++;
      else
         break;
   }
   if(end_pos > pos)
      return StringToInteger(StringSubstr(json, pos, end_pos - pos));

   return def_val;
}

//+------------------------------------------------------------------+
//| Format historical rates into JSON array                          |
//| Concurrently computes UTC timestamps using broker_utc_offset     |
//+------------------------------------------------------------------+
string FormatRatesJson(const string symbol, const string tf_str, const int count, const string req_id)
{
   if(!SymbolSelect(symbol, true))
   {
      return StringFormat("{\"status\":\"ERROR\",\"request_id\":\"%s\",\"message\":\"Symbol '%s' cannot be selected\"}", req_id, symbol);
   }

   ENUM_TIMEFRAMES tf = StringToTimeframe(tf_str);
   MqlRates rates[];
   ArraySetAsSeries(rates, true);
   int copied = CopyRates(symbol, tf, 0, count, rates);

   if(copied <= 0)
   {
      return StringFormat("{\"status\":\"ERROR\",\"request_id\":\"%s\",\"message\":\"CopyRates failed for %s on %s (code %d)\"}",
                          req_id, symbol, tf_str, GetLastError());
   }

   string json_rates = "[";
   for(int i = 0; i < copied; i++)
   {
      if(i > 0) json_rates += ",";
      int candle_time = (int)rates[i].time;
      int candle_utc  = candle_time - g_broker_utc_offset;
      json_rates += StringFormat("{\"time\":%d,\"time_utc\":%d,\"open\":%.5f,\"high\":%.5f,\"low\":%.5f,\"close\":%.5f,\"tick_volume\":%d,\"spread\":%d}",
                                 candle_time, candle_utc, rates[i].open, rates[i].high, rates[i].low, rates[i].close,
                                 rates[i].tick_volume, rates[i].spread);
   }
   json_rates += "]";

   return StringFormat("{\"status\":\"SUCCESS\",\"request_id\":\"%s\",\"symbol\":\"%s\",\"timeframe\":\"%s\",\"count\":%d,\"broker_utc_offset\":%d,\"data\":%s}",
                       req_id, symbol, tf_str, copied, g_broker_utc_offset, json_rates);
}

//+------------------------------------------------------------------+
//| Convert string timeframe to ENUM_TIMEFRAMES                      |
//+------------------------------------------------------------------+
ENUM_TIMEFRAMES StringToTimeframe(const string tf_str)
{
   if(tf_str == "W1")  return PERIOD_W1;
   if(tf_str == "D1")  return PERIOD_D1;
   if(tf_str == "H4")  return PERIOD_H4;
   if(tf_str == "H1")  return PERIOD_H1;
   if(tf_str == "M15") return PERIOD_M15;
   if(tf_str == "M5")  return PERIOD_M5;
   if(tf_str == "M1")  return PERIOD_M1;
   return PERIOD_M15;
}

//+------------------------------------------------------------------+
//| Chart Visualization of Weekly and Daily Liquidity Levels        |
//+------------------------------------------------------------------+
void DrawWeeklyDailyLevels()
{
   MqlRates w_rates[];
   ArraySetAsSeries(w_rates, true);
   int w_copied = CopyRates(_Symbol, PERIOD_W1, 0, 3, w_rates);

   MqlRates d_rates[];
   ArraySetAsSeries(d_rates, true);
   int d_copied = CopyRates(_Symbol, PERIOD_D1, 0, 3, d_rates);

   if(w_copied >= 2)
   {
      double pwh = w_rates[1].high;
      double pwl = w_rates[1].low;
      double pwm = (pwh + pwl) / 2.0;

      ObjectDelete(0, "SMC_PWH");
      ObjectCreate(0, "SMC_PWH", OBJ_HLINE, 0, 0, pwh);
      ObjectSetInteger(0, "SMC_PWH", OBJPROP_COLOR, InpColorPWH);
      ObjectSetInteger(0, "SMC_PWH", OBJPROP_WIDTH, 2);
      ObjectSetString(0, "SMC_PWH", OBJPROP_TEXT, "PWH");

      ObjectDelete(0, "SMC_PWL");
      ObjectCreate(0, "SMC_PWL", OBJ_HLINE, 0, 0, pwl);
      ObjectSetInteger(0, "SMC_PWL", OBJPROP_COLOR, InpColorPWL);
      ObjectSetInteger(0, "SMC_PWL", OBJPROP_WIDTH, 2);
      ObjectSetString(0, "SMC_PWL", OBJPROP_TEXT, "PWL");

      ObjectDelete(0, "SMC_PWM");
      ObjectCreate(0, "SMC_PWM", OBJ_HLINE, 0, 0, pwm);
      ObjectSetInteger(0, "SMC_PWM", OBJPROP_COLOR, clrDarkGray);
      ObjectSetInteger(0, "SMC_PWM", OBJPROP_STYLE, STYLE_DOT);
      ObjectSetString(0, "SMC_PWM", OBJPROP_TEXT, "PWM");
   }

   if(d_copied >= 2)
   {
      double pdh = d_rates[1].high;
      double pdl = d_rates[1].low;
      double pdm = (pdh + pdl) / 2.0;

      ObjectDelete(0, "SMC_PDH");
      ObjectCreate(0, "SMC_PDH", OBJ_HLINE, 0, 0, pdh);
      ObjectSetInteger(0, "SMC_PDH", OBJPROP_COLOR, InpColorPDH);
      ObjectSetInteger(0, "SMC_PDH", OBJPROP_WIDTH, 1);
      ObjectSetString(0, "SMC_PDH", OBJPROP_TEXT, "PDH");

      ObjectDelete(0, "SMC_PDL");
      ObjectCreate(0, "SMC_PDL", OBJ_HLINE, 0, 0, pdl);
      ObjectSetInteger(0, "SMC_PDL", OBJPROP_COLOR, InpColorPDL);
      ObjectSetInteger(0, "SMC_PDL", OBJPROP_WIDTH, 1);
      ObjectSetString(0, "SMC_PDL", OBJPROP_TEXT, "PDL");
   }
}

//+------------------------------------------------------------------+
//| Chart Object Cleanup                                             |
//+------------------------------------------------------------------+
void DeleteAllChartObjects()
{
   ObjectsDeleteAll(0, "SMC_");
}


//+------------------------------------------------------------------+
//| Unified SL and TP Monetary Risk & Reward Calculation             |
//| Uses OrderCalcProfit for both SL and TP (single code path)       |
//| Adds spread and commission cost estimates                        |
//+------------------------------------------------------------------+
bool CalculateSLTPMoney(
   const string sym,
   const ENUM_ORDER_TYPE order_type,
   const double volume,
   const double entry_price,
   const double sl_price,
   const double tp_price,
   double &sl_money,
   double &tp_money,
   double &sl_dist_pts,
   double &tp_dist_pts,
   double &rr
)
{
   double pt = SymbolInfoDouble(sym, SYMBOL_POINT);
   if(pt <= 0.0) pt = 0.01;
   double tick_size = SymbolInfoDouble(sym, SYMBOL_TRADE_TICK_SIZE);
   if(tick_size <= 0.0) tick_size = pt;
   double tick_value = SymbolInfoDouble(sym, SYMBOL_TRADE_TICK_VALUE);
   int digits = (int)SymbolInfoInteger(sym, SYMBOL_DIGITS);

   sl_dist_pts = (sl_price > 0.0) ? MathAbs(entry_price - sl_price) / pt : 0.0;
   tp_dist_pts = (tp_price > 0.0) ? MathAbs(tp_price - entry_price) / pt : 0.0;

   double sl_profit = 0.0;
   double tp_profit = 0.0;
   bool sl_ok = false;
   bool tp_ok = false;

   if(sl_price > 0.0 && volume > 0.0)
   {
      sl_ok = OrderCalcProfit(order_type, sym, volume, entry_price, sl_price, sl_profit);
   }
   if(tp_price > 0.0 && volume > 0.0)
   {
      tp_ok = OrderCalcProfit(order_type, sym, volume, entry_price, tp_price, tp_profit);
   }

   // Fallback using tick formula if OrderCalcProfit returns false
   if(!sl_ok && sl_price > 0.0 && tick_value > 0.0 && volume > 0.0)
   {
      sl_profit = -1.0 * (sl_dist_pts * pt / tick_size) * tick_value * volume;
   }
   if(!tp_ok && tp_price > 0.0 && tick_value > 0.0 && volume > 0.0)
   {
      tp_profit = 1.0 * (tp_dist_pts * pt / tick_size) * tick_value * volume;
   }

   // 2. Spread cost estimate
   int spread_pts = (int)SymbolInfoInteger(sym, SYMBOL_SPREAD);
   double spread_cost = (spread_pts * pt / tick_size) * tick_value * volume;

   // 3. Commission estimate ($7.00 per standard round-turn lot)
   double commission_est = volume * 7.0;

   // 4. Net SL money (loss) and net TP money (gain)
   sl_money = MathAbs(sl_profit) + spread_cost + commission_est;
   tp_money = MathMax(0.0, tp_profit - spread_cost - commission_est);

   // 5. Reward-to-Risk ratio
   rr = (sl_money > 0.0) ? (tp_money / sl_money) : 0.0;
   return true;
}

//+------------------------------------------------------------------+
//| Calculates aggregate monetary open risk across all positions     |
//+------------------------------------------------------------------+
double GetTotalOpenRiskMoney()
{
   double total_risk = 0.0;
   int total = PositionsTotal();
   for(int i = 0; i < total; i++)
   {
      if(!g_pos_info.SelectByIndex(i)) continue;
      if(g_pos_info.Magic() != InpMagicNumber) continue;

      string sym = g_pos_info.Symbol();
      ENUM_POSITION_TYPE p_type = g_pos_info.PositionType();
      double vol = g_pos_info.Volume();
      double open_p = g_pos_info.PriceOpen();
      double sl = g_pos_info.StopLoss();

      if(sl > 0.0)
      {
         ENUM_ORDER_TYPE o_type = (p_type == POSITION_TYPE_BUY) ? ORDER_TYPE_BUY : ORDER_TYPE_SELL;
         double p_loss = 0.0;
         if(OrderCalcProfit(o_type, sym, vol, open_p, sl, p_loss))
         {
            total_risk += MathAbs(MathMin(0.0, p_loss));
         }
         else
         {
            double pt = SymbolInfoDouble(sym, SYMBOL_POINT);
            if(pt <= 0.0) pt = 0.01;
            double tick_sz = SymbolInfoDouble(sym, SYMBOL_TRADE_TICK_SIZE);
            if(tick_sz <= 0.0) tick_sz = pt;
            double tick_val = SymbolInfoDouble(sym, SYMBOL_TRADE_TICK_VALUE);
            total_risk += (MathAbs(open_p - sl) / tick_sz) * tick_val * vol;
         }
      }
   }
   return total_risk;
}

//+------------------------------------------------------------------+
//| Periodic Audit Scanner for Oversized Positions                   |
//+------------------------------------------------------------------+
void AuditOpenPositionsRisk()
{
   static datetime last_audit_time = 0;
   datetime now = TimeCurrent();
   if(now - last_audit_time < (datetime)InpAuditIntervalSec) return;
   last_audit_time = now;

   double balance = AccountInfoDouble(ACCOUNT_BALANCE);
   if(balance <= 0.0) return;

   int total = PositionsTotal();
   for(int i = total - 1; i >= 0; i--)
   {
      if(!g_pos_info.SelectByIndex(i)) continue;
      if(g_pos_info.Magic() != InpMagicNumber) continue;

      ulong ticket = g_pos_info.Ticket();
      string sym = g_pos_info.Symbol();
      ENUM_POSITION_TYPE pos_type = g_pos_info.PositionType();
      double vol = g_pos_info.Volume();
      double open_price = g_pos_info.PriceOpen();
      double sl = g_pos_info.StopLoss();

      double allowed_risk_pct = InpMaxRiskPercentPerTrade;
      if(GetSymbolState(sym) == SYM_STATE_READY_REDUCED)
         allowed_risk_pct *= InpReducedHistoryRiskFactor;

      double monetary_loss = 0.0;
      if(sl > 0.0)
      {
         ENUM_ORDER_TYPE ord_type = (pos_type == POSITION_TYPE_BUY) ? ORDER_TYPE_BUY : ORDER_TYPE_SELL;
         double calc_profit = 0.0;
         if(OrderCalcProfit(ord_type, sym, vol, open_price, sl, calc_profit))
         {
            monetary_loss = MathAbs(MathMin(0.0, calc_profit));
         }
         else
         {
            double pt = SymbolInfoDouble(sym, SYMBOL_POINT);
            if(pt <= 0.0) pt = 0.01;
            double tick_sz = SymbolInfoDouble(sym, SYMBOL_TRADE_TICK_SIZE);
            if(tick_sz <= 0.0) tick_sz = pt;
            double tick_val = SymbolInfoDouble(sym, SYMBOL_TRADE_TICK_VALUE);
            monetary_loss = (MathAbs(open_price - sl) / tick_sz) * tick_val * vol;
         }
      }
      else
      {
         // Naked position without SL: treat as total balance risk
         monetary_loss = balance;
      }

      double risk_pct = (monetary_loss / balance) * 100.0;
      if(risk_pct > allowed_risk_pct)
      {
         string audit_msg = StringFormat(
            "OVERSIZED POSITION: Ticket #%d (%s, %.2f lots @ %.5f) risks $%.2f (%.2f%% of balance $%.2f), exceeding max risk cap (%.2f%%%s)",
            ticket, sym, vol, open_price, monetary_loss, risk_pct, balance, allowed_risk_pct,
            GetSymbolState(sym) == SYM_STATE_READY_REDUCED ? " [REDUCED_HIST]" : ""
         );
         LogEvent("CRITICAL", sym, "AUDIT", audit_msg);

         if(InpAutoCutOversized)
         {
            if(g_trade.PositionClose(ticket))
            {
               LogEvent("TRADE", sym, "AUDIT",
                        StringFormat("OVERSIZED POSITION CUT: Closed ticket #%d to enforce risk cap.", ticket));
            }
         }
      }
   }
}

//+------------------------------------------------------------------+
//| Symbol Specifications & Risk Metrics Table Provider (JSON)       |
//+------------------------------------------------------------------+
string FormatSymbolStatusJson(const string req_id)
{
   double bal = AccountInfoDouble(ACCOUNT_BALANCE);
   double eq  = AccountInfoDouble(ACCOUNT_EQUITY);
   double fm  = AccountInfoDouble(ACCOUNT_MARGIN_FREE);
   double total_open_risk = GetTotalOpenRiskMoney();
   double total_open_risk_bal = (bal > 0.0) ? (total_open_risk / bal) * 100.0 : 0.0;
   double total_open_risk_eq  = (eq > 0.0) ? (total_open_risk / eq) * 100.0 : 0.0;

   // Collect unique symbols to report
   string syms[];
   ArrayResize(syms, 1);
   syms[0] = _Symbol;

   if(InpSymbolsToMonitor != "")
   {
      string tokens[];
      int k = StringSplit(InpSymbolsToMonitor, ',', tokens);
      for(int i = 0; i < k; i++)
      {
         string s = tokens[i];
         StringTrimLeft(s);
         StringTrimRight(s);
         if(s != "" && s != _Symbol)
         {
            int sz = ArraySize(syms);
            ArrayResize(syms, sz + 1);
            syms[sz] = s;
         }
      }
   }

   string syms_json = "[";
   int s_total = ArraySize(syms);
   for(int i = 0; i < s_total; i++)
   {
      string s = syms[i];
      SymbolSelect(s, true);

      double contract_sz = SymbolInfoDouble(s, SYMBOL_TRADE_CONTRACT_SIZE);
      double tick_val    = SymbolInfoDouble(s, SYMBOL_TRADE_TICK_VALUE);
      double tick_sz     = SymbolInfoDouble(s, SYMBOL_TRADE_TICK_SIZE);
      double pt          = SymbolInfoDouble(s, SYMBOL_POINT);
      if(pt <= 0.0) pt = 0.01;
      if(tick_sz <= 0.0) tick_sz = pt;
      int digits         = (int)SymbolInfoInteger(s, SYMBOL_DIGITS);

      double min_lot     = SymbolInfoDouble(s, SYMBOL_VOLUME_MIN);
      double step_lot    = SymbolInfoDouble(s, SYMBOL_VOLUME_STEP);
      double broker_max  = SymbolInfoDouble(s, SYMBOL_VOLUME_MAX);
      double dynamic_max = (bal < 500.0) ? MathMin(min_lot * 3.0, InpMaxLot) : InpMaxLot;
      double max_lot     = MathMin(broker_max, dynamic_max);

      int cur_spread_pts = (int)SymbolInfoInteger(s, SYMBOL_SPREAD);
      double spread_money = (cur_spread_pts * pt / tick_sz) * tick_val * min_lot;

      // 1x ATR on M15
      double atr_arr[];
      ArraySetAsSeries(atr_arr, true);
      double atr_pts = 0.0;
      double atr_money = 0.0;
      if(s == _Symbol && g_atr_handle_m5 != INVALID_HANDLE)
      {
         if(CopyBuffer(g_atr_handle_m5, 0, 1, 1, atr_arr) > 0)
         {
            atr_pts = atr_arr[0] / pt;
            atr_money = (atr_pts * pt / tick_sz) * tick_val * min_lot;
         }
      }
      if(atr_pts <= 0.0)
      {
         atr_pts = (digits == 2 || digits == 3) ? 200.0 : 40.0;
         atr_money = (atr_pts * pt / tick_sz) * tick_val * min_lot;
      }

      // Sample lot size for 1x ATR stop
      string dummy_sizing_log;
      double sample_lot = CalculateLotSize(s, InpMaxRiskPercent, atr_pts, dummy_sizing_log);
      double sample_risk_money = (sample_lot > 0.0) ? (sample_lot * (atr_pts * pt / tick_sz) * tick_val) : (min_lot * (atr_pts * pt / tick_sz) * tick_val);
      double sample_risk_pct   = (bal > 0.0) ? (sample_risk_money / bal) * 100.0 : 0.0;

      // Open positions and risk on this symbol
      int pos_count = 0;
      double sym_open_risk = 0.0;
      for(int p = 0; p < PositionsTotal(); p++)
      {
         if(g_pos_info.SelectByIndex(p) && g_pos_info.Symbol() == s && g_pos_info.Magic() == InpMagicNumber)
         {
            pos_count++;
            double p_open = g_pos_info.PriceOpen();
            double p_sl   = g_pos_info.StopLoss();
            double p_vol  = g_pos_info.Volume();
            if(p_sl > 0.0)
            {
               ENUM_ORDER_TYPE ot = (g_pos_info.PositionType() == POSITION_TYPE_BUY) ? ORDER_TYPE_BUY : ORDER_TYPE_SELL;
               double pl = 0.0;
               if(OrderCalcProfit(ot, s, p_vol, p_open, p_sl, pl))
                  sym_open_risk += MathAbs(MathMin(0.0, pl));
               else
                  sym_open_risk += (MathAbs(p_open - p_sl) / tick_sz) * tick_val * p_vol;
            }
         }
      }
      double sym_open_risk_bal = (bal > 0.0) ? (sym_open_risk / bal) * 100.0 : 0.0;
      double sym_open_risk_eq  = (eq > 0.0) ? (sym_open_risk / eq) * 100.0 : 0.0;

      ENUM_SYMBOL_STATE sym_st = GetSymbolState(s);
      string sym_st_str = (sym_st == SYM_STATE_READY ? "READY" :
                          (sym_st == SYM_STATE_READY_REDUCED ? "READY_REDUCED" :
                          (sym_st == SYM_STATE_BLOCKED ? "BLOCKED" : "CHECKING")));

      if(i > 0) syms_json += ",";
      syms_json += StringFormat(
         "{\"symbol\":\"%s\",\"state\":\"%s\",\"contract_size\":%.2f,\"tick_value\":%.4f,\"tick_size\":%.5f,\"min_lot\":%.2f,\"step_lot\":%.2f,\"max_lot\":%.2f,\"spread_pts\":%d,\"spread_money\":%.2f,\"atr_pts\":%.1f,\"atr_money\":%.2f,\"sample_lot\":%.2f,\"sample_risk_money\":%.2f,\"sample_risk_pct\":%.2f,\"open_positions\":%d,\"open_risk_money\":%.2f,\"open_risk_bal_pct\":%.2f,\"open_risk_eq_pct\":%.2f}",
         s, sym_st_str, contract_sz, tick_val, tick_sz, min_lot, step_lot, max_lot,
         cur_spread_pts, spread_money, atr_pts, atr_money,
         sample_lot, sample_risk_money, sample_risk_pct,
         pos_count, sym_open_risk, sym_open_risk_bal, sym_open_risk_eq
      );
   }
   syms_json += "]";

   return StringFormat(
      "{\"status\":\"SUCCESS\",\"request_id\":\"%s\",\"data\":{\"total_open_risk_money\":%.2f,\"total_open_risk_bal_pct\":%.2f,\"total_open_risk_eq_pct\":%.2f,\"max_total_open_risk_pct\":%.2f,\"balance\":%.2f,\"equity\":%.2f,\"free_margin\":%.2f,\"symbols\":%s}}",
      req_id, total_open_risk, total_open_risk_bal, total_open_risk_eq, InpMaxTotalOpenRiskPercent,
      bal, eq, fm, syms_json
   );
}
