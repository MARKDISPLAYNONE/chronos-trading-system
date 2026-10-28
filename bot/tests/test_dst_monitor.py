"""
Tests for the Phase 2 live DST monitor.

The monitor's job is to produce *evidence*, so the tests check that it records
the right things — not merely that it runs. Two of them matter most:

  * it must use the **same** invariant implementation Phase 1 gates on, so the
    two can never disagree (`test_reuses_the_phase1_verifier`);
  * it must stay silent-but-correct on a closed market: a stale tick is
    "cannot measure", never a timezone defect (the false-ESCALATE lesson).
"""

import calendar
import csv
import datetime as dt
import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from live import dst_monitor as mon  # noqa: E402
from src import config  # noqa: E402

NY_TZ = "America/New_York"


def broker_stamp(naive_ny: dt.datetime) -> int:
    return calendar.timegm(naive_ny.timetuple()) + config.BROKER_OFFSET_HOURS * 3600


def utc_ts(y, mo, d, h, mi, tz=NY_TZ):
    from zoneinfo import ZoneInfo

    return dt.datetime(y, mo, d, h, mi, tzinfo=ZoneInfo(tz)).timestamp()


class FakeRates:
    NAMES = ("time", "open", "high", "low", "close", "tick_volume")

    class _Dtype:
        names = ("time", "open", "high", "low", "close", "tick_volume")

    def __init__(self, rows):
        self._rows = list(rows)
        self.dtype = self._Dtype()

    def __len__(self):
        return len(self._rows)

    def __getitem__(self, key):
        if isinstance(key, str):
            raise KeyError(key)
        return self._rows[key]

    def __iter__(self):
        return iter(self._rows)


class FakeTick:
    def __init__(self, stamp):
        self.time = stamp


class FakeMT5:
    TIMEFRAME_M1 = 1

    def __init__(self, rows, tick_stamp=None):
        self.rows = list(rows)
        self.tick_stamp = tick_stamp

    def symbol_info_tick(self, symbol):
        return FakeTick(self.tick_stamp) if self.tick_stamp else None

    def copy_rates_from_pos(self, symbol, timeframe, start, count):
        return FakeRates(self.rows)

    def last_error(self):
        return (0, "ok")


def clean_week_rows():
    """A week that satisfies all four invariants in NY terms."""
    rows = []
    for naive in (
        dt.datetime(2026, 10, 25, 18, 0),          # Sunday open
        dt.datetime(2026, 10, 26, 16, 59),         # Monday before the break
        dt.datetime(2026, 10, 26, 18, 0),          # after the break
        dt.datetime(2026, 10, 29, 16, 59),
        dt.datetime(2026, 10, 29, 18, 0),
        dt.datetime(2026, 10, 30, 16, 59),         # Friday close
    ):
        rows.append((broker_stamp(naive), 25400.0, 25401.0, 25399.0, 25400.5, 10))
    return sorted(rows)


class TestLocalAnomalies(unittest.TestCase):
    def test_contiguous_minutes_have_no_anomaly(self):
        base = dt.datetime(2026, 10, 26, 10, 0)
        times = [base + dt.timedelta(minutes=i) for i in range(10)]
        self.assertEqual(mon.local_time_anomalies(times), (0, 0, 0))

    def test_a_repeated_local_hour_is_a_backstep(self):
        """This is what a double-converted fall-back looks like."""
        times = [dt.datetime(2026, 11, 1, 1, 55), dt.datetime(2026, 11, 1, 1, 5)]
        backsteps, _gaps, _largest = mon.local_time_anomalies(times)
        self.assertEqual(backsteps, 1)

    def test_a_closure_is_a_gap_not_a_failure(self):
        times = [dt.datetime(2026, 10, 26, 16, 59), dt.datetime(2026, 10, 26, 18, 0)]
        self.assertEqual(mon.local_time_anomalies(times), (0, 1, 61))

    def test_the_weekend_gap_is_measured(self):
        times = [dt.datetime(2026, 10, 30, 16, 59), dt.datetime(2026, 11, 1, 18, 0)]
        _b, gaps, largest = mon.local_time_anomalies(times)
        self.assertEqual(gaps, 1)
        self.assertGreater(largest, 2000)


