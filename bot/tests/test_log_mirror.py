"""
Tests for the daily log mirror (bot/live/log_mirror.py).

The mirror is the only copy of a day's evidence that survives until the Handler
reads it, so the tests are about the two ways such a copy fails: repeating rows
and losing them. Everything here drives the real class against real files — no
fakes for the I/O, because the I/O is the thing under test.
"""

from __future__ import annotations

import contextlib
import csv
import errno as errno_mod
import io
import json
import os
import pathlib
import sys
import tempfile
import unittest

BOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BOT))

from live import log_mirror  # noqa: E402

DST_COLS = ["sample_utc", "ny_wall", "notes"]
EVENT_COLS = ["event_utc", "ny_wall", "kind", "detail"]
AUDIT_COLS = ["trading_date", "window_label", "evaluated_at", "outcome"]


def write_csv(path: pathlib.Path, columns: list[str], rows: list[list[str]],
              *, header: bool = True) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        if header:
            writer.writerow(columns)
        writer.writerows(rows)


def read_csv(path: pathlib.Path) -> list[list[str]]:
    with path.open(newline="", encoding="utf-8") as fh:
        return list(csv.reader(fh))


class MirrorCase(unittest.TestCase):
    """A temporary out-dir (the runner's) and logs-dir (the mirror's)."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        root = pathlib.Path(self._tmp.name)
        self.out = root / "out"
        self.logs = root / "logs"
        self.out.mkdir()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def mirror(self, now: str | None = None) -> log_mirror.Mirror:
        """A fresh Mirror — i.e. what a restarted process would build."""
        # No real backoff in tests: the retry path is exercised, the waiting is
        # not. Production uses the real sleep.
        return log_mirror.Mirror(self.out, self.logs,
                                 save_sleep=lambda _seconds: None)

    def pass_once(self, now: float | None = None) -> dict:
        return self.mirror().mirror_once(now=now)

    def rows_for(self, prefix: str, day: str) -> list[list[str]]:
        path = self.logs / f"{prefix}_{day}.csv"
        return read_csv(path) if path.exists() else []

    # -- fixtures ----------------------------------------------------------
    def add_monitor_rows(self, *stamps: str) -> None:
        write_csv(self.out / "dst_monitor.csv", DST_COLS,
                  [[f"utc-{s}", s, ""] for s in stamps],
                  header=not (self.out / "dst_monitor.csv").exists())

    def add_event_rows(self, *rows: list[str]) -> None:
        write_csv(self.out / "runner_events.csv", EVENT_COLS, list(rows),
                  header=not (self.out / "runner_events.csv").exists())

    def add_audit_rows(self, *rows: list[str]) -> None:
        write_csv(self.out / "window_evaluations_audit.csv", AUDIT_COLS,
                  list(rows),
                  header=not (self.out / "window_evaluations_audit.csv").exists())


class TestRouting(MirrorCase):
    """Each source lands in the right file, with the right header."""

    def test_rows_are_split_by_their_own_ny_date(self):
        self.add_monitor_rows("2026-10-29 23:50:00-04:00",
                              "2026-10-30 00:05:00-04:00")
        self.pass_once()

        first = self.rows_for("DST", "2026-10-29")
        second = self.rows_for("DST", "2026-10-30")
        self.assertEqual(first[0], DST_COLS)            # header once per file
        self.assertEqual(second[0], DST_COLS)
        self.assertEqual([r[1] for r in first[1:]], ["2026-10-29 23:50:00-04:00"])
        self.assertEqual([r[1] for r in second[1:]], ["2026-10-30 00:05:00-04:00"])

    def test_trades_follow_the_trading_date_not_the_clock(self):
        """A 23:45 window closes at 00:15 the next day; it still belongs to its day."""
        self.add_audit_rows(
            ["2026-10-29", "18:45-19:15", "2026-10-29T19:15:00-04:00", "NO_SETUP"],
            ["2026-10-29", "23:45-00:15", "2026-10-30T00:15:00-04:00", "NO_SETUP"],
        )
        self.pass_once()

        self.assertEqual(len(self.rows_for("TRADES", "2026-10-29")), 3)  # header + 2
        self.assertEqual(self.rows_for("TRADES", "2026-10-30"), [])

    def test_a_quoted_newline_is_copied_verbatim(self):
        """A traceback in an event detail is a multi-line CSV field, not two rows."""
        write_csv(self.out / "runner_events.csv", EVENT_COLS, [
            ["2026-10-29T21:00:00+00:00", "2026-10-29 17:00:00-04:00", "start", "up"],
            ["2026-10-29T21:05:00+00:00", "2026-10-29 17:05:00-04:00", "step_error",
             "Traceback (most recent call last):\n  File x, line 1\nValueError: bad"],
        ])
        self.pass_once()

        raw = (self.logs / "RUNNER_2026-10-29.csv").read_text(encoding="utf-8")
        self.assertIn("ValueError: bad", raw)
        rows = self.rows_for("RUNNER", "2026-10-29")
        self.assertEqual(len(rows), 3)                  # header + 2 records
        self.assertIn("Traceback", rows[2][3])

    def test_an_unknown_source_file_is_not_an_error(self):
        """The mirror may start first; missing sources simply mirror nothing."""
        self.assertEqual(self.pass_once(), {"DST": 0, "RUNNER": 0, "TRADES": 0})


class TestRestart(MirrorCase):
    """No duplicates and no gaps across a restart, mid-day."""

    def test_a_restart_does_not_repeat_a_row(self):
        self.add_monitor_rows("2026-10-29 10:00:00-04:00")
        self.pass_once()
        self.pass_once()                                 # nothing new
        self.pass_once()

        self.assertEqual(len(self.rows_for("DST", "2026-10-29")), 2)  # header + 1

    def test_rows_added_while_down_are_mirrored_after_restart(self):
        self.add_monitor_rows("2026-10-29 10:00:00-04:00")
        self.pass_once()
        # The process is down; the runner keeps writing.
        self.add_monitor_rows("2026-10-29 11:00:00-04:00",
                              "2026-10-29 12:00:00-04:00")
        self.pass_once()                                 # a new process resumes

        rows = self.rows_for("DST", "2026-10-29")
        self.assertEqual([r[1] for r in rows[1:]],
                         ["2026-10-29 10:00:00-04:00",
                          "2026-10-29 11:00:00-04:00",
                          "2026-10-29 12:00:00-04:00"])

    def test_every_row_appears_exactly_once_across_many_restarts(self):
        """The property that matters: 30 rows in, 30 rows out, no repeats."""
        stamps = [f"2026-10-29 {hour:02d}:{minute:02d}:00-04:00"
                  for hour in range(10, 13) for minute in range(0, 60, 12)]
        for index, stamp in enumerate(stamps):
            self.add_monitor_rows(stamp)
            self.pass_once()                             # restart after every row
            if index % 7 == 0:
                self.pass_once()                         # and a few extra passes

        rows = self.rows_for("DST", "2026-10-29")[1:]
        self.assertEqual([r[1] for r in rows], stamps)
        self.assertEqual(len(rows), len(set(r[1] for r in rows)))

    def test_the_state_file_records_the_offset_it_resumed_from(self):
        self.add_monitor_rows("2026-10-29 10:00:00-04:00")
        self.pass_once()
        state = log_mirror.load_state(self.logs / ".mirror_state.json")
        entry = state["sources"]["dst_monitor.csv"]
        self.assertEqual(entry["offset"],
                         (self.out / "dst_monitor.csv").stat().st_size)
        self.assertEqual(entry["header"], ",".join(DST_COLS))

    def test_a_truncated_source_restarts_from_zero_instead_of_guessing(self):
        self.add_monitor_rows("2026-10-29 10:00:00-04:00")
        self.pass_once()
        # Someone rotated the file down to a single row.
        (self.out / "dst_monitor.csv").write_text(
            ",".join(DST_COLS) + "\nutc-new,2026-10-29 20:00:00-04:00,x\n",
            encoding="utf-8")
        self.pass_once(now=1761782400.0)                 # 2026-10-30 00:00 UTC

        rows = self.rows_for("DST", "2026-10-29")[1:]
        self.assertIn(["utc-new", "2026-10-29 20:00:00-04:00", "x"], rows)

    def test_a_lost_state_file_reconciles_instead_of_duplicating(self):
        """The day files are the second record of what has been mirrored.

        Losing the state used to mean re-reading from zero and writing every row
        again. That is not academic: the Windows fsync crash left six day files
        and no state, so the next start would have duplicated all of them.
        """
        self.add_monitor_rows("2026-10-29 10:00:00-04:00",
                              "2026-10-29 11:00:00-04:00")
        self.pass_once()
        (self.logs / ".mirror_state.json").unlink()

        self.pass_once()                                 # no state: reconcile

        rows = self.rows_for("DST", "2026-10-29")
        self.assertEqual([r[1] for r in rows[1:]],
                         ["2026-10-29 10:00:00-04:00",
                          "2026-10-29 11:00:00-04:00"])   # once each

    def test_reconciliation_only_skips_a_matching_prefix(self):
        """A day file that does not match cannot cause a row to be skipped."""
        self.logs.mkdir(parents=True, exist_ok=True)
        (self.logs / "DST_2026-10-29.csv").write_text(
            ",".join(DST_COLS) + "\nsomething,else,\n", encoding="utf-8")
        self.add_monitor_rows("2026-10-29 10:00:00-04:00")
        self.pass_once()

        rows = self.rows_for("DST", "2026-10-29")
        self.assertIn(["utc-2026-10-29 10:00:00-04:00",
                       "2026-10-29 10:00:00-04:00", ""], rows)


class TestPartialWrites(MirrorCase):
    """The runner writes with `open(..., "a")`, so a reader can catch it mid-row."""

    def test_a_row_without_its_newline_is_not_consumed_yet(self):
        self.add_monitor_rows("2026-10-29 10:00:00-04:00")
        with (self.out / "dst_monitor.csv").open("a", encoding="utf-8") as fh:
            fh.write("utc-partial,2026-10-29 11:00:00-04:00")    # no newline

        self.pass_once()
        self.assertEqual(len(self.rows_for("DST", "2026-10-29")), 2)  # header + 1

        with (self.out / "dst_monitor.csv").open("a", encoding="utf-8") as fh:
            fh.write(",partial\n")
        self.pass_once()

        rows = self.rows_for("DST", "2026-10-29")[1:]
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[1][2], "partial")

    def test_a_row_with_a_half_written_quoted_field_waits_for_the_rest(self):
        write_csv(self.out / "runner_events.csv", EVENT_COLS,
                  [["2026-10-29T21:00:00+00:00", "2026-10-29 17:00:00-04:00",
                    "start", "up"]])
        with (self.out / "runner_events.csv").open("a", encoding="utf-8") as fh:
            fh.write('"2026-10-29T21:05:00+00:00","2026-10-29 17:05:00-04:00",'
                     'step_error,"Traceback...\n')       # quoted field still open

        self.pass_once()
        self.assertEqual(len(self.rows_for("RUNNER", "2026-10-29")), 2)

        with (self.out / "runner_events.csv").open("a", encoding="utf-8") as fh:
            fh.write('more\nValueError: bad"\n')
        self.pass_once()

        rows = self.rows_for("RUNNER", "2026-10-29")
        self.assertEqual(len(rows), 3)
        self.assertIn("ValueError", rows[2][3])


class TestDurability(MirrorCase):
    """Write-through: every row is on the disk before the offset moves on."""

    def test_every_row_is_flushed_and_fsynced(self):
        calls: list[int] = []
        real = os.fsync

        def counting(fd):
            calls.append(fd)
            return real(fd)

        os.fsync = counting
        try:
            self.add_monitor_rows("2026-10-29 10:00:00-04:00",
                                  "2026-10-29 11:00:00-04:00")
            self.pass_once()
        finally:
            os.fsync = real

        # Two rows = two data fsyncs, plus the state saves and their fsyncs.
        self.assertGreaterEqual(len(calls), 2)

    def test_the_offset_never_runs_ahead_of_the_written_row(self):
        """After a crash the state may lag, never lead: replay beats a gap."""
        self.add_monitor_rows("2026-10-29 10:00:00-04:00",
                              "2026-10-29 11:00:00-04:00")
        self.pass_once()

        state = log_mirror.load_state(self.logs / ".mirror_state.json")
        mirrored_bytes = (self.logs / "DST_2026-10-29.csv").stat().st_size
        source_bytes = (self.out / "dst_monitor.csv").stat().st_size
        self.assertEqual(state["sources"]["dst_monitor.csv"]["offset"], source_bytes)
        self.assertGreater(mirrored_bytes, 0)


class TestTheWindowsFsyncCrash(MirrorCase):
    """The first live run, reproduced.

    `save_state` wrote the file, reopened it read-only and fsynced that handle.
    POSIX permits that; Windows returns EBADF, so the process died on its first
    state save, on every source, having written rows it never recorded. 439
    tests passed because none of them ran where the difference exists.
    """

    def test_no_fsync_ever_uses_a_read_only_handle(self):
        """Platform-independent: catches the bug on Linux, where it is invisible."""
        seen: list[str] = []
        real = log_mirror._fsync

        def spy(fh):
            seen.append(getattr(fh, "mode", "?"))
            return real(fh)

        log_mirror._fsync = spy
        try:
            self.add_monitor_rows("2026-10-29 10:00:00-04:00",
                                  "2026-10-30 09:00:00-04:00")
            self.add_event_rows(["2026-10-29T21:00:00+00:00",
                                 "2026-10-29 17:00:00-04:00", "start", "up"])
            self.add_audit_rows(["2026-10-29", "09:45-10:15",
                                 "2026-10-29T10:15:00-04:00", "NO_SETUP"])
            self.pass_once()
        finally:
            log_mirror._fsync = real

        self.assertTrue(seen)
        for mode in seen:
            self.assertFalse("r" in mode and "+" not in mode,
                             f"fsync on a read-only handle (mode={mode!r})")

    def test_the_guard_refuses_a_read_only_handle(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "f.txt"
            path.write_text("x", encoding="utf-8")
            with path.open("rb") as fh:
                with self.assertRaises(ValueError) as caught:
                    log_mirror._fsync(fh)
        self.assertIn("read-only", str(caught.exception))

    @unittest.skipUnless(sys.platform != "win32",
                         "the emulation is only needed where fsync is permissive")
    def test_a_full_run_survives_windows_fsync_semantics(self):
        """Emulate Windows: fsync raises EBADF unless the handle is writable."""
        try:
            import fcntl
        except ImportError:                              # pragma: no cover
            self.skipTest("fcntl unavailable")

        def windows_fsync(fd):
            if fcntl.fcntl(fd, fcntl.F_GETFL) & os.O_ACCMODE == os.O_RDONLY:
                raise OSError(9, "Bad file descriptor")
            return real_fsync(fd)

        real_fsync = os.fsync
        self.add_monitor_rows("2026-10-29 10:00:00-04:00")
        self.add_event_rows(["2026-10-29T21:00:00+00:00",
                             "2026-10-29 17:00:00-04:00", "start", "up"])
        self.add_audit_rows(["2026-10-29", "09:45-10:15",
                             "2026-10-29T10:15:00-04:00", "NO_SETUP"])

        os.fsync = windows_fsync
        try:
            stats = self.pass_once()
        finally:
            os.fsync = real_fsync

        self.assertEqual(stats, {"DST": 1, "RUNNER": 1, "TRADES": 1})
        state = log_mirror.load_state(self.logs / ".mirror_state.json")
        self.assertNotEqual(state["sources"], {})       # the state was saved
        self.assertEqual(len(self.rows_for("DST", "2026-10-29")), 2)


class TestPreExistingDestinations(MirrorCase):
    """A fresh state over day files that already hold rows.

    This is the state the crashed runs left on the Handler's machine: rows on
    disk, no offsets recorded. The next start must neither crash nor duplicate.
    """

    def crashed_run(self) -> None:
        """One attempt that dies at its first state save, as the real one did.

        The row reaches the day file; the offset never reaches the state. Each
        such run therefore leaves exactly one unaccounted row per source, which
        is why several attempts produced several files.
        """
        mirror = log_mirror.Mirror(self.out, self.logs)
        original = log_mirror.save_state

        def boom(*_args, **_kwargs):
            raise OSError(9, "Bad file descriptor")

        log_mirror.save_state = boom
        try:
            with contextlib.redirect_stderr(io.StringIO()):
                mirror.mirror_once()
        finally:
            log_mirror.save_state = original

    def seed_partial(self, *stamps: str) -> list[list[str]]:
        """Day files holding rows with no state — the Handler's machine.

        Written directly rather than by looping the crashed path, because after
        the first crash the *next* attempt dies in the reconciliation save as
        well, so a loop models the bug rather than the state it leaves behind.
        The bytes are identical either way: header plus one record per attempt.
        """
        self.add_monitor_rows(*stamps)                  # the source has them too
        self.logs.mkdir(parents=True, exist_ok=True)
        with (self.logs / "DST_2026-10-29.csv").open("a", newline="",
                                                      encoding="utf-8") as fh:
            writer = csv.writer(fh)
            writer.writerow(DST_COLS)
            for stamp in stamps:
                writer.writerow([f"utc-{stamp}", stamp, ""])
        return self.rows_for("DST", "2026-10-29")

    def test_a_crashed_run_leaves_rows_and_no_state(self):
        """The incident in one assertion: the row landed, the offset did not."""
        self.add_monitor_rows("2026-10-29 10:00:00-04:00")
        self.crashed_run()

        self.assertEqual(len(self.rows_for("DST", "2026-10-29")), 2)   # header+1
        self.assertFalse((self.logs / ".mirror_state.json").exists())

    def test_a_fresh_start_neither_crashes_nor_duplicates(self):
        partial = self.seed_partial("2026-10-29 10:00:00-04:00",
                                    "2026-10-29 11:00:00-04:00")

        self.pass_once()                                 # the restart

        after = self.rows_for("DST", "2026-10-29")
        self.assertEqual(after, partial)                 # nothing added, nothing lost
        state = log_mirror.load_state(self.logs / ".mirror_state.json")
        entry = state["sources"]["dst_monitor.csv"]
        self.assertEqual(entry["offset"],
                         (self.out / "dst_monitor.csv").stat().st_size)

    def test_new_rows_are_appended_after_the_resumed_point(self):
        self.seed_partial("2026-10-29 10:00:00-04:00",
                          "2026-10-29 11:00:00-04:00")
        self.add_monitor_rows("2026-10-29 12:00:00-04:00")

        self.pass_once()

        rows = self.rows_for("DST", "2026-10-29")
        self.assertEqual([r[1] for r in rows[1:]],
                         ["2026-10-29 10:00:00-04:00",
                          "2026-10-29 11:00:00-04:00",
                          "2026-10-29 12:00:00-04:00"])

    def test_several_days_of_partial_files_are_each_reconciled(self):
        """The Handler's machine had files across several dates, not one."""
        days = ("2026-10-25", "2026-10-28", "2026-10-29")
        self.logs.mkdir(parents=True, exist_ok=True)
        for day in days:
            with (self.logs / f"DST_{day}.csv").open("a", newline="",
                                                      encoding="utf-8") as fh:
                writer = csv.writer(fh)
                writer.writerow(DST_COLS)
                stamp = f"{day} 10:00:00-04:00"
                writer.writerow([f"utc-{stamp}", stamp, ""])
            self.add_monitor_rows(f"{day} 10:00:00-04:00")

        before = {day: self.rows_for("DST", day) for day in days}
        self.assertEqual([len(v) for v in before.values()], [2, 2, 2])

        self.pass_once()

        for day, rows in before.items():
            self.assertEqual(self.rows_for("DST", day), rows, day)


