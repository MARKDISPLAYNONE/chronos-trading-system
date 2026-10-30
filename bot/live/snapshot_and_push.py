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
import os
import pathlib
import re
import shutil
import subprocess
import sys
import time

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


class GitUnavailable(RuntimeError):
    """`git` could not be executed at all — a fault, not a failed command.

    Distinct from a git command that ran and exited non-zero: that is a result
    the job already handles (retry, log, next night). This is the case where
    there is no git to talk to, which no amount of retrying fixes.
    """


#: Where git.exe actually lives on Windows, per install flavour.
#:
#: The layout matters more than it looks. A Git for Windows install has **three**
#: git.exe binaries, and which one resolves depends entirely on who is asking:
#:
#:   cmd\git.exe           what the installer puts on the system PATH
#:   mingw64\bin\git.exe   what Git Bash resolves first (it prepends this dir)
#:   bin\git.exe           the wrapper in the old layout
#:
#: An interactive Git Bash session always finds `mingw64\bin\git.exe` because it
#: prepends its own directories — so `which git` there reports a path the machine
#: PATH may never have contained. That is precisely the asymmetry that makes a
#: scheduled job fail while the same command works in the terminal, and it is why
#: the fallback searches the install layout rather than trusting one file name.
GIT_INSTALL_ROOTS = (
    r"%ProgramFiles%\Git",
    r"%ProgramW6432%\Git",
    r"%ProgramFiles(x86)%\Git",
    r"%LOCALAPPDATA%\Programs\Git",
    r"%USERPROFILE%\scoop\apps\git\current",
)

GIT_RELATIVE_BINARIES = (
    r"cmd\git.exe",
    r"mingw64\bin\git.exe",
    r"bin\git.exe",
    r"usr\bin\git.exe",
)

#: Package-manager shim directories, which contain a `git.exe` or a shim for one.
GIT_SHIM_DIRECTORIES = (
    r"%ProgramData%\chocolatey\bin",
    r"%USERPROFILE%\scoop\shims",
)


def expand_windows_vars(raw: str, env: dict | None = None) -> str:
    r"""Expand `%NAME%` against `env`, on any platform.

    `os.path.expandvars` only understands `%NAME%` when *running* on Windows, so
    relying on it makes this resolution both Windows-only and untestable
    elsewhere — and an untestable fallback is how the original PATH assumption
    shipped. Expanding explicitly means the behaviour that runs on the Handler's
    machine is the behaviour the tests exercise.
    """
    lookup = os.environ if env is None else env

    def substitute(match):
        name = match.group(1)
        return lookup.get(name, match.group(0))

    return re.sub(r"%([^%]+)%", substitute, raw)


def git_candidate_paths(env: dict | None = None) -> list[str]:
    """Every absolute path worth trying, in order, with variables expanded."""
    found: list[str] = []
    seen: set[str] = set()

    def add(raw: str) -> None:
        expanded = expand_windows_vars(raw, env)
        if "%" in expanded:                   # variable was not set on this box
            return
        if os.sep != chr(92):                 # POSIX: our templates use backslashes
            expanded = expanded.replace(chr(92), os.sep)
        key = expanded.lower()
        if key not in seen:
            seen.add(key)
            found.append(expanded)

    # Joined with the running platform's separator. On Windows that is a
    # backslash, which is what these paths are; on any other platform it makes
    # the resolution testable with a fake install tree rather than leaving the
    # fallback exercised only on the machine that has the problem.
    sep = os.sep

    def join(root: str, relative: str) -> str:
        return f"{root.rstrip(chr(92)).rstrip('/')}{sep}{relative.replace(chr(92), sep)}"

    for root in GIT_INSTALL_ROOTS:
        for relative in GIT_RELATIVE_BINARIES:
            add(join(root, relative))
    for shim in GIT_SHIM_DIRECTORIES:
        add(join(shim, "git.exe"))
    return found


#: Set this to point the job at a specific binary, for a machine with an unusual
#: install. `--git` overrides it.
GIT_ENV_VAR = "CHRONOS_GIT"

