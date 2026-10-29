"""
Project Chronos — Phase 2 daily log mirror
==========================================

The Handler is often away from the machine for whole days. Terminal scrollback
truncates, so reading a day's log off the screen does not work, and reaching into
`live/output/` while a run is in progress is the one thing that must not happen.

This is the way out of that corner: a **read-only** tailer that copies what the
runner is already writing into day files that can be committed and read later.

    live/output/dst_monitor.csv              ->  logs/DST_<NY date>.csv
    live/output/runner_events.csv            ->  logs/RUNNER_<NY date>.csv
    live/output/window_evaluations_audit.csv ->  logs/TRADES_<NY date>.csv

WHAT IT DOES NOT DO
-------------------
It never writes to `live/output/`. It opens those three files read-only, never
takes a lock on them, and holds no opinion about what they say. Deleting this
process, or its state file, cannot change a single row of the run, and no
certification verdict depends on it. That is the whole safety argument: the
mirror can only ever be wrong about *its own* copies.

It is a **separate long-lived process**, not a thread inside the runner or the
monitor. A thread would put this file I/O — and any bug in it — inside the
process that produces the evidence. A third process also means it can be
restarted, or left dead for an hour, without touching the run.

WRITE-THROUGH, NOT BATCHED
--------------------------
Every row is appended, flushed and `fsync`ed as it arrives, and the read offset is
recorded immediately afterwards. An OS buffer that has not reached the disk is a
lost row if the machine loses power at 03:00, and the point of this file is that
the day survives whatever happens at 03:00. The cost is a few thousand writes a
day, which is nothing.

RESUME
------
`logs/.mirror_state.json` holds, per source file, the byte offset of the last
mirrored record and the source's header line. Restarting resumes from that
offset, so rows are neither repeated nor skipped. Truncation (a smaller source
than the recorded offset) resets to zero rather than guessing.

**The day files are a second record of the same thing**, and the offset is not
the only way back. A state file that is missing, empty or interrupted means
"resume from zero" only if the day files are empty too; otherwise the longest
prefix of source records already present in them is skipped and the offset is
rebuilt from that. This is not hypothetical — the first live version crashed
before it ever saved a state, leaving rows on disk and no offset anywhere, so a
naive restart would have duplicated everything it had already copied.

Matching is on the record's own bytes, counted, and the walk stops at the first
record that is not already present, so a day file edited or rotated by something
else cannot cause a row to be skipped out of the middle of the source.

The state is written atomically (`tmp` + `replace`) and fsynced, so it cannot be
left half-written, and a normal stop or a `kill` loses nothing.

WHICH DATE A ROW BELONGS TO
---------------------------
`DST` and `RUNNER` rows carry their own NY wall clock, so the file is the date of
the row itself.

`TRADES` is grouped by **`trading_date`**, not by the row's timestamp. A 23:45
NY window of the 29th ends at 00:15 on the 30th, so routing it by its clock would
split one trading day across two files. The audit CSV is what `--certify <date>`
reads, so grouping it the same way makes `TRADES_<date>.csv` exactly the rows
that verdict considered. A trade that resolves days later (Q13) still routes to
its own trading date; `logs/` is committed as a whole each night, so the late row
is picked up on the next snapshot rather than orphaned.

USAGE
-----
    python live/log_mirror.py                  # follow, forever
    python live/log_mirror.py --once           # one pass, for a test or a task
    python live/log_mirror.py --status         # what has been mirrored so far
"""

from __future__ import annotations

import csv
import datetime as dt
import errno
import io
import json
import os
import pathlib
import sys
import time
import traceback

#: Run from anywhere: `python live/log_mirror.py` puts `live/` on sys.path, not
#: `bot/`, and this module imports `src` for the NY timezone.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

#: The three sources, and where each one's date comes from.
#:
#: `date_field` is a column in the source; a value that is not a bare
#: `YYYY-MM-DD` is read as an ISO timestamp whose *date part* is the NY calendar
#: date (both `ny_wall` columns are written with their offset, so no conversion
#: is needed or wanted — they are already NY wall clock).
SOURCES: tuple[dict, ...] = (
    {"key": "DST", "file": "dst_monitor.csv", "date_field": "ny_wall"},
    {"key": "RUNNER", "file": "runner_events.csv", "date_field": "ny_wall"},
    {"key": "TRADES", "file": "window_evaluations_audit.csv",
     "date_field": "trading_date"},
)

