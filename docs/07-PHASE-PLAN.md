# Project Chronos — Phase Plan

**Audience:** Any new developer, the Handler, or the Senior Dev picking this project up cold.
**Purpose:** Defines the 5 sequential phases of Project Chronos, what must be true to enter each phase, what must be true to exit each phase, and the hard rules that govern movement between phases. No phase may be skipped. No phase may begin until the previous phase has explicit Handler sign-off.

This document is the authority on "where are we and what's next." If this document and a conversation disagree, this document wins until formally updated.

---

## How to Use This Document

- Each phase has: **Entry Criteria**, **Objectives**, **Deliverables**, **Exit Criteria**, **Handler Sign-Off Requirement**.
- A phase is NOT complete until every Exit Criterion is checked AND the Handler has explicitly signed off (in writing, in the handover doc or commit history).
- If something breaks during a later phase that traces back to a logic error, the project ROLLS BACK to Phase 1 (Logic Validation). No patching live/demo code without re-validating against history.
- Documentation is updated BEFORE code changes, never after. If a phase forces a design decision that contradicts an existing doc, the doc is corrected first, then the code follows.

---

## Phase 0 — Documentation & Foundation

**Status: Nearly Closed** (see closure checklist in Appendix A)

### Entry Criteria
- Project concept, strategy rules, and collaboration model agreed with Handler.

### Objectives
- Produce the complete 8-document set, written for a zero-context incoming developer.
- Establish repository, folder structure, and version control.
- Resolve all architecture-level open questions (timezone handling, instrument specs, naming conventions) BEFORE any trading logic is written.

### Deliverables
- `docs/01-PRD.md` through `docs/07-PHASE-PLAN.md` (this doc) — all committed.
- `handover/SESSION-HANDOVER.md` — living doc, present and current.
- `introductory/agent-intro-prompt.txt`, `introductory/senior-dev-intro-prompt.txt`.
- GitHub repo `chronos-trading-system`, branch `main`, remote tracking confirmed.
- Confirmed instrument specification (NAS100) recorded in `docs/05-DATABASE-SCHEMA.md` seed data.
- Confirmed broker timezone handling strategy (NY+7, US DST-following, documented in `docs/02-TRD.md` Section 4.1.1).

### Exit Criteria
- [x] All 8 docs written, reviewed, committed, pushed.
- [x] Repo structure (`docs/`, `handover/`, `introductory/`, `bot/`) exists on GitHub.
- [x] Broker timezone architecture resolved and documented (NY+7 / US DST finding — supersedes earlier Nairobi/UTC+3 assumption).
- [x] NAS100 instrument spec confirmed via live MT5 lookup (digits, contract size, tick size/value, volume min/max/step, margin rate).
- [ ] Lot-sizing confirmation: "10 contracts" in strategy = MT5 `volume` field `10.0` — **awaiting explicit Handler yes/no**.
- [ ] Historical data source for Phase 1 decided: Dukascopy tick export vs. MT5 native export — **awaiting Handler/Senior Dev decision**.

### Handler Sign-Off Requirement
Handler must explicitly confirm both open items above are resolved. Only then is Phase 0 formally closed and Phase 1 may begin.

---

## Phase 1 — Logic Validation (Historical Backtesting)

### Entry Criteria
- Phase 0 formally closed (Handler sign-off obtained).
- Historical data source selected and accessible.
- `verify_broker_timezone.py` run successfully with correct NY-time conversion confirmed against known reference bars.

### Objectives
- Prove the strategy logic (window evaluation, context determination, sweep+reversal detection, SL/TP calculation, partial-exit/breakeven rule) produces correct, reproducible outcomes against real historical NAS100 price data.
- Zero manual trading occurs in this phase. No live or demo orders are placed. This is pure data analysis.

### Deliverables
- A backtesting script (or notebook) that ingests historical NAS100 data and, for every one of the 21 daily windows across a defined historical date range, outputs a logged outcome (WIN / LOSS / NO_SETUP / reason string) — mirroring exactly the schema defined in `docs/05-DATABASE-SCHEMA.md`.
- A written validation report: sample size (number of trading days / windows evaluated), outcome distribution, and manual spot-check of at least 20 individual windows against raw candle data to confirm the logic is computing High_A/Low_A/Mid_A, context, sweep, reversal, SL/TP, and partial-exit correctly.
- All timestamp handling in this script uses `zoneinfo`/`pytz` NY conversion — never hardcoded offsets. The broker-display debug helper (NY+7) may be used for cross-referencing only, never for logic.

### Exit Criteria
- [ ] Backtest runs end-to-end without crashing across the full test date range.
- [ ] Every window in the test range produces exactly one outcome row — no missing evaluations.
- [ ] Manual spot-check of ≥20 windows confirms logic matches the PRD Section 6 spec exactly (context rule, sequence-agnostic sweep/reversal, fib-based SL/TP, 80% partial-exit/breakeven).
- [ ] Zero known logic bugs remain open.
- [ ] Validation report written and committed.

### Handler Sign-Off Requirement
Handler reviews the validation report and spot-check samples, explicitly confirms the logic is trustworthy, before Phase 2 begins.

---

## Phase 2 — Live Dry-Run (No Orders Placed)

### Entry Criteria
- Phase 1 signed off.
- Bot code refactored from backtest script into the live monitoring architecture described in `docs/03-APP-FLOW.md`.

