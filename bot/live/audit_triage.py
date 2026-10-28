"""
Project Chronos — Phase 2 audit triage
======================================

Read-only tooling for the live dry-run log. It answers the three questions the
investigation actually needs, from the CSVs the runner already wrote — no MT5, no
network, no guessing:

1. **Every `outside_data_range` row since launch**, classified as either a
   genuine connectivity outage or a transient-lag misfire, with counts
   (`--outside-range`).
2. **Every row re-derived from its own stored candles** (`--self-check`). The
   audit CSV carries Candle A's OHLC and Candle B's open, and the verdict is a
   pure function of those numbers. So an `invalid open location` row can be
   checked against its own data without the broker's bars: if the stored open
   sits inside the stored discount zone, the verdict contradicts the data. This
   covers the whole file, not a spot-check.
3. **A printed derivation for one window** (`--show DATE LABEL`), for the
   eyeball check on a single row.

Why this matters: the runner decides in real time and cannot be re-run, so the
only durable evidence is what it wrote. These checks read that evidence rather
than trusting the reason string.

`--annotate` appends a **reconciliation** file for rows already known to be
wrong. Append-only, in keeping with Q13/Q14: nothing already written is ever
rewritten. The reconciliation lives beside the log, not on top of it.

Usage (on the machine that ran the dry run, from `bot/`):

    python live/audit_triage.py --certify 2026-10-28
    python live/audit_triage.py --self-check
    python live/audit_triage.py --outside-range --monitor live/output/dst_monitor.csv
    python live/audit_triage.py --show 2026-10-26 04:45-05:15
    python live/audit_triage.py --state live/output/runner_state.json
    python live/audit_triage.py --annotate --known-bad known_bad.txt

Exit codes: 0 = nothing found, 1 = the check found a contradiction or an
unexplained row. Non-zero is the point: this is meant to be run in CI onto the
log, not read casually.

`--certify` adds a third verdict and a third exit code, because "the day is not
over" is not a defect and must not be reported as one:

    0  PASS         all four conditions hold; the day counts
    1  FAIL         a condition broke; the day does not count
    2  NOT APPLICABLE / bad usage (e.g. a weekend, or an unparseable date)
    3  INCOMPLETE   nothing is wrong, the day simply is not finished: a window
                    has not closed, or a trade is still open awaiting its exit
                    (Q13). Re-run later; the day is final after its last window
                    closes at 00:15 NY the following morning.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import pathlib
import sys
from decimal import Decimal, InvalidOperation

#: Run from anywhere: `python live/audit_triage.py` has `live/` on sys.path, not
#: `bot/`, so the `src` package would not be importable without this.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from live import csv_emitter  # noqa: E402
from src import context_classifier, sweep_detector, trade_simulator  # noqa: E402
from src import window_evaluator  # noqa: E402
from src.context_classifier import PREMIUM, VOID, classify  # noqa: E402
from src.window_evaluator import REASON_OUTSIDE_RANGE  # noqa: E402

RECONCILIATION_COLUMNS = [
    "annotated_utc",
    "trading_date",
    "window_label",
    "original_reason",
    "corrected_reason",
    "classification",
    "evidence",
    "note",
]

#: Every reason string a live row may legitimately carry.
#:
#: This is DERIVED from the modules that produce the strings, not hand-listed.
#: The hand-listed version had a real defect: it contained the NO_SETUP and gap
#: vocabulary but omitted the WIN/LOSS exit reasons, so `--illegal-reasons` flagged
#: 43 of 46 live rows — including `stop loss hit` and `take profit hit`, which are
#: the *most* legitimate strings in the file. A checker that cries wolf on the
#: normal case is worse than no checker, because it trains you to ignore it.
#:
#: Importing the constants means the check cannot drift again: adding an exit
#: reason to `trade_simulator` extends the allow-list automatically, and a typo in
#: a reason string is still caught.
LIVE_LEGAL_REASONS = frozenset({
    # --- trade exits (src/trade_simulator.py) -----------------------------
    trade_simulator.REASON_TP,
    trade_simulator.REASON_SL,
    trade_simulator.REASON_BREAKEVEN_AFTER_PARTIAL,
    trade_simulator.REASON_TP_AFTER_PARTIAL,
    trade_simulator.REASON_UNRESOLVED,
    # --- no-setup / void (src/window_evaluator.py, src/context_classifier.py)
    context_classifier.REASON_INVALID_OPEN_LOCATION,
    window_evaluator.REASON_MARKET_CLOSED,
    window_evaluator.REASON_MISSING_DATA,
    window_evaluator.REASON_NONEXISTENT_LOCAL_TIME,
    # `outside_data_range` is deliberately ABSENT: it is a backtest verdict that
    # live code must never write. Its absence from this set is the assertion.
    # --- sweep (src/sweep_detector.py) ------------------------------------
    sweep_detector.REASON_NO_SWEEP,
    sweep_detector.REASON_SWEEP_NOT_REVERSED,
    sweep_detector.REASON_AMBIGUOUS_SAME_BAR,
    # --- phase 2 operational (live/csv_emitter.py) -------------------------
    csv_emitter.REASON_RUNNER_DOWN,
    csv_emitter.REASON_LIVE_FEED_STALLED,
})

#: The gap/stall reasons occupy the same column as market reasons, so a consumer
#: must be able to tell "the market did this" from "we failed to watch it".
OPERATIONAL_REASONS = frozenset({
    csv_emitter.REASON_RUNNER_DOWN,
    csv_emitter.REASON_LIVE_FEED_STALLED,
})


def _parse_now(text: str | None) -> float | None:
    """`--now` in NY wall clock (naive) or with an explicit offset."""
    if not text:
        import time as _time

        return _time.time()
    try:
        stamp = dt.datetime.fromisoformat(text)
    except ValueError:
        print(f"--now needs ISO-8601, got {text!r}", file=sys.stderr)
        return None
    if stamp.tzinfo is None:                     # naive means New York wall clock
        from zoneinfo import ZoneInfo

        from src import config

        stamp = stamp.replace(tzinfo=ZoneInfo(config.NY_TZ))
    return stamp.timestamp()


def _dec(value: str) -> Decimal | None:
    try:
        return Decimal(value) if value not in (None, "") else None
    except (InvalidOperation, TypeError):
        return None


def read_rows(path: pathlib.Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def parse_period(text: str) -> tuple[dt.datetime, dt.datetime]:
    """`2026-10-26T14:22/2026-10-27T01:48` -> naive NY datetimes."""
    lo, hi = text.split("/")
    return dt.datetime.fromisoformat(lo), dt.datetime.fromisoformat(hi)


def within(day: str, label: str, lo: dt.datetime, hi: dt.datetime) -> bool:
    """Does this window overlap the given NY period?"""
    start_hour = int(label.split(":")[0])
    start = dt.datetime.combine(dt.date.fromisoformat(day), dt.time(start_hour, 45))
    return start < hi and start + dt.timedelta(minutes=30) > lo


# ---------------------------------------------------------------------------
# 1. outside_data_range triage
# ---------------------------------------------------------------------------
def load_monitor(path: pathlib.Path) -> list[tuple[dt.datetime, dt.datetime]]:
    """(sample NY wall clock, newest bar NY) pairs from `dst_monitor.csv`."""
    out = []
    for row in read_rows(path):
        try:
            when = dt.datetime.fromisoformat(row["ny_wall"])
            newest = dt.datetime.fromisoformat(row["window_last_ny"])
        except (KeyError, ValueError):
            continue
        out.append((when, newest))
    return out


def newest_bar_at(monitor, when: dt.datetime,
                  tolerance_seconds: float = 120.0) -> dt.datetime | None:
    """The newest bar the monitor could see at `when`, or None if it was down."""
    for sample_at, newest in monitor:
        delta = (sample_at - when).total_seconds()
        if 0 <= delta <= tolerance_seconds:
            return newest
    return None


def first_sample_after(monitor, when: dt.datetime):
    """The next monitor sample at/after `when`, however far away it is.

    Used for bracketing: a sample taken *later* still tells us what the terminal
    did not have. If at 01:44 the newest bar is 13:13 and the wing closed at
    21:00, the wing had nothing, regardless of the monitor being silent at 21:00.
    """
    for sample_at, newest in monitor:
        if sample_at >= when:
            return sample_at, newest
    return None, None


def feed_staleness(monitor, window_end: dt.datetime) -> float | None:
    """Minutes between the newest bar and the window's close. None = no sample."""
    newest = newest_bar_at(monitor, window_end)
    if newest is None:
        return None
    return (window_end - newest).total_seconds() / 60.0


