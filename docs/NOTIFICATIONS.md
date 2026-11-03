# Notification Layer & Telegram Message Catalog

The MT5-OO1 Python SMC/ICT Engine uses a structured, three-tier notification layer (`src/notifications/`) that separates engine log lines from user-facing Telegram HTML cards.

---

## 1. Architecture & Safety Invariants

- **Typed Event Payloads (`src/notifications/events.py`)**: 28 strongly typed `@dataclass` payloads with `NotificationEventType` and `NotificationTier`.
- **Centralized HTML Templates (`src/notifications/templates/default_templates.py`)**: All user-facing wording lives in one module. Every card uses:
  - Emoji + bold title
  - 3–8 short lines of key facts with values in `<code>`
  - Consistent formatting: symbol digits for prices, thousands separators for money (`$1,250.00`), signed `R` multiples (`+2.40R`), percentages (`0.50%`), and plain-English MT5 retcodes (never raw error codes or stack traces)
  - Dual-timezone footer: `UTC` and configurable local display timezone (default `UTC+3` in `config/notifications.json`)
  - Automatic HTML escaping (`escape_html`) and safe line-boundary splitting at `4,096` characters (`split_message_safely`)
- **Zero Secrets / Masked Account Numbers**:
  - Account numbers are always masked to the last 4 digits (`****0001`).
  - `sanitize_sensitive_text()` strips any accidental bot tokens, API keys, or raw 8+ digit logins before any message leaves the process.

---

## 2. Three-Tier Noise Policy (`src/notifications/router.py`)

| Tier | Delivery Behavior | Events |
| :--- | :--- | :--- |
| **Tier 1 — `LOUD`** | Immediate push with sound (`disable_notification=False`) | `TradeSetup`, `TradeOpened`, `TargetHit`, `StopLossHit`, `OrderRejected`, `ConnectionLost`, `DailyLossLimitHit`, `DrawdownWarning`, `MarginWarning`, `BotStopped`, `UnauthorizedAccess`, and escalated `CRITICAL` alerts |
| **Tier 2 — `SILENT`** | Delivered silently (`disable_notification=True`) | `BreakEvenMoved`, `PartialClosed`, `SessionOpen`, `SessionClose`, `NewsBlackout`, `DailySummary`, `WeeklySummary`, `BotStarted`, `BotPaused`, `BotResumed`, `ConnectionRestored`, `SpreadWarning`, `DailyLossWarning`, `WarmupProgress`, `ConfigChanged`, `HealthOK` |
| **Tier 3 — `ON_DEMAND`** | Never pushed by default; written to activity logs and available via `/logs [N]` or `/last [N]` | Per-bar `NO SETUP` evaluations, `SetupSkipped` filter reasons, raw bar-by-bar diagnostics |

### Anti-Spam & State-Transition Rules
1. **No Per-Bar "NO SETUP" or 5/15-Minute Heartbeat Spam**:
   - Replaced with **one Pinned Status Card** edited in place (`update_pinned_status_card`), an optional periodic `HealthOK` ("running smoothly") check (default every `60` minutes), and an optional hourly Market-Watch digest (`/marketwatch on|off`).
2. **State-Transition Alerting (`OK -> WARNING -> CRITICAL -> RECOVERED`)**:
   - Alerts fire on state transitions, not on every tick or bar.
   - Identical repeats within the cooldown window (`alert_cooldown_seconds: 600`) are suppressed and counted (`Suppressed Alert Duplicates`, viewable in `/health`).
   - Repeated warnings merge their count (`"occurred N times in 10 min"`) and escalate to `CRITICAL` (`LOUD`) after reaching `escalation_repeat_threshold`.
3. **Single Warm-Up Progress Notification**:
   - `"Insufficient M15 candle history (have 0, required 20)"` emits **one** silent `WarmupProgress` message when warm-up begins and **one** `HISTORY READY` message once synchronized—never repeating on every bar.
4. **Quiet Hours (`config/notifications.json` & `/quiet <hours>`)**:
   - During quiet hours, non-critical `SILENT` notifications are held while critical alerts and live trade events still pass through.

---

## 3. Live Trade Card & Threading (Phase 3)

1. When a setup is detected, the bot posts a `TRADE SETUP` card and records its `message_id`.
2. As the trade progresses (`TradeOpened` $\to$ `BreakEvenMoved` $\to$ `PartialClosed` $\to$ `TargetHit` / `StopLossHit`), the bot:
   - **Edits the original trade card in place** to append a timestamped `📌 LIVE TRADE TIMELINE`.
   - **Threads follow-up messages** using `reply_to_message_id` pointing to the original setup message.
   - **Sends a separate `LOUD` notification** when the position closes (`TargetHit` or `StopLossHit`).
   - **Automatic Fallback**: If `editMessageText` fails (e.g., the user deleted the card or it is older than 48 hours), the router automatically posts a fresh replacement card and updates the tracked `message_id`.

---

## 4. Catalog of All 28 Event Templates

Use `/preview_alerts` in Telegram to receive all 28 sample cards live (each prefixed with `🧪 [SAMPLE]`).

