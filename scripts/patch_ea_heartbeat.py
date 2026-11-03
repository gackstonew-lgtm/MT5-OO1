import re

with open("mql5/Experts/PythonBridgeEA.mq5", "r", encoding="utf-8") as f:
    content = f.read()

# 1. Add connection state and helper functions after g_next_reconnect_time
target1 = """// Network Exponential Backoff & Jitter
int            g_reconnect_backoff_sec = 1;
datetime       g_next_reconnect_time = 0;"""

addition1 = """// Network Exponential Backoff & Jitter
int            g_reconnect_backoff_sec = 1;
datetime       g_next_reconnect_time = 0;

// Honest Connection State Tracking (Part 1)
enum ENUM_BRIDGE_STATE
{
   BRIDGE_DISCONNECTED = 0,
   BRIDGE_RECONNECTING = 1,
   BRIDGE_CONNECTED    = 2
};

ENUM_BRIDGE_STATE g_bridge_state = BRIDGE_DISCONNECTED;
datetime          g_last_reply_time = 0;
int               g_consecutive_failures = 0;
int               g_last_error_code = 0;
string            g_last_error_name = "NONE";

// Terminal Global Variable Keys for Outage Alert Deduplication Across Charts (Part 4)
#define GV_OUTAGE_LOCK       "BRIDGE_OUTAGE_LOCK"
#define GV_OUTAGE_START_TIME "BRIDGE_OUTAGE_START_TIME"
static bool       g_outage_alert_sent = false;

string GetSocketErrorName(const int err)
{
   switch(err)
   {
      case 0:    return "ERR_SUCCESS";
      case 5270: return "ERR_NETSOCKET_INVALID_HANDLE";
      case 5271: return "ERR_NETSOCKET_TOO_MANY_OPENED";
      case 5272: return "ERR_NETSOCKET_CANNOT_CONNECT";
      case 5273: return "ERR_NETSOCKET_IO_ERROR";
      case 5274: return "ERR_NETSOCKET_HANDSHAKE_FAILED";
      case 5275: return "ERR_NETSOCKET_NO_OPTIONS";
      case 4014: return "ERR_FUNCTION_NOT_ALLOWED";
      case 4001: return "ERR_INTERNAL_ERROR";
      default:   return StringFormat("ERR_CODE_%d", err);
   }
}

void SendTelegramDirect(const string message)
{
   if(!InpTelegramFallbackEnabled) return;
   if(InpTelegramBotToken == "" || InpTelegramChatId == "") return;

   static datetime last_send = 0;
   datetime now = TimeCurrent();
   if(now - last_send < 3) return;
   last_send = now;

   string api_endpoint = "https://api.telegram.org";
   string url = StringFormat("%s/bot%s/sendMessage", api_endpoint, InpTelegramBotToken);
   string json_body = StringFormat(
      "{\\"chat_id\\":\\"%s\\",\\"text\\":\\"%s\\",\\"parse_mode\\":\\"HTML\\"}",
      InpTelegramChatId,
      message
   );

   char data[];
   char result[];
   string result_headers;
   StringToCharArray(json_body, data, 0, StringLen(json_body));

   string headers = "Content-Type: application/json\\r\\n";
   ResetLastError();
   int res = WebRequest("POST", url, headers, 3000, data, result, result_headers);
   if(res == -1)
   {
      int err = GetLastError();
      if(err == 4014)
         Print("[PythonBridgeEA] [TELEGRAM] WebRequest blocked (Error 4014)! Allow 'https://api.telegram.org' in Tools > Options > Expert Advisors.");
      else
         PrintFormat("[PythonBridgeEA] [TELEGRAM] WebRequest failed: error %d", err);
   }
}

void CheckAndTriggerOutageAlert()
{
   datetime now = TimeCurrent();
   datetime last_reply = (g_last_reply_time > 0) ? g_last_reply_time : g_last_connect_attempt;
   if(last_reply == 0) last_reply = now;

   int outage_duration = (int)(now - last_reply);

   if(g_bridge_state != BRIDGE_CONNECTED && outage_duration >= 60)
   {
      if(!GlobalVariableCheck(GV_OUTAGE_LOCK))
      {
         GlobalVariableSet(GV_OUTAGE_LOCK, 1.0);
         GlobalVariableSet(GV_OUTAGE_START_TIME, (double)last_reply);
         g_outage_alert_sent = true;

         int next_retry_sec = MathMax(0, (int)(g_next_reconnect_time - now));
         string next_step = "Start the bridge service (e.g. scripts/run_bridge_server.py or install_service.ps1).";

         string alert_msg = StringFormat(
            "🚨 <b>[BRIDGE OUTAGE ALERT]</b>\\n\\n"
            "📊 <b>Symbol:</b> <code>%s</code>\\n"
            "👤 <b>Account:</b> <code>#%d</code>\\n"
            "⚠️ <b>Error:</b> <code>%d (%s)</code>\\n"
            "⏳ <b>Outage Duration:</b> <code>%d seconds</code>\\n"
            "🔄 <b>Retry Count:</b> <code>%d</code> (Next retry in %ds)\\n\\n"
            "👉 <b>Next Step:</b> %s",
            _Symbol, (int)AccountInfoInteger(ACCOUNT_LOGIN),
            g_last_error_code, g_last_error_name,
            outage_duration, g_consecutive_failures, next_retry_sec,
            next_step
         );

         PrintFormat("[PythonBridgeEA] [OUTAGE_ALERT] Outage duration %ds >= 60s. Dispatched terminal Telegram outage alert.", outage_duration);
         SendTelegramDirect(alert_msg);
      }
   }
}

void CheckAndResolveOutageAlert()
{
   if(GlobalVariableCheck(GV_OUTAGE_LOCK))
   {
      datetime now = TimeCurrent();
      datetime start_time = (datetime)GlobalVariableGet(GV_OUTAGE_START_TIME);
      int downtime = (start_time > 0 && now >= start_time) ? (int)(now - start_time) : 60;
      int attempts = g_consecutive_failures;

      string resolved_msg = StringFormat(
         "✅ <b>[BRIDGE OUTAGE RESOLVED]</b>\\n\\n"
         "📊 <b>Symbol:</b> <code>%s</code>\\n"
         "👤 <b>Account:</b> <code>#%d</code>\\n"
         "⏱️ <b>Total Downtime:</b> <code>%d seconds</code>\\n"
         "🔄 <b>Total Reconnection Attempts:</b> <code>%d</code>\\n\\n"
         "🟢 Bridge communication restored successfully.",
         _Symbol, (int)AccountInfoInteger(ACCOUNT_LOGIN),
         downtime, attempts
      );

      PrintFormat("[PythonBridgeEA] [OUTAGE_RESOLVED] Bridge restored after %ds downtime. Dispatched terminal Telegram resolved alert.", downtime);
      SendTelegramDirect(resolved_msg);

      GlobalVariableDel(GV_OUTAGE_LOCK);
      GlobalVariableDel(GV_OUTAGE_START_TIME);
      g_outage_alert_sent = false;
   }
}"""

