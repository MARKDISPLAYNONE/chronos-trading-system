# Project Chronos — Session Handover

**Purpose:** This is the single living source of truth for "where this project currently stands." Any new Handler, Senior Dev, or Coding Agent picking this up should be able to read this file top to bottom and have zero remaining context gaps. No dates are used anywhere in this document by design — only session numbers and status. Update this file at the end of every working session, before closing out.

---

## 1. Project Snapshot

**Name:** Project Chronos
**Repo:** `https://github.com/MARKDISPLAYNONE/chronos-trading-system` — branch `main`
**What it is:** An autonomous trading bot + read-only metrics dashboard that executes a fully-specified manual strategy ("Zeussy Macro Time" window model) on NAS100 via MT5, starting on a Forex.com Global demo account, with a defined path to live capital. Strictly free-tier tools throughout.

**Collaboration model:** Handler (human, relays everything) ↔ Senior Dev (AI chat, owns architecture/logic) ↔ Coding Agent (AI, executes only).

**Current phase:** 🟢 **Phase 1 (Logic Validation) — IMPLEMENTATION COMPLETE, AWAITING HANDLER SIGN-OFF.**

Phase 1 code, tests and the validation report are committed. Every one of the 21 windows on all 211 trading dates in the dataset produced exactly one outcome row (4,431 rows, zero gaps, zero duplicates). Phase 1's exit gate is a **manual review**: the Handler must spot-check ≥20 windows from §11 of the report and confirm the logic is trustworthy before Phase 2 begins.

⚠️ **One finding needs Senior Dev sign-off before Phase 1 is signed off**: the historical CSV does **not** contain true UTC, contrary to `docs/02-TRD.md` §4.1.1. See **§12** — read it before anything else in this file.

---

## 2. Phase 0 Closure Status — FINAL

| Item | Status |
|---|---|
| 8 core docs written, committed, pushed | ✅ Done |
| Repo structure established on GitHub | ✅ Done |
| Broker timezone architecture resolved (NY+7, follows US DST) | ✅ Done |
| NAS100 instrument spec confirmed via live MT5 lookup | ✅ Done |
| "10 contracts" = MT5 `volume` field `10.0` confirmed | ✅ Done |
| Partial-exit trigger unit bug (handle/tick mismatch) resolved | ✅ Done |
| Historical data source for Phase 1 | ✅ Done — **MT5 native export, primary; Dukascopy, fallback only (flagged `cross_feed_directional_only`)** |

**All Phase 0 items closed. No open blockers remain for this phase.**

---

## 3. Document Set — Status

All located in `docs/` unless noted. All 8 exist, are committed, and pushed.

| File | Status |
|---|---|
| `docs/01-PRD.md` | Current. Section 6.7/6.8 rewritten (partial-exit unit-bug fix). |
| `docs/02-TRD.md` | Current. §4.1.1 NY+7 finding unchanged. In `66f0b09`: glossary handle definition corrected to 10 ticks + display-only note, and the unterminated code fence at §4.1.1 closed (renders correctly now). The Session 6 "unexplained local modification" flag is resolved — it was the wholesale add in `8ed0833` plus a markdown error. |
| `docs/03-APP-FLOW.md` | Updated in `66f0b09` — §2.4 step 3 rewritten to the raw price-distance partial trigger (stale handle math removed). Rest unchanged since initial delivery. |
| `docs/04-DESIGN-BRIEF.md` | Current, unchanged since initial delivery. |
| `docs/05-DATABASE-SCHEMA.md` | Updated in `66f0b09` — `handle_size_in_ticks` column removed from `instruments`; four new controlled-vocabulary reasons added (`market_closed_no_data`, `ambiguous_same_bar_insufficient_granularity`, `missing_market_data`, `outside_data_range`) plus three reserved ones; new §8.1 (evaluation-order rules: same-bar M1 trigger, pessimistic intrabar ordering, fill assumption, R-multiple formula). **The DDL in `supabase/migrations/` must therefore NOT create `handle_size_in_ticks`.** |
| `docs/06-IMPLEMENTATION-PLAN.md` | §6 corrected in `66f0b09` (stale handover path). **Step 1 folder structure still not executed** — see §8. |
| `docs/07-PHASE-PLAN.md` | Current. Appendix A updated this session — Phase 0 marked formally closed, historical data source decision recorded. |
| `handover/SESSION-HANDOVER.md` | This file. Fully rewritten this session. |
| `introductory/agent-intro-prompt.txt` | Current, unchanged since rename. |
| `introductory/senior-dev-intro-prompt.txt` | Current, unchanged since rename. |
| `bot/historical_validation/verify_broker_timezone.py` | Current, NY+7-confirmed version. |

---

## 4. Confirmed Facts (Do Not Re-Litigate)

- **Instrument (NAS100):** Digits 1, Contract size 1, Tick size 0.1, Tick value $0.1/tick/lot, Volume min/max/step 0.1/1000/0.1, Margin ~0.5% notional. API symbol string: `"NAS100"`.
- **Broker timezone:** Forex.com/GAIN Capital runs **"NY+7"** — broker server time is always exactly 7 hours ahead of New York LOCAL time, following **US DST transition dates**. This part is confirmed and still holds.
  - ⚠️ **AMENDED in Phase 1 — see §12.** The second half of the original statement ("MT5 raw epoch is true UTC; NY+7 is display-only") is **not true of the actual export**: the epoch carries broker time, so NY+7 is load-bearing on input and the conversion is `utc_label(epoch) − 7h`. The strategy still never reasons about broker time as a *concept* — it reasons about New York wall clock, which is what the adapter produces — but the offset is now part of the input path, not a debug extra. Awaiting Senior Dev sign-off before TRD §4.1.1 is edited.