#: Seconds between passes when following.
DEFAULT_INTERVAL = 15.0

#: Default locations: the runner's own output, and a repo-root `logs/`.
DEFAULT_OUT_DIR = pathlib.Path(__file__).resolve().parent / "output"
DEFAULT_LOGS_DIR = pathlib.Path(__file__).resolve().parents[2] / "logs"
DEFAULT_STATE = DEFAULT_LOGS_DIR / ".mirror_state.json"


def _ny_today(now: float | None = None) -> dt.date:
    """The NY calendar date at `now`, for rows that cannot say for themselves."""
    from zoneinfo import ZoneInfo

    from src import config

    instant = (dt.datetime.now(dt.timezone.utc) if now is None
               else dt.datetime.fromtimestamp(now, dt.timezone.utc))
    return instant.astimezone(ZoneInfo(config.NY_TZ)).date()


def row_date(value: str, *, default: dt.date) -> dt.date:
    """The NY calendar date a row belongs to, from its own date column.

    Accepts `2026-10-29`, `2026-10-29T23:45:12-05:00` and the space-separated
    form the monitor writes. Anything unusable falls back to `default` (the
    mirror's current NY date) rather than dropping the row: an unroutable row
    still belongs in *some* day file, and losing it would be the worse error.
    """
    text = (value or "").strip()
    if not text:
        return default
    if len(text) == 10:
        try:
            return dt.date.fromisoformat(text)
        except ValueError:
            return default
    try:
        return dt.datetime.fromisoformat(text).date()
    except ValueError:
        return default


