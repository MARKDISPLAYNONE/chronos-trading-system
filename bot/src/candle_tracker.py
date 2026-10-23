"""
Project Chronos — Candle A / Candle B construction (PRD §6.2, §6.3)
===================================================================

Candle A: the 15-minute reference candle at HH:45 -> HH+1:00.
Candle B: the following 15-minute candle at HH+1:00 -> HH+1:15.

Both are pure OHLC aggregation of M1 bars, which is why granularity does not
matter here (Senior Dev, Q5): aggregating M1 to 15 minutes is exact, so
Candle A/B structure is identical whether built from M1 or M15 input. Only
sweep/reversal *sequencing* needs M1.

The builder reports `n_bars` and `complete` rather than silently accepting a
short candle: a Candle A missing minutes has a possibly-wrong High/Low, and a
missing Candle B open would change the context classification. Deciding what
to do about that is the evaluator's job, not this module's.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from .bars import BarSeries, M1Bar


@dataclass(frozen=True)
class Candle:
    """Aggregated 15-minute candle built from M1 bars."""

    start: dt.datetime
    end: dt.datetime
    open: float
    high: float
    low: float
    close: float
    n_bars: int
    expected_bars: int
    bars: tuple[M1Bar, ...]

    @property
    def complete(self) -> bool:
        return self.n_bars == self.expected_bars

    @property
    def mid(self) -> float:
        """`Mid_A` — pure arithmetic, NOT forced onto a tick (Senior Dev, Q6)."""
        return (self.high + self.low) / 2.0

    @property
    def range(self) -> float:
        """`High_A - Low_A`, used for the fib-based stop loss (PRD §6.7)."""
        return self.high - self.low


def build_candle(
    series: BarSeries,
    start: dt.datetime,
    minutes: int = 15,
) -> Candle | None:
    """Aggregate M1 bars in `[start, start+minutes)` into a candle.

    Returns None when there is not a single bar in the range (that means no
    market data at all for this stretch, which the evaluator reports as
    `market_closed_no_data`).
    """
    end = start + dt.timedelta(minutes=minutes)
    bars = series.bars_between(start, end)
    if not bars:
        return None
    return Candle(
        start=start,
        end=end,
        open=bars[0].open,
        high=max(b.high for b in bars),
        low=min(b.low for b in bars),
        close=bars[-1].close,
        n_bars=len(bars),
        expected_bars=minutes,
        bars=tuple(bars),
    )