if target1 in content:
    content = content.replace(target1, addition1)
    print("Replaced target 1 (Global declarations)")
else:
    print("Failed to find target 1")

# 2. Update OnTimer heartbeat and add CheckAndTriggerOutageAlert()
old_ontimer_hb = """   // 4c. Periodic Heartbeat Status (throttled to InpHeartbeatSec)
   static datetime last_heartbeat_status_time = 0;
   if(TimeCurrent() - last_heartbeat_status_time >= (datetime)InpHeartbeatSec)
   {
      last_heartbeat_status_time = TimeCurrent();
      LogEvent("INFO", _Symbol, "HEARTBEAT",
               StringFormat("Heartbeat: Terminal online, bridge connected, active positions: %d, spread: %d pts",
                            PositionsTotal(), (int)SymbolInfoInteger(_Symbol, SYMBOL_SPREAD)));
   }"""

new_ontimer_hb = """   // 4c. Honest Periodic Heartbeat Status (Part 1 - Never claim connected unless recent reply received)
   static datetime last_heartbeat_status_time = 0;
   datetime now_cur = TimeCurrent();
   CheckAndTriggerOutageAlert();

   if(now_cur - last_heartbeat_status_time >= (datetime)InpHeartbeatSec)
   {
      last_heartbeat_status_time = now_cur;
      int sec_since_reply = (g_last_reply_time > 0 && now_cur >= g_last_reply_time) ? (int)(now_cur - g_last_reply_time) : -1;
      bool is_honest_connected = (g_socket != INVALID_HANDLE && SocketIsConnected(g_socket) && sec_since_reply >= 0 && sec_since_reply <= (int)(2 * InpWatchdogTimeoutSec));

      string reply_str = (sec_since_reply >= 0) ? StringFormat("last reply %ds ago", sec_since_reply) : "no reply yet";
      string bridge_str = is_honest_connected ? StringFormat("bridge connected (%s)", reply_str) :
                          StringFormat("bridge %s (%s, err=%d %s, retries=%d, next retry in %ds)",
                                       (g_next_retry_time > now_cur ? "RECONNECTING" : "DISCONNECTED"),
                                       reply_str, g_last_error_code, g_last_error_name,
                                       g_consecutive_failures, MathMax(0, (int)(g_next_retry_time - now_cur)));

      LogEvent("INFO", _Symbol, "HEARTBEAT",
               StringFormat("Heartbeat: Terminal online, %s, active positions: %d, spread: %d pts",
                            bridge_str, PositionsTotal(), (int)SymbolInfoInteger(_Symbol, SYMBOL_SPREAD)));
   }"""