def stale_before(monitor, start: dt.datetime, close: dt.datetime) -> bool:
    """Was the terminal already behind the window at its own close?

    The last monitor sample at or before `close` is a direct look at what the
    terminal had while the window was open. If its newest bar predates the
    window's start, the window could not have contained a single bar.
    """
    last = None
    for sample_at, newest in monitor:
        if sample_at <= close:
            last = newest
        else:
            break
    return last is not None and last <= start


def _freeze_brackets(monitor, start: dt.datetime,
                     close: dt.datetime) -> bool:
    """Is this window inside a freeze proven by the samples on either side?

    Both halves must hold, or this is not evidence:

    * the newest bar at the next available sample is still older than the
      window's start (nothing for the window existed when the monitor looked
      next), and
    * the freeze is not contradicted by a sample inside the window showing fresh
      bars.
    """
    sample_at, newest = first_sample_after(monitor, close)
    if newest is None:
        return False
    if newest > start:
        return False                      # bars for this window did exist
    inside = [n for s, n in monitor if start <= s < close]
    return all(n <= start for n in inside) if inside else True


#: A window whose newest available bar is this far behind its close cannot have
#: had bars in it. Thirty minutes is one full window, so the two cases do not
#: overlap: below it, the feed had data *inside* the window (a late tail); above
#: it, the feed had no data for the window at all (a stall).
STALL_STALENESS_MINUTES = 30.0


