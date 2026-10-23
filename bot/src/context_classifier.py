"""
Project Chronos — Premium / Discount / Void classification (PRD §6.4)
=====================================================================

Classification depends ONLY on Candle B's OPEN price relative to Candle A:

    Low_A <  open <  Mid_A   -> DISCOUNT -> LONG setups only
    Mid_A <  open <  High_A  -> PREMIUM  -> SHORT setups only
    open == Mid_A            -> VOID, "invalid open location"
    open >= High_A           -> VOID, "invalid open location"
    open <= Low_A            -> VOID, "invalid open location"

Two details that are easy to get wrong and are therefore handled explicitly:

1. The inequalities are STRICT. `open == Mid_A` is VOID, not "the nearer half".
2. `Mid_A` need not lie on a tick (Q6). When Candle A's range is an odd number
   of ticks, `Mid_A` is a half-tick and a tick-aligned open can never equal it,
   so the VOID-at-mid branch simply never fires. That is correct and is not
   special-cased. Comparison still goes through the epsilon helpers so float
   noise cannot flip an equality.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from .pricing import eq, ge, gt, le, lt

PREMIUM = "PREMIUM"
DISCOUNT = "DISCOUNT"
VOID = "VOID"

REASON_INVALID_OPEN_LOCATION = "invalid open location"


class Context(str, Enum):
    PREMIUM = PREMIUM
    DISCOUNT = DISCOUNT
    VOID = VOID


@dataclass(frozen=True)
class Classification:
    context: Context
    reason: str | None = None  # populated only when VOID

    @property
    def is_void(self) -> bool:
        return self.context is Context.VOID


def classify(open_b: float, low_a: float, mid_a: float, high_a: float) -> Classification:
    """Classify context from Candle B's open. Raises on an inconsistent candle."""
    if not le(low_a, mid_a) or not le(mid_a, high_a):
        raise ValueError(
            f"inconsistent candle A: low={low_a} mid={mid_a} high={high_a} "
            "(expected low <= mid <= high)"
        )

    # VOID conditions first — they are mutually exclusive with both contexts.
    if eq(open_b, mid_a) or ge(open_b, high_a) or le(open_b, low_a):
        return Classification(Context.VOID, REASON_INVALID_OPEN_LOCATION)

    if gt(open_b, mid_a) and lt(open_b, high_a):
        return Classification(Context.PREMIUM)
    if gt(open_b, low_a) and lt(open_b, mid_a):
        return Classification(Context.DISCOUNT)

    # Unreachable for well-formed input; kept explicit rather than defaulting,
    # because a silent default here would be a silent strategy bug.
    raise ValueError(
        f"unclassifiable open {open_b} against low={low_a} mid={mid_a} high={high_a}"
    )
