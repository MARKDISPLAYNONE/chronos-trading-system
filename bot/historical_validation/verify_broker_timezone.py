"""
Project Chronos — Broker Timezone Verification Script
=====================================================

CORRECTED IN PHASE 1. This file previously implemented the opposite convention
and its two output columns could not both match the MT5 chart. It now implements
the convention that the historical export actually proves, and it is the
reference implementation of docs/02-TRD.md §4.1.1.

THE RULE
--------
MT5's bar `time` field is **broker server time encoded as if it were Unix epoch
UTC**. Forex.com / GAIN Capital run "NY+7": the server clock is always exactly
7 hours ahead of New York local time, following US DST dates. Because the broker
and New York change clocks on the same dates, the offset between them is a
**constant 7 hours year-round**. So:

    New York wall clock = utc_label(epoch) - 7 hours

That subtraction is the ENTIRE conversion. No `zoneinfo`, no `pytz`, no DST
lookup, no broker-offset branching. The DST shift is already baked into the raw
value and the constant subtraction cancels it identically in both regimes:

    EDT period: broker UTC+3 -> -7h -> UTC-4  (New York)   CORRECT
    EST period: broker UTC+2 -> -7h -> UTC-5  (New York)   CORRECT

Adding a second, DST-aware conversion on top of this would double-count the
shift and move every bar by an hour for half the year. Do not do it.

WHY THIS IS NOT OPTIONAL
------------------------
Read under the old assumption ("the epoch is true UTC"), every session boundary
drifts when DST changes:

    daily break   19:00-20:00 NY in summer, 18:00-19:00 NY in winter
    Friday close  19:59 NY in summer, 18:59 NY in winter

Read with the correction, they are fixed local times all year: break
17:00-18:00, Friday close 16:59, Sunday open 18:00. An exchange does not move
its session when a timezone changes, so only the corrected reading is physical.
The same evidence also explains the strategy's three deliberately skipped
windows (15:45-18:15), which bracket that 17:00-18:00 break.

HOW TO RUN
----------
1. OFFLINE, against an exported CSV (no MT5, any OS, deterministic):

       python3 verify_broker_timezone.py --csv ../data/nas100_m1_history.csv

   Exit code 0 = every session invariant holds. Non-zero = the file does not
   satisfy the convention and must not be used for strategy work.

2. LIVE, against a running MT5 terminal (Windows only), which additionally
   prints recent bars so a human can compare against the terminal's own chart:

       python3 verify_broker_timezone.py

RE-RUN THIS after every US DST transition date (2nd Sunday March, 1st Sunday
November). If the invariants stop holding, STOP bot development and escalate —
do not patch in a new hardcoded offset.
"""

from __future__ import annotations

import argparse
import csv
import sys
from datetime import datetime, timedelta, timezone

# ---------------------------------------------------------------------------
# The single load-bearing constant. Broker clock = New York local + 7 hours.
# Constant year-round precisely because both change on US DST dates.
# ---------------------------------------------------------------------------
BROKER_OFFSET_HOURS = 7

# ---------------------------------------------------------------------------
# Session invariants this venue must satisfy, in New York local time.
# ---------------------------------------------------------------------------
DAILY_BREAK = ("17:00", "18:00")   # platform maintenance / rollover, Mon-Thu
FRIDAY_CLOSE = "16:59"             # last M1 bar of the week
SUNDAY_OPEN = "18:00"              # first M1 bar of the week

TICK_SIZE = 0.1


# ---------------------------------------------------------------------------
# Conversion — deliberately arithmetic only
# ---------------------------------------------------------------------------
def broker_wall_clock(epoch: int) -> datetime:
    """Decode the raw epoch as the broker's wall clock (naive).

    `utc` is used here purely as a decoding device to read the label; no
    timezone semantics are attached to the result.
    """
    return datetime.fromtimestamp(epoch, timezone.utc).replace(tzinfo=None)