class TestScheduleIntegrity(unittest.TestCase):
    def test_normal_days_pass(self):
        checked, bad = mon.schedule_integrity([dt.date(2026, 10, 26), dt.date(2026, 11, 2)])
        self.assertEqual(len(checked), 2)
        self.assertEqual(bad, [])

    def test_weekends_are_skipped(self):
        checked, bad = mon.schedule_integrity([dt.date(2026, 11, 1)])
        self.assertEqual(checked, [])
        self.assertEqual(bad, [])

    def test_a_transition_sunday_does_not_break_the_schedule(self):
        """Sunday is never scheduled, which is why the fall-back cannot double-fire."""
        for day in (dt.date(2026, 11, 1), dt.date(2026, 3, 8)):
            _checked, bad = mon.schedule_integrity([day])
            self.assertEqual(bad, [])


class TestVenueOpen(unittest.TestCase):
    def test_saturday_closed(self):
        self.assertFalse(mon.venue_open_at(dt.datetime(2026, 10, 31, 12, 0)))

    def test_sunday_before_open_closed(self):
        self.assertFalse(mon.venue_open_at(dt.datetime(2026, 11, 1, 17, 0)))

    def test_sunday_after_open_open(self):
        self.assertTrue(mon.venue_open_at(dt.datetime(2026, 11, 1, 18, 0)))

    def test_break_closed(self):
        self.assertFalse(mon.venue_open_at(dt.datetime(2026, 10, 26, 17, 30)))

    def test_friday_after_close_closed(self):
        self.assertFalse(mon.venue_open_at(dt.datetime(2026, 10, 30, 17, 0)))


class TestTransitions(unittest.TestCase):
    def test_nearest_transition_is_the_november_fall_back(self):
        got = mon.nearest_transition(dt.datetime(2026, 11, 2, 9, 0))
        self.assertEqual(got, dt.datetime(2026, 11, 1, 2, 0))

    def test_nearest_transition_is_the_march_spring_forward(self):
        got = mon.nearest_transition(dt.datetime(2026, 3, 10, 9, 0))
        self.assertEqual(got, dt.datetime(2026, 3, 8, 2, 0))

    def test_proximity_is_signed(self):
        near = dt.datetime(2026, 11, 1, 2, 0)
        self.assertAlmostEqual(
            mon.transition_proximity_hours(dt.datetime(2026, 11, 1, 4, 0), near), 2.0
        )