- **Position size:** 10 contracts = MT5 `volume` field `10.0`.
- **Partial-exit trigger:** Raw price-distance percentage — `entry + 0.80 × (TP − entry)` (long) / `entry − 0.80 × (entry − TP)` (short). Unit-invariant. "Handle" (= 1.0 NAS100 point = 10 ticks, derived as `1 / tick_size`) is display-only, never a calculation input.
- **Sweep/reversal rule:** Sequence-agnostic. Only the final sweep+reversal state before window close matters.
- **80%-partial + breakeven mechanic:** Unchanged, confirmed correct, unaffected by the $-risk sanity-check observation.
- **Historical data source (Phase 1):** MT5 native export is the authoritative, primary source. Rationale: feed-consistency with the actual execution venue matters more than raw depth, because sweep/reversal logic is sensitive to exact wick-level precision (1-tick thresholds) — cross-feed discrepancies could produce false backtest outcomes. Dukascopy may be used only as a depth fallback, and any result derived from it must be tagged `cross_feed_directional_only` in backtest output, never treated as definitive logic validation.
- **Manual demo test trade** (10 lots, entry 28985.2, SL 28956.0, TP 28991.6, closed −$203): confirms MT5 order/SL mechanics only — not strategy validation data. Its 20-handle move, however, is what empirically confirmed 10 ticks/handle, which fixed the partial-exit unit bug.

---

## 5. Errors & Dead Ends (Do Not Reintroduce)

- ~~Africa/Nairobi fixed-offset broker timezone assumption~~ — superseded by confirmed NY+7/US-DST finding.
- ~~"1 handle = 4 ticks"~~ — wrong; confirmed 10 ticks/handle. Removed from trigger calculation entirely; trigger uses raw price-distance percentage now.
- ~~Strict-sequence sweep/reversal rule~~ — superseded by final sequence-agnostic rule.
- ~~SPX500 instrument lookup~~ — wrong symbol; corrected to NAS100.
- Running `git add/commit/push` before `git init` — resolved.

---

## 6. Phase 0 — CLOSED (Historical Reference Only)

Phase 0 required 6 items to close; all are now done (see Section 2). The final blocker — historical data source for Phase 1 — was resolved by Senior Dev decision: **MT5 native export, primary; Dukascopy, fallback only, flagged `cross_feed_directional_only` if used.** No further action needed on this item. Full rationale preserved in Section 4 above and in `docs/07-PHASE-PLAN.md` Phase 1 Entry Criteria.

---

## 7. What's Done

- Full 8-document set, written for zero-context incoming developer, covering all 13 architecture layers.
- GitHub repo live, all commits pushed and verified. Only `docs/`, `handover/`, `introductory/` and `bot/historical_validation/` exist — Implementation Plan Step 1's folder structure has NOT been created (tracked in §8, not here).
- NAS100 instrument spec confirmed via live MT5 lookup.
- Broker timezone handling fully resolved, documented, verification script built and confirmed.
- Core strategy spec fully locked: windows, context rule, sweep/reversal (sequence-agnostic), fib-based SL/TP, position sizing, partial-exit trigger (unit-bug-free).
- Python environment set up (Python 3.14, `MetaTrader5` 5.0.6231, `pytz` 2026.5), confirmed working against live MT5 demo terminal.
- MT5 demo account (Forex.com Global) confirmed accessible, order/SL mechanics confirmed functional (mechanics test only).
- **Phase 0 formally closed — all 7 checklist items done.**
- **Phase 1 delivered (this session).** Working, tested, reproducible:
  - `bot/src/` — `config`, `pricing`, `bars`, `time_windows`, `candle_tracker`,
    `context_classifier`, `sweep_detector`, `trade_calculator`,
    `trade_simulator`, `window_evaluator`, `m1_csv_source`, `outputs`, `report`.
  - `bot/historical_validation/validate_against_history.py` — the Phase 1 driver
    (adapter-driven, CLI, `--convention`, `--out-dir`, `--start/--end`).
  - `bot/tests/` — **96 unit tests, all passing** (`python3 -m unittest discover -s tests`).
  - `README.md`, `bot/requirements.txt` (stdlib runtime; pytest optional),
    `bot/.env.example` (names only, nothing needed in Phase 1).
  - Generated evidence in `bot/historical_validation/output/`:
    `phase1_validation_report.md`, `window_evaluations.csv`,
    `trades.csv`, `window_evaluations_audit.csv`, `spot_check.csv`,
    `chronos_phase1.sqlite` (git-ignored, regenerable).

**Phase 1 results at a glance** (NAS100 M1, 2026-01-01 → 2026-10-22,
zero-spread/zero-slippage logic test — **not** a P&L forecast):

| Metric | Value |
|---|---|
| Bars consumed | 287,746 |
| Trading dates × windows | 211 × 21 = **4,431** rows, exactly one per window |
| WIN / LOSS / NO_SETUP | 1,718 / 913 / 1,800 |
| Trades | 2,631 (win rate 65.3%) |
| Net R | **+575.135** (avg +0.219 R/trade) |
| Partial exits fired | 943 (35.8% of trades) |
| Most common non-trade reasons | `no sweep` 1,051 · `ambiguous_same_bar_insufficient_granularity` 342 · `market_closed_no_data` 292 · `invalid open location` 103 |
| Data-integrity anomalies | 6 feed holes (`missing_market_data`), 6 file-boundary windows (`outside_data_range`), 3 trades open across a session break |

