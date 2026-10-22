# Project Chronos — Phase Plan

> **Audience note:** This document assumes zero prior context. It defines
> the macro-level phases of this project — the big gates that determine
> "are we allowed to move forward with more risk/responsibility yet?" This
> is distinct from `06-IMPLEMENTATION-PLAN.md`, which covers granular build
> steps WITHIN these phases. Read this after all prior docs. This document
> is also the primary reference for "what phase are we in right now?" —
> which should always be reflected in `00-HANDOVER.md`.

## 1. Purpose of This Document

Project Chronos involves progressively increasing levels of real-world
consequence: from "code that reads historical data" (zero risk) to "code
that places real orders with real money" (full risk). This document defines
**hard gates** between phases — conditions that MUST be met before
advancing — so that risk is only taken on deliberately, never by accident
or momentum.

**Golden rule: no phase may be skipped, and no phase may begin before the
previous phase's exit criteria are explicitly confirmed met.** This applies
equally to the Agent, the Senior Dev, and the Handler — nobody fast-tracks
this, including under time pressure or excitement about progress.

## 2. Phase Overview (Summary Table)

| Phase | Name | Risk Level | Real Money? | Core Question Answered |
|---|---|---|---|---|
| 0 | Documentation & Foundation | None | No | "Do we have a complete, unambiguous spec and environment before writing any logic?" |
| 1 | Logic Validation (Historical) | None | No | "Does the code correctly implement the strategy rules, proven against historical data?" |
| 2 | Live Dry-Run (Forward Test, No Orders) | None (data only) | No | "Does the logic behave correctly against REAL-TIME data, including timing/connectivity realities historical replay can't test?" |
| 3 | Demo Execution | None (simulated money) | No (demo account) | "Does real order execution — fills, partials, breakeven, SL/TP — work correctly against actual broker behavior?" |
| 4 | Live Execution | Real | **Yes** | "Are we deliberately and explicitly ready to risk real capital?" |

## 3. Phase 0 — Documentation & Foundation

### Objective
Establish a complete, unambiguous specification and development
environment before any strategy logic is written.

### Entry Criteria
- Project kickoff (this is the starting phase — no prior phase required)

### Activities
- Produce all 8 core documents (this document is part of that set)
- Set up repository, folder structure, environment variable templates
- Confirm MT5 demo account access
- Confirm GitHub repository connected and functioning

### Exit Criteria (ALL must be true to advance to Phase 1)
- [ ] All 8 documents (`00` through `07`) exist and are complete — no
      placeholder/TBD content in core requirement sections (open items
      lists are acceptable and expected, but they must be scoped/tracked,
      not silently ignored)
- [ ] Repository is initialized, connected to GitHub remote, folder
      structure per `06-IMPLEMENTATION-PLAN.md` Section 2 exists
- [ ] `.gitignore` confirmed working (no secrets ever committed)
- [ ] MT5 demo account confirmed accessible
- [ ] Exact instrument symbol, tick size, and point value confirmed
      directly from the MT5 demo account (resolving the open item
      referenced across TRD, Schema, and Implementation docs)
- [ ] Source of historical 1-minute/tick data for Phase 1 confirmed and
      accessible (Dukascopy export, MT5 historical export, or equivalent)

### What Is Explicitly NOT Allowed in This Phase
- No strategy logic code
- No MT5 connection code beyond confirming access/credentials work
- No real or demo orders of any kind

## 4. Phase 1 — Logic Validation (Historical)

### Objective
Prove, with zero live risk, that the coded strategy logic exactly matches
the specification in `01-PRD.md` Section 6, using historical data.

### Entry Criteria
- Phase 0 exit criteria fully met

### Activities
- Implementation Plan Steps 3 through 7 (core utilities through historical
  validation run)
- Manual hand-tracing and verification of logic against real historical
  windows, per the verification steps defined in each Implementation Plan
  step

### Exit Criteria (ALL must be true to advance to Phase 2)
- [ ] All unit tests (time windows, context classification, sweep/
      reversal detection, trade calculation) pass
- [ ] Historical validation run (Implementation Plan Step 7) completed
      across a meaningful sample period (minimum several weeks of data,
      ideally spanning different market conditions)
- [ ] Handler and/or Senior Dev have manually spot-checked a reasonable
      sample (15-20+ windows) of the validation output against hand-
      calculated expectations, with zero discrepancies found (or all
      discrepancies resolved and re-validated)
- [ ] Specific confirmation that DST transition handling has been tested
      and is correct (per TRD Section 11)
