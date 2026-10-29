"""
Project Chronos — Phase 2 daily log snapshot
============================================

Commits and pushes the mirrored day files so they reach the Handler (and this
repository) without anyone reading a terminal.

Runs once a day, shortly after midnight New York, on the machine running the
dry run. Picks the **previous completed NY day** — the only day that can be
complete when this runs — stages `logs/`, commits `daily log snapshot <date>`,
and pushes to the feature branch.

WHY THE PREVIOUS DAY, NOT TODAY
-------------------------------
A trading date's last window closes at **00:15 NY the next morning**, so at
00:20 today's data is 99% written but not finished, and yesterday's is final.
"Previous completed calendar day" is therefore the right answer no matter what
time this is run: at 00:20 it is yesterday, and at 23:00 it is *still* yesterday,
because today is not over.

WHAT IT STAGES
--------------
All of `logs/`, not only the three files named for the date. That is deliberate:
a trade that resolves days later (Q13) appends a row to an *older* day's file,
and staging only today's date would leave that row uncommitted forever. The
commit message still names the day the run is for.

FAILING WITHOUT HURTING ANYTHING
--------------------------------
Every step is a subprocess with a timeout and no exception escapes this module.
A failed push (no Wi-Fi, GitHub down, expired credentials) is written to
`logs/SNAPSHOT_PUSH.log`, reported on stdout, and exits non-zero so Task
Scheduler marks the run failed. Nothing is left in a state that needs a human:

* the commit stays local, and the next night's run pushes both;
* if the same day's commit already exists (a run that committed but could not
  push), it is not repeated — the push is simply retried;
* the mirror and the runner are separate processes and are untouched by any of
  this.

It refuses to run on `main`/`master`. The hard rule is that logs never land on
the default branch, and a scheduled script is exactly the thing that would do it
by accident one day.

USAGE
-----
    python live/snapshot_and_push.py              # last night's files
    python live/snapshot_and_push.py --date 2026-10-29
    python live/snapshot_and_push.py --dry-run    # say what it would do
    python live/snapshot_and_push.py --no-push    # commit locally, do not push
"""

from __future__ import annotations

import argparse
import datetime as dt
import pathlib
import subprocess
import sys

#: Run from anywhere: `python live/snapshot_and_push.py` puts `live/` on
#: sys.path, not `bot/`, and this module imports `src` for the NY timezone.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

DEFAULT_REPO = pathlib.Path(__file__).resolve().parents[2]
DEFAULT_LOGS_DIR = DEFAULT_REPO / "logs"
LOG_NAME = "SNAPSHOT_PUSH.log"

#: Nothing here should take minutes; a hung git is a failed run, not a wait.
GIT_TIMEOUT_SECONDS = 120.0

#: Refuse to push these. A scheduled job must never be the thing that puts the
#: live logs on the default branch.
FORBIDDEN_BRANCHES = {"main", "master"}


def ny_now(now: dt.datetime | None = None) -> dt.datetime:
    """Aware NY datetime. Naive input is taken as NY wall clock."""
    from zoneinfo import ZoneInfo

    from src import config

    zone = ZoneInfo(config.NY_TZ)
    if now is None:
        return dt.datetime.now(zone)
    if now.tzinfo is None:
        return now.replace(tzinfo=zone)
    return now.astimezone(zone)


def previous_completed_day(now: dt.datetime | None = None) -> dt.date:
    """The most recent NY date that is already over.

    Always `today - 1`: at 00:20 the day that just ended is yesterday, and at
    23:00 yesterday is still the last *complete* day, because today is not done.
    """
    return ny_now(now).date() - dt.timedelta(days=1)


def _run(repo: pathlib.Path, *args: str) -> subprocess.CompletedProcess:
    """Run one git command. Never raises for a non-zero exit — the caller reads it."""
    return subprocess.run(
        ["git", *args], cwd=str(repo), capture_output=True, text=True,
        timeout=GIT_TIMEOUT_SECONDS, check=False,
    )


def current_branch(repo: pathlib.Path) -> str | None:
    done = _run(repo, "rev-parse", "--abbrev-ref", "HEAD")
    if done.returncode != 0:
        return None
    return done.stdout.strip() or None


def _already_committed(repo: pathlib.Path, message: str) -> bool:
    """Was this exact snapshot already committed? Then only the push is owed."""
    done = _run(repo, "log", "--all", "--fixed-strings", "--grep", message,
                "--format=%H", "-n", "1")
    return done.returncode == 0 and bool(done.stdout.strip())


class Result:
    def __init__(self) -> None:
        self.ok = False
        self.did_commit = False
        self.did_push = False
        self.message = ""

    def __repr__(self) -> str:                       # pragma: no cover - debugging
        return (f"Result(ok={self.ok}, commit={self.did_commit}, "
                f"push={self.did_push}, message={self.message!r})")


def log_line(logs_dir: pathlib.Path, text: str) -> None:
    """Append to the run's own log. Best-effort: logging must never fail a run."""
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    try:
        logs_dir.mkdir(parents=True, exist_ok=True)
        with (logs_dir / LOG_NAME).open("a", encoding="utf-8") as fh:
            fh.write(f"{stamp}  {text}\n")
            fh.flush()
    except Exception:
        pass