**Independent verification performed before claiming the result** (this is the
part that makes the numbers trustworthy — do the same on any re-run):
1. Every derived field (Candle A OHLC, `Mid_A`, Candle B open, context,
   direction, entry/SL/TP/partial) was recomputed by a second, separately
   written script across 3,959 complete windows — **zero mismatches**.
2. All 2,631 trades were re-simulated end-to-end by that second
   implementation (trigger time, exit kind, exit price, R) — **zero
   disagreements**.
3. All 342 same-bar-ambiguity windows were inspected individually and
   confirmed to be genuine Q5 cases with **zero** discarded valid triggers.

---

## 8. What's NOT Done

- **No live bot, no dashboard, no Supabase.** Phase 1 is historical replay only; `mt5_client`, `supabase_client`, `order_executor`, `trade_manager`, `main.py` and everything under `dashboard/` are later phases (Steps 8–16).
- **Phase 1 is not signed off yet** — it needs the Handler's ≥20-window spot-check (report §11 / `spot_check.csv`).
- **DST coverage gap:** the dataset ends 2026-10-22, so the **March 8 spring-forward** is exercised but the **November 1 fall-back** is not. Fall-back is reasoned about (both transitions fall on a Sunday, so Mon–Fri windows cannot enter the repeated hour) but not empirically tested. A dataset spanning Nov 1 is the remaining DST evidence.
- **Currency P&L is absent by design** (Q9): `result_currency_amount` is NULL and `instruments.point_value` remains unconfirmed.
- **Two Phase-1 modelling limits worth knowing:** no gap fills are modelled (3 trades were open across a session break, listed in report §10), and trades are simulated independently, so two positions may overlap — that is a reporting simplification, not a portfolio-risk claim.
- **`bot/src/config.py` holds no secrets and reads no environment.** Phase 2 will need `bot/.env` for MT5/Supabase; the names are already in `bot/.env.example`.
- **Implementation Plan Step 1 (folder structure) has NOT been executed.** This is the literal next build step after Phase 1 scaffolding settles — it is not something already done, and earlier wording in this file overclaimed it. Still missing: `README.md`, `bot/src/`, `bot/tests/`, `bot/requirements.txt`, `bot/.env.example`, `dashboard/`, `supabase/migrations/`, `.github/workflows/`. (`dashboard/`, `supabase/` and `.github/` are out of Phase 1 scope anyway — see §11.2 Q1/Q2.)
- Supabase project creation + migrations (Implementation Plan Step 2) — **out of Phase 1 scope by decision; Handler-side task, not the Agent's** (§11.2 Q1/Q2). It must not block Phase 1.
- Phase 1 backtesting script (against MT5 native export data) not yet started — deliberately, pending answers to §11.

---

## 9. Immediate Next Steps (In Order)

1. 🔴 **Handler — Phase 1 sign-off gate.** Open
   `bot/historical_validation/output/phase1_validation_report.md` §11 and
   `spot_check.csv`, and verify ≥20 windows against the raw candles using the
   recipe printed in the report. This is the Phase 1 exit criterion and cannot
   be delegated to the Agent.
2. 🔴 **Handler → Senior Dev — rule on §12 (the timestamp convention).** The code
   already implements the data-proven reading and refuses to run otherwise, but
   `docs/02-TRD.md` §4.1.1 still states the opposite. The TRD needs correcting
   and `verify_broker_timezone.py` needs re-running. Proposed text is in §12.
3. 🔴 **Handler → Senior Dev — confirm Q11 and Q12** (see §11.7). Both were
   implemented to the literal spec + the Agent's recommendation because the
   Handler instructed "no further blockers"; each is a one-line change if the
   Senior Dev rules differently.
4. **Then Phase 2 (dry-run) only on explicit sign-off**, and only after
   Implementation Plan Step 2 (Supabase) is done Handler-side. Phase 2 adds
   `config.py` env loading, `mt5_client`, `supabase_client`, `logger`, `main.py`
   and refactors the validated logic into the live loop — the Phase 1 modules
   (`time_windows`, `candle_tracker`, `context_classifier`, `sweep_detector`,
   `trade_calculator`) are designed to be reused unchanged.
5. Optional before Phase 2, if a longer history is cheap to export: re-run with
   a dataset spanning 2026-11-01 to close the fall-back DST gap (see §8).

## 10. Session Log

**Session 1:** Initial strategy spec gathering, PRD/TRD/all 8 docs drafted and delivered, repo created and pushed, introductory prompts created.

**Session 2:** NAS100 instrument spec corrected (was initially SPX500), confirmed via live MT5 lookup. Sweep/reversal rule finalized as sequence-agnostic. Manual demo test trade executed (mechanics check only).

**Session 3:** Broker timezone investigation. Nairobi/UTC+3 hypothesis tested, flagged as fragile.

**Session 4:** Broker timezone finding finalized as NY+7/US-DST convention. `docs/02-TRD.md` §4.1.1, `verify_broker_timezone.py`, handover updated and committed.

**Session 5:** `docs/07-PHASE-PLAN.md` fully rewritten (5-phase structure, closure checklist). `verify_broker_timezone.py` finalized. 10-lot=volume 10.0 confirmed. Partial-exit unit bug (handle/tick mismatch) identified and fixed — trigger logic rewritten to raw price-distance percentage. `docs/01-PRD.md` §6.7/6.8 rewritten and committed.

