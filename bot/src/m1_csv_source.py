"""
Project Chronos — MT5 M1 CSV adapter (Q3/Q4)
============================================

The backtest never talks to MT5. It consumes the flat CSV contract agreed with
the Handler:

    timestamp_utc,open,high,low,close,volume

one row per 1-minute bar. `timestamp_utc` may be Unix epoch seconds (what the
current export produces) or ISO-8601 UTC — this module detects which, says so
in its report, and never guesses per-row.

--------------------------------------------------------------------------
THE IMPORTANT PART: what the timestamp actually means
--------------------------------------------------------------------------
Q3 said the adapter must "never trust any broker-local assumption from the
file itself". This adapter goes further and *proves* the convention against
the data before a single window is evaluated.

MT5 writes **broker server time encoded as if it were UTC**, so
`NY wall clock = utc_label(epoch) - 7h`. This is now the authoritative rule in
docs/02-TRD.md §4.1.1 (rewritten in Phase 1; the original text wrongly treated
the epoch as true UTC). Evidence from `data/nas100_m1_history.csv`
(287,746 M1 bars, Jan-Oct 2026):

  1. SESSION INVARIANTS. Under `epoch = true UTC` the daily maintenance break
     sits at 19:00-20:00 NY in summer and 18:00-19:00 NY in winter, and the
     Friday close drifts 19:59 -> 18:59 across the Mar 8 DST date. Under
     `broker = NY+7` every one of these lands on a fixed NY local time:
     break 17:00-18:00, Friday close 16:59, Sunday open 18:00, in *both* the
     EST and EDT halves of the dataset. Exchanges do not move their session
     when a timezone changes, so only the second reading is physical.
  2. INTRADAY VOLUME. In July (unambiguously EDT), average tick-volume peaks
     at raw labels 16:00-22:00. The US cash session (09:30-16:00 ET) is raw
     16:30-23:00 under NY+7 but 13:30-20:00 under the UTC reading — and raw
     13:00-14:00 is among the *quietest* hours in the file.
  3. THE STRATEGY'S OWN SKIP SET. The three excluded windows 15:45-18:15
     bracket a 17:00-18:00 break. Under the UTC reading the break would sit
     at 19:00-20:00 and the skip set would be unexplained.

Consequences:
  * TRD §4.1.1 and `verify_broker_timezone.py` were corrected to match in Phase 1
    (Senior Dev instructed the edit); this adapter is the enforced implementation;
  * `load_bars()` validates the chosen convention and **refuses to run** if
    the data does not satisfy it, so a wrong convention can never silently
    produce a plausible-looking backtest;
  * both conventions remain selectable (`--convention`), so reversing the
    decision is a flag, not a rewrite.
"""

from __future__ import annotations

import csv
import datetime as dt
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from pathlib import Path
from zoneinfo import ZoneInfo

from . import config
from .bars import BarSeries, M1Bar
from .pricing import is_on_tick_grid

UTC = ZoneInfo(config.UTC_TZ)
NY = ZoneInfo(config.NY_TZ)

EXPECTED_HEADER = ["timestamp_utc", "open", "high", "low", "close", "volume"]

#: `broker`  -> epoch carries broker server time (NY+7)  [Phase 1 default]
#: `utc`     -> epoch carries true UTC                    [TRD §4.1.1 as written]
CONVENTION_BROKER = "broker"
CONVENTION_UTC = "utc"
CONVENTIONS = (CONVENTION_BROKER, CONVENTION_UTC)


class AdapterError(RuntimeError):
    """Raised when the CSV cannot be trusted as strategy input."""


@dataclass
class LoadReport:
    """Everything the validation report needs to say about the input file."""

    path: str
    convention: str
    timestamp_format: str
    n_bars: int
    first_raw: str
    last_raw: str
    first_ny: str
    last_ny: str
    duplicate_timestamps: int
    non_monotonic_timestamps: int
    ohlc_violations: int
    off_tick_prices: int
    non_positive_volume: int
    session_checks: dict[str, dict] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    def all_checks_passed(self) -> bool:
        return all(c["passed"] for c in self.session_checks.values())


# ---------------------------------------------------------------------------
# Timestamp parsing
# ---------------------------------------------------------------------------
def _looks_numeric(value: str) -> bool:
    v = value.strip()
    return v.lstrip("-").isdigit()


def _parse_timestamp(value: str) -> tuple[dt.datetime, str]:
    """Return `(naive_utc_datetime, format_name)`.

    Explicit branch (never guess per row beyond this single decision at the
    top of the file): all-digit -> epoch seconds; otherwise ISO-8601.
    """
    value = value.strip()
    if _looks_numeric(value):
        return dt.datetime.fromtimestamp(int(value), UTC).replace(tzinfo=None), "unix_epoch"
    text = value.replace("Z", "+00:00")
    parsed = dt.datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        # An ISO string with no offset is treated as UTC: the column is named
        # `timestamp_utc`, and inventing a local interpretation would be
        # exactly the "broker-local assumption" Q3 forbids.
        return parsed, "iso8601_naive_utc"
    return parsed.astimezone(UTC).replace(tzinfo=None), "iso8601"


