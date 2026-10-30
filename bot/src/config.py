"""
Project Chronos — Phase 1 configuration constants
=================================================

SCOPE NOTE (Phase 1): this module deliberately contains **no secrets and no
environment lookups**. Phase 1 is pure historical data analysis: it places no
orders and talks to no broker and no database (see docs/07-PHASE-PLAN.md
Phase 1, and handover §11.6 Q1/Q2 — Supabase is out of Phase 1 scope).

Every value below is either (a) taken from a confirmed fact in
`handover/SESSION-HANDOVER.md` §4, or (b) a value fixed by a Senior Dev
decision recorded in `docs/05-DATABASE-SCHEMA.md` §8.1. Nothing here is
invented. When Phase 2 adds `mt5_client.py` / `supabase_client.py`, their
credentials come from `bot/.env` (git-ignored) — never from this file.
"""

from decimal import Decimal

# ---------------------------------------------------------------------------
# Instrument (confirmed via live MT5 lookup — handover §4, do not re-derive)
# ---------------------------------------------------------------------------
INSTRUMENT_SYMBOL = "NAS100"

#: Smallest price increment. The whole strategy is tick-driven: sweep and
#: reversal thresholds are +/- 1 tick, TP is Mid_A +/- 1 tick. Decimal, so
#: that every price in the engine is exact (see src/pricing.py).
TICK_SIZE = Decimal("0.1")

#: USD value of one tick, per contract (lot). Confirmed: $0.1/tick/lot.
TICK_VALUE_PER_LOT = Decimal("0.1")

#: "1 handle = 10 ticks" is DERIVED, never stored (Q9 / schema §4.1 note).
#: Kept as a property-style helper rather than a constant so it can never be
#: hardcoded into a calculation by mistake.
def handle_size_in_ticks() -> "Decimal":
    """Display/reporting unit only. NEVER an input to any trigger calculation."""
    return Decimal(1) / TICK_SIZE


# ---------------------------------------------------------------------------
# Position sizing (PRD §6.8)
# ---------------------------------------------------------------------------
CONTRACTS_TOTAL = 10
CONTRACTS_PARTIAL_EXIT = 9
CONTRACTS_RUNNER = 1

#: Fraction of the entry->TP distance at which the 9-contract partial fires.
#: Applied to RAW PRICE DISTANCE (PRD §6.8) — never to handles/ticks.
PARTIAL_EXIT_FRACTION = Decimal("0.80")

# ---------------------------------------------------------------------------
# Stop loss (PRD §6.7) — fib multiple of Candle A's range
# ---------------------------------------------------------------------------
SL_RANGE_MULTIPLE = Decimal("0.5")

# ---------------------------------------------------------------------------
# Time
# ---------------------------------------------------------------------------
#: The ONLY timezone strategy logic may reason about (PRD §6.1).
NY_TZ = "America/New_York"
UTC_TZ = "UTC"

#: Broker server clock = New York local time + 7 hours ("NY+7"), following US
#: DST dates. This is the load-bearing conversion for INPUT data: MT5's bar
#: `time` field is broker server time encoded as if it were UTC, so
#:
#:     New York wall clock = utc_label(epoch) - BROKER_OFFSET_HOURS
#:
#: and that subtraction is the entire conversion — no DST lookup, no zoneinfo.
#: The DST shift is already inside the raw value, and because the broker and New
#: York change clocks on the same dates, the 7-hour offset is constant
#: year-round. Corrected and now authoritative in docs/02-TRD.md §4.1.1.
#:
#: `m1_csv_source` enforces the convention against session invariants at load
#: time and refuses to run if the data disagrees.
BROKER_OFFSET_HOURS = 7

# ---------------------------------------------------------------------------
# Backtest defaults (Q3/Q4 decisions)
# ---------------------------------------------------------------------------
#: Guard against a trade that never closes (should not happen; if it does the
#: window is reported as a data-integrity anomaly rather than silently scored).
MAX_BARS_TO_RESOLVE_TRADE = 5 * 24 * 60

#: Number of windows to emit in the human spot-check table (Phase 1 exit
#: criterion requires at least 20 to be manually verified).
SPOT_CHECK_TARGET = 24


# ---------------------------------------------------------------------------
# Phase 2 — live feed (docs/07-PHASE-PLAN.md Phase 2)
# ---------------------------------------------------------------------------
#: How much M1 history to request from the terminal per poll.
#:
#: Two different requirements, and the larger one wins:
#:
#: 1. **Evaluation.** The longest single window is 30 minutes of A+B, plus the
#:    bars needed to resolve an open trade. A couple of thousand minutes is ample.
#: 2. **The startup invariant gate.** Two of its four checks are about the weekly
#:    boundaries — "the week ends at 16:59 Friday" and "it starts at 18:00
#:    Sunday" — and they can only be judged if the sample contains the most recent
#:    such boundary. The worst case is a restart late on a Friday, which needs the
#:    *previous* Friday's close: 6 days 23 hours back, and across a session that
#:    never trades at weekends that is
#:
#:        Fri 00:00-16:00   17 h      Sun 18:00-24:00    6 h
#:        Thu               23 h      Sat                0 h
#:        Wed               23 h      Fri 00:00-16:59   17 h
#:        Tue               23 h      ------------------------
#:        Mon               23 h      total            132 h = 7,920 bars
#:
#:    9,000 gives that ~14% headroom. 3,000 — the previous value — reached back
#:    roughly two trading days, so on every weekday except Friday **no Friday was
#:    in the window at all** and the check silently skipped itself. It looked like
#:    a pass on every restart that did not happen to be on a Friday.
#:
#: The gate no longer crashes when it cannot judge a boundary (it reports
#: `not_evaluable`), so this value is not load-bearing for correctness — it is
#: what makes the check actually run rather than report that it could not.
LIVE_HISTORY_BARS = 9000

#: Safety margin before a bar counts as closed. A bar stamped T covers
#: [T, T+60s); we only accept it once `T + 60 + margin <= now`. The margin
#: absorbs the clock difference between this machine and the broker. The error
#: is deliberately one-sided: a bar may be accepted late (it arrives next poll),
#: never early (which would build Candle A/B from an unfinished candle).
FORMING_BAR_SAFETY_SECONDS = 5

#: A tick older than this cannot be used to measure the broker's UTC offset —
#: on a closed market the last tick is the *closing* tick, so the difference
#: against `time.time()` measures how long the venue has been shut. See TRD
#: §4.1.1 rule 5.
STALE_TICK_SECONDS = 120

#: How many bars the live source keeps in memory. Must comfortably exceed
#: MAX_BARS_TO_RESOLVE_TRADE (5 days) so a window that opened days ago can still
#: be scored; 10 days leaves a wide margin at negligible memory cost.
LIVE_RETENTION_BARS = 10 * 24 * 60
