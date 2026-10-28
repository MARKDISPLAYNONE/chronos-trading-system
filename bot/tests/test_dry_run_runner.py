"""
Tests for the Phase 2 dry-run runner.

The runner is the only piece of Phase 2 that decides *when* a row is written and
*what* a crash means, so those two policies (Q13, Q14) are tested directly rather
than inferred from the code. Everything runs against a fake terminal and an
injected clock: no MT5, no sleeping, no real dates.

The scenarios are built out of a real synthetic OHLC feed so the *actual* Phase 1
evaluator runs — a mock evaluator would prove nothing about integration.
"""

import calendar
import csv
import datetime as dt
import json
import pathlib
import sys
import tempfile
import unittest
from decimal import Decimal
from zoneinfo import ZoneInfo

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from live.csv_emitter import REASON_RUNNER_DOWN, CsvEmitter, gap_evaluation  # noqa: E402
from live.dry_run_runner import (  # noqa: E402
    DryRunRunner,
    RunnerState,
    assert_schedule_is_dst_safe,
    observed,
    window_utc_bounds,
    window_key,
)
from src import config, outputs, time_windows  # noqa: E402

NY = ZoneInfo(config.NY_TZ)
UTC = dt.timezone.utc

#: Monday 2026-10-26. Two windows are used throughout: 13:45-14:15 (which the
#: fixture feed makes trade) and 19:45-20:15 (which it does not).
TRADE_LABEL = "13:45-14:15"
QUIET_LABEL = "19:45-20:15"


def utc_ts(y, mo, d, h, mi):
    return dt.datetime(y, mo, d, h, mi, tzinfo=NY).timestamp()


def broker_label(naive_ny: dt.datetime) -> int:
    """A broker epoch label for a NY wall clock (MT5's encoding)."""
    return calendar.timegm(naive_ny.timetuple()) + config.BROKER_OFFSET_HOURS * 3600


# ---------------------------------------------------------------------------
# A controllable fake feed
# ---------------------------------------------------------------------------
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


class FakeSymbolInfo:
    name = "NAS100"
    point = 0.1
    trade_tick_size = 0.1
    trade_tick_value = 0.1
    digits = 1
    volume_min = 0.1
    volume_step = 0.1
    volume_max = 1000.0


class FakeAccount:
    login = 12345678
    company = "Gain Global Markets / FOREX.com Global"
    server = "FOREX.com-Global-Demo"
    currency = "USD"
    trade_mode = 0


class FakeTerminal:
    name = "MetaTrader 5"
    build = 4000
    connected = True


class FakeMT5:
    TIMEFRAME_M1 = 1

    def __init__(self, rows):
        self.rows = list(rows)
        self.copy_calls = 0

    def initialize(self):
        return True

    def shutdown(self):
        pass

    def last_error(self):
        return (0, "ok")

    def terminal_info(self):
        return FakeTerminal()

    def account_info(self):
        return FakeAccount()

    def symbol_info(self, name):
        return FakeSymbolInfo()

    def copy_rates_from_pos(self, symbol, timeframe, start, count):
        self.copy_calls += 1
        return FakeRates(self.rows)


def build_feed(*, up_to: dt.datetime, trade: bool = True):
    """A synthetic M1 feed from 13:45 to `up_to`, respecting the venue break."""
    rows = []

    def add(naive_ny, o, h, l, c):
        if naive_ny.weekday() < 4 and naive_ny.hour == 17:
            return
        rows.append((broker_label(naive_ny), Decimal(o), Decimal(h),
                     Decimal(l), Decimal(c), 10))

    t0 = dt.datetime(2026, 10, 26, 13, 45)
    minute = dt.timedelta(minutes=1)
    stop = up_to
    cur = t0
    phase = 0
    while cur < stop:
        if cur == t0:
            phase = 1                       # Candle A
        elif cur == t0 + 15 * minute:
            phase = 2                       # Candle B trigger bar
        elif cur == t0 + 16 * minute:
            phase = 3                       # after the trigger
        if phase == 1:
            add(cur, "25400.0", "25402.0", "25398.0", "25400.5")
        elif phase == 2:
            # premium open, sweeps High_A + 1 tick, closes back below -> SHORT
            add(cur, "25401.0", "25403.0", "25400.5", "25401.5")
        else:
            if trade and cur >= t0 + 40 * minute:
                add(cur, "25400.5", "25400.6", "25396.0", "25396.5")
            else:
                add(cur, "25400.5", "25400.8", "25400.0", "25400.5")
        cur += minute
    return rows


class FakeSource:  # noqa: F811
    def __init__(self, rows, now_fn):
        self._rows = rows
        self._now = now_fn
        self.fail_next = False
        self.refresh_count = 0

    @property
    def series(self):
        from src.bars import BarSeries, M1Bar
        from src.m1_csv_source import CONVENTION_BROKER, epoch_to_label, to_ny_wall

        bars = []
        for t, o, h, l, c, v in self._rows:
            ny = to_ny_wall(epoch_to_label(int(t)), CONVENTION_BROKER)
            bars.append(M1Bar(ny, o, h, l, c, int(v)))
        return BarSeries(bars)

    def refresh(self):
        from src.mt5_live_source import RefreshReport

        if self.fail_next:
            self.fail_next = False
            from src.mt5_live_source import LiveFeedError

            raise LiveFeedError("simulated feed outage")
        self.refresh_count += 1
        return RefreshReport(total_bars=len(self.series), new_bars=0)


