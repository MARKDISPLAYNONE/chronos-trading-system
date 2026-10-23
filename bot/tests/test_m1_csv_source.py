"""Tests for the CSV adapter, convention detection and the session gate (Q3)."""

import datetime as dt
import pathlib
import sys
import tempfile
import unittest
from zoneinfo import ZoneInfo

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from src import config  # noqa: E402
from src.m1_csv_source import (  # noqa: E402
    CONVENTION_BROKER,
    CONVENTION_UTC,
    AdapterError,
    check_session_invariants,
    detect_convention,
    load_bars,
    to_ny_wall,
)

UTC = ZoneInfo("UTC")


def write_csv(path, rows, header="timestamp_utc,open,high,low,close,volume"):
    lines = [header]
    for i, (ts, o, h, l, c, v) in enumerate(rows):
        if isinstance(ts, dt.datetime):
            ts = int(ts.replace(tzinfo=UTC).timestamp())
        lines.append(f"{ts},{o},{h},{l},{c},{v}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def full_week(offset_hours):
    """A minimal but invariant-satisfying week in the given raw convention.

    Broker convention: NY wall clock = raw - 7h, so raw = NY + 7h.
    UTC convention:   raw = true UTC.
    """
    bars = []
    monday = dt.datetime(2026, 3, 2, 0, 0)  # EST period -> NY = UTC-5
    for day in range(5):  # Mon..Fri
        base = monday + dt.timedelta(days=day)
        for minute in range(0, 24 * 60):
            ny = base + dt.timedelta(minutes=minute)
            if ny.weekday() == 4 and ny.time() > dt.time(16, 59):
                continue
            if dt.time(17, 0) <= ny.time() < dt.time(18, 0):
                continue
            raw = ny + dt.timedelta(hours=offset_hours) if offset_hours else ny - dt.timedelta(hours=5)
            price = 20000.0 + (minute % 50) * 0.1
            bars.append((raw.replace(tzinfo=None), price, price + 0.2, price - 0.2, price, 10))
    # Sunday 18:00-24:00
    sunday = monday - dt.timedelta(days=1)
    for minute in range(6 * 60):
        ny = sunday + dt.timedelta(hours=18) + dt.timedelta(minutes=minute)
        raw = ny + dt.timedelta(hours=offset_hours) if offset_hours else ny - dt.timedelta(hours=5)
        price = 20000.0
        bars.append((raw.replace(tzinfo=None), price, price + 0.2, price - 0.2, price, 10))
    bars.sort()
    return bars


class TestConversion(unittest.TestCase):
    def test_broker_convention_subtracts_seven_hours(self):
        label = dt.datetime(2026, 7, 1, 17, 0)
        self.assertEqual(to_ny_wall(label, CONVENTION_BROKER), dt.datetime(2026, 7, 1, 10, 0))

    def test_utc_convention_is_dst_aware(self):
        summer = dt.datetime(2026, 7, 1, 17, 0)
        winter = dt.datetime(2026, 1, 15, 17, 0)
        self.assertEqual(to_ny_wall(summer, CONVENTION_UTC), dt.datetime(2026, 7, 1, 13, 0))  # EDT
        self.assertEqual(to_ny_wall(winter, CONVENTION_UTC), dt.datetime(2026, 1, 15, 12, 0))  # EST

    def test_the_two_conventions_differ(self):
        label = dt.datetime(2026, 7, 1, 17, 0)
        self.assertNotEqual(
            to_ny_wall(label, CONVENTION_BROKER), to_ny_wall(label, CONVENTION_UTC)
        )


class TestLoading(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = pathlib.Path(self.tmp.name) / "bars.csv"

    def tearDown(self):
        self.tmp.cleanup()

    def test_loads_iso_timestamps_too(self):
        write_csv(
            self.path,
            [
                ("2026-03-02T12:00:00Z", 100.0, 100.2, 99.9, 100.1, 5),
                ("2026-03-02T12:01:00Z", 100.1, 100.3, 100.0, 100.2, 6),
            ],
        )
        series, rep = load_bars(self.path, convention=CONVENTION_BROKER, validate=False)
        self.assertEqual(len(series), 2)
        self.assertEqual(rep.timestamp_format, "iso8601")
        self.assertEqual(series.first_time, dt.datetime(2026, 3, 2, 5, 0))

    def test_bad_header_is_rejected(self):
        write_csv(self.path, [], header="time,o,h,l,c,v")
        with self.assertRaises(AdapterError):
            load_bars(self.path, convention=CONVENTION_BROKER, validate=False)

    def test_ohlc_violation_is_rejected(self):
        write_csv(self.path, [(dt.datetime(2026, 3, 2, 12), 100.0, 99.0, 99.5, 99.8, 5)])
        with self.assertRaises(AdapterError):
            load_bars(self.path, convention=CONVENTION_BROKER, validate=False)

    def test_off_tick_price_is_rejected(self):
        write_csv(self.path, [(dt.datetime(2026, 3, 2, 12), 100.05, 100.2, 100.0, 100.1, 5)])
        with self.assertRaises(AdapterError):
            load_bars(self.path, convention=CONVENTION_BROKER, validate=False)

    def test_missing_file_is_rejected(self):
        with self.assertRaises(AdapterError):
            load_bars(self.path, convention=CONVENTION_BROKER, validate=False)


class TestSessionGate(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = pathlib.Path(self.tmp.name) / "bars.csv"

    def tearDown(self):
        self.tmp.cleanup()

    def test_broker_convention_passes_invariants(self):
        write_csv(self.path, full_week(7))
        series, rep = load_bars(self.path, convention=CONVENTION_BROKER, validate=True)
        self.assertTrue(rep.all_checks_passed(), rep.session_checks)

    def test_wrong_convention_fails_the_gate(self):
        # A file exported as broker time must NOT validate as UTC.
        write_csv(self.path, full_week(7))
        with self.assertRaises(AdapterError):
            load_bars(self.path, convention=CONVENTION_UTC, validate=True)

    def test_detection_picks_the_satisfiable_convention(self):
        write_csv(self.path, full_week(7))
        self.assertEqual(detect_convention(self.path), CONVENTION_BROKER)

    def test_saturday_bars_fail_the_gate(self):
        rows = full_week(7)
        sat = dt.datetime(2026, 3, 7, 10, 0) + dt.timedelta(hours=7)
        rows.append((sat, 20000.0, 20000.2, 19999.8, 20000.0, 1))
        write_csv(self.path, sorted(rows))
        series, _ = load_bars(self.path, convention=CONVENTION_BROKER, validate=False)
        checks = check_session_invariants(series)
        self.assertFalse(checks["no_saturday_bars"]["passed"])


if __name__ == "__main__":
    unittest.main()
