"""
Project Chronos — sweep + reversal detection (PRD §6.5, schema §8.1 rule 1)
==========================================================================

The rule, PREMIUM / SHORT:
    sweep    : price trades to  High_A + 1 tick  or beyond
    reversal : price then trades back to High_A - 1 tick or lower   <- entry
DISCOUNT / LONG is the mirror:
    sweep    : price trades to  Low_A - 1 tick or beyond
    reversal : price then trades back to Low_A + 1 tick or higher   <- entry

Sequence-agnostic (confirmed): movement toward the OPPOSITE level neither
confirms nor invalidates anything. A re-sweep after the first sweep resets
nothing. What matters is only "has a sweep happened, and after it, has a
reversal touch happened, before the window closes".

Also confirmed: a setup exists iff there is a sweep touch at t and a reversal
touch at u > t, both before close.

Why only Candle B bars are scanned
----------------------------------
PRD §6.5 says the sweep may occur "at any point in the window", and the window
starts at HH:45 (Candle A). That sounds like a difference from scanning Candle
B only — it is not, and this is worth stating because it is the kind of thing
that gets "fixed" wrongly later: `High_A` is BY DEFINITION the maximum price of
Candle A, so during Candle A price can never reach `High_A + 1 tick`. The sweep
is therefore unreachable until Candle B, and the two readings are identical.
No bar of Candle A is skipped in principle; there is simply nothing there that
could fire. (Mirror argument for `Low_A - 1 tick` on the long side.)

Same-bar ambiguity (Senior Dev, Q5 — binding)
---------------------------------------------
With M1 bars, a single bar can contain both the sweep and the reversal.
Ordering inside a bar is unknowable, so a single bar only counts as a
confirmed trigger when its own close confirms the reversal side:

    PREMIUM: bar.high >= High_A + 1t  AND  bar.close <= High_A - 1t
    DISCOUNT: bar.low <= Low_A - 1t   AND  bar.close >= Low_A + 1t

If a bar straddles BOTH thresholds (so a reversal touch is present) but its
close does not confirm, the ordering is unproven. This module then:
  * does NOT accept the trigger for that bar, and
  * keeps looking at LATER bars — a later reversal touch is unambiguous
    (it is strictly after the sweep bar, so ordering is proven) and resolves
    the window legitimately;
  * if the window closes without such a resolution, the outcome is
    `NO_SETUP` / "ambiguous_same_bar_insufficient_granularity".
The bot never guesses in its own favour (Q5).
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from .bars import M1Bar
from .pricing import D, ge, le

REASON_NO_SWEEP = "no sweep"
REASON_SWEEP_NOT_REVERSED = "sweep not reversed"
REASON_AMBIGUOUS_SAME_BAR = "ambiguous_same_bar_insufficient_granularity"

LONG = "LONG"
SHORT = "SHORT"


@dataclass(frozen=True)
class SweepResult:
    """Outcome of scanning one Candle B for sweep + reversal."""

    triggered: bool
    reason: str | None = None
    sweep_time: dt.datetime | None = None
    sweep_price: float | None = None
    reversal_time: dt.datetime | None = None
    reversal_price: float | None = None
    trigger_bar_index: int | None = None
    ambiguous_encountered: bool = False

    @property
    def trigger_time(self) -> dt.datetime | None:
        return self.reversal_time


def _levels(direction: str, high_a, low_a, tick):
    """Return `(sweep_level, reversal_level)` for the direction."""
    high_a, low_a, tick = D(high_a), D(low_a), D(tick)
    if direction == SHORT:
        return high_a + tick, high_a - tick
    if direction == LONG:
        return low_a - tick, low_a + tick
    raise ValueError(f"unknown direction {direction!r}")


def _bar_touches_sweep(bar: M1Bar, direction: str, sweep_level: float) -> bool:
    return ge(bar.high, sweep_level) if direction == SHORT else le(bar.low, sweep_level)


def _bar_touches_reversal(bar: M1Bar, direction: str, reversal_level: float) -> bool:
    return le(bar.low, reversal_level) if direction == SHORT else ge(bar.high, reversal_level)


def _bar_close_confirms_reversal(bar: M1Bar, direction: str, reversal_level: float) -> bool:
    """The Q5 same-bar test: did this bar close back on the reversal side?"""
    return le(bar.close, reversal_level) if direction == SHORT else ge(bar.close, reversal_level)


def detect(
    direction: str,
    candle_b_bars: list[M1Bar],
    high_a: float,
    low_a: float,
    tick: float,
) -> SweepResult:
    """Scan Candle B's M1 bars in chronological order for sweep -> reversal."""
    sweep_level, reversal_level = _levels(direction, high_a, low_a, tick)

    swept = False
    sweep_time: dt.datetime | None = None
    sweep_bar_index: int | None = None
    ambiguous = False

    for i, bar in enumerate(candle_b_bars):
        if not swept:
            if not _bar_touches_sweep(bar, direction, sweep_level):
                continue
            swept = True
            sweep_time = bar.time
            sweep_bar_index = i

            if _bar_touches_reversal(bar, direction, reversal_level):
                if _bar_close_confirms_reversal(bar, direction, reversal_level):
                    # Same bar, and its close proves the reversal held.
                    return SweepResult(
                        triggered=True,
                        sweep_time=bar.time,
                        sweep_price=sweep_level,
                        reversal_time=bar.time,
                        reversal_price=reversal_level,
                        trigger_bar_index=i,
                        ambiguous_encountered=ambiguous,
                    )
                # Straddles both thresholds, but close does not confirm:
                # ordering unproven. Hold and look for a later bar.
                ambiguous = True
            continue

        # Already swept on an earlier bar: any touch of the reversal level now
        # proves the ordering, so it is a valid trigger.
        if _bar_touches_reversal(bar, direction, reversal_level):
            return SweepResult(
                triggered=True,
                sweep_time=sweep_time,
                sweep_price=sweep_level,
                reversal_time=bar.time,
                reversal_price=reversal_level,
                trigger_bar_index=i,
                ambiguous_encountered=ambiguous,
            )

    if not swept:
        return SweepResult(triggered=False, reason=REASON_NO_SWEEP)
    if ambiguous:
        return SweepResult(
            triggered=False,
            reason=REASON_AMBIGUOUS_SAME_BAR,
            sweep_time=sweep_time,
            sweep_price=sweep_level,
            ambiguous_encountered=True,
        )
    return SweepResult(
        triggered=False,
        reason=REASON_SWEEP_NOT_REVERSED,
        sweep_time=sweep_time,
        sweep_price=sweep_level,
    )