def triage_outside_range(rows: list[dict],
                         outages: list[tuple[dt.datetime, dt.datetime]] | None = None,
                         monitor=None) -> tuple[list[dict], dict]:
    """Classify each `outside_data_range` row.

    Two independent sources of evidence, in order of strength:

    1. **The monitor's own newest bar** (`--monitor dst_monitor.csv`). This is the
       direct measurement: if the newest bar at the window's close is older than
       the window itself, the feed had nothing for that window, full stop. If it
       is current to within a minute or two, the feed was healthy and only the
       final bar was late.
    2. **The row's shape**, which is a weaker but always-available proxy:
       `no_bars_in_window` is the shape of a stall, `incomplete_candle` with
       `bars_candle_b` < 15 is the shape of a late tail.

    Shape alone is not enough, and this is the trap the first pass fell into: a
    single `--outages` range only accounts for outages you already knew about, so
    every *other* stall lands in the unexplained bucket and looks like a bug. The
    monitor is what distinguishes "a fourth outage we had not noticed" from "the
    late-tail bug", and it does so by measurement rather than by my assumption.
    """
    out = []
    for row in rows:
        if row.get("reason") != REASON_OUTSIDE_RANGE:
            continue
        flags = (row.get("flags") or "").split(";")
        bars_a = int(row.get("bars_candle_a") or 0)
        bars_b = int(row.get("bars_candle_b") or 0)
        if "no_bars_in_window" in flags:
            shape = "no_bars_in_window"
        elif "incomplete_candle" in flags:
            shape = "short_tail"
        else:
            shape = "unexplained_shape"

        day = dt.date.fromisoformat(row["trading_date"])
        start_hour = int(row["window_label"].split(":")[0])
        start = dt.datetime.combine(day, dt.time(start_hour, 45))
        close = start + dt.timedelta(minutes=30)

        staleness = feed_staleness(monitor, close) if monitor else None
        in_outage = bool(outages) and any(
            within(row["trading_date"], row["window_label"], lo, hi)
            for lo, hi in outages
        )

        if staleness is not None:
            # Measured. This is the answer when the monitor was up.
            verdict = ("feed_stalled" if staleness >= STALL_STALENESS_MINUTES
                       else "transient_tail")
            basis = f"monitor: newest bar {staleness:.0f} min behind close"
            confidence = "measured"
        elif monitor and _freeze_brackets(monitor, start, close):
            # The monitor was down for this window, but it recorded the newest
            # bar before and after the gap and in both cases it was older than
            # this window. The freeze is therefore proven across the blind spot.
            verdict = "feed_stalled"
            basis = "monitor: freeze bracketed across its own downtime"
            confidence = "bracketed"
        elif monitor and shape == "no_bars_in_window" and stale_before(monitor, start, close):
            # No sample during this window, but the last one before it closed
            # already showed the terminal behind the window's own start, and the
            # runner independently reported zero bars. Two sources, one answer.
            verdict = "feed_stalled"
            basis = ("runner reported zero bars; monitor's last pre-close sample "
                     "was already behind this window")
            confidence = "corroborated"
        elif monitor and outages is not None:
            verdict = "connectivity" if in_outage else "unexplained_no_monitor_sample"
            basis = ("monitor down for this window; classified on the supplied outage range"
                     if in_outage else "monitor down and outside every supplied outage")
            confidence = "outage_range"
        elif outages is not None:
            verdict = "connectivity" if in_outage else "unexplained_transient_lag"
            basis = "outage range only (no monitor supplied)"
            confidence = "outage_range"
        else:
            verdict = "unknown_no_evidence"
            basis = "no monitor and no outage range supplied"
            confidence = "none"

        out.append({
            "trading_date": row["trading_date"],
            "window_label": row["window_label"],
            "shape": shape,
            "bars_a": bars_a,
            "bars_b": bars_b,
            "staleness_minutes": staleness,
            "verdict": verdict,
            "confidence": confidence,
            "basis": basis,
        })
    counts: dict[str, int] = {}
    for item in out:
        counts[item["verdict"]] = counts.get(item["verdict"], 0) + 1
    return out, counts


def stall_intervals(monitor, *, min_minutes: float = 10.0) -> list[dict]:
    """Spans where the monitor's newest bar stopped advancing.

    This is how a second, unnoticed outage is found: the newest bar freezes at a
    time and does not move for tens of minutes. The span is reported in NY wall
    clock, from the moment the freeze begins to the last sample that still showed
    it (so the recovery is bounded, not invented).
    """
    spans = []
    run_start = run_last = prev_sample = None
    for sample_at, newest in monitor:
        if run_last is None or newest != run_last:
            if run_start is not None:
                spans.append((run_start, run_last, prev_sample))
            run_start, run_last = sample_at, newest
        prev_sample = sample_at
    if run_start is not None:
        spans.append((run_start, run_last, prev_sample))

    out = []
    for began, frozen_bar, ended in spans:
        minutes = (ended - began).total_seconds() / 60.0
        if minutes < min_minutes:
            continue
        out.append({
            "stalled_bar_ny": frozen_bar,
            "frozen_from_ny": began,
            "observed_until_ny": ended,
            "minutes": minutes,
            "recovery_lag_minutes": (ended - frozen_bar).total_seconds() / 60.0,
        })
    return out


def monitor_gaps(monitor, *, min_minutes: float = 3.0) -> list[tuple[dt.datetime, dt.datetime]]:
    """Spans with no monitor sample at all — the monitor process itself was down.

    Worth separating from a feed freeze: a monitor gap is not evidence that the
    feed was healthy, it is absence of evidence, and rows inside one must not be
    silently counted either way.
    """
    gaps = []
    for (prev, _), (cur, _n) in zip(monitor, monitor[1:]):
        minutes = (cur - prev).total_seconds() / 60.0
        if minutes >= min_minutes:
            gaps.append((prev, cur))
    return gaps


# ---------------------------------------------------------------------------
# 2. self-consistency: re-derive every verdict from the stored candles
# ---------------------------------------------------------------------------
def rederive(row: dict) -> dict | None:
    """Recompute the context classification from the row's own numbers.

    Returns None when the row does not carry enough data to check (a gap row,
    or a window with no candles). Otherwise a dict with what the data implies.
    """
    low = _dec(row.get("candle_a_low"))
    mid = _dec(row.get("candle_a_mid"))
    high = _dec(row.get("candle_a_high"))
    open_b = _dec(row.get("candle_b_open"))
    if None in (low, mid, high, open_b):
        return None
    verdict = classify(open_b, low, mid, high)
    zone = "discount" if verdict.context.value == "DISCOUNT" else (
        "premium" if verdict.context.value == "PREMIUM" else "void")
    return {
        "context": verdict.context.value,
        "zone": zone,
        "low_a": low, "mid_a": mid, "high_a": high, "open_b": open_b,
        "margin_from_low": open_b - low,
        "margin_from_mid": open_b - mid,
        "margin_from_high": high - open_b,
    }