**Session 6:** Senior Dev made final call on historical data source: MT5 native export, primary; Dukascopy, fallback only (flagged `cross_feed_directional_only`, tagged). `docs/07-PHASE-PLAN.md` Appendix A updated — **Phase 0 formally closed.** Handover doc rewritten to reflect closure and transition into Phase 1. Flagged an unexplained local modification to `docs/02-TRD.md` for Handler to verify before committing.

**Session 7 (Phase 1 pre-flight):** No code written, deliberately. Agent re-read the full doc set (01, 02, 07, then 03/05/06 + this handover) as Phase 1 execution began and stopped at Hard Rule 6. Outcomes:
- Resolved the Session 6 flag: the "unexplained local `docs/02-TRD.md`" content is committed in `8ed0833` (whole-file add), and the working tree is clean. It contains genuine defects (D2/D3).
- Confirmed `handover/SESSION-HANDOVER.md` has never existed in any commit; `handover/SESSION-HANDOVER.md` is the only living state doc (D1).
- Corrected §7's "correct structure" claim — Implementation Plan Step 1 is only partially done (D4).
- Raised 12 blocking questions in §11: scope (Q1–Q2), data/harness (Q3–Q4), logic modelling (Q5–Q9), market calendar (Q10–Q12). The two that most change Phase 1's numbers are **Q5** (same-bar sweep+reversal on M1) and **Q7** (SL/TP/partial ordering inside one bar); the one that most changes feasibility is **Q3** (how MT5 data reaches a runnable harness).
- Recommended path recorded in §9. Agent is idle pending Senior Dev answers.

**Session 8 (doc-consistency pass + decision binding):** Senior Dev confirmed D1–D4 as real and prescribed exact fixes; answered 10 of 12 questions (Q1–Q10), leaving **Q11 and Q12 open**.
- **Commit `66f0b09`:** D1 (old handover path rewritten to `handover/SESSION-HANDOVER.md` in 4 files; repo-wide grep for the old literal now clean), D2 (TRD glossary → 10 ticks + display-only note; APP-FLOW §2.4 step 3 → raw price-distance trigger; `handle_size_in_ticks` **dropped** from `instruments`), D3 (code fence in TRD §4.1.1 closed, fence count now even), plus the new controlled-vocabulary reasons (`market_closed_no_data`, `ambiguous_same_bar_insufficient_granularity`) and new schema **§8.1** evaluation-order rules.
- **Follow-up commit on top of `66f0b09`** (this file): D4 wording corrected in §7/§8, §11 restructured into resolved-defects + binding-answers (§11.6), §3 doc-status rows, §9 next steps rewritten, this log.
- **Still zero Phase 1 code.** Deliberate. Hard Rule 6 held: Q11/Q12 were flagged rather than guessed.
- **Open implementation detail flagged to the Handler, not decided unilaterally:** a local SQLite mirror of `window_evaluations` cannot satisfy the `user_id` / `instrument_id` / `trade_id` foreign keys (no auth user, no Supabase, no trades table in Phase 1). Proposal: keep identical column names/types and leave those three columns nullable/unpopulated, documented in code. Awaiting confirmation alongside Q11/Q12.


**Session 9 (Phase 1 implementation — this session):** Data arrived (`data/nas100_m1_history.csv`, 287,746 M1 bars, Jan 1 – Oct 22 2026). Phase 1 built end-to-end and verified.
- **Found the timezone defect (§12) before writing any strategy code.** The export's epoch is broker time, not UTC. Proved with four session invariants plus an intraday volume profile plus the strategy's own skip set; `--convention utc` fails all four invariants on this file. Flagged for sign-off rather than silently patching the TRD.
- **Built** 13 modules under `bot/src/`, the Phase 1 driver, and 96 unit tests (all passing). Runtime 11 s for 4,431 windows. Standard library only. Code + outputs committed as `2dc3ae1`.
- **Evaluated** 211 trading dates × 21 windows = 4,431 rows, exactly one per window, zero duplicates. 2,631 trades, 65.3% win rate, +575.1R net (zero-cost fills — a logic gate, not a P&L forecast).
- **Fixed two classification bugs found by inspecting real data:** the last export day was being labelled `market_closed_no_data` when the file simply ends (now `outside_data_range`), and US-holiday Mondays (13:00→18:00 closure) were being labelled feed holes (now `market_closed_no_data` via the 60-minute structural-gap rule, added to schema §8.1).
- **Cross-verified independently:** a second, separately written implementation reproduced every derived field across 3,959 complete windows and re-simulated all 2,631 trades with zero disagreements; all 342 ambiguity windows individually confirmed as genuine Q5 cases with no discarded valid triggers.
- **Not done, deliberately:** no dashboard, no Supabase, no MT5 client, no order code — all later phases. TRD §4.1.1 and `verify_broker_timezone.py` left untouched pending sign-off (§12.4).

---

## 11. Phase 1 Pre-Flight — Questions and how each was resolved (Q1–Q12)

**Status: all 12 resolved — 10 answered explicitly by the Senior Dev (§11.6), 2 implemented to the literal spec with the alternative kept one line away (§11.7). Phase 1 is built and complete.**

This section is kept as the record of *why* the code looks the way it does. It was written when the Coding Agent stopped at Hard Rule 6 because Phase 1's specification contained model-level gaps that could not be resolved by reading the documents.