if old_ontimer_hb in content:
    content = content.replace(old_ontimer_hb, new_ontimer_hb)
    print("Replaced target 2 (OnTimer heartbeat)")
else:
    print("Failed to find target 2")

# 3. Update EnsureConnection, DisconnectSocket, HandleMessage
old_ensure = """bool EnsureConnection()
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
   string handshake = StringFormat("{\\"client\\":\\"PythonBridgeEA\\",\\"symbol\\":\\"%s\\",\\"account\\":%d,\\"magic\\":%d,\\"broker_utc_offset\\":%d}\\n",
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
}"""

new_ensure = """bool EnsureConnection()
{
   datetime now = TimeCurrent();
   int sec_since_reply = (g_last_reply_time > 0 && now >= g_last_reply_time) ? (int)(now - g_last_reply_time) : -1;

   if(g_socket != INVALID_HANDLE && SocketIsConnected(g_socket))
   {
      if(sec_since_reply >= 0 && sec_since_reply <= (int)(2 * InpWatchdogTimeoutSec))
      {
         g_bridge_state = BRIDGE_CONNECTED;
         return true;
      }
      g_bridge_state = BRIDGE_RECONNECTING;
   }

   if(now < g_next_reconnect_time)
   {
      g_bridge_state = BRIDGE_RECONNECTING;
      return false;
   }

   DisconnectSocket();

   g_socket = SocketCreate();
   if(g_socket == INVALID_HANDLE)
   {
      int err = GetLastError();
      g_consecutive_failures++;
      g_last_error_code = err;
      g_last_error_name = GetSocketErrorName(err);
      g_bridge_state = BRIDGE_DISCONNECTED;
      PrintFormat("[PythonBridgeEA] [ERROR] SocketCreate failed: %d (%s)", err, g_last_error_name);
      g_next_reconnect_time = now + 5;
      return false;
   }

   SocketTimeouts(g_socket, InpTimeoutMs, InpTimeoutMs);

   if(!SocketConnect(g_socket, InpServerHost, (uint)InpServerPort, InpTimeoutMs))
   {
      int err = GetLastError();
      g_consecutive_failures++;
      g_last_error_code = err;
      g_last_error_name = GetSocketErrorName(err);

      g_reconnect_backoff_sec = MathMin(30, (g_reconnect_backoff_sec <= 0 ? 1 : g_reconnect_backoff_sec * 2));
      int jitter = MathRand() % 3;
      int delay = g_reconnect_backoff_sec + jitter;
      g_next_reconnect_time = now + delay;
      g_bridge_state = BRIDGE_RECONNECTING;

      if(now - g_last_logged_failure >= 10)
      {
         PrintFormat("[PythonBridgeEA] [DISCONNECTED] Connect to %s:%d failed (Error %d: %s). Next retry in %ds.",
                     InpServerHost, InpServerPort, err, g_last_error_name, delay);
         g_last_logged_failure = now;
      }
      DisconnectSocket();
      return false;
   }

   // Connected successfully
   g_reconnect_backoff_sec = 1;
   g_next_reconnect_time = 0;
   g_bridge_state = BRIDGE_CONNECTED;
   g_last_heartbeat_time = now;
   g_last_reply_time = now;
   g_watchdog_halted = false;

   PrintFormat("[PythonBridgeEA] [CONNECTED] Successfully connected to Python Bridge Server on %s:%d (Magic %d)",
               InpServerHost, InpServerPort, g_active_magic);

   // Resolve any active outage alert
   CheckAndResolveOutageAlert();

   g_consecutive_failures = 0;
   g_last_error_code = 0;
   g_last_error_name = "NONE";

   // Send initial identification handshake (HELLO) with active per-symbol magic
   string handshake = StringFormat("{\\"action\\":\\"HELLO\\",\\"client\\":\\"PythonBridgeEA\\",\\"version\\":\\"3.10\\",\\"symbol\\":\\"%s\\",\\"account\\":%d,\\"magic\\":%d,\\"broker_utc_offset\\":%d}\\n",
                                   _Symbol, (int)AccountInfoInteger(ACCOUNT_LOGIN), g_active_magic, g_broker_utc_offset);
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
   g_bridge_state = BRIDGE_DISCONNECTED;
}"""

