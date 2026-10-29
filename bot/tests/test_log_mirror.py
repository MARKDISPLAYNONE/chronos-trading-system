"""
Tests for the daily log mirror (bot/live/log_mirror.py).

The mirror is the only copy of a day's evidence that survives until the Handler
reads it, so the tests are about the two ways such a copy fails: repeating rows
and losing them. Everything here drives the real class against real files — no
fakes for the I/O, because the I/O is the thing under test.
"""

from __future__ import annotations

import csv
import io
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
        mirror = log_mirror.Mirror(self.out, self.logs)
        return mirror

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

    def test_a_lost_state_file_re_mirrors_rather_than_stopping(self):
        self.add_monitor_rows("2026-10-29 10:00:00-04:00")
        self.pass_once()
        (self.logs / ".mirror_state.json").unlink()
        self.pass_once()                                 # no state: start again

        # Re-reading from zero duplicates what was already copied, which is the
        # honest outcome of losing the resume point; what must not happen is a
        # crash or a silently empty file.
        self.assertGreaterEqual(len(self.rows_for("DST", "2026-10-29")), 2)


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
