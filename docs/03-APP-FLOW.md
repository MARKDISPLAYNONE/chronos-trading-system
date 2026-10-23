# Project Chronos — Application Flow Document

> **Audience note:** This document assumes zero prior context. It describes
> every operational flow in the system — what happens, in what order, and
> under what conditions — for both the bot engine and the dashboard. Read
> this after `01-PRD.md` (what/why) and `02-TRD.md` (tech stack/how), before
> touching any implementation detail in `06-IMPLEMENTATION-PLAN.md`.

## 1. Purpose of This Document

This document describes **behavioral flow**, not strategy rules (see PRD
Section 6 for exact rule logic) and not technical architecture (see TRD).
Think of this as: "if you pressed play on this system right now, what
exactly would happen, step by step, in every scenario?"

There are two major flows covered:
1. **Bot Engine Flow** — the autonomous trading process
2. **Dashboard User Flow** — what the Handler sees and does when using the
   dashboard

## 2. Bot Engine Flow — Full Lifecycle

### 2.1 Startup Sequence
START
│
▼
[1] Load environment variables (.env)
│ - MT5 login credentials
│ - Supabase connection credentials
│ - Instrument symbol name
│
▼
[2] Connect to MT5 terminal
│ - If connection fails → log critical error, halt, alert (see Section 5)
│ - If successful → proceed
│
▼
[3] Verify connection to Supabase database
│ - If connection fails → log critical error locally (file-based
│ fallback, since DB is unreachable), halt, alert
│ - If successful → proceed
│
▼
[4] Verify instrument symbol exists and is tradeable on the connected
account
│ - If symbol not found → log critical error, halt, alert
│ - If found → record tick size, point value for this session
│
▼
[5] Enter main monitoring loop (Section 2.2)

text


### 2.2 Main Monitoring Loop (runs continuously while bot is active)
LOOP (checks time every N seconds — exact interval set in implementation,
should be frequent enough not to miss a window boundary, e.g., every 1-5 sec)
│
▼
[1] Get current time in America/New_York timezone (DST-aware)
│
▼
[2] Is today Monday–Friday?
│ NO → sleep, loop again (no action on weekends)
│ YES → continue
│
▼
[3] Does current time fall within the START of one of the 21 defined
windows (i.e., time == HH:45 for a valid HH)?
│ NO → sleep, loop again
│ YES → begin Window Evaluation Flow (Section 2.3) for this window

text


**Important flow note:** The bot does not "wake up" only at window start
times — it continuously monitors time and price, and specifically begins
tracking a NEW window's Candle A the moment a window's start time is
reached. Multiple windows are never active simultaneously (confirmed by the
fixed, non-overlapping window list in PRD 6.1).

