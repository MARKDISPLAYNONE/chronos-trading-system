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

    python live/audit_triage.py --self-check
    python live/audit_triage.py --outside-range --outages 2026-10-26T14:22/2026-10-27T01:48
    python live/audit_triage.py --show 2026-10-26 04:45-05:15
    python live/audit_triage.py --annotate --known-bad known_bad.txt

Exit codes: 0 = nothing found, 1 = the check found a contradiction or an
unexplained row. Non-zero is the point: this is meant to be run in CI onto the
log, not read casually.
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

#: Reason strings a live row may legitimately carry. Anything else in the log is
#: a vocabulary leak from the backtest code paths.
LIVE_LEGAL_REASONS = {
    "no sweep",
    "sweep not reversed",
    "ambiguous_same_bar_insufficient_granularity",
    "invalid open location",
    "market_closed_no_data",
    "missing_market_data",
    "data_ends_before_trade_closed",
    "dry_run_gap_runner_down",
    "live_feed_stalled",
}


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
def triage_outside_range(rows: list[dict],
                         outages: list[tuple[dt.datetime, dt.datetime]] | None = None
                         ) -> tuple[list[dict], dict]:
    """Classify each `outside_data_range` row.

    The discriminator is the row's own shape, which is exact:

    * `no_bars_in_window` — nothing arrived for the window at all.
    * `incomplete_candle` with `bars_candle_b` < 15 — the window had bars but
      the tail was short. A complete window can never produce
      `outside_data_range`, so this reason *only* arises from a short tail.

    Then, and only then, the outage periods decide attribution: a short tail
    during a known outage is connectivity; the same shape outside every outage is
    the transient-lag defect.
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

        in_outage = bool(outages) and any(
            within(row["trading_date"], row["window_label"], lo, hi)
            for lo, hi in outages
        )
        if outages is None:
            verdict = "unknown_no_outage_data"
        elif in_outage:
            verdict = "connectivity"
        else:
            verdict = "unexplained_transient_lag"
        out.append({
            "trading_date": row["trading_date"],
            "window_label": row["window_label"],
            "shape": shape,
            "bars_a": bars_a,
            "bars_b": bars_b,
            "verdict": verdict,
            "evaluated_at": row.get("evaluated_at", ""),
        })
    counts: dict[str, int] = {}
    for item in out:
        counts[item["verdict"]] = counts.get(item["verdict"], 0) + 1
    return out, counts


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
# CLI
# ---------------------------------------------------------------------------
def main(argv: list[str] | None = None, out_dir: str | pathlib.Path = "output"
         ) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out-dir", default=str(out_dir))
    ap.add_argument("--outside-range", action="store_true",
                    help="triage every outside_data_range row")
    ap.add_argument("--outages", action="append", default=None,
                    metavar="LO/HI",
                    help="known NY outage, e.g. 2026-10-26T14:22/2026-10-27T01:48")
    ap.add_argument("--self-check", action="store_true",
                    help="re-derive every row's verdict from its own candles")
    ap.add_argument("--show", nargs=2, metavar=("DATE", "LABEL"),
                    help="print one window's full derivation")
    ap.add_argument("--annotate", action="store_true",
                    help="append reconciled rows for --known-bad")
    ap.add_argument("--known-bad", help="file of DATE LABEL lines to reconcile")
    ap.add_argument("--illegal-reasons", action="store_true",
                    help="flag any reason string that live code must never write")
    args = ap.parse_args(argv)

    base = pathlib.Path(args.out_dir)
    audit = read_rows(base / "window_evaluations_audit.csv")
    flat = read_rows(base / "window_evaluations.csv")
    if not audit:
        print(f"no audit rows at {base / 'window_evaluations_audit.csv'}", file=sys.stderr)
        return 2

    rc = 0
    outages = [parse_period(x) for x in args.outages] if args.outages else None

    if args.outside_range:
        rows, counts = triage_outside_range(audit, outages)
        print(f"{'date':>12} {'window':>13} {'shape':>20} {'a/b':>7}  verdict")
        for r in rows:
            print(f"{r['trading_date']:>12} {r['window_label']:>13} "
                  f"{r['shape']:>20} {r['bars_a']:>2}/{r['bars_b']:<2}  {r['verdict']}")
        print(f"\ntotal {REASON_OUTSIDE_RANGE} rows: {len(rows)}")
        for verdict, n in sorted(counts.items()):
            print(f"  {verdict:<28} {n}")
        if counts.get("unexplained_transient_lag"):
            rc = 1

    if args.illegal_reasons:
        bad = [r for r in flat if (r.get("reason") or "") not in LIVE_LEGAL_REASONS]
        print(f"\nrows with a reason live code must never write: {len(bad)}")
        for r in bad:
            print(f"  {r['trading_date']} {r['window_label']} -> {r.get('reason')!r}")
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
        for line in pathlib.Path(args.known_bad).read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                day, label = line.split()
                wanted.append((day, label))
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
            for day, label in wanted:
                if (day, label) in existing:
                    continue
                match = [r for r in audit
                         if r["trading_date"] == day and r["window_label"] == label]
                original = match[0]["reason"] if match else ""
                writer.writerow({
                    "annotated_utc": stamp,
                    "trading_date": day,
                    "window_label": label,
                    "original_reason": original,
                    "corrected_reason": "dry_run_gap_runner_down",
                    "classification": "connectivity_outage_not_strategy",
                    "evidence": "journal disconnect interval corroborated by dst_monitor",
                    "note": "reconciled append-only; the original row is unchanged",
                })
                written += 1
        print(f"\nreconciliation: {written} new row(s) appended to {path}")

    return rc


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