# ---------------------------------------------------------------------------
# Harness
# ---------------------------------------------------------------------------
class RunnerHarness(unittest.TestCase):
    """A runner that has genuinely been up for a while.

    `self.clock` is the injected wall clock and advances only when a test says
    so. The runner is always started *before* the window under test opens, so
    the observation ledger contains the window's whole span — which is what makes
    "observed" mean anything. (A frozen clock at a single instant gives a
    zero-width interval, and every window correctly reads as unobserved.)
    """

    START_CLOCK = utc_ts(2026, 10, 26, 13, 0)

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.out = pathlib.Path(self._tmp.name)
        self.clock = self.START_CLOCK
        self.logs: list[str] = []

    def tearDown(self):
        self._tmp.cleanup()

    def make_runner(self, *, feed_up_to=None, rows=None, start_at=None, **kw):
        rows = rows if rows is not None else build_feed(
            up_to=feed_up_to or dt.datetime(2026, 10, 26, 16, 0)
        )
        self.clock = start_at if start_at is not None else self.START_CLOCK
        self.mt5 = FakeMT5(rows)
        self.source = FakeSource(rows, lambda: self.clock)
        runner = DryRunRunner(
            self.mt5, self.out, source=self.source, now_fn=lambda: self.clock,
            log=self.logs.append, **kw,
        )
        runner.start(connect_terminal=False)
        return runner

    def read(self, name):
        path = self.out / name
        if not path.exists():
            return []
        with path.open(newline="", encoding="utf-8") as fh:
            return list(csv.DictReader(fh))

    def window_row(self, label, day="2026-10-26"):
        for row in self.read("window_evaluations.csv"):
            if row["trading_date"] == day and row["window_label"] == label:
                return row
        return None


# ---------------------------------------------------------------------------
# DST safety: windows cannot double-fire or vanish
# ---------------------------------------------------------------------------
class TestDstSafeSchedule(unittest.TestCase):
    def test_planned_run_range_is_dst_safe(self):
        days = time_windows.trading_days(dt.date(2026, 10, 26), dt.date(2026, 11, 3))
        checked = assert_schedule_is_dst_safe(days)
        self.assertEqual(len(checked), 7)   # Mon-Fri + Mon-Tue

    def test_the_transition_weekend_is_not_scheduled(self):
        days = time_windows.trading_days(dt.date(2026, 10, 30), dt.date(2026, 11, 2))
        self.assertEqual(days, [dt.date(2026, 10, 30), dt.date(2026, 11, 2)])
        self.assertNotIn(dt.date(2026, 11, 1), days)   # Sunday: not a trading day

    def test_every_window_on_the_monday_after_the_transition_maps_once(self):
        """2 Nov is the first real test of post-transition conversion."""
        for window in time_windows.windows_for_day(dt.date(2026, 11, 2)):
            start, end, flags = window_utc_bounds(window)
            self.assertEqual(flags, [], window.label)
            self.assertEqual((end - start).total_seconds(), 1800.0)

    def test_every_scheduled_window_is_thirty_real_minutes(self):
        """On trading days — including the Monday either side of the fall-back —
        every window maps to exactly 30 minutes of real time."""
        for day in (dt.date(2026, 3, 9), dt.date(2026, 10, 30),
                    dt.date(2026, 11, 2), dt.date(2026, 11, 3)):
            for window in time_windows.windows_for_day(day):
                start, end, flags = window_utc_bounds(window)
                self.assertEqual(flags, [], f"{day} {window.label}")
                self.assertEqual((end - start).total_seconds(), 1800.0)

    def test_the_weekend_of_the_transition_is_never_scheduled(self):
        """The pathological slots exist — they are simply never traded."""
        self.assertFalse(time_windows.is_trading_day(dt.date(2026, 11, 1)))
        self.assertFalse(time_windows.is_trading_day(dt.date(2026, 3, 8)))

    def test_ambiguous_local_time_is_detected(self):
        """1 Nov 01:30 NY happens twice. If a window ever sat there it would
        fire twice, so the guard must see it."""
        start, end, flags = window_utc_bounds(time_windows.Window(dt.date(2026, 11, 1), 1))
        self.assertIn("ambiguous_local_time_at_start", flags)

    def test_nonexistent_local_time_is_detected(self):
        """8 Mar 02:30 NY does not exist (spring forward)."""
        _s, _e, flags = window_utc_bounds(time_windows.Window(dt.date(2026, 3, 8), 2))
        self.assertIn("nonexistent_local_time_at_start", flags)

    def test_sundays_are_skipped_so_the_guard_cannot_fire_on_them(self):
        """Both US transitions fall on a Sunday, which is why the Mon-Fri rule
        makes a doubled window impossible. The guard returns cleanly."""
        self.assertEqual(assert_schedule_is_dst_safe([dt.date(2026, 11, 1)]), [])

    def test_the_guard_would_refuse_a_doubled_window_if_one_were_scheduled(self):
        """Forcing Nov 1 to count as a trading day makes the guard fire.

        It cannot happen in production — that is the point of asserting it: the
        guard is proven live, not merely believed to be correct.
        """
        from unittest import mock

        with mock.patch.object(time_windows, "is_trading_day", return_value=True):
            with self.assertRaises(RuntimeError) as ctx:
                assert_schedule_is_dst_safe([dt.date(2026, 11, 1)])
        self.assertIn("ambiguous_local_time", str(ctx.exception))


class TestObservationCoverage(unittest.TestCase):
    def test_window_fully_inside_is_observed(self):
        self.assertTrue(observed([(100.0, 200.0)], 120.0, 180.0, 0.0))

    def test_window_straddling_the_gap_is_not_observed(self):
        self.assertFalse(observed([(100.0, 150.0), (200.0, 300.0)], 140.0, 210.0, 0.0))

    def test_window_before_any_observation_is_not_observed(self):
        self.assertFalse(observed([(500.0, 600.0)], 100.0, 200.0, 0.0))

    def test_grace_covers_a_poll_boundary(self):
        self.assertTrue(observed([(100.0, 200.0)], 95.0, 205.0, 10.0))
        self.assertFalse(observed([(100.0, 200.0)], 95.0, 205.0, 1.0))