def snapshot(day: dt.date, *, repo: pathlib.Path = DEFAULT_REPO,
             logs_dir: pathlib.Path | None = None, push: bool = True,
             dry_run: bool = False) -> Result:
    """Stage, commit and (optionally) push the mirror files for `day`."""
    logs_dir = pathlib.Path(logs_dir) if logs_dir else repo / "logs"
    result = Result()
    message = f"daily log snapshot {day.isoformat()}"

    def report(text: str, *, ok: bool | None = None) -> None:
        print(text)
        log_line(logs_dir, text)
        if ok is True:
            result.ok = True

    branch = current_branch(repo)
    if branch is None:
        report("FAILED: not a git repository (or git is unavailable)", ok=False)
        result.message = "not a git repository"
        return result
    if branch in FORBIDDEN_BRANCHES:
        report(f"REFUSED: on branch {branch!r}; logs are never pushed to the "
               f"default branch", ok=False)
        result.message = f"refused on {branch}"
        return result

    expected = [logs_dir / f"{prefix}_{day.isoformat()}.csv"
                for prefix in ("DST", "RUNNER", "TRADES")]
    present = [path for path in expected if path.exists()]

    if dry_run:
        report(f"{day}: " + (", ".join(p.name for p in present) if present
                             else "no dedicated files (checking logs/ for late rows)"))
        report(f"[dry-run] would stage logs/*.csv, commit {message!r}"
               + (", and push" if push else ""), ok=True)
        result.message = "dry-run"
        return result

    # Stage the CSV day files by name, never the directory: the job's own status
    # log lives beside them, and a `git add logs/` would commit the record of the
    # commit into the commit. Every date is included, not only `day`, because a
    # trade resolving late (Q13) appends to an older day's file and would
    # otherwise stay uncommitted for ever.
    csvs = sorted(logs_dir.glob("*_[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9].csv"))
    if not csvs:
        report(f"nothing to snapshot for {day}: no mirror files in {logs_dir}",
               ok=True)
        result.message = "no files"
        return result

    report(f"{day}: " + (", ".join(p.name for p in present) if present
                         else "no files for this date, but logs/ has rows to commit"))

    def _stage_name(path: pathlib.Path) -> str:
        """Repo-relative where possible; git also accepts absolute paths."""
        try:
            return str(path.relative_to(repo))
        except ValueError:
            return str(path)

    added = _run(repo, "add", "--", *[_stage_name(path) for path in csvs])
    if added.returncode != 0:
        report(f"FAILED to stage {logs_dir}: {added.stderr.strip()}", ok=False)
        result.message = "git add failed"
        return result

    staged = _run(repo, "diff", "--cached", "--name-only")
    if staged.stdout.strip():
        commit = _run(repo, "commit", "-m", message)
        if commit.returncode != 0:
            report(f"FAILED to commit: {(commit.stderr or commit.stdout).strip()}",
                   ok=False)
            result.message = "git commit failed"
            return result
        result.did_commit = True
        report(f"committed {message!r}")
    elif _already_committed(repo, message):
        report(f"{message!r} was already committed; only the push is owed")
    else:
        report(f"nothing new to commit for {day}", ok=True)
        result.message = "nothing to commit"
        return result

    if not push:
        report("committed locally; --no-push given", ok=True)
        result.message = "committed, not pushed"
        return result

    pushed = _run(repo, "push", "origin", branch)
    if pushed.returncode != 0:
        # Not an exception, not a crash: the commit is safe locally and tomorrow
        # night's run retries the push. The message says exactly that so nobody
        # goes looking for a repair.
        detail = (pushed.stderr or pushed.stdout).strip().splitlines()
        report("FAILED to push: " + (detail[-1] if detail else "unknown error"),
               ok=False)
        report("  the commit is local and safe; the next run will push it")
        result.message = "push failed"
        return result

    result.did_push = True
    report(f"pushed {branch}", ok=True)
    result.message = "pushed"
    return result


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Commit and push the previous completed NY day's mirrored logs.")
    ap.add_argument("--date", default=None, metavar="YYYY-MM-DD",
                    help="override the day (default: the previous completed NY day)")
    ap.add_argument("--repo", default=str(DEFAULT_REPO))
    ap.add_argument("--logs-dir", default=None)
    ap.add_argument("--no-push", action="store_true", help="commit but do not push")
    ap.add_argument("--dry-run", action="store_true",
                    help="report what would happen and change nothing")
    ap.add_argument("--now", default=None, metavar="ISO",
                    help="override the clock (naive = NY wall clock)")
    args = ap.parse_args(argv)

    if args.date:
        try:
            day = dt.date.fromisoformat(args.date)
        except ValueError:
            print(f"--date needs YYYY-MM-DD, got {args.date!r}", file=sys.stderr)
            return 2
    else:
        stamp = None
        if args.now:
            try:
                stamp = dt.datetime.fromisoformat(args.now)
            except ValueError:
                print(f"--now needs ISO-8601, got {args.now!r}", file=sys.stderr)
                return 2
        day = previous_completed_day(stamp)

    try:
        result = snapshot(day, repo=pathlib.Path(args.repo),
                          logs_dir=pathlib.Path(args.logs_dir) if args.logs_dir else None,
                          push=not args.no_push, dry_run=args.dry_run)
    except Exception as exc:                          # never escape a scheduled job
        message = f"FAILED with an unexpected error: {type(exc).__name__}: {exc}"
        print(message, file=sys.stderr)
        log_line(pathlib.Path(args.logs_dir) if args.logs_dir
                 else pathlib.Path(args.repo) / "logs", message)
        return 1
    return 0 if result.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