### Objectives
- Run the bot against LIVE/real-time MT5 data feed (demo account connection), executing the full monitoring loop and window evaluation logic in real time, but **never placing an actual order**. All "entries" are logged as if they happened (a simulated trade record), written to Supabase exactly as a real trade would be, flagged internally as dry-run.
- Confirm the bot correctly handles: midnight-crossing windows, the 3 skipped windows (15:45–18:15), weekends (no evaluation Sat/Sun), and connection drops (logged via `system_events`).

### Deliverables
- Bot running continuously (minimum 5 consecutive trading days) in dry-run mode.
- Every one of the 21 daily windows logged for every day the bot ran, with correct outcome and reason.
- `system_events` log showing clean startup/shutdown and any connection issues, with no silent failures.

### Exit Criteria
- [ ] Minimum 5 consecutive trading days of dry-run operation with zero missing window evaluations.
- [ ] Zero unhandled exceptions / crashes requiring manual restart.
- [ ] Dashboard (if built by this point per Implementation Plan ordering) correctly displays dry-run data.
- [ ] Handler has reviewed at least one full day's worth of logged windows and confirms they make sense against the actual chart.

### Handler Sign-Off Requirement
Handler explicitly confirms dry-run output is trustworthy and stable before any real order is allowed to be placed, even on demo.

---

## Phase 3 — Demo Execution (Real Orders, Demo Account)

### Entry Criteria
- Phase 2 signed off.
- `ENVIRONMENT=demo` and `CONFIRM_LIVE_TRADING=false` safeguard flags verified in place and functioning (per `docs/06-IMPLEMENTATION-PLAN.md`).

### Objectives
- Bot places real orders on the Forex.com Global DEMO account, autonomously, with no manual intervention, following the full strategy spec including entry, SL/TP, and the 80%-partial-exit/breakeven rule.
- Prove the system can run unattended for an extended period with correct trade management.

### Deliverables
- Minimum run duration: to be set by Handler (suggested: 2–4 continuous weeks of trading days) with no manual trade intervention.
- Every trade and every window logged in Supabase.
- Dashboard fully reflects live demo trading activity.

### Exit Criteria
- [ ] Full minimum run duration completed with zero manual intervention required to keep the bot operating correctly.
- [ ] Trade execution mechanics (entry, SL, TP, partial close at 80%, breakeven move) confirmed working correctly on at least one real example of each outcome type (WIN, LOSS, and at least one partial-exit event).
- [ ] No discrepancies between dashboard data and MT5 account history.
- [ ] Handler has reviewed demo P&L and operational stability and is satisfied the system behaves exactly as designed.

### Handler Sign-Off Requirement
Handler explicitly authorizes transition to live capital. This is the highest-scrutiny gate in the project.

---

## Phase 4 — Live Execution (Real Capital)

### Entry Criteria
- Phase 3 signed off by Handler.
- `ENVIRONMENT=live` and `CONFIRM_LIVE_TRADING=true` — both flags must be deliberately and explicitly set; the system must refuse to trade live if either is missing or false.
- Funding, broker live account setup, and any compliance/account verification steps completed outside this system's scope.

### Objectives
- Operate autonomously on a live, funded account, under the exact same validated logic as Phase 1–3, with no code changes made solely for the purpose of "going live."

### Deliverables
- Continuous autonomous live operation.
- Full audit trail (every window, every trade, every system event) identical in structure to prior phases.

### Exit Criteria
This phase has no "exit" in the traditional sense — it is the steady-state operating mode of the finished system. Ongoing exit/rollback conditions:
- [ ] Any logic discrepancy discovered in live operation triggers an immediate rollback to Phase 1 (halt live trading, re-validate against history, re-run Phase 2 and 3 before resuming live).
- [ ] Any unhandled exception results in the bot safely halting (no open-ended retry loops that could place unintended orders) and logging to `system_events`, with Handler notified.

### Handler Sign-Off Requirement
Not applicable in the traditional phase-gate sense beyond the entry gate — however, Handler retains ongoing override authority to pause/halt live trading at any time.

---

## Cross-Phase Rules (Apply at All Times)

1. **No skipping phases.** Phase N+1 cannot begin until Phase N's Exit Criteria are fully met and Handler sign-off is recorded.
2. **Regression forces rollback.** Any bug or unexpected behavior discovered in Phase 2, 3, or 4 that traces to a logic error requires rollback to Phase 1 re-validation before resuming forward progress.
3. **Docs before code.** If a phase reveals a decision that changes previously documented behavior, the relevant doc (PRD/TRD/Schema/App-Flow) is updated and committed first, then code is changed to match.
4. **Explicit Handler sign-off required at every transition.** No phase transition is assumed or inferred from silence — it must be explicitly stated.
5. **Strictly free-tier tools at every phase.** No phase introduces a paid service without explicit Handler approval relayed through the Handler.
6. **No real secrets in any commit, chat, or code, at any phase.**

---

## Appendix A — Phase 0 Closure Checklist (Live Status)

| Item | Status |
|---|---|
| 8 core docs written, committed, pushed | ✅ Done |
| Repo structure established on GitHub | ✅ Done |
| Broker timezone architecture resolved (NY+7, US DST) | ✅ Done |
| NAS100 instrument spec confirmed | ✅ Done |
| "10 contracts" = MT5 volume `10.0` confirmation | ⬜ Open — awaiting Handler yes/no |
| Historical data source decision (Dukascopy vs MT5 export) | ⬜ Open — awaiting decision |

Phase 0 is **not** formally closed until both open items above are checked off.