"""
Tests for the daily log snapshot (bot/live/snapshot_and_push.py).

This job is the only thing standing between a day's logs and their ending up
somewhere nobody can read them, so the tests use real git: a real repository, a
real remote, and real failures. Faking git here would test the mock.
"""

from __future__ import annotations

import contextlib
import datetime as dt
import io
import pathlib
import subprocess
import sys
import tempfile
import unittest

BOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BOT))

from live import snapshot_and_push as snap  # noqa: E402

#: A fixed clock: 00:20 NY on 30 October 2026 — the moment Task Scheduler runs.
AT_MIDNIGHT = dt.datetime(2026, 10, 30, 0, 20, tzinfo=dt.timezone.utc)


def ny_at(day: dt.date, hour: int, minute: int = 0) -> dt.datetime:
    """An aware NY datetime, given as wall clock."""
    return snap.ny_now(dt.datetime.combine(day, dt.time(hour, minute)))


class GitCase(unittest.TestCase):
    """A throwaway repo with a bare remote, on a feature branch."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        root = pathlib.Path(self._tmp.name)
        self.repo = root / "repo"
        self.remote = root / "remote.git"
        self.repo.mkdir()
        self.git("init", "--bare", str(self.remote), cwd=root)
        self.git("init")
        self.git("config", "user.email", "tester@example.invalid")
        self.git("config", "user.name", "Tester")
        self.git("remote", "add", "origin", str(self.remote))
        (self.repo / "README.md").write_text("chronos\n", encoding="utf-8")
        self.git("add", "-A")
        self.git("commit", "-m", "initial")
        self.git("checkout", "-b", "arena/test-branch")
        self.git("push", "-u", "origin", "arena/test-branch")
        self.logs = self.repo / "logs"

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def git(self, *args: str, cwd: pathlib.Path | None = None) -> str:
        done = subprocess.run(["git", *args], cwd=str(cwd or self.repo),
                              capture_output=True, text=True, check=False)
        self.assertEqual(done.returncode, 0, done.stderr)
        return done.stdout

    def write_day_files(self, day: dt.date) -> None:
        self.logs.mkdir(exist_ok=True)
        for prefix in ("DST", "RUNNER", "TRADES"):
            (self.logs / f"{prefix}_{day.isoformat()}.csv").write_text(
                "a,b\n1,2\n", encoding="utf-8")

    def commits(self) -> list[str]:
        return self.git("log", "--format=%s").splitlines()

    def remote_subjects(self) -> list[str]:
        return self.git("log", "--format=%s", "arena/test-branch",
                        cwd=self.remote).splitlines()

    def snapshot(self, day: dt.date, **kwargs):
        """Run the job with its console output captured.

        Several tests fail the push on purpose; without this the suite would
        print "FAILED to push" and a reader would think the tests themselves
        had broken.
        """
        kwargs.setdefault("repo", self.repo)
        kwargs.setdefault("logs_dir", self.logs)
        with contextlib.redirect_stdout(io.StringIO()):
            return snap.snapshot(day, **kwargs)


class TestWhichDayIsSnapshotted(unittest.TestCase):
    """"Previous completed day" must not depend on when the job happens to run."""

    def test_at_the_scheduled_time_it_is_yesterday(self):
        self.assertEqual(
            snap.previous_completed_day(ny_at(dt.date(2026, 10, 30), 0, 20)),
            dt.date(2026, 10, 29))

    def test_late_that_evening_it_is_still_yesterday(self):
        """At 23:00 today is not over, so yesterday is still the last whole day."""
        self.assertEqual(
            snap.previous_completed_day(ny_at(dt.date(2026, 10, 30), 23, 55)),
            dt.date(2026, 10, 29))

    def test_a_missed_trigger_firing_after_midnight_still_picks_a_whole_day(self):
        """A task that fires late (01:30) must not push a half-written day."""
        self.assertEqual(
            snap.previous_completed_day(ny_at(dt.date(2026, 10, 31), 1, 30)),
            dt.date(2026, 10, 30))

    def test_utc_is_not_used(self):
        """03:00 UTC on the 30th is still 23:00 NY on the 29th."""
        moment = dt.datetime(2026, 10, 30, 3, 0, tzinfo=dt.timezone.utc)
        self.assertEqual(snap.ny_now(moment).day, 29)
        self.assertEqual(snap.previous_completed_day(moment), dt.date(2026, 10, 28))

    def test_the_answer_across_a_dst_change_is_still_yesterday(self):
        for day in (dt.date(2026, 11, 1), dt.date(2026, 11, 2)):
            self.assertEqual(
                snap.previous_completed_day(ny_at(day, 0, 20)),
                day - dt.timedelta(days=1))


class TestSnapshot(GitCase):
    DAY = dt.date(2026, 10, 29)

    def test_it_commits_the_days_three_files_and_pushes(self):
        self.write_day_files(self.DAY)
        result = self.snapshot(self.DAY)

        self.assertTrue(result.ok, result.message)
        self.assertTrue(result.did_commit)
        self.assertTrue(result.did_push)
        self.assertEqual(self.commits()[0], "daily log snapshot 2026-10-29")
        self.assertEqual(self.remote_subjects()[0], "daily log snapshot 2026-10-29")
        tracked = self.git("ls-tree", "--name-only", "HEAD", "logs/").split()
        self.assertEqual(sorted(tracked), [
            "logs/DST_2026-10-29.csv", "logs/RUNNER_2026-10-29.csv",
            "logs/TRADES_2026-10-29.csv"])

    def test_it_writes_its_own_status_log(self):
        self.write_day_files(self.DAY)
        self.snapshot(self.DAY)
        text = (self.logs / snap.LOG_NAME).read_text(encoding="utf-8")
        self.assertIn("committed", text)
        self.assertIn("pushed", text)

    def test_a_second_run_the_same_day_creates_no_second_commit(self):
        self.write_day_files(self.DAY)
        self.snapshot(self.DAY)
        before = len(self.commits())

        again = self.snapshot(self.DAY)
        self.assertTrue(again.ok, again.message)
        self.assertFalse(again.did_commit)
        self.assertEqual(len(self.commits()), before)

    def test_a_day_with_no_files_is_not_a_failure(self):
        """A weekend, or the mirror never ran: report it, do not raise."""
        result = self.snapshot(dt.date(2026, 10, 31))
        self.assertTrue(result.ok, result.message)
        self.assertFalse(result.did_commit)
        self.assertEqual(result.message, "no files")

    def test_a_late_row_for_an_older_day_is_still_committed(self):
        """Q13: a trade resolved days later appends to its own trading date."""
        older = dt.date(2026, 10, 27)
        self.write_day_files(self.DAY)
        self.snapshot(self.DAY)
        # A row lands in an already-snapshotted day's file.
        (self.logs / f"TRADES_{older.isoformat()}.csv").write_text(
            "a,b\n3,4\n", encoding="utf-8")
        self.snapshot(dt.date(2026, 10, 30))

        tracked = self.git("ls-tree", "--name-only", "HEAD", "logs/").split()
        self.assertIn(f"logs/TRADES_{older.isoformat()}.csv", tracked)

    def test_it_never_touches_the_mirror_state(self):
        self.write_day_files(self.DAY)
        (self.logs / ".mirror_state.json").write_text("{}", encoding="utf-8")
        self.snapshot(self.DAY)
        # The state file is ignored, so it is not staged — but it must survive.
        self.assertTrue((self.logs / ".mirror_state.json").exists())
        tracked = self.git("ls-tree", "-r", "--name-only", "HEAD").split()
        self.assertNotIn("logs/.mirror_state.json", tracked)


class TestFailureHandling(GitCase):
    """A failed push must cost one night, not a repair."""

    DAY = dt.date(2026, 10, 29)

    def test_a_failed_push_is_logged_and_reported_without_raising(self):
        self.write_day_files(self.DAY)
        self.git("remote", "set-url", "origin", "/nonexistent/remote.git")

        result = self.snapshot(self.DAY)          # must not raise

        self.assertFalse(result.ok)
        self.assertTrue(result.did_commit)         # the work is safe locally
        self.assertFalse(result.did_push)
        text = (self.logs / snap.LOG_NAME).read_text(encoding="utf-8")
        self.assertIn("FAILED to push", text)
        self.assertIn("next run will push it", text)

    def test_the_next_night_pushes_both_days(self):
        """The retry is the recovery: no human touches anything."""
        first = dt.date(2026, 10, 29)
        self.write_day_files(first)
        self.git("remote", "set-url", "origin", "/nonexistent/remote.git")
        self.assertFalse(self.snapshot(first).ok)

        self.git("remote", "set-url", "origin", str(self.remote))
        self.write_day_files(dt.date(2026, 10, 30))
        second = self.snapshot(dt.date(2026, 10, 30))

        self.assertTrue(second.ok, second.message)
        self.assertTrue(second.did_push)
        subjects = self.remote_subjects()
        self.assertIn("daily log snapshot 2026-10-30", subjects)
        self.assertIn("daily log snapshot 2026-10-29", subjects)

    def test_a_commit_that_could_not_push_is_retried_not_repeated(self):
        self.write_day_files(self.DAY)
        self.git("remote", "set-url", "origin", "/nonexistent/remote.git")
        self.snapshot(self.DAY)
        commits_before = len(self.commits())

        self.git("remote", "set-url", "origin", str(self.remote))
        result = self.snapshot(self.DAY)

        self.assertTrue(result.ok, result.message)
        self.assertFalse(result.did_commit)        # nothing new to commit
        self.assertTrue(result.did_push)
        self.assertEqual(len(self.commits()), commits_before)

    def test_it_refuses_to_push_the_logs_to_main(self):
        self.write_day_files(self.DAY)
        self.git("checkout", "-b", "main")

        result = self.snapshot(self.DAY)

        self.assertFalse(result.ok)
        self.assertFalse(result.did_push)
        self.assertIn("main", result.message)
        self.assertNotIn("daily log snapshot", " ".join(self.remote_subjects()))

    def test_outside_a_repository_it_reports_rather_than_crashes(self):
        with tempfile.TemporaryDirectory() as tmp:
            with contextlib.redirect_stdout(io.StringIO()):
                result = snap.snapshot(self.DAY, repo=pathlib.Path(tmp),
                                       logs_dir=pathlib.Path(tmp) / "logs")
        self.assertFalse(result.ok)
        self.assertIn("not a git repository", result.message)


class TestCli(unittest.TestCase):
    def test_a_dry_run_changes_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            for args in (("init",),
                         ("config", "user.email", "t@example.invalid"),
                         ("config", "user.name", "T"),
                         ("commit", "--allow-empty", "-m", "init"),
                         ("checkout", "-b", "arena/local")):
                subprocess.run(["git", *args], cwd=str(root),
                               capture_output=True, check=True)
            (root / "logs").mkdir()
            (root / "logs" / "DST_2026-10-29.csv").write_text("a\n1\n",
                                                              encoding="utf-8")
            rc = snap.main(["--date", "2026-10-29", "--repo", str(root),
                            "--dry-run"])
            # Nothing committed, nothing added: the file is still untracked.
            staged = subprocess.run(["git", "diff", "--cached", "--name-only"],
                                    cwd=str(root), capture_output=True, text=True)
            self.assertEqual(staged.stdout.strip(), "")
        self.assertEqual(rc, 0)

    def test_a_bad_date_is_a_usage_error(self):
        self.assertEqual(snap.main(["--date", "last night"]), 2)


if __name__ == "__main__":
    unittest.main()