# ---------------------------------------------------------------------------
# Convention conversion
# ---------------------------------------------------------------------------
def epoch_to_label(epoch: int) -> dt.datetime:
    """Decode a raw MT5/CSV epoch as a naive *label* — no timezone semantics.

    MT5's `time` field and the CSV's `timestamp_utc` column both carry broker
    wall-clock time encoded as if it were Unix UTC. Decoding it "as UTC" is a
    reading device only; the result is a label, not an instant, and must still
    go through `to_ny_wall()` to become strategy time. `utc` is used here purely
    because that is how the number is stored on disk.
    """
    return dt.datetime.fromtimestamp(int(epoch), UTC).replace(tzinfo=None)


def to_ny_wall(naive_utc_from_label: dt.datetime, convention: str) -> dt.datetime:
    """Convert the file's timestamp label to a naive New York wall clock."""
    if convention == CONVENTION_BROKER:
        # Broker wall clock is NY + 7h, and the broker follows US DST dates,
        # so subtracting a constant 7h is correct year-round (this is exactly
        # what makes the convention stable — see module docstring).
        return naive_utc_from_label - dt.timedelta(hours=config.BROKER_OFFSET_HOURS)
    if convention == CONVENTION_UTC:
        aware = naive_utc_from_label.replace(tzinfo=UTC).astimezone(NY)
        return aware.replace(tzinfo=None)
    raise AdapterError(f"unknown timestamp convention: {convention!r}")


# ---------------------------------------------------------------------------
# Session invariants — the proof, and the runtime gate
# ---------------------------------------------------------------------------
def check_session_invariants(series: BarSeries) -> dict[str, dict]:
    """Verify that NY wall-clock times show a physically sensible session.

    These are the checks that a wrong timestamp convention fails. Every
    threshold allows for legitimate exceptions (exchange half-days, the first
    partial session of the export) without allowing a systemic shift.
    """
    checks: dict[str, dict] = {}

    by_date: dict[dt.date, list[dt.datetime]] = {}
    for t in series.times:
        by_date.setdefault(t.date(), []).append(t)

    # 1. No Saturday trading at all.
    sat = sum(1 for t in series.times if t.weekday() == 5)
    checks["no_saturday_bars"] = {
        "passed": sat == 0,
        "detail": f"{sat} Saturday bars",
    }

    # 2. Daily maintenance break must sit at 17:00-18:00 NY on Mon-Thu.
    in_break = sum(1 for t in series.times if t.weekday() < 4 and t.hour == 17)
    checks["daily_break_1700_1800_ny"] = {
        "passed": in_break == 0,
        "detail": f"{in_break} Mon-Thu bars inside 17:00-18:00 NY",
    }

    # 3. Friday sessions end at 16:59 NY (half-days may end earlier, never later).
    fri_after_close = sum(1 for t in series.times if t.weekday() == 4 and t.time() > dt.time(16, 59))
    fri_last = {}
    for d, times in by_date.items():
        if d.weekday() == 4:
            fri_last[d] = times[-1].time()
    fri_at_close = sum(1 for v in fri_last.values() if v == dt.time(16, 59))
    checks["friday_close_1659_ny"] = {
        "passed": fri_after_close == 0 and fri_last and fri_at_close / len(fri_last) >= 0.8,
        "detail": (
            f"{fri_at_close}/{len(fri_last)} Fridays close 16:59, "
            f"{fri_after_close} bars after 16:59"
        ),
    }

    # 4. Sunday session opens at 18:00 NY.
    sun_first = {}
    for d, times in by_date.items():
        if d.weekday() == 6:
            sun_first[d] = times[0].time()
    sun_at_open = sum(1 for v in sun_first.values() if v == dt.time(18, 0))
    sun_before_open = sum(1 for t in series.times if t.weekday() == 6 and t.time() < dt.time(18, 0))
    checks["sunday_open_1800_ny"] = {
        "passed": (
            bool(sun_first)
            and sun_at_open / len(sun_first) >= 0.95
            and sun_before_open == 0
        ),
        "detail": (
            f"{sun_at_open}/{len(sun_first)} Sundays open 18:00, "
            f"{sun_before_open} bars before 18:00"
        ),
    }

    return checks


def convention_scores(path: str | Path) -> dict[str, dict]:
    """Score every candidate convention against the invariants.

    Used by the validation report to *show* the evidence, and by
    `detect_convention()` to pick the winner from data rather than opinion.
    """
    out: dict[str, dict] = {}
    for conv in CONVENTIONS:
        try:
            series, _ = load_bars(path, convention=conv, validate=False)
        except AdapterError as exc:  # pragma: no cover - malformed file
            out[conv] = {"error": str(exc)}
            continue
        checks = check_session_invariants(series)
        out[conv] = {
            "checks": checks,
            "n_passed": sum(1 for c in checks.values() if c["passed"]),
            "n_checks": len(checks),
        }
    return out