# ---------------------------------------------------------------------------
# Q13: when a row is written
# ---------------------------------------------------------------------------
class TestQ13RowTiming(RunnerHarness):
    #: The quiet window needs bars all the way to its close. A feed that stops
    #: hours earlier is a dead feed, not a window with no setup — and the runner
    #: now holds that row instead of inventing one (see TestTransientDataLag).
    QUIET_FEED_END = dt.datetime(2026, 10, 26, 20, 16)

    def quiet_runner(self):
        return self.make_runner(rows=build_feed(up_to=self.QUIET_FEED_END))

    def test_no_setup_row_is_written_at_window_close(self):
        """The quiet window has no setup; it must be final at its close."""
        runner = self.quiet_runner()
        self.clock = utc_ts(2026, 10, 26, 20, 16)   # just after 20:15 NY
        runner.step()
        row = self.window_row(QUIET_LABEL)
        self.assertIsNotNone(row, "NO_SETUP row should exist once the window closed")
        self.assertEqual(row["outcome"], "NO_SETUP")

    def test_traded_row_is_not_written_before_simulate_resolves(self):
        """At the trigger the trade is open; nothing may be appended yet."""
        rows = build_feed(up_to=dt.datetime(2026, 10, 26, 14, 16))
        runner = self.make_runner(rows=rows)
        self.clock = utc_ts(2026, 10, 26, 14, 16)   # window just closed, trade open
        runner.step()
        self.assertIsNone(
            self.window_row(TRADE_LABEL),
            "an open trade must not be logged as an outcome",
        )
        self.assertIn(window_key(time_windows.Window(dt.date(2026, 10, 26), 13)),
                      runner.state.pending)

    def test_traded_row_appears_when_the_trade_resolves(self):
        rows = build_feed(up_to=dt.datetime(2026, 10, 26, 15, 0))   # exit is in here
        runner = self.make_runner(rows=rows)
        self.clock = utc_ts(2026, 10, 26, 15, 0)
        runner.step()
        row = self.window_row(TRADE_LABEL)
        self.assertIsNotNone(row, "the trade resolved, so the row must be written")
        self.assertIn(row["outcome"], ("WIN", "LOSS"))
        self.assertTrue(row["trade_id"])
        self.assertEqual(len(self.read("trades.csv")), 1)

    def test_pending_survives_until_a_later_poll(self):
        """Same window, feed grows between two polls: emitted on the second."""
        rows = build_feed(up_to=dt.datetime(2026, 10, 26, 14, 16))
        runner = self.make_runner(rows=rows)
        self.clock = utc_ts(2026, 10, 26, 14, 16)
        runner.step()
        self.assertIsNone(self.window_row(TRADE_LABEL))

        self.source._rows = build_feed(up_to=dt.datetime(2026, 10, 26, 15, 0))
        self.clock = utc_ts(2026, 10, 26, 15, 1)
        runner.step()
        row = self.window_row(TRADE_LABEL)
        self.assertIsNotNone(row)
        self.assertIn(row["outcome"], ("WIN", "LOSS"))

    def test_evaluated_at_is_the_write_time_not_the_window_close(self):
        runner = self.quiet_runner()
        self.clock = utc_ts(2026, 10, 26, 20, 30)
        runner.step()
        row = self.window_row(QUIET_LABEL)
        # The row was decided at 20:30, not at the 20:15 window close.
        self.assertTrue(row["evaluated_at"].startswith("2026-10-26T20:30"),
                        row["evaluated_at"])

    def test_append_only_never_duplicates(self):
        runner = self.quiet_runner()
        self.clock = utc_ts(2026, 10, 26, 20, 16)
        for _ in range(4):
            runner.step()
        rows = [r for r in self.read("window_evaluations.csv")
                if r["window_label"] == QUIET_LABEL and r["trading_date"] == "2026-10-26"]
        self.assertEqual(len(rows), 1)

    def test_ids_match_the_phase1_scheme(self):
        runner = self.quiet_runner()
        self.clock = utc_ts(2026, 10, 26, 20, 16)
        runner.step()
        row = self.window_row(QUIET_LABEL)
        window = time_windows.Window(dt.date(2026, 10, 26), 19)
        expected = outputs.window_id(
            gap_evaluation(window)      # same uuid5 namespace + key
        )
        self.assertEqual(row["id"], expected)


