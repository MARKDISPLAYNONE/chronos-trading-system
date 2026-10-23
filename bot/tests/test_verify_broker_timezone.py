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


if __name__ == "__main__":
    unittest.main()
