# Project Chronos — Product Requirements Document (PRD)

## 1. Overview

Project Chronos is an autonomous trading system that executes a time-window-based,
liquidity-sweep trading strategy on an index CFD/futures instrument, with a
metrics dashboard for performance tracking and decision support. The system is
built entirely on free-tier tools and is designed for a single user (the
Handler/strategy owner), progressing from local logic validation to a fully
autonomous live-capable bot.

This is not a general-purpose trading platform. It is a purpose-built
implementation of one specific, well-defined strategy ("Zeussy Macro Time"
window model), with the dashboard serving as an analytics/decision-support
layer, not a discretionary trading tool.

## 2. Problem Statement

The strategy owner currently has a manually-identified trading edge based on
fixed daily time windows and price-action rules (premium/discount context +
liquidity sweep + reversal). Manually monitoring 21 time windows per day,
across a 5-day week, with precise tick-level entry/exit conditions, is not
practically executable by a human with consistency or accuracy. There is also
no existing system to:

- Execute the rules with exact, repeatable precision (no emotional
  interference, no missed windows, no hesitation on entries)
- Log every window's outcome (traded or not) with a clear reason, to build a
  real evidence base for refining the strategy over time
- Visualize performance metrics to support go/no-go decisions on scaling or
  adjusting the strategy

## 3. Goals & Objectives

1. **Encode the strategy exactly as specified** (see Section 6) with zero
   deviation or silent assumption-making.
2. **Validate the logic** against historical price data before any capital
   (real or demo) is at risk.
3. **Forward-test autonomously** on a demo account with no manual
   intervention required once running.
4. **Provide full transparency** via logging: every one of the 21 daily
   windows must produce a traceable outcome and reason, win/loss/no-trade.
5. **Provide a metrics dashboard** so the strategy owner can assess
   performance (win rate, R-multiple, drawdown, per-window-time performance
   breakdown, etc.) without reading raw logs.
6. **Build for a safe, controlled path to live trading** — demo and live
   share identical logic, differing only in account credentials/flag.
7. **Use only free-tier tools** throughout — no paid infrastructure
   dependency at any phase described in this document.
8. **Operate autonomously** — once running, the system requires no manual
   input to detect windows, evaluate setups, execute trades, or log outcomes.

## 4. Target User

Single user: the strategy owner (Handler). This is not a multi-tenant SaaS
product. Any multi-user patterns (auth, RLS) exist for structural soundness
and future extensibility, not because multiple users are expected.

## 5. Scope

### In Scope
- Strategy logic engine (Python) implementing the full rule set (Section 6)
- Historical tick-data-based logic validation (no live capital)
- Live/demo market data ingestion via MetaTrader 5
- Autonomous trade execution (market orders, SL/TP, partial exit) on MT5 demo
  account, with a clear, explicit switch to live later
- Persistent storage of every window's evaluation outcome and every trade's
  full detail
- A web-based metrics dashboard (read-only, single-user, authenticated)
- Session-based documentation and handover process for continued development

### Out of Scope (for now)
- Multi-instrument support (initially built for one instrument only —
  confirmed CFD/index instrument on the MT5 demo account)
- Multi-user accounts, billing, or any SaaS-style commercialization
- Discretionary override controls in the dashboard (no manual trade
  placement via the UI in this phase)
- Mobile app (web dashboard only, responsive design acceptable but not a
  dedicated app)
- Machine learning / adaptive strategy logic — rules are fixed and
  rule-based as specified, not self-optimizing

## 6. Strategy Requirements (Authoritative Specification)

This section is the single source of truth for strategy logic. Any
ambiguity must be escalated (Handler → Senior Dev) before implementation —
never assumed.

### 6.1 Schedule
- Active Monday–Friday only, New York local time (America/New_York,
  DST-aware)
- 21 fixed daily time windows, each spanning HH:45 to (HH+1):15 (30 minutes)
- Windows explicitly skipped: 15:45–16:15, 16:45–17:15, 17:45–18:15
  (intentional gap)
