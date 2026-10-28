"""
Project Chronos — Phase 2 dry-run runner (NO ORDERS, EVER)
=========================================================

The live loop: ingest M1 bars from the terminal → evaluate each of the 21 daily
windows with the **unmodified** Phase 1 evaluator → append the result to CSV.
Nothing in this file, or in anything it imports, can reach an order function.
That is enforced by a test that walks the import graph, not by a flag.

THREE POLICIES, ALL FROM THE HANDLER, ALL IMPLEMENTED LITERALLY
---------------------------------------------------------------
**Q13 — when a row is written.** A NO_SETUP row is written at window close: it is
final, nothing can resolve later. A *traded* row is written only when
`simulate()` actually resolves the trade — which can be days later. Append-only
throughout; no replay, no back-fill. The cost is that file order is decision
order, not window order, and `evaluated_at` says so.

**Q14 — what a crash means.** If the runner dies, windows it did not observe are
logged `dry_run_gap_runner_down` and are **never** reconstructed from broker
history. The live log contains only what this process observed while running.
Phase 2 policy only: `docs/07-PHASE-PLAN.md` Phase 3 requires genuine crash
recovery with broker-side reconciliation, because by then real orders exist.

**Structural no-order guarantee.** No `order_send`, no `order_check`, no
`positions_*`, no `trade_request`, no import that provides them.

THE HARD PART, AND HOW IT IS ACTUALLY SOLVED
--------------------------------------------
When MT5 reconnects it *backfills* the minutes we were blind for. So a naive
implementation would silently do exactly what Q14 forbids: it would evaluate a
window using bars it never watched arrive. Missing bars in the buffer are not the
problem — the problem is bars that look present but were not observed live.

So the runner keeps an **observation ledger**: a list of wall-clock intervals
during which it was connected and polling, persisted across restarts. A window is
counted as observed only if its entire `[open, close)` span falls inside one of
those intervals. Anything else is a gap, *even if the bars exist*. That separates
the three cases cleanly:

| Situation | What it means | Logged as |
|---|---|---|
| Window outside the ledger | the runner was down | `NO_SETUP` / `dry_run_gap_runner_down` |
| Window inside the ledger, no bars | venue shut, or a feed hole | the evaluator's own `market_closed_no_data` / `missing_market_data` |
| Window inside the ledger, bars present | normal | normal evaluation |

DST: WINDOWS CANNOT DOUBLE-FIRE, AND THAT IS PROVEN AT STARTUP
--------------------------------------------------------------
The 21 windows are New York *wall-clock* slots. If a slot fell in the repeated
hour of a fall-back day it could fire twice, and if it fell in a spring-forward
gap it could not fire at all. So at startup the runner converts every scheduled
window in the planned range to UTC and **refuses to start** if any of them is
ambiguous (a doubled local time) or non-existent (a skipped one). Under the
Mon–Fri rule this can never happen — both US transitions fall on a Sunday — and
that is exactly why it is worth asserting rather than assuming: the assertion is
what turns "should be fine" into a checked fact. `--check` prints the result.

HOW TO RUN
----------
    python live/dry_run_runner.py --check                 # guards only, no loop
    python live/dry_run_runner.py                         # run until Ctrl-C
    python live/dry_run_runner.py --poll-seconds 20 --out-dir output
    python live/dry_run_runner.py --max-runtime-seconds 120   # smoke test

Run `dst_monitor.py` in a **separate** terminal at the same time. They share no
state and neither can block the other.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import pathlib
import signal
import sys
import time
import traceback
from dataclasses import dataclass, field
from zoneinfo import ZoneInfo

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from src import config  # noqa: E402
from src import outputs  # noqa: E402
from src import time_windows  # noqa: E402
from src.mt5_live_source import (  # noqa: E402
    LiveBarSource,
    LiveFeedError,
    broker_now,
    connect,
)
from src.window_evaluator import (  # noqa: E402
    OUTCOME_MISSING,
    REASON_DATA_ENDS,
    REASON_OUTSIDE_RANGE,
    WindowEvaluation,
    evaluate_window,
)

from live.csv_emitter import (  # noqa: E402
    REASON_LIVE_FEED_STALLED,
    REASON_RUNNER_DOWN,
    CsvEmitter,
    gap_evaluation,
    stall_evaluation,
)

NY = ZoneInfo(config.NY_TZ)
UTC = dt.timezone.utc

#: How far back gap-filling looks on the first-ever start, in hours, measured
#: from New York midnight of the start date. Deliberately under 24h: the point is
#: to complete the launch day honestly, not to invent a history.
GAP_LOOKBACK_HOURS = 26

#: How long a window may sit with an incomplete tail before the runner stops
#: treating it as "not yet" and calls it a feed stall. This is the ONLY place
#: that decision is made. It is a policy number, not a measurement: five minutes
#: is many poll cycles (so ordinary publication lag can never reach it) and far
#: shorter than any genuine market closure, which is what makes it a safe
#: boundary between a late bar and a dead feed.
DATA_LAG_GRACE_SECONDS = 300.0


# ---------------------------------------------------------------------------
# Window <-> UTC, with the DST guard
# ---------------------------------------------------------------------------
def window_utc_bounds(window: time_windows.Window) -> tuple[dt.datetime, dt.datetime, list[str]]:
    """Convert a NY wall-clock window to a UTC span, flagging DST pathologies.

    Returns `(start_utc, end_utc, flags)`. A flag means the local time does not
    map to exactly one instant, so the window must not be scheduled.
    """
    flags: list[str] = []

    def one(naive: dt.datetime, label: str) -> dt.datetime:
        aware, exists = time_windows.localize(naive)
        if not exists:
            flags.append(f"nonexistent_local_time_at_{label}")
        # Ambiguity: a wall clock that occurs twice has different UTC offsets
        # for fold=0 and fold=1. `localize` cannot see this (it returns the
        # first occurrence), so it is checked explicitly.
        a0 = naive.replace(tzinfo=NY, fold=0)
        a1 = naive.replace(tzinfo=NY, fold=1)
        if a0.utcoffset() != a1.utcoffset():
            flags.append(f"ambiguous_local_time_at_{label}")
        return aware.astimezone(UTC)

    start_utc = one(window.start, "start")
    end_utc = one(window.end, "end")
    if end_utc <= start_utc:
        flags.append("end_not_after_start")
    return start_utc, end_utc, flags


def assert_schedule_is_dst_safe(days: list[dt.date]) -> list[str]:
    """Every scheduled window must map to exactly one UTC span.

    Raises if not: a doubled or skipped local slot would double-fire or drop a
    window, which would corrupt the only evidence Phase 2 produces. Returns the
    human-readable lines it checked, for the log.
    """
    checked: list[str] = []
    problems: list[str] = []
    for day in days:
        if not time_windows.is_trading_day(day):
            continue
        for window in time_windows.windows_for_day(day):
            start_utc, end_utc, flags = window_utc_bounds(window)
            if flags:
                problems.append(f"{day.isoformat()} {window.label}: {', '.join(flags)}")
            minutes = (end_utc - start_utc).total_seconds() / 60.0
            if abs(minutes - 30.0) > 1e-9:
                problems.append(
                    f"{day.isoformat()} {window.label}: spans {minutes:.0f} min, expected 30"
                )
        checked.append(day.isoformat())
    if problems:
        raise RuntimeError(
            "DST-safe schedule check FAILED — these windows do not map to exactly "
            f"one 30-minute UTC span: {problems}. Refusing to start: a doubled or "
            "skipped local slot would produce a duplicate or missing evaluation."
        )
    return checked


def observed(intervals: list[tuple[float, float]], start_utc: float,
             end_utc: float, grace_seconds: float) -> bool:
    """Was `[start, end)` fully inside a single connected polling interval?"""
    for lo, hi in intervals:
        if lo - grace_seconds <= start_utc and hi + grace_seconds >= end_utc:
            return True
    return False


# ---------------------------------------------------------------------------
# Persistent state
# ---------------------------------------------------------------------------
@dataclass
class RunnerState:
    """Everything that must survive a crash. Small, explicit, JSON."""

    path: pathlib.Path
    first_start_utc: float | None = None
    last_heartbeat_utc: float | None = None
    observations: list[tuple[float, float]] = field(default_factory=list)
    pending: dict[str, dict] = field(default_factory=dict)
    #: Windows whose tail had not arrived when they closed. Keyed like
    #: `pending`; the value records when the deferral started, which is what
    #: DATA_LAG_GRACE_SECONDS is measured against.
    deferred: dict[str, dict] = field(default_factory=dict)

    @classmethod
    def load(cls, path: pathlib.Path) -> "RunnerState":
        state = cls(path=path)
        if not path.exists():
            return state
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            # A corrupt state file must not stop the run: the CSVs are the
            # source of truth for what was emitted, and the cost of losing the
            # ledger is extra gap rows (conservative), never fabricated data.
            return state
        state.first_start_utc = raw.get("first_start_utc")
        state.last_heartbeat_utc = raw.get("last_heartbeat_utc")
        state.observations = [tuple(x) for x in raw.get("observations", [])]
        state.pending = raw.get("pending", {})
        state.deferred = raw.get("deferred", {})
        return state

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "first_start_utc": self.first_start_utc,
            "last_heartbeat_utc": self.last_heartbeat_utc,
            "observations": [list(x) for x in self.observations],
            "pending": self.pending,
            "deferred": self.deferred,
            "saved_utc": dt.datetime.now(UTC).isoformat(),
        }
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        tmp.replace(self.path)          # atomic: a crash mid-write cannot corrupt


# ---------------------------------------------------------------------------
# The runner
# ---------------------------------------------------------------------------
@dataclass
class StepResult:
    refreshed: bool = False
    evaluated: int = 0
    emitted: int = 0
    pending: int = 0
    gaps: int = 0
    bars: int = 0
    note: str = ""


def window_key(window: time_windows.Window) -> str:
    return f"{window.trading_date.isoformat()}|{window.label}"


class DryRunRunner:
    """The Phase 2 loop. One call to `step()` is one poll cycle."""

    def __init__(
        self,
        mt5,
        out_dir: pathlib.Path,
        *,
        poll_seconds: int = 20,
        now_fn=None,
        source=None,
        emitter: CsvEmitter | None = None,
        symbol: str = config.INSTRUMENT_SYMBOL,
        grace_seconds: float = 120.0,
        history_bars: int = config.LIVE_HISTORY_BARS,
        log=print,
    ) -> None:
        self._mt5 = mt5
        self.out_dir = pathlib.Path(out_dir)
        self.poll_seconds = poll_seconds
        self._now_fn = now_fn or time.time
        self._injected_source = source
        self.source = source
        self.emitter = emitter or CsvEmitter(self.out_dir)
        self.symbol = symbol
        self.grace_seconds = grace_seconds
        self.history_bars = history_bars
        self._log = log
        self.state = RunnerState.load(self.out_dir / "runner_state.json")
        self.connected = False
        self._gap_scan_from: float | None = None
        self.steps = 0
        self.evaluations_total = 0
        self._warned_empty = False

    # -- lifecycle --------------------------------------------------------
    def start(self, *, connect_terminal: bool = True) -> dict:
        """Connect, run the guards, reconcile the previous run. Idempotent-ish."""
        now = self._now_fn()
        if self.state.first_start_utc is None:
            self.state.first_start_utc = now

        facts: dict = {}
        if connect_terminal and self._injected_source is None:
            facts = connect(self._mt5, require_demo=True)

        if self.source is None:
            self.source = self._injected_source or LiveBarSource(
                self._mt5, self.symbol, history_bars=self.history_bars,
                now_fn=self._now_fn,
            )

        # --- guards: DST-safe schedule + trading-day coverage ---------------
        days = self._days_in_planned_range(now)
        checked = assert_schedule_is_dst_safe(days)
        facts["dst_safe_dates"] = len(checked)

        self._open_observation(now)
        self.connected = True
        self._gap_scan_from = self._gap_scan_lower_bound(now)

        self.emitter.event(
            "start",
            f"runner up; {facts.get('dst_safe_dates', len(checked))} schedule dates "
            f"verified DST-safe; poll {self.poll_seconds}s; "
            f"symbol {self.symbol}; bars {len(self.source.series)}",
            when=broker_now_ny(now),
        )
        self._log(
            f"started: {len(self.source.series)} bars, "
            f"{len(checked)} trading dates verified DST-safe"
        )

        # --- reconcile anything that was pending when we died (Q14) ---------
        self._reconcile_pending_after_crash(now)
        self.state.save()
        return facts

    def step(self) -> StepResult:
        """One poll cycle: refresh, evaluate due windows, advance pending, gap-fill."""
        result = StepResult()
        now = self._now_fn()

        try:
            report = self.source.refresh()
            result.refreshed = True
            result.bars = report.total_bars
            if report.has_problems:
                self.emitter.event(
                    "feed_anomaly", report.summary(), when=broker_now_ny(now)
                )
        except Exception as exc:
            # A dead feed closes the observation interval. Everything that
            # closes while it is closed becomes a gap, by construction.
            self._log(f"feed error: {type(exc).__name__}: {exc}")
            self.emitter.event(
                "feed_error", f"{type(exc).__name__}: {exc}", when=broker_now_ny(now)
            )
            self._close_observation(now)
            self.connected = False
            self.state.save()
            result.note = "feed_error"
            return result

        if not self.connected:
            self._open_observation(now)
            self.connected = True
            self.emitter.event("reconnect", "feed restored", when=broker_now_ny(now))
            self._log("feed restored")

        self.state.last_heartbeat_utc = now
        series = self.source.series

        # 1. windows that closed while we were watching
        result.evaluated, result.emitted = self._evaluate_due(series, now)
        # 2. windows held because their tail bars were late
        result.emitted += self._retry_deferred(series, now)
        # 3. windows whose trade may have resolved since the last poll
        resolved, emitted_now = self._advance_pending(series, now)
        result.pending = resolved
        result.emitted += emitted_now
        # 4. anything still unaccounted for was not observed
        result.gaps = self._fill_gaps(now)

        self.evaluations_total += result.evaluated
        self.steps += 1
        self.state.save()
        if result.evaluated or result.emitted or result.gaps or resolved:
            self._log(
                f"step {self.steps}: evaluated {result.evaluated}, "
                f"emitted {result.emitted}, pending {resolved}, gaps {result.gaps}, "
                f"bars {result.bars}"
            )
        return result

    def run(self, *, max_seconds: float | None = None) -> int:
        started = self._now_fn()
        try:
            while True:
                try:
                    self.step()
                except Exception:
                    self._log("step failed (continuing):")
                    traceback.print_exc()
                    self.emitter.event(
                        "step_error", traceback.format_exc(limit=2),
                        when=broker_now_ny(self._now_fn()),
                    )
                if max_seconds is not None and self._now_fn() - started >= max_seconds:
                    break
                time.sleep(self.poll_seconds)
        except KeyboardInterrupt:
            pass
        finally:
            self.stop()
        return 0

    def stop(self) -> None:
        now = self._now_fn()
        self._close_observation(now)
        self.state.save()
        self.emitter.event(
            "stop",
            f"clean shutdown after {self.steps} steps, "
            f"{self.evaluations_total} evaluations",
            when=broker_now_ny(now),
        )
        self._log(f"stopped after {self.steps} steps")

    # -- observation ledger ----------------------------------------------
    def _open_observation(self, now: float) -> None:
        if self.state.observations and self.state.observations[-1][1] is None:
            return
        self.state.observations.append((now, None))  # type: ignore[arg-type]

    def _close_observation(self, now: float) -> None:
        if not self.state.observations:
            return
        lo, hi = self.state.observations[-1]
        if hi is None:
            self.state.observations[-1] = (lo, now)

    def _intervals(self, now: float) -> list[tuple[float, float]]:
        """Closed intervals, with the currently-open one extended to `now`."""
        out: list[tuple[float, float]] = []
        for lo, hi in self.state.observations:
            out.append((lo, now if hi is None else hi))
        return out

    def _gap_scan_lower_bound(self, now: float) -> float:
        """Where gap-filling starts counting from.

        On a restart: the last heartbeat — anything after it was unobserved.
        On the very first start: New York midnight of the start date, so the
        launch day is logged completely (the windows before launch are gaps,
        honestly recorded) without inventing a history before the run existed.
        """
        if self.state.last_heartbeat_utc:
            return self.state.last_heartbeat_utc
        ny_day = dt.datetime.fromtimestamp(now, UTC).astimezone(NY).date()
        midnight_ny = dt.datetime.combine(ny_day, dt.time(0, 0), tzinfo=NY)
        # New York midnight of the start date: the launch day is completed
        # honestly, nothing before it is invented. `max` is belt-and-braces so
        # the lookback can never reach further than one day back.
        return max(midnight_ny.astimezone(UTC).timestamp(),
                   now - GAP_LOOKBACK_HOURS * 3600)

    # -- window iteration -------------------------------------------------
    def _days_in_planned_range(self, now: float) -> list[dt.date]:
        ny_today = dt.datetime.fromtimestamp(now, UTC).astimezone(NY).date()
        lo = ny_today - dt.timedelta(days=3)
        hi = ny_today + dt.timedelta(days=10)     # covers the planned 26 Oct-3 Nov window
        return time_windows.trading_days(lo, hi)

    def _windows_closing_between(self, lo_utc: float, hi_utc: float):
        """Yield `(window, start_utc, end_utc)` for windows closing in `[lo, hi)`."""
        lo_day = dt.datetime.fromtimestamp(lo_utc, UTC).astimezone(NY).date()
        hi_day = dt.datetime.fromtimestamp(hi_utc, UTC).astimezone(NY).date()
        for day in time_windows.trading_days(lo_day - dt.timedelta(days=1),
                                             hi_day + dt.timedelta(days=1)):
            for window in time_windows.windows_for_day(day):
                start_utc, end_utc, _flags = window_utc_bounds(window)
                close = end_utc.timestamp()
                if lo_utc <= close < hi_utc:
                    yield window, start_utc.timestamp(), close

    # -- emission ---------------------------------------------------------
    def _guard_live_reason(self, ev: WindowEvaluation) -> None:
        """`outside_data_range` is a backtest concept and must never be written.

        In a file, "the data stops here" is a fact. In a live feed the series can
        only grow, so the same comparison means "the bars have not arrived yet",
        which is a transient condition and not a verdict about the window. If a
        path ever tries to emit it, that is a bug in the runner, and it fails
        loudly here rather than being silently recorded as a market fact.
        """
        if ev.reason == REASON_OUTSIDE_RANGE:
            raise AssertionError(
                f"refusing to emit {REASON_OUTSIDE_RANGE!r} for "
                f"{ev.trading_date} {ev.window_label}: the live feed is not a file "
                "and 'no data yet' is not 'no data ever'"
            )

    def _emit_checked(self, ev: WindowEvaluation, now: float,
                      note: str = "") -> bool:
        """The only way a row reaches disk."""
        self._guard_live_reason(ev)
        return self.emitter.emit(ev, emit_time=self._ny_now(now), note=note)

    def _defer(self, key: str, ev: WindowEvaluation, now: float) -> None:
        """Park a window whose own bars had not arrived when it closed.

        Not an outcome, not a gap: the runner was watching, the data is simply
        late. It is re-examined every poll and only becomes a row once either the
        bars land (normal evaluation) or DATA_LAG_GRACE_SECONDS passes (a stall).
        """
        if key in self.state.deferred:
            return
        self.state.deferred[key] = {
            "first_deferred_utc": now,
            "bars_b": ev.bars_b,
            "bars_a": ev.bars_a,
        }
        self.emitter.event(
            "data_late",
            f"{key} closed before its final bar arrived "
            f"(bars_a={ev.bars_a}, bars_b={ev.bars_b}); holding the row and "
            f"re-checking for {DATA_LAG_GRACE_SECONDS:.0f}s",
            when=self._ny_now(now),
        )
        self._log(f"  {key}: tail bars late -> deferred")

    def _retry_deferred(self, series, now: float) -> int:
        """Re-examine deferred windows. Returns rows actually written."""
        emitted = 0
        for key, meta in list(self.state.deferred.items()):
            day_s, label = key.split("|")
            window = next((w for w in time_windows.windows_for_day(dt.date.fromisoformat(day_s))
                           if w.label == label), None)
            if window is None:                       # pragma: no cover - defensive
                self.state.deferred.pop(key, None)
                continue
            ev = self._evaluate(window)
            if ev is not None and self.emitter.already_emitted(ev):
                self.state.deferred.pop(key, None)
                continue
            if ev is not None and ev.reason != REASON_OUTSIDE_RANGE:
                # The bars arrived. Before treating that as an ordinary
                # evaluation, apply the same observation gate as everywhere
                # else: if the runner died while the window was open, the data
                # that eventually arrived is history we did not watch. Q14
                # applies to a deferred window exactly as to any other.
                start_utc, close_utc, _ = window_utc_bounds(window)
                if not observed(self._intervals(now), start_utc.timestamp(),
                                close_utc.timestamp(), self.grace_seconds):
                    gap = gap_evaluation(window, detail="not_observed_by_runner",
                                         source=ev)
                    if self._emit_checked(gap, now):
                        emitted += 1
                    self.state.deferred.pop(key, None)
                    continue
                # The bars arrived. This is now an ordinary evaluation.
                if self._is_pending(ev):
                    _start, close_utc, _ = window_utc_bounds(window)
                    self._mark_pending(key, ev, close_utc.timestamp(), now)
                elif self._emit_checked(ev, now, note="emitted_after_data_lag"):
                    emitted += 1
                    self._log_result(window, ev)
                self.state.deferred.pop(key, None)
                continue
            # Still no complete window. `ev is None` means the feed delivered
            # nothing at all, which is a stall too — the deadline must not depend
            # on being able to evaluate, or a silent feed would never be logged.
            age = now - float(meta.get("first_deferred_utc", now))
            if age < DATA_LAG_GRACE_SECONDS:
                continue
            # Watched, connected, and still no bars for a window that is long
            # since closed: that is a feed fault, and it says so.
            stalled = stall_evaluation(window, detail="no_bars_after_grace",
                                       source=ev if ev is not None else None)
            if self._emit_checked(stalled, now):
                emitted += 1
            self.state.deferred.pop(key, None)
            self.emitter.event(
                "feed_stalled",
                f"{key} produced no bars within {DATA_LAG_GRACE_SECONDS:.0f}s of "
                f"closing; logged as {REASON_LIVE_FEED_STALLED} (feed fault, "
                f"not runner downtime)",
                when=self._ny_now(now),
            )
            self._log(f"  {key}: no bars after grace -> {REASON_LIVE_FEED_STALLED}")
        return emitted

    # -- evaluation -------------------------------------------------------
    def _evaluate_due(self, series, now: float) -> tuple[int, int]:
        evaluated = emitted = 0
        lo = self._gap_scan_from if self._gap_scan_from is not None else now - 3600
        intervals = self._intervals(now)
        for window, start_utc, close_utc in self._windows_closing_between(lo, now):
            key = window_key(window)
            # Observation gate. A window whose whole span was not watched is a
            # gap (Q14), even though the bars for it exist in the back-filled
            # history. Without this check the runner would silently do the one
            # thing it is forbidden to do: score windows from broker history.
            if not observed(intervals, start_utc, close_utc, self.grace_seconds):
                continue
            ev = self._evaluate(window)
            if ev is None:
                continue
            if self.emitter.already_emitted(ev):
                continue
            evaluated += 1
            if self._is_pending(ev):
                self._mark_pending(key, ev, close_utc, now)
                continue
            if ev.reason == REASON_OUTSIDE_RANGE:
                # The window's own tail has not been published yet. Holding it
                # is the whole fix: the previous behaviour wrote this row once,
                # immediately, and never looked again — so a bar that was 20
                # seconds late became a permanent, wrong "no data" verdict.
                self._defer(key, ev, now)
                continue
            if self._emit_checked(ev, now):
                emitted += 1
                self._log_result(window, ev)
        return evaluated, emitted

    def _advance_pending(self, series, now: float) -> tuple[int, int]:
        """Re-evaluate open trades. Q13: the row is written when it resolves."""
        still_pending = 0
        emitted = 0
        for key, meta in list(self.state.pending.items()):
            day_s, label = key.split("|")
            day = dt.date.fromisoformat(day_s)
            window = next((w for w in time_windows.windows_for_day(day)
                           if w.label == label), None)
            if window is None:                      # pragma: no cover - defensive
                self.state.pending.pop(key, None)
                continue
            ev = self._evaluate(window)
            if ev is None:
                still_pending += 1
                continue
            if self.emitter.already_emitted(ev):
                self.state.pending.pop(key, None)
                continue
            if self._is_pending(ev):
                deadline = meta.get("deadline_utc", now + 86400)
                if now < deadline:
                    still_pending += 1
                    continue
                # The trade has had MAX_BARS_TO_RESOLVE_TRADE of history and
                # still has no exit. Log it as unresolved rather than hanging.
                self._emit_checked(ev, now, note="resolution_deadline_passed")
                self.state.pending.pop(key, None)
                emitted += 1
                self._log(f"unresolved past deadline: {key}")
                continue
            # The trade resolved — but did we actually watch the minute it
            # exited in? If the feed died mid-trade, the exit is in the
            # back-filled history and must NOT be scored (Q14). This is the
            # same rule as a crash, applied to an outage the process survived.
            if not self._exit_was_observed(ev, now):
                gap = gap_evaluation(window, detail="exit_not_observed",
                                     source=ev)
                self._emit_checked(gap, now)
                self.state.pending.pop(key, None)
                emitted += 1
                self.emitter.event(
                    "gap", f"{key} exit fell outside an observed interval; "
                           f"logged as {REASON_RUNNER_DOWN}, not scored",
                    when=self._ny_now(now),
                )
                self._log(f"  {key}: exit not observed -> {REASON_RUNNER_DOWN}")
                continue
            self._emit_checked(ev, now)
            self.state.pending.pop(key, None)
            emitted += 1
            self._log_result(window, ev)
        return still_pending, emitted

    def _exit_was_observed(self, ev: WindowEvaluation, now: float) -> bool:
        """Was the trade's exit minute inside a connected polling interval?"""
        trade = ev.trade
        if trade is None or trade.final_exit_time is None:
            return False
        exit_minute = trade.final_exit_time
        start_utc = exit_minute.replace(tzinfo=NY).astimezone(UTC).timestamp()
        return observed(self._intervals(now), start_utc, start_utc + 60.0,
                        self.grace_seconds)

    def _evaluate(self, window: time_windows.Window) -> WindowEvaluation | None:
        """Evaluate one window, tolerating a feed that has delivered no bars.

        `evaluate_window` (Phase 1, validated, untouched) assumes a non-empty
        series and raises `IndexError` on an empty one. `LiveBarSource` refuses
        to construct over an empty feed, so this is defence in depth rather than
        the expected path — but a live loop must not die on it, and the window
        must not be silently scored. Returning None means "cannot decide yet";
        callers skip it and the window is re-examined on the next poll.
        """
        try:
            series = self.source.series
        except Exception:
            return None
        if len(series) == 0:
            if not self._warned_empty:
                self._warned_empty = True
                self.emitter.event(
                    "feed_empty",
                    "no M1 bars available — windows cannot be evaluated yet",
                    when=self._ny_now(self._now_fn()),
                )
            return None
        return evaluate_window(series, window)

    @staticmethod
    def _is_pending(ev: WindowEvaluation) -> bool:
        """Open trade with no exit yet — not an outcome, just 'not finished'."""
        return ev.outcome == OUTCOME_MISSING and ev.reason == REASON_DATA_ENDS

    def _mark_pending(self, key: str, ev: WindowEvaluation, close_utc: float,
                      now: float) -> None:
        trigger = ev.sweep.trigger_time if ev.sweep else None
        if trigger is not None:
            _, _, _ = window_utc_bounds(
                time_windows.Window(ev.trading_date, ev.window.start_hour)
            )
            trigger_utc = trigger.replace(tzinfo=NY).astimezone(UTC).timestamp()
        else:
            trigger_utc = close_utc
        deadline = trigger_utc + config.MAX_BARS_TO_RESOLVE_TRADE * 60
        self.state.pending[key] = {
            "first_seen_utc": self.state.pending.get(key, {}).get("first_seen_utc", now),
            "trigger_utc": trigger_utc,
            "deadline_utc": deadline,
            "direction": ev.plan.direction if ev.plan else "",
        }

    # -- gaps -------------------------------------------------------------
    def _fill_gaps(self, now: float) -> int:
        """Windows that closed while we were not watching. Never reconstructed."""
        lo = self._gap_scan_from if self._gap_scan_from is not None else now
        intervals = self._intervals(now)
        filled = 0
        for window, start_utc, close_utc in self._windows_closing_between(lo, now):
            key = window_key(window)
            if key in self.state.pending:
                continue
            ev = self._evaluate(window)
            if ev is None:
                continue
            if self.emitter.already_emitted(ev):
                continue
            if observed(intervals, start_utc, close_utc, self.grace_seconds):
                continue
            gap = gap_evaluation(
                window, detail="not_observed_by_runner",
                source=ev if ev.plan is not None else None,
            )
            if self._emit_checked(gap, now):
                filled += 1
                self.emitter.event(
                    "gap", f"{key} logged as {REASON_RUNNER_DOWN} "
                           f"(window not fully observed)",
                    when=self._ny_now(now),
                )
        self._gap_scan_from = now - self.grace_seconds
        return filled

    def _reconcile_pending_after_crash(self, now: float) -> None:
        """Q14: a trade that was open when we died is logged, never resolved.

        The entry WAS observed, so the audit row keeps the plan (direction,
        entry, SL, TP) — those are facts we saw. The exit was not observed, so no
        exit is invented: the row is a gap, not a WIN/LOSS.
        """
        if not self.state.pending:
            return
        for key, meta in list(self.state.pending.items()):
            day_s, label = key.split("|")
            day = dt.date.fromisoformat(day_s)
            window = next((w for w in time_windows.windows_for_day(day)
                           if w.label == label), None)
            if window is None:                      # pragma: no cover
                self.state.pending.pop(key, None)
                continue
            try:
                ev = self._evaluate(window)
            except Exception:
                ev = None
            gap = gap_evaluation(
                window,
                detail="open_trade_at_shutdown",
                source=ev if (ev is not None and ev.plan is not None) else None,
            )
            if self._emit_checked(gap, now):
                self.emitter.event(
                    "gap",
                    f"{key} had an open trade when the runner stopped "
                    f"(direction {meta.get('direction', '?')}); logged as "
                    f"{REASON_RUNNER_DOWN}, exit never inferred",
                    when=self._ny_now(now),
                )
                self._log(f"crash reconciliation: {key} -> {REASON_RUNNER_DOWN}")
            self.state.pending.pop(key, None)
        self.state.save()

    # -- misc -------------------------------------------------------------
    def _ny_now(self, now: float) -> dt.datetime:
        return dt.datetime.fromtimestamp(now, UTC).astimezone(NY).replace(tzinfo=None)

    def _log_result(self, window: time_windows.Window, ev: WindowEvaluation) -> None:
        extra = ""
        if ev.plan is not None:
            extra = f" {ev.plan.direction} entry {ev.plan.entry}"
        if ev.trade is not None and ev.trade.resolved:
            extra += f" -> {ev.trade.exit_kind} {ev.trade.result_r_multiple:+.3f}R"
        self._log(f"  {window_key(window)}: {ev.outcome} / {ev.reason}{extra}")


