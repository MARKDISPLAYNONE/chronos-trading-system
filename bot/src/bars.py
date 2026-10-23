"""
Project Chronos — M1 bar container
==================================

A minimal, dependency-free container for 1-minute bars keyed by **naive New
York wall-clock time**.

Why naive NY wall clock rather than tz-aware UTC? Because that is exactly what
the strategy spec is written in: every rule in PRD §6 is expressed in New York
local clock time (a window starts at "HH:45"). The adapter (`m1_csv_source.py`)
is the single place where a raw timestamp is turned into this representation,
so no other module ever performs a timezone conversion and no DST arithmetic
is ever repeated.

Bars are immutable; the series is a sorted list plus an index map, giving O(1)
lookup by minute and cheap forward scanning (which the trade simulator needs).
"""

from __future__ import annotations

import bisect
import datetime as dt
from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True, slots=True)
class M1Bar:
    """One 1-minute OHLC bar. `time` is the bar's OPEN time, NY wall clock."""

    time: dt.datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: int

    def __post_init__(self) -> None:
        # Coerce at the single boundary so no price anywhere downstream is a
        # float. Callers may pass strings/Decimals; nothing else is allowed.
        for field_name in ("open", "high", "low", "close"):
            value = getattr(self, field_name)
            if not isinstance(value, Decimal):
                object.__setattr__(self, field_name, Decimal(str(value)))
        # Cheap sanity: an inverted/NaN bar would silently corrupt every
        # downstream calculation, so refuse it at construction time.
        if self.high < self.low:
            raise ValueError(f"bar {self.time}: high {self.high} < low {self.low}")
        if not (self.low <= self.open <= self.high and self.low <= self.close <= self.high):
            raise ValueError(f"bar {self.time}: open/close outside [low, high]")


class BarSeries:
    """Sorted, indexed collection of `M1Bar` keyed by NY wall-clock time."""

    __slots__ = ("_bars", "_index", "_times")

    def __init__(self, bars: list[M1Bar]) -> None:
        bars = sorted(bars, key=lambda b: b.time)
        for prev, cur in zip(bars, bars[1:]):
            if prev.time == cur.time:
                raise ValueError(f"duplicate M1 bar timestamp: {cur.time}")
        self._bars = bars
        self._times = [b.time for b in bars]
        self._index = {b.time: i for i, b in enumerate(bars)}

    def __len__(self) -> int:
        return len(self._bars)

    def __iter__(self):
        return iter(self._bars)

    @property
    def times(self) -> list[dt.datetime]:
        return self._times

    @property
    def first_time(self) -> dt.datetime:
        return self._bars[0].time

    @property
    def last_time(self) -> dt.datetime:
        return self._bars[-1].time

    def get(self, when: dt.datetime) -> M1Bar | None:
        idx = self._index.get(when)
        return None if idx is None else self._bars[idx]

    def index_of(self, when: dt.datetime) -> int | None:
        return self._index.get(when)

    def __getitem__(self, index: int) -> M1Bar:
        return self._bars[index]

    def iter_from(self, index: int, limit: int | None = None):
        """Yield bars from `index` onward (used by the trade simulator)."""
        stop = len(self._bars) if limit is None else min(len(self._bars), index + limit)
        for i in range(index, stop):
            yield self._bars[i]

    def bars_between(self, start: dt.datetime, end: dt.datetime) -> list[M1Bar]:
        """Bars with `start <= time < end` (end-exclusive, as windows are)."""
        lo = bisect.bisect_left(self._times, start)
        hi = bisect.bisect_left(self._times, end)
        return self._bars[lo:hi]

    def expected_minutes(self, start: dt.datetime, minutes: int) -> list[dt.datetime]:
        """The exact minute timestamps a complete candle of this length needs."""
        return [start + dt.timedelta(minutes=i) for i in range(minutes)]

    def last_time_on_date(self, day: dt.date) -> dt.datetime | None:
        """Last bar of a given NY calendar date — used to detect early closes."""
        lo = bisect.bisect_left(self._times, dt.datetime.combine(day, dt.time(0, 0)))
        hi = bisect.bisect_left(self._times, dt.datetime.combine(day, dt.time(0, 0)) + dt.timedelta(days=1))
        return self._times[hi - 1] if hi > lo else None
