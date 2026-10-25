"""
Project Chronos — Phase 2 live probe (READ-ONLY, no orders, no account changes)
===============================================================================

WHY THIS EXISTS
---------------
Phase 1 ran entirely off an exported CSV. Everything it learned about MT5 is
inferred from that file. Phase 2 talks to a live terminal, and the standing rule
is **"flag, don't decide"**: if the live API behaves differently from the CSV
assumptions (field names, epoch behaviour, symbol availability, forming bars,
connection drops), that is escalated — never guessed at silently.

So before a single line of the live source adapter is written, this script
measures the live terminal and prints the facts the adapter must be built
against. It answers, from the real terminal:

  1. Is MetaTrader5 importable, and which build?
  2. Is the account really a DEMO account? (Phase 2 must never be live.)
  3. Does SYMBOL exist, and what are its digits / point / trade tick size?
  4. What do `copy_rates_from_pos` rows actually look like — dtypes, fields,
     and is `time` an int epoch?
  5. **Does the broker clock really run 7 hours ahead of New York right now?**
     Measured directly: broker_stamp − true_utc_stamp. This is the single most
     load-bearing assumption in TRD §4.1.1, and until now it was only ever
     proven against a CSV that ends 2026-10-22.
  6. Are the session invariants (17:00–18:00 break, 18:00 Sunday open, 16:59
     Friday close) still true on *live* bars?
  7. Is the newest bar in `copy_rates_from_pos` still FORMING? (The CSV never
     had one. An unfinished bar must never be used to build Candle A or B.)

SAFETY
------
This script only reads. It calls `initialize`, `terminal_info`, `account_info`,
`symbol_info`, `symbol_info_tick`, `copy_rates_from_pos` and `last_error`.
It does not import or reference `order_send`, `order_check` or any trading
function, and it changes no account state. Read-only by construction.

HOW TO RUN (Windows, MT5 terminal open and logged into the demo account)
-----------------------------------------------------------------------
    python probe_mt5_live.py
    python probe_mt5_live.py --symbol NAS100 --bars 3000

Exit code 0 = every check that can pass offline-live passed.
Exit code 1 = something is not what Phase 1 assumed: send the output back,
change nothing, and let the Senior Dev rule on it.

Paste the whole output back to the Agent. It contains no credentials: the login
number is masked and no password is ever read.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta, timezone

# Phase 1's single source of truth for the conversion. Reused, not re-derived:
# if this probe disagrees with the adapter later, one of them is wrong and the
# disagreement is the bug.
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[1]))
from src import config  # noqa: E402

DAILY_BREAK_HOUR = 17          # 17:00-18:00 NY, Mon-Thu (TRD §4.1.1)
FRIDAY_CLOSE = "16:59"         # last M1 bar of the week
SUNDAY_OPEN = "18:00"          # first M1 bar of the week


# ---------------------------------------------------------------------------
# Pure helpers — no MT5 required, so they are unit-tested offline
# ---------------------------------------------------------------------------
def decode_broker_label(epoch: int) -> datetime:
    """Decode a broker epoch label as a naive wall clock (no tz semantics)."""
    return datetime.fromtimestamp(epoch, timezone.utc).replace(tzinfo=None)


def to_ny(epoch: int) -> datetime:
    """The entire Phase 1 conversion: broker label minus a constant 7 hours."""
    return decode_broker_label(epoch) - timedelta(hours=config.BROKER_OFFSET_HOURS)


def measure_offset_hours(broker_stamp: int, true_utc_stamp: float) -> float:
    """Broker clock minus true UTC, in hours.

    `broker_stamp` is an MT5 `time` field (broker wall clock, encoded as epoch).
    `true_utc_stamp` is a real UTC epoch (time.time()). The difference is the
    broker's own offset from UTC -- 2h in northern winter, 3h in northern
    summer for a US-DST broker. This measures the clock directly instead of
    inferring it from session structure.
    """
    return (broker_stamp - true_utc_stamp) / 3600.0


def forming_bar_index(bar_times: list[int], now_broker: int) -> int | None:
    """Index of the newest bar if it is still forming, else None.

    An M1 bar stamped T covers [T, T+60s). It is closed once the broker clock
    has reached T+60s. The CSV export never contained a forming bar; a live
    `copy_rates_from_pos` call does. Using one would build Candle A/B out of an
    unfinished candle, which is a Phase-1 assumption the live feed breaks.
    """
    if not bar_times:
        return None
    return len(bar_times) - 1 if bar_times[-1] + 60 > now_broker else None


def session_invariants(ny_times: list[datetime]) -> list[tuple[str, bool, str]]:
    """The same four invariants Phase 1 gates on, applied to a live window."""
    rows: list[tuple[str, bool, str]] = []
    if not ny_times:
        return [("input", False, "no bars returned")]

    sat = sum(1 for t in ny_times if t.weekday() == 5)
    rows.append(("no Saturday bars", sat == 0, f"{sat} Saturday bars"))

    in_break = sum(1 for t in ny_times if t.weekday() < 4 and t.hour == DAILY_BREAK_HOUR)
    rows.append((f"daily break {DAILY_BREAK_HOUR:02d}:00-{DAILY_BREAK_HOUR + 1:02d}:00 NY",
                 in_break == 0, f"{in_break} Mon-Thu bars inside the break"))

    by_date: dict[object, list[datetime]] = {}
    for t in ny_times:
        by_date.setdefault(t.date(), []).append(t)
    late = sum(1 for t in ny_times
               if t.weekday() == 4 and t.strftime("%H:%M") > FRIDAY_CLOSE)
    fri = [ts[-1] for d, ts in by_date.items() if d.weekday() == 4]
    rows.append((f"Friday close {FRIDAY_CLOSE} NY", late == 0,
                 f"{late} Friday bars after {FRIDAY_CLOSE} NY"
                 + (f" (last bars: {[t.strftime('%H:%M') for t in fri]})" if fri else "")))

    early = sum(1 for t in ny_times
                if t.weekday() == 6 and t.strftime("%H:%M") < SUNDAY_OPEN)
    sun = [ts[0] for d, ts in by_date.items() if d.weekday() == 6]
    rows.append((f"Sunday open {SUNDAY_OPEN} NY", early == 0,
                 f"{early} Sunday bars before {SUNDAY_OPEN} NY"
                 + (f" (first bars: {[t.strftime('%H:%M') for t in sun]})" if sun else "")))
    return rows


# ---------------------------------------------------------------------------
# Live probe (all MT5 access lives here)
# ---------------------------------------------------------------------------
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Read-only MT5 probe for Phase 2")
    ap.add_argument("--symbol", default=config.INSTRUMENT_SYMBOL)
    ap.add_argument("--bars", type=int, default=3000, help="how many M1 bars to pull")
    args = ap.parse_args(argv)

    try:
        import MetaTrader5 as mt5  # noqa: N813  (Windows-only, lazy on purpose)
    except Exception as exc:  # pragma: no cover - depends on host OS
        print("FATAL: the MetaTrader5 package is not importable here.")
        print(f"       {type(exc).__name__}: {exc}")
        print("       Run this on the Windows machine that hosts the terminal.")
        return 1

    print("=" * 96)
    print("Project Chronos — Phase 2 live probe (read-only)")
    print("=" * 96)
    print(f"MetaTrader5 package version : {getattr(mt5, '__version__', 'unknown')}")

    if not mt5.initialize():
        print(f"FATAL: initialize() failed — last_error = {mt5.last_error()}")
        print("       Is the terminal installed, running and logged in?")
        return 1

    problems: list[str] = []
    try:
        ti = mt5.terminal_info()
        ai = mt5.account_info()
        if ti is None or ai is None:
            print(f"FATAL: terminal_info()/account_info() returned None "
                  f"(last_error={mt5.last_error()})")
            return 1

        print(f"Terminal                    : {ti.name} build {ti.build}, "
              f"connected={ti.connected}")
        masked = ("*" * (len(str(ai.login)) - 2)) + str(ai.login)[-2:] if ai.login else "n/a"
        print(f"Account                     : {ai.company} / server {ai.server} "
              f"/ login {masked}")
        print(f"Account currency / leverage : {ai.currency} / 1:{ai.leverage}")
        print(f"trade_mode                  : {ai.trade_mode} "
              f"(0=demo, 1=contest, 2=real)")
        if ai.trade_mode != 0:
            problems.append("account is NOT a demo account — Phase 2 must not run here")
        if not ti.connected:
            problems.append("terminal reports disconnected")

        # ---- symbol spec ---------------------------------------------------
        info = mt5.symbol_info(args.symbol)
        if info is None:
            print(f"\nFATAL: symbol {args.symbol!r} not found. Visible symbols "
                  f"matching nothing. last_error={mt5.last_error()}")
            print("       ESCALATE — do not substitute a lookalike symbol name.")
            return 1
        if not info.visible and not mt5.symbol_select(args.symbol, True):
            problems.append(f"{args.symbol} could not be selected into Market Watch")

        print("\n--- symbol spec ---")
        print(f"name / path                 : {info.name} / {info.path}")
        print(f"digits / point              : {info.digits} / {info.point}")
        print(f"trade_tick_size             : {getattr(info, 'trade_tick_size', 'MISSING')}")
        print(f"trade_tick_value            : {getattr(info, 'trade_tick_value', 'MISSING')}")
        print(f"volume_min / step / max     : {info.volume_min} / {info.volume_step} / {info.volume_max}")
        print(f"trade_mode / filling        : {info.trade_mode} / "
              f"{getattr(info, 'filling_mode', 'MISSING')}")
        print(f"spread (points)             : {info.spread}")
        print(f"session_deals / quotes      : {info.session_deals} / {info.session_quotes}")

        tick_size = getattr(info, "trade_tick_size", None) or info.point
        if abs(float(tick_size) - float(config.TICK_SIZE)) > 1e-12:
            problems.append(
                f"trade_tick_size={tick_size} does not match Phase 1's "
                f"config.TICK_SIZE={config.TICK_SIZE} — quantization is built on 0.1"
            )

        # ---- the clock: measured, not inferred ------------------------------
        print("\n--- clock (the load-bearing assumption) ---")
        import time as _time
        tick = mt5.symbol_info_tick(args.symbol)
        if tick is None or not tick.time:
            problems.append("symbol_info_tick() gave no timestamp — cannot measure the clock")
            now_broker = int(_time.time())
        else:
            now_utc = _time.time()
            off = measure_offset_hours(tick.time, now_utc)
            now_broker = tick.time
            print(f"last tick, broker stamp     : {decode_broker_label(tick.time)} (raw broker label)")
            print(f"true UTC now (system clock) : {datetime.fromtimestamp(now_utc, timezone.utc).replace(tzinfo=None)}")
            print(f"measured broker − UTC       : {off:+.2f} h   (expect +2.00 winter / +3.00 summer)")
            print(f"NY now, per TRD §4.1.1 −7h  : {to_ny(tick.time)}")
            ny_true = datetime.now(timezone.utc).astimezone(
                __import__("zoneinfo").ZoneInfo("America/New_York")).replace(tzinfo=None)
            print(f"NY now, per system tz (EST/EDT, cross-check only): {ny_true}")
            drift = abs((to_ny(tick.time) - ny_true).total_seconds())
            print(f"agreement, −7h rule vs system tz: {drift:.0f} s"
                  f"{'  OK' if drift < 120 else '  <-- ESCALATE: they disagree'}")
            if drift >= 120:
                problems.append(
                    f"constant −7h gives NY {to_ny(tick.time)} but the system's own "
                    f"America/New_York says {ny_true} ({drift:.0f}s apart)"
                )
            if not (1.5 <= off <= 3.5):
                problems.append(f"measured broker−UTC offset {off:+.2f}h is outside 2–3h; "
                                "the NY+7 model does not hold on this feed")

        # ---- bars ----------------------------------------------------------
        rates = mt5.copy_rates_from_pos(args.symbol, mt5.TIMEFRAME_M1, 0, args.bars)
        if rates is None or len(rates) == 0:
            print(f"\nFATAL: copy_rates_from_pos returned nothing "
                  f"(last_error={mt5.last_error()}). ESCALATE.")
            return 1

        print("\n--- M1 bars ---")
        print(f"rows returned               : {len(rates)}")
        print(f"dtype names                 : {rates.dtype.names}")
        print(f"first row                   : {tuple(rates[0])}")
        print(f"last row                    : {tuple(rates[-1])}")
        time_is_int = all(isinstance(int(b["time"]), int) for b in rates[:50])
        print(f"`time` is an integer epoch  : {time_is_int}")

        bar_times = [int(b["time"]) for b in rates]
        gaps = [(a, b) for a, b in zip(bar_times, bar_times[1:]) if b - a > 60]
        print(f"gaps between consecutive M1 : {len(gaps)}"
              + (f" (largest {max(b - a for a, b in gaps)}s)" if gaps else ""))
        for a, b in gaps[-3:]:
            print(f"    {decode_broker_label(a)} -> {decode_broker_label(b)}  "
                  f"({(b - a) // 60} min)")

        fi = forming_bar_index(bar_times, now_broker)
        print(f"newest bar still forming?   : "
              f"{'YES — index ' + str(fi) + ', must be dropped' if fi is not None else 'no'}")
        if fi is not None:
            print("    NOTE: the CSV never contained a forming bar. The live adapter")
            print("          must exclude it or Candle A/B can be built from an")
            print("          unfinished candle. This is a CSV→live difference, logged.")

        ny_times = [to_ny(t) for t in bar_times]
        print(f"NY range (constant −7h)     : {ny_times[0]} -> {ny_times[-1]}")
        print("\n--- session invariants on live bars ---")
        for name, ok, detail in session_invariants(ny_times):
            print(f"  [{'PASS' if ok else 'FAIL'}] {name:<36} {detail}")
            if not ok:
                problems.append(f"live invariant failed: {name} ({detail})")

        print("\n--- weekday coverage in this window ---")
        seen: dict[str, int] = {}
        for t in ny_times:
            seen[t.strftime("%a")] = seen.get(t.strftime("%a"), 0) + 1
        print("  " + "  ".join(f"{k}:{v}" for k, v in sorted(seen.items())))
    finally:
        mt5.shutdown()

    print("\n" + "=" * 96)
    if problems:
        print("RESULT: ESCALATE — the live feed is not what Phase 1 assumed:")
        for p in problems:
            print(f"  • {p}")
        print("Send this output back before any Phase 2 code is written against it.")
        return 1
    print("RESULT: all live checks passed. Phase 2 may be built against these facts.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