def new_york_wall_clock(epoch: int) -> datetime:
    """Broker wall clock minus the constant 7 hours. THE whole conversion."""
    return broker_wall_clock(epoch) - timedelta(hours=BROKER_OFFSET_HOURS)


def _hm(t: datetime) -> str:
    return t.strftime("%H:%M")


# ---------------------------------------------------------------------------
# Verification
# ---------------------------------------------------------------------------
def verify(bars: list[tuple[int, float, float, float, float, int]]) -> tuple[bool, list[tuple[str, bool, str]]]:
    """Check the session invariants. Returns (all_passed, rows)."""
    if not bars:
        return False, [("input", False, "no bars to verify")]

    ny = [(t, new_york_wall_clock(t)) for t, *_ in bars]
    rows: list[tuple[str, bool, str]] = []

    # 1. No Saturday trading anywhere.
    sat = sum(1 for _, n in ny if n.weekday() == 5)
    rows.append(("no Saturday bars", sat == 0, f"{sat} Saturday bars"))

    # 2. No Mon-Thu bar inside the daily break.
    in_break = sum(1 for _, n in ny if n.weekday() < 4 and n.hour == 17)
    rows.append(
        (
            f"daily break {DAILY_BREAK[0]}-{DAILY_BREAK[1]} NY",
            in_break == 0,
            f"{in_break} Mon-Thu bars inside 17:00-18:00 NY",
        )
    )

    # 3. Fridays end at 16:59 NY, never later (half-days may end earlier).
    by_date: dict[object, list[datetime]] = {}
    for _, n in ny:
        by_date.setdefault(n.date(), []).append(n)
    fri_last = {d: ts[-1] for d, ts in by_date.items() if d.weekday() == 4}
    late = sum(1 for n in (t for _, t in ny) if n.weekday() == 4 and _hm(n) > FRIDAY_CLOSE)
    at_close = sum(1 for t in fri_last.values() if _hm(t) == FRIDAY_CLOSE)
    ok = bool(fri_last) and late == 0 and at_close / len(fri_last) >= 0.8
    rows.append(
        (
            f"Friday close {FRIDAY_CLOSE} NY",
            ok,
            f"{at_close}/{len(fri_last)} Fridays close at {FRIDAY_CLOSE}, {late} bars later than that",
        )
    )

    # 4. Sundays open at 18:00 NY.
    sun_first = {d: ts[0] for d, ts in by_date.items() if d.weekday() == 6}
    early = sum(1 for n in (t for _, t in ny) if n.weekday() == 6 and _hm(n) < SUNDAY_OPEN)
    at_open = sum(1 for t in sun_first.values() if _hm(t) == SUNDAY_OPEN)
    ok = bool(sun_first) and early == 0 and at_open / len(sun_first) >= 0.95
    rows.append(
        (
            f"Sunday open {SUNDAY_OPEN} NY",
            ok,
            f"{at_open}/{len(sun_first)} Sundays open at {SUNDAY_OPEN}, {early} bars earlier than that",
        )
    )

    return all(r[1] for r in rows), rows


def _read_csv(path: str) -> list[tuple[int, float, float, float, float, int]]:
    """Read the agreed CSV contract: timestamp_utc,open,high,low,close,volume."""
    out = []
    with open(path, newline="", encoding="utf-8") as fh:
        reader = csv.reader(fh)
        header = next(reader, None)
        if header is None or [h.strip() for h in header][:6] != [
            "timestamp_utc", "open", "high", "low", "close", "volume",
        ]:
            raise SystemExit(f"ERROR: unexpected CSV header in {path}: {header}")
        for row in reader:
            if not row or not row[0].strip():
                continue
            out.append(
                (
                    int(row[0]),
                    float(row[1]),
                    float(row[2]),
                    float(row[3]),
                    float(row[4]),
                    int(float(row[5])),
                )
            )
    return out


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------
def report(rows: list[tuple[str, bool, str]], source: str, n_bars: int) -> int:
    print()
    print("Broker timezone verification — convention: broker clock = NY + 7h (constant)")
    print(f"Source: {source}   Bars: {n_bars:,}")
    print("=" * 96)
    for name, passed, detail in rows:
        print(f"  [{'PASS' if passed else 'FAIL'}] {name:<28} {detail}")
    print("=" * 96)
    if all(r[1] for r in rows):
        print("\nRESULT: the file satisfies the NY+7 constant-offset convention.")
        print("        Use it for strategy work. New York wall clock = utc_label(epoch) - 7h.")
        return 0
    print("\nRESULT: this file does NOT satisfy the convention.")
    print("        Do NOT evaluate strategy windows against it. Investigate the export")
    print("        before proceeding — see docs/02-TRD.md 4.1.1.")
    return 1


