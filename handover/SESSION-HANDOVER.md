# Project Chronos — Session Handover

**Purpose:** This is the single living source of truth for "where this project currently stands." Any new Handler, Senior Dev, or Coding Agent picking this up should be able to read this file top to bottom and have zero remaining context gaps. No dates are used anywhere in this document by design — only session numbers and status. Update this file at the end of every working session, before closing out.

---

## 1. Project Snapshot

**Name:** Project Chronos
**Repo:** `https://github.com/MARKDISPLAYNONE/chronos-trading-system` — branch `main`
**What it is:** An autonomous trading bot + read-only metrics dashboard that executes a fully-specified manual strategy ("Zeussy Macro Time" window model) on NAS100 via MT5, starting on a Forex.com Global demo account, with a defined path to live capital. Strictly free-tier tools throughout.

**Collaboration model:** Handler (human, relays everything) ↔ Senior Dev (AI chat, owns architecture/logic) ↔ Coding Agent (AI, executes only).

**Current phase:** 🟢 **Phase 2 (Live Dry-Run, no orders) — IN PROGRESS.** Phase 1 is **CLOSED and merged to `main` at `1f9deb8`,** with Handler sign-off recorded.

Phase 2 runs the validated logic against a live MT5 feed on the Handler's own
machine for at least 5 consecutive trading days (planned: Mon 26 Oct → Tue 3 Nov
2026), logging every window and **placing no orders — structurally, not by
convention**. The window deliberately spans the **US fall-back DST transition
(Sun 1 Nov 2026, 02:00 NY)**, the one regime Phase 1's dataset could not cover
(it ends 2026-10-22 and therefore only exercised spring-forward). See §15.

✅ **The Phase 1 timestamp finding is closed.** The historical CSV does not contain true UTC; the correction was instructed by the Senior Dev and applied to `docs/02-TRD.md` §4.1.1 and `verify_broker_timezone.py`. See **§12** for the reasoning (read it before changing any timestamp code).

✅ **Tick-grid quantization is applied.** Every price is `Decimal`; `SL`, `TP` and the partial trigger are snapped to the 0.1 grid **after** calculation, pessimistically (SHORT floor, LONG ceiling), and `R` is measured against the quantized stop. Net R moved +575.135 → **+579.373** (one trade flipped, deliberately). See **§10 Session 11** and **TRD §4.1.2**; the rule is binding on Phases 2–5.

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
| `docs/02-TRD.md` | Current. **§4.1.1 fully rewritten** (timezone correction, §12): constant −7h broker→NY rule, evidence table, enforcement. Glossary handle corrected to 10 ticks + display-only note; code fence fixed. The Session 6 "unexplained local modification" flag is resolved — it was the wholesale add in `8ed0833` plus a markdown error. |
| `docs/03-APP-FLOW.md` | Updated in `0b5efb5` — §2.4 step 3 rewritten to the raw price-distance partial trigger (stale handle math removed). Rest unchanged since initial delivery. |
| `docs/04-DESIGN-BRIEF.md` | Current, unchanged since initial delivery. |
| `docs/05-DATABASE-SCHEMA.md` | Updated in `0b5efb5` — `handle_size_in_ticks` column removed from `instruments`; four new controlled-vocabulary reasons added (`market_closed_no_data`, `ambiguous_same_bar_insufficient_granularity`, `missing_market_data`, `outside_data_range`) plus three reserved ones; new §8.1 (evaluation-order rules: same-bar M1 trigger, pessimistic intrabar ordering, fill assumption, R-multiple formula). **The DDL in `supabase/migrations/` must therefore NOT create `handle_size_in_ticks`.** |
| `docs/06-IMPLEMENTATION-PLAN.md` | §6 corrected in `0b5efb5` (stale handover path). **Step 1 folder structure still not executed** — see §8. |
| `docs/07-PHASE-PLAN.md` | Current. Appendix A updated this session — Phase 0 marked formally closed, historical data source decision recorded. |
| `handover/SESSION-HANDOVER.md` | This file. Fully rewritten this session. |
| `introductory/agent-intro-prompt.txt` | Current, unchanged since rename. |
| `introductory/senior-dev-intro-prompt.txt` | Current, unchanged since rename. |
| `bot/historical_validation/verify_broker_timezone.py` | Current, NY+7-confirmed version. |

---

## 4. Confirmed Facts (Do Not Re-Litigate)

- **Instrument (NAS100):** Digits 1, Contract size 1, Tick size 0.1, Tick value $0.1/tick/lot, Volume min/max/step 0.1/1000/0.1, Margin ~0.5% notional. API symbol string: `"NAS100"`.
- **Broker timezone:** Forex.com/GAIN Capital runs **"NY+7"** — broker server time is always exactly 7 hours ahead of New York LOCAL time, following **US DST transition dates**. This part is confirmed and still holds.
  - ✅ **CORRECTED in Phase 1 (settled, see §12).** The original second half ("MT5 raw epoch is true UTC; NY+7 is display-only") was wrong. The epoch carries **broker time**, so NY+7 is load-bearing on input and the conversion is a **constant −7h subtraction** (`utc_label(epoch) − 7h`) — no zoneinfo, no DST lookup in that step. TRD §4.1.1 and `verify_broker_timezone.py` were rewritten to match. Strategy logic still never reasons about broker time as a concept: it reasons in New York wall clock, which is what the subtraction produces.
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
- **Tick-grid quantization applied (this session).** All price arithmetic is now
  `decimal.Decimal`; `Mid_A`/`Range` stay exact (Q6) while `SL`, `TP` and the
  partial trigger are snapped to 0.1 **after** calculation, pessimistically
  (SHORT `ROUND_FLOOR`, LONG `ROUND_CEILING`). 87.6% of plans had at least one
  price off-grid. Documented in **PRD §6.7** and **TRD §4.1.2** — it was an
  undocumented gap in the original spec, not an engine error.
- **Phase 1 delivered (this session).** Working, tested, reproducible:
  - `bot/src/` — `config`, `pricing`, `bars`, `time_windows`, `candle_tracker`,
    `context_classifier`, `sweep_detector`, `trade_calculator`,
    `trade_simulator`, `window_evaluator`, `m1_csv_source`, `outputs`, `report`.
  - `bot/historical_validation/validate_against_history.py` — the Phase 1 driver
    (adapter-driven, CLI, `--convention`, `--out-dir`, `--start/--end`).
  - `bot/tests/` — **128 unit tests, all passing** (`python3 -m unittest discover -s tests`).
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
| WIN / LOSS / NO_SETUP | 1,717 / 914 / 1,800 |
| Trades | 2,631 (win rate 65.3%) |
| Net R | **+579.373** (avg +0.220 R/trade) |
| Partial exits fired | 943 (35.8% of trades) |
| Prices off the 0.1 tick grid (SL/TP/partial/entry) | **0** — all quantized post-calculation (TRD §4.1.2) |
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
- **Phase 2 code is complete but has never run against a live terminal.** Every module is offline-tested (281 tests) and the API shape was measured by the probe, but the loop itself is unexercised. `--check` then a supervised first ten minutes is the plan (§15.10).
- **DST coverage gap (narrowed, see §15.2):** the dataset ends 2026-10-22, so the
  **March 8 spring-forward** is exercised but the **November 1 fall-back** is not.
  Phase 2's live monitor is now the instrument intended to close this, and it is
  the reason the dry-run window extends through Mon 2 Nov.
- **Currency P&L is absent by design** (Q9): `result_currency_amount` is NULL and `instruments.point_value` remains unconfirmed.
- **Two Phase-1 modelling limits worth knowing:** no gap fills are modelled (3 trades were open across a session break, listed in report §10), and trades are simulated independently, so two positions may overlap — that is a reporting simplification, not a portfolio-risk claim.
- **`bot/src/config.py` holds no secrets and reads no environment.** Phase 2 will need `bot/.env` for MT5/Supabase; the names are already in `bot/.env.example`.
- **Implementation Plan Step 1 (folder structure) is now PARTLY done**, corrected here rather than left overclaimed: `README.md`, `bot/src/`, `bot/tests/`, `bot/requirements.txt`, `bot/.env.example`, `bot/historical_validation/` and `data/` exist. Still missing: `dashboard/`, `supabase/migrations/`, `bot/src/live/` (Phase 2), `.github/workflows/`. (`dashboard/`, `supabase/` and `.github/` are later-phase or Handler-side — see §11.2 Q1/Q2.)
- Supabase project creation + migrations (Implementation Plan Step 2) — **out of Phase 1 scope by decision; Handler-side task, not the Agent's** (§11.2 Q1/Q2). It must not block Phase 1.
- ~~Phase 1 backtesting script not yet started~~ — **done** (`bot/historical_validation/validate_against_history.py`).

---

## 9. Immediate Next Steps (In Order)

0. ✅ **CLOSED — Phase 1 signed off and merged** (`1f9deb8`). The former
   sign-off gate is recorded as satisfied by the Handler's instruction at Phase 2
   kickoff; `spot_check.csv` holds the 24 sampled windows used as evidence.
1. 🔴 **Handler — keep the machine and MT5 terminal running** for the Phase 2
   dry-run window (Mon 26 Oct → Tue 3 Nov 2026). Downtime is not a failure: it is
   logged as `dry_run_gap_runner_down` and the run resumes forward-only.
   **Next action:** run the live probe (below) and return its output before the
   runner is built against the live API.
2. 🟠 **Phase 2 running. Clean-day clock started at the 28 Oct 08:47 NY restart.**
   No fixed end date (§15.16). Each day is judged with
   `python live/audit_triage.py --certify DATE` — five consecutive PASSes close
   the phase. Days before the restart are excluded, not rewritten.
   **The first day that can pass is 29 Oct**: 28 Oct has nine windows before the
   restart and so always contains gap rows (§15.17). Expect `INCOMPLETE` (exit 3)
   if `--certify` is run before a day's last window closes at 00:15 NY.
   The `outside_data_range` defect is understood and fixed (§15.11); the running
   runner still has the old code. Next: run `live/audit_triage.py` against the
   live log, then restart the runner to pick up the fix — the restart is what
   starts the clean-day count.
3. ✅ **DONE — Phase 2 build complete.** `mt5_live_source.py` (§15.7),
   `dry_run_runner.py`, `dst_monitor.py`, `csv_emitter.py` (§15.9). Q13/Q14
   approved and implemented. **Ready to launch** after the §15.10 preflight.
   **Live offset still unconfirmed** — needs a market-open probe run (§15.8).
4. ✅ **DONE — tick-grid quantization applied** (Senior Dev instructed).
   `Decimal` throughout, post-calculation pessimistic snap, `raw_*` audit
   columns, PRD §6.7 + TRD §4.1.2, 128 tests. Any Phase 2 order path must reuse
   `pricing.quantize_to_tick` rather than re-deriving the rounding.
5. ✅ **DONE — timestamp correction applied** (Senior Dev instructed): TRD §4.1.1
   rewritten, `verify_broker_timezone.py` replaced with the constant-offset logic
   plus an offline `--csv` mode, tests added. Re-run the verifier after every US
   DST transition date as a standing check.
6. 🔴 **Handler → Senior Dev — confirm Q11 and Q12** (see §11.7). Both were
   implemented to the literal spec + the Agent's recommendation because the
   Handler instructed "no further blockers"; each is a one-line change if the
   Senior Dev rules differently.
7. **Then Phase 3 (demo execution) only on explicit sign-off**, and only after
   Implementation Plan Step 2 (Supabase) is done Handler-side. Phase 3 adds
   `order_executor`, `trade_manager`, `supabase_client` and `main.py` — the Phase 1 modules
   (`time_windows`, `candle_tracker`, `context_classifier`, `sweep_detector`,
   `trade_calculator`) are designed to be reused unchanged.
8. Optional before Phase 3, if a longer history is cheap to export: re-run with
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
- **Commit `0b5efb5`:** D1 (old handover path rewritten to `handover/SESSION-HANDOVER.md` in 4 files; repo-wide grep for the old literal now clean), D2 (TRD glossary → 10 ticks + display-only note; APP-FLOW §2.4 step 3 → raw price-distance trigger; `handle_size_in_ticks` **dropped** from `instruments`), D3 (code fence in TRD §4.1.1 closed, fence count now even), plus the new controlled-vocabulary reasons (`market_closed_no_data`, `ambiguous_same_bar_insufficient_granularity`) and new schema **§8.1** evaluation-order rules.
- **Follow-up commit on top of `0b5efb5`** (this file): D4 wording corrected in §7/§8, §11 restructured into resolved-defects + binding-answers (§11.6), §3 doc-status rows, §9 next steps rewritten, this log.
- **Still zero Phase 1 code.** Deliberate. Hard Rule 6 held: Q11/Q12 were flagged rather than guessed.
- **Open implementation detail flagged to the Handler, not decided unilaterally:** a local SQLite mirror of `window_evaluations` cannot satisfy the `user_id` / `instrument_id` / `trade_id` foreign keys (no auth user, no Supabase, no trades table in Phase 1). Proposal: keep identical column names/types and leave those three columns nullable/unpopulated, documented in code. Awaiting confirmation alongside Q11/Q12.


**Session 9 (Phase 1 implementation — this session):** Data arrived (`data/nas100_m1_history.csv`, 287,746 M1 bars, Jan 1 – Oct 22 2026). Phase 1 built end-to-end and verified.
- **Found the timezone defect (§12) before writing any strategy code.** The export's epoch is broker time, not UTC. Proved with four session invariants plus an intraday volume profile plus the strategy's own skip set; `--convention utc` fails all four invariants on this file. Flagged for sign-off rather than silently patching the TRD.
- **Built** 13 modules under `bot/src/`, the Phase 1 driver, and 96 unit tests (all passing). Runtime 11 s for 4,431 windows. Standard library only. Code + outputs committed as `4227f64`.
- **Evaluated** 211 trading dates × 21 windows = 4,431 rows, exactly one per window, zero duplicates. 2,631 trades, 65.3% win rate, +575.1R net (zero-cost fills — a logic gate, not a P&L forecast).
- **Fixed two classification bugs found by inspecting real data:** the last export day was being labelled `market_closed_no_data` when the file simply ends (now `outside_data_range`), and US-holiday Mondays (13:00→18:00 closure) were being labelled feed holes (now `market_closed_no_data` via the 60-minute structural-gap rule, added to schema §8.1).
- **Cross-verified independently:** a second, separately written implementation reproduced every derived field across 3,959 complete windows and re-simulated all 2,631 trades with zero disagreements; all 342 ambiguity windows individually confirmed as genuine Q5 cases with no discarded valid triggers.
- **⚠️ Workspace reset incident (twice):** the sandbox re-cloned the repository
  mid-session (reflog shows `clone: from ...` at 18:34), which discards the local
  object database while keeping working-tree files. All work survived as
  uncommitted files, but commit hashes from earlier in the session became
  invalid. **Lesson for the next session: commit and push early and often, and
  verify with `git rev-parse HEAD` after pushing rather than trusting a
  successful push of a branch that may not contain the work.** Final hashes for
  this session are listed in §14.
- **Not done, deliberately:** no dashboard, no Supabase, no MT5 client, no order code — all later phases.