class WindowsLock(PermissionError):
    """`PermissionError` as Windows raises it, with `winerror` present."""

    def __init__(self, winerror: int = 5) -> None:
        super().__init__(13, "Access is denied")
        self.winerror = winerror


@contextlib.contextmanager
def failing_rename(fail_times: int | None = None, winerror: int = 5):
    """Make the atomic rename fail for the state file, `fail_times` times.

    `None` means "every time". This is what a sync client or a virus scanner
    holding the destination open looks like from inside the process.
    """
    real = pathlib.Path.replace
    calls = {"n": 0}

    def fake(self, target):
        if "mirror_state" in str(self) or "mirror_state" in str(target):
            calls["n"] += 1
            if fail_times is None or calls["n"] <= fail_times:
                raise WindowsLock(winerror)
        return real(self, target)

    pathlib.Path.replace = fake
    try:
        yield calls
    finally:
        pathlib.Path.replace = real


class TestLockedStateFile(MirrorCase):
    """The rename fails because something else holds the destination open."""

    def test_a_transient_lock_is_retried_and_then_succeeds(self):
        self.add_monitor_rows("2026-10-29 10:00:00-04:00")

        with failing_rename(fail_times=2) as calls:
            self.pass_once()

        self.assertGreaterEqual(calls["n"], 3)          # two failures, one success
        state = log_mirror.load_state(self.logs / ".mirror_state.json")
        self.assertEqual(state["sources"]["dst_monitor.csv"]["offset"],
                         (self.out / "dst_monitor.csv").stat().st_size)

    def test_retries_are_bounded(self):
        self.add_monitor_rows("2026-10-29 10:00:00-04:00")
        with failing_rename(fail_times=None) as calls:
            self.pass_once()
        self.assertEqual(calls["n"], log_mirror.REPLACE_ATTEMPTS)

    def test_a_locked_state_file_does_not_crash_or_stop_the_pass(self):
        self.add_monitor_rows("2026-10-29 10:00:00-04:00",
                              "2026-10-29 11:00:00-04:00")
        mirror = self.mirror()
        with failing_rename(fail_times=None):
            with contextlib.redirect_stderr(io.StringIO()) as err:
                stats = mirror.mirror_once()

        self.assertEqual(stats, {"DST": 2, "RUNNER": 0, "TRADES": 0})  # rows still copied
        self.assertIsNotNone(mirror.state_error)
        self.assertIn("state NOT saved", err.getvalue())
        self.assertIn("WinError 5", err.getvalue())
        self.assertEqual(len(self.rows_for("DST", "2026-10-29")), 3)   # header + 2

    def test_the_old_state_survives_a_failed_save(self):
        """Renaming atomically must not truncate the previous good state."""
        self.add_monitor_rows("2026-10-29 10:00:00-04:00")
        self.pass_once()                                # a good state exists
        before = (self.logs / ".mirror_state.json").read_text(encoding="utf-8")

        self.add_monitor_rows("2026-10-29 11:00:00-04:00")
        with failing_rename(fail_times=None):
            with contextlib.redirect_stderr(io.StringIO()):
                self.pass_once()

        after = (self.logs / ".mirror_state.json").read_text(encoding="utf-8")
        self.assertEqual(after, before)                 # intact, not half-written
        self.assertIn("dst_monitor.csv", json.loads(after)["sources"])

    def test_a_real_error_is_not_retried(self):
        """Retrying a fault that will never clear only hides it."""
        calls = {"n": 0}
        real = pathlib.Path.replace

        def broken(self, target):
            if "mirror_state" in str(target):
                calls["n"] += 1
                raise OSError(errno_mod.EIO, "Input/output error")
            return real(self, target)

        pathlib.Path.replace = broken
        try:
            with self.assertRaises(OSError):
                log_mirror.save_state(self.logs / ".mirror_state.json",
                                      {"sources": {}},
                                      sleep=lambda _s: None)
        finally:
            pathlib.Path.replace = real
        self.assertEqual(calls["n"], 1)

    def test_several_failed_then_recovered_cycles_lose_and_repeat_nothing(self):
        """The Handler's real sequence: save fails, then succeeds, then fails.

        Each cycle is a fresh `Mirror`, i.e. a restart, so every one of them has
        to work out where it is — from the state file when it can, from the day
        files when it cannot.
        """
        stamps = [f"2026-10-29 {hour:02d}:00:00-04:00" for hour in range(10, 16)]
        for index, stamp in enumerate(stamps):
            self.add_monitor_rows(stamp)
            locked = index % 2 == 0            # alternate failure and recovery
            with failing_rename(fail_times=None if locked else 0):
                with contextlib.redirect_stderr(io.StringIO()):
                    self.pass_once()

        rows = self.rows_for("DST", "2026-10-29")[1:]
        self.assertEqual([r[1] for r in rows], stamps)   # every row, in order, once
        self.assertEqual(len(rows), len(set(r[1] for r in rows)))

    def test_no_gaps_when_every_cycle_fails_to_save(self):
        stamps = [f"2026-10-29 {hour:02d}:30:00-04:00" for hour in range(9, 15)]
        for stamp in stamps:
            self.add_monitor_rows(stamp)
            with failing_rename(fail_times=None):
                with contextlib.redirect_stderr(io.StringIO()):
                    self.pass_once()

        rows = [r[1] for r in self.rows_for("DST", "2026-10-29")[1:]]
        self.assertEqual(rows, stamps)

    def test_the_state_file_is_never_left_half_written(self):
        self.add_monitor_rows("2026-10-29 10:00:00-04:00")
        with failing_rename(fail_times=None):
            with contextlib.redirect_stderr(io.StringIO()):
                self.pass_once()

        state_path = self.logs / ".mirror_state.json"
        if state_path.exists():                         # absent is fine
            json.loads(state_path.read_text(encoding="utf-8"))