_GIT_BINARY: str | None = None
_GIT_SEARCHED: list[str] = []


def find_git(explicit: str | None = None, *, refresh: bool = False) -> str:
    """Locate `git`, or fail with a message that names every place we looked.

    Resolution order: explicit argument, then `$CHRONOS_GIT`, then PATH, then the
    standard install locations. The last step is what makes this work under Task
    Scheduler on a machine where git was never added to the system PATH.
    """
    global _GIT_BINARY, _GIT_SEARCHED
    if explicit:
        if not pathlib.Path(explicit).exists():
            raise GitUnavailable(
                f"{explicit!r} was given but does not exist")
        return explicit
    if _GIT_BINARY and not refresh:
        return _GIT_BINARY

    searched: list[str] = []
    from_env = os.environ.get(GIT_ENV_VAR)
    if from_env:
        searched.append(f"${GIT_ENV_VAR}={from_env}")
        if pathlib.Path(from_env).exists():
            _GIT_BINARY, _GIT_SEARCHED = from_env, searched
            return from_env

    on_path = shutil.which("git")
    searched.append(f"PATH -> {on_path or 'not found'}")
    if on_path:
        _GIT_BINARY, _GIT_SEARCHED = on_path, searched
        return on_path

    for candidate in git_candidate_paths():
        searched.append(candidate)
        if pathlib.Path(candidate).exists():
            _GIT_BINARY, _GIT_SEARCHED = candidate, searched
            return candidate

    _GIT_SEARCHED = searched
    windows_paths = git_candidate_paths()
    lines = ["could not find git. Looked in:"]
    lines += [f"    {entry}" for entry in searched]
    if windows_paths:
        lines += ["  Standard install locations:",
                  *(f"    {entry}" for entry in windows_paths)]
    else:
        lines.append("  Standard install locations: not checked - no Windows "
                     "install variable (ProgramFiles, LOCALAPPDATA, ...) is set")
    lines.append(f"  PATH was: {os.environ.get('PATH') or '(empty)'}")
    lines.append(f"  Fix: pass --git <path to git.exe>, or set {GIT_ENV_VAR}.")
    lines.append("  (A scheduled task gets the machine PATH, not Git Bash's.)")
    raise GitUnavailable("\n".join(lines))


def set_git_binary(path: str | None) -> None:
    """Pin the binary for tests and for `--git`. `None` re-detects."""
    global _GIT_BINARY, _GIT_SEARCHED
    _GIT_BINARY = path
    _GIT_SEARCHED = []


def describe_failure(exc: BaseException, *, operation: str,
                     repo: pathlib.Path) -> str:
    """Everything needed to diagnose this without a traceback.

    A scheduled task has no one watching a console, so the log line has to carry
    the context a traceback would: what was attempted, on what, from where, and
    with what environment. `FileNotFoundError: [WinError 2]` on its own is the
    single least useful message this job can produce — it cannot distinguish a
    missing executable from a missing working directory, because on Windows both
    say exactly that.
    """
    lines = [f"{operation}: {type(exc).__name__}: {exc}"]
    filename = getattr(exc, "filename", None)
    winerror = getattr(exc, "winerror", None)
    if filename:
        lines.append(f"  involved path : {filename}")
    if winerror is not None:
        lines.append(f"  winerror      : {winerror}")
    lines.append(f"  repository    : {repo}"
                 + ("" if repo.is_dir() else "   (does not exist)"))
    binary = _GIT_BINARY or "(none resolved)"
    lines.append(f"  git binary    : {binary}"
                 + ("" if pathlib.Path(binary).exists() or binary == "(none resolved)"
                    else "   (does not exist)"))
    if _GIT_SEARCHED:
        lines.append(f"  searched      : {'; '.join(_GIT_SEARCHED)}")
    return "\n".join(lines)