def self_check(rows: list[dict]) -> list[dict]:
    """Rows whose reason contradicts their own stored candles."""
    problems = []
    for row in rows:
        flags = (row.get("flags") or "").split(";")
        # Only rows that actually reached the classifier can be checked. A row
        # flagged as incomplete never did, so its reason says nothing about the
        # open — that is a different check (the triage above).
        if any(f in flags for f in ("incomplete_candle", "no_bars_in_window")):
            continue
        if row.get("reason") == REASON_OUTSIDE_RANGE:
            continue
        derived = rederive(row)
        if derived is None:
            continue
        stored_context = (row.get("context") or "").strip()
        reason = (row.get("reason") or "").strip()

        # The contradiction that matters: VOID said, but the open is inside a
        # valid zone. Or a valid context said, but the open is on a boundary.
        if reason == "invalid open location" and derived["context"] != VOID:
            # The specific case: the row says the open was unusable, and its own
            # stored numbers say it was usable.
            problems.append({
                **row, "_problem": "invalid_open_claimed_but_open_is_valid",
                "_derived": derived,
            })
        elif stored_context == VOID and derived["context"] != VOID:
            problems.append({
                **row, "_problem": "void_claimed_but_open_is_inside_zone",
                "_derived": derived,
            })
        elif stored_context in (PREMIUM, "DISCOUNT") and derived["context"] == VOID:
            problems.append({
                **row, "_problem": "zone_claimed_but_open_is_on_boundary",
                "_derived": derived,
            })
    return problems


# ---------------------------------------------------------------------------
# 3. print one row's derivation
# ---------------------------------------------------------------------------
def show_row(row: dict) -> str:
    derived = rederive(row)
    lines = [
        f"{row['trading_date']} {row['window_label']}",
        f"  outcome           : {row.get('outcome')}",
        f"  reason            : {row.get('reason')}",
        f"  context           : {row.get('context')}",
        f"  flags             : {row.get('flags')}",
        f"  bars a/b          : {row.get('bars_candle_a')} / {row.get('bars_candle_b')}",
        f"  candle A high/mid/low : {row.get('candle_a_high')} / "
        f"{row.get('candle_a_mid')} / {row.get('candle_a_low')}",
        f"  candle B open     : {row.get('candle_b_open')}",
    ]
    if derived is None:
        lines.append("  -> not enough stored data to re-derive the classification")
        return "\n".join(lines)
    lines += [
        f"  derived context   : {derived['context']} ({derived['zone']})",
        f"  open - Low_A      : {derived['margin_from_low']}",
        f"  open - Mid_A      : {derived['margin_from_mid']}",
        f"  High_A - open     : {derived['margin_from_high']}",
        f"  zone width (High-Low): {derived['high_a'] - derived['low_a']}",
    ]
    if row.get("reason") == "invalid open location" and derived["context"] != VOID:
        lines.append("  -> CONTRADICTION: the stored open is inside a valid zone, "
                     "so 'invalid open location' is wrong for this row")
    elif derived["context"] == VOID:
        lines.append("  -> consistent: the stored open is on/outside a boundary, "
                     "so VOID is the correct call")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 4. clean-day certification
# ---------------------------------------------------------------------------
#: Outcomes that are an actual decision about the window. `MISSING_EVALUATION` is
#: excluded on purpose: it is the schema's "this window could not be evaluated",
#: so it is the absence of an outcome, not one. A day containing it is not clean.
CERTIFIABLE_OUTCOMES = {"WIN", "LOSS", "NO_SETUP"}

#: Condition labels, in the order they are reported. Each is a separate gate so a
#: failure says *which* property broke, not just "FAIL".
CERTIFY_CONDITIONS = [
    ("coverage", "all scheduled windows logged exactly once"),
    ("outcome", "every row has a valid outcome and a reason"),
    ("range", "no outside_data_range / live_feed_stalled rows"),
    ("gaps", "no dry_run_gap_runner_down rows"),
]

#: The four verdicts. INCOMPLETE exists because "the day has not finished yet"
#: and "a window is genuinely missing" are different facts and used to be
#: reported identically — which made the tool unusable unattended, since every
#: mid-day run looked like a defect.
CERTIFY_PASS = "PASS"
CERTIFY_FAIL = "FAIL"
CERTIFY_INCOMPLETE = "INCOMPLETE"
CERTIFY_NOT_APPLICABLE = "NOT_APPLICABLE"

#: Why a scheduled window has no row yet.
ABSENT_NOT_DUE = "not_due"                    # the window has not closed
ABSENT_AWAITING_TRADE = "awaiting_trade"      # Q13: the row comes when simulate() resolves
ABSENT_AWAITING_DATA = "awaiting_data"        # held for late bars, still inside the grace
ABSENT_MISSING = "missing"                    # nothing explains it: a real defect

#: A row appears on the first poll after a window closes, so for a few seconds
#: after the close its absence means nothing. 90 s is several polls and longer
#: than any publication lag, and it only ever delays a verdict, never hides one.
ROW_GRACE_SECONDS = 90.0


def _window_bounds(window):
    """UTC start/close for a window, via the DST-tested helper.

    Imported lazily: this tool must stay runnable on a machine with no MT5 and no
    terminal, and `live.dry_run_runner` pulls in the live source.
    """
    from live.dry_run_runner import window_utc_bounds

    start_utc, end_utc, _flags = window_utc_bounds(window)
    return start_utc, end_utc


def load_certify_state(path: pathlib.Path) -> tuple[set[str], set[str]]:
    """`(pending, deferred)` window keys from `runner_state.json`.

    These are the two legitimate reasons a *closed* window has no row yet, and
    without them every open trade would be reported as a missing window. A
    missing or unreadable file yields empty sets: the tool then treats unexplained
    absences as failures, which is the conservative direction.
    """
    import json

    if not path.exists():
        return set(), set()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return set(), set()
    return set((raw.get("pending") or {}).keys()), set((raw.get("deferred") or {}).keys())