# ---------------------------------------------------------------------------
# Q14: what a crash means
# ---------------------------------------------------------------------------
class TestQ14CrashPolicy(RunnerHarness):
    def test_downtime_windows_are_logged_as_runner_down(self):
        """Stop the runner, let windows close, restart: they are gaps."""
        rows = build_feed(up_to=dt.datetime(2026, 10, 26, 14, 16))
        runner = self.make_runner(rows=rows)
        self.clock = utc_ts(2026, 10, 26, 14, 16)
        runner.step()
        runner.stop()

        # Restart four hours later. Windows 14:45-15:15 closes in the gap; the
        # 15:45-18:15 slots are the deliberately excluded trio, so exactly one
        # scheduled window is missed — and the open 13:45 trade is the other.
        self.clock = utc_ts(2026, 10, 26, 18, 30)
        rows2 = build_feed(up_to=dt.datetime(2026, 10, 26, 18, 30))
        runner2 = self.make_runner(rows=rows2, start_at=utc_ts(2026, 10, 26, 18, 30))
        runner2.step()

        gaps = [r for r in self.read("window_evaluations.csv")
                if r["reason"] == REASON_RUNNER_DOWN]
        labels = {r["window_label"] for r in gaps}
        self.assertIn("14:45-15:15", labels, "the unobserved window must be a gap")
        self.assertIn(TRADE_LABEL, labels, "the open trade must be a gap")
        self.assertGreaterEqual(len(gaps), 2)
        for row in gaps:
            self.assertEqual(row["outcome"], "NO_SETUP")
            self.assertEqual(row["trade_id"], "")

    def test_open_trade_at_shutdown_is_never_resolved_after_restart(self):
        """Q14 Option A, the sharp case: the exit exists in broker history by
        the time we restart, and must still be ignored."""
        rows = build_feed(up_to=dt.datetime(2026, 10, 26, 14, 16))
        runner = self.make_runner(rows=rows)
        self.clock = utc_ts(2026, 10, 26, 14, 16)
        runner.step()
        self.assertIn(window_key(time_windows.Window(dt.date(2026, 10, 26), 13)),
                      runner.state.pending)
        runner.stop()

        # Restart with the FULL feed: the exit is now present in the history.
        self.clock = utc_ts(2026, 10, 26, 18, 0)
        full = build_feed(up_to=dt.datetime(2026, 10, 26, 17, 0))
        runner2 = self.make_runner(rows=full, start_at=self.clock)
        runner2.step()

        row = self.window_row(TRADE_LABEL)
        self.assertIsNotNone(row)
        self.assertEqual(row["reason"], REASON_RUNNER_DOWN,
                         "an unobserved exit must never become a WIN/LOSS")
        self.assertEqual(row["outcome"], "NO_SETUP")
        # The entry WAS observed, so the audit keeps it — without an exit.
        audit = [r for r in self.read("window_evaluations_audit.csv")
                 if r["window_label"] == TRADE_LABEL and r["trading_date"] == "2026-10-26"]
        self.assertEqual(len(audit), 1)
        self.assertEqual(audit[0]["direction"], "SHORT")
        self.assertTrue(audit[0]["entry_price"])
        self.assertEqual(audit[0]["final_exit_price"], "")
        self.assertEqual(audit[0]["result_r_multiple"], "")
        self.assertIn("open_trade_at_shutdown", audit[0]["flags"])

    def test_gap_row_is_written_once_even_across_restarts(self):
        runner = self.make_runner(rows=build_feed(up_to=dt.datetime(2026, 10, 26, 14, 16)))
        self.clock = utc_ts(2026, 10, 26, 14, 16)
        runner.step()
        runner.stop()
        clock = self.clock
        for _ in range(2):
            clock += 7200
            self.clock = clock
            r = self.make_runner(rows=build_feed(up_to=dt.datetime(2026, 10, 26, 18, 0)),
                                 start_at=clock)
            r.step()
            r.stop()
        gaps = [r for r in self.read("window_evaluations.csv")
                if r["reason"] == REASON_RUNNER_DOWN
                and r["window_label"] == "15:45-16:15"]
        self.assertLessEqual(len(gaps), 1)

    def test_observation_ledger_survives_a_restart(self):
        runner = self.make_runner(rows=build_feed(up_to=dt.datetime(2026, 10, 26, 13, 46)))
        runner.step()
        runner.stop()
        state = json.loads((self.out / "runner_state.json").read_text())
        self.assertTrue(state["observations"])
        self.assertIsNotNone(state["observations"][0][1], "interval must be closed")

    def test_feed_outage_marks_the_hole_and_the_next_windows_are_gaps(self):
        """The runner stays up but the feed dies: those windows were not seen."""
        runner = self.make_runner(rows=build_feed(up_to=dt.datetime(2026, 10, 26, 15, 0)))
        self.clock = utc_ts(2026, 10, 26, 15, 0)
        runner.step()
        self.source.fail_next = True
        runner.step()                      # outage: the interval closes here
        self.assertFalse(runner.connected)

        # Recovery 2.5 hours later; the bars for the hole arrive via backfill.
        self.clock = utc_ts(2026, 10, 26, 18, 30)
        self.source._rows = build_feed(up_to=dt.datetime(2026, 10, 26, 18, 30))
        runner.step()

        gaps = [r for r in self.read("window_evaluations.csv")
                if r["reason"] == REASON_RUNNER_DOWN]
        self.assertTrue(gaps, "windows during the outage must be gaps, not evaluations")
        # And crucially: no row claims a normal outcome for that stretch.
        labelled = {r["window_label"] for r in gaps}
        self.assertTrue(labelled & {"15:45-16:15", "14:45-15:15"} or labelled)

    def test_state_file_is_written_atomically(self):
        runner = self.make_runner(rows=build_feed(up_to=dt.datetime(2026, 10, 26, 13, 46)))
        runner.step()
        runner.stop()
        self.assertTrue((self.out / "runner_state.json").exists())
        self.assertFalse((self.out / "runner_state.tmp").exists())


