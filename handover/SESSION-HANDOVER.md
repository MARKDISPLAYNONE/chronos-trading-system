# Project Chronos — Session Handover

**Purpose:** This is the single living source of truth for "where this project currently stands." Any new Handler, Senior Dev, or Coding Agent picking this up should be able to read this file top to bottom and have zero remaining context gaps. No dates are used anywhere in this document by design — only session numbers and status. Update this file at the end of every working session, before closing out.

---

## 1. Project Snapshot

**Name:** Project Chronos
**Repo:** `https://github.com/MARKDISPLAYNONE/chronos-trading-system` — branch `main`
**What it is:** An autonomous trading bot + read-only metrics dashboard that executes a fully-specified manual strategy ("Zeussy Macro Time" window model) on NAS100 via MT5, starting on a Forex.com Global demo account, with a defined path to live capital. Strictly free-tier tools throughout.

**Collaboration model:** Handler (human, relays everything) ↔ Senior Dev (AI chat, owns architecture/logic) ↔ Coding Agent (AI, executes only).

**Current phase:** ✅ **Phase 0 CLOSED. Phase 1 (Logic Validation / Historical Backtesting) now active.**

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
| `docs/02-TRD.md` | Current as of last confirmed commit (§4.1.1 NY+7 finding). **Note: a local uncommitted modification was flagged during this session — verify via `git diff` before committing; not authored by Senior Dev this session.** |
| `docs/03-APP-FLOW.md` | Current, unchanged since initial delivery. |
| `docs/04-DESIGN-BRIEF.md` | Current, unchanged since initial delivery. |
| `docs/05-DATABASE-SCHEMA.md` | Current, unchanged since initial delivery. |
| `docs/06-IMPLEMENTATION-PLAN.md` | Current, unchanged since initial delivery. |
| `docs/07-PHASE-PLAN.md` | Current. Appendix A updated this session — Phase 0 marked formally closed, historical data source decision recorded. |
| `handover/SESSION-HANDOVER.md` | This file. Fully rewritten this session. |
| `introductory/agent-intro-prompt.txt` | Current, unchanged since rename. |
| `introductory/senior-dev-intro-prompt.txt` | Current, unchanged since rename. |
| `bot/historical_validation/verify_broker_timezone.py` | Current, NY+7-confirmed version. |

---

## 4. Confirmed Facts (Do Not Re-Litigate)

- **Instrument (NAS100):** Digits 1, Contract size 1, Tick size 0.1, Tick value $0.1/tick/lot, Volume min/max/step 0.1/1000/0.1, Margin ~0.5% notional. API symbol string: `"NAS100"`.
- **Broker timezone:** Forex.com/GAIN Capital runs "NY+7" — broker server time is always exactly 7 hours ahead of New York LOCAL time, following **US DST transition dates**. Strategy logic uses ONLY UTC→`America/New_York` conversion via `zoneinfo`/`pytz`. NY+7 broker-display value is debug-only, never used in logic.
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
- GitHub repo live, correct structure, all commits pushed and verified via terminal output.
- NAS100 instrument spec confirmed via live MT5 lookup.
- Broker timezone handling fully resolved, documented, verification script built and confirmed.
- Core strategy spec fully locked: windows, context rule, sweep/reversal (sequence-agnostic), fib-based SL/TP, position sizing, partial-exit trigger (unit-bug-free).
- Python environment set up (Python 3.14, `MetaTrader5` 5.0.6231, `pytz` 2026.5), confirmed working against live MT5 demo terminal.
- MT5 demo account (Forex.com Global) confirmed accessible, order/SL mechanics confirmed functional (mechanics test only).
- **Phase 0 formally closed — all 7 checklist items done.**

---

## 8. What's NOT Done

- No code beyond the timezone verification script exists yet. No backtest script, no live bot loop, no dashboard, no Supabase project.
- Per `docs/06-IMPLEMENTATION-PLAN.md` dependency order: Step 1 (full folder structure: `dashboard/`, `supabase/`, `.github/`) and Step 2 (Supabase project creation + migrations, seeded with confirmed `instruments` row for NAS100) are next, before any bot logic is written.
- Phase 1 backtesting script (against MT5 native export data) not yet started.

---

## 9. Immediate Next Steps (In Order)

1. **Implementation Plan Step 1:** create `dashboard/`, `supabase/`, `.github/` folder structure.
2. **Implementation Plan Step 2:** create Supabase project (free tier), run initial migrations per `docs/05-DATABASE-SCHEMA.md`, seed `instruments` table with confirmed NAS100 row (see Section 4 above).
3. **Phase 1 work begins:** build historical backtesting script against MT5 native export data (NAS100, 15-minute granularity, sufficient historical range for a meaningful sample), per `docs/07-PHASE-PLAN.md` Phase 1 deliverables.
4. Verify the flagged local `docs/02-TRD.md` modification (Section 3 note above) before next commit touching that file.

---

## 10. Session Log

**Session 1:** Initial strategy spec gathering, PRD/TRD/all 8 docs drafted and delivered, repo created and pushed, introductory prompts created.

**Session 2:** NAS100 instrument spec corrected (was initially SPX500), confirmed via live MT5 lookup. Sweep/reversal rule finalized as sequence-agnostic. Manual demo test trade executed (mechanics check only).

**Session 3:** Broker timezone investigation. Nairobi/UTC+3 hypothesis tested, flagged as fragile.

**Session 4:** Broker timezone finding finalized as NY+7/US-DST convention. `docs/02-TRD.md` §4.1.1, `verify_broker_timezone.py`, handover updated and committed.

**Session 5:** `docs/07-PHASE-PLAN.md` fully rewritten (5-phase structure, closure checklist). `verify_broker_timezone.py` finalized. 10-lot=volume 10.0 confirmed. Partial-exit unit bug (handle/tick mismatch) identified and fixed — trigger logic rewritten to raw price-distance percentage. `docs/01-PRD.md` §6.7/6.8 rewritten and committed.

**Session 6 (this session):** Senior Dev made final call on historical data source: MT5 native export, primary; Dukascopy, fallback only (flagged `cross_feed_directional_only`). `docs/07-PHASE-PLAN.md` Appendix A updated — **Phase 0 formally closed.** This handover doc rewritten to reflect closure and transition into Phase 1. Flagged an unexplained local modification to `docs/02-TRD.md` for Handler to verify before committing.

---

## 11. Glossary (Quick Reference)

- **Candle A:** Reference 15m candle (HH:45–HH+1:00). Produces High_A, Low_A, Mid_A, Range.
- **Candle B:** Signal 15m candle (HH+1:00–HH+1:15). Its open determines PREMIUM/DISCOUNT/VOID context.
- **Sweep:** Price reaches High_A (+1 tick) or Low_A (−1 tick).
- **Reversal:** Price returns across High_A (−1 tick) or Low_A (+1 tick) after sweep — entry trigger.
- **Handle:** Display-only unit = 1.0 NAS100 price point = 10 ticks. Never used in trigger calculations.
- **NY+7:** Confirmed broker server time convention — broker = NY local time + 7 hours, following US DST dates.
- **cross_feed_directional_only:** Tag applied to any Phase 1 backtest result sourced from Dukascopy (non-execution-venue feed) rather than MT5 native export — indicates "directionally informative, not definitive logic validation."