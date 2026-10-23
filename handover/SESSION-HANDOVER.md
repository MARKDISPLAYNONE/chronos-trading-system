# Project Chronos — Session Handover

## Current Phase
Phase 0 — Documentation & Foundation (nearly complete)

## Status Summary
All 8 core docs complete, pushed. Instrument spec confirmed (NAS100).
Broker server timezone empirically confirmed as matching Africa/Nairobi
(UTC+3) as of Oct 23, 2026 — DST behavior of broker server still needs
final confirmation from MT5/MQL5 dev contact. Demo test trade executed
successfully (10 lots, SL hit, -$203, confirms order/SL execution works).

## What's Done
- [x] Full documentation set (8 docs)
- [x] Git repo initialized, GitHub remote connected, pushed
- [x] .gitignore created and verified
- [x] Introductory prompts renamed for path consistency
- [x] MT5 demo account confirmed accessible (Forex.com Global demo)
- [x] Correct NAS100 instrument spec confirmed:
      Digits: 1 | Contract size: 1 | Tick size: 0.1 | Tick value: 0.1
      Volume: min 0.1, max 1000, step 0.1
- [x] Demo test trade executed successfully (10 lots, SL mechanics
      confirmed working) — NOTE: this was a manual execution test, NOT a
      strategy-rule-based signal trade; its risk:reward (~4.5:1 against
      the position) does not reflect the strategy's actual fib-based
      SL/TP structure and should not be used as strategy performance data
- [x] Python + MetaTrader5 package + pytz installed
- [x] Broker timezone verification script created and run
      (bot/historical_validation/verify_broker_timezone.py)
- [x] RESULT: broker server time matches Africa/Nairobi (UTC+3) as of
      Oct 23, 2026 — confirmed by cross-checking script output against
      MT5's own H1 chart display (both showed 14:00 for the same bar)
- [x] TRD amended with Section 4.1.1 (timezone handling architecture)

## What's NOT Done
- [ ] CRITICAL: confirm whether broker server timezone is FIXED UTC+3
      year-round, or a DST-observing zone (e.g., EET) that currently
      coincides with UTC+3 — ask MT5/MQL5 dev contact directly. This
      determines whether BROKER_TZ should be "Africa/Nairobi" (fixed) or
      something like "Europe/Athens" (DST-aware) in code
- [ ] Exact MT5 API symbol string confirmation (used "NAS100" in script,
      worked successfully — likely confirmed correct, but verify no
      alternate suffix/format needed, e.g., "NAS100.cfd" or similar)
- [ ] Confirm "10 contracts" in strategy = volume 10.0 in MT5 (0.1 step
      size suggests this is correct, but not yet explicitly confirmed)
- [ ] Clarify Handler's SL/TP sizing note (Session 3) — working
      interpretation: this is a $-risk sanity-check confirming ~1:2
      risk:reward from the existing fib-based SL/TP levels, NOT a change
      to the PRD Section 6.7 specification. Awaiting Handler confirmation
      this interpretation is correct
- [ ] Full folder structure (dashboard/, supabase/, .github/) — bot/
      folder has started organically via the verification script
- [ ] Supabase project creation
- [ ] Historical data source decision for Phase 1 (Dukascopy vs MT5
      export)

## Blockers
None currently — timezone DST question is an open item, not a blocker
(working assumption in place, trivially swappable if wrong).

## Open Questions
1. Is broker server timezone fixed UTC+3, or DST-observing? (ask MT5 dev)
2. Exact MT5 API symbol string — fully confirmed or needs double-check?
3. "10 contracts" = volume 10.0 — explicit confirmation needed
4. SL/TP sizing note interpretation — confirm with Handler
5. Historical data source for Phase 1 validation

## Recommended Next Step
1. Handler: ask MT5/MQL5 dev the DST question (Open Question 1) — fastest
   path to fully closing the timezone item
2. Handler: confirm Open Questions 3 and 4 (quick yes/no confirmations)
3. Senior Dev: finalize `instruments` table seed values once above
   confirmed
4. Proceed to Implementation Plan Step 1 (full folder structure) + Step 2
   (Supabase project creation)

## Session Log (append-only, most recent at top)
### Session 3
- Installed Python dependencies (MetaTrader5, pytz) successfully
- Created and ran broker timezone verification script
- CONFIRMED: broker server time = Africa/Nairobi (UTC+3) as of today's
  date — cross-checked against MT5 terminal's own chart display
- Flagged remaining DST-behavior question for broker server, to be
  resolved via direct question to MT5/MQL5 dev contact
- Executed a manual demo test trade (10 lots) — confirmed order/SL
  execution mechanics work on this account; noted this trade is NOT
  representative of strategy-rule-based R:R and shouldn't be used as
  strategy validation data
- Handler raised SL/TP dollar-risk sizing note — interpreted as a
  sanity-check confirmation of ~1:2 R:R from existing fib levels, not a
  spec change; awaiting explicit confirmation
- Amended TRD with Section 4.1.1 documenting the timezone handling
  architecture (broker time ≠ UTC assumption, DST-aware conversion
  required, no hardcoded offsets)

### Session 2
- Corrected instrument lookup (was SPX500, now confirmed NAS100 spec)
- Flagged timezone ambiguity, architectural decision made to use
  NY-time-direct calculation via DST-aware zone objects

### Session 1
- Produced full 8-document documentation set
- Initialized git repo, connected GitHub remote, pushed initial commit