"""
Tests for live/durable.py, the one implementation of atomic file replacement.

Both failures this module exists for reached a live Windows machine past a green
suite, because neither is reproducible on POSIX in the form Windows presents
them. So the tests do two things: check the behaviour, and check the *shape* —
the read-only-fsync guard and the Windows error classification are asserted
directly, so the POSIX-only blind spot cannot reopen.
"""

from __future__ import annotations

import errno
import io
import json
import os
import pathlib
import sys
import tempfile
import unittest

BOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BOT))

from live import durable  # noqa: E402


class WindowsLock(PermissionError):
    """`PermissionError` as Windows raises it: `winerror` present."""

    def __init__(self, winerror: int = 5) -> None:
        super().__init__(13, "Access is denied")
        self.winerror = winerror


class TestClassification(unittest.TestCase):
    def test_the_windows_lock_errors_are_recognised(self):
        for winerror in (5, 32, 33, 117):
            self.assertTrue(durable.is_transient_lock_error(WindowsLock(winerror)),
                            winerror)

    def test_a_non_lock_windows_error_is_not(self):
        self.assertFalse(durable.is_transient_lock_error(WindowsLock(2)))

    def test_the_posix_equivalents_are_recognised(self):
        for code in (errno.EACCES, errno.EPERM, errno.EBUSY):
            self.assertTrue(
                durable.is_transient_lock_error(OSError(code, "busy")), code)

    def test_a_real_fault_is_not_transient(self):
        for code in (errno.EIO, errno.ENOSPC, errno.ENOENT):
            self.assertFalse(durable.is_transient_lock_error(OSError(code, "x")))

    def test_reasons_are_short_and_actionable(self):
        self.assertIn("WinError 5", durable.short_reason(WindowsLock(5)))
        self.assertIn("permission denied",
                      durable.short_reason(PermissionError("no")))
        self.assertLessEqual(len(durable.short_reason(OSError("x" * 500))), 80)


class TestFsync(unittest.TestCase):
    def test_a_read_only_handle_is_refused(self):
        """The bug that shipped: fsync on a read-only handle is EBADF on Windows."""
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "f.txt"
            path.write_text("x", encoding="utf-8")
            with path.open("rb") as fh:
                with self.assertRaises(ValueError) as caught:
                    durable.fsync_handle(fh)
        self.assertIn("read-only", str(caught.exception))

    def test_a_write_handle_is_accepted(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "f.txt"
            with path.open("w", encoding="utf-8") as fh:
                fh.write("x")
                durable.fsync_handle(fh)          # must not raise


class TestRetry(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = pathlib.Path(self._tmp.name)
        self.target = self.dir / "state.json"
        self.target.write_text("old", encoding="utf-8")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _patch_replace(self, fail_times, winerror=5):
        real = pathlib.Path.replace
        calls = {"n": 0}

        def fake(self, other):
            calls["n"] += 1
            if fail_times is None or calls["n"] <= fail_times:
                raise WindowsLock(winerror)
            return real(self, other)

        pathlib.Path.replace = fake
        return calls, lambda: setattr(pathlib.Path, "replace", real)

    def test_a_transient_lock_is_retried_until_it_clears(self):
        calls, restore = self._patch_replace(fail_times=2)
        try:
            durable.atomic_write_text(self.target, "new", sleep=lambda _s: None)
        finally:
            restore()
        self.assertEqual(self.target.read_text(encoding="utf-8"), "new")
        self.assertEqual(calls["n"], 3)

    def test_retries_are_bounded(self):
        calls, restore = self._patch_replace(fail_times=None)
        try:
            with self.assertRaises(OSError):
                durable.atomic_write_text(self.target, "new", sleep=lambda _s: None)
        finally:
            restore()
        self.assertEqual(calls["n"], durable.DEFAULT_ATTEMPTS)

    def test_the_old_content_survives_when_every_attempt_fails(self):
        _, restore = self._patch_replace(fail_times=None)
        try:
            with self.assertRaises(OSError):
                durable.atomic_write_text(self.target, "new", sleep=lambda _s: None)
        finally:
            restore()
        self.assertEqual(self.target.read_text(encoding="utf-8"), "old")

    def test_a_real_error_is_not_retried(self):
        calls = {"n": 0}
        real = pathlib.Path.replace

        def broken(self, other):
            calls["n"] += 1
            raise OSError(errno.EIO, "Input/output error")

        pathlib.Path.replace = broken
        try:
            with self.assertRaises(OSError):
                durable.atomic_write_text(self.target, "new", sleep=lambda _s: None)
        finally:
            pathlib.Path.replace = real
        self.assertEqual(calls["n"], 1)


class TestDiscovery(unittest.TestCase):
    def test_the_shared_module_is_the_only_definition(self):
        """The mirror must not carry its own copy of this rule again."""
        mirror = (BOT / "live" / "log_mirror.py").read_text(encoding="utf-8")
        self.assertIn("from live.durable import", mirror)
        self.assertNotIn("os.fsync(", mirror.split("from live.durable")[1])

    def test_the_runner_uses_it_too(self):
        runner = (BOT / "live" / "dry_run_runner.py").read_text(encoding="utf-8")
        self.assertIn("from live.durable import atomic_write_text", runner)


if __name__ == "__main__":
    unittest.main()
