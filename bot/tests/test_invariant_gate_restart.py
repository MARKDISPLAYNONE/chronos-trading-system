"""
Restart-time regression matrix for the live invariant gate.

A live feed is judged by the same session invariants as a finished CSV export,
but it is queried at an arbitrary instant: Monday morning, Friday before the
weekly close, Sunday before the new week opens. The fixed-phase export always
covered whole weeks, so the gate never had to ask *which* Fridays a sample was
supposed to contain. The live feed does, on every restart.

Round 5 was a crash from answering that question wrongly. `history_bars = 3000`
reached back roughly two trading days, so on a midweek restart the sample held
no Friday at all — and the gate read "no Friday to check" as "Friday check
passed". On a Friday morning it held the in-progress Friday only, whose last bar
is 05:59, and the gate read "one Friday, did not close at 16:59" as a genuine
fault. Same window, two opposite wrong answers.

So the property this file pins down is the three-valued verdict:

  * `failed`        — the sample contains a completed instance of the boundary
                      and it is wrong. Must still raise, exactly as before;
  * `not_evaluable` — the sample contains no completed instance. Must not raise,
                      and must never be reported as a pass;
  * `passed`        — a completed instance is present and correct.

Every scenario is a real restart at a real NY wall clock, with the feed generated
to honour the session rules (17:00-18:00 weekday break, Friday close 16:59,
Sunday open 18:00) so nothing here depends on hand-written fixture rows that
could drift out of agreement with the checks.
"""

import datetime as dt
import pathlib
import sys
import unittest
from decimal import Decimal
from zoneinfo import ZoneInfo

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from src import config  # noqa: E402
from src.mt5_live_source import LiveBarSource, LiveFeedError  # noqa: E402
from tests.test_mt5_live_source import FakeMT5, FakeRates  # noqa: E402
from src.m1_csv_source import check_session_invariants  # noqa: E402
from src.bars import BarSeries, M1Bar  # noqa: E402

NY = ZoneInfo(config.NY_TZ)
BROKER_BREAK_START = dt.time(17, 0)
BROKER_BREAK_END = dt.time(18, 0)


# ---------------------------------------------------------------------------
# Feed construction
# ---------------------------------------------------------------------------
def _utc(ny: dt.datetime) -> float:
    """UTC instant for a naive NY wall clock, through the tz database."""
    return ny.replace(tzinfo=NY).timestamp()


def _broker_label(ny: dt.datetime) -> int:
    """The broker epoch the −7h rule turns back into this NY wall clock.

    Deliberately the same derivation the suite already uses for single bars
    (`broker_stamp_for_ny`), not an ad-hoc one: an improvised conversion here
    once produced 17:00-18:00 break failures that looked like a code bug.
    """
    import calendar

    return calendar.timegm(
        (ny + dt.timedelta(hours=config.BROKER_OFFSET_HOURS)).timetuple()
    )


def is_open(ny: dt.datetime, *, friday_close=dt.time(16, 59)):
    """Is the venue trading at this NY minute?

    `friday_close` is the only fault knob: the last minute of each Friday's
    session. 16:59 is the correct venue behaviour; anything else is a feed whose
    weekly boundary is wrong, and the gate must refuse it.
    """
    wd, hhmm = ny.weekday(), ny.time()
    if wd == 5:                                   # Saturday
        return False
    if wd == 6:                                   # Sunday
        return hhmm >= dt.time(18, 0)
    if wd == 4:                                   # Friday
        return hhmm <= friday_close
    return not (BROKER_BREAK_START <= hhmm < BROKER_BREAK_END)


def feed(now_ny: dt.datetime, bars: int, **faults):
    """`bars` closed minutes ending one minute before `now_ny`."""
    rows, cur = [], now_ny - dt.timedelta(minutes=1)
    price = Decimal("25400.0")
    while len(rows) < bars:
        if is_open(cur, **faults):
            rows.append((_broker_label(cur), price, price + 1, price - 1, price, 5))
        cur -= dt.timedelta(minutes=1)
    return list(reversed(rows))