- [ ] Specific confirmation that both midnight-crossing windows have been
      tested and date-tagged correctly per the Database Schema Section 7
      convention
- [ ] **Explicit Handler sign-off** that the logic is trusted — this is a
      judgment call, not just a checklist, since this logic underpins
      every later phase

### What Is Explicitly NOT Allowed in This Phase
- No live or demo order execution code is activated (dry-run/calculation-
  only is fine; actual `order_send()` calls to MT5 must not run)
- No connection to a live account under any circumstance

## 5. Phase 2 — Live Dry-Run (Forward Test, No Orders)

### Objective
Prove the system behaves correctly against REAL-TIME, live market data and
a live MT5 connection — catching issues that historical replay cannot
reveal, such as connectivity handling, timing precision, and real-time data
feed quirks — while still taking zero risk (no orders placed).

### Entry Criteria
- Phase 1 exit criteria fully met

### Activities
- Implementation Plan Steps 8 (dry-run mode only) through 11, run against
  the MT5 demo account's LIVE data feed, with `order_executor.py` kept in
  dry-run mode throughout this phase (logs intended orders, places none)
- Run continuously for a sustained period (minimum one full trading week
  recommended) to observe real-time behavior across all 21 daily windows,
  multiple days, and ideally a weekend boundary (confirming the bot
  correctly does nothing Saturday/Sunday)
- Handler independently observes a sample of windows live (e.g., watching
  the MT5/TradingView chart manually during a few windows) and compares
  against what the bot logged, to catch any live-data-handling
  discrepancies not visible in historical replay

### Exit Criteria (ALL must be true to advance to Phase 3)
- [ ] Bot has run continuously for the minimum sustained period with no
      crashes requiring manual restart (brief, logged reconnections are
      acceptable; silent failures are not)
- [ ] All 21 daily windows produced exactly one logged outcome per day,
      every day, with zero `MISSING_EVALUATION` records (or any that did
      occur are understood, explained, and resolved)
- [ ] Handler's manual spot-check observations match the bot's logged
      outcomes with zero discrepancies
- [ ] Any `system_events` records generated during this phase have been
      reviewed and resolved/understood (per Database Schema Section 4.4)
- [ ] **Explicit Handler sign-off** that live, real-time behavior is
      trusted

### What Is Explicitly NOT Allowed in This Phase
- `order_executor.py` must remain in dry-run mode — zero real or demo
  orders placed
- No live account connection under any circumstance

## 6. Phase 3 — Demo Execution

### Objective
Prove that real order execution — fills, spread handling, partial exits,
breakeven stop management, SL/TP behavior — works correctly against actual
broker execution behavior (which can differ meaningfully from pure
calculation/simulation), using the MT5 **demo account only**.

### Entry Criteria
- Phase 2 exit criteria fully met

### Activities
- Implementation Plan Steps 8 (full, real execution) through 11, with
  `order_executor.py` switched OFF dry-run mode, `ENVIRONMENT=demo`
- Run continuously for a sustained period (minimum several weeks
  recommended, ideally covering enough confirmed setups to observe at
  least a handful of complete trade lifecycles, including at least one
  partial-exit scenario and at least one full-SL-loss scenario)
- Full dashboard built and used to review performance (Implementation
  Plan Steps 12-16, if not already completed in parallel during earlier
  phases)

### Exit Criteria (ALL must be true to advance to Phase 4)
- [ ] Minimum sustained demo execution period completed
- [ ] At least one complete observed trade lifecycle for each scenario
      type: full TP hit, full SL hit, partial-exit-then-breakeven-stop,
      partial-exit-then-final-TP-hit
- [ ] All `EXECUTION_FAILED` and other `system_events` encountered during
      this phase have been investigated and resolved (or are understood
      and accepted as rare/edge-case broker behavior, documented clearly)
- [ ] Dashboard accurately reflects all demo trading activity with no
      discrepancies against manual review of raw MT5 account history
- [ ] Handler has reviewed overall strategy performance metrics (win
      rate, R-multiple, equity curve, per-window breakdown) from genuine
      demo execution data and made a deliberate, informed decision about
      whether the strategy performs as expected
- [ ] **Explicit Handler sign-off** — this is the most important gate in
      the entire project. This sign-off is a real risk decision, not a
      formality. The Handler should feel genuinely confident, based on
      real evidence from this phase, before proceeding
- [ ] Confirmation that the "Critical Safeguard" double-flag mechanism
      (Implementation Plan Section 5) is in place and tested, so that
      Phase 4 cannot be entered accidentally

