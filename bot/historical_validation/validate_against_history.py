#!/usr/bin/env python3
"""
Project Chronos — Phase 1 historical validation driver
=====================================================

Reads an M1 CSV, evaluates all 21 windows for every Mon-Fri date the file
covers, writes the schema-mirror outputs and a Markdown validation report.

Phase 1 scope only: no orders, no broker, no database, no secrets.

    python3 historical_validation/validate_against_history.py \
        --csv ../data/nas100_m1_history.csv

Exit codes:
    0  all evaluations produced, coverage invariant holds
    2  input rejected (bad CSV, untrustworthy clock, failed invariants)
    3  coverage invariant violated (missing/duplicate window rows)
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys
from pathlib import Path

# Allow `python3 historical_validation/validate_against_history.py` from bot/.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src import config, outputs, report  # noqa: E402
from src.m1_csv_source import AdapterError, convention_scores, load_bars  # noqa: E402
from src.time_windows import trading_days, windows_for_day  # noqa: E402
from src.window_evaluator import evaluate_window  # noqa: E402

DEFAULT_CSV = Path(__file__).resolve().parents[2] / "data" / "nas100_m1_history.csv"
DEFAULT_OUT = Path(__file__).resolve().parent / "output"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Phase 1 logic validation over historical M1 data")
    p.add_argument("--csv", default=str(DEFAULT_CSV), help="M1 CSV in the agreed contract")
    p.add_argument(
        "--convention",
        default=None,
        choices=["broker", "utc", "auto"],
        help="timestamp convention; 'auto' (default) detects it from the data "
        "and refuses to guess if ambiguous",
    )
    p.add_argument("--out-dir", default=str(DEFAULT_OUT), help="output directory")
    p.add_argument("--start", default=None, help="first NY date to evaluate (YYYY-MM-DD)")
    p.add_argument("--end", default=None, help="last NY date to evaluate (YYYY-MM-DD)")
    p.add_argument("--no-report", action="store_true", help="skip Markdown report")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    started = dt.datetime.now()

    # --- load + gate -------------------------------------------------------
    try:
        evidence = convention_scores(args.csv)
        convention = None if args.convention in (None, "auto") else args.convention
        series, load_report = load_bars(args.csv, convention=convention, validate=True)
    except AdapterError as exc:
        print(f"INPUT REJECTED: {exc}", file=sys.stderr)
        return 2

    print(f"Loaded {load_report.n_bars:,} M1 bars from {args.csv}")
    print(f"  convention        : {load_report.convention}")
    print(f"  NY wall-clock span: {load_report.first_ny} -> {load_report.last_ny}")
    for name, check in load_report.session_checks.items():
        print(f"  invariant {name:28s} {'PASS' if check['passed'] else 'FAIL'} ({check['detail']})")

    # --- determine the evaluation range -----------------------------------
    first_date = series.first_time.date()
    last_date = series.last_time.date()
    if args.start:
        first_date = dt.date.fromisoformat(args.start)
    if args.end:
        last_date = dt.date.fromisoformat(args.end)

    dates = trading_days(first_date, last_date)
    print(f"\nEvaluating {len(dates)} trading dates x {len(config.__dict__) and 21} windows "
          f"({len(dates) * 21:,} window evaluations) from {first_date} to {last_date}")

    # --- evaluate ----------------------------------------------------------
    evaluations = []
    for day in dates:
        for window in windows_for_day(day):
            evaluations.append(evaluate_window(series, window, config.TICK_SIZE))

    # --- coverage invariant ------------------------------------------------
    expected = len(dates) * 21
    per_date = {}
    for ev in evaluations:
        per_date[ev.trading_date] = per_date.get(ev.trading_date, 0) + 1
    bad = {d: c for d, c in per_date.items() if c != 21}
    dupes = len(evaluations) - len({(e.trading_date, e.window_label) for e in evaluations})

    out_dir = Path(args.out_dir)
    paths = outputs.write_all(out_dir, evaluations)

    # spot-check subset, written with the audit columns
    chosen = report.select_spot_check(evaluations, config.SPOT_CHECK_TARGET)
    outputs._write_csv(
        out_dir / "spot_check.csv",
        outputs.AUDIT_COLUMNS,
        [outputs.audit_row(e) for e in chosen],
    )

    summary = report.summarize(evaluations)
    duration = (dt.datetime.now() - started).total_seconds()

    if not args.no_report:
        text = report.render(
            evaluations=evaluations,
            series=series,
            load_report=load_report,
            convention_evidence=evidence,
            started_at=started,
            duration_s=duration,
            source_path=args.csv,
        )
        report.write(out_dir / "phase1_validation_report.md", text)

    # --- console summary ---------------------------------------------------
    print("\n=== OUTCOME DISTRIBUTION ===")
    for key in ("WIN", "LOSS", "NO_SETUP", "MISSING_EVALUATION"):
        c = summary["outcomes"].get(key, 0)
        if c:
            print(f"  {key:20s} {c:6d}  ({100 * c / len(evaluations):5.1f}%)")
    print("\n=== NO_SETUP / NON-TRADE REASONS ===")
    for reason, c in sorted(summary["reasons"].items(), key=lambda kv: -kv[1]):
        print(f"  {reason:46s} {c:6d}")
    print("\n=== TRADES (R, zero-cost fills) ===")
    print(f"  trades={summary['traded']}  wins={summary['wins']}  "
          f"losses={summary['losses']}  win_rate={100 * summary['wins'] / max(1, summary['traded']):.1f}%")
    print(f"  net_R={summary['net_r']:+.3f}  avg_R={summary['avg_r']:+.3f}  "
          f"partials={summary['partials']}")
    print("\n=== COVERAGE ===")
    print(f"  expected rows {expected:,} | produced {len(evaluations):,} | "
          f"dates with wrong count {len(bad)} | duplicate (date,window) {dupes}")

    print("\n=== OUTPUTS ===")
    for name, path in paths.items():
        print(f"  {name:20s} {path}")
    if not args.no_report:
        print(f"  {'report':20s} {out_dir / 'phase1_validation_report.md'}")
    print(f"  {'spot_check':20s} {out_dir / 'spot_check.csv'}")
    print(f"\nDone in {duration:.1f}s")

    if bad or dupes:
        print("\nCOVERAGE INVARIANT VIOLATED", file=sys.stderr)
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