def classify_absent(day: dt.date, present: set[str], *, now: float,
                    pending: set[str], deferred: set[str]) -> list[dict]:
    """For every scheduled window with no row, say why it has no row.

    Order matters, and it is deliberate:

    1. **not closed yet** — the window's own close is in the future (plus a small
       publication grace). Nothing is missing; the day is simply not over.
    2. **awaiting a trade** — the window closed, the runner saw it, and the trade
       it opened has not resolved. Q13 says the row is written when `simulate()`
       decides, which can be days later. Not a defect.
    3. **awaiting data** — held for late bars, still inside the grace window.
    4. **missing** — anything else. This is the only case that is a failure, and
       the only one that used to be indistinguishable from case 1.
    """
    import time as _time

    from src import time_windows

    out = []
    for window in time_windows.windows_for_day(day):
        if window.label in present:
            continue
        key = f"{day.isoformat()}|{window.label}"
        _start, close = _window_bounds(window)
        closes_at = close.timestamp()
        if closes_at + ROW_GRACE_SECONDS > now:
            kind = ABSENT_NOT_DUE
        elif key in pending:
            kind = ABSENT_AWAITING_TRADE
        elif key in deferred:
            kind = ABSENT_AWAITING_DATA
        else:
            kind = ABSENT_MISSING
        out.append({
            "label": window.label,
            "kind": kind,
            "closes_ny": window.end,
            "closes_utc": close,
            "is_failure": kind == ABSENT_MISSING,
        })
    # Chronological by close, not by start_hour: `windows_for_day` begins at
    # 23:45 because that slot belongs to this trading date but opens last, and a
    # human reading a list of missing windows expects time order.
    out.sort(key=lambda item: item["closes_utc"])
    return out


def certify(day: dt.date, rows: list[dict], *,
            now: float | None = None,
            pending: set[str] | None = None,
            deferred: set[str] | None = None) -> dict:
    """Apply the four-part clean-day rule to one New York trading date.

    1. **Coverage** — every scheduled window is accounted for: it has a row, or it
       has a stated reason for not having one yet (see `classify_absent`). A
       window with no row and no explanation is a failure.
    2. **Outcome** — every row carries a real decision and says why.
    3. **No range/stall rows** — `outside_data_range` was the defect that made
       this rule necessary; `live_feed_stalled` means the terminal gave no data
       for a window we were watching.
    4. **No gap rows** — the runner must have watched all of it.

    The verdict is one of PASS / FAIL / INCOMPLETE / NOT_APPLICABLE. INCOMPLETE
    means the day cannot be judged yet — a window has not closed, or a trade is
    still open — and is *not* a failure. That distinction is the whole point:
    a daily tool run unsupervised must not report "missing window" for a window
    that has not happened.
    """
    import time as _time

    from src import time_windows

    now = _time.time() if now is None else now
    pending = pending or set()
    deferred = deferred or set()

    if not time_windows.is_trading_day(day):
        return {
            "date": day, "status": CERTIFY_NOT_APPLICABLE, "applicable": False,
            "passed": False, "failures": [], "warnings": [], "pending": [],
            "detail": f"{day} is a {day.strftime('%A')} - not a trading day, nothing to certify",
            "counts": {},
        }

    scheduled = [w.label for w in time_windows.windows_for_day(day)]
    on_day = [r for r in rows if r.get("trading_date") == day.isoformat()]
    failures: list[tuple[str, str]] = []
    warnings: list[str] = []

    # --- 1. coverage, with the reasons a window may legitimately be absent ---
    labels = [r.get("window_label", "") for r in on_day]
    seen: dict[str, int] = {}
    for label in labels:
        seen[label] = seen.get(label, 0) + 1
    duplicates = sorted(l for l, n in seen.items() if n > 1)
    unexpected = sorted(l for l in seen if l not in scheduled)
    absent = classify_absent(day, set(seen), now=now,
                             pending=pending, deferred=deferred)
    missing = [a for a in absent if a["is_failure"]]

    if missing:
        failures.append((
            "coverage",
            f"{len(missing)} closed window(s) with no row and no explanation: "
            + ", ".join(f"{a['label']} (closed {a['closes_ny']:%H:%M} NY)"
                        for a in missing),
        ))
    if duplicates:
        failures.append(("coverage", f"duplicated windows: {', '.join(duplicates)}"))
    if unexpected:
        failures.append(("coverage", f"windows not in the schedule: {', '.join(unexpected)}"))

    # --- 2. outcome + reason ---------------------------------------------
    for r in on_day:
        outcome = (r.get("outcome") or "").strip()
        reason = (r.get("reason") or "").strip()
        if outcome not in CERTIFIABLE_OUTCOMES:
            failures.append((
                "outcome",
                f"{r.get('window_label')}: outcome {outcome!r} is not a decided "
                f"outcome ({'/'.join(sorted(CERTIFIABLE_OUTCOMES))})",
            ))
        elif not reason:
            failures.append((
                "outcome", f"{r.get('window_label')}: {outcome} with an empty reason",
            ))

    # --- 3. range / stall rows -------------------------------------------
    for row_reason, label in ((REASON_OUTSIDE_RANGE, "outside_data_range"),
                              (csv_emitter.REASON_LIVE_FEED_STALLED, "live_feed_stalled")):
        hits = [r for r in on_day if (r.get("reason") or "").strip() == row_reason]
        if hits:
            failures.append((
                "range",
                f"{len(hits)} row(s) with {label}: "
                f"{', '.join(r.get('window_label', '?') for r in hits)}",
            ))

    # --- 4. gap rows ------------------------------------------------------
    gaps = [r for r in on_day
            if (r.get("reason") or "").strip() == csv_emitter.REASON_RUNNER_DOWN]
    if gaps:
        failures.append((
            "gaps",
            f"{len(gaps)} row(s) the runner did not observe: "
            f"{', '.join(r.get('window_label', '?') for r in gaps)}",
        ))

    # --- verdict ----------------------------------------------------------
    if failures:
        status = CERTIFY_FAIL
    elif absent:
        status = CERTIFY_INCOMPLETE
    else:
        status = CERTIFY_PASS

    if status == CERTIFY_PASS:
        traded = sum(1 for r in on_day if (r.get("outcome") or "") in ("WIN", "LOSS"))
        warnings.append(f"{traded} traded window(s), "
                        f"{len(on_day) - traded} NO_SETUP")

    return {
        "date": day,
        "applicable": True,
        "status": status,
        "passed": status == CERTIFY_PASS,
        "failures": failures,
        "warnings": warnings,
        "pending": absent,
        "detail": "",
        "counts": {
            "rows": len(on_day),
            "scheduled": len(scheduled),
            "win": sum(1 for r in on_day if (r.get("outcome") or "") == "WIN"),
            "loss": sum(1 for r in on_day if (r.get("outcome") or "") == "LOSS"),
            "no_setup": sum(1 for r in on_day if (r.get("outcome") or "") == "NO_SETUP"),
        },
        #: The honest answer to "when can I certify this?". The date's last
        #: window closes at 00:15 the following NY day, which is exactly the
        #: detail a person gets wrong when judging a day by its calendar date.
        "complete_after_ny": _last_close(day),
    }