- Full active window list:
  `23:45–00:15, 00:45–01:15, 01:45–02:15, 02:45–03:15, 03:45–04:15,
  04:45–05:15, 05:45–06:15, 06:45–07:15, 07:45–08:15, 08:45–09:15,
  09:45–10:15, 10:45–11:15, 11:45–12:15, 12:45–13:15, 13:45–14:15,
  14:45–15:15, 18:45–19:15, 19:45–20:15, 20:45–21:15, 21:45–22:15,
  22:45–23:15`
- Two windows cross midnight ET (23:45–00:15 and 22:45–23:15's following
  window) — date-tagging for logging must handle this explicitly (trading
  day = the date on which the window STARTS, unless otherwise decided in
  implementation planning)

### 6.2 Reference Candle (Candle A)
- The 15-minute candle from HH:45 to (HH+1):00
- Record: `High_A`, `Low_A`, `Mid_A = (High_A + Low_A) / 2`
- Range = `High_A - Low_A`, used for SL calculation

### 6.3 Signal Candle (Candle B)
- The following 15-minute candle, (HH+1):00 to (HH+1):15
- Its OPEN price determines context (Section 6.4)
- Price action within this candle (via 1-minute or tick data) determines
  sweep + reversal (Section 6.5)

### 6.4 Context Classification (based on Candle B's open price only)
- `open` strictly between `Mid_A` and `High_A` → **PREMIUM** context →
  SHORT setups only
- `open` strictly between `Low_A` and `Mid_A` → **DISCOUNT** context →
  LONG setups only
- `open == Mid_A`, or `open >= High_A`, or `open <= Low_A` → **VOID**,
  tag `NO_SETUP`, reason: `"invalid open location"`

### 6.5 Sweep + Reversal Trigger (entry condition)
Must complete before Candle B closes (at :15). Evaluated using intrabar
(1-minute or tick) data. **Sequence of unrelated price movement does not
invalidate the setup** — only whether the correct level is swept and
reversed before window close matters. Movement toward the opposite level
is explicitly ignored (neither confirms nor invalidates).

**PREMIUM (short) case:**
- Sweep: price trades to `High_A + 1 tick` or beyond, at any point in the
  window
- Reversal (= entry trigger): price subsequently trades back down to
  `High_A - 1 tick` or lower
- If sweep does not occur by window close → `NO_SETUP`, reason: `"no sweep"`
- If sweep occurs but reversal does not complete by window close →
  `NO_SETUP`, reason: `"sweep not reversed"`

**DISCOUNT (long) case — mirrored:**
- Sweep: price trades to `Low_A - 1 tick` or beyond
- Reversal (= entry trigger): price trades back up to `Low_A + 1 tick` or
  higher
- Same `NO_SETUP` reasons, mirrored, if incomplete

### 6.6 Entry Execution
- Market order, submitted immediately on reversal trigger
- Spread must not block or delay entry — order fills at market regardless
  of spread cost at that moment
- Short entry price reference: `High_A - 1 tick`
- Long entry price reference: `Low_A + 1 tick`

### 6.7 Stop Loss & Take Profit (Fib-Based, off Candle A Range)

- **Short:** SL = High_A + (0.5 × Range); TP = Mid_A − 1 tick
- **Long:** SL = Low_A − (0.5 × Range); TP = Mid_A + 1 tick
- SL/TP are never adjusted after entry, except per the partial-exit rule in 6.8.
- All values are raw, exact prices computed from Candle A's High_A, Low_A, Mid_A, and Range. No unit conversion of any kind occurs in this calculation.

### 6.8 Position Sizing & Partial Exit

