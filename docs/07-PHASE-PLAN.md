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

**Status: ✅ CLOSED** (all 7 checklist items done — see Appendix A; Phase 0 was formally closed before Phase 1 began)

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

**Status: ✅ CLOSED — merged to `main` at `1f9deb8` (Handler sign-off recorded).**
All exit criteria met; evidence in `bot/historical_validation/output/`.

### Entry Criteria
- Phase 0 formally closed (Handler sign-off obtained). ✅
- Historical data source: MT5 native export from the Forex.com Global demo account, primary source. If MT5's available history depth is insufficient for a statistically meaningful sample, Dukascopy may be used as fallback, with any resulting backtest output explicitly tagged `cross_feed_directional_only`.
- `verify_broker_timezone.py` run successfully with correct NY-time conversion confirmed against known reference bars.

### Objectives
- Prove the strategy logic (window evaluation, context determination, sweep+reversal detection, SL/TP calculation, partial-exit/breakeven rule) produces correct, reproducible outcomes against real historical NAS100 price data.
- Zero manual trading occurs in this phase. No live or demo orders are placed. This is pure data analysis.

### Deliverables
- A backtesting script (or notebook) that ingests historical NAS100 data and, for every one of the 21 daily windows across a defined historical date range, outputs a logged outcome (WIN / LOSS / NO_SETUP / reason string) — mirroring exactly the schema defined in `docs/05-DATABASE-SCHEMA.md`.
- A written validation report: sample size (number of trading days / windows evaluated), outcome distribution, and manual spot-check of at least 20 individual windows against raw candle data to confirm the logic is computing High_A/Low_A/Mid_A, context, sweep, reversal, SL/TP, and partial-exit correctly.
- All timestamp handling in this script follows `docs/02-TRD.md` §4.1.1 as corrected during Phase 1: **broker epoch → New York is a constant −7h subtraction**, with no DST-aware conversion applied on top. `zoneinfo` is used only for reporting (EST/EDT tagging) and for non-existent-local-time guards, never to convert broker epochs. *(Corrected at Phase 2 kickoff: the original wording here — "never hardcoded offsets" — predated the Phase 1 correction and contradicted the TRD, which is authoritative.)*

### Exit Criteria
- [x] Backtest runs end-to-end without crashing across the full test date range.
- [x] Every window in the test range produces exactly one outcome row — no missing evaluations. (4,431/4,431 across 211 dates × 21 windows, zero duplicates.)
- [x] Manual spot-check of ≥20 windows confirms logic matches the PRD Section 6 spec exactly (context rule, sequence-agnostic sweep/reversal, fib-based SL/TP, 80% partial-exit/breakeven). (`spot_check.csv`, 24 windows.)
- [x] Zero known logic bugs remain open. (Two classification defects and the timestamp convention were found and fixed; quantization gap closed in `caca96a`.)
- [x] Validation report written and committed.

### Handler Sign-Off Requirement
Handler reviews the validation report and spot-check samples, explicitly confirms the logic is trustworthy, before Phase 2 begins.

---

## Phase 2 — Live Dry-Run (No Orders Placed)

**Status: 🔄 IN PROGRESS — started after the Phase 1 merge (`1f9deb8`).**

**Scoping decisions taken by the Handler at kickoff (binding for this phase):**

- **Executed locally on the Handler's machine**, with the MT5 terminal logged
  into the existing demo account, because MT5 requires a live terminal and no
  free-tier always-on Windows host exists. Cloud/scaling architecture is
  deferred to the Phase 3→4 boundary. The Handler keeps the machine and the
  terminal running for the whole window; any downtime is a logged gap
  (`dry_run_gap_runner_down`), not a failure.
- **No Supabase writes in this phase.** Rows are appended to local CSVs with the
  same schema mirror Phase 1 produced (`window_evaluations.csv`,
  `window_evaluations_audit.csv`). Supabase is Handler-side and parallel; the
  dashboard criterion below is therefore deferred, not waived.
- **No order capability exists in the Phase 2 codebase at all** — not a flag,
  not a commented-out call. Verified by a static test over the runner's import
  graph, not merely by observing that nothing was sent.
- **Live DST invariant monitor** runs for the whole window, so the US fall-back
  transition (Sunday 1 Nov 2026, 02:00 NY) is captured live. Phase 1's dataset
  ended 2026-10-22 and therefore covered only the spring transition.

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
- [ ] **≥5 consecutive New York trading days** of dry-run operation (planned window: Mon 26 Oct → Tue 3 Nov 2026).
- [ ] **Zero orders placed**, verified *structurally* — the order-sending call is unreachable from the Phase 2 entrypoint and a test asserts it — not merely observed empirically.
- [ ] **All 21 windows logged on every day the runner was up**, each with a valid outcome and reason (zero missing evaluations).
- [ ] **Zero unhandled crashes.** Restarts are permitted only via the gap protocol: windows missed while down are logged `dry_run_gap_runner_down` and never backfilled.
- [ ] **DST invariant checks PASS continuously** through the 1 Nov 2026 fall-back transition: exactly one evaluation per window on 2 Nov, no double-fire, no dropped window, no hour shift in the maintenance break, Friday close or Sunday open.
- [ ] Dashboard criterion **deferred** to Phase 3 (no Supabase writes in Phase 2 — see the scoping decisions above).
- [ ] Handler has reviewed at least one full day of logged windows against the actual chart and signed off.

### Handler Sign-Off Requirement
Handler explicitly confirms dry-run output is trustworthy and stable before any real order is allowed to be placed, even on demo.

---

## Phase 3 — Demo Execution (Real Orders, Demo Account)

> **Measured execution cost (Phase 2 probe, Oct 2026):** live spread on NAS100
> was **30 points = 3.0 index points** at probe time. Phase 1 and Phase 2 both
> assume zero spread by decision (Q8), so this changed no result in either. Phase
> 3 is where it starts to matter: a clean TP run is worth ≈1R against a stop of
> ≈0.5·Range, so 3.0 points is a meaningful fraction of 1R on a median-range
> candle. Phase 3 must model spread explicitly rather than inherit the zero-cost
> assumption, and should measure the spread distribution across sessions rather
> than reuse this single sample.

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
| "10 contracts" = MT5 volume `10.0` confirmation | ✅ Done |
| Partial-exit trigger unit bug (handle/tick) resolved | ✅ Done |
| Historical data source decision | ✅ Done — MT5 native export (primary), Dukascopy (fallback, flagged `cross_feed_directional_only`) |

**Phase 0 is formally CLOSED.** Phase 1 (Logic Validation) begins now.