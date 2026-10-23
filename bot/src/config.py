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

# ---------------------------------------------------------------------------
# Instrument (confirmed via live MT5 lookup — handover §4, do not re-derive)
# ---------------------------------------------------------------------------
INSTRUMENT_SYMBOL = "NAS100"

#: Smallest price increment. The whole strategy is tick-driven: sweep and
#: reversal thresholds are +/- 1 tick, TP is Mid_A +/- 1 tick.
TICK_SIZE = 0.1

#: USD value of one tick, per contract (lot). Confirmed: $0.1/tick/lot.
TICK_VALUE_PER_LOT = 0.1

#: "1 handle = 10 ticks" is DERIVED, never stored (Q9 / schema §4.1 note).
#: Kept as a property-style helper rather than a constant so it can never be
#: hardcoded into a calculation by mistake.
def handle_size_in_ticks() -> float:
    """Display/reporting unit only. NEVER an input to any trigger calculation."""
    return 1.0 / TICK_SIZE


# ---------------------------------------------------------------------------
# Position sizing (PRD §6.8)
# ---------------------------------------------------------------------------
CONTRACTS_TOTAL = 10
CONTRACTS_PARTIAL_EXIT = 9
CONTRACTS_RUNNER = 1

#: Fraction of the entry->TP distance at which the 9-contract partial fires.
#: Applied to RAW PRICE DISTANCE (PRD §6.8) — never to handles/ticks.
PARTIAL_EXIT_FRACTION = 0.80

# ---------------------------------------------------------------------------
# Stop loss (PRD §6.7) — fib multiple of Candle A's range
# ---------------------------------------------------------------------------
SL_RANGE_MULTIPLE = 0.5

# ---------------------------------------------------------------------------
# Time
# ---------------------------------------------------------------------------
#: The ONLY timezone strategy logic may reason about (PRD §6.1).
NY_TZ = "America/New_York"
UTC_TZ = "UTC"

#: Broker server clock = New York local time + 7 hours ("NY+7"), following US
#: DST dates (TRD §4.1.1, handover §4).
#:
#: IMPORTANT — Phase 1 finding: this is NOT display-only. The MT5 export in
#: `data/nas100_m1_history.csv` stores broker server time encoded as if it
#: were UTC, so this offset is load-bearing on the INPUT side. See
#: `m1_csv_source.py` and handover §13 for the empirical proof (session
#: invariants + intraday volume profile). The adapter validates the
#: convention at load time and refuses to run if the data disagrees.
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