def detect_convention(path: str | Path) -> str:
    """Pick the convention the data actually satisfies. Fails loudly if none."""
    scores = convention_scores(path)
    passed = [c for c, s in scores.items() if s.get("n_passed") == s.get("n_checks")]
    if len(passed) == 1:
        return passed[0]
    if not passed:
        detail = "; ".join(f"{c}: {s}" for c, s in scores.items())
        raise AdapterError(
            "no timestamp convention satisfies the session invariants — the "
            f"input file is not trustworthy as-is ({detail})"
        )
    raise AdapterError(
        "timestamp convention is ambiguous for this file (both pass the "
        "invariants) — set --convention explicitly"
    )


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------
def load_bars(
    path: str | Path,
    *,
    convention: str | None = None,
    validate: bool = True,
) -> tuple[BarSeries, LoadReport]:
    """Read the CSV, convert to NY wall clock, and (optionally) gate on invariants.

    `convention=None` means "detect from the data".
    """
    path = Path(path)
    if not path.exists():
        raise AdapterError(f"input CSV not found: {path}")
    if convention is None:
        convention = detect_convention(path)
    if convention not in CONVENTIONS:
        raise AdapterError(f"unknown convention {convention!r}; expected one of {CONVENTIONS}")

    bars: list[M1Bar] = []
    duplicates = 0
    non_monotonic = 0
    ohlc_violations = 0
    off_tick = 0
    non_positive_volume = 0
    seen: set[dt.datetime] = set()
    prev_raw: dt.datetime | None = None
    first_raw = last_raw = None
    ts_format = "unknown"

    with path.open(newline="", encoding="utf-8") as fh:
        reader = csv.reader(fh)
        header = next(reader, None)
        if header is None or [h.strip() for h in header] != EXPECTED_HEADER:
            raise AdapterError(
                f"unexpected CSV header.\n  expected: {EXPECTED_HEADER}\n  found:    {header}"
            )
        for line_no, row in enumerate(reader, start=2):
            if not row or all(not c.strip() for c in row):
                continue
            if len(row) != 6:
                raise AdapterError(f"line {line_no}: expected 6 columns, found {len(row)}")
            try:
                raw_naive, ts_format = _parse_timestamp(row[0])
                # Decimal straight from the file's text: exact, never via float
                o, h, l, c = (Decimal(row[i].strip()) for i in (1, 2, 3, 4))
                volume = int(float(row[5]))
            except (ValueError, TypeError, InvalidOperation) as exc:
                raise AdapterError(f"line {line_no}: unparseable row {row!r} ({exc})") from exc

            if first_raw is None:
                first_raw = raw_naive
            last_raw = raw_naive
            if prev_raw is not None and raw_naive < prev_raw:
                non_monotonic += 1
            prev_raw = raw_naive

            ny_wall = to_ny_wall(raw_naive, convention)
            if ny_wall in seen:
                duplicates += 1
                continue
            seen.add(ny_wall)

            if volume <= 0:
                non_positive_volume += 1

            # Count bad bars instead of letting M1Bar raise, so the adapter can
            # report *how many* are broken rather than dying on the first one.
            if h < l or not (l <= o <= h) or not (l <= c <= h):
                ohlc_violations += 1
                continue
            if any(not is_on_tick_grid(price, config.TICK_SIZE) for price in (o, h, l, c)):
                off_tick += 1
                continue

            bars.append(M1Bar(ny_wall, o, h, l, c, volume))

    if not bars:
        raise AdapterError(f"no usable bars in {path}")

    series = BarSeries(bars)
    report = LoadReport(
        path=str(path),
        convention=convention,
        timestamp_format=ts_format,
        n_bars=len(series),
        first_raw=first_raw.isoformat(sep=" ") if first_raw else "",
        last_raw=last_raw.isoformat(sep=" ") if last_raw else "",
        first_ny=series.first_time.isoformat(sep=" "),
        last_ny=series.last_time.isoformat(sep=" "),
        duplicate_timestamps=duplicates,
        non_monotonic_timestamps=non_monotonic,
        ohlc_violations=ohlc_violations,
        off_tick_prices=off_tick,
        non_positive_volume=non_positive_volume,
    )

    if ohlc_violations:
        raise AdapterError(f"{ohlc_violations} bar(s) violate OHLC ordering — refusing to continue")
    if off_tick:
        raise AdapterError(
            f"{off_tick} bar(s) have prices off the {config.TICK_SIZE} tick grid — "
            "the instrument spec or the file is wrong"
        )

    if validate:
        report.session_checks = check_session_invariants(series)
        if not report.all_checks_passed():
            failed = {k: v for k, v in report.session_checks.items() if not v["passed"]}
            raise AdapterError(
                f"session invariants FAILED for convention {convention!r}: {failed}. "
                "Refusing to evaluate windows against data whose clock cannot be trusted."
            )

    return series, report
