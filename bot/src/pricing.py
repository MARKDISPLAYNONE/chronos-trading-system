"""
Project Chronos — exact decimal price arithmetic and tick-grid quantization
===========================================================================

WHY DECIMAL
-----------
Prices arrive as decimal strings in the CSV ("25420.7"). Parsing them as
`float` introduces binary representation error immediately, and every
subsequent comparison, subtraction and halving inherits it. For a strategy
whose thresholds are one tick wide (0.1) that is indefensible: `0.1 + 0.2`
is not `0.3` in binary floating point, and a stop that lands 1e-16 above a
level can flip a WIN into a LOSS.

So every price in the engine is a `decimal.Decimal`, constructed from the
string in the file (never from a float), and every comparison is exact.
The epsilon helpers below are retained as a safety net for anything that
still arrives via float, but with Decimal input they never fire.

WHY QUANTIZATION (Phase 1 correction, Senior Dev instructed)
------------------------------------------------------------
`Mid_A = (High_A + Low_A) / 2` is exact arithmetic, and NAS100 ticks are
0.1 — so when Candle A's range spans an ODD number of ticks, `Mid_A` lands on
a half-tick (e.g. 25420.75). That is fine for COMPARISON (PRD §6.4 / Q6: the
open is compared against the exact midpoint, and the VOID-at-mid branch simply
becomes unreachable on odd ranges — correct, not special-cased).

It is NOT fine for ORDER PRICES. `SL`, `TP` and the 80% partial trigger are
submitted to a broker whose smallest increment is one tick; a 25420.75 stop
does not exist on the grid. This was an undocumented gap in the original
spec, not an engine bug: PRD §6.7 defined the fib levels but never said what
happens when they land between two ticks. It affected ~49% of trades.

THE RULE (binding — docs/02-TRD.md §4.1.2, PRD §6.7)
----------------------------------------------------
After calculation, quantize `SL`, `TP` and `partial_trigger` to the tick grid,
directionally, so that every adjustment makes the trade WORSE for us:

    SHORT  -> ROUND_FLOOR    (all three move down)
    LONG   -> ROUND_CEILING  (all three move up)

Proof that this is uniformly pessimistic rather than merely arbitrary — for
each price, "worse" means "harder to reach favourably or easier to lose":

  SHORT  entry = High_A - 1t, and SL > entry, TP < entry, TP < partial < entry
         SL floored  -> closer to entry -> tighter stop -> hit more easily   BAD for us
         TP floored  -> further below entry -> harder to reach              BAD for us
         80% floored -> further below entry -> harder to fire               BAD for us
  LONG   mirror: SL ceiled -> tighter; TP ceiled -> further; 80% ceiled -> harder.

`Mid_A` and `Range` are NEVER quantized — they stay exact so that context
classification matches the spec exactly (Q6 unchanged).
"""

from __future__ import annotations

from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR, InvalidOperation

#: Safety net only. With Decimal input there is no binary noise, so this
#: never actually decides an outcome — see the module docstring.
EPS = Decimal("1e-9")

ZERO = Decimal("0")
ONE = Decimal("1")

FLOOR = "floor"
CEILING = "ceiling"


def D(value) -> Decimal:
    """Coerce to Decimal exactly.

    Strings are parsed as-is; floats go through `str()` first so that
    `D(0.1)` is `Decimal('0.1')` and not `0.1000000000000000055511151231`.
    Never pass a float that carries a computed price — parse the string.
    """
    if isinstance(value, Decimal):
        return value
    if isinstance(value, float):
        return Decimal(str(value))
    return Decimal(value)


# ---------------------------------------------------------------------------
# Comparisons (toleration of legacy float input; exact for Decimal input)
# ---------------------------------------------------------------------------
def gt(a, b) -> bool:
    return D(a) > D(b) + EPS


def ge(a, b) -> bool:
    return D(a) >= D(b) - EPS


def lt(a, b) -> bool:
    return D(a) < D(b) - EPS


def le(a, b) -> bool:
    return D(a) <= D(b) + EPS


def eq(a, b) -> bool:
    return abs(D(a) - D(b)) <= EPS


# ---------------------------------------------------------------------------
# Tick grid
# ---------------------------------------------------------------------------
def is_on_tick_grid(value, tick) -> bool:
    """True when `value` is an exact multiple of `tick`."""
    v, t = D(value), D(tick)
    if t == 0:
        raise ValueError("tick size must be non-zero")
    try:
        return (v / t) == (v / t).to_integral_value()
    except InvalidOperation:  # pragma: no cover - defensive
        return False


def quantize_to_tick(value, tick, direction: str) -> Decimal:
    """Snap `value` to the nearest tick in the given DIRECTION (never nearest).

    Rounding nearest would be a coin-flip on whether a trade gets slightly
    better or slightly worse; the strategy takes the pessimistic side always.
    """
    v, t = D(value), D(tick)
    if direction not in (FLOOR, CEILING):
        raise ValueError(f"direction must be {FLOOR!r} or {CEILING!r}, got {direction!r}")
    rounding = ROUND_FLOOR if direction == FLOOR else ROUND_CEILING
    return (v / t).to_integral_value(rounding=rounding) * t


def direction_for(trade_direction: str) -> str:
    """SHORT -> floor, LONG -> ceiling. The single place the rule lives."""
    if trade_direction == "SHORT":
        return FLOOR
    if trade_direction == "LONG":
        return CEILING
    raise ValueError(f"unknown direction {trade_direction!r}")


def round_price(value, dp: int = 5) -> Decimal:
    """Round for OUTPUT ONLY (never for logic) so reports stay readable.

    5dp keeps values finer than a tick yet kills artefacts from accumulation.
    """
    q = Decimal(1).scaleb(-dp)
    return D(value).quantize(q)
