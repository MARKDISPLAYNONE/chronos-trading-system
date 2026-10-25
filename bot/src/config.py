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
#: How much M1 history to request from the terminal per poll. Must comfortably
#: cover the longest single window evaluation (30 minutes of A+B) plus the bars
#: needed to resolve an open trade, so 3,000 minutes (~2 days) is generous.
LIVE_HISTORY_BARS = 3000

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
