"""
Tests for the snapshot job under a Task Scheduler-style environment.

The bug these exist for is not reproducible by running the job the way anyone
tests it. A task scheduled by Windows inherits the **machine** PATH; an
interactive Git Bash session prepares its own, with Git's directories prepended.
A git install configured as "Use Git from Git Bash only" therefore works by hand
and fails on a timer — the failure being a bare
`FileNotFoundError: [WinError 2]`, which does not even say *git*.

So these tests build the hostile environment deliberately: no PATH, no
CHRONOS_GIT, and — where needed — a fake git at an absolute path. They run the
real entry point, not a helper, because the entry point is what a schedule calls.
"""

from __future__ import annotations

import contextlib
import io
import os
import pathlib
import stat
import sys
import tempfile
import unittest

BOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BOT))

from live import snapshot_and_push as snap  # noqa: E402


def make_fake_git(directory: pathlib.Path, *, branch: str = "arena/test-branch",
                  record: pathlib.Path | None = None) -> pathlib.Path:
    """A stand-in `git` on disk, so PATH resolution is exercised for real.

    It answers `rev-parse --abbrev-ref HEAD` and records every invocation, which
    is what proves the job found and used *this* binary rather than something
    else on a PATH inherited from the test runner.
    """
    path = directory / "git"
    path.write_text(
        "#!/bin/sh\n"
        + (f'echo "$@" >> "{record}"\n' if record else "")
        + 'case "$1" in\n'
        + f'  rev-parse) echo "{branch}" ;;\n'
        + '  *) exit 0 ;;\n'
        + "esac\n",
        encoding="utf-8",
    )
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return path


class ScheduledEnvCase(unittest.TestCase):
    """A temporary repo, plus control over what the job can see."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self._tmp.name)
        self.repo = self.root / "repo"
        (self.repo / "logs").mkdir(parents=True)
        self.saved_env = {key: os.environ.get(key)
                          for key in ("PATH", "CHRONOS_GIT")}
        snap.set_git_binary(None)            # drop any cached resolution

    def tearDown(self) -> None:
        for key, value in self.saved_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        snap.set_git_binary(None)
        self._tmp.cleanup()

    def strip_environment(self) -> None:
        """What a minimal scheduled process looks like: no PATH, no overrides."""
        os.environ.pop("CHRONOS_GIT", None)
        os.environ["PATH"] = ""

    def run_main(self, *args: str) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = snap.main([*args])
        return rc, out.getvalue(), err.getvalue()


class TestFindingGit(ScheduledEnvCase):
    def test_an_empty_path_still_finds_a_binary_via_the_override(self):
        """The fix for a git install that is not on the machine PATH."""
        self.strip_environment()
        fake = make_fake_git(self.root)
        os.environ["CHRONOS_GIT"] = str(fake)

        self.assertEqual(snap.find_git(refresh=True), str(fake))

    def test_a_binary_resolved_from_path_is_used(self):
        fake_dir = self.root / "gitbin"
        fake_dir.mkdir()
        fake = make_fake_git(fake_dir)
        os.environ["PATH"] = str(fake_dir)        # only this, nothing else
        os.environ.pop("CHRONOS_GIT", None)

        self.assertEqual(snap.find_git(refresh=True), str(fake))

    def test_a_missing_git_names_every_place_it_looked(self):
        self.strip_environment()

        with self.assertRaises(snap.GitUnavailable) as caught:
            snap.find_git(refresh=True)

        message = str(caught.exception)
        self.assertIn("could not find git", message)
        self.assertIn("PATH", message)                 # says what PATH was
        self.assertIn("CHRONOS_GIT", message)          # says how to fix it
        self.assertIn("--git", message)
        # Whether the Windows locations could be checked is itself reported,
        # rather than silently omitted on a box without those variables.
        self.assertIn("Standard install locations", message)

    def test_the_windows_install_locations_are_listed_with_variables_set(self):
        """On the Handler's machine these are set, so these paths get tried."""
        env = {"ProgramFiles": r"C:\Program Files",
               "LOCALAPPDATA": r"C:\Users\admin\AppData\Local",
               "USERPROFILE": r"C:\Users\admin"}
        paths = snap.git_candidate_paths(env)
        # Separator is the running platform's, so compare on the structure.
        normalised = [p.replace(os.sep, "/") for p in paths]

        # The three binaries a Git for Windows install actually contains.
        self.assertIn("C:/Program Files/Git/cmd/git.exe", normalised)
        self.assertIn("C:/Program Files/Git/mingw64/bin/git.exe", normalised)
        self.assertIn("C:/Program Files/Git/bin/git.exe", normalised)
        # And the per-user install.
        self.assertIn(
            "C:/Users/admin/AppData/Local/Programs/Git/cmd/git.exe", normalised)

    def test_a_path_whose_variable_is_unset_is_skipped_not_guessed(self):
        paths = snap.git_candidate_paths({"ProgramFiles": r"C:\Program Files"})
        self.assertTrue(paths)
        for path in paths:
            self.assertNotIn("%", path)

    def test_an_explicit_path_that_does_not_exist_is_rejected_clearly(self):
        with self.assertRaises(snap.GitUnavailable) as caught:
            snap.find_git(explicit=str(self.root / "no-such-git"))
        self.assertIn("does not exist", str(caught.exception))


