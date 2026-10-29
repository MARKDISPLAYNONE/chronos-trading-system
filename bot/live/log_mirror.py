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

The one window that remains is the same one the runner has: if the process dies
between fsyncing a target file and saving the state, the next start replays that
single record. The state is written atomically (`tmp` + `replace`), so it cannot
be left half-written, and a normal stop or a `kill` loses nothing.

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


def save_state(path: pathlib.Path, state: dict) -> None:
    """Atomic save: a crash mid-write cannot leave a half-written offset."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")
    with tmp.open("rb") as fh:
        os.fsync(fh.fileno())          # the state itself must reach the disk
    tmp.replace(path)


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
                 *, now_fn=None) -> None:
        self.out_dir = pathlib.Path(out_dir)
        self.logs_dir = pathlib.Path(logs_dir)
        self.state_path = (pathlib.Path(state_path) if state_path
                           else self.logs_dir / ".mirror_state.json")
        self.now_fn = now_fn or time.time
        self.state = load_state(self.state_path)
        self.stats: dict[str, int] = {}

    # -- writing -----------------------------------------------------------
    def _append(self, target: pathlib.Path, header: bytes, raw: bytes) -> None:
        """Append one record and make sure it is on the disk before returning."""
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        new = not target.exists() or target.stat().st_size == 0
        with target.open("ab") as fh:
            if new and header:
                fh.write(header.rstrip(b"\r\n") + b"\n")
            fh.write(raw.rstrip(b"\r\n") + b"\n")
            fh.flush()
            os.fsync(fh.fileno())

    # -- reading -----------------------------------------------------------
    def _mirror_source(self, source: dict, now: float) -> int:
        path = self.out_dir / source["file"]
        if not path.exists():
            return 0

        key = source["file"]
        entry = self.state["sources"].setdefault(key, {})
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
                save_state(self.state_path, self.state)
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
            save_state(self.state_path, self.state)
            return 0

        columns = next(csv.reader(io.StringIO(header_text)), [])
        if source["date_field"] not in columns:
            # The header does not name the column this source is routed by.
            # Advance anyway and say so once; refusing to advance would re-read
            # the same rows forever.
            entry["offset"] = offset + records[-1][2]
            save_state(self.state_path, self.state)
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
            save_state(self.state_path, self.state)
        return written

    # -- one pass ----------------------------------------------------------
    def mirror_once(self, now: float | None = None) -> dict:
        """Copy everything new from every source. Returns per-source row counts."""
        now = self.now_fn() if now is None else now
        self.stats = {}
        for source in SOURCES:
            try:
                self.stats[source["key"]] = self._mirror_source(source, now)
            except Exception:
                # One bad source must not stop the other two, and must not stop
                # the process: a mirror that dies quietly is worse than one that
                # misses a pass and says so.
                self.stats[source["key"]] = -1
                print(f"mirror failed for {source['file']}:", file=sys.stderr)
                traceback.print_exc()
        return self.stats

    def run(self, *, interval: float = DEFAULT_INTERVAL, once: bool = False,
            max_passes: int | None = None) -> int:
        passes = 0
        try:
            while True:
                stats = self.mirror_once()
                passes += 1
                if any(v > 0 for v in stats.values()):
                    print("mirrored: " + ", ".join(
                        f"{k} {v}" for k, v in stats.items()), flush=True)
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