### What Is Explicitly NOT Allowed in This Phase
- No connection to a live account under any circumstance, regardless of
  how well demo performance looks — this phase is demo-only, full stop

## 7. Phase 4 — Live Execution

### Objective
Operate the fully validated system against a live account with real
capital.

### Entry Criteria
- Phase 3 exit criteria fully met, including explicit Handler sign-off

### Activities
- Switch `ENVIRONMENT=live` and set the required explicit confirmation
  flag (Implementation Plan Section 5)
- **Strongly recommended (not currently a hard requirement, but flag this
  explicitly to the Handler as a recommendation):** begin live trading
  with reduced position size relative to the full 10-contract spec, and
  scale up only after a further period of confirmed live performance
  consistent with demo results. This is a risk-management judgment call
  for the Handler to make deliberately, not a decision to default into
  silently.
- Continue using the dashboard as the primary performance monitoring tool
- Continue logging every window/trade exactly as in prior phases — logging
  requirements do not relax in live mode, if anything they matter more

### Exit Criteria
There is no "exit" from Phase 4 in the same sense as earlier phases — this
is the ongoing operational state of the system. However, the following
ongoing obligations apply indefinitely:
- Continued monitoring of `system_events` for any critical issues
- Periodic review of dashboard performance metrics against expectations
  set during Phase 3
- Any strategy rule changes (per PRD Section 6) must be treated as a new
  specification change requiring documentation updates (PRD, Schema if
  applicable) BEFORE code changes — the same rigor applies to live-phase
  changes as to original development, arguably more so

### What Is Required at All Times During This Phase
- Immediate Handler attention to any `CRITICAL` severity `system_events`
  record (per Database Schema Section 4.4) — an autonomous system trading
  real money with an unresolved critical error is the single highest-risk
  state this project can be in
- No silent/unreviewed changes to strategy logic, risk parameters, or
  position sizing — all changes follow the same documentation-first
  discipline established from Phase 0 onward

## 8. Cross-Phase Rules (Apply at Every Phase)

1. **No phase skipping.** Even if time pressure or enthusiasm suggests
   "we've basically proven this, let's skip ahead" — don't. The gates
   exist specifically because confidence without evidence is how trading
   systems lose money.
2. **Every phase transition requires explicit Handler sign-off**,
   recorded in `00-HANDOVER.md` at the time of transition (e.g., "Phase 2
   exit criteria met and confirmed by Handler on [session reference];
   proceeding to Phase 3").
3. **Regression requires phase rollback.** If a bug or unexpected behavior
   is discovered in a later phase that reveals a flaw in logic validated
   in an earlier phase, the project rolls back to re-validate at that
   earlier phase — it does not patch forward and hope.
4. **All documentation updates happen BEFORE code changes**, not after —
   if implementation reveals a doc was wrong/ambiguous, fix the doc first,
   then the code, so the docs remain a trustworthy source of truth.

## 9. Current Phase Status

**This section should always be kept in sync with `00-HANDOVER.md` — if
they ever disagree, `00-HANDOVER.md` is the more current/authoritative
source, and this section should be updated to match.**

Current phase: **Phase 0 — Documentation & Foundation**
Status: Documentation substantially complete (8 core docs produced);
repository/environment setup steps still pending per Phase 0 exit
criteria.

## 10. Open Items / Unresolved Phase Planning Questions

- Exact minimum durations for Phase 2 (dry-run) and Phase 3 (demo) are
  given as recommendations ("one week," "several weeks") — the Handler
  may adjust these based on how many confirmed setups actually occur in
  practice (given 21 windows/day but likely a much smaller number of
  actual confirmed trades), as long as the exit criteria (observing each
  scenario type at least once, etc.) are genuinely met rather than just
  time elapsed
- Formal process for "regression rollback" (Section 8, rule 3) is stated
  as a principle here but not yet given a detailed procedure — if this is
  ever triggered, define the specific rollback steps at that time based
  on the actual situation
That's all 7 core documents complete. Here's where we stand:

Summary of what exists now
Doc	Status
00-HANDOVER.md	Created (simplified version), pending real update now that docs are done
01-PRD.md	✅ Complete
02-TRD.md	✅ Complete
03-APP-FLOW.md	✅ Complete
04-DESIGN-BRIEF.md	✅ Complete
05-DATABASE-SCHEMA.md	✅ Complete
06-IMPLEMENTATION-PLAN.md	✅ Complete
07-PHASE-PLAN.md	✅ Complete