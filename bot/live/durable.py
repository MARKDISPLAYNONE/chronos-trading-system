"""
Project Chronos — durable, lock-tolerant file replacement
=========================================================

One place for a rule that has now bitten twice in this project, both times on a
Windows machine whose whole tree is inside OneDrive:

**Replacing a file atomically is two operations, and each can fail alone.**

1. *Writing* the temporary file. If its bytes are not flushed and `fsync`ed
   before the rename, the rename can succeed while the contents sit in an OS
   buffer — a power cut then leaves a zero-length file with a perfectly good
   name. And `fsync` needs a handle opened for **writing**: POSIX allows it on a
   read-only descriptor, Windows returns `EBADF`.
2. *Renaming* over the destination. On Windows this fails with `ACCESS_DENIED`
   or `SHARING_VIOLATION` while another process holds the destination open — and
   a folder-sync client opens a file within milliseconds of noticing it changed.
   Antivirus and the search indexer do the same.

Neither failure exists on POSIX in the form Windows presents it, which is why
both reached a live machine past a green test suite. Hence this module: the
constants, the classifier and the retry live here once, and every durable write
in the project goes through them.

What is retried is deliberately narrow. Only errors that mean *busy right now*
are retried; anything else is raised on the first attempt, because retrying a
permission that will never be granted, or a full disk, only delays the report.
"""

from __future__ import annotations

import errno
import os
import pathlib
import time

#: Windows errors that mean "someone else has this file open right now".
#:
#: 5   ACCESS_DENIED                        - rename over a file held open
#: 32  SHARING_VIOLATION                    - destination opened without FILE_SHARE_DELETE
#: 33  LOCK_VIOLATION                       - a byte-range lock spans the rename
#: 117 ERROR_UNABLE_TO_MOVE_REPLACEMENT_2   - the replacement file itself is busy
TRANSIENT_LOCK_WINERRORS = frozenset({5, 32, 33, 117})

#: The POSIX equivalents, so the same code path and tests work on Linux where
#: the Windows numbers do not exist.
TRANSIENT_LOCK_ERRNOS = frozenset(
    value for value in (
        getattr(errno, "EACCES", None),
        getattr(errno, "EPERM", None),
        getattr(errno, "EBUSY", None),
        getattr(errno, "EDEADLK", None),
    ) if value is not None
)

#: Bounded retries: short, because the condition lasts milliseconds and a live
#: process that stalls is worse than one that reports. ~1.5 s in total.
DEFAULT_ATTEMPTS = 5
DEFAULT_BACKOFF_SECONDS = (0.05, 0.1, 0.2, 0.4)


def is_transient_lock_error(exc: BaseException) -> bool:
    """Is this a "file is briefly busy" error rather than a real fault?

    On Windows `winerror` is authoritative and `errno` is only CPython's
    approximation of it, so when it is present it decides alone. Falling back to
    `errno` as well would classify an unrelated Windows `PermissionError` as a
    lock and retry it pointlessly.
    """
    winerror = getattr(exc, "winerror", None)
    if winerror is not None:
        return winerror in TRANSIENT_LOCK_WINERRORS
    return getattr(exc, "errno", None) in TRANSIENT_LOCK_ERRNOS


def short_reason(exc: BaseException) -> str:
    """One clause a person can act on, without a traceback."""
    winerror = getattr(exc, "winerror", None)
    if winerror in TRANSIENT_LOCK_WINERRORS:
        return f"file locked by another process (WinError {winerror})"
    if isinstance(exc, PermissionError):
        return "permission denied"
    text = str(exc) or type(exc).__name__
    return text if len(text) <= 80 else text[:77] + "..."


def fsync_handle(fh) -> None:
    """Flush and `fsync` a handle that was opened for writing.

    The read-only guard is the point: `with path.open("rb") as fh:
    os.fsync(fh.fileno())` looks correct, passes on Linux and raises `EBADF` on
    Windows. Failing the same way on every platform turns a field-only crash
    into a test failure.
    """
    mode = getattr(fh, "mode", "")
    if mode and "r" in mode and "+" not in mode:
        raise ValueError(
            f"fsync on a read-only handle (mode={mode!r}): allowed on POSIX, "
            f"EBADF on Windows. Open the file for writing instead.")
    fh.flush()
    os.fsync(fh.fileno())


def replace_with_retry(tmp: pathlib.Path, target: pathlib.Path, *,
                       sleep=time.sleep, attempts: int = DEFAULT_ATTEMPTS,
                       backoff: tuple[float, ...] = DEFAULT_BACKOFF_SECONDS) -> None:
    """`tmp.replace(target)`, retrying while the destination is briefly locked.

    Raises the last error when the attempts are exhausted. A caller that must
    keep running catches it; a caller whose state is not reconstructable lets it
    propagate, because continuing while unable to persist is the silent option.
    """
    schedule = [*backoff, None][:attempts]
    last: OSError | None = None
    for delay in schedule:
        try:
            tmp.replace(target)
            return
        except OSError as exc:
            if not is_transient_lock_error(exc):
                raise
            last = exc
            if delay is None:
                break
            sleep(delay)
    assert last is not None
    raise last


def atomic_write_text(path: pathlib.Path, text: str, *,
                      sleep=time.sleep, attempts: int = DEFAULT_ATTEMPTS,
                      backoff: tuple[float, ...] = DEFAULT_BACKOFF_SECONDS,
                      encoding: str = "utf-8") -> None:
    """Write `text` to `path` so a reader sees the old file or the new one.

    Temp file in the same directory (a rename across filesystems is not a
    rename), fsynced on the write handle, then renamed over the target with
    bounded retries for a transiently locked destination.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding=encoding) as fh:
        fh.write(text)
        fsync_handle(fh)
    # After the close: Windows cannot replace a file that still has an open
    # handle, and `replace` is what makes the write atomic.
    replace_with_retry(tmp, path, sleep=sleep, attempts=attempts, backoff=backoff)