### 2.3 Window Evaluation Flow (runs once per active window, i.e., up to 21
times per trading day)
WINDOW EVALUATION START (triggered at window's HH:45 mark)
│
▼
[1] Begin tracking Candle A (HH:45–HH:00+1, 15-minute candle)
│ - Record open, high, low, close as the candle forms
│ - Wait for candle close (i.e., wait until HH:00+1 is reached)
│
▼
[2] Candle A closed → finalize High_A, Low_A, Mid_A, Range
│
▼
[3] Begin tracking Candle B (HH:00+1–HH:15+1)
│ - Capture OPEN price the instant this candle begins
│
▼
[4] Classify context using Candle B's open vs. Mid_A/High_A/Low_A
│ (exact thresholds per PRD 6.4)
│
├── VOID → [a] Write NO_SETUP record to database immediately
│ [b] Reason: "invalid open location"
│ [c] END this window's evaluation — no further monitoring
│ needed for this window
│
└── PREMIUM or DISCOUNT → continue to [5]
│
▼
[5] Monitor intrabar price action (1-min or tick data) for sweep +
reversal, per PRD 6.5, until Candle B closes (HH:15+1)
│
├── Window closes, no sweep occurred →
│ [a] Write NO_SETUP record
│ [b] Reason: "no sweep"
│ [c] END
│
├── Window closes, sweep occurred but no reversal →
│ [a] Write NO_SETUP record
│ [b] Reason: "sweep not reversed"
│ [c] END
│
└── Sweep AND reversal both occur before window close →
[a] This is now a CONFIRMED SETUP
[b] Proceed immediately to Trade Execution Flow (Section 2.4)

text


**Critical timing note:** The reversal trigger IS the entry signal — there
is no delay between confirming reversal and submitting the order. This must
happen as close to instantaneously as the system allows (see PRD 6.6 on
spread handling — the order fills at market regardless of spread cost at
that moment).

### 2.4 Trade Execution Flow
TRADE EXECUTION START (triggered the instant reversal is confirmed)
│
▼
[1] Calculate exact entry reference price (High_A - 1 tick for short,
Low_A + 1 tick for long)
│
▼
[2] Calculate SL and TP per PRD 6.7 (based on Candle A's range)
│
▼
[3] Calculate the partial-exit trigger price as a RAW PRICE DISTANCE
(no unit conversion of any kind — see PRD §6.8):
      long:  trigger = entry + 0.80 × (TP − entry)
      short: trigger = entry − 0.80 × (entry − TP)
    (Handle is a display-only unit: 1 handle = 10 ticks for NAS100,
     derived at runtime as 1 / tick_size. It is never used in trigger
     calculations — see PRD §6.8.)
│
▼
[4] Submit market order for 10 contracts, with SL and TP attached
│ - If order submission fails (e.g., rejected by broker) → log as a
│ special outcome: NOT a NO_SETUP (the setup WAS valid), but a
│ distinct "EXECUTION_FAILED" status with the broker's rejection
│ reason — this must never be silently dropped or miscategorized
│ as NO_SETUP
│ - If order succeeds → continue to [5]
│
▼
[5] Enter Trade Management Loop (Section 2.5) — monitor this open
position until it is fully closed

text


### 2.5 Trade Management Loop (runs for the lifetime of an open position)
TRADE MANAGEMENT LOOP
│
▼
[1] Monitor current price relative to the pre-calculated 80%-to-TP level
(Section 2.4, step 3)
│
├── Price has NOT yet reached 80% level, and SL or TP has not been
│ hit → continue monitoring (loop)
│
├── Price reaches the 80%-to-TP level FIRST (before SL or TP) →
│ [a] Close 9 of 10 contracts immediately (partial exit)
│ [b] Move SL on remaining 1 contract to breakeven (entry price)
│ [c] Continue monitoring the remaining 1 contract until it
│ closes (either breakeven stop or original TP)
│ [d] Once remaining contract closes → proceed to [2]
│
├── Original SL is hit BEFORE reaching 80% level (full 10 contracts
│ still open) →
│ [a] Full position closes at SL
│ [b] Proceed to [2] — this is a LOSS outcome
│
└── Original TP is hit directly (rare if 80% level is reached first,
but must be handled in case price gaps or moves too fast for the
80% check to trigger first) →
[a] Full position closes at TP
[b] Proceed to [2] — this is a WIN outcome
│
▼
[2] Trade fully closed — calculate final outcome:
│ - WIN: net profitable result (includes partial-exit scenarios where
│ the 9-contract partial was profitable, even if the final 1
│ contract scratched at breakeven)
│ - LOSS: full SL hit with no partial exit having occurred
│ - Record exact entry price, SL price, TP price, final exit price(s),
│ R-multiple or $ result, and whether partial/breakeven was
│ triggered
│
▼
[3] Write complete trade record to database (see 05-DATABASE-SCHEMA.md
for exact fields)
│
▼
[4] Return to Main Monitoring Loop (Section 2.2) — await next window

text


## 3. Daily Summary Flow (End-of-Day Processing)
END OF TRADING DAY (after last window, 22:45-23:15, has fully resolved)
│
▼
[1] Confirm all 21 windows for the day have a logged outcome
│ - If any window is missing a record (bot downtime, crash, etc.) →
│ this must be flagged, not silently ignored. Log a
│ "MISSING_EVALUATION" record with reason "bot unavailable during
│ window" so the daily record stays complete and honest
│
▼
[2] (Optional, future enhancement — not required in early phases) Trigger
any daily aggregate calculations if the dashboard requires
pre-computed daily summaries rather than computing on-the-fly from raw
records

text


**Note for incoming devs:** In early phases, the dashboard can compute all
summaries on-the-fly from raw window/trade records — there is no strict
requirement for a separate daily aggregation job unless performance becomes
an issue at scale. This is explicitly deferred, not forgotten.

## 4. Dashboard User Flow

### 4.1 Access Flow
HANDLER OPENS DASHBOARD URL
│
▼
[1] Not authenticated? → Redirect to login screen (Supabase Auth)
│
▼
[2] Handler logs in with credentials
│
▼
[3] Authenticated → redirect to main dashboard view

text


### 4.2 Main Dashboard View Flow
MAIN DASHBOARD LOADS
│
▼
[1] Query Supabase for summary metrics (date range: default = current
week, adjustable by Handler)
│
▼
[2] Display:
│ - Overall win rate, total R-multiple/net result for selected period
│ - Equity curve chart
│ - NO_SETUP breakdown (count by reason)
│ - Per-window-time performance table (which of the 21 slots perform
│ best/worst)
│
▼
[3] Handler can adjust date range filter → triggers re-query, re-render
│
▼
[4] Handler can click into "Trade Log" view for full trade-by-trade detail

text


### 4.3 Trade Log View Flow
HANDLER NAVIGATES TO TRADE LOG VIEW
│
▼
[1] Query Supabase for all trade records (and/or NO_SETUP records,
toggleable) within selected date range
│
▼
[2] Display as sortable/filterable table:
│ - Date, window, context, outcome, reason, entry/SL/TP/exit prices,
│ R-multiple, partial-exit flag
│
▼
[3] Handler can filter by: outcome type (WIN/LOSS/NO_SETUP), specific
reason, date range, window time slot
│
▼
[4] (Read-only — no edit/delete/manual trade entry in this phase, per PRD
Section 7)

text


## 5. Error & Exception Flows

These flows describe what happens when things go wrong — critical for an
autonomous system, since there is no human watching every window in real
time.

### 5.1 Bot Connectivity Failures

| Scenario | Flow |
|---|---|
| MT5 terminal disconnects mid-session | Bot attempts reconnection on a defined retry schedule (exact interval set in implementation); if reconnection fails repeatedly, bot logs a critical local error and halts rather than operating blind |
| Supabase unreachable during a window evaluation | Bot falls back to writing the outcome to a local log file (so no data is permanently lost), and attempts to sync to Supabase once connectivity is restored |
| Bot process crashes entirely | Any window that was mid-evaluation at crash time will be missing a record; this is caught by the Daily Summary Flow (Section 3) which flags missing evaluations explicitly |

### 5.2 Order Execution Failures

| Scenario | Flow |
|---|---|
| Broker rejects the market order (e.g., insufficient margin, market closed, symbol restriction) | Logged as `EXECUTION_FAILED` with broker's exact rejection message — never miscategorized as `NO_SETUP`, since the setup WAS valid; this distinction matters for strategy evaluation accuracy |
| Partial exit order fails (e.g., broker rejects the 9-contract close) | Bot retries per a defined retry policy (to be specified in implementation); if retries fail, this is logged as a distinct critical event requiring Handler attention — the position is now in an unmanaged state and must not be left silently unmonitored |
| SL/TP modification fails (e.g., moving SL to breakeven after partial) | Same as above — critical event, flagged, not silently ignored |

### 5.3 Alerting (High-Level Requirement)

For any critical failure (connectivity loss, execution failure, unmanaged
position state), the bot must produce a clearly visible alert. Exact
alerting mechanism (email, log file the Handler checks, dashboard banner,
etc.) is to be decided during Implementation Planning — but the REQUIREMENT
that such failures are never silent is fixed here and must not be dropped
during implementation.

## 6. Flow Summary Diagram (Full System, Simplified)
text

                     ┌─────────────────────────┐
                     │   Bot Engine (Python)     │
                     │   runs continuously        │
                     │   Mon-Fri, NY hours        │
                     └────────────┬──────────────┘
                                  │
               evaluates each of 21 windows/day
                                  │
             ┌────────────────────┼────────────────────┐
             │                                          │
     NO_SETUP outcome                          CONFIRMED SETUP
             │                                          │
             │                                 Trade Execution Flow
             │                                          │
             │                                 Trade Management Loop
             │                                          │
             │                                 Trade fully closes
             │                                          │
             └──────────────┬───────────────────────────┘
                             │
                    Write record to Supabase
                             │
                             ▼
                ┌─────────────────────────┐
                │   Dashboard (Next.js)     │
                │   Handler views metrics   │
                │   whenever they choose    │
                └─────────────────────────┘
text


## 7. Open Items / Unresolved Flow Questions

To be resolved during Implementation Planning:

- Exact retry policy/backoff timing for MT5 reconnection attempts
- Exact retry policy for failed partial-exit or SL-modification orders
- Final decision on alerting mechanism for critical failures (email vs.
  dashboard banner vs. log file vs. combination)
- Exact polling interval for the Main Monitoring Loop (balance between
  responsiveness and resource usage)
- Whether a separate daily aggregation job is needed early, or on-the-fly
  dashboard queries are sufficient at current data volume