def _last_close(day: dt.date):
    """NY naive wall clock at which every window of `day` has closed."""
    from src import time_windows

    return max(w.end for w in time_windows.windows_for_day(day))


def render_certification(result: dict) -> str:
    day = result["date"]
    if result["status"] == CERTIFY_NOT_APPLICABLE:
        return f"{day}: NOT APPLICABLE - {result['detail']}"

    c = result["counts"]
    pending = result["pending"]
    not_due = [p for p in pending if p["kind"] == ABSENT_NOT_DUE]
    trades = [p for p in pending if p["kind"] == ABSENT_AWAITING_TRADE]
    data = [p for p in pending if p["kind"] == ABSENT_AWAITING_DATA]
    missing = [p for p in pending if p["kind"] == ABSENT_MISSING]

    lines = [f"{day}  {result['status']}"]
    if result["status"] == CERTIFY_INCOMPLETE:
        if c["rows"] == 0 and len(not_due) == c["scheduled"]:
            lines[0] += " - this day has not started yet"
        else:
            lines[0] += " - too early to certify, this is not a failure"
    lines += [
        f"  rows            : {c['rows']}/{c['scheduled']} scheduled windows",
        f"  outcomes        : {c['win']} WIN, {c['loss']} LOSS, {c['no_setup']} NO_SETUP",
    ]
    for name, detail in result["failures"]:
        lines.append(f"  [FAIL] {name:<8} {detail}")
    if result["status"] == CERTIFY_PASS:
        for name, desc in CERTIFY_CONDITIONS:
            lines.append(f"  [ok]   {name:<8} {desc}")
        for w in result["warnings"]:
            lines.append(f"  note:  {w}")
        lines.append("  -> this day counts toward the five consecutive clean days")
    elif result["status"] == CERTIFY_INCOMPLETE:
        for name, desc in CERTIFY_CONDITIONS:
            lines.append(f"  [ok]   {name:<8} {desc} (nothing found against it yet)")
        if not_due:
            first = min(not_due, key=lambda p: p["closes_ny"])
            lines.append(
                f"  wait:  {len(not_due)} window(s) have not closed yet "
                f"(next: {first['label']}, closes {first['closes_ny']:%H:%M} NY)")
        if trades:
            lines.append(
                f"  wait:  {len(trades)} window(s) have an open trade still to "
                f"resolve: {', '.join(p['label'] for p in trades)}")
        if data:
            lines.append(
                f"  wait:  {len(data)} window(s) are holding for late bars: "
                f"{', '.join(p['label'] for p in data)}")
        if not missing:
            lines.append("  -> NOT a defect. Re-run when the day is complete.")
        last_close = result.get("complete_after_ny")
        if last_close and not_due:
            lines.append(f"  -> the day is final after {last_close:%Y-%m-%d %H:%M} NY")
    else:
        lines.append("  -> this day does NOT count; the consecutive run restarts here")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 5. pre-restart state inspection
# ---------------------------------------------------------------------------
def inspect_state(path: pathlib.Path) -> int:
    """Summarise `runner_state.json` and say what the next start will do with it.

    Written for the moment after an unclean kill, when the question is "is this
    file safe to restart on?" The answer is a property of what is inside it, not
    of a policy, so it is worth reading rather than guessing. Read-only.
    """
    import json

    if not path.exists():
        print(f"no state file at {path} — a first start will be treated as such")
        return 0
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        print(f"UNREADABLE state file ({type(exc).__name__}: {exc})")
        print("  The runner treats this as a first start: it will not crash, but")
        print("  the observation ledger is lost, so windows it had already watched")
        print("  may be logged as gaps. Conservative, never fabricated.")
        return 1

    def ts(key):
        v = raw.get(key)
        return (dt.datetime.fromtimestamp(v, dt.timezone.utc).isoformat()
                if v is not None else None)

    observations = [tuple(x) for x in raw.get("observations", [])]
    open_interval = bool(observations) and observations[-1][1] is None
    clean = bool(raw.get("clean_stop", False))
    pending = raw.get("pending", {}) or {}
    deferred = raw.get("deferred", {})

    print(f"state file            : {path}")
    print(f"first start utc       : {ts('first_start_utc')}")
    print(f"last heartbeat utc    : {ts('last_heartbeat_utc')}")
    print(f"clean_stop            : {clean}")
    print(f"observation intervals : {len(observations)}"
          f"{'  (last is OPEN -> a kill, not a stop)' if open_interval else ''}")
    print(f"pending trades        : {len(pending)}")
    for key in sorted(pending):
        print(f"    {key}")
    print(f"deferred windows      : {len(deferred)}")
    for key in sorted(deferred):
        print(f"    {key}")
    print()
    if clean:
        print("VERDICT: closed cleanly. Safe to restart; no gap rows expected from")
        print("         the previous session.")
    else:
        print("VERDICT: not closed cleanly. On the next start the runner will:")
        if open_interval:
            print("  - close the open observation interval at the last heartbeat, so")
            print("    the downtime is NOT counted as watched (this is what stops the")
            print("    outage being scored from back-filled history)")
        for key in sorted(pending):
            print(f"  - log {key} as dry_run_gap_runner_down: the trade was open when")
            print("    the process died and its exit is never inferred (Q14)")
        for key in sorted(deferred):
            print(f"  - log {key} as dry_run_gap_runner_down: its bars never arrived")
            print("    while the runner was alive")
        print("  This is expected and correct. Do not edit or delete this file:")
        print("  it is how the runner knows what it owed the log.")
    return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