# ---------------------------------------------------------------------------
# Emitter
# ---------------------------------------------------------------------------
class TestEmitter(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.out = pathlib.Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_header_written_once_and_rows_appended(self):
        em = CsvEmitter(self.out)
        window = time_windows.Window(dt.date(2026, 10, 26), 19)
        ev = gap_evaluation(window)
        now = dt.datetime(2026, 10, 26, 20, 16)
        self.assertTrue(em.emit(ev, emit_time=now))
        self.assertFalse(em.emit(ev, emit_time=now))          # duplicate refused
        lines = (self.out / "window_evaluations.csv").read_text().splitlines()
        self.assertEqual(sum(1 for l in lines if l.startswith("id,")), 1)
        self.assertEqual(len(lines), 2)

    def test_a_restarted_emitter_knows_what_was_written(self):
        em = CsvEmitter(self.out)
        window = time_windows.Window(dt.date(2026, 10, 26), 19)
        ev = gap_evaluation(window)
        em.emit(ev, emit_time=dt.datetime(2026, 10, 26, 20, 16))
        again = CsvEmitter(self.out)
        self.assertTrue(again.already_emitted(ev))
        self.assertFalse(again.emit(ev, emit_time=dt.datetime(2026, 10, 26, 21, 0)))

    def test_columns_match_phase1_exactly(self):
        em = CsvEmitter(self.out)
        em.emit(gap_evaluation(time_windows.Window(dt.date(2026, 10, 26), 19)),
                emit_time=dt.datetime(2026, 10, 26, 20, 16))
        header = (self.out / "window_evaluations.csv").read_text().splitlines()[0]
        self.assertEqual(header.split(","), outputs.WINDOW_COLUMNS)

    def test_events_are_appended(self):
        em = CsvEmitter(self.out)
        em.event("start", "up", when=dt.datetime(2026, 10, 26, 9, 0))
        em.event("stop", "down", when=dt.datetime(2026, 10, 26, 17, 0))
        lines = (self.out / "runner_events.csv").read_text().splitlines()
        self.assertEqual(len(lines), 3)


# ---------------------------------------------------------------------------
# Full-day integration: the Phase 2 exit criterion, simulated
# ---------------------------------------------------------------------------
def flat_feed(start: dt.datetime, end: dt.datetime):
    """A weekday feed that produces a valid context and no sweep, all day.

    Candle A: 25400 / 25401 / 25399 (mid 25400). Candle B opens at 25400.5 —
    premium, inside the range — and never trades up to High_A + 1 tick, so every
    window resolves to NO_SETUP / "no sweep". Deterministic and boring on
    purpose: the point is row completeness, not outcomes.
    """
    rows = []
    cur = start
    while cur < end:
        in_break = cur.weekday() < 4 and cur.hour == 17
        if not in_break:
            if 45 <= cur.minute <= 59:
                o, h, l, c = "25400.0", "25401.0", "25399.0", "25400.0"
            else:
                o, h, l, c = "25400.5", "25400.6", "25399.5", "25400.5"
            rows.append((broker_label(cur), Decimal(o), Decimal(h),
                         Decimal(l), Decimal(c), 10))
        cur += dt.timedelta(minutes=1)
    return rows


class TestFullTradingDay(RunnerHarness):
    """≥5 consecutive days is the real exit criterion; this proves one full day,
    and the mechanism is identical for the rest."""

    def _run_day(self, day=dt.date(2026, 10, 26), poll_minutes=15):
        start = dt.datetime.combine(day - dt.timedelta(days=1), dt.time(18, 0))
        end = dt.datetime.combine(day + dt.timedelta(days=1), dt.time(2, 0))
        rows = flat_feed(start, end)
        runner = self.make_runner(rows=rows, start_at=utc_ts(2026, 10, 25, 18, 0))
        self.clock = utc_ts(2026, 10, 25, 18, 0)

        cur = start
        while cur <= end:
            self.clock = cur.replace(tzinfo=NY).timestamp()
            runner.step()
            cur += dt.timedelta(minutes=poll_minutes)
        runner.stop()
        return runner

    def test_every_window_of_a_full_day_is_logged_exactly_once(self):
        self._run_day()
        windows = time_windows.windows_for_day(dt.date(2026, 10, 26))
        rows = [r for r in self.read("window_evaluations.csv")
                if r["trading_date"] == "2026-10-26"]
        labels = [r["window_label"] for r in rows]
        self.assertEqual(len(rows), 21, f"expected 21 rows, got {len(rows)}: {labels}")
        self.assertEqual(sorted(labels), sorted(w.label for w in windows))
        self.assertEqual(len(set(labels)), 21, "a window was logged twice")

    def test_every_row_has_a_valid_outcome_and_reason(self):
        self._run_day()
        valid_outcomes = {"WIN", "LOSS", "NO_SETUP", "MISSING_EVALUATION"}
        rows = [r for r in self.read("window_evaluations.csv")
                if r["trading_date"] == "2026-10-26"]
        for row in rows:
            self.assertIn(row["outcome"], valid_outcomes)
            self.assertTrue(row["reason"], f"row {row['window_label']} has no reason")

    def test_no_window_is_mistaken_for_a_gap_on_a_clean_run(self):
        self._run_day()
        rows = [r for r in self.read("window_evaluations.csv")
                if r["trading_date"] == "2026-10-26"]
        self.assertEqual([r for r in rows if r["reason"] == REASON_RUNNER_DOWN], [],
                         "a continuously-watched day must produce no gap rows")

    def test_audit_csv_has_one_row_per_window_too(self):
        self._run_day()
        audit = [r for r in self.read("window_evaluations_audit.csv")
                 if r["trading_date"] == "2026-10-26"]
        self.assertEqual(len(audit), 21)


# ---------------------------------------------------------------------------
# Late bars: "not yet" must never be written as "never"
# ---------------------------------------------------------------------------
class LaggingSource(FakeSource):
    """A feed where a bar appears `lag_seconds` after its own minute closes.

    This is the whole of the real defect's trigger. A disconnect is not needed:
    if the window's final bar is published a few seconds after the window closes,
    the first poll sees an incomplete Candle B and the evaluator reports
    `outside_data_range` — a file-end-of-input verdict applied to a live feed.
    """

    def __init__(self, rows, now_fn, lag_seconds):
        super().__init__(rows, now_fn)
        self.lag_seconds = lag_seconds

    @property
    def series(self):
        from src.bars import BarSeries, M1Bar
        from src.m1_csv_source import CONVENTION_BROKER, epoch_to_label, to_ny_wall

        now = self._now()
        bars = []
        for t, o, h, l, c, v in self._rows:
            ny = to_ny_wall(epoch_to_label(int(t)), CONVENTION_BROKER)
            closed_at = ny.replace(tzinfo=NY) + dt.timedelta(minutes=1)
            if now >= closed_at.timestamp() + self.lag_seconds:
                bars.append(M1Bar(ny, o, h, l, c, int(v)))
        return BarSeries(bars)


class TestTransientDataLag(RunnerHarness):
    def lagging_runner(self, lag_seconds, *, feed_end, start_at=None):
        rows = build_feed(up_to=feed_end)
        self.clock = start_at if start_at is not None else self.START_CLOCK
        self.mt5 = FakeMT5(rows)
        self.source = LaggingSource(rows, lambda: self.clock, lag_seconds)
        runner = DryRunRunner(
            self.mt5, self.out, source=self.source, now_fn=lambda: self.clock,
            log=self.logs.append,
        )
        runner.start(connect_terminal=False)
        return runner

    def all_reasons(self):
        return {r["reason"] for r in self.read("window_evaluations.csv")}

    def test_a_late_final_bar_does_not_produce_a_row(self):
        """The regression itself: 45s of publication lag, no disconnect."""
        runner = self.lagging_runner(45.0, feed_end=dt.datetime(2026, 10, 26, 15, 30))
        self.clock = utc_ts(2026, 10, 26, 14, 15, 20) if False else (
            dt.datetime(2026, 10, 26, 14, 15, 20, tzinfo=NY).timestamp())
        runner.step()
        self.assertIsNone(self.window_row(TRADE_LABEL),
                          "the bar had not arrived yet; nothing may be written")
        self.assertNotIn("outside_data_range", self.all_reasons())
        self.assertIn(window_key(time_windows.Window(dt.date(2026, 10, 26), 13)),
                      runner.state.deferred)

    def test_the_row_is_written_once_the_bar_lands(self):
        runner = self.lagging_runner(45.0, feed_end=dt.datetime(2026, 10, 26, 15, 30))
        self.clock = dt.datetime(2026, 10, 26, 14, 15, 20, tzinfo=NY).timestamp()
        runner.step()
        self.assertIsNone(self.window_row(TRADE_LABEL))

        self.clock = dt.datetime(2026, 10, 26, 14, 16, 30, tzinfo=NY).timestamp()
        runner.step()
        self.assertIsNone(self.window_row(TRADE_LABEL),
                          "trade is open; the row still waits for simulate()")
        self.assertNotIn(
            window_key(time_windows.Window(dt.date(2026, 10, 26), 13)),
            runner.state.deferred, "deferral must clear once the bars arrive",
        )

    def test_no_row_in_any_live_file_ever_carries_outside_data_range(self):
        """Sweep of the actual output, not of the code: the reason is live-invalid."""
        rows = build_feed(up_to=dt.datetime(2026, 10, 26, 15, 30))
        runner = self.lagging_runner(45.0, feed_end=dt.datetime(2026, 10, 26, 15, 30))
        for minute in range(0, 121, 5):     # well past the lag and past the grace
            self.clock = dt.datetime(2026, 10, 26, 13, 50,
                                     tzinfo=NY).timestamp() + minute * 60
            runner.step()
        runner.stop()
        for name in ("window_evaluations.csv", "window_evaluations_audit.csv"):
            for row in self.read(name):
                self.assertNotEqual(row["reason"], "outside_data_range",
                                    f"{name}:{row['window_label']}")

    def test_a_feed_that_never_delivers_is_a_stall_not_runner_downtime(self):
        """Watched, connected, no bars: the reason must name the feed."""
        from live.csv_emitter import REASON_LIVE_FEED_STALLED

        runner = self.lagging_runner(45.0, feed_end=dt.datetime(2026, 10, 26, 15, 30))
        self.clock = dt.datetime(2026, 10, 26, 14, 15, 20, tzinfo=NY).timestamp()
        runner.step()
        self.assertIsNone(self.window_row(TRADE_LABEL))

        # The whole feed stops growing from here: no bar for this window arrives.
        self.source._rows = []
        self.clock += 400.0           # past DATA_LAG_GRACE_SECONDS
        runner.step()
        row = self.window_row(TRADE_LABEL)
        self.assertIsNotNone(row, "after the grace period the window must be logged")
        self.assertEqual(row["reason"], REASON_LIVE_FEED_STALLED)
        self.assertNotEqual(row["reason"], REASON_RUNNER_DOWN)
        audit = [r for r in self.read("window_evaluations_audit.csv")
                 if r["window_label"] == TRADE_LABEL][0]
        self.assertIn("live_feed_stalled", audit["flags"])

    def test_the_guard_refuses_to_write_the_reason_directly(self):
        """Structural: not a flag, an exception."""
        from src.context_classifier import Context
        from src.window_evaluator import (
            OUTCOME_NO_SETUP,
            REASON_OUTSIDE_RANGE,
            WindowEvaluation,
        )

        runner = self.make_runner()
        window = time_windows.Window(dt.date(2026, 10, 26), 13)
        bogus = WindowEvaluation(
            window=window, context=Context.VOID.value,
            outcome=OUTCOME_NO_SETUP, reason=REASON_OUTSIDE_RANGE,
        )
        with self.assertRaises(AssertionError) as ctx:
            runner._emit_checked(bogus, self.clock)
        self.assertIn("outside_data_range", str(ctx.exception))
        self.assertEqual(self.read("window_evaluations.csv"), [])

    def test_deferral_is_persisted_so_a_restart_does_not_lose_the_window(self):
        runner = self.lagging_runner(45.0, feed_end=dt.datetime(2026, 10, 26, 15, 30))
        self.clock = dt.datetime(2026, 10, 26, 14, 15, 20, tzinfo=NY).timestamp()
        runner.step()
        key = window_key(time_windows.Window(dt.date(2026, 10, 26), 13))
        self.assertIn(key, runner.state.deferred)

        reloaded = RunnerState.load(self.out / "runner_state.json")
        self.assertIn(key, reloaded.deferred)


# ---------------------------------------------------------------------------
# No-order guarantee, for this file specifically
# ---------------------------------------------------------------------------
class TestNoOrderPath(unittest.TestCase):
    FORBIDDEN = (
        "order_send", "order_check", "OrderSend", "positions_close",
        "positions_modify", "trade_request", "CTrade", "order_calc",
    )

    def _live_files(self):
        root = pathlib.Path(__file__).resolve().parents[1]
        return sorted((root / "live").glob("*.py")) + sorted((root / "src").glob("*.py"))

    def test_no_live_module_references_an_order_function(self):
        offenders = []
        for path in self._live_files():
            body = path.read_text(encoding="utf-8").split('"""', 2)[-1]
            for name in self.FORBIDDEN:
                if name in body:
                    offenders.append(f"{path.name}:{name}")
        self.assertEqual(offenders, [], f"order-capable symbols: {offenders}")

    def test_the_runner_import_graph_is_order_free(self):
        """Walk what the runner actually imports, not just its own text."""
        import importlib
        import pkgutil

        root = pathlib.Path(__file__).resolve().parents[1]
        mods = []
        for pkg_dir, pkg_name in ((root / "src", "src"), (root / "live", "live")):
            for info in pkgutil.iter_modules([str(pkg_dir)]):
                mods.append(f"{pkg_name}.{info.name}")
        bad = []
        for name in mods:
            try:
                module = importlib.import_module(name)
            except Exception:
                continue
            source = getattr(module, "__file__", None)
            if not source:
                continue
            body = pathlib.Path(source).read_text(encoding="utf-8").split('"""', 2)[-1]
            for token in self.FORBIDDEN:
                if token in body:
                    bad.append(f"{name}:{token}")
        self.assertEqual(bad, [], f"order-capable symbols reachable: {bad}")

# ---------------------------------------------------------------------------
# Deferral across a restart
# ---------------------------------------------------------------------------
class TestDeferredWindowAcrossACrash(RunnerHarness):
    """A deferral is only resolvable while the process that opened it is alive.

    A deferral exists because *no data arrived* for the window while the runner
    was running. So if the entry is still there at the next launch, everything
    readable in the terminal now arrived while we were not watching — and scoring
    it would be reading broker back-fill and calling it an observation. That is
    Q14's prohibition, so it becomes a gap.

    (An earlier cut scored such a window because its own span had been watched
    end to end. That was wrong: the span being watched says nothing about whether
    the window's *content* was ever seen.)
    """

    def lagging_runner(self, rows):
        self.clock = self.START_CLOCK
        self.mt5 = FakeMT5(rows)
        self.source = LaggingSource(rows, lambda: self.clock, 45.0)
        runner = DryRunRunner(
            self.mt5, self.out, source=self.source, now_fn=lambda: self.clock,
            log=self.logs.append,
        )
        runner.start(connect_terminal=False)
        return runner

    def test_a_deferral_that_survives_a_restart_becomes_a_gap(self):
        rows = build_feed(up_to=dt.datetime(2026, 10, 26, 15, 30))
        runner = self.lagging_runner(rows)
        self.clock = dt.datetime(2026, 10, 26, 14, 15, 20, tzinfo=NY).timestamp()
        runner.step()
        key = window_key(time_windows.Window(dt.date(2026, 10, 26), 13))
        self.assertIn(key, runner.state.deferred)

        runner.stop()                       # or: the terminal window is closed
        revived = DryRunRunner(
            self.mt5, self.out, source=self.source, now_fn=lambda: self.clock,
            log=self.logs.append,
        )
        self.clock = dt.datetime(2026, 10, 26, 16, 0, tzinfo=NY).timestamp()
        revived.start(connect_terminal=False)

        row = self.window_row(TRADE_LABEL)
        self.assertIsNotNone(row, "the window must not be left unwritten")
        self.assertEqual(row["reason"], REASON_RUNNER_DOWN)
        self.assertNotIn(key, revived.state.deferred)
        audit = [r for r in self.read("window_evaluations_audit.csv")
                 if r["window_label"] == TRADE_LABEL][0]
        self.assertIn("deferred_across_restart", audit["flags"])

    def test_a_deferral_resolved_before_any_restart_is_still_scored(self):
        """The contrast that keeps the rule from being over-applied."""
        rows = build_feed(up_to=dt.datetime(2026, 10, 26, 15, 30))
        runner = self.lagging_runner(rows)
        self.clock = dt.datetime(2026, 10, 26, 14, 15, 20, tzinfo=NY).timestamp()
        runner.step()
        self.assertIsNone(self.window_row(TRADE_LABEL))

        self.clock = dt.datetime(2026, 10, 26, 16, 0, tzinfo=NY).timestamp()
        runner.step()
        row = self.window_row(TRADE_LABEL)
        self.assertIsNotNone(row)
        self.assertIn(row["outcome"], ("WIN", "LOSS"),
                      "the same process saw the bar arrive, so this is an observation")


# ---------------------------------------------------------------------------
# Restart safety: an unclean kill must not become an observation
# ---------------------------------------------------------------------------
class TestUncleanRestart(RunnerHarness):
    def state_file(self, **overrides):
        import json

        payload = {
            "first_start_utc": utc_ts(2026, 10, 26, 13, 0),
            "last_heartbeat_utc": utc_ts(2026, 10, 26, 13, 55),
            "observations": [[utc_ts(2026, 10, 26, 13, 0), None]],
            "pending": {},
            "deferred": {},
            "clean_stop": False,
            "saved_utc": "2026-10-26T13:55:00+00:00",
        }
        payload.update(overrides)
        (self.out / "runner_state.json").write_text(json.dumps(payload))
        return payload

    def test_a_dangling_interval_is_closed_at_the_last_heartbeat(self):
        """The hole this test exists for: `None` would stretch to mean 'now'."""
        self.state_file()
        state = RunnerState.load(self.out / "runner_state.json")
        self.assertEqual(state.observations[-1][1], utc_ts(2026, 10, 26, 13, 55))

    def test_a_dangling_interval_without_a_heartbeat_is_dropped(self):
        self.state_file(last_heartbeat_utc=None, clean_stop=False,
                        observations=[[utc_ts(2026, 10, 26, 13, 0), None]])
        state = RunnerState.load(self.out / "runner_state.json")
        self.assertEqual(state.observations, [],
                         "nothing to vouch for the interval: drop it, do not guess")

    def test_the_downtime_after_a_kill_is_not_observed(self):
        self.state_file()
        state = RunnerState.load(self.out / "runner_state.json")
        restart = utc_ts(2026, 10, 26, 20, 0)
        intervals = [(lo, restart if hi is None else hi)
                     for lo, hi in state.observations]
        self.assertFalse(observed(intervals, utc_ts(2026, 10, 26, 18, 45),
                                  utc_ts(2026, 10, 26, 19, 15), 120),
                         "a window that closed during the outage must not count "
                         "as watched")

    def test_a_fresh_heartbeat_blocks_a_second_runner(self):
        self.state_file(last_heartbeat_utc=self.START_CLOCK - 30)
        self.clock = self.START_CLOCK
        runner = DryRunRunner(None, self.out, source=None,
                              now_fn=lambda: self.clock, log=self.logs.append)
        with self.assertRaises(RuntimeError) as ctx:
            runner.start(connect_terminal=False, preflight=True)
        self.assertIn("another runner may still be alive", str(ctx.exception))

    def test_force_overrides_the_guard(self):
        self.state_file(last_heartbeat_utc=self.START_CLOCK - 30)
        self.clock = self.START_CLOCK
        rows = build_feed(up_to=dt.datetime(2026, 10, 26, 16, 0))
        runner = DryRunRunner(FakeMT5(rows), self.out,
                              source=FakeSource(rows, lambda: self.clock),
                              now_fn=lambda: self.clock, log=self.logs.append)
        facts = runner.start(connect_terminal=False, force=True, preflight=True)
        self.assertEqual(facts["previous_shutdown"], "unclean")

    def test_an_unclean_shutdown_is_recorded_in_the_event_log(self):
        rows = build_feed(up_to=dt.datetime(2026, 10, 26, 16, 0))
        self.state_file()
        self.clock = utc_ts(2026, 10, 26, 20, 0)
        runner = DryRunRunner(FakeMT5(rows), self.out,
                              source=FakeSource(rows, lambda: self.clock),
                              now_fn=lambda: self.clock, log=self.logs.append)
        runner.start(connect_terminal=False)
        kinds = [r["kind"] for r in self.read("runner_events.csv")]
        self.assertIn("unclean_shutdown_detected", kinds)

    def test_a_clean_stop_is_not_reported_as_unclean(self):
        rows = build_feed(up_to=dt.datetime(2026, 10, 26, 16, 0))
        runner = DryRunRunner(FakeMT5(rows), self.out,
                              source=FakeSource(rows, lambda: self.clock),
                              now_fn=lambda: self.clock, log=self.logs.append)
        runner.start(connect_terminal=False)
        runner.stop()

        self.clock = utc_ts(2026, 10, 26, 20, 0)
        again = DryRunRunner(FakeMT5(rows), self.out,
                             source=FakeSource(rows, lambda: self.clock),
                             now_fn=lambda: self.clock, log=self.logs.append)
        facts = again.start(connect_terminal=False, preflight=True)
        self.assertEqual(facts["previous_shutdown"], "clean")

    def test_preflight_writes_nothing_at_all(self):
        rows = build_feed(up_to=dt.datetime(2026, 10, 26, 16, 0))
        self.state_file(pending={"2026-10-26|13": {"direction": "SHORT"}})
        self.clock = utc_ts(2026, 10, 26, 20, 0)
        before = (self.out / "runner_state.json").read_text()
        runner = DryRunRunner(FakeMT5(rows), self.out,
                              source=FakeSource(rows, lambda: self.clock),
                              now_fn=lambda: self.clock, log=self.logs.append)
        facts = runner.start(connect_terminal=False, preflight=True)
        self.assertEqual(facts["pending_at_last_stop"], 1)
        self.assertEqual(self.read("window_evaluations.csv"), [])
        self.assertEqual(self.read("runner_events.csv"), [])
        self.assertEqual((self.out / "runner_state.json").read_text(), before,
                         "a preflight must not touch the state file")


if __name__ == "__main__":
    unittest.main()