def broker_now_ny(now_utc: float) -> dt.datetime:
    """NY wall clock for event logging (broker label minus the constant offset)."""
    return broker_now(now_utc) - dt.timedelta(hours=config.BROKER_OFFSET_HOURS)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main(argv: list[str] | None = None, mt5_module=None) -> int:
    ap = argparse.ArgumentParser(
        description="Phase 2 dry-run runner — evaluates windows, places NO orders"
    )
    ap.add_argument("--symbol", default=config.INSTRUMENT_SYMBOL)
    ap.add_argument("--out-dir", default=str(pathlib.Path(__file__).resolve().parent / "output"))
    ap.add_argument("--poll-seconds", type=int, default=20)
    ap.add_argument("--max-runtime-seconds", type=float, default=None)
    ap.add_argument("--check", action="store_true",
                    help="run the startup guards and exit without looping")
    args = ap.parse_args(argv)
    out_dir = pathlib.Path(args.out_dir)

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

    runner = DryRunRunner(mt5, out_dir, poll_seconds=args.poll_seconds,
                          symbol=args.symbol)
    try:
        facts = runner.start()
        if args.check:
            print("\n--- startup checks ---")
            for k, v in facts.items():
                print(f"  {k}: {v}")
            print(f"  DST-safe dates verified: {facts.get('dst_safe_dates')}")
            print(f"  bars loaded            : {len(runner.source.series)}")
            print("\nREADY: the runner can start. No orders are reachable from it.")
            runner.stop()
            return 0
        rc = runner.run(max_seconds=args.max_runtime_seconds)
        return rc
    finally:
        try:
            mt5.shutdown()
        except Exception:
            pass


if __name__ == "__main__":
    raise SystemExit(main())