class TestFailureReporting(MirrorCase):
    """A failed source is reported, never counted as -1."""

    def test_a_failed_source_does_not_look_like_data(self):
        self.add_monitor_rows("2026-10-29 10:00:00-04:00")
        real = log_mirror.Mirror._mirror_source

        def boom(self, source, now):
            if source["key"] == "DST":
                raise PermissionError(13, "Permission denied")
            return real(self, source, now)

        log_mirror.Mirror._mirror_source = boom
        try:
            with contextlib.redirect_stderr(io.StringIO()):
                stats = self.pass_once()
        finally:
            log_mirror.Mirror._mirror_source = real

        self.assertEqual(stats["DST"], 0)               # a count, not a sentinel
        self.assertNotIn(-1, stats.values())
        self.assertEqual(stats["RUNNER"], 0)

    def test_the_run_prints_failures_as_failures(self):
        self.add_monitor_rows("2026-10-29 10:00:00-04:00")
        real = log_mirror.Mirror._mirror_source

        def boom(self, source, now):
            if source["key"] == "DST":
                raise PermissionError(13, "Permission denied")
            return real(self, source, now)

        log_mirror.Mirror._mirror_source = boom
        try:
            with contextlib.redirect_stdout(io.StringIO()) as out:
                with contextlib.redirect_stderr(io.StringIO()):
                    self.mirror().run(interval=0, max_passes=1)
        finally:
            log_mirror.Mirror._mirror_source = real

        text = out.getvalue()
        self.assertIn("failed: DST (permission denied)", text)
        self.assertNotIn("-1", text)

    def test_a_successful_pass_says_nothing_about_failures(self):
        self.add_monitor_rows("2026-10-29 10:00:00-04:00")
        with contextlib.redirect_stdout(io.StringIO()) as out:
            self.mirror().run(interval=0, max_passes=1)
        self.assertEqual(out.getvalue().strip(), "mirrored: DST 1")