class TestSample(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.out = pathlib.Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_a_clean_open_market_samples_the_offset(self):
        now = utc_ts(2026, 10, 26, 14, 0)           # 14:00 NY, market open
        mt5 = FakeMT5(clean_week_rows(), tick_stamp=broker_stamp(dt.datetime(2026, 10, 26, 13, 59)))
        m = mon.Monitor(mt5, now_fn=lambda: now)
        sample = m.sample()
        self.assertEqual(sample.invariant_all_passed, "True")
        self.assertNotEqual(sample.measured_offset_hours, "")
        self.assertEqual(sample.venue_open, "True")
        self.assertEqual(m.failures, [])

    def test_a_stale_tick_is_not_a_timezone_defect(self):
        """Sunday, market shut since Friday: same rule as the probe."""
        now = utc_ts(2026, 10, 25, 12, 0)
        mt5 = FakeMT5(clean_week_rows(),
                      tick_stamp=broker_stamp(dt.datetime(2026, 10, 23, 16, 59)))
        m = mon.Monitor(mt5, now_fn=lambda: now)
        sample = m.sample()
        self.assertEqual(sample.measured_offset_hours, "")
        self.assertIn("cannot measure live offset", sample.notes)
        self.assertEqual(m.failures, [])

    def test_a_broken_feed_fails_the_invariants(self):
        rows = [(broker_stamp(dt.datetime(2026, 10, 26, 17, 30)), 1.0, 1.0, 1.0, 1.0, 1)]
        now = utc_ts(2026, 10, 26, 19, 0)
        m = mon.Monitor(FakeMT5(rows), now_fn=lambda: now)
        sample = m.sample()
        self.assertEqual(sample.invariant_all_passed, "False")
        self.assertIn("daily break", sample.invariant_failures)
        self.assertTrue(m.failures)

    def test_it_reuses_the_phase1_verifier(self):
        """Not a re-implementation — it *is* verify_broker_timezone.py.

        Proven two ways: the loaded module's file is that script, and its
        verdict on given bars matches calling it directly. If the monitor ever
        grew its own check, one of these would diverge.
        """
        now = utc_ts(2026, 10, 26, 14, 0)
        m = mon.Monitor(FakeMT5(clean_week_rows()), now_fn=lambda: now)
        self.assertTrue(m._verifier.__file__.endswith("verify_broker_timezone.py"))
        self.assertEqual(m._verifier.BROKER_OFFSET_HOURS, config.BROKER_OFFSET_HOURS)

        bars = clean_week_rows()
        self.assertEqual(m._verifier.verify(bars)[0], True)
        broken = [(broker_stamp(dt.datetime(2026, 10, 26, 17, 30)), 1.0, 1.0, 1.0, 1.0, 1)]
        self.assertEqual(m._verifier.verify(broken)[0], False)

    def test_the_monitor_and_the_verifier_agree_on_the_same_bars(self):
        """The monitor's PASS must equal the verifier's own verdict."""
        now = utc_ts(2026, 10, 26, 14, 0)
        mt5 = FakeMT5(clean_week_rows())
        m = mon.Monitor(mt5, now_fn=lambda: now)
        sample = m.sample()
        direct_ok, _rows = m._verifier.verify(clean_week_rows())
        self.assertEqual(sample.invariant_all_passed, str(direct_ok))

    def test_no_bars_is_reported_not_crashed(self):
        now = utc_ts(2026, 10, 26, 14, 0)
        m = mon.Monitor(FakeMT5([], tick_stamp=broker_stamp(dt.datetime(2026, 10, 26, 13, 59))),
                        now_fn=lambda: now)
        sample = m.sample()
        self.assertEqual(sample.invariant_all_passed, "False")
        self.assertTrue(m.failures)

    def test_csv_row_has_every_column(self):
        now = utc_ts(2026, 10, 26, 14, 0)
        m = mon.Monitor(FakeMT5(clean_week_rows(),
                                tick_stamp=broker_stamp(dt.datetime(2026, 10, 26, 13, 59))),
                        now_fn=lambda: now)
        row = m.sample().as_row()
        self.assertEqual(list(row.keys()), mon.CSV_COLUMNS)


class TestAppendOnlyCsv(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.out = pathlib.Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_header_once_rows_appended(self):
        writer = mon.AppendOnlyCsv(self.out / "d.csv", ["a", "b"])
        writer.write({"a": 1, "b": 2})
        writer.write({"a": 3, "b": 4})
        lines = (self.out / "d.csv").read_text().splitlines()
        self.assertEqual(lines[0], "a,b")
        self.assertEqual(len(lines), 3)

    def test_unknown_keys_are_ignored(self):
        writer = mon.AppendOnlyCsv(self.out / "d.csv", ["a"])
        writer.write({"a": 1, "surprise": 2})
        with (self.out / "d.csv").open() as fh:
            rows = list(csv.DictReader(fh))
        self.assertEqual(rows[0]["a"], "1")


class TestRunOnce(unittest.TestCase):
    def test_once_writes_one_row_and_exits_zero(self):
        with tempfile.TemporaryDirectory() as tmp:
            now = utc_ts(2026, 10, 26, 14, 0)
            mt5 = FakeMT5(clean_week_rows(),
                          tick_stamp=broker_stamp(dt.datetime(2026, 10, 26, 13, 59)))
            m = mon.Monitor(mt5, now_fn=lambda: now)
            rc = mon.run(mt5, out_dir=pathlib.Path(tmp), sample_seconds=1,
                         once=True, monitor=m)
            self.assertEqual(rc, 0)
            with (pathlib.Path(tmp) / "dst_monitor.csv").open() as fh:
                rows = list(csv.DictReader(fh))
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["invariant_all_passed"], "True")

    def test_once_exits_one_when_a_check_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            rows = [(broker_stamp(dt.datetime(2026, 10, 26, 17, 30)), 1.0, 1.0, 1.0, 1.0, 1)]
            now = utc_ts(2026, 10, 26, 19, 0)
            mt5 = FakeMT5(rows)
            m = mon.Monitor(mt5, now_fn=lambda: now)
            rc = mon.run(mt5, out_dir=pathlib.Path(tmp), sample_seconds=1,
                         once=True, monitor=m)
            self.assertEqual(rc, 1)


def weekday_only_rows(first_ny, last_ny):
    """A contiguous run of bars in the real session shape, no weekend boundary."""
    rows = []
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
            rows.append((broker_stamp(cur), 25400.0, 25401.0, 25399.0, 25400.5, 10))
        cur += dt.timedelta(minutes=1)
    return rows


class TestVacuousWeekendChecksInTheMonitor(unittest.TestCase):
    """Live regression: `[FAIL]` on every sample of a Wednesday morning.

    The trailing 3000-bar window is about 50 hours, so most samples contain no
    Friday close and no Sunday open. `verify()` reported the absent denominator as
    a failure, which flagged 82 samples in the 28 Oct archive while the feed was
    healthy. Absence of evidence is not evidence of failure.
    """

    def bars(self):
        return weekday_only_rows(dt.datetime(2026, 10, 27, 6, 0),
                                 dt.datetime(2026, 10, 27, 9, 0))   # Tuesday

    def test_a_window_without_weekend_boundaries_reports_pass(self):
        ok, out = mon.load_verifier().verify(self.bars())
        self.assertTrue(ok, [r for r in out if not r[1]])
        detail = {name: d for name, _p, d in out}
        self.assertIn("not applicable", detail["Friday close 16:59 NY"])
        self.assertIn("not applicable", detail["Sunday open 18:00 NY"])

    def test_a_genuine_late_friday_bar_still_fails_through_the_monitor(self):
        bars = self.bars()
        bars.append((broker_stamp(dt.datetime(2026, 10, 30, 17, 30)),
                     25400.0, 25401.0, 25399.0, 25400.5, 10))
        ok, out = mon.load_verifier().verify(sorted(bars))
        self.assertFalse(ok)
        failed = {name for name, passed, _d in out if not passed}
        self.assertIn("Friday close 16:59 NY", failed)

    def test_the_sample_note_says_the_check_did_not_run(self):
        bars = self.bars()
        now = dt.datetime(2026, 10, 27, 9, 0, tzinfo=dt.timezone.utc).timestamp()
        # A fresh tick so liveness is not the thing under test here. The stamp is
        # a broker label, not a UTC epoch (see tick_age_seconds).
        verifier = mon.Monitor(
            FakeMT5(bars, tick_stamp=broker_stamp(dt.datetime(2026, 10, 27, 8, 59))),
            now_fn=lambda: now,
        )
        sample = verifier.sample()
        self.assertEqual(sample.invariant_all_passed, "True")
        self.assertIn("not applicable", sample.notes)
        self.assertEqual(verifier.failures, [])


if __name__ == "__main__":
    unittest.main()
