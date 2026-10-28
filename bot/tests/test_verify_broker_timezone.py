"""Tests for the corrected broker-timezone verifier (TRD §4.1.1, Phase 1).

These pin the two properties that make the correction correct:
  1. broker -> New York is a CONSTANT -7h subtraction, identical in EST and EDT;
  2. the session invariants reject a file read as true UTC.
"""

import datetime as dt
import pathlib
import sys
import unittest

HV = pathlib.Path(__file__).resolve().parents[1] / "historical_validation"
sys.path.insert(0, str(HV))

import verify_broker_timezone as v  # noqa: E402


def epoch_of(y, mo, d, h, mi):
    """Epoch whose *label* decodes to the given wall clock (i.e. how MT5 writes it)."""
    return int(dt.datetime(y, mo, d, h, mi, tzinfo=dt.timezone.utc).timestamp())


class TestConversion(unittest.TestCase):
    def test_subtracts_exactly_seven_hours(self):
        e = epoch_of(2026, 7, 1, 17, 0)
        self.assertEqual(v.new_york_wall_clock(e), dt.datetime(2026, 7, 1, 10, 0))

    def test_offset_is_constant_across_the_dst_boundary(self):
        # EST period (before 2026-03-08) and EDT period (after) must both be -7h.
        winter = epoch_of(2026, 1, 15, 17, 0)
        summer = epoch_of(2026, 7, 15, 17, 0)
        for e in (winter, summer):
            delta = v.broker_wall_clock(e) - v.new_york_wall_clock(e)
            self.assertEqual(delta, dt.timedelta(hours=7))

    def test_no_dst_dependent_branching(self):
        # If any DST logic leaked in, the same local hour would map differently
        # in January and July. It must not.
        jan = v.new_york_wall_clock(epoch_of(2026, 1, 15, 18, 0))
        jul = v.new_york_wall_clock(epoch_of(2026, 7, 15, 18, 0))
        self.assertEqual((jan.hour, jan.minute), (11, 0))
        self.assertEqual((jul.hour, jul.minute), (11, 0))

    def test_broker_wall_clock_is_the_raw_label(self):
        e = epoch_of(2026, 10, 22, 23, 59)
        self.assertEqual(v.broker_wall_clock(e), dt.datetime(2026, 10, 22, 23, 59))


def build_week(shift_hours: int = 0):
    """A synthetic Mon-Fri + Sunday week in broker-labels, optionally mis-shifted."""
    bars = []
    monday = dt.datetime(2026, 3, 2)
    for day in range(5):
        base = monday + dt.timedelta(days=day)
        for minute in range(24 * 60):
            ny = base + dt.timedelta(minutes=minute)
            if ny.weekday() == 4 and ny.time() > dt.time(16, 59):
                continue
            if dt.time(17, 0) <= ny.time() < dt.time(18, 0):
                continue
            label = ny + dt.timedelta(hours=7 + shift_hours)  # broker label
            bars.append((int(label.replace(tzinfo=dt.timezone.utc).timestamp()),
                         20000.0, 20000.2, 19999.8, 20000.0, 10))
    sunday = monday - dt.timedelta(days=1) + dt.timedelta(hours=18)
    for minute in range(6 * 60):
        ny = sunday + dt.timedelta(minutes=minute)
        label = ny + dt.timedelta(hours=7 + shift_hours)
        bars.append((int(label.replace(tzinfo=dt.timezone.utc).timestamp()),
                     20000.0, 20000.2, 19999.8, 20000.0, 10))
    bars.sort()
    return bars


class TestInvariants(unittest.TestCase):
    def test_correct_convention_passes(self):
        ok, rows = v.verify(build_week(0))
        self.assertTrue(ok, [r for r in rows if not r[1]])

    def test_utc_reading_is_rejected(self):
        # shift+7h simulates reading the same file as if the epoch were true UTC
        ok, rows = v.verify(build_week(7))
        self.assertFalse(ok)
        failed = [name for name, passed, _ in rows if not passed]
        self.assertIn("daily break 17:00-18:00 NY", failed)

    def test_saturday_bars_are_rejected(self):
        bars = build_week(0)
        sat = dt.datetime(2026, 3, 7, 10, 0) + dt.timedelta(hours=7)
        bars.append((int(sat.replace(tzinfo=dt.timezone.utc).timestamp()),
                     20000.0, 20000.2, 19999.8, 20000.0, 10))
        ok, rows = v.verify(sorted(bars))
        self.assertFalse(ok)
        self.assertIn("no Saturday bars", [n for n, p, _ in rows if not p])

    def test_empty_input_is_rejected(self):
        ok, rows = v.verify([])
        self.assertFalse(ok)