class TestTheHandlersGitLayout(ScheduledEnvCase):
    """`which git` reports `/mingw64/bin/git` — Git Bash's own copy.

    Git Bash prepends its own directories, so that binary always wins the lookup
    there regardless of what the machine PATH contains. A scheduled task has no
    such prefixing, which is why the same command can work in the terminal and
    fail on a timer. The fallback must therefore look inside the install layout,
    not just at the name `git` on PATH.
    """

    def test_a_mingw64_only_install_is_found_with_an_empty_path(self):
        root = self.root / "Program Files" / "Git"
        (root / "mingw64" / "bin").mkdir(parents=True)
        binary = root / "mingw64" / "bin" / "git.exe"
        binary.write_text("#!/bin/sh\necho arena/x\n", encoding="utf-8")
        binary.chmod(0o755)
        self.assertFalse((root / "cmd").exists())     # as on the Handler's box

        os.environ["ProgramFiles"] = str(self.root / "Program Files")
        os.environ.pop("CHRONOS_GIT", None)
        os.environ["PATH"] = ""

        self.assertEqual(snap.find_git(refresh=True), str(binary))

    def test_every_binary_a_git_for_windows_install_contains_is_tried(self):
        env = {"ProgramFiles": str(self.root / "pf")}
        paths = snap.git_candidate_paths(env)
        names = {os.path.basename(os.path.dirname(p)) or p for p in paths}
        joined = " ".join(paths)
        self.assertIn("cmd", joined)
        self.assertIn("mingw64", joined)              # what `which git` reported
        self.assertIn("usr", joined)

    def test_the_resolved_binary_is_actually_invoked(self):
        """Resolution is worthless if the wrong thing still runs."""
        root = self.root / "Program Files" / "Git"
        (root / "mingw64" / "bin").mkdir(parents=True)
        record = self.root / "calls.txt"
        binary = root / "mingw64" / "bin" / "git.exe"
        binary.write_text(
            "#!/bin/sh\n"
            f'echo "$@" >> "{record}"\n'
            'if [ "$1" = "rev-parse" ]; then echo arena/test; fi\n' 
            "exit 0\n",
            encoding="utf-8")
        binary.chmod(0o755)

        os.environ["ProgramFiles"] = str(self.root / "Program Files")
        os.environ.pop("CHRONOS_GIT", None)
        os.environ["PATH"] = ""

        self.assertEqual(snap.current_branch(self.repo), "arena/test")
        self.assertIn("rev-parse", record.read_text(encoding="utf-8"))


