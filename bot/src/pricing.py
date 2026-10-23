"""
Project Chronos — price comparison helpers
==========================================

Why this module exists
----------------------
PRD §6.4 decides context from Candle B's open versus `Mid_A`, `High_A` and
`Low_A`. `Mid_A` is pure arithmetic `(High_A + Low_A) / 2` and is NOT always
on a tick: with `tick_size = 0.1`, an odd-range Candle A puts `Mid_A` on a
half-tick (e.g. High 10000.0 / Low 9999.7 -> Mid 9999.85).

Senior Dev ruling (Q6): that is correct and must NOT be special-cased. The
only defence needed is a small epsilon against binary floating-point noise
(`0.1 + 0.2 != 0.3`), which must never be used to bend the logic.

So: comparisons that must respect tick-exact intent go through these helpers,
with `EPS = 1e-9`. `a >= b` is expressed as `ge(a, b)` meaning "a is greater
than or equal to b, allowing for float representation error".
"""

from __future__ import annotations

EPS = 1e-9


def gt(a: float, b: float) -> bool:
    """a > b, tolerant of float noise."""
    return a > b + EPS


def ge(a: float, b: float) -> bool:
    """a >= b, tolerant of float noise."""
    return a >= b - EPS


def lt(a: float, b: float) -> bool:
    """a < b, tolerant of float noise."""
    return a < b - EPS


def le(a: float, b: float) -> bool:
    """a <= b, tolerant of float noise."""
    return a <= b + EPS


def eq(a: float, b: float) -> bool:
    """a == b, tolerant of float noise."""
    return abs(a - b) <= EPS


def round_price(p: float, dp: int = 5) -> float:
    """Round for output only (never for logic) to keep CSV/SQLite readable.

    5dp is deliberate: it is finer than a tick (1dp here) yet coarse enough to
    kill float artefacts such as 25277.400000000001.
    """
    return round(p + 0.0, dp)