#: Same directory `dry_run_runner.py` writes to by default, so an unsupervised
#: `python live/audit_triage.py --certify DATE` reads the files it is meant to.
DEFAULT_OUT_DIR = pathlib.Path(__file__).resolve().parent / "output"


def main(argv: list[str] | None = None,
         out_dir: str | pathlib.Path = DEFAULT_OUT_DIR) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out-dir", default=str(out_dir))
    ap.add_argument("--outside-range", action="store_true",
                    help="triage every outside_data_range row")
    ap.add_argument("--outages", action="append", default=None,
                    metavar="LO/HI",
                    help="known NY outage, e.g. 2026-10-26T14:22/2026-10-27T01:48")
    ap.add_argument("--monitor", default=None,
                    metavar="PATH",
                    help="dst_monitor.csv; classifies each row by measured feed "
                         "staleness instead of by a pre-supplied outage range")
    ap.add_argument("--stalls", action="store_true",
                    help="list every feed freeze visible in --monitor")
    ap.add_argument("--self-check", action="store_true",
                    help="re-derive every row's verdict from its own candles")
    ap.add_argument("--show", nargs=2, metavar=("DATE", "LABEL"),
                    help="print one window's full derivation")
    ap.add_argument("--annotate", action="store_true",
                    help="append reconciled rows for --known-bad")
    ap.add_argument("--known-bad", help="file of DATE LABEL lines to reconcile")
    ap.add_argument("--certify", default=None, metavar="DATE",
                    help="apply the 4-part clean-day rule to one NY trading date "
                         "(YYYY-MM-DD). Prints PASS / FAIL / INCOMPLETE with the "
                         "failing condition(s). INCOMPLETE means the day is not "
                         "over yet, and is not a defect.")
    ap.add_argument("--now", default=None, metavar="ISO",
                    help="treat this as the current NY wall clock (naive = New "
                         "York, or include an offset). Default: the real clock. "
                         "For replaying a past moment.")
    ap.add_argument("--state", default=None, metavar="PATH",
                    help="inspect runner_state.json before a restart (read-only)")
    ap.add_argument("--illegal-reasons", action="store_true",
                    help="flag any reason string that live code must never write")
    args = ap.parse_args(argv)

    if args.state:
        return inspect_state(pathlib.Path(args.state))

    base = pathlib.Path(args.out_dir)
    audit = read_rows(base / "window_evaluations_audit.csv")
    flat = read_rows(base / "window_evaluations.csv")
    if not audit and not args.certify:
        print(f"no audit rows at {base / 'window_evaluations_audit.csv'}", file=sys.stderr)
        return 2

    rc = 0
    outages = [parse_period(x) for x in args.outages] if args.outages else None
    monitor = None
    if args.monitor:
        monitor = load_monitor(pathlib.Path(args.monitor))
        if not monitor:
            print(f"no usable rows in {args.monitor}", file=sys.stderr)
            return 2

    if args.stalls and monitor is not None:
        print("=== feed freezes visible in the monitor (newest bar not advancing) ===")
        for s in stall_intervals(monitor):
            print(f"  bar frozen at {s['stalled_bar_ny']:%m-%d %H:%M} NY; "
                  f"observed frozen {s['frozen_from_ny']:%m-%d %H:%M} -> "
                  f"{s['observed_until_ny']:%m-%d %H:%M} "
                  f"({s['minutes']:.0f} min); newest bar "
                  f"{s['recovery_lag_minutes']:.0f} min behind wall clock")
        gaps = monitor_gaps(monitor)
        if gaps:
            print(f"\n=== monitor itself was down ({len(gaps)} span(s)) ===")
            for lo, hi in gaps:
                print(f"  {lo:%m-%d %H:%M} -> {hi:%m-%d %H:%M} "
                      f"({(hi - lo).total_seconds() / 60:.0f} min): "
                      f"no evidence either way for windows inside this span")

    if args.outside_range:
        rows, counts = triage_outside_range(audit, outages, monitor)
        print(f"{'date':>12} {'window':>13} {'shape':>17} {'a/b':>6} {'stale':>7}  verdict")
        for r in rows:
            stale = (f"{r['staleness_minutes']:.0f}m"
                     if r["staleness_minutes"] is not None else "?")
            print(f"{r['trading_date']:>12} {r['window_label']:>13} "
                  f"{r['shape']:>17} {r['bars_a']:>2}/{r['bars_b']:<2} {stale:>7}  "
                  f"{r['verdict']}")
        print(f"\ntotal {REASON_OUTSIDE_RANGE} rows: {len(rows)}")
        for verdict, n in sorted(counts.items()):
            print(f"  {verdict:<30} {n}")
        unexplained = sum(n for v, n in counts.items() if v.startswith("unexplained"))
        if unexplained:
            print(f"\n{unexplained} row(s) are not accounted for by the feed. "
                  f"Investigate before certifying any day.")
            rc = 1

    if args.certify:
        try:
            day = dt.date.fromisoformat(args.certify)
        except ValueError:
            print(f"--certify needs a date as YYYY-MM-DD, got {args.certify!r}",
                  file=sys.stderr)
            return 2
        now = _parse_now(args.now)
        if now is None:
            return 2
        # Certification reads the schema-mirror file; the audit CSV carries the
        # same columns, so a missing first choice degrades rather than lies.
        source = flat or audit
        # A *missing log file* is a usage error. A log file with no rows yet for
        # this date is not: on the morning of a trading day the runner may not
        # have written one, and that is INCOMPLETE, not a broken command.
        if not (base / "window_evaluations.csv").exists() and \
                not (base / "window_evaluations_audit.csv").exists():
            print(f"no dry-run log at {base} — nothing to certify from",
                  file=sys.stderr)
            return 2
        # The runner's own state file is what distinguishes "still open" from
        # "missing"; without it every unresolved trade would look like a defect.
        pending, deferred = load_certify_state(base / "runner_state.json")
        result = certify(day, source, now=now, pending=pending, deferred=deferred)
        print(render_certification(result))
        if not result["applicable"]:
            return 2
        if not flat:
            print("\nnote: certified from window_evaluations_audit.csv "
                  "(window_evaluations.csv absent)")
        if result["status"] == CERTIFY_FAIL:
            rc = 1
        elif result["status"] == CERTIFY_INCOMPLETE:
            rc = 3

    if args.illegal_reasons:
        # The audit CSV carries the same reason column, so fall back to it: a
        # missing window_evaluations.csv used to make this check examine nothing
        # and print a clean "0", which is the worst possible failure mode for a
        # checker whose whole job is to catch a vocabulary leak.
        source = flat or audit
        origin = ("window_evaluations.csv" if flat
                  else "window_evaluations_audit.csv (window_evaluations.csv absent)")
        bad = [r for r in source
               if (r.get("reason") or "").strip() not in LIVE_LEGAL_REASONS]
        print(f"\nexamined {len(source)} rows from {origin}")
        print(f"rows with a reason live code must never write: {len(bad)}")
        by_reason: dict[str, int] = {}
        for r in bad:
            by_reason[(r.get("reason") or "").strip()] = (
                by_reason.get((r.get("reason") or "").strip(), 0) + 1)
        for reason, n in sorted(by_reason.items(), key=lambda kv: -kv[1]):
            print(f"  {n:>3}x {reason!r}")
        if bad:
            rc = 1

    if args.self_check:
        problems = self_check(audit)
        print(f"\nself-check: {len(audit)} rows, {len(problems)} contradict their own candles")
        for p in problems:
            d = p["_derived"]
            print(f"  {p['trading_date']} {p['window_label']}: {p['_problem']}")
            print(f"      stored context={p.get('context')} reason={p.get('reason')!r}")
            print(f"      candles: low={d['low_a']} mid={d['mid_a']} high={d['high_a']} "
                  f"open_b={d['open_b']} -> {d['context']}")
            print(f"      open is {d['margin_from_low']} above Low_A, "
                  f"{d['margin_from_mid']} vs Mid_A")
        if problems:
            rc = 1

    if args.show:
        day, label = args.show
        match = [r for r in audit
                 if r["trading_date"] == day and r["window_label"] == label]
        if not match:
            print(f"no audit row for {day} {label}", file=sys.stderr)
            return 2
        print(show_row(match[0]))

    if args.annotate:
        if not args.known_bad:
            print("--annotate needs --known-bad", file=sys.stderr)
            return 2
        wanted = []
        for lineno, line in enumerate(
                pathlib.Path(args.known_bad).read_text().splitlines(), 1):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split()
            if len(parts) not in (2, 3):
                print(f"{args.known_bad}:{lineno}: expected DATE LABEL [CATEGORY]",
                      file=sys.stderr)
                return 2
            day, label = parts[0], parts[1]
            category = parts[2] if len(parts) == 3 else "stall"
            wanted.append((day, label, category))
        stamp = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
        path = base / "window_evaluations_reconciliation.csv"
        written = 0
        existing = {(r["trading_date"], r["window_label"])
                    for r in read_rows(path)}
        new = not path.exists() or path.stat().st_size == 0
        with path.open("a", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=RECONCILIATION_COLUMNS,
                                    extrasaction="ignore")
            if new:
                writer.writeheader()
            for day, label, category in wanted:
                if (day, label) in existing:
                    continue
                match = [r for r in audit
                         if r["trading_date"] == day and r["window_label"] == label]
                original = match[0]["reason"] if match else ""
                if category == "stall":
                    # We were watching and the feed delivered nothing. Naming it
                    # `dry_run_gap_runner_down` here would claim we were blind,
                    # which is the one thing that was not true.
                    corrected = csv_emitter.REASON_LIVE_FEED_STALLED
                    classification = "feed_stall_not_strategy"
                    note = ("reconciled append-only; the original row is unchanged. "
                            "Runner was connected and watching; the terminal "
                            "supplied no bars for this window.")
                elif category == "tail":
                    # The feed was healthy and the final bar was merely late. The
                    # correct evaluation was never performed and cannot be
                    # reconstructed without re-reading history, which Q14 forbids.
                    corrected = ""
                    classification = "transient_lag_not_evaluated"
                    note = ("reconciled append-only; the original row is unchanged. "
                            "Do not treat this window as an outcome — it was never "
                            "correctly evaluated.")
                else:
                    print(f"unknown category {category!r} for {day} {label}",
                          file=sys.stderr)
                    return 2
                writer.writerow({
                    "annotated_utc": stamp,
                    "trading_date": day,
                    "window_label": label,
                    "original_reason": original,
                    "corrected_reason": corrected,
                    "classification": classification,
                    "evidence": match[0].get("flags", "") if match else "",
                    "note": note,
                })
                written += 1
        print(f"\nreconciliation: {written} new row(s) appended to {path}")

    return rc


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