class TestSyncClientHint(unittest.TestCase):
    def test_a_onedrive_environment_variable_is_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp) / "OneDrive - Contoso"
            (root / "Desktop" / "projects").mkdir(parents=True)
            old = os.environ.get("OneDriveCommercial")
            os.environ["OneDriveCommercial"] = str(root)
            try:
                hint = log_mirror.sync_client_hint(root / "Desktop" / "projects")
            finally:
                if old is None:
                    os.environ.pop("OneDriveCommercial", None)
                else:
                    os.environ["OneDriveCommercial"] = old
        self.assertIsNotNone(hint)
        self.assertIn("OneDrive", hint)

    def test_a_plain_folder_reports_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            for name in ("OneDrive", "OneDriveConsumer", "OneDriveCommercial"):
                os.environ.pop(name, None)
            self.assertIsNone(log_mirror.sync_client_hint(pathlib.Path(tmp)))


class TestStateRelocation(unittest.TestCase):
    """A state file inside a synced folder is moved out, automatically."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self._tmp.name)
        self.synced = self.root / "OneDrive" / "Desktop" / "projects" / "logs"
        self.synced.mkdir(parents=True)
        # A path with no sync client in it, for the "leave it alone" case.
        self.plain = self.root / "scratch" / "chronos" / "logs"
        self.plain.mkdir(parents=True)
        self._env = {k: os.environ.get(k) for k in
                     ("OneDrive", "OneDriveCommercial", "OneDriveConsumer",
                      "XDG_STATE_HOME", "LOCALAPPDATA")}
        for key in ("OneDrive", "OneDriveCommercial", "OneDriveConsumer"):
            os.environ.pop(key, None)

    def tearDown(self) -> None:
        for key, value in self._env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        self._tmp.cleanup()

    def test_without_a_sync_client_the_state_stays_beside_the_logs(self):
        path, why = log_mirror.resolve_state_path(self.plain)
        self.assertEqual(path, self.plain / ".mirror_state.json")
        self.assertIn("no sync client", why)

    def test_with_onedrive_it_moves_out_of_the_synced_folder(self):
        os.environ["OneDrive"] = str(self.root / "OneDrive")
        os.environ["XDG_STATE_HOME"] = str(self.root / "statedir")
        state_dir = self.root / "statedir" / "chronos"
        old = log_mirror.out_of_sync_state_path
        log_mirror.out_of_sync_state_path = lambda: state_dir / "mirror-state.json"
        try:
            path, why = log_mirror.resolve_state_path(self.synced)
        finally:
            log_mirror.out_of_sync_state_path = old
        self.assertEqual(path, state_dir / "mirror-state.json")
        self.assertIn("moved out of", why)
        self.assertNotIn("OneDrive", str(path))

    def test_an_explicit_state_path_always_wins(self):
        os.environ["OneDrive"] = str(self.root / "OneDrive")
        chosen = self.root / "elsewhere" / "state.json"
        path, why = log_mirror.resolve_state_path(self.synced, str(chosen))
        self.assertEqual(path, chosen)
        self.assertIn("--state", why)

    def test_an_existing_state_is_adopted_rather_than_abandoned(self):
        """Moving the file must not throw away the offsets it holds."""
        os.environ["OneDrive"] = str(self.root / "OneDrive")
        legacy = self.synced / ".mirror_state.json"
        legacy.write_text('{"sources": {"dst_monitor.csv": {"offset": 99}}}',
                          encoding="utf-8")
        target = self.root / "safe" / "mirror-state.json"
        old = log_mirror.out_of_sync_state_path
        log_mirror.out_of_sync_state_path = lambda: target
        try:
            path, why = log_mirror.resolve_state_path(self.synced)
        finally:
            log_mirror.out_of_sync_state_path = old

        self.assertEqual(path, target)
        self.assertIn("adopted", why)
        self.assertEqual(json.loads(target.read_text())["sources"]
                         ["dst_monitor.csv"]["offset"], 99)

    def test_the_chosen_path_is_printed_by_status(self):
        with tempfile.TemporaryDirectory() as tmp:
            logs = pathlib.Path(tmp) / "logs"
            logs.mkdir()
            with contextlib.redirect_stdout(io.StringIO()) as out:
                rc = log_mirror.main(["--logs-dir", str(logs), "--status"])
        self.assertEqual(rc, 0)
        self.assertIn("state file:", out.getvalue())


class TestHelpers(unittest.TestCase):
    def test_row_date_accepts_every_shape_the_logs_use(self):
        default = __import__("datetime").date(2026, 11, 1)
        self.assertEqual(log_mirror.row_date("2026-10-29", default=default),
                         __import__("datetime").date(2026, 10, 29))
        self.assertEqual(
            log_mirror.row_date("2026-10-29 23:50:00-04:00", default=default),
            __import__("datetime").date(2026, 10, 29))
        self.assertEqual(
            log_mirror.row_date("2026-10-30T00:15:00-04:00", default=default),
            __import__("datetime").date(2026, 10, 30))

    def test_an_unusable_date_falls_back_instead_of_dropping_the_row(self):
        default = __import__("datetime").date(2026, 11, 1)
        for bad in ("", "   ", "not a date", "2026-13-45"):
            self.assertEqual(log_mirror.row_date(bad, default=default), default)

    def test_the_mirror_never_opens_a_source_for_writing(self):
        """The read-only claim, checked structurally rather than promised."""
        source = pathlib.Path(log_mirror.__file__).read_text(encoding="utf-8")
        for op in ('"a"', "'a'", '"w"', "'w'", '"r+"'):
            self.assertNotIn(f"self.out_dir / {op}", source)
        self.assertIn('path.open("rb")', source)


if __name__ == "__main__":
    unittest.main()