1. **`TradeSetup` (`LOUD`)** — `🎯 TRADE SETUP — XAUUSD BUY` (Entry, SL/TP prices & pips, R:R, lots, `$` and `%` risk, session, HTF bias, score, reason)
2. **`TradeOpened` (`LOUD`)** — `⚡ ORDER FILLED — XAUUSD BUY` (Ticket, fill vs requested price, slippage in points, lots, risk, active SL/TP)
3. **`BreakEvenMoved` (`SILENT`)** — `🛡️ TRADE PROTECTED (BREAK-EVEN) — XAUUSD` (Ticket, old SL $\to$ new SL, locked profit, trigger R)
4. **`PartialClosed` (`SILENT`)** — `💰 PARTIAL PROFIT LOCKED — XAUUSD` (Ticket, closed vs remaining lots, realized P&L `$` and `R`)
5. **`TargetHit` (`LOUD`)** — `🎉 TARGET HIT — XAUUSD BUY` (Ticket, close price, realized P&L `$` and `R`, hold duration, daily running P&L)
6. **`StopLossHit` (`LOUD`)** — `🛑 STOP LOSS HIT — XAUUSD BUY` (Ticket, close price, loss `$` and `R`, hold duration, daily loss used vs cap)
7. **`OrderRejected` (`LOUD`)** — `🚫 ORDER REJECTED — XAUUSD BUY` (Plain-English broker reason mapped from MT5 retcode, bot action, open trades status)
8. **`SetupSkipped` (`ON_DEMAND`)** — `⏭️ SETUP SKIPPED — XAUUSD BUY` (Plain-English risk/filter reason, required vs current balance)
9. **`BotStarted` (`SILENT`)** — `🚀 TRADING BOT STARTED` (Account mode, profile, monitored symbols, active session, fast-mode state)
10. **`BotPaused` (`SILENT`)** — `⏸️ TRADING BOT PAUSED` (Reason, open positions management note)
11. **`BotResumed` (`SILENT`)** — `▶️ TRADING BOT RESUMED` (Active session, risk status)
12. **`BotStopped` (`LOUD`)** — `🛑 TRADING BOT STOPPED` (Reason, open positions count)
13. **`ConnectionLost` (`LOUD`)** — `🔌 MT5 BRIDGE CONNECTION LOST` (Last heartbeat age, auto-reconnect status, open positions safety note)
14. **`ConnectionRestored` (`SILENT`)** — `🟢 MT5 BRIDGE RECONNECTED` (Downtime duration, synchronized open positions)
15. **`SpreadWarning` (`SILENT`)** — `⚠️ SPREAD ELEVATED — XAUUSD` (Current vs normal spread, max cap, bot action)
16. **`DailyLossWarning` (`SILENT`)** — `⚠️ DAILY LOSS BUFFER WARNING` (Daily loss used `$` and `%`, remaining buffer before halt)
17. **`DailyLossLimitHit` (`LOUD`)** — `🚨 DAILY LOSS CAP REACHED — TRADING HALTED` (Daily loss `$` and `%` vs cap, next reset time)
18. **`DrawdownWarning` (`LOUD`)** — `🚨 DRAWDOWN GUARDRAIL ALERT` (Current equity vs peak equity, drawdown used vs max cap)
19. **`MarginWarning` (`LOUD`)** — `🚨 MARGIN LEVEL WARNING` (Current margin level `%`, free margin, entry floor vs emergency flatten floor)
20. **`NewsBlackout` (`SILENT`)** — `📰 HIGH-IMPACT NEWS WINDOW` (Event title, currency, blackout window, bot action)
21. **`SessionOpen` (`SILENT`)** — `🌅 SESSION OPEN — LONDON` (Window in UTC, primary focus instruments, volatility profile)
22. **`SessionClose` (`SILENT`)** — `🌙 SESSION CLOSE — LONDON` (Session trades, net session P&L `$` and `R`, next session)
23. **`DailySummary` (`SILENT`)** — `📊 DAILY PERFORMANCE SUMMARY` (Total trades, wins/losses, win rate, net P&L `$` and `R`, best/worst trade, max DD, ending balance/equity)
24. **`WeeklySummary` (`SILENT`)** — `📈 WEEKLY PERFORMANCE SUMMARY` (Total trades, win rate, weekly net P&L `$` and `R`, profit factor, best/worst trade, blocked setups count)
25. **`WarmupProgress` (`SILENT`)** — `⏳ WARMING UP HISTORY` / `✅ HISTORY READY` (Symbol, timeframe, loaded/required bars)
26. **`ConfigChanged` (`SILENT`)** — `⚙️ CONFIGURATION UPDATED` (Setting name, old $\to$ new value, operator source)
27. **`UnauthorizedAccess` (`LOUD`)** — `🔒 SECURITY ALERT — UNAUTHORIZED ACCESS BLOCKED` (Rejected command, sender name, masked chat ID)
28. **`HealthOK` (`SILENT`)** — `🟢 RUNNING SMOOTHLY — PERIODIC CHECK` (Bridge state, uptime, open positions, session, today's P&L)

---

## 5. Operator Commands & Inline Buttons

All commands and callback buttons enforce strict `TELEGRAM_CHAT_ID` verification.

- **Status Card Inline Buttons**: `⏸️ Pause` (`cmd_pause`), `▶️ Resume` (`cmd_resume`), `📊 Status` (`cmd_status`), `📋 Positions` (`cmd_positions`), and `🚨 Close All` (`cmd_closeall_prompt` $\to$ two-step confirmation `cmd_closeall_confirm` / `cmd_closeall_cancel`).
- **Commands**:
  - `/status` — Clean status card with inline action buttons
  - `/positions` — Open positions with entry, SL, TP, floating P&L, and risk (with automatic retry and friendly fallback)
  - `/today` — Today's performance summary card
  - `/week` — Weekly performance summary card
  - `/health` — Bridge status, outbound queue depth, `Suppressed Alert Duplicates` counter, quiet hours, and market-watch status
  - `/marketwatch on|off` — Toggle hourly market-watch digest
  - `/quiet <hours>` — Enable quiet hours for `<hours>` (`0` to disable)
  - `/logs [N]` or `/last [N]` — Show last `N` on-demand activity log lines
  - `/preview_alerts` — Send 1 sample of all 28 structured message types labelled `🧪 [SAMPLE]`
  - `/help` — Full categorized command reference