class TestFailureDiagnostics(ScheduledEnvCase):
    def test_a_missing_working_directory_is_named_not_just_raised(self):
        self.strip_environment()
        gone = self.root / "moved-away"
        with self.assertRaises(snap.GitUnavailable) as caught:
            snap._run(gone, "rev-parse", "--abbrev-ref", "HEAD")

        message = str(caught.exception)
        self.assertIn("git rev-parse --abbrev-ref HEAD", message)   # the operation
        self.assertIn("does not exist", message)                     # the cause
        self.assertIn(str(gone), message)                            # the path

    def test_the_log_line_carries_the_operation_and_the_environment(self):
        """A scheduled run has no console, so the log is the only traceback."""
        self.strip_environment()
        rc, _out, err = self.run_main("--date", "2026-10-29",
                                      "--repo", str(self.repo),
                                      "--logs-dir", str(self.repo / "logs"))

        self.assertEqual(rc, 1)
        self.assertIn("could not find git", err)
        logged = (self.repo / "logs" / snap.LOG_NAME).read_text(encoding="utf-8")
        self.assertIn("FAILED", logged)
        self.assertIn("git", logged.lower())
        self.assertIn("PATH", logged)

    def test_the_diagnostic_never_hides_which_binary_failed(self):
        self.strip_environment()
        os.environ["PATH"] = ""
        snap.set_git_binary(str(self.root / "ghost-git"))
        try:
            with self.assertRaises(snap.GitUnavailable) as caught:
                snap._run(self.repo, "status")
        finally:
            snap.set_git_binary(None)
        self.assertIn("ghost-git", str(caught.exception))


class TestTheWholeJobUnderAMinimalEnvironment(ScheduledEnvCase):
    def test_a_dry_run_completes_with_only_an_override_and_no_path(self):
        """End to end through `main`, under conditions that broke the schedule."""
        self.strip_environment()
        record = self.root / "calls.txt"
        fake = make_fake_git(self.root, record=record)
        os.environ["CHRONOS_GIT"] = str(fake)
        (self.repo / "logs" / "DST_2026-10-29.csv").write_text(
            "a\n1\n", encoding="utf-8")

        rc, out, _err = self.run_main("--date", "2026-10-29",
                                      "--repo", str(self.repo),
                                      "--logs-dir", str(self.repo / "logs"),
                                      "--dry-run")

        self.assertEqual(rc, 0, out)
        self.assertIn("would stage", out)
        self.assertTrue(record.exists(), "the resolved git was never invoked")
        self.assertIn("rev-parse", record.read_text(encoding="utf-8"))

    def test_the_git_flag_works_without_any_environment_at_all(self):
        self.strip_environment()
        fake = make_fake_git(self.root)
        rc, _out, err = self.run_main("--doctor", "--repo", str(self.repo),
                                      "--git", str(fake))
        self.assertEqual(rc, 0, err)


class TestDoctor(ScheduledEnvCase):
    def test_it_reports_what_the_scheduled_process_can_see(self):
        self.strip_environment()
        fake = make_fake_git(self.root)
        os.environ["CHRONOS_GIT"] = str(fake)

        rc, out, _err = self.run_main("--doctor", "--repo", str(self.repo))

        self.assertEqual(rc, 0)
        self.assertIn("chronos snapshot doctor", out)
        self.assertIn("git resolved", out)
        self.assertIn(str(fake), out)
        self.assertIn("PATH entries", out)

    def test_it_fails_when_git_cannot_be_found(self):
        """The one question the Handler needs answered, answered in one word."""
        self.strip_environment()
        rc, out, _err = self.run_main("--doctor", "--repo", str(self.repo))
        self.assertEqual(rc, 1)
        self.assertIn("NOT FOUND", out)

    def test_it_flags_a_repository_that_is_not_there(self):
        self.strip_environment()
        fake = make_fake_git(self.root)
        os.environ["CHRONOS_GIT"] = str(fake)
        rc, out, _err = self.run_main("--doctor", "--repo",
                                      str(self.root / "nowhere"))
        self.assertIn("MISSING", out)
        self.assertEqual(rc, 0)          # git is what decides the exit code


if __name__ == "__main__":
    unittest.main()