def load_state(path: pathlib.Path) -> dict:
    """Mirror state, or an empty one. Never raises: a lost state is recovered."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {"sources": {}}
    if not isinstance(raw, dict):
        return {"sources": {}}
    raw.setdefault("sources", {})
    return raw


def _fsync(fh) -> None:
    """Force a file's bytes to the disk, on a handle opened for writing.

    Every durable write goes through here so the rule lives in one place: fsync
    needs a **writable** handle. POSIX allows it on a read-only descriptor,
    Windows does not (`_commit` fails with `EBADF`), and a bug of exactly that
    shape reached a real machine because the difference is invisible on Linux.

    The guard makes that mistake fail the same way everywhere, at the moment it
    is made, instead of on one platform in the field.
    """
    mode = getattr(fh, "mode", "")
    if mode and "r" in mode and "+" not in mode:
        raise ValueError(
            f"fsync on a read-only handle (mode={mode!r}): allowed on POSIX, "
            f"EBADF on Windows. Open the file for writing instead.")
    fh.flush()
    os.fsync(fh.fileno())


#: Windows errors that mean "someone else has this file open right now".
#:
#: 5   ACCESS_DENIED       - a rename over a file another process holds open
#: 32  SHARING_VIOLATION   - the destination was opened without FILE_SHARE_DELETE
#: 33  LOCK_VIOLATION      - a byte-range lock is held across the rename
#: 117 ERROR_UNABLE_TO_MOVE_REPLACEMENT_2 - the replacement file is busy
#:
#: These are *transient*: they mean "not this millisecond", not "not ever". A
#: sync client (OneDrive, Dropbox), an antivirus scanner or the search indexer
#: opens a file it has just noticed and closes it milliseconds later, and the
#: rename that lands inside that window fails.
TRANSIENT_LOCK_WINERRORS = frozenset({5, 32, 33, 117})

#: The POSIX equivalents, so the same code path and tests work on Linux where
#: these numbers do not exist.
TRANSIENT_LOCK_ERRNOS = frozenset(
    value for value in (
        getattr(errno, "EACCES", None),
        getattr(errno, "EPERM", None),
        getattr(errno, "EBUSY", None),
        getattr(errno, "EDEADLK", None),
    ) if value is not None
)

#: Bounded retries. Short, because the condition lasts milliseconds and a mirror
#: that stops for seconds is a mirror that falls behind. Five attempts spanning
#: ~1.5 s: long enough to outlast a scanner, short enough not to stall the run.
REPLACE_ATTEMPTS = 5
REPLACE_BACKOFF_SECONDS = (0.05, 0.1, 0.2, 0.4)


def _short_reason(exc: BaseException) -> str:
    """One clause a person can act on, without a traceback."""
    winerror = getattr(exc, "winerror", None)
    if winerror in (5, 32, 33, 117):
        return f"file locked by another process (WinError {winerror})"
    if isinstance(exc, PermissionError):
        return "permission denied"
    text = str(exc) or type(exc).__name__
    return text if len(text) <= 80 else text[:77] + "..."


def sync_client_hint(path: pathlib.Path) -> str | None:
    """Name the folder-sync client that is probably locking files here, if any.

    A file-sync client watches a folder and opens new and changed files the
    moment they appear — which is exactly when this module renames its state
    file. On Windows that produces a transient `Access is denied` on the rename.
    The Handler's project lives under `Desktop\projects\...`, and on a Windows
    machine with OneDrive, Desktop is very often inside `OneDrive\`.

    Detected by environment variable first (the client's own root, reliable) and
    by path if the variables are absent (a path copied from a synced folder).
    """
    roots = []
    for name in ("OneDrive", "OneDriveConsumer", "OneDriveCommercial",
                 "Dropbox", "MEGASYNC"):
        value = os.environ.get(name)
        if value:
            roots.append((name.split("OneDrive")[0] or name, value))
    resolved = str(path.resolve()).lower()
    for label, root in roots:
        if root and root.lower() in resolved:
            return f"{label} (${label}, {root})"
    for label in ("onedrive", "dropbox", "google drive", "icloud"):
        if label in resolved:
            return f"{label.title()} (path)"
    if os.environ.get("OneDrive") or os.environ.get("OneDriveConsumer"):
        return None            # configured, but this folder is outside it
    return None


def _is_transient_lock_error(exc: BaseException) -> bool:
    """Is this a "file is briefly busy" error rather than a real fault?

    Read from `winerror` on Windows and `errno` elsewhere, so the same predicate
    can be exercised by tests on any platform.
    """
    winerror = getattr(exc, "winerror", None)
    if winerror is not None and winerror in TRANSIENT_LOCK_WINERRORS:
        return True
    return getattr(exc, "errno", None) in TRANSIENT_LOCK_ERRNOS


def _write_state_file(path: pathlib.Path, payload: str) -> None:
    """Write `payload` to `path` atomically: temp file, fsync, then rename."""
    tmp = path.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        fh.write(payload)
        _fsync(fh)
    # After the close: Windows cannot replace a file that still has an open
    # handle, and `replace` is what makes the write atomic.
    tmp.replace(path)


def save_state(path: pathlib.Path, state: dict, *,
               sleep=time.sleep, attempts: int = REPLACE_ATTEMPTS) -> None:
    """Atomic save, with bounded retries for a transiently locked destination.

    The fsync happens on the **write** handle, before it is closed. Writing the
    file, reopening it read-only and fsyncing that handle looks equivalent and is
    not: POSIX permits fsync on a read-only descriptor, Windows does not —
    `os.fsync` there is `_commit()`, which fails with `EBADF` (Errno 9) unless
    the handle was opened for writing. Doing it the read-only way passed every
    test on Linux and crashed on the first real run on the Handler's machine.

    The rename then failed on the same machine with `Access is denied`
    (WinError 5): the destination was held open at that instant by something
    else on the machine — not by this process, which holds no handle on either
    file at this point. See `TRANSIENT_LOCK_WINERRORS`. Those are retried; every
    other error is raised immediately, because retrying a real fault (a
    permission that will never be granted, a full disk) only hides it.

    Raises the last error when the retries are exhausted. Callers that must keep
    running use `Mirror._save`, which turns that into a reported deferral.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(state, indent=2, sort_keys=True)

    last: OSError | None = None
    for delay in [*REPLACE_BACKOFF_SECONDS, None][:attempts]:
        try:
            _write_state_file(path, payload)
            return
        except OSError as exc:
            if not _is_transient_lock_error(exc):
                raise
            last = exc
            if delay is None:
                break
            sleep(delay)
    assert last is not None
    raise last


def _split_complete_records(blob: bytes) -> tuple[list[tuple[list[str], bytes, int]], int]:
    """Parse the complete CSV records in `blob`.

    Returns `(records, consumed)`. Each record is `(cells, raw_bytes,
    end_offset)`; `consumed` is the byte length of the prefix that formed whole
    records. Anything after it is an incomplete record and is left for the next
    pass.

    Two details matter more than they look:

    * **Raw bytes travel with the parsed cells.** A quoted field may contain a
      newline (an exception traceback in an event detail does), so records are
      located by the CSV parser and copied byte for byte. The mirror is then a
      faithful slice of the source rather than a re-serialisation of it.
    * **Offsets are byte offsets into the source.** They are computed from the
      original bytes, never from a decoded string, so a non-UTF-8 byte in a
      detail field cannot shift the next read by a byte and corrupt every row
      after it.
    """
    if not blob:
        return [], 0

    # A record always ends with a newline; a trailing fragment is not one yet.
    if not blob.endswith(b"\n"):
        cut = blob.rfind(b"\n")
        blob = blob[:cut + 1] if cut >= 0 else b""
    if not blob:
        return [], 0

    lines = blob.splitlines(keepends=True)          # bytes, so lengths are exact
    ends = [0]
    for line in lines:
        ends.append(ends[-1] + len(line))

    text = blob.decode("utf-8", errors="replace")
    records: list[tuple[list[str], bytes, int]] = []
    line_no = 0
    # strict=True is what makes the reader *raise* on an unterminated quoted
    # field instead of quietly returning a truncated record. Without it a row the
    # runner is half-way through writing would be mirrored as a short row and the
    # offset would move past it, so the complete row would never be copied.
    reader = csv.reader(io.StringIO(text), strict=True)
    try:
        for cells in reader:
            raw = b"".join(lines[line_no:reader.line_num])
            line_no = reader.line_num
            records.append((cells, raw, ends[line_no]))
    except csv.Error:
        # With strict=True the parser raises *instead of* yielding the record it
        # could not finish, so everything already in `records` is whole and the
        # only thing to do is stop at the last complete boundary.
        pass
    return records, ends[line_no]


class Mirror:
    """One pass copies whatever is new; `run` repeats that until stopped."""

    def __init__(self, out_dir: pathlib.Path | str = DEFAULT_OUT_DIR,
                 logs_dir: pathlib.Path | str = DEFAULT_LOGS_DIR,
                 state_path: pathlib.Path | str | None = None,
                 *, now_fn=None, save_sleep=None) -> None:
        self.out_dir = pathlib.Path(out_dir)
        self.logs_dir = pathlib.Path(logs_dir)
        self.state_path = (pathlib.Path(state_path) if state_path
                           else self.logs_dir / ".mirror_state.json")
        self.now_fn = now_fn or time.time
        #: Injected so tests exercise the retry path without waiting for it.
        self.save_sleep = save_sleep or time.sleep
        self.state = load_state(self.state_path)
        #: Rows mirrored per source key in the last pass. A count, never a
        #: sentinel: a negative number in a count column reads as corrupt data.
        self.stats: dict[str, int] = {}
        #: Source key -> short reason, for sources that failed this pass.
        self.errors: dict[str, str] = {}
        #: Why the state file could not be saved in the last pass, if it could
        #: not. Non-fatal by design: the offsets stay correct in memory.
        self.state_error: str | None = None
        #: Offsets are verified against the day files once, on the first pass.
        self._bootstrapped = False

    def _save(self) -> bool:
        """Persist the state, or report why not. Never raises.

        A locked or unwritable state file must not end the pass: the rows are
        already on the disk, the offsets are still correct in memory, and the
        process can keep mirroring. What is lost is the *durable* record of the
        offset — which is exactly what `_resume_from_destination` rebuilds on the
        next start, so the fallback already exists and is tested.
        """
        try:
            save_state(self.state_path, self.state, sleep=self.save_sleep)
        except OSError as exc:
            self.state_error = _short_reason(exc)
            return False
        self.state_error = None
        return True

    # -- writing -----------------------------------------------------------
    def _append(self, target: pathlib.Path, header: bytes, raw: bytes) -> None:
        """Append one record and make sure it is on the disk before returning."""
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        new = not target.exists() or target.stat().st_size == 0
        with target.open("ab") as fh:
            if new and header:
                fh.write(header.rstrip(b"\r\n") + b"\n")
            fh.write(raw.rstrip(b"\r\n") + b"\n")
            _fsync(fh)

    # -- reading -----------------------------------------------------------
    def _resume_from_destination(self, source: dict, path: pathlib.Path
                                 ) -> tuple[int, str | None]:
        """Rebuild the offset for a source whose state was never persisted.

        This exists because of a real failure. The crash in `save_state` meant
        every run wrote its rows to the day files and then died *before* saving
        an offset, so the next start called itself fresh and re-read from zero,
        rewriting rows that were already on disk. Six part-written files and no
        state file is exactly what that leaves behind.

        So a fresh start is not "offset zero", it is "offset zero unless the day
        files already account for a prefix of this source". The longest prefix of
        source records already present in the day files is skipped; matching is
        on the record's own bytes, counted, so an identical row that appears
        twice is only credited twice.

        The walk **stops at the first record that is not already there**. A day
        file that has been rotated, edited or written by something else
        therefore cannot cause a row to be skipped out of the middle of the
        source — at worst the offset lands early and a row is written twice,
        which is the recoverable direction.
        """
        from collections import Counter

        existing: Counter = Counter()
        for day_file in sorted(self.logs_dir.glob(f"{source['key']}_*.csv")):
            try:
                blob = day_file.read_bytes()
            except OSError:
                continue
            records, _consumed = _split_complete_records(blob)
            for _cells, raw, _end in records[1:]:        # skip the day header
                existing[raw.rstrip(b"\r\n")] += 1
        if not existing:
            return 0, None

        try:
            records, _consumed = _split_complete_records(path.read_bytes())
        except OSError:
            return 0, None
        if not records:
            return 0, None

        header = records[0][1].decode("utf-8", errors="replace").strip()
        offset = 0
        for _cells, raw, end in records[1:]:
            normal = raw.rstrip(b"\r\n")
            if existing.get(normal, 0) <= 0:
                break
            existing[normal] -= 1
            offset = end
        return offset, header

    def _bootstrap_offsets(self) -> None:
        """Check every saved offset against the day files before reading anything.

        The saved offset is what was last *persisted*; the day files are what was
        last *written*. When a save fails they disagree, and the state is the one
        that is behind — it says "resume at byte 142" while the destination
        already contains the row that followed. Trusting it re-copies that row.

        That is not hypothetical. It is what this check was added for: a run
        whose state save failed, restarted, and duplicated the row it had just
        written. The failure needed no crash — only a locked state file.

        Taking the later of the two is the safe direction in both cases: a stale
        state is corrected, and a fresh one is left alone.
        """
        for source in SOURCES:
            path = self.out_dir / source["file"]
            if not path.exists():
                continue
            entry = self.state["sources"].setdefault(source["file"], {})
            recorded = int(entry.get("offset") or 0)
            recovered, header = self._resume_from_destination(source, path)
            if header is not None and "header" not in entry:
                entry["header"] = header
            if recovered > recorded:
                entry["offset"] = recovered
                why = ("no state was ever saved" if not recorded
                       else f"the saved state was {recovered - recorded} byte(s) "
                            f"behind the day files")
                print(f"{source['file']}: resuming at byte {recovered} "
                      f"({why}; those rows are already mirrored)", flush=True)
                self._save()

    def _mirror_source(self, source: dict, now: float) -> int:
        path = self.out_dir / source["file"]
        if not path.exists():
            return 0

        key = source["file"]
        entry = self.state["sources"].setdefault(key, {})
        if "offset" not in entry:
            # No recorded offset: either a first run, or a previous one that
            # wrote rows and died before saving. Ask the day files which it was
            # rather than assuming zero and duplicating them.
            recovered, header = self._resume_from_destination(source, path)
            entry["offset"] = recovered
            if header is not None:
                entry["header"] = header
            if recovered:
                print(f"{source['file']}: resuming at byte {recovered} "
                      f"(day files already hold a prefix of it; no state was "
                      f"saved by the previous run)", flush=True)
                # Persist the conclusion now. It was reached by reading the day
                # files, which is expensive and, more to the point, is the only
                # thing standing between a restart and a duplicate.
                self._save()
        offset = int(entry.get("offset") or 0)

        with path.open("rb") as fh:
            size = fh.seek(0, os.SEEK_END)
            if size < offset:
                # Truncated or replaced under us: start over rather than invent
                # a mapping between two different files.
                offset = 0
                entry["offset"] = 0
            fh.seek(offset)
            blob = fh.read()

        records, consumed = _split_complete_records(blob)
        if not records:
            if blob:
                # Bytes are waiting but no complete record could be parsed: the
                # writer is mid-row (normal), or the file is malformed (not).
                # Say so before it looks like a silent gap.
                entry["waiting"] = int(entry.get("waiting") or 0) + 1
                if entry["waiting"] in (3, 30):
                    print(f"{source['file']}: {len(blob)} unparsed byte(s) at "
                          f"offset {offset} after {entry['waiting']} passes - "
                          f"still waiting for a complete record", file=sys.stderr)
                self._save()
            return 0
        entry["waiting"] = 0

        header_text = entry.get("header")
        if header_text is None:                    # first ever pass on this source
            _cells, header_bytes, _end = records[0]
            header_text = header_bytes.decode("utf-8", errors="replace").strip()
            entry["header"] = header_text
            entries = records[1:]
        else:
            entries = [r for r in records
                       if r[1].rstrip(b"\r\n").decode("utf-8", errors="replace")
                       != header_text.strip()]

        if not entries:
            entry["offset"] = offset + records[-1][2]
            self._save()
            return 0

        columns = next(csv.reader(io.StringIO(header_text)), [])
        if source["date_field"] not in columns:
            # The header does not name the column this source is routed by.
            # Advance anyway and say so once; refusing to advance would re-read
            # the same rows forever.
            entry["offset"] = offset + records[-1][2]
            self._save()
            return 0
        column = columns.index(source["date_field"])
        header_bytes = header_text.encode("utf-8")

        default = _ny_today(now)
        written = 0
        for cells, raw, end_offset in entries:
            value = cells[column] if column < len(cells) else ""
            day = row_date(value, default=default)
            self._append(self.logs_dir / f"{source['key']}_{day.isoformat()}.csv",
                         header_bytes, raw)
            written += 1
            # The row is on the disk, so its offset may be recorded. Saving here
            # rather than after the batch is what keeps a crash from replaying it.
            entry["offset"] = offset + end_offset
            self.state["sources"][key] = entry
            # One save per row, not per batch: the row is already durable, so
            # the offset may be too. If this cannot be saved, the next row tries
            # again and the offset stays correct in memory meanwhile.
            self._save()
        return written

    # -- one pass ----------------------------------------------------------
    def mirror_once(self, now: float | None = None) -> dict:
        """Copy everything new from every source. Returns per-source row counts."""
        now = self.now_fn() if now is None else now
        self.stats = {}
        self.errors = {}
        self.state_error = None
        if not self._bootstrapped:
            self._bootstrapped = True
            try:
                self._bootstrap_offsets()
            except OSError as exc:               # an unreadable day file
                print(f"could not verify saved offsets: {_short_reason(exc)}",
                      file=sys.stderr)
        for source in SOURCES:
            try:
                self.stats[source["key"]] = self._mirror_source(source, now)
            except Exception as exc:
                # One bad source must not stop the other two, and must not stop
                # the process: a mirror that dies quietly is worse than one that
                # misses a pass and says so.
                #
                # The count stays a count. It used to be `-1` here, which
                # printed as "mirrored: DST -1" and read like corrupt data
                # rather than a failure. Failures have their own channel.
                self.stats[source["key"]] = 0
                self.errors[source["key"]] = _short_reason(exc)
                print(f"mirror failed for {source['file']}:", file=sys.stderr)
                traceback.print_exc()
        if self.state_error:
            # Said once per pass, not once per row: a locked state file would
            # otherwise produce a line per row and bury everything else.
            print(f"state NOT saved: {self.state_error}", file=sys.stderr)
            print("  offsets are still correct in memory and mirroring continues; "
                  "the next start rebuilds them from the day files",
                  file=sys.stderr)
            hint = sync_client_hint(self.state_path.parent)
            if hint and "locked" in (self.state_error or ""):
                print(f"  likely cause: {hint} is syncing this folder. "
                      f"Move the state file out of it with "
                      f"--state <path outside the synced folder>.",
                      file=sys.stderr)
        return self.stats

    def run(self, *, interval: float = DEFAULT_INTERVAL, once: bool = False,
            max_passes: int | None = None) -> int:
        passes = 0
        try:
            while True:
                stats = self.mirror_once()
                passes += 1
                moved = {k: v for k, v in stats.items() if v > 0}
                if moved:
                    print("mirrored: " + ", ".join(
                        f"{k} {v}" for k, v in moved.items()), flush=True)
                if self.errors:
                    print("failed: " + ", ".join(
                        f"{k} ({why})" for k, why in self.errors.items()),
                        flush=True)
                if once or (max_passes is not None and passes >= max_passes):
                    return 0
                time.sleep(interval)
        except KeyboardInterrupt:
            print("\nstopped by operator", flush=True)
            return 0


def describe_state(state_path: pathlib.Path, logs_dir: pathlib.Path) -> str:
    """Human summary of what has been mirrored — read-only."""
    state = load_state(state_path)
    lines = [f"mirror state: {state_path}"]
    if not state["sources"]:
        lines.append("  nothing mirrored yet")
    for key in ("dst_monitor.csv", "runner_events.csv",
                "window_evaluations_audit.csv"):
        entry = state["sources"].get(key) or {}
        lines.append(f"  {key:<32} offset {int(entry.get('offset') or 0):>10} bytes")
    files = sorted(logs_dir.glob("*_*.csv")) if logs_dir.exists() else []
    if not files:
        lines.append("no day files yet")
    for path in files:
        lines.append(f"  {path.name:<28} {path.stat().st_size:>10} bytes")

    # Offsets are *resume points*, not a measure of what has been mirrored: a run
    # that wrote rows and died before saving its state leaves offsets at 0 and
    # the day files full. Saying only "offset 0" reads as "nothing was mirrored",
    # which is how a real incident was misdiagnosed once already.
    with_rows = [p for p in files if p.stat().st_size > 0]
    all_zero = all(int((state["sources"].get(key) or {}).get("offset") or 0) == 0
                   for key in ("dst_monitor.csv", "runner_events.csv",
                               "window_evaluations_audit.csv"))
    if with_rows and all_zero:
        lines.append("")
        lines.append("WARNING: day files hold rows but no source has a saved "
                     "offset.")
        lines.append("  An earlier run wrote rows and did not persist its state "
                     "(crash, or a kill")
        lines.append("  before the first save). A fresh start will reconcile "
                     "against these files and")
        lines.append("  resume past the rows they already contain, rather than "
                     "duplicating them.")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(
        description="Mirror the live dry-run logs into per-NY-day files "
                    "(read-only; never touches live/output).")
    ap.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR))
    ap.add_argument("--logs-dir", default=str(DEFAULT_LOGS_DIR))
    ap.add_argument("--state", default=None,
                    help=f"mirror state (default: <logs-dir>/.mirror_state.json)")
    ap.add_argument("--interval", type=float, default=DEFAULT_INTERVAL,
                    help=f"seconds between passes (default {DEFAULT_INTERVAL:g})")
    ap.add_argument("--once", action="store_true", help="one pass, then exit")
    ap.add_argument("--status", action="store_true",
                    help="print what has been mirrored so far and exit")
    args = ap.parse_args(argv)

    logs_dir = pathlib.Path(args.logs_dir)
    state_path = pathlib.Path(args.state) if args.state else logs_dir / ".mirror_state.json"
    if args.status:
        print(describe_state(state_path, logs_dir))
        return 0

    mirror = Mirror(args.out_dir, logs_dir, state_path)
    return mirror.run(interval=args.interval, once=args.once)


if __name__ == "__main__":
    raise SystemExit(main())