class TestVacuousWeekendChecks(unittest.TestCase):
    """A window with no Friday/Sunday in it must not be reported as a failure.

    Live regression: the trailing 3000-bar window is ~50 hours, so most of the
    time it contains neither a Friday close nor a Sunday open. The checks divided
    by that zero and reported FAIL on every sample of a Wednesday morning — 82
    samples in the 28 Oct archive. Absence of evidence, read as contradiction.
    """

    def _window(self, first_ny, last_ny):
        """Bars over [first, last] in the real session shape, as broker labels."""
        out = []
        cur = first_ny
        while cur <= last_ny:
            wd, hm = cur.weekday(), cur.strftime("%H:%M")
            trading = not (
                wd == 5
                or (wd < 4 and "17:00" <= hm < "18:00")
                or (wd == 4 and hm > "16:59")
                or (wd == 6 and hm < "18:00")
            )
            if trading:
                label = cur + dt.timedelta(hours=7)
                out.append((int(label.replace(tzinfo=dt.timezone.utc).timestamp()),
                            20000.0, 20000.2, 19999.8, 20000.0, 10))
            cur += dt.timedelta(minutes=1)
        return out

    def _row(self, rows, name):
        for n, passed, detail in rows:
            if n.startswith(name):
                return passed, detail
        raise AssertionError(f"no row starting {name!r} in {[r[0] for r in rows]}")

    def test_a_window_with_no_friday_or_sunday_passes(self):
        """Wednesday 28 Oct 02:00 -> 04:00: neither boundary exists in it."""
        bars = self._window(dt.datetime(2026, 10, 28, 2, 0),
                            dt.datetime(2026, 10, 28, 4, 0))
        ok, rows = v.verify(bars)
        self.assertTrue(ok, [r for r in rows if not r[1]])
        passed, detail = self._row(rows, "Friday close")
        self.assertTrue(passed)
        self.assertIn("not applicable", detail)
        passed, detail = self._row(rows, "Sunday open")
        self.assertTrue(passed)
        self.assertIn("not applicable", detail)

    def test_a_sunday_clipped_by_the_left_edge_is_not_judged(self):
        """The 03:36 sample: window starts 23:37 Sunday, so 18:00 was never seen."""
        bars = self._window(dt.datetime(2026, 10, 25, 23, 37),
                            dt.datetime(2026, 10, 26, 1, 0))
        ok, rows = v.verify(bars)
        passed, detail = self._row(rows, "Sunday open")
        self.assertTrue(passed, detail)
        self.assertIn("not applicable", detail)
        self.assertTrue(ok)

    def test_a_friday_clipped_by_the_right_edge_is_not_judged(self):
        """A window ending mid-Friday has not seen the close yet."""
        bars = self._window(dt.datetime(2026, 10, 23, 9, 0),
                            dt.datetime(2026, 10, 23, 12, 0))   # Friday morning
        ok, rows = v.verify(bars)
        passed, detail = self._row(rows, "Friday close")
        self.assertTrue(passed, detail)
        self.assertIn("not applicable", detail)

    def test_a_friday_that_closes_on_time_is_still_judged(self):
        bars = self._window(dt.datetime(2026, 10, 23, 15, 0),
                            dt.datetime(2026, 10, 23, 17, 30))  # covers 16:59
        ok, rows = v.verify(bars)
        passed, detail = self._row(rows, "Friday close")
        self.assertTrue(passed, detail)
        self.assertNotIn("not applicable", detail)
        self.assertIn("1/1", detail)

    def test_a_real_late_friday_bar_still_fails(self):
        """The vacuous-case fix must not swallow a genuine violation."""
        bars = self._window(dt.datetime(2026, 10, 23, 15, 0),
                            dt.datetime(2026, 10, 23, 17, 30))
        late = dt.datetime(2026, 10, 23, 17, 15) + dt.timedelta(hours=7)
        bars.append((int(late.replace(tzinfo=dt.timezone.utc).timestamp()),
                     20000.0, 20000.2, 19999.8, 20000.0, 10))
        ok, rows = v.verify(sorted(bars))
        self.assertFalse(ok)
        passed, detail = self._row(rows, "Friday close")
        self.assertFalse(passed)
        self.assertIn("1 bars later", detail)

    def test_a_real_early_sunday_bar_still_fails(self):
        bars = self._window(dt.datetime(2026, 10, 25, 18, 0),
                            dt.datetime(2026, 10, 25, 20, 0))
        early = dt.datetime(2026, 10, 25, 17, 30) + dt.timedelta(hours=7)
        bars.append((int(early.replace(tzinfo=dt.timezone.utc).timestamp()),
                     20000.0, 20000.2, 19999.8, 20000.0, 10))
        ok, rows = v.verify(sorted(bars))
        self.assertFalse(ok)
        passed, detail = self._row(rows, "Sunday open")
        self.assertFalse(passed)
        self.assertIn("1 bars earlier", detail)

    def test_the_weekend_invariants_still_hold_on_a_full_week(self):
        ok, rows = v.verify(build_week(0))
        self.assertTrue(ok)
        passed, detail = self._row(rows, "Friday close")
        self.assertIn("1/1", detail)
        passed, detail = self._row(rows, "Sunday open")
        self.assertIn("1/1", detail)


if __name__ == "__main__":
    unittest.main()