**Where the answers live now (docs-before-code, Phase Plan Cross-Phase Rule 3):**
- Same-bar ambiguity rule, intrabar exit ordering, fill assumptions, R-multiple formula → **`docs/05-DATABASE-SCHEMA.md` §8.1** (new subsection), plus new controlled-vocabulary reasons in §8.
- Stale handle/unit text and the malformed fence → fixed in `docs/02-TRD.md`, `docs/03-APP-FLOW.md`, `docs/05-DATABASE-SCHEMA.md`.
- Phase 1 scope, data contract, sample size → recorded in §11.6 below.


### 11.1 Documentation defects — ALL FOUR RESOLVED

Senior Dev confirmed D1–D4 as real and prescribed exact fixes. All applied in commit `66f0b09` (`docs: fix D1-D4 doc defects and bind Phase 1 decisions`). See §10, Session 8. Table retained as the record of what was wrong and what was changed.

| # | Defect | Location | Resolution |
|---|---|---|---|
| D1 | Hard-rule doc path pointed at a `docs/`-prefixed handover filename that never existed (renamed early in the project; older text never updated). | `introductory/agent-intro-prompt.txt`, `introductory/senior-dev-intro-prompt.txt`, `docs/06-IMPLEMENTATION-PLAN.md` §6, this file | ✅ **FIXED.** Every literal occurrence of the old path (4 files) rewritten to `handover/SESSION-HANDOVER.md`. Repo-wide grep for the old literal now returns nothing. No pointer file, no duplicate source of truth created. |
| D2 | Stale "1 handle = 4 ticks" in three places, contradicting the closed Phase 0 finding (10 ticks/handle) and PRD §6.8. | `docs/02-TRD.md:32`, `docs/03-APP-FLOW.md:147`, `docs/05-DATABASE-SCHEMA.md:77` | ✅ **FIXED.** TRD §2 glossary now reads "1 handle = 10 ticks" + display-only note. APP-FLOW §2.4 step 3 rewritten to the raw price-distance trigger formula (no handle conversion) + same note. `handle_size_in_ticks` **removed from the `instruments` schema entirely** — handle is derived at runtime as `1 / tick_size`, never stored or defaulted. |
| D3 | `docs/02-TRD.md` §4.1.1 had exactly one code fence, so the `python` block never closed and swallowed §4.2 onward when rendered. | `docs/02-TRD.md` | ✅ **FIXED.** Fence closed before §4.2 begins; file now has an even fence count (2). Pure syntax fix, no content change. This was the "unexplained local modification" from Session 6 — confirmed by Senior Dev as a markdown error from their own §4.1.1 rewrite, committed wholesale in `8ed0833`. |
| **D3b** | Same class as D3, found while verifying fence counts: `docs/05-DATABASE-SCHEMA.md` §5 RLS block and `docs/06-IMPLEMENTATION-PLAN.md` §5 safeguard block were also unterminated, swallowing §5→§6 prose when rendered. Pre-existing since initial delivery, not introduced this session. | `docs/05-DATABASE-SCHEMA.md`, `docs/06-IMPLEMENTATION-PLAN.md` | ✅ **FIXED** under the D3 ruling (pure syntax fix, no content change). All 7 docs now have an even fence count. Flagged to the Handler as one step beyond the literal D3 scope — disclosed, not silent. |
| D4 | Implementation Plan Step 1 ("create folder structure") was recorded as done but is **not** done. | §7 / §8 of this file | ✅ **FIXED.** §7 no longer claims structure is built; the item now appears explicitly in §8 "What's NOT Done" as the literal next build step. |

### 11.2 Scope questions (blocking: what am I allowed to build?)

