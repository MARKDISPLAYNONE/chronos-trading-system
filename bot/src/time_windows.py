"""
Project Chronos — time windows & trading-day logic (PRD §6.1)
=============================================================

The 21 fixed daily windows, each 30 minutes long:

    Candle A = HH:45 -> HH+1:00
    Candle B = HH+1:00 -> HH+1:15   (entry logic evaluated here)

Three windows are deliberately EXCLUDED from the set — 15:45-16:15,
16:45-17:15 and 17:45-18:15 — because they bracket the broker's daily
platform maintenance / rollover break (17:00-18:00 New York). Senior Dev
confirmed this during Phase 1 (Q10). Per PRD §6.1 those three are
structurally NOT among the 21 "evaluated" windows, so the backtest must not
emit a `window_evaluations` row for them at all — they are excluded, not
evaluated-and-skipped.

Day rule: Monday-Friday only, in New York local time. Sunday's 18:00-24:00
session therefore produces no evaluations (PRD §6.1 as written; see
handover §11.6 Q11 for the standing note on this).

DST note (validated against real data in Phase 1): both US DST transitions
occur at 02:00 on a **Sunday**. Because this module only ever enumerates
Mon-Fri dates, a window's local start time can never fall inside a
spring-forward gap (02:00-02:59 does not exist) or a fall-back repetition
(01:00-01:59 happens twice). `window_start()` still detects a non-existent
local time and flags it rather than silently resolving it — belt and braces,
in case the day rule is ever changed.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from zoneinfo import ZoneInfo

from . import config

NY = ZoneInfo(config.NY_TZ)

#: The 21 window start hours (Candle A opens at :45 of this hour).
#: Ascending clock order is 00..14 then 18..23, but the PRD lists 23 first.
#: Order is preserved as the PRD lists it so labels/reports match the spec.
WINDOW_HOURS: tuple[int, ...] = (23, 0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 18, 19, 20, 21, 22)

#: Structurally excluded from evaluation (maintenance break). NO row is written.
EXCLUDED_WINDOW_HOURS: tuple[int, ...] = (15, 16, 17)

WINDOW_MINUTES = 30
CANDLE_MINUTES = 15


def window_label(hour: int) -> str:
    """`'01:45-02:15'` style label, matching `window_evaluations.window_label`."""
    return f"{hour:02d}:45-{(hour + 1) % 24:02d}:15"


ALL_WINDOW_LABELS: tuple[str, ...] = tuple(window_label(h) for h in WINDOW_HOURS)


@dataclass(frozen=True)
class Window:
    """One evaluation slot on one trading date."""

    trading_date: dt.date
    start_hour: int

    @property
    def label(self) -> str:
        return window_label(self.start_hour)

    @property
    def start(self) -> dt.datetime:
        """Candle A open, naive New York wall clock."""
        return dt.datetime.combine(self.trading_date, dt.time(self.start_hour, 45))

    @property
    def candle_a_start(self) -> dt.datetime:
        return self.start

    @property
    def candle_b_start(self) -> dt.datetime:
        return self.start + dt.timedelta(minutes=CANDLE_MINUTES)

    @property
    def end(self) -> dt.datetime:
        """Window close (Candle B close) — exclusive."""
        return self.start + dt.timedelta(minutes=WINDOW_MINUTES)


def is_trading_day(day: dt.date) -> bool:
    """Monday-Friday, New York calendar. Weekends are never evaluated."""
    return day.weekday() < 5


def trading_days(first: dt.date, last: dt.date) -> list[dt.date]:
    """Inclusive Mon-Fri dates between `first` and `last`."""
    out: list[dt.date] = []
    day = first
    while day <= last:
        if is_trading_day(day):
            out.append(day)
        day += dt.timedelta(days=1)
    return out


def windows_for_day(day: dt.date) -> list[Window]:
    return [Window(day, h) for h in WINDOW_HOURS]


#: When the week stops and starts, in New York wall clock. These are the two
#: instants the boundary invariants are about, and they are defined here rather
#: than inline so a caller can reason about *when a boundary was due* without
#: re-deriving the session.
FRIDAY_CLOSE = dt.time(16, 59)
SUNDAY_OPEN = dt.time(18, 0)


def most_recent_friday_close(now: dt.datetime) -> dt.datetime:
    """The latest Friday 16:59 that has already happened, at or before `now`.

    `now` is naive New York wall clock, matching the rest of this module.

    A Friday still in progress does **not** count: at 06:00 on a Friday the most
    recent completed close is the *previous* Friday, six days earlier. That is
    exactly the distinction the live gate needs and did not have — it treated the
    presence of any Friday bar as proof that a closed Friday was in view.
    """
    day = now.date()
    # `weekday()`: Monday is 0, Friday is 4.
    delta = (day.weekday() - 4) % 7
    candidate = dt.datetime.combine(day - dt.timedelta(days=delta), FRIDAY_CLOSE)
    if candidate > now:                      # today is Friday, not yet closed
        candidate -= dt.timedelta(days=7)
    return candidate


def most_recent_sunday_open(now: dt.datetime) -> dt.datetime:
    """The latest Sunday 18:00 that has already happened, at or before `now`."""
    day = now.date()
    delta = (day.weekday() - 6) % 7          # Sunday is 6
    candidate = dt.datetime.combine(day - dt.timedelta(days=delta), SUNDAY_OPEN)
    if candidate > now:                      # today is Sunday, before the open
        candidate -= dt.timedelta(days=7)
    return candidate


def localize(naive_local: dt.datetime) -> tuple[dt.datetime, bool]:
    """Attach `America/New_York` to a naive New York wall clock.

    Returns `(aware, exists)`. `exists` is False when the wall-clock time does
    not exist that day (US DST spring-forward gap, 02:00-02:59). We detect it
    by round-tripping through UTC: a non-existent local time does not survive
    the trip (02:30 comes back as 03:30).
    """
    aware = naive_local.replace(tzinfo=NY)
    back = aware.astimezone(dt.timezone.utc).astimezone(NY).replace(tzinfo=None)
    return aware, back == naive_local


def trading_date_of_bar(ny_wall: dt.datetime, start_hour: int) -> dt.date:
    """Date-tagging convention for `trading_date` (schema §7).

    `trading_date` = the calendar date on which Candle A (the HH:45 bar)
    STARTS, in New York time. For the two midnight-crossing windows this means
    e.g. the window starting 23:45 on Jan 5 is tagged 2026-01-05 even though
    it closes at 00:15 on Jan 6.
    """
    return (ny_wall - dt.timedelta(minutes=45 + (start_hour % 1))).date()