- **Position:** 10 contracts total (MT5 `volume` field = `10.0`, confirmed against this account's 0.1 lot step).

- **Partial-exit trigger (raw price distance — no handle/tick conversion in the trigger logic):**
Long: trigger_price = entry + 0.80 × (take_profit − entry)
Short: trigger_price = entry − 0.80 × (entry − take_profit)

text


When price reaches `trigger_price` favorably (before TP, before SL): close 9 of the 10 contracts, move SL on the remaining 1 contract to breakeven (entry price). No other position adjustments occur. This is the only exception to "SL/TP never adjusted" in 6.7.

- **"Handle" is a display/reporting unit only, never a calculation input.** For NAS100, confirmed empirically and from instrument spec:
1 handle = 1.0 NAS100 price point = 10 ticks (tick_size = 0.1)
handle_size = 1.0 / instrument.tick_size

text


This is derived at runtime from the `instruments` table's `tick_size` field — never hardcoded as a fixed tick count — so the dashboard/logs can report "moved X handles" in human-readable form without that number ever feeding back into the trigger calculation above.

- **Historical note (do not reintroduce):** an earlier draft of this section referenced "1 handle = 4 ticks" as part of the trigger calculation itself. That value was never derived from the confirmed instrument spec (tick_size 0.1 → 10 ticks/handle) and was never used to place a real trade. It has been fully superseded by the raw-price-distance formula above, which removes "handle" from the calculation path entirely.

### 6.9 Edge Cases (must produce explicit tagged outcomes, never silent skips)
1. No sweep by window close → `NO_SETUP`, reason: `"no sweep"`
2. Sweep occurs, no reversal by window close → `NO_SETUP`, reason:
   `"sweep not reversed"`
3. Candle B open at `Mid_A` or outside `[Low_A, High_A]` → `NO_SETUP`,
   reason: `"invalid open location"`
4. Wrong-direction price movement (opposite level tapped) is explicitly
   ignored — does not confirm or invalidate the setup either way

### 6.10 Analytics / Logging Requirement
For **every one of the 21 daily windows**, every trading day, log:
- Date, window identifier (e.g., `"01:45-02:15"`)
- Context: `PREMIUM` / `DISCOUNT` / `VOID`
- Outcome: `WIN` / `LOSS` / `NO_SETUP`
- Specific reason (exact rule/condition that determined the outcome)
- If traded: entry price, SL price, TP price, exit price, R-multiple or
  $ result, whether partial/breakeven was triggered

This logging is a core product requirement, not a nice-to-have — the
strategy owner's primary goal is to understand WHY outcomes occurred, to
refine risk management and conviction over time.

## 7. Dashboard Requirements (high-level — full detail in Design Brief)

- Authenticated, single-user access
- Daily/weekly/monthly performance summary (win rate, R-multiple, net
  result, number of NO_SETUP windows vs traded windows)
- Per-window-time breakdown (e.g., "which of the 21 time slots perform
  best/worst")
- Trade log table (filterable/sortable) with full trade detail per
  Section 6.10
- NO_SETUP reason breakdown (which rule most often blocks trades — useful
  for strategy refinement)
- Equity curve visualization
- Read-only in this phase — no manual trade placement from the dashboard

## 8. Success Criteria

- Strategy logic runs against historical data with zero logic bugs
  (validated outcomes match manual rule tracing on sample windows)
- System runs autonomously on MT5 demo for a sustained period with no
  manual intervention required for window detection, execution, or logging
- Every single daily window (21/day) produces a logged outcome with a
  correct, specific reason — zero silent/unexplained skips
- Dashboard accurately reflects logged data with no discrepancies
- Entire system built and operated using only free-tier tools/services
- Clear, working path exists to flip from demo to live with no logic
  rewrite

## 9. Constraints & Assumptions

- `MetaTrader5` Python package is Windows-only; local execution (Phase
  1–3) assumes a Windows machine is available and running. A cloud-based
  execution path (e.g., MetaApi.cloud free tier) is the planned upgrade
  for true 24/7 autonomy without a dedicated local machine (see TRD and
  Phase Plan).
- MT5 demo account access is already confirmed (via WebTrader).
- All free-tier service limits (Supabase, Vercel, GitHub Actions, MetaApi,
  etc.) must be respected; no paid upgrades without explicit approval
  flagged through the Handler.
- Historical tick-level or 1-minute data availability for the chosen
  instrument must be confirmed before Phase 1 logic validation begins.

## 10. Open Items (to resolve during Implementation Planning)
- Final confirmation of the exact instrument to trade on the MT5 demo
  account (symbol name, tick size, point value — must match or be
  explicitly reconciled against the original NAS100 CFD specification)
- Exact trading-day date-tagging convention for the two midnight-crossing
  windows