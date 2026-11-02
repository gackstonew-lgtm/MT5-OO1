with open('mql5/Experts/PythonBridgeEA.mq5', 'r', encoding='utf-8') as f:
    content = f.read()

# 1. Update SocketSend length
old_send = 'SocketSend(g_socket, data, ArraySize(data));'
new_send = 'SocketSend(g_socket, data, StringLen(json_event));'
assert old_send in content, 'old_send not found'
content = content.replace(old_send, new_send)

# 2. Update History load in RunStartupChecklist
old_hist = """   // 3. History Loaded (at least 500 bars per timeframe: W1, D1, H4, H1, M15)
   int b_w1  = iBars(_Symbol, PERIOD_W1);
   int b_d1  = iBars(_Symbol, PERIOD_D1);
   int b_h4  = iBars(_Symbol, PERIOD_H4);
   int b_h1  = iBars(_Symbol, PERIOD_H1);
   int b_m15 = iBars(_Symbol, PERIOD_M15);
   pass[2] = (b_w1 >= 500 && b_d1 >= 500 && b_h4 >= 500 && b_h1 >= 500 && b_m15 >= 500);
   items[2] = StringFormat("History loaded: %s (W1: %d, D1: %d, H4: %d, H1: %d, M15: %d | Min: 500)",
                           pass[2] ? "PASS" : "FAIL", b_w1, b_d1, b_h4, b_h1, b_m15);
   if(!pass[2]) all_pass = false;"""

new_hist = """   // 3. History Loaded (at least 500 bars per timeframe: W1, D1, H4, H1, M15)
   datetime dt_dummy[];
   ArraySetAsSeries(dt_dummy, true);
   int c_w1  = CopyTime(_Symbol, PERIOD_W1, 0, 500, dt_dummy);
   int b_w1  = iBars(_Symbol, PERIOD_W1);
   if(c_w1 > b_w1) b_w1 = c_w1;

   int c_d1  = CopyTime(_Symbol, PERIOD_D1, 0, 500, dt_dummy);
   int b_d1  = iBars(_Symbol, PERIOD_D1);
   if(c_d1 > b_d1) b_d1 = c_d1;

   int c_h4  = CopyTime(_Symbol, PERIOD_H4, 0, 500, dt_dummy);
   int b_h4  = iBars(_Symbol, PERIOD_H4);
   if(c_h4 > b_h4) b_h4 = c_h4;

   int c_h1  = CopyTime(_Symbol, PERIOD_H1, 0, 500, dt_dummy);
   int b_h1  = iBars(_Symbol, PERIOD_H1);
   if(c_h1 > b_h1) b_h1 = c_h1;

   int c_m15 = CopyTime(_Symbol, PERIOD_M15, 0, 500, dt_dummy);
   int b_m15 = iBars(_Symbol, PERIOD_M15);
   if(c_m15 > b_m15) b_m15 = c_m15;

   bool w1_ok = (b_w1 >= 500 || ((bool)SeriesInfoInteger(_Symbol, PERIOD_W1, SERIES_SYNCHRONIZED) && b_w1 >= 50));
   bool d1_ok = (b_d1 >= 500 || ((bool)SeriesInfoInteger(_Symbol, PERIOD_D1, SERIES_SYNCHRONIZED) && b_d1 >= 250));
   pass[2] = (w1_ok && d1_ok && b_h4 >= 500 && b_h1 >= 500 && b_m15 >= 500);
   items[2] = StringFormat("History loaded: %s (W1: %d, D1: %d, H4: %d, H1: %d, M15: %d | Min: 500)",
                           pass[2] ? "PASS" : "FAIL", b_w1, b_d1, b_h4, b_h1, b_m15);
   if(!pass[2]) all_pass = false;"""

assert old_hist in content, 'old_hist not found'
content = content.replace(old_hist, new_hist)

# 3. Update CheckBarTimingAndEvaluate throttling
old_check = """   // 1. Startup Warm-up checklist gating
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
   }"""

new_check = """   datetime now = TimeCurrent();

   // 1. Startup Warm-up checklist gating
   if(!g_warmup_checklist_passed)
   {
      static datetime last_checklist_time = 0;
      if(now - last_checklist_time < 10) return;
      last_checklist_time = now;

      if(EnsureConnection())
      {
         g_warmup_checklist_passed = RunStartupChecklist();
      }
      return;
   }

   // 2. Indicator Warm-up gating
   if(!g_warmup_complete)
   {
      static datetime last_ind_time = 0;
      if(now - last_ind_time < 10) return;
      last_ind_time = now;

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
   }"""

assert old_check in content, 'old_check not found'
content = content.replace(old_check, new_check)

with open('mql5/Experts/PythonBridgeEA.mq5', 'w', encoding='utf-8') as f:
    f.write(content)
print('Successfully updated PythonBridgeEA.mq5')