def start(now_ny: dt.datetime, rows):
    """Perform the restart: construct the source exactly as the runner does."""
    mt5 = FakeMT5(FakeRates(rows))
    return LiveBarSource(mt5, now_fn=lambda: _utc(now_ny))


#: A Wednesday, so the sample always reaches back over a completed weekend.
MIDWEEK = dt.datetime(2026, 11, 4, 8, 47)


# ---------------------------------------------------------------------------
# The reported crash
# ---------------------------------------------------------------------------
class TestFridayMorningRestart(unittest.TestCase):
    """2026-10-30 06:00: the restart that failed, and its neighbours."""

    NOW = dt.datetime(2026, 10, 30, 6, 0)

    def test_the_reported_crash_no_longer_happens(self):
        source = start(self.NOW, feed(self.NOW, config.LIVE_HISTORY_BARS))
        self.assertEqual(source.gate_verdict, "passed", source.session_checks)

    def test_a_shallow_window_on_friday_is_not_evaluable_not_a_crash(self):
        """The exact old configuration: 3000 bars, Friday morning."""
        source = start(self.NOW, feed(self.NOW, 3000))
        self.assertEqual(source.gate_verdict, "not_evaluable")
        warnings = " ".join(source.gate_warnings)
        self.assertIn("friday_close_1659_ny: not evaluable", warnings)
        self.assertIn("sunday_open_1800_ny: not evaluable", warnings)

    def test_the_friday_in_progress_is_not_counted_as_a_closed_friday(self):
        """Before the fix this read `0/1 Fridays close 16:59` and raised."""
        source = start(self.NOW, feed(self.NOW, config.LIVE_HISTORY_BARS))
        detail = source.session_checks["friday_close_1659_ny"]
        self.assertTrue(detail["evaluable"])
        self.assertIn("1/1 Fridays close 16:59", detail["detail"])

    def test_a_shallow_window_before_any_friday_is_not_evaluable(self):
        """A week can start with no Friday behind it at all.

        200 bars back from Monday 03:00 stops inside Sunday evening. Note that
        depth in bars is not depth in wall clock: 600 bars from the same instant
        reaches back over the whole closed weekend to Friday 16:00, because the
        weekend contributes no bars at all.
        """
        monday = dt.datetime(2026, 11, 2, 3, 0)
        source = start(monday, feed(monday, 200))
        self.assertEqual(source.gate_verdict, "not_evaluable")
        self.assertIn("friday_close_1659_ny: not evaluable",
                      " ".join(source.gate_warnings))

    def test_a_window_that_spans_the_closed_weekend_still_judges_friday(self):
        """600 bars is enough precisely because Saturday and Sunday are shut."""
        monday = dt.datetime(2026, 11, 2, 3, 0)
        source = start(monday, feed(monday, 600))
        self.assertEqual(source.gate_verdict, "passed")
        self.assertIn("1/1 Fridays close 16:59",
                      source.session_checks["friday_close_1659_ny"]["detail"])


class TestWindowEdgeIsNotAVerdict(unittest.TestCase):
    """The second way a window edge impersonated a venue fault.

    A window that starts part-way into a Sunday holds that Sunday's 18:00 open
    outside the sample, so its first bar is whatever minute the window cut at.
    The check read that as "this Sunday opened late" and refused to start, which
    is the same mistake as the Friday one seen from the other end of the sample.
    """

    def test_a_window_starting_mid_sunday_is_not_evaluable(self):
        monday = dt.datetime(2026, 11, 2, 3, 0)
        source = start(monday, feed(monday, 200))       # head lands Sun 21:39
        self.assertEqual(source.gate_verdict, "not_evaluable")
        self.assertFalse(source.session_checks["sunday_open_1800_ny"]["evaluable"])

    def test_the_same_window_deep_enough_judges_sunday(self):
        monday = dt.datetime(2026, 11, 2, 3, 0)
        source = start(monday, feed(monday, config.LIVE_HISTORY_BARS))
        checks = source.session_checks["sunday_open_1800_ny"]
        self.assertTrue(checks["evaluable"])
        self.assertTrue(checks["passed"], checks["detail"])

    def test_a_genuinely_late_sunday_open_is_still_a_failure(self):
        """Sunday opens at 19:00 throughout: a real fault, visible in full."""
        rows = []
        cur, price = MIDWEEK - dt.timedelta(minutes=1), Decimal("25400.0")
        while len(rows) < config.LIVE_HISTORY_BARS:
            wd, hhmm = cur.weekday(), cur.time()
            trading = (
                (wd == 6 and hhmm >= dt.time(19, 0))
                or (wd < 4 and not (BROKER_BREAK_START <= hhmm < BROKER_BREAK_END))
                or (wd == 4 and hhmm <= dt.time(16, 59))
            )
            if trading:
                rows.append((_broker_label(cur), price, price + 1, price - 1, price, 5))
            cur -= dt.timedelta(minutes=1)
        with self.assertRaises(LiveFeedError) as ctx:
            start(MIDWEEK, list(reversed(rows)))
        self.assertIn("sunday_open_1800_ny", str(ctx.exception))