def _run(repo: pathlib.Path, *args: str) -> subprocess.CompletedProcess:
    """Run one git command. Never raises for a non-zero exit — the caller reads it."""
    global _GIT_SEARCHED
    operation = f"git {' '.join(args)}"
    # Check the working directory first: it is a local, unambiguous fault, and
    # reporting "could not find git" when the real problem is a bad repo path
    # would send someone looking in the wrong place.
    if not repo.is_dir():
        raise GitUnavailable(describe_failure(
            FileNotFoundError(2, "the repository directory does not exist"),
            operation=operation, repo=repo))
    binary = find_git()
    try:
        return subprocess.run(
            [binary, *args], cwd=str(repo), capture_output=True, text=True,
            timeout=GIT_TIMEOUT_SECONDS, check=False,
        )
    except OSError as exc:
        # Includes the FileNotFoundError that started this: whatever its cause,
        # the log now names the binary, the working directory and the search.
        raise GitUnavailable(
            describe_failure(exc, operation=operation, repo=repo)) from exc


#: Git failures that mean "try again in a moment", not "this will never work".
#:
#: The first group is lock contention. `.git/index.lock` exists only while
#: another git process holds the index — and on a OneDrive-synced tree a sync
#: pass can hold `.git/` itself, which is the same class of problem that broke
#: the mirror's state save. `git add` and `git commit` both die on it with
#: "Unable to create ... File exists", and without a retry that costs a whole
#: night: the job reports failure and the next run picks it up.
#:
#: The second group is the transient network. The Handler has occasional drops;
#: a push that fails because the link blinked is not a push that needs a human.
TRANSIENT_GIT_MARKERS = (
    "index.lock",
    "unable to create",
    "could not lock",
    "another git process",
    "could not resolve host",
    "failed to connect",
    "connection timed out",
    "connection reset",
    "connection refused",
    "remote end hung up",
    "unable to access",
    "operation timed out",
    "tls",
    "network is unreachable",
)

#: Never retried: these need a person, and retrying only delays the report.
FATAL_GIT_MARKERS = (
    "authentication failed",
    "permission denied",
    "could not read username",
    "repository not found",
    "non-fast-forward",
    "[rejected]",
)

#: Four attempts over ~1.8 s of backoff. Git lock contention lasts about as long
#: as one sync pass; a network blink longer than this is the next run's problem,
#: which is already handled.
GIT_RETRY_ATTEMPTS = 4
GIT_RETRY_BACKOFF = (0.1, 0.4, 1.3)


def _is_transient_git_failure(text: str) -> bool:
    lowered = (text or "").lower()
    if any(marker in lowered for marker in FATAL_GIT_MARKERS):
        return False
    return any(marker in lowered for marker in TRANSIENT_GIT_MARKERS)


def _run_git(repo: pathlib.Path, *args: str, sleep=time.sleep,
             attempts: int = GIT_RETRY_ATTEMPTS) -> tuple[subprocess.CompletedProcess, int]:
    """Run git, retrying the failures that clear on their own.

    Returns `(completed_process, attempts_used)`. A non-zero exit after the last
    attempt is returned, not raised: the caller decides what it means.
    """
    schedule = [*GIT_RETRY_BACKOFF, None][:attempts]
    used = 0
    done = _run(repo, *args)
    for delay in schedule:
        used += 1
        if done.returncode == 0:
            return done, used
        if not _is_transient_git_failure(done.stderr + done.stdout):
            return done, used
        if delay is None:
            break
        sleep(delay)
        done = _run(repo, *args)
    return done, used


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

    added, attempts = _run_git(repo, "add", "--",
                               *[_stage_name(path) for path in csvs])
    if added.returncode != 0:
        report(f"FAILED to stage {logs_dir}: {added.stderr.strip()}", ok=False)
        result.message = "git add failed"
        return result

    staged = _run(repo, "diff", "--cached", "--name-only")
    if staged.stdout.strip():
        commit, attempts = _run_git(repo, "commit", "-m", message)
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

    pushed, attempts = _run_git(repo, "push", "origin", branch)
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
    report(f"pushed {branch}" + (f" (after {attempts} attempts)" if attempts > 1 else ""),
           ok=True)
    result.message = "pushed"
    return result