**Q1 — Phase 1 build set.** Phase Plan Phase 1 names one deliverable (`validate_against_history.py` + validation report), but Implementation Plan Step 7 declares it depends on Steps 3–6 (`time_windows`, `candle_tracker`, `context_classifier`, `sweep_detector`, `trade_calculator`, with unit tests), which in turn depend on Steps 1–2. Implementation Plan Step 1/§6 also asks for Step 2 (Supabase project + migrations + seed) before bot logic. Proposed reading, for confirmation: Phase 1 includes Step 1 (folder scaffolding), Steps 3–6 **logic modules only** (`config.py`, `time_windows`, `candle_tracker`, `context_classifier`, `sweep_detector`, `trade_calculator` + their tests), and Step 7 (backtest + report). It excludes `mt5_client`, `supabase_client`, `order_executor`, `trade_manager`, `main.py` (Phases 2–3) and the dashboard (Steps 12+). **Is Step 2 (Supabase) in Phase 1 at all?** I cannot create a Supabase project from this environment (no account access, and it requires the Handler's own free-tier signup), and a pure historical replay needs no DB to validate logic.

**Q2 — Which artifact is authoritative for Phase 1 output?** Step 7's backtest "mirrors exactly the schema in `05-DATABASE-SCHEMA.md`", but Phase 1 places no orders, has no Supabase project, and has no auth user to satisfy the `user_id` FK. Proposed: Phase 1 emits a schema-shaped **CSV/JSONL** (same column names/types as `window_evaluations` + `trades`) and a Markdown report; DB writes start in Phase 2 when a real authenticated user exists. Confirm.

### 11.3 Data questions (blocking: what do I run it against?)

**Q3 — How does the data reach the script?** `MetaTrader5` is Windows-only; my execution environment is Linux with no MT5 terminal, no demo credentials, and no Windows. Two options:
- (a) I define a strict CSV input contract, you export from MT5 on Windows, commit a **sanitised** sample (or hand it over out-of-band), and the backtest is adapter-driven so it is testable/CI-runnable without MT5. Recommended.
- (b) The script calls `mt5.copy_rates_range()` directly. Then nothing is runnable or verifiable except on your Windows machine, and Phase 1's exit criterion "runs end-to-end" can only be checked by you, not gated in CI.
Confirm (a), or (b), or (a)+(b) with CSV as the tested path.

**Q4 — Granularity and sample size.** Phase 1's sweep/reversal rule is 1-tick-precise, and Phase Plan says "enough depth for a statistically meaningful sample" without defining it. Decide: M1 bars only, or tick data? What minimum date range (sessions/weeks) must the validation report cover? Must the range include at least one US DST transition (TRD §11 requires DST testing), i.e. straddling 2nd-Sunday-March or 1st-Sunday-November?

### 11.4 Logic questions (blocking: these change the numbers)

**Q5 — Same-bar sweep and reversal on M1 data.** With 1-minute bars you cannot prove that a reversal touched *after* the sweep inside the same bar. Valid trigger, or not? This is the single highest-impact modelling choice in Phase 1 (it is the difference between a real entry and a `NO_SETUP`/`sweep not reversed`). Also confirm the sequence-agnostic rule formally as: *a setup exists iff ∃ sweep touch at time t and ∃ reversal touch at time u > t, both before window close* — and that a re-sweep after an initial sweep does not reset or invalidate anything.

**Q6 — `Mid_A` is not always on a tick.** tick_size = 0.1, so when `Range / tick_size` is odd, `Mid_A = (High_A + Low_A)/2` lands on a half-tick (e.g. High 10000.0 / Low 9999.7 → Mid 9999.85). Candle B's open is always on a tick, so `open == Mid_A` is then unreachable and the "VOID because open is exactly at the midpoint" branch can never fire. Decide: strict equality only (VOID-at-mid occurs only on even-tick ranges), or compare open against a rounded Mid_A (round/floor/ceil — which?), or treat "strictly between" as the only test.

**Q7 — Exit ordering inside one bar.** When a single M1 bar spans SL and TP, or the 80% partial level and SL, the outcome is arbitrary unless a rule is fixed. Proposed pessimistic convention: SL is evaluated first for the whole open position; TP only if SL not touched; the 80% partial fires only if neither SL nor TP was touched in that bar. Confirm or replace.

**Q8 — Fill assumptions.** Zero spread, zero slippage, zero commission, entry filled exactly at the PRD §6.6 reference (`High_A − 1 tick` / `Low_A + 1 tick`) rather than at a later bar price. Confirm — this materially flatters results versus live execution, and Phase 1 is a logic gate, not a profitability gate.

**Q9 — Partial-exit and R-multiple definition.** Confirm the arithmetic I will implement: risk per contract = `0.5·Range + 1 tick` for both directions; R denominator = total initial risk (10 contracts); partial = 9 contracts at 80% of entry→TP distance, remainder to breakeven; `WIN` iff net > 0 (so partial→breakeven is a `WIN` at ≈ +0.72R, partial→TP ≈ +0.82R, full SL = −1R, gap straight to TP = +1R). Also: does the Phase 1 report need currency P&L, or is R-multiple sufficient? `instruments.point_value` is still unconfirmed in the confirmed-facts list (only tick value $0.1/tick/lot is).

### 11.5 Market-calendar questions (blocking: ~6 windows/week are affected)

**Q10 — Windows that fall outside NAS100's trading session.** NAS100 (Forex.com) is not open 24/7: it closes around Fri 17:00 ET and reopens Sun 18:00 ET, with a daily maintenance break. Under the Mon–Fri rule, **six windows every Friday** (18:45, 19:45, 20:45, 21:45, 22:45, 23:45) have no market data at all, and US holidays / half-days produce absent bars in other windows. The three deliberately skipped windows (15:45–18:15) appear to bracket exactly that daily maintenance break — please confirm that this is the reason they were excluded, since it would also settle whether the marginal windows at 14:45 and 18:45 are intentional. Needed: does a no-data window become `MISSING_EVALUATION` with reason `"bot unavailable during window"` (which is factually wrong for a backtest — the bot was available, the market was shut), or a new controlled-vocabulary reason (e.g. `"market closed"`) that must be added to `05-DATABASE-SCHEMA.md` §8 first?

**Q11 — Sunday session. 🔴 STILL OPEN.** Sunday 18:00–24:00 ET is the same market session as Monday's, but the Mon–Fri rule excludes it. Confirm Sunday-evening windows are genuinely **not** in scope (they appear to be excluded precisely because they are not "Mon–Fri NY calendar days", which is consistent — but it means the 00:00–00:45 Monday gap is a real hole in coverage the strategy accepts).

**Q12 — Derived-state reporting. 🔴 STILL OPEN.** For the validation report's "why" column, do you want an intermediate audit trail per window (sweep time/price, reversal time/price, context, SL/TP, partial trigger) or only the final outcome + reason? The former is far more useful for the ≥20-window manual spot-check, but it is extra output to define. Recommend yes.

### 11.6 Answers received (Senior Dev, via Handler) — BINDING FOR PHASE 1

These are decisions, not proposals. They supersede my proposals in §11.2–11.5 wherever the two differ, and they are the spec the code must implement.

| Q | Decision |
|---|---|
| **D1–D4** | All four defects confirmed real. Fixes prescribed and applied exactly as directed (see §11.1). |
| **Q1/Q2 — Scope** | **Supabase and the DB are NOT in Phase 1 scope at all.** Historical replay needs no DB. Phase 1 output is local only: CSV and/or a local SQLite file whose table structure mirrors `window_evaluations` from `05-DATABASE-SCHEMA.md`, so the later migration to real Supabase (Phase 2+) is a straight schema match, not a redesign. Supabase project creation is a **Handler-side** task happening in parallel, independent of Phase 1 — the Agent must not block on it and must not touch it. |
| **Q3 — Data delivery** | **Option (a) confirmed.** Adapter-driven backtest against a CSV contract the Handler exports from MT5. Agent needs no MT5 access beyond the file. Contract: `timestamp_utc, open, high, low, close, volume` — one row per **M1** bar. `timestamp_utc` is Unix epoch **or** ISO-8601 UTC (implementation's choice, must be explicit and consistent); the adapter converts to `America/New_York` internally via `zoneinfo` and **never trusts any broker-local assumption from the file itself**. |
| **Q4 — Granularity & sample** | **M1** for sweep/reversal sequencing (M15 only ever meant Candle A/B structure, which is granularity-safe — pure OHLC aggregation). Minimum **6 months of M1**, and the range **must include at least one US DST transition date** (non-negotiable — it is the point of re-validating the NY+7 finding under both regimes). Prefer a full year if MT5 offers it (both March and November transitions, larger sample). Tick data rejected as overkill for free-tier Phase 1. |
| **Q5 — Same-bar sweep+reversal** | **Never assume valid.** A single M1 bar may register a confirmed trigger only if its own direction is consistent with "swept, then reversed" — PREMIUM: `bar.high >= High_A + 1 tick` AND `bar.close <= High_A − 1 tick`; DISCOUNT mirrored. If the bar straddles both thresholds but its close does not confirm the reversal side → `NO_SETUP` / **`"ambiguous_same_bar_insufficient_granularity"`** (new controlled-vocabulary string, now in schema §8). Never guess in the bot's favour. |
| **Q6 — `Mid_A` half-tick** | **Not a bug, no special-casing.** `Mid_A` is a pure arithmetic reference value; it is used only for comparison against Candle B's open (always tick-aligned) and for TP (`Mid_A ± 1 tick`, which re-aligns it). `open == Mid_A` being effectively unreachable on odd-tick ranges is a correct, harmless consequence of the math. Use standard float comparison with a small epsilon (e.g. `1e-9`) **only** to guard float noise — never to change the logic. |
| **Q7 — Intrabar ordering** | **My pessimistic proposal adopted verbatim.** SL checked first for the whole open position; TP only if SL untouched; 80%-partial only if neither SL nor TP was touched. **If one bar contains both the 80% trigger and the SL, the SL wins.** |
| **Q8 — Fills** | **Zero slippage, zero spread, zero commission.** Entries fill exactly at the computed trigger price; exits exactly at SL/TP/partial-trigger. Must be stated **explicitly and prominently in the validation report** so nobody mistakes Phase 1 P&L for realistic live expectations. |
| **Q9 — R-multiple** | `R = |entry − SL|`. Partial fired: `partial_R = 0.9 × ((trigger − entry)/R)` + `runner_R = 0.1 × ((final_exit − entry)/R)`, where `final_exit` is whichever of TP / breakeven / SL actually closed the runner. No partial: `total_R = (final_exit − entry)/R`. Shorts mirror the signs (`entry − price`). Report `total_R` per trade **plus** the raw outcome label (WIN/LOSS/NO_SETUP/reason). Phase 1 reports R only — currency P&L deferred (consistent with zero-spread simplification and the still-unconfirmed `point_value`). |
| **Q10 — No market data** | **`NO_SETUP` + `"market_closed_no_data"`**, never `MISSING_EVALUATION`. `MISSING_EVALUATION` is reserved for genuine bot-side failures (bot supposed to be running, crashed, or skipped something). Every Friday-evening window from 18:45 ET onward, plus any holiday with no data, logs `NO_SETUP` / `market_closed_no_data`. This preserves the "all 21 windows produce exactly one logged outcome" invariant without lying about bot availability. **Confirmed:** the three skipped windows 15:45–18:15 bracket the daily platform maintenance/rollover break — that is the intended reason. Those three are structurally excluded (not among the 21), produce **no row at all**, and must not be logged. |
| **Q5 restated — sequence-agnostic** | Confirmed: a setup exists iff ∃ sweep touch at time *t* and ∃ reversal touch at time *u* > *t*, both before window close. A re-sweep after an initial sweep neither resets nor invalidates anything. |

**Consequence for the build:** the only things still preventing code are **Q11** (does the Sunday 18:00–24:00 ET session produce evaluations? it directly determines `time_windows` day logic and the backtest's expected row count) and **Q12** (does the validation report need a per-window derived-state audit trail, or outcome+reason only?). Both are pasted verbatim to the Handler in the Session 8 message.

### 11.7 How Q11 and Q12 were resolved (read this before disputing either)

The Handler's instruction was "no further blockers — proceed", not an explicit
answer to these two. Both were therefore implemented to the **most literal
reading of the existing documents**, with the alternative kept one line away:

- **Q11 (Sunday session)** — implemented exactly as PRD §6.1 is written:
  Monday–Friday only, New York calendar. Sunday's 18:00–24:00 session is
  present in the data (42 Sundays, 8,100 bars) and is **not** evaluated, which
  is 252 windows that could exist and do not. If the Senior Dev rules that
  Sunday belongs in scope, the change is one line in
  `time_windows.is_trading_day()` and the row count becomes
  211×21 + 252 = 4,683. Everything downstream recomputes; nothing else changes.
- **Q12 (derived-state reporting)** — the Agent's recommendation was implemented
  as a **superset**: the schema-mirror CSV carries only schema columns, while
  `window_evaluations_audit.csv` and `spot_check.csv` carry the full derivation
  (Candle A OHLC, sweep/reversal timestamps and prices, entry/SL/TP/80%
  trigger, exit kind, R, bar counts, flags) and the report's §11 table prints
  them. If only outcome+reason were wanted, the extra files can be deleted
  without touching the engine.


---

## 12. Phase 1 — Data Timezone Finding (CRITICAL — NEEDS SENIOR DEV SIGN-OFF)

**Read this before trusting either the backtest or `docs/02-TRD.md` §4.1.1.**

### 12.1 The finding

`data/nas100_m1_history.csv` does **not** contain true UTC timestamps. MT5's
`copy_rates_*` wrote **broker server time (NY+7) encoded as if it were UTC**.
The correct conversion is therefore

    NY wall clock = utc_label(epoch) − 7 hours        (constant, year-round)

not `utc_label(epoch) → America/New_York`, which is what TRD §4.1.1 prescribes.
TRD §4.1.1 explicitly says the raw epoch is "timezone-agnostic" and that NY+7 is
a display-only convenience. On this export that is wrong, and it is listed in
§4 of this file under "do not re-litigate" — which is exactly why it is being
escalated rather than quietly patched.

### 12.2 Why it is not a judgement call

Under the wrong reading, every session boundary moves and every window in the
strategy is evaluated two to three hours away from where the strategy says.
Three independent signals agree, and they are reproducible with
`m1_csv_source.convention_scores()` (the adapter prints the table):

| Signal | `epoch = true UTC` (TRD §4.1.1) | `epoch = broker NY+7` |
|---|---|---|
| Daily maintenance break | 19:00–20:00 NY in summer, 18:00–19:00 NY in winter — **moves at DST** | 17:00–18:00 NY every day, all year (0 violations) |
| Friday close | drifts 19:59 → 18:59 NY across 2026-03-08 | 16:59 NY in both EST and EDT (39/42; 3 are half-days) |
| Sunday open | drifts 21:00 → 20:00 NY | 18:00 NY (42/42) |
| July volume peak (raw labels) | would be 13:30–20:00, where 13:00–14:00 is among the **quietest** hours | 16:30–23:00, exactly the US cash session |
| The strategy's own skip set (15:45–18:15) | break would sit at 19:00–20:00 — skip set unexplained | break sits exactly inside the skip set |

An exchange does not move its session when a timezone changes; only a
timestamp convention does. The data cannot be read the TRD's way and still
describe a real NAS100 session.

### 12.3 What the code does about it

- The adapter **defaults to `broker`**, validates the four session invariants at
  load time, and **refuses to run** if they fail. A wrong convention cannot
  silently produce a plausible-looking backtest.
- `--convention utc` is available and is used by the report to print the
  rejected reading side by side, so the evidence is reproduced on every run.
- `bot/src/config.py` documents `BROKER_OFFSET_HOURS = 7` as **load-bearing on
  input**, superseding the "debug-only" framing in the old verify script.

### 12.4 What was deliberately NOT done

`docs/02-TRD.md` §4.1.1 and `bot/historical_validation/verify_broker_timezone.py`
were **not edited** — that is a Senior Dev decision, and the TRD's finding is
marked "do not re-litigate". Proposed replacement for §4.1.1 §"Practical
implication":

> MT5's `copy_rates_*` returns the bar's `time` field in **broker server time
> encoded as Unix epoch**, so the broker convention is load-bearing on the input
> side, not display-only. Forex.com/GAIN run NY+7 (server = New York local + 7h,
> following US DST dates), so the New York wall clock is obtained by
> `utc_label(epoch) − 7h`, a constant offset that holds year-round precisely
> because the broker and New York change clocks on the same dates. Verify any
> export against session invariants — daily break 17:00–18:00 NY, Friday close
> 16:59 NY, Sunday open 18:00 NY, no Saturday bars — before trusting it.

Also worth re-running: `verify_broker_timezone.py`'s own cross-check assumes the
opposite convention (it prints `as_ny` then adds 7h for the "broker display"),
so under this data its two columns cannot both match the MT5 chart. The earlier
"confirmed" result should be re-derived, not carried forward.

---

## 13. Glossary (Quick Reference)

- **Candle A:** Reference 15m candle (HH:45–HH+1:00). Produces High_A, Low_A, Mid_A, Range.
- **Candle B:** Signal 15m candle (HH+1:00–HH+1:15). Its open determines PREMIUM/DISCOUNT/VOID context.
- **Sweep:** Price reaches High_A (+1 tick) or Low_A (−1 tick).
- **Reversal:** Price returns across High_A (−1 tick) or Low_A (+1 tick) after sweep — entry trigger.
- **Handle:** Display-only unit = 1.0 NAS100 price point = 10 ticks. Never used in trigger calculations.
- **NY+7:** Confirmed broker server time convention — broker = NY local time + 7 hours, following US DST dates.
- **cross_feed_directional_only:** Tag applied to any Phase 1 backtest result sourced from Dukascopy (non-execution-venue feed) rather than MT5 native export — indicates "directionally informative, not definitive logic validation."