# ---------------------------------------------------------------------------
# The matrix: every restart instant must land on the right verdict
# ---------------------------------------------------------------------------
class TestRestartMatrix(unittest.TestCase):
    """Same healthy feed, restarted at many points in the week."""

    def assert_clean(self, now_ny, bars=config.LIVE_HISTORY_BARS):
        source = start(now_ny, feed(now_ny, bars))
        self.assertIn(
            source.gate_verdict, ("passed", "not_evaluable"),
            f"{now_ny} produced {source.gate_verdict}: {source.gate_warnings}",
        )
        self.assertIsNotNone(source.gate_verdict)
        return source

    def test_midweek_restarts_pass(self):
        for now in (
            dt.datetime(2026, 11, 2, 3, 0),      # Monday, pre-open
            dt.datetime(2026, 11, 2, 9, 35),     # Monday, mid-session
            MIDWEEK,                             # Wednesday, the 28 Oct shape
            dt.datetime(2026, 11, 5, 19, 38),    # Thursday evening, the 29 Oct shape
        ):
            with self.subTest(now=str(now)):
                self.assertEqual(self.assert_clean(now).gate_verdict, "passed")

    def test_weekend_restarts_pass(self):
        for now in (
            dt.datetime(2026, 11, 7, 12, 0),     # Saturday, venue shut
            dt.datetime(2026, 11, 8, 19, 0),     # Sunday, new week open
        ):
            with self.subTest(now=str(now)):
                self.assertEqual(self.assert_clean(now).gate_verdict, "passed")

    def test_friday_restarts_at_every_hour_of_the_session(self):
        for hour in (0, 3, 6, 10, 14, 16):
            now = dt.datetime(2026, 11, 6, hour, 0)
            with self.subTest(hour=hour):
                self.assert_clean(now)

    def test_the_hour_after_the_close_still_judges_this_friday(self):
        """16:59 has passed, so *this* Friday is now a completed instance."""
        now = dt.datetime(2026, 11, 6, 17, 30)
        source = self.assert_clean(now)
        self.assertTrue(source.session_checks["friday_close_1659_ny"]["evaluable"])


# ---------------------------------------------------------------------------
# The part that must not soften: real faults still stop the run
# ---------------------------------------------------------------------------
class TestRealFaultsStillStop(unittest.TestCase):
    """`not_evaluable` must never become a place for a bad feed to hide."""

    def assert_rejected(self, now_ny, bars=config.LIVE_HISTORY_BARS, **faults):
        with self.assertRaises(LiveFeedError) as ctx:
            start(now_ny, feed(now_ny, bars, **faults))
        self.assertIn("friday_close_1659_ny", str(ctx.exception))

    def test_a_friday_that_closes_early_is_still_a_failure(self):
        self.assert_rejected(MIDWEEK, friday_close=dt.time(15, 0))

    def test_bars_after_the_friday_close_are_still_a_failure(self):
        self.assert_rejected(MIDWEEK, friday_close=dt.time(18, 0))

    def test_the_same_fault_is_caught_on_a_friday_morning_too(self):
        friday = dt.datetime(2026, 11, 6, 6, 0)
        self.assert_rejected(friday, friday_close=dt.time(18, 0))

    def test_a_shallow_window_cannot_hide_a_fault_it_does_not_contain(self):
        """Not evaluable is not a pass — the verdict says so out loud."""
        source = start(MIDWEEK, feed(MIDWEEK, 200, friday_close=dt.time(15, 0)))
        self.assertEqual(source.gate_verdict, "not_evaluable")
        self.assertTrue(source.gate_warnings)