def doctor(repo: pathlib.Path, logs_dir: pathlib.Path) -> int:
    """Print what this job can and cannot see, from the environment it is in.

    Written for one specific question: *why does this work when I run it and not
    from Task Scheduler?* Running the task with `--doctor` answers it in the
    scheduled context itself, which is the only context that matters — a
    scheduled task inherits the machine PATH, not the one a Git Bash session
    builds for itself, and nothing on the interactive side can reveal that.
    """
    print("chronos snapshot doctor")
    print(f"  python        : {sys.executable}")
    print(f"  python version: {sys.version.split()[0]}")
    print(f"  platform      : {sys.platform}")
    print(f"  working dir   : {os.getcwd()}")
    print(f"  repository    : {repo}" + ("" if repo.is_dir() else "  <-- MISSING"))
    print(f"  logs dir      : {logs_dir}"
          + ("" if logs_dir.is_dir() else "  <-- MISSING (no day files yet?)"))

    on_path = shutil.which("git")
    print(f"  git on PATH   : {on_path or '<-- NOT FOUND'}")
    try:
        # `--git` pins a binary; re-detecting would ignore the answer the
        # operator just gave.
        binary = find_git() if _GIT_BINARY else find_git(refresh=True)
        print(f"  git resolved  : {binary}")
        if _GIT_SEARCHED:
            print(f"  via           : {'; '.join(_GIT_SEARCHED)}")
        ok = True
    except GitUnavailable as exc:
        print(f"  git resolved  : NO -- {exc}")
        ok = False

    path = os.environ.get("PATH") or ""
    entries = path.split(os.pathsep) if path else []
    print(f"  PATH entries  : {len(entries)}")
    for entry in entries[:8]:
        print(f"      {entry}")
    if not path:
        print("      <-- PATH is empty")
    elif len(entries) > 8:
        print(f"      ... {len(entries) - 8} more")

    # Every install location searched, with whether it is actually there. This
    # is the line that turns "it failed" into "git is at X, add it to PATH" or
    # "git is in none of these places, install it or pass --git".
    print("  install paths :")
    for candidate in git_candidate_paths():
        mark = "found" if pathlib.Path(candidate).exists() else "     "
        print(f"      [{mark}] {candidate}")
    if os.environ.get(GIT_ENV_VAR):
        print(f"  {GIT_ENV_VAR}   : {os.environ[GIT_ENV_VAR]}")

    if ok and repo.is_dir():
        branch = current_branch(repo)
        print(f"  branch        : {branch}")
        if branch in FORBIDDEN_BRANCHES:
            print("      <-- the job will refuse to push from this branch")
    return 0 if ok else 1


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
    ap.add_argument("--git", default=None, metavar="PATH",
                    help="path to git.exe, for a machine where git is not on PATH")
    ap.add_argument("--doctor", action="store_true",
                    help="print what this job can see (git, PATH, repo) and exit; "
                         "run it as the scheduled task to diagnose PATH problems")
    args = ap.parse_args(argv)

    if args.git:
        set_git_binary(args.git)
    repo = pathlib.Path(args.repo)
    logs_dir = pathlib.Path(args.logs_dir) if args.logs_dir else repo / "logs"
    if args.doctor:
        return doctor(repo, logs_dir)

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
        result = snapshot(day, repo=repo, logs_dir=logs_dir,
                          push=not args.no_push, dry_run=args.dry_run)
    except GitUnavailable as exc:                     # a fault, with context
        message = f"FAILED: {exc}"
        print(message, file=sys.stderr)
        log_line(logs_dir, message)
        return 1
    except Exception as exc:                          # never escape a scheduled job
        message = describe_failure(exc, operation="the snapshot run",
                                   repo=repo)
        print(message, file=sys.stderr)
        log_line(logs_dir, message)
        return 1
    return 0 if result.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