if old_ensure in content:
    content = content.replace(old_ensure, new_ensure)
    print("Replaced target 3 (EnsureConnection & DisconnectSocket)")
else:
    print("Failed to find target 3")

# 4. Update HandleMessage for quiet PING and throttled 1/min summary
old_handle = """void HandleMessage(const string message)
{
   PrintFormat("[PythonBridgeEA] [REQUEST] %s", message);
   g_last_heartbeat_time = TimeCurrent();
   g_watchdog_halted = false;

   string response = ProcessCommand(message);
   response += "\\n";

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
}"""

new_handle = """void HandleMessage(const string message)
{
   g_last_heartbeat_time = TimeCurrent();
   g_last_reply_time = TimeCurrent();
   g_bridge_state = BRIDGE_CONNECTED;
   g_watchdog_halted = false;

   bool is_ping = (StringFind(message, "\\"action\\":\\"PING\\"") >= 0 || StringFind(message, "\\"action\\": \\"PING\\"") >= 0);

   static datetime last_min_summary = 0;
   static int min_req_count = 0;
   static int min_ping_count = 0;

   if(is_ping)
   {
      min_ping_count++;
   }
   else
   {
      min_req_count++;
      PrintFormat("[PythonBridgeEA] [REQUEST] %s", message);
   }

   string response = ProcessCommand(message);
   response += "\\n";

   if(!is_ping)
   {
      PrintFormat("[PythonBridgeEA] [RESPONSE] %s", StringSubstr(response, 0, StringLen(response) - 1));
   }

   // 1-minute throttled heartbeat summary (Part 1 #4)
   if(TimeCurrent() - last_min_summary >= 60)
   {
      last_min_summary = TimeCurrent();
      PrintFormat("[PythonBridgeEA] [HEARTBEAT_SUMMARY] Symbol: %s (Magic %d) | State: CONNECTED | Last reply: 0s ago | Requests: %d | Pings: %d | Errors: %d",
                  _Symbol, g_active_magic, min_req_count, min_ping_count, g_consecutive_failures);
      min_req_count = 0;
      min_ping_count = 0;
   }

   uchar send_buffer[];
   StringToCharArray(response, send_buffer, 0, StringLen(response));
   int sent = SocketSend(g_socket, send_buffer, ArraySize(send_buffer));
   if(sent <= 0)
   {
      int err = GetLastError();
      PrintFormat("[PythonBridgeEA] [ERROR] SocketSend failed: %d (%s)",
                  err, GetSocketErrorName(err));
      DisconnectSocket();
   }
}"""

if old_handle in content:
    content = content.replace(old_handle, new_handle)
    print("Replaced target 4 (HandleMessage)")
else:
    print("Failed to find target 4")

with open("mql5/Experts/PythonBridgeEA.mq5", "w", encoding="utf-8") as f:
    f.write(content)
print("Finished patching PythonBridgeEA.mq5")