def print_sample(bars: list[tuple[int, float, float, float, float, int]], n: int = 10) -> None:
    """Show the conversion for the most recent bars, for chart cross-checking."""
    print()
    print(f"Most recent {min(n, len(bars))} bars")
    print("-" * 96)
    print(f"{'Raw epoch':>12} | {'Broker clock (= raw label)':>26} | {'New York (raw - 7h)':>20} | {'Last price':>10}")
    print("-" * 96)
    for epoch, _o, _h, _l, c, _v in bars[-n:]:
        print(
            f"{epoch:>12} | {broker_wall_clock(epoch).strftime('%Y-%m-%d %H:%M'):>26} | "
            f"{new_york_wall_clock(epoch).strftime('%Y-%m-%d %H:%M'):>20} | {c:>10.1f}"
        )
    print("-" * 96)
    print("Cross-check: the 'Broker clock' column is what the MT5 terminal's own")
    print("chart shows for that bar. 'New York' is what every strategy window in")
    print("PRD 6.1 is defined against. No DST conversion is applied to either.")


# ---------------------------------------------------------------------------
# Modes
# ---------------------------------------------------------------------------
def run_csv(path: str) -> int:
    bars = _read_csv(path)
    if not bars:
        print(f"ERROR: no bars in {path}")
        return 2
    bars.sort(key=lambda b: b[0])
    ok, rows = verify(bars)
    print_sample(bars)
    return report(rows, path, len(bars))


def run_live(symbol: str, count: int) -> int:
    try:
        import MetaTrader5 as mt5
    except ImportError:
        print("ERROR: MetaTrader5 is not installed (Windows only).")
        print("       Use --csv against an exported file instead:")
        print("       python3 verify_broker_timezone.py --csv ../data/nas100_m1_history.csv")
        return 2

    if not mt5.initialize():
        print(f"ERROR: mt5.initialize() failed. Error code: {mt5.last_error()}")
        return 2
    try:
        if not mt5.symbol_select(symbol, True):
            print(f"ERROR: could not select {symbol} in Market Watch.")
            return 2
        rates = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_M1, 0, count)
        if rates is None or len(rates) == 0:
            print(f"ERROR: no rates returned for {symbol}.")
            return 2
        bars = [
            (
                int(r["time"]),
                float(r["open"]),
                float(r["high"]),
                float(r["low"]),
                float(r["close"]),
                int(r["tick_volume"]),
            )
            for r in rates
        ]
    finally:
        mt5.shutdown()

    ok, rows = verify(bars)
    print_sample(bars)
    print("\nNOTE: a live window of a few hundred bars may not span a full week, so")
    print("      the Friday/Sunday checks can report 'no data'. Use --csv with a")
    print("      multi-month export for the complete verification.")
    return report(rows, f"MT5 live ({symbol})", len(bars))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Verify the MT5 broker timezone convention (NY+7).")
    ap.add_argument("--csv", help="verify an exported M1 CSV instead of a live terminal")
    ap.add_argument("--symbol", default="NAS100", help="symbol for live mode (default NAS100)")
    ap.add_argument("--bars", type=int, default=5000, help="live mode: how many M1 bars to pull")
    args = ap.parse_args(argv)
    if args.csv:
        return run_csv(args.csv)
    return run_live(args.symbol, args.bars)


if __name__ == "__main__":
    sys.exit(main())