**Session 10 (backup + timezone correction — this session):** Handler ordered an immediate push, then the TRD/timezone correction, then a status report.
- **Push:** the sandbox had re-cloned the repo (reflog `clone:` at 18:34), so the first push carried **no work** — the branch was identical to its base. Detected by comparing `git rev-parse HEAD` to the remote tip. Re-committed everything (`0b5efb5` docs, `4227f64` code+data+report, `1db1964` handover) and pushed; remote tip now equals local HEAD, verified.
- **`main` conflict check:** **no conflict.** `origin/main` (`5e0f35c`) has `docs/02-TRD.md` byte-identical to the base (`f6168daa`); the Handler's pasted §4.1.1 never reached GitHub. `data/nas100_m1_history.csv` is also byte-identical (`98ca2fe4`) on both, so the merge will be clean.
- **Correction applied:** TRD §4.1.1 replaced with the constant −7h rule + evidence + enforcement; `verify_broker_timezone.py` rewritten (arithmetic-only conversion, offline `--csv` mode, exit-code gate); `config.py` / `m1_csv_source.py` docstrings repointed at the corrected section; report §1 status changed from "awaiting sign-off" to "RESOLVED"; 8 new tests (`test_verify_broker_timezone.py`) → **104 tests, all passing**. Verifier run against the real export: all four invariants PASS, exit 0; the old reading fails 3 of 4.
- **Not done, deliberately:** no dashboard, no Supabase, no MT5 client, no order code — all later phases.

**Session 11 (tick-grid quantization — this session):** Senior Dev retracted an
earlier garbled-paste diagnosis (nothing was wrong with the R formula) and
instructed the real change: quantize order prices to the tick grid.
- **Diagnosed first, on the real output:** 1,297/2,631 (49.3%) of plans had an
  off-grid `SL` **and** an off-grid `TP`; 1,980 (75.3%) had an off-grid partial
  trigger; 2,065/4,139 `candle_a_mid` values were also off-grid, which is why
  `Mid_A`/`Range` were deliberately **left exact** rather than dragged onto the
  grid.
- **Implemented:** `pricing.py` rewritten around `Decimal` (`D()`, `is_on_tick_grid`,
  `quantize_to_tick(value, tick, direction)`, `direction_for`); `trade_calculator.py`
  rewritten to keep `raw_stop_loss` / `raw_take_profit` / `raw_partial_trigger` for
  audit, to clamp the partial inside TP, and to raise on misordered plans;
  `config`, `bars`, `m1_csv_source`, `candle_tracker`, `context_classifier`,
  `sweep_detector`, `window_evaluator`, `report`, `outputs` converted to `Decimal`.
  `M1Bar.__post_init__` and `sweep_detector._levels()` coerce at the boundary —
  `Decimal + float` raises in Python, so no float may leak into a comparison.
- **Tests:** +22 in `test_quantization.py` (floor/ceiling primitives, an off-grid
  Candle A 25430.5/25411.0 → `Mid_A` 25420.75, pessimism invariants across all 58
  observed ranges, partial-never-beyond-TP, the on-grid no-op case); the rest of
  the suite re-pointed at `Decimal` comparisons. **128 tests, all passing.**
- **Re-ran everything** (18 s, 4,431 windows, same 2,631 trade ids):
  WIN 1,717 / LOSS 914 / NO_SETUP 1,800; win rate 65.3%; net R **+579.373**
  (was +575.135, **+4.238**); avg +0.220; partials 943.
  **0** off-grid values remain in any price column.
- **Before/after diff, honestly:** 1,057 final exit prices and 1,201 R-multiples
  moved; mean ΔR **+0.003529**, range **−1.983 to +0.043**, **1,200 up / 1 down**.
  Exactly **one** trade flipped WIN→LOSS: a LONG whose TP ceiling'd from
  25,232.55 to 25,232.60, a price the market never printed before the bar rolled
  into the (also tightened) stop. That is the pessimism rule doing its job.
  The audit CSV now exports `raw_*` columns plus `prices_quantized` and
  `quantize_direction` (the earlier comparison script `KeyError`'d on exactly
  these missing columns — fixed at source rather than worked around).
- **Docs:** PRD §6.7 note + new TRD §4.1.2 (rule, pessimism argument, worked
  example from real rows, consequences, file map). Report §2 rewritten (its old
  claim that a clean TP run is worth slightly under 1R was pre-quantization
  arithmetic) and a new **§2.1** added that computes the quantization accounting
  from the run itself instead of hard-coding numbers. Also fixed the stale
  `§14` pointer in §10 by adding §14 below.
- **Not done, deliberately:** no dashboard, no Supabase, no MT5 client, no order
  code — all later phases.

**Session 12 (Phase 2 kickoff — this session):** Phase 1 merged and closed; Phase 2
scoped, documented and started. No engine code written yet — deliberately, see §15.4.
- **Merge verified, not assumed.** `origin/main` = `1f9deb8`, a real merge commit
  with parents `5e0f35c` (data) and `caca96a` (this branch). `git merge-base
  caca96a origin/main` = `caca96a`, i.e. the branch is fully contained in `main`.
  `git diff caca96a origin/main` is **empty** — main's tree is byte-identical.
- **⚠️ Workspace reset, third occurrence.** The sandbox re-cloned at 14:19
  (reflog `clone:`), discarding the object DB and checking the branch out at its
  base `8ed0833`, so a naive `git status` looked like "everything deleted". The
  remote branch still had `caca96a`. Recovered with `fetch --unshallow` +
  `reset --hard caca96a`, then fast-forwarded onto `main`; branch and remote are
  both `1f9deb8` now, tree clean, 128 tests green before any new work.
- **Ran a DST pre-check before planning anything** (§15.2), because the dry-run
  window starts Mon 26 Oct and today (25 Oct) is the EU fall-back date. Result:
  the constant −7h rule survives the EU date — the maintenance break sits at
  16:59→18:00 on **every** date in the file, including the Mar 8–28 stretch
  between the US and EU spring-forward dates, which is exactly where an
  EU-following broker would have shown a one-hour shift. It does not.
- **Docs updated before code** (per Cross-Phase Rule 3): `07-PHASE-PLAN.md`
  Phase 1 marked CLOSED with criteria checked, Phase 2 marked IN PROGRESS with
  the Handler's four scoping decisions and a sharpened exit-criteria list;
  Phase 0's stale "Nearly Closed" status corrected; the Phase 1 deliverable line
  that said "never hardcoded offsets" corrected (it contradicted TRD §4.1.1);
  schema vocabulary gained `"dry_run_gap_runner_down"`.
- **Built the unblocking artifact, not the guess.** `bot/live/probe_mt5_live.py`
  — a read-only probe the Handler runs on the Windows box, which measures the
  live API instead of assuming it (§15.3). 15 new offline tests
  (`test_probe_mt5_live.py`) pin its pure logic → **143 tests, all passing.**
- **Not started, deliberately:** `mt5_live_source.py`, the runner, the live DST
  monitor. Two questions in §15.4 change their shape, and the probe output has
  to come back first.

**Session 12, follow-up after the first live run:** the Handler ran the probe on
the Windows box. It confirmed the connection, the demo account, the broker and
the full NAS100 spec (all matching Phase 1 — see §15.6), then crashed on
`SymbolInfo.session_quotes`, a field this MT5 build does not have. The probe was
at fault, not the feed. Rebuilt it so optional fields cannot raise and each
section is isolated (§15.6), with 18 new tests that inject a fake terminal
shaped like the real one. **Still needed from the Handler: a re-run**, to collect
the rates dtype/epoch, forming-bar and offset-measurement sections.

**Session 12, follow-up 2 (false ESCALATE + live source):** the Handler re-ran the
probe on **Sunday 25 Oct**, market shut since Friday 16:59 NY. It reported a ~43h
"broker offset" and escalated; that was the probe measuring weekend closure, not a
timezone defect — confirmed by the four session invariants passing on the same
run's bars, and by the calendar (this session's date is a Sunday).
- **Fixed as TRD §4.1.1 rule 5 (new):** a stale tick is not a clock sample. The
  offset is reported only when the tick is younger than `STALE_TICK_SECONDS`;
  otherwise the probe prints `market closed — cannot measure live offset, last
  tick is Xh old` and escalates nothing. TRD §4.1.1 needed no other change.
- **Corrected an accepted finding.** "Newest bar still forming, must be dropped"
  was an artefact of the *same* frozen tick: with the tick as "now", Friday's
  closing bar looks unfinished forever. Current broker time is now derived from
  the system clock (`America/New_York` + 7h) everywhere, including forming-bar
  detection. Excluding forming bars is still correct — it rests on the API
  contract — but the evidence behind it was contaminated, and that is now stated.
- **Built `bot/src/mt5_live_source.py`** (§15.7): same `(BarSeries, LoadReport)`
  contract as the CSV adapter, reusing `to_ny_wall`/`check_session_invariants`
  rather than re-deriving them; drops forming bars with a one-sided safety margin;
  rejects bad bars instead of repairing them; merges repeat poll rows; bounds
  memory; refuses a non-demo account and a wrong tick size; gates on the four
  session invariants. Phase 1 modules untouched — proven by an integration test
  that runs a fake live feed into the real `evaluate_window` and gets a complete
  PREMIUM → SHORT → partial → TP trade.
- **Windows `tzdata` risk found and handled:** Windows ships no IANA database, so
  `zoneinfo` raises and Phase 2 cannot start. Added to `requirements.txt`, checked
  at startup with an actionable message, and reported by the probe's new
  environment section (which runs before the terminal is touched).
- **Tests: 161 → 219.**
- **Still open:** live offset confirmation (needs a market-open run — must not be
  described as "verified live" until then), Q13/Q14, and the runner itself.

**Session 13 (Phase 2 build — this session):** Q13 and Q14 approved; runner, DST
monitor and CSV emitter built. **281 tests, all passing.**
- **Docs first:** the Phase 3 crash-recovery requirement is now written into
  `docs/07-PHASE-PLAN.md` (as instructed): genuine broker-side reconciliation is
  required once real orders exist, and Phase 2's log-and-abandon is explicitly
  not acceptable there. Mechanism not built — recorded so it is not rediscovered
  cold at the boundary.
- **`bot/live/csv_emitter.py`** — append-only; reuses `src.outputs`' row builders
  so live and backtest columns cannot drift; re-reads the CSV on restart so a
  crash cannot duplicate a row.
- **`bot/live/dry_run_runner.py`** — the loop. Three findings came out of writing
  its tests, all fixed:
  1. **Backfill would have silently broken Q14.** After a reconnect MT5 returns
     the bars for the minutes we were blind for, so a naive runner scores the
     missed window as a normal evaluation. Fixed with an observation ledger: a
     window is only evaluated if its whole span was watched. Same rule applied to
     a trade's **exit minute**, so an outage mid-trade yields a gap, not a WIN.
  2. `_gap_scan_lower_bound` used `min` where the later bound was needed, pulling
     a full extra day of windows into the launch-day scan.
  3. `evaluate_window` raises `IndexError` on an empty `BarSeries`. Guarded
     runner-side (Phase 1 logic left untouched) so a feed with no bars cannot
     kill the loop or score a window.
- **`bot/live/dst_monitor.py`** — independent background process; loads
  `verify_broker_timezone.py` **by path** and calls its `verify()`, so the
  monitor and the Phase 1 gate cannot disagree (a test asserts the verdicts
  match). Records the measured offset (staleness-gated), the four invariants,
  NY-minute back-steps (a repeated local hour) and gaps, plus per-date schedule
  integrity.
- **DST double-fire is now provable, not assumed:** at startup every scheduled
  window in the planned range is converted to UTC and the runner refuses to start
  if any is ambiguous or non-existent. A test forces 2026-11-01 to count as a
  trading day to prove the guard fires.
- **Full-day integration test:** a synthetic weekday through the real runner
  produces exactly 21 rows for that trading date, 21 distinct labels, valid
  outcome + reason each, zero gap rows.
- **Readiness:** ready for a Monday 26 Oct launch after a ~5-minute preflight
  (`pip install tzdata` → probe → `--check`). See §15.10 for the honest residual
  risk and the day-one partial-coverage note.

**Session 14 (defect investigation — this session):** traced `outside_data_range`
firing on a healthy connection. **Two distinct defects**: a backtest verdict
applied to a live feed (its trigger is a *late bar*, not a disconnect), and the
row being written once and never revisited, which made a sub-minute glitch a
permanent wrong entry. Fixed both (§15.11). **309 tests, all passing.**
- Reproduced: 0/21 misfires with prompt bars, 21/21 with the closing bar 45s
  late — same feed, same clock, no disconnect. After the fix: 0/21.
- `outside_data_range` is now unemittable (raises), genuine stalls are
  `live_feed_stalled`, late tails are deferred and re-checked.
- Found that **my own Q13 tests encoded the bug** — they fed a dead feed and
  asserted a NO_SETUP row for it. Fixtures corrected.
- `bot/live/audit_triage.py` + `live/known_bad_windows.txt` do the log analysis
  on the Handler's machine; the audit CSV is not in this workspace, so no counts
  or row verdicts are claimed here.
- **No day can be certified clean**; the four-part certification rule is in
  §15.11 and the clean-day clock restarts at the restart, not at launch.
- The live runner was **not** restarted or patched (per instruction): the fix is
  committed and inert until the Handler restarts it.

**Session 15 (29-row reclassification + forced kill — this session):** the
`outside_data_range` breakdown is corrected from 8/21 to **22 feed stalls / 7
late tails / 0 unexplained**, across three separate outages. **331 tests pass.**
- Two of the three outages (27 Oct 06:37-09:35 and 27 Oct 13:13 to 28 Oct
  ~03:20) were not previously known. Both confirmed in `dst_monitor.csv`.
- The Handler's "0/0 is a different failure mode" instinct was right about the
  *shape* and wrong about the cause being different: one line of code produces
  both shapes, depending only on how stale the newest bar is.
- `--illegal-reasons` allow-list fixed (was flagging 43/46 by omitting every
  WIN/LOSS reason; now derived from the modules) plus two silent-pass holes.
- **The forced kill exposed a real bug**: an open observation interval is
  persisted as `None`, i.e. "extends to now", so a restart would have counted
  the entire downtime as watched and scored windows from back-filled history.
  Fixed and tested. This would have fired on tonight's restart, not before.
- `--check` now writes nothing; `--state` prints what the next start will do.
- 04:45-05:15 confirmed correct: `open - Low_A = -1.6`, VOID stands.
- `known_bad_windows.txt` rewritten with all 29 rows and two categories (`stall`
  → `live_feed_stalled`, `tail` → not recoverable, no invented outcome).