# ---------------------------------------------------------------------------
# What the runner does with the verdict
# ---------------------------------------------------------------------------
class TestRunnerReportsTheVerdict(unittest.TestCase):
    """The verdict is worthless if the operator never sees it.

    `start()` builds the source itself, so this drives the real construction and
    the real logging branch rather than calling the gate directly.
    """

    NOW = dt.datetime(2026, 10, 26, 11, 5)          # Monday, the 28 Oct shape

    def runner(self, bars):
        import tempfile

        from live.dry_run_runner import DryRunRunner

        out = pathlib.Path(tempfile.mkdtemp())
        logs = []
        runner = DryRunRunner(
            FakeMT5(FakeRates(feed(self.NOW, bars))), out,
            now_fn=lambda: _utc(self.NOW), log=lambda m: logs.append(str(m)),
        )
        return runner, out, logs

    def test_a_shallow_window_starts_and_says_what_it_could_not_judge(self):
        runner, out, logs = self.runner(800)
        runner.start(connect_terminal=False)        # must not raise
        gate = [line for line in logs if "invariant gate:" in line]
        self.assertTrue(gate, logs)
        self.assertTrue(all("not evaluable" in line for line in gate), gate)

        import csv
        import io

        rows = (out / "runner_events.csv").read_text(encoding="utf-8")
        kinds = [row["kind"] for row in csv.DictReader(io.StringIO(rows))]
        self.assertIn("invariant_not_evaluable", kinds)
        self.assertIn("start", kinds)

    def test_a_full_window_starts_with_a_passed_gate(self):
        runner, out, logs = self.runner(config.LIVE_HISTORY_BARS)
        runner.start(connect_terminal=False)
        self.assertIn("invariant gate: passed (all four session checks judged)",
                      " ".join(logs))


# ---------------------------------------------------------------------------
# The check itself, below the source
# ---------------------------------------------------------------------------
class TestCheckSessionInvariants(unittest.TestCase):
    """Unit-level behaviour of the shared check, backtest path included."""

    @staticmethod
    def series(now_ny, bars=config.LIVE_HISTORY_BARS, **faults):
        out = []
        for epoch, o, h, l, c, v in feed(now_ny, bars, **faults):
            ny = dt.datetime.fromtimestamp(epoch, dt.timezone.utc).replace(
                tzinfo=None
            ) - dt.timedelta(hours=config.BROKER_OFFSET_HOURS)
            out.append(M1Bar(time=ny, open=o, high=h, low=l, close=c, volume=v))
        return BarSeries(out)

    def test_the_backtest_path_is_unchanged(self):
        """No `now_ny` means complete data, so no check is ever skipped."""
        checks = check_session_invariants(self.series(MIDWEEK))
        self.assertEqual(set(checks), {
            "no_saturday_bars", "daily_break_1700_1800_ny",
            "friday_close_1659_ny", "sunday_open_1800_ny",
        })
        for name, detail in checks.items():
            self.assertTrue(detail.get("evaluable", True), name)

    def test_a_live_window_with_no_completed_friday_says_so(self):
        checks = check_session_invariants(self.series(MIDWEEK, 200), now_ny=MIDWEEK)
        self.assertFalse(checks["friday_close_1659_ny"]["evaluable"])

    def test_the_thresholds_are_the_documented_ones(self):
        """80% Fridays / 95% Sundays — the reason `not_evaluable` exists."""
        source = pathlib.Path(__file__).resolve().parents[1] / "src" / "m1_csv_source.py"
        body = source.read_text(encoding="utf-8")
        self.assertIn(">= 0.8", body)
        self.assertIn(">= 0.95", body)


if __name__ == "__main__":
    unittest.main()
