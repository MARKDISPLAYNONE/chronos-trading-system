"""
Project Chronos — Phase 2 live DST invariant monitor
====================================================

A **separate background process** that watches the broker clock continuously
through the US fall-back transition (Sunday 1 Nov 2026, 02:00 NY). It is the
empirical test Phase 1 could not run: the historical export ends 2026-10-22, so
only the spring transition was ever exercised.

It never blocks, and is never blocked by, the dry-run runner. If it dies the
runner keeps running; if the runner dies the monitor keeps logging. Both write
append-only files under `bot/live/output/`.

WHY A SEPARATE PROCESS
----------------------
The transition happens at 02:00 Sunday, when the venue is shut. A monitor
embedded in the runner would be idle exactly when the interesting thing
happened, and any crash in the monitor would look like a runner crash. Running
independently means its evidence survives a runner failure — which is the whole
point of collecting evidence.

WHAT IT ACTUALLY MEASURES
-------------------------
Per sample, three independent things:

1. **The broker's UTC offset, measured directly.** `tick.time - time.time()`,
   gated on tick freshness (TRD §4.1.1 rule 5: a stale tick measures how long
   the venue has been shut, not the offset). This is the *only* measurement that
   can see the switch itself, because the venue is closed across 02:00 and no
   bar ever exists inside the repeated hour.
2. **The four session invariants**, reusing `verify_broker_timezone.verify()`
   verbatim — the same reference implementation Phase 1 gates on, applied to the
   rolling live window rather than an offline CSV.
3. **Schedule integrity**: for each trading date in the window, that the
   21-window schedule still yields exactly 21 distinct labels. A DST bug that
   duplicated or dropped a local hour would show up here as 20 or 22.

It also records every gap and every back-step between consecutive NY minutes,
because a *repeated* local hour would appear as a back-step and a *skipped* one
as an over-long gap. Those two are printed explicitly around the transition.

WHAT IT DOES NOT DO
-------------------
No orders. No database. No decisions. It observes and appends. It never adjusts
the offset to make a check pass — if an invariant fails, that is the finding.

HOW TO RUN
----------
    python live/dst_monitor.py                 # until Ctrl-C, 60 s samples
    python live/dst_monitor.py --once          # one sample, for a smoke test
    python live/dst_monitor.py --sample-seconds 30 --out-dir output

Exit code 0 on a clean `--once` sample; 1 if a check failed or the terminal
could not be reached (so it can be used as a pre-flight gate).
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import importlib.util
import pathlib
import sys
import time
import traceback
from dataclasses import asdict, dataclass, field

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from src import config  # noqa: E402
from src import time_windows  # noqa: E402

#: Kept in sync with probe_mt5_live.MAX_BROKER_UTC_OFFSET_HOURS: the broker runs
#: UTC+2 (winter) / UTC+3 (summer), and staleness is judged on the generous end.
MAX_BROKER_UTC_OFFSET_HOURS = 3

CSV_COLUMNS = [
    "sample_utc",
    "ny_wall",
    "broker_label",
    "measured_offset_hours",
    "tick_age_seconds",
    "venue_open",
    "bars_in_window",
    "window_first_ny",
    "window_last_ny",
    "gap_count",
    "largest_gap_minutes",
    "backstep_count",
    "invariant_all_passed",
    "invariant_failures",
    "schedule_dates_checked",
    "schedule_bad_dates",
    "transition_proximity_hours",
    "notes",
]


# ---------------------------------------------------------------------------
# The reference implementation, loaded by path
# ---------------------------------------------------------------------------
def load_verifier():
    """Import `verify_broker_timezone.py` from its directory.

    It is a script, not a package module, and re-implementing its checks here
    would defeat the purpose: the monitor must apply *the same* rules Phase 1
    gates on, so that a disagreement between them is impossible by construction.
    """
    path = (pathlib.Path(__file__).resolve().parents[1]
            / "historical_validation" / "verify_broker_timezone.py")
    spec = importlib.util.spec_from_file_location("_chronos_verify_tz", path)
    if spec is None or spec.loader is None:      # pragma: no cover
        raise RuntimeError(f"cannot load the timezone verifier from {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ---------------------------------------------------------------------------
# Pure helpers (all unit-tested offline)
# ---------------------------------------------------------------------------
def tick_age_seconds(tick_epoch: int, now_utc: float) -> float:
    """Age of the last tick, in the most generous direction (see probe)."""
    freshest_possible_utc = tick_epoch - MAX_BROKER_UTC_OFFSET_HOURS * 3600
    return now_utc - freshest_possible_utc


def venue_open_at(ny_now: dt.datetime) -> bool:
    """Is the venue trading at this NY wall-clock instant?

    Holidays are not modelled (we have no holiday calendar for 2026 live), so
    this can say "open" on a holiday. It is used only to contextualise a stale
    tick, never to decide that a window should have traded.
    """
    wd, hm = ny_now.weekday(), ny_now.strftime("%H:%M")
    if wd == 5:
        return False
    if wd == 6:
        return hm >= "18:00"
    if hm < "17:00":
        return True
    if hm < "18:00":
        return False
    if wd == 4:
        return False
    return True


def schedule_integrity(days: list[dt.date]) -> tuple[list[str], list[str]]:
    """Per-date window-schedule check: 21 windows, 21 distinct labels.

    This is the arithmetic that a DST bug breaks. If a local hour were skipped
    the schedule would shrink; if repeated, the labels would collide. Both are
    caught here before they can reach a report as a mysterious missing row.
    """
    checked: list[str] = []
    bad: list[str] = []
    for day in days:
        if not time_windows.is_trading_day(day):
            continue
        checked.append(day.isoformat())
        windows = time_windows.windows_for_day(day)
        labels = [w.label for w in windows]
        ends = [w.end for w in windows]
        if len(windows) != 21 or len(set(labels)) != 21 or len(set(ends)) != 21:
            bad.append(
                f"{day.isoformat()}: {len(windows)} windows, "
                f"{len(set(labels))} distinct labels"
            )
        # A window's close must be strictly after its open, and the 23:45 window
        # must cross midnight — the one case where a date-arithmetic slip shows.
        if any(w.end <= w.start for w in windows):
            bad.append(f"{day.isoformat()}: a window does not end after it starts")
    return checked, bad


def local_time_anomalies(ny_times: list[dt.datetime]) -> tuple[int, int, int]:
    """Count back-steps and over-long gaps between consecutive NY minutes.

    Returns `(backsteps, gaps, largest_gap_minutes)`.

    * A **back-step** (next <= prev) is what a *repeated* local hour looks like:
      the conversion has mapped two different instants onto the same NY minute,
      or onto one earlier. Any occurrence is a finding.
    * A **gap** is normal on a closed market (the 17:00-18:00 break, the
      weekend), so gaps are counted and reported, not failed. What matters is
      where they are: a gap that is *not* a known closure is worth a human look.
    """
    backsteps = 0
    gaps = 0
    largest = 0
    for prev, cur in zip(ny_times, ny_times[1:]):
        delta = (cur - prev).total_seconds() / 60.0
        if delta <= 0:
            backsteps += 1
        elif delta > 1.0:
            gaps += 1
            largest = max(largest, int(delta))
    return backsteps, gaps, largest


def transition_proximity_hours(ny_now: dt.datetime, transition: dt.datetime) -> float:
    """Signed hours from the next/nearest DST transition (for the log)."""
    return (ny_now - transition).total_seconds() / 3600.0


def nearest_transition(ny_now: dt.datetime) -> dt.datetime:
    """The US DST transition nearest to `ny_now`, in NY wall clock.

    Hard-coded to the documented rule (2nd Sunday March, 1st Sunday November,
    02:00 local) — the same dates TRD §4.1.1 states the broker follows. Used for
    reporting only; nothing in the conversion depends on it.
    """
    year = ny_now.year
    candidates = [_nth_sunday(year, 3, 2), _nth_sunday(year, 11, 1)]
    candidates += [_nth_sunday(year - 1, 11, 1), _nth_sunday(year + 1, 3, 2)]
    return min(candidates, key=lambda c: abs((ny_now - c).total_seconds()))


def _nth_sunday(year: int, month: int, n: int) -> dt.datetime:
    """02:00 NY on the n-th Sunday of a month."""
    day = dt.date(year, month, 1)
    offset = (6 - day.weekday()) % 7          # first Sunday
    return dt.datetime.combine(day + dt.timedelta(days=offset + 7 * (n - 1)),
                               dt.time(2, 0))


# ---------------------------------------------------------------------------
# One sample
# ---------------------------------------------------------------------------
@dataclass
class Sample:
    sample_utc: str = ""
    ny_wall: str = ""
    broker_label: str = ""
    measured_offset_hours: str = ""
    tick_age_seconds: str = ""
    venue_open: str = ""
    bars_in_window: int = 0
    window_first_ny: str = ""
    window_last_ny: str = ""
    gap_count: int = 0
    largest_gap_minutes: int = 0
    backstep_count: int = 0
    invariant_all_passed: str = ""
    invariant_failures: str = ""
    schedule_dates_checked: int = 0
    schedule_bad_dates: str = ""
    transition_proximity_hours: str = ""
    notes: str = ""

    def as_row(self) -> dict:
        return {k: ("" if v is None else v) for k, v in asdict(self).items()}


class Monitor:
    """Collects one sample at a time. All MT5 access lives here."""

    def __init__(self, mt5, symbol: str = config.INSTRUMENT_SYMBOL,
                 *, history_bars: int = config.LIVE_HISTORY_BARS,
                 lookback_days: int = 6,
                 now_fn=None) -> None:
        self._mt5 = mt5
        self.symbol = symbol
        self.history_bars = history_bars
        self.lookback_days = lookback_days
        self._now_fn = now_fn or time.time
        self._verifier = load_verifier()
        self.failures: list[str] = []

    # -- helpers ----------------------------------------------------------
    def _ny_now(self, now_utc: float) -> dt.datetime:
        from zoneinfo import ZoneInfo

        aware = dt.datetime.fromtimestamp(now_utc, dt.timezone.utc)
        return aware.astimezone(ZoneInfo(config.NY_TZ)).replace(tzinfo=None)

    def _broker_label_now(self, ny_now: dt.datetime) -> dt.datetime:
        return ny_now + dt.timedelta(hours=config.BROKER_OFFSET_HOURS)

    # -- the sample -------------------------------------------------------
    def sample(self) -> Sample:
        s = Sample()
        self.failures = []
        now_utc = self._now_fn()
        ny_now = self._ny_now(now_utc)
        s.sample_utc = dt.datetime.fromtimestamp(now_utc, dt.timezone.utc).isoformat()
        s.ny_wall = ny_now.isoformat(sep=" ")
        s.broker_label = self._broker_label_now(ny_now).isoformat(sep=" ")
        s.venue_open = str(venue_open_at(ny_now))
        s.transition_proximity_hours = f"{transition_proximity_hours(ny_now, nearest_transition(ny_now)):+.1f}"

        notes: list[str] = []

        # --- 1. measured offset, freshness-gated --------------------------
        tick = self._mt5.symbol_info_tick(self.symbol)
        tick_stamp = getattr(tick, "time", None) if tick is not None else None
        if tick_stamp:
            age = tick_age_seconds(int(tick_stamp), now_utc)
            s.tick_age_seconds = f"{age:.0f}"
            if age > config.STALE_TICK_SECONDS:
                age_h = age / 3600.0
                s.measured_offset_hours = ""
                notes.append(
                    f"market closed — cannot measure live offset, last tick is "
                    f"{age_h:.1f}h old"
                )
                if venue_open_at(ny_now):
                    notes.append("venue should be OPEN — feed liveness problem")
                    self.failures.append("stale tick while the venue should be open")
            else:
                s.measured_offset_hours = f"{(int(tick_stamp) - now_utc) / 3600.0:+.3f}"
        else:
            notes.append("no tick timestamp available")
            self.failures.append("symbol_info_tick gave no timestamp")

        # --- 2. the four invariants, on a rolling live window --------------
        rates = self._mt5.copy_rates_from_pos(
            self.symbol, self._mt5.TIMEFRAME_M1, 0, self.history_bars
        )
        bars = self._to_verifier_bars(rates)
        s.bars_in_window = len(bars)
        if not bars:
            notes.append("no M1 bars returned")
            self.failures.append("no bars returned — invariants cannot be checked")
            s.invariant_all_passed = "False"
            s.notes = " | ".join(notes)
            return s

        ny = [self._verifier.new_york_wall_clock(t) for t, *_ in bars]
        s.window_first_ny = ny[0].isoformat(sep=" ")
        s.window_last_ny = ny[-1].isoformat(sep=" ")

        ok, rows = self._verifier.verify(bars)
        failed = [f"{name} ({detail})" for name, passed, detail in rows if not passed]
        s.invariant_all_passed = str(ok)
        s.invariant_failures = "; ".join(failed)
        if not ok:
            self.failures.append(f"session invariants failed: {'; '.join(failed)}")
        # A check that could not run must not look like one that ran and passed.
        # The detail text says which boundary the window could not see; put it in
        # the row so the audit trail records the vacuity rather than hiding it.
        skipped = [name for name, passed, detail in rows
                   if passed and detail.startswith("not applicable")]
        if skipped:
            notes.append(
                "weekend invariants not applicable in this window: "
                + ", ".join(skipped)
            )

        # --- 3. local-hour anomalies + schedule integrity ------------------
        backsteps, gaps, largest = local_time_anomalies(ny)
        s.backstep_count = backsteps
        s.gap_count = gaps
        s.largest_gap_minutes = largest
        if backsteps:
            self.failures.append(
                f"{backsteps} back-step(s) in NY minutes — a repeated local hour"
            )

        days = sorted({n.date() for n in ny})
        checked, bad = schedule_integrity(days)
        s.schedule_dates_checked = len(checked)
        s.schedule_bad_dates = "; ".join(bad)
        if bad:
            self.failures.append(f"schedule integrity: {'; '.join(bad)}")

        s.notes = " | ".join(notes)
        return s

    def _to_verifier_bars(self, rates) -> list[tuple[int, float, float, float, float, int]]:
        """Convert an MT5 rates array into the verifier's tuple format."""
        if rates is None or len(rates) == 0:
            return []
        names = getattr(getattr(rates, "dtype", None), "names", None)
        vol_field = None
        for candidate in ("tick_volume", "real_volume", "volume"):
            if names and candidate in names:
                vol_field = candidate
                break
        out = []
        for row in rates:
            try:
                if names:
                    t = int(row[names.index("time")]) if not isinstance(row, dict) else int(row["time"])
                    o = float(row[names.index("open")])
                    h = float(row[names.index("high")])
                    l = float(row[names.index("low")])
                    c = float(row[names.index("close")])
                    v = int(float(row[names.index(vol_field)])) if vol_field else 0
                else:                       # pragma: no cover - defensive
                    t, o, h, l, c, v = int(row[0]), *map(float, row[1:5]), int(float(row[5]))
                out.append((t, o, h, l, c, v))
            except Exception:               # pragma: no cover - skip malformed rows
                continue
        return out


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------
class AppendOnlyCsv:
    """Append-only CSV with a header written once. Never rewrites, never sorts."""

    def __init__(self, path: pathlib.Path, columns: list[str]) -> None:
        self.path = path
        self.columns = columns

    def write(self, row: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        new = not self.path.exists() or self.path.stat().st_size == 0
        with self.path.open("a", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=self.columns, extrasaction="ignore")
            if new:
                writer.writeheader()
            writer.writerow({c: row.get(c, "") for c in self.columns})


def _log(line: str) -> None:
    print(line, flush=True)


def run(mt5, *, out_dir: pathlib.Path, sample_seconds: int, once: bool,
        monitor: Monitor | None = None) -> int:
    monitor = monitor or Monitor(mt5)
    csv_out = AppendOnlyCsv(out_dir / "dst_monitor.csv", CSV_COLUMNS)
    _log("=" * 96)
    _log("Project Chronos — Phase 2 live DST monitor (read-only)")
    _log(f"sample every {sample_seconds}s; output {csv_out.path}")
    _log("watching for the US fall-back transition: Sun 2026-11-01 02:00 NY")
    _log("=" * 96)
    last_failures = None
    while True:
        try:
            s = monitor.sample()
            csv_out.write(s.as_row())
            flag = "PASS" if s.invariant_all_passed == "True" and not monitor.failures else "FAIL"
            offset = s.measured_offset_hours + "h" if s.measured_offset_hours else "n/a"
            _log(
                f"[{flag}] {s.ny_wall} NY | broker {s.broker_label} | offset {offset}"
                f" | tick {s.tick_age_seconds or '?'}s | bars {s.bars_in_window}"
                f" | backsteps {s.backstep_count} | gaps {s.gap_count}"
                f" | schedule {s.schedule_dates_checked}d"
                f"{' | ' + s.notes if s.notes else ''}"
            )
            for f in monitor.failures:
                _log(f"        !! {f}")
            # Only shout when the failure set changes — a monitor that repeats
            # the same line every minute trains the operator to ignore it.
            if monitor.failures != last_failures:
                if monitor.failures:
                    _log("        ^^ failure set changed — investigate, do not patch offsets")
                elif last_failures:
                    _log("        ^^ recovered")
                last_failures = list(monitor.failures)
            if once:
                return 0 if not monitor.failures else 1
        except KeyboardInterrupt:
            _log("\nstopped by operator")
            return 0
        except Exception:
            _log("sample failed:")
            traceback.print_exc()
            if once:
                return 1
        try:
            time.sleep(sample_seconds)
        except KeyboardInterrupt:
            _log("\nstopped by operator")
            return 0


def main(argv: list[str] | None = None, mt5_module=None) -> int:
    ap = argparse.ArgumentParser(description="Phase 2 live DST invariant monitor")
    ap.add_argument("--symbol", default=config.INSTRUMENT_SYMBOL)
    ap.add_argument("--out-dir", default=str(pathlib.Path(__file__).resolve().parent / "output"))
    ap.add_argument("--sample-seconds", type=int, default=60)
    ap.add_argument("--once", action="store_true", help="take one sample and exit")
    args = ap.parse_args(argv)

    if mt5_module is not None:
        mt5 = mt5_module
    else:
        try:
            import MetaTrader5 as mt5  # noqa: N813
        except Exception as exc:
            print(f"FATAL: MetaTrader5 not importable ({type(exc).__name__}: {exc})")
            print("       Run this on the Windows machine that hosts the terminal.")
            return 1

    if not mt5.initialize():
        print(f"FATAL: initialize() failed — last_error={mt5.last_error()}")
        return 1
    try:
        return run(mt5, out_dir=pathlib.Path(args.out_dir),
                   sample_seconds=args.sample_seconds, once=args.once)
    finally:
        try:
            mt5.shutdown()
        except Exception:
            pass


if __name__ == "__main__":
    raise SystemExit(main())