**Session 16 (live triage, 28 Oct — this session):** first real-data session since
the restart. **367 tests pass.**
- `[FAIL]` on every monitor sample was a **zero-denominator artefact**, plus a
  second shape (a Sunday clipped by the window's left edge, `0/1`). Both now report
  `not applicable` and pass, with the omission recorded in `notes`. Replaying all
  3088 archived samples: 82 → 0 failures, and none of them contained a real
  breach. **Nothing certification-facing read that column**; history needs no
  annotation.
- The 231-minute `polling_gap` at startup: the four gap rows were **correct** (Q14
  working on real data), but the event was **mislabelled** and wrote an **inverted
  interval** — my bug from session 15. Fixed.
- "10 trading dates" is a rolling `today-3 … today+10`, **not** a fixed list; there
  is no Nov 3 cutoff. The misleading comment that invited a hard-coded end is gone.
- Ctrl+C / SIGTERM are now a clean stop (`CLEAN STOP —` line), documented for the
  Handler together with when a force-kill is genuinely necessary.
- `audit_triage.py --certify DATE`: the four-part rule as a command. No more
  relay-and-interpret.
- **Clean-day clock starts at the 28 Oct 08:47 NY restart; no fixed end date.**

**Session 17 (certify hardening — this session):** `--certify` reported a
"missing window" that had simply not happened yet. **389 tests pass.**
- Confirmed against the schedule: at 09:30 NY on 28 Oct, `09:45-10:15` is the
  first window that has not closed. Not a defect.
- `--certify` now returns **PASS / FAIL / INCOMPLETE / NOT_APPLICABLE** (exit
  0/1/3/2) and classifies every absent window as not-due, awaiting-trade,
  awaiting-data or genuinely missing. Only the last one fails.
- `runner_state.json` is read automatically for the awaiting-trade case;
  removing it falls back to FAIL rather than excusing an unexplained absence.
- `--now ISO` pins the clock (naive = NY) for replay and deterministic tests.
  Default `--out-dir` now matches the runner's.
- **28 Oct can never itself PASS** — nine of its windows precede the 08:47
  restart, so it always carries gap rows. The first certifiable day is 29 Oct;
  the clock still starts at the restart (§15.16, now qualified in §15.17).

**Session 18:** a report that `--certify` had called six future windows missing
traced to a **stale checkout**, not the logic — `02a2837` reproduces the reported
output byte for byte; `ab7d8f5` returns INCOMPLETE on the same fixture. **404
tests pass.**
- Every verdict now states its rule (`logic: certify v2 (not-due aware)`), so an
  old binary can no longer masquerade as a verdict. **This is the fix.**
- Regression tests lock the mid-afternoon shape: six future windows ⇒ INCOMPLETE,
  never FAIL, never named in a coverage failure; a real stall still fails.
- `--digest N`: one line per trading day + consecutive-PASS streak.
- `--certify` FAIL prints last runner activity (heartbeat vs newest event, NY).
- Startup banner reworded: it is a **capability notice** ("handlers armed for this
  run"), not a shutdown record; `CLEAN STOP` remains the only stop record.

**Session 19:** the Handler is away for whole days and terminal scrollback
truncates. **439 tests pass.**
- `live/log_mirror.py` — a third, read-only process that copies the three live
  CSVs into `logs/DST|RUNNER|TRADES_<NY date>.csv`, write-through with
  `flush()`+`fsync()` per row, resumable from a byte offset, no dupes or gaps
  across restarts. Never writes to `live/output/`; no verdict depends on it.
- `live/snapshot_and_push.py` + `snapshot_and_push.bat` — at 00:20 NY, commits
  and pushes the previous completed day to the feature branch. A failed push is
  logged, exits non-zero, needs no repair, and is retried the next night.
- TRADES files are grouped by `trading_date` (not the row clock) so a 23:45
  window stays with its trading day; the snapshot stages every date so a
  late-resolving trade is not orphaned.
- The job refuses to run on `main`/`master`.

**Session 20:** `log_mirror.py` crashed on its first live run — fsync on a
read-only handle, which POSIX allows and Windows rejects (`EBADF`). **447 tests
pass.**
- Fixed via a single `_fsync()` helper that fsyncs the open write handle and
  refuses read-only ones, so the mistake cannot recur on one platform only.
- The crash left day files written but no state; a fixed restart would have
  duplicated them. `_resume_from_destination()` rebuilds the offset from the day
  files, so a lost state is recoverable rather than destructive.
- `--status` now warns when day files hold rows but no offset is saved — the
  reading that sent the first diagnosis the wrong way.
- The `logs/*.csv` files reported as committed are **not in the repository**:
  never committed on any branch. They are crash artifacts (verified by replaying
  the shipped code), not seed data.

**Session 21:** the state-file rename failed on Windows (`WinError 5`), and the
fallback it forced was **duplicating rows**. **460 tests pass.**
- Rename failure is external and transient (OneDrive/AV holding the destination);
  proved it is *not* our stale handle by inspecting open descriptors at
  `replace()` time.
- `save_state` retries WinError 5/32/33/117 (and POSIX equivalents) five times
  over ~1.5 s, then abandons the save rather than crashing; other errors are not
  retried.
- **Serious:** a stale state file was trusted, so a restart re-read rows already
  mirrored. `_bootstrap_offsets()` now takes the later of the saved offset and
  what the day files imply. Found by the multi-cycle test, not by reading.
- `-1` sentinel removed: counts are counts, failures print on their own line.
- `sync_client_hint()` names the suspected sync client and points at `--state`.

**Session 22:** audited every durable writer against the OneDrive lock class.
**485 tests pass.**
- **`dry_run_runner.py` was equally exposed**: `RunnerState.save()` had no retry
  *and no fsync*, at five sites — startup death, a false unclean-shutdown report,
  and a lost ledger on power cut. Now uses `live.durable`; still raises on a
  persistent lock, because the ledger has no second copy.
- `dst_monitor`/`csv_emitter` were already safe (append-only, no rename).
- `snapshot_and_push` was safe but lost a night on `.git/index.lock`; now retries
  lock and network errors, never auth failures.
- New `live/durable.py` is the single implementation; the mirror and the runner
  both import it.
- Mirror state now relocates itself to `%LOCALAPPDATA%\Chronos\` when a synced
  folder is detected, adopting any existing file; `--state` still wins.
- `\p` SyntaxWarning fixed (raw docstring), with a test that imports under
  `-W error::SyntaxWarning`.

**Session 23:** two Windows-environment failures. **501 tests pass.**
- `--status` said "no sync client detected" with `$OneDrive` set: the env read
  was fine, the *predicate* required the root to be a string prefix of the path.
  Now three outcomes (contained / by path / configured), component-wise
  comparison on `realpath`, and relocation in all three — the state file is
  disposable, the crash is not. `--status` shows what was compared.
- `FileNotFoundError: [WinError 2]` on the first scheduled run: could **not**
  confirm the PATH hypothesis (Windows gives the same text for a missing
  executable and a missing cwd, with no filename). Both causes now covered:
  `find_git()` searches PATH then the standard Git install locations, cwd is
  checked and named, and every failure prints the operation, binary, cwd, PATH.
- `--doctor` diagnoses it from the scheduled context; `.bat` passes arguments
  through.
- `tests/test_scheduled_environment.py` simulates a stripped PATH — this bug
  class is now testable locally.

**Session 24:** the Handler's `which git` -> `/mingw64/bin/git`, which **was not
in the fallback list** from 15.23. **506 tests pass.**
- Git for Windows has three `git.exe`s and Git Bash always resolves
  `mingw64\bin`; the fallback only searched `cmd\` and `bin\`. Now all of
  `cmd\`, `mingw64\bin\`, `bin\`, `usr\bin\` plus scoop/chocolatey shims.
- `%VAR%` expansion used `os.path.expandvars`, which is Windows-only — so the
  fallback was untestable off Windows. Now expanded explicitly, separators follow
  the platform, and "PATH empty, only mingw64 present" is a test.
- `python` resolved under Task Scheduler (the script logged), so only git is in
  question; `cmd` is absent from the Handler's shell, hence `--doctor`.
- Python 3.14: no removed/deprecated APIs in use; version now printed by
  `--doctor`. Suite runs on 3.11 here, so 3.14 behaviour is unverified.

---

## 11. Phase 1 Pre-Flight — Questions and how each was resolved (Q1–Q12)

**Status: all 12 resolved — 10 answered explicitly by the Senior Dev (§11.6), 2 implemented to the literal spec with the alternative kept one line away (§11.7). Phase 1 is built and complete.**

This section is kept as the record of *why* the code looks the way it does. It was written when the Coding Agent stopped at Hard Rule 6 because Phase 1's specification contained model-level gaps that could not be resolved by reading the documents.

**Where the answers live now (docs-before-code, Phase Plan Cross-Phase Rule 3):**
- Same-bar ambiguity rule, intrabar exit ordering, fill assumptions, R-multiple formula → **`docs/05-DATABASE-SCHEMA.md` §8.1** (new subsection), plus new controlled-vocabulary reasons in §8.
- Stale handle/unit text and the malformed fence → fixed in `docs/02-TRD.md`, `docs/03-APP-FLOW.md`, `docs/05-DATABASE-SCHEMA.md`.
- Phase 1 scope, data contract, sample size → recorded in §11.6 below.


### 11.1 Documentation defects — ALL FOUR RESOLVED

Senior Dev confirmed D1–D4 as real and prescribed exact fixes. All applied in commit `0b5efb5` (`docs: fix D1-D4 doc defects and bind Phase 1 decisions`). See §10, Session 8. Table retained as the record of what was wrong and what was changed.

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

## 12. Phase 1 — Data Timezone Finding (RESOLVED — CORRECTION APPLIED)

**STATUS: RESOLVED.** Escalated, then corrected on Senior Dev instruction. `docs/02-TRD.md` §4.1.1 has been rewritten and `bot/historical_validation/verify_broker_timezone.py` replaced. Both now state the constant-offset rule, and the verifier reproduces the invariant checks offline. Nothing in this section is an open question any more — it is kept because it explains *why* the code and docs look the way they do, and because the next person will otherwise "helpfully" reintroduce a DST lookup.

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

### 12.4 What was applied (Senior Dev instruction, this session)

`docs/02-TRD.md` §4.1.1 was **replaced** with the corrected rule and
`bot/historical_validation/verify_broker_timezone.py` was **rewritten**:

- Broker → New York is a **constant −7h subtraction** on the raw epoch label.
  No `zoneinfo`, no `pytz`, no DST lookup in that step — the DST shift is already
  inside the raw value, and a second DST-aware conversion would double-count it.
- The section now documents the EDT/EST arithmetic explicitly (broker UTC+3 →−7h→
  UTC−4; broker UTC+2 →−7h→ UTC−5), the five pieces of evidence, and the rule
  that `zoneinfo` is used only for reporting/regime tagging and the
  non-existent-local-time guard (`time_windows.localize()`), never for the
  broker→NY conversion.
- The verifier gained an **offline `--csv` mode** so the same invariant gate runs
  without Windows or MT5 (`python3 verify_broker_timezone.py --csv ../data/nas100_m1_history.csv`),
  plus unit tests (`bot/tests/test_verify_broker_timezone.py`) covering the
  constant-offset property in both DST regimes and proving that the old UTC
  reading fails the gate.
- `bot/src/config.py` and `bot/src/m1_csv_source.py` docstrings were updated to
  point at the corrected §4.1.1 instead of describing it as wrong.

The earlier "confirmed" result from `verify_broker_timezone.py` has therefore
been **re-derived**, not carried forward.

---

## 13. Glossary (Quick Reference)

- **Candle A:** Reference 15m candle (HH:45–HH+1:00). Produces High_A, Low_A, Mid_A, Range.
- **Candle B:** Signal 15m candle (HH+1:00–HH+1:15). Its open determines PREMIUM/DISCOUNT/VOID context.
- **Sweep:** Price reaches High_A (+1 tick) or Low_A (−1 tick).
- **Reversal:** Price returns across High_A (−1 tick) or Low_A (+1 tick) after sweep — entry trigger.
- **Handle:** Display-only unit = 1.0 NAS100 price point = 10 ticks. Never used in trigger calculations.
- **NY+7:** Confirmed broker server time convention — broker = NY local time + 7 hours, following US DST dates.
- **cross_feed_directional_only:** Tag applied to any Phase 1 backtest result sourced from Dukascopy (non-execution-venue feed) rather than MT5 native export — indicates "directionally informative, not definitive logic validation."

---

## 14. Commit Reference & Reproduction (this branch)

Branch: `arena/3e3fffac-chronos-trading-system` (never `main`). Verify any claim
with `git rev-parse HEAD` against `git ls-remote origin <branch>` before trusting it.

| Commit | Contents |
|---|---|
| `0b5efb5` | Docs D1–D4 fixes, Q1–Q10 bound, schema §8.1 evaluation order |
| `4227f64` | Phase 1 engine, driver, 96 tests, data, generated report |
| `1db1964` | Handover hash repoint + workspace-reset incident record |
| `f0584be` | TRD §4.1.1 timezone correction + rewritten verifier + 8 tests |
| *(this session)* | Tick-grid quantization (PRD §6.7, TRD §4.1.2, Decimal rewrite, 128 tests, regenerated outputs) |

Reproduce from a clean checkout:

```bash
cd bot && python3 -m unittest discover -s tests          # 128 tests
cd .. && python3 bot/historical_validation/validate_against_history.py \
    --csv data/nas100_m1_history.csv                      # regenerates output/
python3 bot/historical_validation/verify_broker_timezone.py \
    --csv data/nas100_m1_history.csv                      # 4/4 invariants, exit 0
```

---

## 15. Phase 2 — Live Dry-Run (kickoff state)

**Status: scoped and documented; runner not built.** Target window **Mon 26 Oct →
Tue 3 Nov 2026** (6 NY trading days), which deliberately spans the US fall-back
transition on **Sunday 1 Nov 2026, 02:00 NY**.

### 15.1 What Phase 2 may and may not contain

Binding, from the Handler's kickoff instruction:

1. Live MT5 connection, real-time data, full window-evaluation logic.
2. **Zero orders, structurally.** The order call must not exist in the Phase 2
   entrypoint's code path — not a flag defaulting to off, not a commented-out
   line. Asserted by a test that walks the entrypoint's import graph.
3. Phase 1 logic modules (`window_evaluator`, `candle_tracker`,
   `context_classifier`, `sweep_detector`, `trade_calculator`, `trade_simulator`,
   `pricing`) are reused **unmodified**. A logic rewrite would silently
   invalidate Phase 1's validation.
4. Local CSVs, append-only, same schema mirror as Phase 1. No Supabase, no
   dashboard work in this phase.
5. Downtime is logged as `dry_run_gap_runner_down` and the run continues forward.
   Missed windows are **never** reconstructed.
6. Any live-API behaviour that differs from the CSV assumptions is escalated,
   not patched around silently.

### 15.2 DST pre-check performed at kickoff (read before the 1 Nov run)

The dry-run window is only meaningful if the timestamp rule holds when the clocks
change. Phase 1's export ends **2026-10-22**, so it exercises the March
spring-forward but never the November fall-back. Two questions matter now:

**(a) Does today's EU fall-back (25 Oct) move anything?** No — and this was
checked rather than assumed, because the TRD claims the broker follows **US**,
not EU, transition dates, and that claim is exactly what an EU-date broker would
falsify. Measured on the export:

| Period | EU vs US DST state | Maintenance break observed (NY, under −7h) |
|---|---|---|
| Jan 1 – Mar 7 | both standard | 16:59 → 18:00 |
| **Mar 8 – Mar 28** | **US on DST, EU still standard** | **16:59 → 18:00 — no shift** |
| Mar 29 – Oct 22 | both on DST | 16:59 → 18:00 |

Every one of the 18 March breaks is `16:59 → 18:00`; every Sunday in the file
opens at exactly 18:00; every Friday closes at 16:59 except three holidays
(Good Friday 3 Apr 09:14, Juneteenth 19 Jun 12:59, Independence Day observed
3 Jul 12:59 — all genuine early closes). A broker on EU dates would have shown a
one-hour shift in the Mar 8–28 column. It does not. **Conclusion: the constant
−7h rule is sound on both sides of both spring transitions, and today's EU
change should be a no-op.**

**(b) What is still untested?** The November fall-back itself. One nuance worth
stating plainly, because it changes what the monitor can prove: the 02:00 NY
change happens while the venue is **shut** (Friday 16:59 → Sunday 18:00). So no
bar can ever be observed inside the repeated hour — the transition is observable
only *indirectly*, via the Sunday 18:00 open and Monday's break still sitting at
17:00–18:00 NY. The same caveat applies retroactively to Phase 1's "spans the
March transition" claim: what it proved is that the offset was 7 on both sides,
not that the switch instant is where the model says. That is why the live probe
measures the broker clock **directly** (`tick.time − time.time()`) as well as
checking bar invariants: the direct measurement is the only thing that can see
the switch itself.

### 15.3 Build plan (in order)

| Step | Artifact | Why this order |
|---|---|---|
| 0 | `bot/live/probe_mt5_live.py` — **built this session** | Measures the live API (symbol spec, epoch behaviour, forming bar, measured clock offset, invariants) so the adapter is written against facts, not assumptions. Read-only. |
| 1 | `bot/src/mt5_live_source.py` | Same contract as `m1_csv_source.load_bars` → `(BarSeries, LoadReport)`, producing a *rolling* series. Reuses `to_ny()`/`check_session_invariants`; never re-derives conversion. Drops the forming bar. |
| 2 | `bot/live/dry_run_runner.py` | The loop: wait for a window to close → evaluate with the Phase 1 modules → append the row. Owns the gap protocol and startup/shutdown events. |
| 3 | `bot/live/dst_monitor.py` | Continuous: measured clock offset + the four invariants on a trailing window, sampled across 1 Nov. |
| 4 | `bot/tests/test_no_order_path.py` | Walks the runner's import graph and asserts no order-capable symbol is reachable. The structural guarantee, as a test. |
| 5 | `bot/tests/test_mt5_live_source.py` | Contract tests against a **fake** MT5 module (the real one is Windows-only), including the forming bar, a disconnect mid-poll, and a symbol that does not exist. |

### 15.4 Open questions — need a ruling before steps 2–4 are built

**Q13 (blocking, log semantics).** The Handler's instruction is "one row per
window exactly as windows close, no backfill/replay". That is not achievable
literally: `evaluate_window` calls `trade_simulator.simulate(...)` with
`config.MAX_BARS_TO_RESOLVE_TRADE`, walking forward up to **5 days** of bars from
the trigger to find the exit. A traded window therefore has no final outcome at
its own close. Options:
 - **(A) recommended:** append the row when its outcome is determined — NO_SETUP
   rows at window close, traded rows when the simulated trade resolves (usually
   within minutes). Append-only is preserved; the only cost is that "rows
   written" lags "windows closed", which is recorded in the audit CSV.
 - (B) append a provisional row at close and never update it → it would record an
   outcome the engine had not yet decided.
 - (C) split into two files (close event + outcome event) → two rows per window,
   contradicting "one row per window".

**Q14 (blocking, crash semantics).** If the runner dies while a trade is open,
on restart that trade's exit bars are **missing from memory** but present in
MT5's own M1 history. Two defensible readings of "never fabricate missed
windows":
 - **(A) recommended:** conservative route — the affected window is logged
   `dry_run_gap_runner_down` and never resolved. Costs a handful of rows in
   exchange for "the log only ever contains what the bot actually saw".
 - (B) resolve it from `copy_rates_range` after restart, on the grounds that the
   broker's own bars are evidence, not fabrication.
 The difference is definitional for the Handler/Senior Dev, not technical, so it
 is asked rather than chosen.

**Q15 (non-blocking flag, handled).** `copy_rates_from_pos(..., 0, N)` includes
the **currently forming** M1 bar, which the CSV never had. The adapter will
exclude it (bar closed ⟺ `bar.time + 60 ≤ now_broker`). Flagged because it is
precisely the class of CSV→live difference that rule 6 covers; the probe prints
whether it is happening on the terminal.

**Q16 (non-blocking flag).** Supabase/`system_events` are named in the Phase 2
objectives in `07-PHASE-PLAN.md` but are excluded by the kickoff instruction. The
phase plan now records that as a decision rather than a gap, and marks the
dashboard exit-criterion **deferred, not waived**.

**Q17 (non-blocking flag).** Phase 2's entry criterion requires Phase 1 sign-off;
recorded as satisfied by the kickoff instruction, with `spot_check.csv`
(24 windows) as its evidence — noted so the gate is traceable, not assumed.

### 15.5 What cannot be verified from the sandbox

`MetaTrader5` is Windows-only and is **not importable here** (verified:
`ModuleNotFoundError`). So every live claim — connection, symbol spec, epoch
behaviour, forming bar, measured offset — must come from the Handler's machine
via `probe_mt5_live.py`. What *is* verified here: 143 offline tests, the probe's
read-only guarantee, and the arithmetic of the conversion and the invariants. No
Phase 2 result will be reported as "passed" on sandbox evidence alone.

### 15.6 Live facts measured on the Handler's terminal (first probe run)

The first live run crashed partway, but everything it printed before crashing is
real terminal output and is recorded here as measured fact — not inference.

| Fact | Measured | Status |
|---|---|---|
| MT5 connection | live terminal, logged in | ✅ |
| Account type | `trade_mode: 0` (demo) | ✅ Phase 2 is allowed to run |
| Broker | Gain Global Markets / FOREX.com Global | ✅ same broker as the export |
| NAS100 `digits` / `point` | 1 / 0.1 | ✅ |
| `trade_tick_size` / `trade_tick_value` | 0.1 / 0.1 | ✅ matches `config.TICK_SIZE` exactly — quantization (TRD §4.1.2) stands |
| `volume_min` / `step` / `max` | 0.1 / 0.1 / 1000.0 | ✅ 10.0 volume step is legal |
| Live spread | **30 points = 3.0 index points** | ℹ️ new information, see below |

**The spread number.** 3.0 index points is roughly 30× the tick size. It changes
nothing in Phase 1 or Phase 2, both of which assume zero spread by decision (Q8)
and score logic, not execution. It is the first real execution-cost figure the
project owns, and it is enough to matter for Phase 3: a clean TP run is worth
≈1R and the stop is ≈0.5·Range, so on a median range a 3.0-point spread is a
non-trivial fraction of 1R. Recorded in `07-PHASE-PLAN.md` under Phase 3.

**What is still missing** (the sections the crash skipped): rates dtype and
epoch type, the forming-bar check, and the direct broker-offset measurement.
Those are the three that gate the live adapter, so the probe fix (next
paragraph) must be re-run before `mt5_live_source.py` is written.

#### The probe bug, and why it mattered

The run died on `SymbolInfo.session_quotes` — an attribute this package build
does not expose. The field is a cosmetic session counter that the engine never
reads, so nothing about the live data was wrong; the probe was.

The real defect was not the missing `getattr`. It was that one unguarded
attribute could abort five sections, and it did: the crash landed *before* the
clock, rates and forming-bar checks, i.e. before every measurement that actually
matters. Two changes:

1. `field(obj, name)` reads optional attributes without raising and reports their
   absence as a **note**, never an escalation.
2. Each section runs inside its own guard. A section that raises is recorded as
   an escalation and **the probe continues** to the remaining sections.

Class of bug now closed by test, not by intention: `test_probe_mt5_live.py`
injects a fake MT5 module shaped like the real terminal (no `session_quotes`) and
asserts the probe still reaches the offset measurement and exits 0. There is a
second test where an *entire section* raises and the following sections still
run. Suite is now **161 tests**.

### 15.7 Phase 2 progress: the live source adapter (built, tested offline)

`bot/src/mt5_live_source.py` exists and is offline-tested. It is the live
counterpart of `m1_csv_source.py` and deliberately exposes the same contract:

| | Phase 1 (CSV) | Phase 2 (live) |
|---|---|---|
| Entry point | `load_bars(path)` | `load_history(mt5, symbol)` |
| Returns | `(BarSeries, LoadReport)` | `(BarSeries, LoadReport)` |
| Streaming | n/a (whole file) | `LiveBarSource.refresh()` |
| Timestamp | `detect_convention()` then `to_ny_wall` | `epoch_to_label` then the **same** `to_ny_wall(..., "broker")` |
| Gate | `check_session_invariants` | the **same** function |

Downstream modules (`window_evaluator`, `candle_tracker`, `context_classifier`,
`sweep_detector`, `trade_calculator`, `trade_simulator`) are untouched: they
receive a `BarSeries` and cannot tell which feed produced it. An integration test
runs a synthetic live feed through `LiveBarSource` into the real
`evaluate_window` and asserts a complete PREMIUM → SHORT → partial → TP trade, so
"the Phase 1 logic is reused" is a test result, not a claim.

#### The three things the CSV never taught us

1. **A forming bar exists.** `copy_rates_from_pos` returns the current,
   unfinished minute. It is dropped, and the drop is counted
   (`RefreshReport.forming_bars_dropped`) rather than silently ignored. A bar is
   accepted only once `stamp + 60 + FORMING_BAR_SAFETY_SECONDS <= broker now`,
   and the margin is one-sided: bars may be accepted late, never early.
2. **"Now" is not the last tick.** Current broker time comes from the system
   clock through `America/New_York` + 7h. Using the tick would freeze "now" at
   the Friday close all weekend and make every bar look unfinished. This is the
   same trap that produced the false ESCALATE (§15.8).
3. **Polls repeat bars.** Every poll re-reads a trailing window, so bars are
   merged by timestamp and the repeat count is reported.

Bad data is **rejected, never repaired**: an off-tick or OHLC-invalid bar is
dropped and counted, and the minute simply stays missing, which the evaluator
already handles as incomplete data. The `check_session_invariants` gate runs on
first build and refuses a feed whose clock cannot be trusted. Two of its four
checks are boundary checks (a Friday close, a Sunday open) that a short trailing
window cannot satisfy; those two are enforced only when the sample contains such
a day, the other two always.

#### Windows needs `tzdata`

Strategy code needs an IANA timezone database and **Windows ships none**, so
`zoneinfo` raises there unless the free `tzdata` package is installed. This is
now in `bot/requirements.txt`, checked at startup by `ensure_environment()`
(actionable error, not a traceback), and reported by the probe's new "python
environment" section — which runs *before* the terminal is touched. If the probe
reports `zoneinfo : MISSING`, install tzdata before anything else.

#### Still not built, still deliberately

`dry_run_runner.py` (the loop), `dst_monitor.py`, and the CSV row emitter. Q13
and Q14 in §15.4 are unresolved and change the runner's shape, so building it
now would mean guessing.

### 15.8 Second live run — the false ESCALATE, and what it taught

The probe was re-run on **Sunday 2026-10-25**, with the venue shut since Friday
16:59 NY. It reported a ≈43-hour "broker offset" and escalated. **That was a
measurement error in the probe, not a feed defect.** A closed market's last tick
is the *closing* tick, so `tick.time − time.time()` measures how long the venue
has been shut. The four session invariants passed on the same run's bar data,
which is the independent evidence that the −7h rule was never in question.

Fixed as TRD §4.1.1 rule 5:

- the offset is only reported when the tick is younger than
  `config.STALE_TICK_SECONDS`; otherwise the probe prints
  `market closed — cannot measure live offset, last tick is Xh old` and escalates
  nothing;
- if the session calendar says the venue *should* be open, the same staleness is
  flagged as a feed-liveness problem for a human to look at — but still not as a
  timezone defect;
- **the current broker time used for forming-bar detection no longer comes from
  the tick at all.** This corrects a finding from the same run that had been
  accepted: "the newest bar is still forming, must be dropped" was an artefact of
  the frozen tick, not a property of the feed. On a closed market the last bar is
  simply the last bar. The conclusion "exclude forming bars" is still right — it
  stands on the API contract, not on that observation — but the evidence for it
  was contaminated, and the adapter now derives "now" from the system clock.

Status of the offset: **confirmed against 287,746 historical bars, NOT yet
confirmed live.** A genuine live confirmation needs a run while the market is
open (Monday during NY hours). Until then no report may describe it as
"verified live". The suite pins all of this: a Sunday fake terminal must produce
exit 0 with no offset number, and a Monday-mid-session fake must produce one.

### 15.9 Phase 2 build complete: runner, DST monitor, CSV emitter

Built this session, all offline-tested. **281 tests, all passing.**

| File | What it is |
|---|---|
| `bot/live/csv_emitter.py` | Append-only writer reusing Phase 1's exact row builders from `src.outputs` — columns cannot drift |
| `bot/live/dry_run_runner.py` | The loop: ingest → `evaluate_window` (unmodified) → append. Owns Q13 timing and Q14 crash policy |
| `bot/live/dst_monitor.py` | Separate background process; continuous invariant + offset + schedule-integrity evidence through 1 Nov |
| `bot/tests/test_dry_run_runner.py` | 40 tests |
| `bot/tests/test_dst_monitor.py` | 26 tests |

#### The one design problem that mattered

MT5 **backfills** the minutes it was not connected for. So the naive
implementation of Q14 — "if the runner missed a window, don't score it" — would
have silently violated Q14, because after a reconnect the bars for the missed
window are simply *there*, and `evaluate_window` would score them like any other.

The runner therefore keeps an **observation ledger**: the wall-clock intervals
during which it was connected and polling, persisted in `runner_state.json`. A
window counts as observed only if its entire `[open, close)` span sits inside one
interval. `evaluate_window` is never called for a window that is not observed,
and a trade whose **exit minute** was not observed is not scored either — even
though the process survived and the exit is in the history. That last case is the
one a reader would not think to check, and it has its own test.

Consequence for the log, stated plainly: a window's row records what the runner
**saw**, not what the market did. That is the Handler's policy and it is now
enforced by structure rather than by intention.

#### Q13 / Q14 as implemented

- **Q13.** NO_SETUP rows at window close. Traded rows only when `simulate()`
  resolves — possibly days later. Pending windows are re-evaluated every poll and
  the pending set is persisted. A trade that is still unresolved after
  `MAX_BARS_TO_RESOLVE_TRADE` is emitted as `MISSING_EVALUATION` /
  `data_ends_before_trade_closed` with a `resolution_deadline_passed` flag, so no
  window can hang forever. `evaluated_at` is the **write** time, so file order is
  decision order and the audit trail says so.
- **Q14.** Crash reconciliation at startup: any window pending at shutdown becomes
  `dry_run_gap_runner_down`. Its audit row keeps the candles, sweep and plan
  (facts that *were* seen) and carries **no** exit price, exit kind or R. Nothing
  is inferred.
- **Phase 3 note added** to `docs/07-PHASE-PLAN.md`: crash recovery with
  broker-side reconciliation is required once real orders exist; Option A is
  explicitly not acceptable there. Not built now, by instruction.

#### DST: windows provably cannot double-fire

At startup the runner converts every scheduled window in the planned range to
UTC and **refuses to start** if any is ambiguous (a doubled local time) or
non-existent (a skipped one). Under the Mon–Fri rule this cannot happen — both US
transitions fall on a Sunday — and a test forces `2026-11-01` to be treated as a
trading day to prove the guard actually fires rather than merely existing.
`--check` prints the result. 7 trading dates verified in the 26 Oct – 3 Nov range.

#### Evidence from a simulated full day

A synthetic weekday fed through the real runner produces **21 rows for that
trading date, 21 distinct labels, valid outcome + reason on every one, zero gap
rows** — the Phase 2 exit criterion demonstrated for one day, with the mechanism
identical for the rest. Four tests cover it.

### 15.10 Readiness for a Monday 26 Oct launch

**Yes, ready** — with a short preflight, because nothing here can be tested
against a real terminal from this sandbox.

Preflight (about five minutes, in order):

1. `pip install tzdata` — **required**; Windows has no IANA database and the
   runner will refuse to start without it with an explicit message.
2. `python live/probe_mt5_live.py` — confirms the terminal, demo account, symbol
   spec and (now that the market is open) the **live offset**. This is the
   outstanding confirmation; until it comes back, no report may call the offset
   "verified live".
3. `python live/dry_run_runner.py --check` — runs every startup guard (demo
   account, symbol spec, DST-safe schedule, invariant gate on the live feed) and
   exits without looping. If this passes, the runner is safe to start.

Then, in two separate terminals:

```
python live/dst_monitor.py            :: continuous, independent
python live/dry_run_runner.py         :: the loop
```

**Residual risk, honestly:** the live API surface `LiveBarSource` consumes
(`copy_rates_from_pos` dtype, integer epoch, forming bar) was already measured on
this terminal by the probe, so the shape is known. What has never run is the
runner's *loop* against that API. `--check` exercises startup; the first ten
minutes of the real run exercise the rest. If something is wrong it will show as
a `feed_error` or `step_error` event in `runner_events.csv` within minutes, not as
silent nonsense — the loop logs and continues rather than dying.

**What would actually delay launch:** tzdata not installed, the terminal not
logged into the demo account, or the probe reporting a non-demo `trade_mode`.
None of those is a code problem.

**Day-one partial coverage is expected.** If the runner starts mid-morning on
Monday, the windows that closed earlier that day are logged as
`dry_run_gap_runner_down` with a `not_observed_by_runner` flag. That is the honest
record, not a defect — the exit criterion ("21/21 every day") applies to full days
after launch. Starting at the Sunday 18:00 reopen would avoid it entirely;
starting Monday morning will not, and the log will say so.

### 15.11 Investigation: `outside_data_range` on a live feed (28 Oct)

Two separate defects, one root cause each. They are **not** the same bug and they
need separate fixes; conflating them would have left the second one in place.

#### Defect A — the reason string

`outside_data_range` is a **backtest** verdict. In `window_evaluator.py` it means
exactly one thing: `window.end > series.last_time` — "the input file stops before
this window". That is a fact about a file. Against a live feed it is never a
fact, because the series can only grow, so the same comparison reads "the bars
have not arrived **yet**" and writes it down as "the bars will **never** arrive".

It only reaches the row when the window is *partial*, i.e. through
`_classify_missing_data`. A complete window never gets there. So the trigger is
narrow and specific: **the last minute of Candle B had not been published at the
instant the window was evaluated**, which happens when the terminal publishes the
bar a few seconds after the minute rolls over.

Reproduced against the real runner, same feed and clock, no disconnect anywhere:

| Feed behaviour | 21 windows of 2026-10-26 |
|---|---|
| bar published the instant its minute closes | **0** `outside_data_range` |
| closing bar occasionally 45s late | **21 of 21** `outside_data_range`, `bars_b=14`, `flags=incomplete_candle` |

That is the answer to "how can this fire with a stable connection": it does not
need a connection problem at all. It needs a bar to be a few seconds late. Which
windows misfire is then decided by where the 20-second poll lands relative to
publication — i.e. by luck. That is why three windows misfired and eighteen did
not, on a day whose connection was up.

#### Defect B — the row was permanent

Worse than the wrong label: the misfire was **unrecoverable**. The row was
emitted on the first poll after close, `already_emitted` then blocked any
correction, and the window left the 120-second gap scan soon after. So a
transient condition lasting under a minute became a permanent, wrong entry in an
append-only log — and append-only is precisely what makes it permanent.

**My own Q13 tests encoded this bug.** `test_no_setup_row_is_written_at_window_close`
fed bars that stopped four hours before the window and asserted a NO_SETUP row
appeared. On a live feed that fixture is a dead feed, and the assertion was the
defect written down as a requirement. Those fixtures now end at the window's
close; the tests were asserting the wrong thing, not just failing.

#### The fix (on this branch, inert until the runner is restarted)

1. **A window whose tail is late is held, not judged.** `_evaluate_due` parks it
   in `runner_state.json` under `deferred` and re-examines it every poll. When
   the bars arrive the row is written normally, flagged `emitted_after_data_lag`.
2. **`outside_data_range` is unemittable, structurally.** `_emit_checked` is now
   the only path to disk and it raises `AssertionError` on that reason. A test
   asserts the exception; another sweeps every live CSV and asserts the string
   never appears. Not a flag, not a default: a crash if anyone reintroduces it.
3. **A real stall is named as one.** If the bars still have not arrived after
   `DATA_LAG_GRACE_SECONDS` (300s), the row is written as
   **`live_feed_stalled`** with flag `live_feed_stalled` — deliberately distinct
   from `dry_run_gap_runner_down`, because "we were not looking" and "we were
   looking and nothing came" are different facts and must not be counted
   together.
4. **Deferral respects Q14.** A deferred window whose span was not fully observed
   (the runner died mid-window) becomes a gap, not a score. A deferred window
   that *was* fully watched is still scored after a restart — deferral is about
   data, not attention, and conflating the two would discard real observations.

After the fix, the same 45-second-lag arm of the reproduction gives **21 rows, 0
`outside_data_range`** — the rows are correct, just a few seconds later.

**One number needs the Senior Dev's sign-off:** `DATA_LAG_GRACE_SECONDS = 300`.
It is a policy choice, not a measurement — how long "not yet" may last before it
becomes "never". Five minutes is ~15 poll cycles and far shorter than the shortest
genuine closure (60 minutes), so it cannot misfire on a market break. It is a
single module constant in `live/dry_run_runner.py`.

#### Tasks that could not be done from here

`window_evaluations_audit.csv`, `runner_events.csv`, `dst_monitor.csv` and the MT5
Journal all live on the machine that ran the dry run; nothing in this workspace
has them, and the Phase 1 dataset ends 2026-10-22 19:59 NY, so it cannot stand in
for 26 Oct either. **No count is claimed below and no row was guessed.**

`bot/live/audit_triage.py` does the analysis on that machine:

```
python live/audit_triage.py --outside-range --outages 2026-10-26T14:22/2026-10-27T01:48
python live/audit_triage.py --self-check
python live/audit_triage.py --show 2026-10-26 04:45-05:15
python live/audit_triage.py --illegal-reasons
```

- `--outside-range` classifies every `outside_data_range` row by shape
  (`no_bars_in_window` vs `short_tail` with `bars_b` < 15) and by whether it falls
  inside a supplied outage, printing counts of connectivity vs unexplained.
- `--self-check` re-derives **every** row's context from its own stored
  `candle_a_low/mid/high` and `candle_b_open`. This settles the 04:45-05:15
  question without the raw bars: the verdict is a pure function of two numbers
  already in the file. If those numbers put the open inside the discount zone,
  `invalid open location` is wrong **for that row**, and it will say so and exit 1.
  It also covers the rest of the file, which is the real point.

#### `known_bad_windows.txt` — and a correction

Task 1's range ("`14:45-15:15` through `00:45-01:15`") is eight windows, and the
venue has **no 15:45, 16:45 or 17:45 slot** — it is shut 17:00-18:00 NY. So the
eight consecutive windows run `14:45-15:15`, then `18:45-19:15` … `23:45-00:15`,
then `00:45-01:15` on the 27th. Written out in `bot/live/known_bad_windows.txt`
and asserted against the schedule by a test, so the list cannot drift.

The three transient-lag windows are deliberately **not** in that file: their
correct classification is not `dry_run_gap_runner_down` (the runner was watching)
and cannot be decided until the audit rows are read. Annotating them as outages
would repeat the original error in the opposite direction.

#### Clean days — nothing can be certified

**No day is certifiable, and this is not limited to 26 Oct.** The runner wrote
these rows on a day with no connection fault, so any day produced by the pre-fix
code is suspect wherever the reason appears — and 25, 26, 27 and 28 Oct were all
produced by it.

- **25 Oct** — cannot: the run started 18:13 NY, so earlier windows are gaps.
- **26 Oct** — cannot, proven: an outage plus at least the three known transient
  rows.
- **27 Oct** — cannot: the outage ran to 01:48 NY, so it has gap windows.
- **28 Oct** — cannot be certified *from here*: the CSVs are not in this
  workspace. It can be tested, not assumed — see the rule below.
- **Anything after the fix is deployed** — can, subject to the same rule.

The certification rule, now checkable rather than a matter of judgement. A day is
clean only if all four hold:

1. 21 rows for the trading date, one per scheduled window, no duplicates;
2. every row has a valid outcome and a non-empty reason;
3. **zero** `outside_data_range` rows and zero `live_feed_stalled` rows;
4. zero `dry_run_gap_runner_down` rows, i.e. the runner observed all 21.

Point 3 is why the defect had to be fixed rather than annotated: with the reason
gone from the vocabulary, a day that satisfies 1-4 cannot have been affected by
either defect. Absence of the string is meaningful *because* the misfire always
manifested as exactly that string.

**The clean-day clock therefore restarts at the restart**, not at launch. Days
already logged cannot count toward the five consecutive clean days, and the
Handler's restart to pick up this fix is the event that starts the count.

### 15.12 The 29 `outside_data_range` rows, resolved (28 Oct, second pass)

The first pass classified 8 as connectivity and 21 as "unexplained transient
lag". **That was wrong, and the error was mine**: I classified against the single
`--outages` range I was given, so any outage *outside* that range fell into the
unexplained bucket. The rows were never unexplained — they were a second and
third outage nobody had cross-referenced yet.

Re-classified against `dst_monitor.csv`'s own `window_last_ny` (the newest bar
the terminal would give up) instead of against a supplied range:

| | windows | verdict | evidence |
|---|---|---|---|
| Stall 1 | 8 | feed stall | bar frozen 26 Oct 14:21, recovered after 27 Oct 01:47 |
| Stall 2 | 3 | feed stall | bar frozen 27 Oct 06:37, recovered 09:35 |
| Stall 3 | 11 | feed stall | bar frozen 27 Oct 13:13, recovered between 03:16 and 03:36 on 28 Oct |
| Late tail | 7 | transient lag | feed healthy; newest bar 0–2 min behind the close |
| **Total** | **29** | | 22 stall, 7 lag, **0 unexplained** |

Both stalls 2 and 3 are new. Neither is a bug in the bot.

#### How each row was attributed

The discriminator is **how far behind the newest bar was at the window's close**,
not the row's shape:

* **≤ 2 minutes behind** → the feed was live and only the final bar was late. The
  window's own data was there to be read a poll later. That is the late-tail
  defect, and the 7 rows show it exactly (six at 0 min, one at 2 min).
* **≥ 38 minutes behind** → the terminal had no bars for the window at all. That
  is a feed stall, and `0/0` is simply what a stall looks like from inside the
  evaluator when the freeze is longer than a window.

`dst_monitor` was down for part of stall 3 (20:20 → 01:44 and 01:44 → 03:36), but
it bracketed the freeze anyway: the newest bar was still 27 Oct 13:13 at both
01:44 *and* its last sample before the gap. A sample taken later still tells you
what the terminal did not have. The runner's own zero-bar reports (at ~02:16 and
~03:16) narrow recovery to between 03:16 and 03:36.

#### Answering the "0/0 is a different mechanism" question

**It is the same mechanism, and the shape difference is only how deep the lag is.**
Both shapes come from the single line `if end > series.last_time:
return REASON_OUTSIDE_RANGE`:

* last bar 13:14 for a 12:45–13:15 window → Candle B has 14 of 15 bars → `15/14`,
  `incomplete_candle`
* last bar 13:13 for a 14:45–15:15 window → zero bars in the window → `0/0`,
  `no_bars_in_window`

One condition, two pictures. Which is why the fix does not branch on shape: it
asks "is the newest bar older than this window", and defers if so. A 20-second
lateness and a 14-hour stall are the same question with different answers, and
the 300-second grace is what separates them.

#### Sanity check from the monitor's own liveness channel

`dst_monitor` independently flagged all three, in real time, via `tick_age_seconds`
and the note `venue should be OPEN — feed liveness problem` — 1180 samples, in
spans matching each stall (26 Oct 14:24 → 27 Oct 01:47, 27 Oct 06:39 → 09:35,
27 Oct 13:16 → 20:20). Its `invariant_all_passed` column stayed **True**
throughout, which is correct: those four invariants test NY/DST correctness, not
whether data is flowing. **The monitor's liveness evidence is in `notes` and
`tick_age_seconds`, not in the FAIL column** — the only 82 FAIL rows in the file
are one benign weekend-boundary note, appearing only after recovery (03:36–04:57
on 28 Oct).

Worth stating plainly: **the stalls raised no error.** `refresh()` never threw —
the terminal kept returning the same trailing 3000 bars — so `runner_events.csv`
contains no `feed_error` row between 25 Oct 18:32 and the kill. That is exactly
why the fix detects stalls by "no bars for this window after 300 s" rather than
by catching an exception: there was no exception to catch.

#### 04:45-05:15 — closed, agreed

`candle_a_low = 29006.0`, `candle_b_open = 29004.4`, so `open - Low_A = -1.6`.
The open is *below* Low_A, i.e. on the VOID side by 16 ticks — not "0.4 inside
the discount zone". **`invalid open location` was correct.** The Handler's manual
reconstruction was off; the stored numbers settle it and no further action is
needed. (The other VOID row, 05:45-06:15, has `open == Mid_A` exactly, also a
correct VOID.)

### 15.13 `--illegal-reasons` was wrong, and is fixed

It flagged **43 of 46** rows. The allow-list held the NO_SETUP and gap vocabulary
but **omitted every WIN/LOSS exit reason**, so `stop loss hit`, `take profit hit`,
`breakeven stop hit after partial` and `target hit after partial` were reported as
illegal. Those are the most legitimate strings in the file. A checker that cries
wolf on the normal case trains you to ignore it, so this was worse than no check.

Fixed by **deriving** the list from the modules that own the strings
(`trade_simulator`, `sweep_detector`, `window_evaluator`, `context_classifier`,
`csv_emitter`) instead of hand-listing it. Adding an exit reason now extends the
allow-list automatically; a typo is still caught; and `outside_data_range` is
deliberately absent, which is the assertion.

Re-run on the real log: **29 flagged, all of them `outside_data_range`, zero false
positives.**

Two further holes found while fixing it:

* the check read `window_evaluations.csv`, which was **not in the dump**, so it
  examined zero rows and printed a clean "0" — a silent pass. It now falls back to
  the audit CSV and prints how many rows it examined.
* `--check` called `start()`, which reconciles and *writes* gap rows. A preflight
  that consumes the rows it is meant to warn you about is worse than useless. It
  now runs the guards read-only and writes nothing at all.

### 15.14 The forced kill: what it did, and one real bug it exposed

The Handler closed the terminal windows; `runner_events.csv` confirms it — the
last event is a `start` at 25 Oct 18:32 NY and there is no matching `stop`. The
run ended around 28 Oct 04:57 NY (last monitor sample 04:57:42, last audit row
03:45-04:15).

**(a) Corruption: no. Something worse, and it was armed for the next restart.**
`RunnerState.save()` writes to `.tmp` and `os.replace`s it, so a kill cannot leave
a half-written JSON. But `_open_observation` appends `(start, None)`, and
`_intervals` reads a trailing `None` as *"still open, extends to now"*. Nothing
closes it if the process is killed — so the next launch would have stretched that
one interval across the **entire downtime**, counted the outage as watched, and
scored every window inside it from back-filled history. Q14's prohibition, done
silently, with rows that look completely normal.

The old code never had to face this because the process survived the whole run.
**Tonight's restart would have been the first time.** Fixed: `RunnerState.load()`
closes a dangling interval at `last_heartbeat_utc` (the last moment we know we
were alive), or drops it if there is no heartbeat. Tested directly, including the
case where the interval is closed and a window that fell inside the downtime is
then correctly *not* observed.

A second, smaller risk: two runners on one log would each emit the same window,
because `already_emitted` is per-process — permanent duplicates. Now refused when
the state file's heartbeat is under 90 s old, overridable with `--force`.

A window cannot get permanently stuck in `deferred`: the retry always terminates
once `DATA_LAG_GRACE_SECONDS` passes.

**A second hole, found in the same data and fixed the same way.** The monitor has
two multi-hour sampling gaps (27 Oct 20:20 → 28 Oct 01:44, and 01:44 → 03:36) with
**no `start` or `stop` event anywhere near them**. So the runner was not restarted
— it was alive and not polling, most likely blocked inside an MT5 call on a dead
socket, which is consistent with both processes freezing at the same moment.

An interval left open *in memory* has the identical effect to one left open in the
file: `_intervals` extends it to now, so the hole counted as watched. Seven windows
closing inside those gaps were therefore evaluated rather than logged as gaps.
`step()` now detects a hole longer than `POLL_GAP_SECONDS` (300 s), closes the
interval at the previous heartbeat, and opens a new one — so the hole becomes
unobserved and its windows become gaps. A `polling_gap` event records it.

This also means the "22 stalls" breakdown is right about the *feed* and
incomplete about the *runner*: for the seven windows inside those gaps the runner
was neither watching nor awake, so they are **both** a feed stall and an
observation gap. The reconciliation keeps `live_feed_stalled` for them because
that is what the row's own shape shows, but the honest one-line summary is
"stalled feed, unwatched runner". Neither label is scored as market behaviour,
which is the property that matters.

**(b) Nothing needs clearing. Keep `runner_state.json` and every CSV.**

The state file self-heals: unreadable → treated as a first start (cost: some
windows logged as gaps, never fabricated); dangling interval → closed at the
heartbeat; `pending` → gap rows at startup. **Do not delete it** — it is how the
runner knows which windows it owes the log. Deleting it would not corrupt
anything, but windows whose trades were open would lose their gap rows entirely
and could be scored later from back-fill.

Check before restarting:

```
python live/audit_triage.py --state live/output/runner_state.json
```

That prints the heartbeat, whether the last interval is still open, the pending
trades and the deferred windows, and states what the next start will do with each.
Expect `clean_stop: False`, one open interval, and the trade that was open at
04:57 to be logged as a gap.

Also confirm in Task Manager that no `python.exe` running `dry_run_runner.py`
survives, or simply wait 90 seconds after the kill.

**(c) Restart, from `bot/` — monitor first, so its evidence covers the start:**

```
python -m pip install tzdata
python live/probe_mt5_live.py
python live/audit_triage.py --state live/output/runner_state.json
python live/dry_run_runner.py --check
```

then, in two separate terminals:

```
python live/dst_monitor.py          :: leave running, independent of the runner
python live/dry_run_runner.py       :: the loop
```

`--check` writes nothing and is safe to run repeatedly. Do not start the runner
until the state inspection and `--check` both look sane.

### 15.15 Live findings, 28 Oct: two vacuous failures and one real fix

#### 1. `[FAIL]` on every sample was a zero-denominator artefact

`verify()` computed `bool(fri_last) and late == 0 and at_close / len(fri_last) >= 0.8`.
When the trailing window contained no Friday, `bool(fri_last)` was False and the
check reported **FAIL**. Same for Sunday. A "failure" that means *there was
nothing to check*.

The live window is ~50 hours, so on a Wednesday morning it contains neither
boundary: 82 samples in the 28 Oct archive failed this way while the feed was
healthy.

There was a **second, less obvious shape** in the same archive: 23 samples read
`Sundays 0/1`. One Sunday *was* visible — but clipped by the window's left edge,
so its first bar in the window was 23:37 instead of the 18:00 open. The check
compared a mid-session bar against the open minute and called it wrong. A window
is a slice, not a history; it can miss a boundary at either edge.

Both are the same error as the Session 12 stale-weekend-tick escalate: absence of
evidence read as evidence of failure.

**Fix.** A boundary is only judged if the window actually contains it
(`window_first <= boundary <= window_last`). Otherwise the check reports
`not applicable` and passes, and the monitor writes that fact into `notes` — so a
check that did not run is distinguishable from one that ran and passed. The
**positive** violations are untouched and still fail unconditionally: a bar later
than Friday 16:59, or earlier than Sunday 18:00, is wrong however the window is
clipped. Verified by replaying all 3088 archived samples through the corrected
verifier: **82 → 0 failures, and 0 of them contained a genuine boundary breach**.
Phase 1's verdict on the real 287,746-bar dataset is **unchanged** (39/42 Fridays,
42/42 Sundays — the three being half-days).

**Did it contaminate anything certification-facing? No.** `invariant_all_passed`
and `failures` are read in exactly two places: the monitor's own console `[PASS]/
[FAIL]` flag and its `--once` exit code. Nothing else reads them — not the runner,
not `audit_triage.py`, not the certification rule. The stall attribution in §15.12
used `window_last_ny`, `tick_age_seconds` and `notes`, all unaffected.

**Does the history need reconciliation? No.** No decision was taken on those rows,
so nothing downstream is wrong. And the CSV is self-explanatory without editing:
the failing detail string carries the denominator (`0/0 Fridays...`), and the row
carries `window_first_ny` / `window_last_ny`, which is precisely what proves the
boundary sat outside the window. A reader can re-derive vacuity from the row
itself. Rewriting it would break append-only for no gain, so: **no rewrite, no
annotation file.**

#### 2. The 231-minute `polling_gap` at startup: right rows, wrong event

Confirmed — the substance of the Handler's reading is correct. The four
`dry_run_gap_runner_down` rows (04:45, 05:45, 06:45, **07:45**-08:15 on 28 Oct) are
the dead time between the 04:57 kill and the 08:47 restart, reconciled as
unwatched rather than back-filled. That is the Q14 restart path working on real
data for the first time.

But the `polling_gap` *event* attached to it was wrong, and it was mine:

* `start()` never adopted the new start as the heartbeat, so the first `step()`
  measured `now - last_heartbeat_utc` = 231 minutes and reported a freeze that the
  **state file had already reconciled**. The event text ("machine asleep or an MT5
  call blocked") points the operator at the wrong cause.
* Worse, closing the interval at that stale heartbeat wrote an **inverted
  interval** into the ledger (start 12:48 UTC → end 08:57 UTC). Harmless — a
  window can never match an interval whose end precedes its start — but it is
  corrupt data.

**Fix.** `start()` sets `last_heartbeat_utc = now` *after* the gap scan and the
reconciliation have used the old value; `_close_observation` clamps so an interval
can never invert. The restart now logs `start` → `unclean_shutdown_detected` → the
four gap rows, and nothing else. The mid-run freeze detector still works for a
genuinely suspended process (tested).

#### 3. "10 trading dates" is not a fixed list — no Nov 3 cutoff exists

`_days_in_planned_range` is computed on every start as
`ny_today - 3 … ny_today + 10`, so it always yields 10 trading dates and rolls
forward: on 28 Oct it covers 26 Oct - 6 Nov; on 10 Nov it covers 5 Nov - 20 Nov;
on 20 Dec, 17 Dec - 30 Dec. **There is no end date to outlive, and the runner will
not refuse to validate past 3 Nov.**

What *was* wrong was the comment claiming the range "covers the planned 26 Oct-3
Nov window" — the kind of comment that invites someone to hard-code it later.
Replaced with named constants (`SCHEDULE_LOOKAHEAD_DAYS`, `SCHEDULE_LOOKBACK_DAYS`)
and a docstring stating there is no fixed end, plus tests that assert the range
still covers dates months later and that every such start passes the DST-safe
check. Verified on a simulated 2026-11-10 start: `dst_safe_dates: 10`.

#### 4. Ctrl+C is now a clean stop

`SIGINT`, `SIGTERM`, `SIGHUP` and `SIGBREAK` are handled by setting a flag only —
no I/O inside the handler, because the loop may be mid-append or mid-`os.replace`
and a clean stop exists precisely to keep those consistent. The loop notices within
one second (it sleeps in slices) and calls `stop()`, which closes the interval,
writes `clean_stop: true`, appends a `stop` event and prints:

```
CLEAN STOP — state flushed, clean_stop=True after N steps and M evaluations.
The next start will NOT log this session's downtime as a gap.
```

**What the Handler watches for:**

* **Clean stop** — that `CLEAN STOP —` line appears, and `runner_events.csv` gains
  a `stop` row. Closing the window afterwards is safe and the next start logs
  nothing about a gap.
* **Force-kill is genuinely necessary only when:** (a) the process is wedged inside
  an MT5 call and does not respond to Ctrl+C within ~30 s (the call is blocking, so
  the flag cannot be seen until it returns); or (b) the machine is being powered
  off. Both are recoverable — see below — so a kill is a last resort, not a hazard.
* **After a kill**, the next start prints `unclean_shutdown_detected` and logs the
  affected windows as gaps. That is correct behaviour, not damage. Check with
  `audit_triage.py --state` before restarting.

#### 5. `--certify DATE` replaces relay-and-interpret

```
python live/audit_triage.py --certify 2026-10-28
```

Applies the four-part rule and prints PASS/FAIL with every failing condition
named, not just the first:

```
2026-10-28  FAIL
  rows            : 21/21 scheduled windows
  outcomes        : 2 WIN, 0 LOSS, 18 NO_SETUP
  [FAIL] coverage missing windows: 09:45-10:15
  [FAIL] outcome  13:45-14:15: outcome 'MISSING_EVALUATION' is not a decided outcome
  [FAIL] range    1 row(s) with outside_data_range: 04:45-05:15
  [FAIL] gaps     1 row(s) the runner did not observe: 18:45-19:15
  -> this day does NOT count; the consecutive run restarts here
```

Exit codes: `0` PASS, `1` FAIL, `2` not a trading day (e.g. Sunday) or bad usage.
`MISSING_EVALUATION` counts as *not* a decided outcome — the schema value means
"could not be evaluated", so a day containing one is not clean. It reads
`window_evaluations.csv` and falls back to the audit CSV, saying so when it does.

### 15.16 The clean-day clock starts at the 28 Oct 08:47 NY restart

**Decision: Phase 2 has no fixed end date.** The run continues until five
consecutive clean days are certified; the 26 Oct - 3 Nov window is dropped as a
target.

**The clock starts at the restart on 2026-10-28 08:47 NY (12:47 UTC).** Nothing
logged before that can count: 26 Oct carries both defects, 27 Oct carries stalls 2
and 3, and the 28 Oct session ended in an unclean kill. Those days are documented,
not secret — §15.11-15.15 explain every row — but they are not candidates.

A day counts only if `--certify` says PASS. Run it at the end of each session; the
five-day count is the number of consecutive PASSes, and any FAIL restarts the run
at that date.

### 15.17 `--certify` could not tell "not yet" from "missing" (fixed)

`--certify 2026-10-28` reported `coverage missing windows: 09:45-10:15, ...`. That
was **not a defect**. Verified against the schedule: the runner restarted at
08:47 NY, nine of that day's windows had closed by 09:30, and `09:45-10:15` is
exactly the **first window that had not closed yet** (it closes at 10:15). The day
was in progress.

The tool was wrong to call it a failure. It compared rows against all 21 scheduled
windows with no notion of the current time, so *every* mid-run certification would
report phantom missing windows — which made it unfit to run unsupervised, the one
thing it exists for.

**Now it distinguishes four verdicts, with four exit codes:**

| verdict | meaning | exit |
|---|---|---|
| `PASS` | all four conditions hold; the day counts | 0 |
| `FAIL` | a condition broke; the day does not count | 1 |
| `NOT_APPLICABLE` | a weekend, or bad usage | 2 |
| `INCOMPLETE` | nothing is wrong; the day is not finished | 3 |

`INCOMPLETE` is the new one, and it is not a soft failure — it is a statement that
no verdict is yet possible. A window with no row is now classified by *why*:

1. **not due** — its close is still in the future, plus a 90-second grace (a row
   lands on the first poll after the close, not at the close);
2. **awaiting trade** — the window closed and its trade has not resolved. Q13 says
   the row is written when `simulate()` decides, which can be days later. The
   evidence is `runner_state.json`'s `pending`, read automatically from the
   `--out-dir`;
3. **awaiting data** — held for late bars, still inside the grace (`deferred`);
4. **missing** — none of the above. The only one that fails.

Case 2 matters as much as case 1: without the state file, *every open trade* would
be reported as a missing window. Removing the state file is tested to fall back to
`FAIL`, because an unexplained absence should not be excused by optimism.

**What it prints now** (the reported case, replayed at 09:30 NY):

```
2026-10-28  FAIL
  rows            : 9/21 scheduled windows
  [FAIL] gaps     4 row(s) the runner did not observe: 04:45-05:15, ... 07:45-08:15
  -> this day does NOT count; the consecutive run restarts here
```

The phantom coverage failure is gone. What remains is real: those four windows
fall inside the 04:57-08:47 downtime and are gap rows by construction.

And with those four windows absent from the log instead of logged as gaps:

```
2026-10-28  INCOMPLETE - too early to certify, this is not a failure
  rows            : 9/21 scheduled windows
  [ok]   coverage all scheduled windows logged exactly once (nothing found against it yet)
  wait:  12 window(s) have not closed yet (next: 09:45-10:15, closes 10:15 NY)
  -> NOT a defect. Re-run when the day is complete.
  -> the day is final after 2026-10-29 00:15 NY
```

That last line is the answer to a question people get wrong: a trading date's
final window closes at **00:15 the following NY day**, so "is 28 Oct done?" is not
answered by the calendar date.

`--now ISO` pins the clock (naive = New York wall clock), so a past moment can be
replayed exactly — which is how the reported case was reproduced and how the tests
stay deterministic. Leaving the real clock in a test is what let the first version
pass by luck: the sandbox happened to sit at 11:21 NY.

Two smaller fixes in the same pass:

* the tool's default `--out-dir` was `./output`, while the runner writes to
  `bot/live/output`. Running `python live/audit_triage.py --certify DATE` with no
  arguments — the unsupervised case — read the wrong directory. It now defaults to
  the runner's own directory.
* a missing log *file* is a usage error (exit 2), but a log file with no rows yet
  for the date is a legitimate `INCOMPLETE`. Those were the same branch.

#### Consequence for the clean-day clock: 28 Oct cannot itself PASS

The §15.16 decision — clock starts at the 28 Oct 08:47 restart — is right about
excluding earlier days, but **28 Oct is not a candidate day in its own right**:
nine of its windows opened before the restart, so it necessarily contains gap rows
and `--certify` can never pass it. The first day that can be certified is
**29 Oct**.

That is a scheduling fact, not a change to the decision: the restart is still the
moment the clock starts, and the first eligible day is the first full day after it.
Five consecutive clean days therefore means 29-30 Oct and 2-4 Nov at the earliest,
subject to `--certify` saying PASS for each.

### 15.18 The 29 Oct "six missing windows" report: a stale checkout, not a defect

Report at **17:25:41 NY on 2026-10-29**: `FAIL`, coverage missing `23:45-00:15,
18:45-19:15, 19:45-20:15, 20:45-21:15, 21:45-22:15, 22:45-23:15`. None of those
windows had opened.

**Those six are exactly the scheduled windows still in the future at 17:25**, and
the ordering is the tell: the list starts at `23:45-00:15`, which is
`windows_for_day` order, not chronological order. The v2 logic sorts pending
windows by close time and sorts `23:45` last.

Reproduced both ways on the same fixture, same clock:

| code | output |
|---|---|
| `ab7d8f5` (current) | `INCOMPLETE - too early to certify, this is not a failure`, `wait: 6 window(s) have not closed yet (next: 18:45-19:15…)`, exit **3** |
| `02a2837` (previous) | `FAIL`, `[FAIL] coverage missing windows: 23:45-00:15, 18:45-19:15, …`, exit **1** |

The second is **character-for-character** the reported output. The live run
executed code from before the not-due fix — the commit the runner was restarted
onto. **The v2 logic is not partial: it handles any number of future windows**,
consecutive or not, because the test is per window (`close + 90 s > now`) rather
than a single boundary. Sixteen future windows would classify identically.

No production logic was changed to "fix" this, because there was nothing wrong
with it. What was genuinely missing is **provenance**: a wrong verdict and an old
binary looked identical, and the person reading the output had no way to tell.
That is now closed:

* every verdict ends with **`logic: certify v2 (not-due aware)`**. A report
  without that line, or with `v1`, is not evidence about today.
* `--digest` lines carry the same marker.

**Handler check:** `--certify` today should print `INCOMPLETE / exit 3` while the
day is open, and the `logic:` line. If it prints `FAIL` plus a `missing windows`
list **and no `logic:` line**, the checkout is stale — pull the branch, do not
investigate the log.

#### Added this session

1. **Regression tests for the exact shape** (they pass on v2, and would fail on
   v1): 15 rows at 17:25 NY with six future windows ⇒ `INCOMPLETE`, no coverage
   failure, no future label in any `[FAIL]` line, every future window classified
   `not_due`, none `missing`. End-to-end through the CLI at exit 3. Plus the
   inverse: a real stall on a closed window still `FAIL`s, and the future windows
   are still not named in that failure.
2. **`--digest N`** — one line per trading day for the last N *trading* days
   (weekends take no line), newest last, plus the consecutive-PASS streak and
   what is left to reach five. This is the one-paste view of where the run
   stands.
3. **`--certify` on FAIL now prints the last runner activity** — the later of the
   state-file heartbeat and the newest `runner_events.csv` row, in NY time. A
   FAIL prompts "was it even running?"; the answer is already on disk.
4. `--certify`/`--digest` no longer refuse a directory that has only
   `window_evaluations.csv`; they fall back to it exactly as `certify()` does.

#### The startup banner, clarified (the second open item)

Confirmed: **the second line is a static capability banner, not an event.**
`run()` prints it once, immediately after installing signal handlers, and lists
what it managed to install on this platform. It says nothing about any shutdown.
The event record is the separate `CLEAN STOP — …` line at the end of a run and
the `stop` row in `runner_events.csv`.

It previously read `clean stop: SIGBREAK, SIGINT, SIGTERM → graceful shutdown
(state flushed, clean_stop=True)` — past tense, under a line saying the last
shutdown was *unclean*. It now reads:

```
signal handlers armed for this run: SIGBREAK, SIGINT, SIGTERM - Ctrl+C / kill
will stop cleanly (capability notice, not a shutdown record)
```

So the pair now reads as what it is: *the last stop was unclean* (an event), then
*handlers are armed for this run* (a property). Two tests pin it: the banner is
emitted once, contains "capability notice", and contains neither `clean_stop=True`
nor `clean stop:`; and `CLEAN STOP` appears exactly once, at the stop.

### 15.19 Getting a whole day's logs off the machine (mirror + nightly snapshot)

The problem is not technical: the Handler is away for whole days, terminal
scrollback truncates, and `live/output/` must not be read while a run is in
progress. Two new pieces fix it without touching anything certification-critical.

#### `live/log_mirror.py` — a third process, read-only, additive

    live/output/dst_monitor.csv              ->  logs/DST_<NY date>.csv
    live/output/runner_events.csv            ->  logs/RUNNER_<NY date>.csv
    live/output/window_evaluations_audit.csv ->  logs/TRADES_<NY date>.csv

It **never writes to `live/output/`**, takes no lock, and no verdict depends on
it: deleting the process or its state file cannot alter a single row of the run.
The mirror can only be wrong about its own copies, which is the whole safety
argument for letting it run beside the runner.

**It is a separate process, not a thread**, deliberately. A thread would put this
file I/O — and any bug in it — inside the process that produces the evidence.

* **Write-through.** Every row is appended, flushed and `fsync`ed as it arrives,
  and the offset is saved immediately after. A row sitting in an OS buffer at
  03:00 is a row lost to a power cut, which defeats the purpose.
* **Resume.** `logs/.mirror_state.json` holds the byte offset and header per
  source; a restart continues from there, no dupes, no gaps. Truncation resets to
  zero rather than guessing.
* **Byte-faithful.** Records are located by the CSV parser and copied as raw
  bytes, so a quoted newline (a traceback in an event detail) survives and a
  non-UTF-8 byte cannot shift every later offset.
* **Mid-write safe.** A half-written row — including one whose quoted field is
  still open — is held back until it is complete. This needed
  `csv.reader(..., strict=True)`: the default silently returns a *truncated*
  record, which would have been mirrored and then never re-read.

One residual window, the same one the runner has: a crash between fsyncing the
target and saving the state replays that single record. The state is written
atomically (`tmp` + `replace`), and a normal stop or a `kill` loses nothing.

**Which date a row belongs to.** `DST` and `RUNNER` use the row's own NY clock.
`TRADES` is grouped by **`trading_date`**, not its timestamp: the 23:45 window
ends at 00:15 the next morning, so routing by clock would split one trading day
across two files. Grouped by trading date, `TRADES_<date>.csv` is exactly the
rows `--certify <date>` read.

#### `live/snapshot_and_push.py` — nightly commit and push

Runs at **00:20 NY**, picks the **previous completed NY day** (always
`today - 1`: at 00:20 that is yesterday, and at 23:00 it is *still* yesterday,
because today is not over), stages `logs/`, commits `daily log snapshot <date>`,
and pushes to the feature branch.

* **Stages the day CSVs by name, every date** — not the directory. The job's own
  status log sits beside them, and `git add logs/` would commit the record of the
  commit into the commit. Every date is included because a trade resolving days
  later (Q13) appends to an older day's file; staging only today's would leave
  that row uncommitted for ever.
* **Pushes never go to `main`/`master`**: a scheduled script is exactly the thing
  that would eventually do it by accident. It refuses and says so.
* **A failed push costs one night, not a repair.** Nothing raises; the failure is
  written to `logs/SNAPSHOT_PUSH.log`, printed, and exits non-zero so Task
  Scheduler shows a failed run. The commit stays local and the next night pushes
  both. If the day's commit already exists, it is not repeated — only the push is
  retried.

Windows wrapper: **`snapshot_and_push.bat`** in the repository root. Register it
once in Task Scheduler: daily at 00:20, time zone Eastern Time (New York), with
"run as soon as possible after a missed start" so a sleeping machine still pushes
the same night. The script picks the day for itself, so a late trigger is safe.

#### Running it

Three processes now, all independent:

    1  python live/dry_run_runner.py      (the run)
    2  python live/dst_monitor.py         (DST invariants)
    3  python live/log_mirror.py          (the copy that leaves the machine)

    python live/log_mirror.py --status    # what has been mirrored so far

`--once` exists for a one-pass run; `--interval` controls the follow rate
(default 15 s). If the mirror is not running, nothing breaks — it simply resumes
from its last offset when it starts, so a forgotten process costs nothing but
delay.

### 15.20 The mirror's first live run: fsync on a read-only handle (fixed)

The first run of `log_mirror.py` on the Windows machine died immediately on all
three sources:

    OSError: [Errno 9] Bad file descriptor
      os.fsync(fh.fileno())   <- save_state()
      <- _mirror_source() <- mirror_once()

**Cause.** `save_state` wrote the temporary file with `write_text()`, then
reopened it **read-only** and fsynced that handle:

    tmp.write_text(payload)
    with tmp.open("rb") as fh:       # read-only handle
        os.fsync(fh.fileno())        # fine on POSIX, EBADF on Windows

`os.fsync` on POSIX works on any descriptor. On Windows it is `_commit()`, which
requires a handle opened for writing and returns `EBADF` otherwise. Same class of
failure as the earlier tzdata miss: a POSIX-permissive assumption that no test on
Linux could see. All 439 tests passed because fsync-on-read-only succeeds here.

**Fix.** One `_fsync(fh)` helper does every durable write, with the fsync on the
open **write** handle before it closes, and `replace()` after the close (Windows
cannot replace a file that still has a handle open). The helper also refuses a
read-only handle outright, so the same mistake now fails identically on every
platform instead of only in the field.

**Consequence that had to be fixed with it.** The crash meant every run wrote its
rows and then died *before* saving an offset. The day files were left non-empty
with no state, so the "fixed" version would have restarted at offset 0 and
duplicated every row it had already copied. `_resume_from_destination` now
rebuilds the offset from the day files: the longest prefix of source records
already present (matched on raw bytes, counted) is skipped, and the walk stops at
the first record that is not there. A missing state file is therefore recoverable
rather than destructive.

**`--status` was misleading during the incident** and is fixed too. It printed
`offset 0 bytes` for every source, which reads as "nothing was mirrored" while
six day files held rows. It now warns explicitly when day files hold rows but no
source has a saved offset — the state an interrupted run leaves.

#### The `logs/*.csv` files in the report

They are **not in this repository**. `git ls-tree -r f21330b` shows eight files
and none is under `logs/`; `git log --all -- 'logs/*'` returns nothing; no commit
on any branch has ever contained `logs/`. Nothing was committed and nothing needs
removing from the repo.

They are **not seed or backfill data** either. They are artifacts of the crashed
runs, and they match the crash exactly. Replaying the shipped `f21330b` code
against a realistic source with Windows fsync semantics, three attempts leave:

    DST_2026-10-25.csv      3 rows   (the same row, three times)
    RUNNER_2026-10-25.csv   3 rows
    TRADES_2026-10-26.csv   3 rows
    state file:             NEVER WRITTEN

Each attempt writes its first row per source, dies in `save_state`, and re-reads
from zero next time. That explains both the files and the `--status` reading of
"0 bytes": the offsets were never persisted because persisting them was the thing
that crashed. The exact mix of dates in the Handler's six files reflects which
day each attempt reached first.

**What to do with them.** Either is safe:

* keep them — the fixed mirror reconciles against them and appends only what is
  missing (verified end to end: nine files, nine rows, no duplicates);
* delete `logs/*.csv` for a clean slate — the sources in `live/output/` are
  append-only and complete, so the mirror rebuilds the day files from offset 0.

The second is tidier, because the files left by the crash contain duplicated
rows and the rebuild does not.

### 15.21 A locked state file on Windows, and the duplicate it exposed

Two things surfaced on the second live run. The reported one was a rename
failure; the dangerous one was what it silently caused.

#### The rename failure

    PermissionError: [WinError 5] Access is denied:
      '.mirror_state.tmp' -> '.mirror_state.json'
      tmp.replace(path)  <- save_state()

**Not our own stale handle.** Checked directly: at the moment `replace()` runs,
no descriptor in the process refers to either file — the `with` block that writes
the temp file has closed, and `load_state` closed the destination long before.
Verified by listing `/proc/self/fd` from inside `Path.replace`.

**Cause: external, transient.** A Windows rename-over-existing-file
(`MoveFileEx` with `MOVEFILE_REPLACE_EXISTING`) fails with `ACCESS_DENIED` or
`SHARING_VIOLATION` while another process holds the destination open. On a
OneDrive-synced folder that is routine: the client opens a file within
milliseconds of noticing it changed, and the rename lands inside that window.
Antivirus and the search indexer do the same.

The Handler's project sits under `Desktop\projects\...`, and on a Windows
machine with OneDrive, `Desktop` is very often redirected into `OneDrive\` — which
is exactly `sync_client_hint()`'s check. The tool cannot prove it from this
side, so it now **names the client when it can**: if a save is deferred and the
folder looks synced (`$OneDrive`, `$OneDriveCommercial`, `$OneDriveConsumer`, or
a path containing OneDrive/Dropbox/Google Drive/iCloud) the failure message says
so and suggests `--state <path outside the synced folder>`. **That flag already
exists and is the recommended fix if this recurs**: the state file is
machine-local and gitignored; it does not need to live in a synced, committed
folder.

**Fix.** `save_state` retries only the errors that mean "busy right now" —
WinError 5, 32, 33, 117 on Windows, `EACCES`/`EPERM`/`EBUSY`/`EDEADLK`
elsewhere — five attempts over ~1.5 s. Every other error is raised on the first
attempt, because retrying a permission that will never be granted or a full disk
only hides it. When the retries are exhausted the save is abandoned, not crashed:
`Mirror._save` records the reason, printing it **once per pass** rather than once
per row, and mirroring continues with correct offsets in memory.

#### The duplicate it exposed (the serious one)

`_resume_from_destination` only ran when there was **no** state file. A state file
that existed but was *stale* — last saved before the lock arrived — was trusted
blindly. Sequence, reproduced:

    cycle 0  locked   no state, writes row 10, save fails
    cycle 1  ok       reconcile from day files, writes row 11, saved
    cycle 2  locked   loads state (ends at row 11), writes row 12, save FAILS
    cycle 3  ok       loads the stale state, re-reads from row 12 -> DUPLICATE

Found by the multi-cycle test that was written to check precisely this. It
duplicated two rows across six cycles.

**Fix.** `_bootstrap_offsets()` runs once per process start and takes the **later**
of the saved offset and the one the day files imply. A state that is behind is
corrected; a state that is current is left alone; a state that is *ahead* (day
files rotated away) is left alone too, which is the non-duplicating direction.
The saved offset is what was last persisted; the day files are what was last
written. When a save fails they disagree, and the state is the one that is wrong.

#### Output

`-1` was a failure sentinel in a count column and printed as
`mirrored: DST -1, RUNNER 39, TRADES 78` — indistinguishable from corrupt data.
Counts are now counts, and failures have their own line:

    mirrored: RUNNER 39, TRADES 78
    failed: DST (file locked by another process (WinError 5))
    state NOT saved: file locked by another process (WinError 5)
      offsets are still correct in memory and mirroring continues; the next
      start rebuilds them from the day files

A source that fails reports 0 rows *and* a reason; nothing is ever negative.

#### Verified end to end

Cycles 1-4 with every rename blocked, then lock cleared, then lock back, then
cleared again. Result: **six DST rows, six unique, in order, no gaps, no
duplicates**; state durable once a rename succeeded; silent on a pass with
nothing to do. The regression test fails on the pre-fix code (verified by
disabling the bootstrap).

### 15.22 The lock-contention class, audited across every writer (Phase-2 integrity)

The Handler confirmed it: `C:\Users\admin\OneDrive` is the whole project tree,
so `bot/live/output/` — `runner_state.json` and the live CSVs — sits inside a
synced folder. The mirror's bug was therefore not a logging nice-to-have. Every
durable write in the project was audited against the same failure.

#### Verdict per component

| component | file | rename-replace? | fsync? | verdict |
|---|---|---|---|---|
| `log_mirror.py` | `logs/.mirror_state.json` | yes | yes | fixed in 15.20/15.21 |
| **`dry_run_runner.py`** | `live/output/runner_state.json` | **yes** | **none** | **NEEDED THE FIX** |
| `dst_monitor.py` | none — append-only CSV | no | no | not exposed to this class |
| `csv_emitter.py` | the CSVs | no, append | no | not exposed to this class |
| `snapshot_and_push.py` | `.git/index.lock` | git's own | git's own | **NEEDED A RETRY** |

**`dry_run_runner.py` was equally exposed, and worse.** `RunnerState.save()`
wrote the temporary file with `write_text()` — no flush, no fsync — then
`replace()`ed with no retry, at five call sites:

1. `start()` — outside the step loop, so a `WinError 5` there **killed the runner
   at startup**;
2. `stop()` — inside `finally:`, so a failure **replaced the clean-stop record
   with a traceback**, and `clean_stop=True` was never persisted, so the next
   start reported `unclean_shutdown_detected` and wrote gap rows for downtime
   that was in fact a clean stop;
3. the normal step, 4. the feed-error path, 5. crash reconciliation.

The missing fsync was a second, independent defect: the rename could land while
the contents were still in an OS buffer, so a power cut leaves a valid name over
an empty file, which the runner reads as a first start — losing `pending`
(open trades, Q14), `deferred`, and the observation intervals. **None of that is
reconstructable from the CSVs.**

**Why a CSV row cannot be corrupted either way:** `CsvEmitter.emit()` refuses a
window id already present in `window_evaluations.csv`, and crash reconciliation
goes through `_emit_checked`. So the damage from a lost ledger is *ledger drift*
(wrong Q13/Q14 decisions on the next start), not duplicate or fabricated rows.
That is still disqualifying for Phase 2, but it is not silent row corruption.

**Fix:** `RunnerState.save()` now goes through `live.durable.atomic_write_text`
— fsync on the write handle, then a bounded retry of the rename. On exhausted
retries it still **raises**, deliberately: the mirror can fall back to its day
files, but the runner has no second copy of its ledger, and continuing to run
while unable to persist it would be the silent option. A crash is visible and
restartable. Three tests pin this: retried-then-succeeds, fsync-before-rename,
and a persistent lock still raising.

**`dst_monitor.py` and `csv_emitter.py` were already safe** from this class: both
only ever append (`open(..., "a")`), never rename over an existing file. They are
exposed to a *different*, milder risk — a sharing violation on the append while
something holds the file open. `dst_monitor` catches that per sample and keeps
running (a lost monitoring sample, not corruption); the emitter would surface it
as a step failure, which the run loop catches and logs. Neither fabricates or
duplicates data, so no change was made.

**`snapshot_and_push.py` was safe but lossy.** `git add` and `git commit` die
with `fatal: Unable to create '.git/index.lock': File exists` (reproduced), which
cost a whole night: the job reports failure and the next run picks it up. It now
retries lock contention and transient network errors four times over ~1.8 s, and
deliberately does **not** retry authentication failures or rejected pushes —
those need a person, and retrying them only delays the report. `push` is not
affected by `index.lock`; its retries are for the link blinking.

#### One implementation, not three

`live/durable.py` now owns the rule: the error classification, the read-only
fsync guard, and the bounded retry. `log_mirror` and `dry_run_runner` both import
it. Three copies of a subtle platform rule is how this class of bug spread in the
first place. `WINERROR` decides alone when present — Windows' `errno` is only
CPython's approximation of it, and falling back to both would retry an unrelated
`PermissionError` pointlessly.

#### The state file now moves itself out of a synced folder

Not left to the Handler remembering a flag. `resolve_state_path()` detects a sync
client (environment variables first, then a path containing OneDrive/Dropbox/
Drive/iCloud) and, when found, puts the mirror's state in
`%LOCALAPPDATA%\Chronos\mirror-state.json` — deliberately chosen because
`LOCALAPPDATA` is excluded from OneDrive's Known Folder Move. `--state` still
wins; the choice and its reason are printed on every start and by `--status`.
An existing state file at the old location is **adopted**, so the offsets are not
thrown away. Non-synced machines are unchanged.

#### `SyntaxWarning: invalid escape sequence '\p'`

`Desktop\projects` in a docstring. Docstring is now raw. A test asserts the
module imports cleanly under `-W error::SyntaxWarning`, so the next one cannot
land quietly.

#### Not changed, and why

The runner's state file was **not** moved out of the synced tree. Relocating it
mid-run would orphan the live ledger and manufacture gap rows, and the path is
part of the Handler's operational picture (`--state`, `inspect_state`). The retry
addresses the transient case; if the lock ever becomes persistent, the better fix
is to exclude `bot/live/output/` from OneDrive in the client — a GUI action on
the Handler's side, and the one I would recommend.

### 15.23 Two Windows-environment failures: sync detection, and git under Task Scheduler

#### 1. "no sync client detected" with `$OneDrive` plainly set

**The environment read was never wrong.** `os.environ["OneDrive"]` returns
`C:\Users\admin\OneDrive` correctly, and on Windows `os.environ` is already
case-insensitive. The bug was in the *predicate*, which required one of:

    OneDrive root is a string prefix of the resolved path      -> no
    the literal text "OneDrive" appears in the path            -> no

The project is at `C:\Users\admin\Desktop\projects\...` and the OneDrive
root is `C:\Users\admin\OneDrive`; neither test can hold. Reproduced here:

    env OneDrive : C:\Users\admin\OneDrive
    hint         : None
    resolve      : (...\logs\.mirror_state.json, 'next to the day files
                   (no sync client detected)')

The conflation is the defect: *a client is installed on this machine* and *this
folder is provably inside it* are different facts, and the code reported the
absence of proof as the absence of a client — silently. That silence is why it
survived to a live machine.

**Fixed by splitting the answer into three, and saying which you got:**

| `how` | meaning |
|---|---|
| `contained` | the resolved path is provably inside the client's root |
| `by path` | no variable says so, but the path names a known client |
| `configured` | the client exists; this folder cannot be shown to be inside it |

Comparison is now **component-wise** via `os.path.commonpath` on `realpath`, so
`OneDrive` no longer matches `OneDriveBackup`, and a Desktop redirected by Known
Folder Move is seen where it actually lives rather than where its name suggests.

**Relocation happens in all three cases, deliberately.** The trade is asymmetric:
the mirror's state file is machine-local, regenerable and gitignored, so parking
it in `%LOCALAPPDATA%` costs nothing; leaving it in a synced tree costs the crash
this project has hit twice. `--state` still overrides. The reason is printed on
every start, and `--status` now shows what was compared:

    sync client: OneDrive [configured] - OneDrive is configured
      (C:\Users\admin\OneDrive) but <path> is not inside it

**For the Handler:** this shape is evidence the project folder may *not* be
inside `C:\Users\admin\OneDrive` at all — two different roots. `--status`
will now say definitively. Either way the tool is correct: if the folder is
genuinely outside OneDrive, the state file is relocated slightly unnecessarily,
which is harmless.

#### 2. `FileNotFoundError: [WinError 2]` from the first scheduled run

**I could not confirm the PATH hypothesis from the message, and did not assume
it.** On Windows a missing *executable* and a missing *working directory* both
produce exactly `[WinError 2] The system cannot find the file specified`, with no
filename attached — unlike POSIX, where both carry it. So the log line is
genuinely ambiguous between two causes.

What is certain: `snapshot_and_push.py` invoked `git` as a bare subprocess name
and let `OSError` escape into the catch-all, which logged only the exception
type. That is consistent with both causes and diagnostic of neither.

**Both causes are now covered:**

* `find_git()` resolves in order: `--git`, `$CHRONOS_GIT`, PATH, then the standard
  Git for Windows locations (`C:\Program Files\Git\cmd\git.exe`, …). That last
  step fixes the failure whether or not the hypothesis is right — "Use Git from
  Git Bash only" leaves `git.exe` off the machine PATH, and a scheduled task sees
  only the machine PATH, which is exactly the asymmetry reported.
* the working directory is checked *before* git is looked for, and named if it is
  missing.
* any `OSError` becomes a `GitUnavailable` carrying the operation, the binary,
  the cwd and its existence, `winerror`, the search list and the PATH.

Before:

    FAILED with an unexpected error: FileNotFoundError: [WinError 2] The system cannot find the file specified

After:

    FAILED: git rev-parse --abbrev-ref HEAD: FileNotFoundError: [Errno 2] the repository directory does not exist
      repository    : /tmp/nonexistent-repo-test   (does not exist)
      git binary    : (none resolved)

#### 3. `--doctor`: diagnose it from the scheduled context

    python live/snapshot_and_push.py --doctor
    snapshot_and_push.bat --doctor          (arguments are passed through)

Prints the interpreter, working directory, repository, logs directory, whether
git is on PATH, where it resolved, how, the PATH entries, and the branch. Run as
**the scheduled task** — that context is the only one that can answer "why does
this work by hand and not on a timer". On a machine with git off the PATH:

    git on PATH   : <-- NOT FOUND
    git resolved  : NO -- could not find git. Looked in:
      PATH -> not found
      C:\Program Files\Git\cmd\git.exe
      ...
    PATH was: (empty)
    Fix: pass --git <path to git.exe>, or set CHRONOS_GIT.

Exit 0 when git is found, 1 when it is not, so a temporary task set to
`--doctor` shows the answer as a pass/fail in Task Scheduler itself.

#### 4. Testing this class without a scheduled run

`tests/test_scheduled_environment.py` builds the hostile environment on purpose:
`PATH` set but empty, no override, and a fake `git` at an absolute path. It runs
the real `main()`, not a helper, because the entry point is what a schedule
calls. Twelve tests; seven of the new ones fail against the pre-fix code.

The general lesson, recorded because this is the second instance: **a green suite
on Linux cannot see a Windows-only environment difference.** Both this and the
fsync bug were "works here, fails on the Handler's machine". The counters are the
guards in `live/durable.py` and the stripped-environment harness here.

### 15.24 The Handler's environment, and the gap it exposed in the git fallback

Evidence received from the machine (Git Bash, MINGW64):

    $ which git     ->  /mingw64/bin/git
    $ which python  ->  /c/Users/admin/AppData/Local/Python/pythoncore-3.14-64/python
    $ cmd //c "where git"     ->  bash: cmd: command not found
    $ cmd //c "where python"  ->  bash: cmd: command not found

**Three findings, one of them a defect in 15.23.**

1. **`/mingw64/bin/git` was not in the fallback list.** A Git for Windows install
   contains three `git.exe` binaries — `cmd\git.exe`, `mingw64\bin\git.exe`,
   `bin\git.exe` — and Git Bash prepends its own directories, so
   `mingw64\bin\git.exe` *always* wins the lookup there regardless of what the
   machine PATH holds. That is the asymmetry in one line: the terminal reports a
   binary the scheduled task may not be able to reach. 15.23 searched only
   `cmd\`, `bin\` and the per-user install, so on an install where `cmd\git.exe`
   is absent it would still have failed. Now searches all three, plus `usr\bin`
   and the scoop/chocolatey shim directories, across `%ProgramFiles%`,
   `%ProgramW6432%`, `%ProgramFiles(x86)%`, `%LOCALAPPDATA%` and scoop.

2. **`python` resolved, so the .bat's `python` line is not the problem.** The
   script ran far enough to write its own log line, which means the interpreter
   was found under Task Scheduler. Only git is in question.

3. **`cmd` is not on the Handler's Git Bash PATH**, so `where git` is not
   available to them. That is why `--doctor` matters: it answers the same
   question from inside the scheduled context, where the answer is authoritative
   anyway.

**`%VAR%` expansion was also wrong.** `os.path.expandvars` only understands
`%NAME%` when *running* on Windows, so the candidate list was both Windows-only
and **untestable everywhere else** — an untestable fallback is precisely how the
original PATH assumption survived. Now `expand_windows_vars()` expands `%NAME%`
explicitly against any mapping, and separators follow the running platform, so
the resolution the Handler's machine performs is the resolution the tests
exercise. Verified: with `PATH` empty and only `mingw64\bin\git.exe` present,
`find_git()` resolves it and `current_branch()` actually invokes it.

**Also checked: Python 3.14.** The Handler now runs 3.14, while this sandbox runs
3.11, so nothing here proves 3.14 behaviour. What can be checked statically was:
no use of `utcnow`/`utcfromtimestamp`, `distutils`, `imp`, `SafeConfigParser` or
any other API removed or deprecated since 3.12; `from __future__ import
annotations` is in every live module, so the 3.10+ union syntax is not a runtime
concern. The `\p` SyntaxWarning fixed in 15.22 matters more on 3.14 than it did
on 3.11 — invalid escapes have been warnings since 3.12 and are scheduled to
become errors. `--doctor` now prints the interpreter version, so a scheduled task
using a different Python than the Handler's terminal is visible rather than
guessed at.

**What the Handler should run next**, as the scheduled task's argument:

    --doctor

It prints the interpreter and its version, the working directory, the repository,
whether git is on PATH, where it resolved and by which rule, every install path
searched with found/absent against each, the PATH entries, and the branch. Exit 0
if git was found, 1 if not, so the task's own status shows the answer.

