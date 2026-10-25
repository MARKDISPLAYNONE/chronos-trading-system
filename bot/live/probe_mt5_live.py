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

FAILURE ISOLATION (fixed after the first live run)
--------------------------------------------------
The first run on the Handler's terminal crashed on `SymbolInfo.session_quotes`,
an attribute this package build does not have. It took down the clock, the
rates and the forming-bar sections — the parts that actually matter. Two changes
make that class of bug impossible to repeat:

  * every non-essential attribute is read through `field()`, which returns a
    placeholder instead of raising, and is reported as a cosmetic note;
  * each section runs inside its own guard, so a failure in one is recorded and
    the probe **continues**. A missing cosmetic field can never cost you the
    offset measurement again.

A section that raises is still reported as an escalation — the point is that the
rest of the evidence is collected first.

SAFETY
------
This script only reads. It calls `initialize`, `terminal_info`, `account_info`,
`symbol_info`, `symbol_info_tick`, `copy_rates_from_pos` and `last_error`.
It does not import or reference `order_send`, `order_check` or any trading
function, and it changes no account state. Read-only by construction.

HOW TO RUN (Windows, MT5 terminal open and logged into the demo account)
-----------------------------------------------------------------------
    python live/probe_mt5_live.py
    python live/probe_mt5_live.py --symbol NAS100 --bars 3000

Exit code 0 = every check that can pass live passed.
Exit code 1 = something is not what Phase 1 assumed: send the output back,
change nothing, and let the Senior Dev rule on it.

Paste the whole output back to the Agent. It contains no credentials: the login
number is masked and no password is ever read.
"""

from __future__ import annotations

import argparse
import calendar
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Phase 1's single source of truth for the conversion. Reused, not re-derived:
# if this probe disagrees with the adapter later, one of them is wrong and the
# disagreement is the bug.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src import config  # noqa: E402

DAILY_BREAK_HOUR = 17          # 17:00-18:00 NY, Mon-Thu (TRD §4.1.1)
FRIDAY_CLOSE = "16:59"         # last M1 bar of the week
SUNDAY_OPEN = "18:00"          # first M1 bar of the week

#: printed instead of a value when the package build lacks an attribute
ABSENT = "<absent on this build>"


def field(obj, name: str, default=ABSENT):
    """Read an optional attribute without ever raising.

    MT5's named tuples vary between package builds — `SymbolInfo` on the
    Handler's terminal has no `session_quotes`, for example. These fields are
    cosmetic for our purposes, so their absence is a note, never a failure.
    """
    try:
        return getattr(obj, name, default)
    except Exception:  # a property that raises is, for our purposes, absent
        return default


def as_dict(obj) -> dict:
    """Best-effort field dump, so an unknown build's schema is still visible."""
    for attempt in ("_asdict",):
        fn = getattr(obj, attempt, None)
        if callable(fn):
            try:
                return dict(fn())
            except Exception:
                pass
    try:
        return {k: v for k, v in vars(obj).items() if not k.startswith("_")}
    except Exception:
        return {}


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


def rate_times(rates) -> list[int]:
    """Pull the integer `time` field out of a rates array, whatever it looks like.

    Handles numpy structured arrays (`rates["time"]`), a sequence of dicts, and
    a sequence of tuples whose declared dtype names give the column order.
    """
    if rates is None:
        return []
    try:
        column = rates["time"]
        return [int(t) for t in column]
    except Exception:
        pass
    names = getattr(getattr(rates, "dtype", None), "names", None)
    idx = list(names).index("time") if names and "time" in names else 0
    out: list[int] = []
    for row in rates:
        try:
            out.append(int(row["time"]))
        except Exception:
            out.append(int(row[idx]))
    return out


# ---------------------------------------------------------------------------
# Findings
# ---------------------------------------------------------------------------
class Findings:
    """Escalations vs cosmetic notes. Only escalations change the exit code."""

    def __init__(self) -> None:
        self.problems: list[str] = []
        self.notes: list[str] = []

    def problem(self, msg: str) -> None:
        self.problems.append(msg)

    def note(self, msg: str) -> None:
        self.notes.append(msg)


def section(title: str, findings: Findings):
    """Decorator: a failing section is recorded and the probe continues.

    This is the direct fix for the first live run, where one absent attribute
    aborted every section after it.
    """
    def wrap(fn):
        def run(*a, **kw):
            print(f"\n--- {title} ---")
            try:
                return fn(*a, **kw)
            except Exception as exc:  # noqa: BLE001 - deliberate catch-all
                print(f"  [SECTION FAILED] {type(exc).__name__}: {exc}")
                print("  (continuing — the remaining sections still run)")
                findings.problem(
                    f"section '{title}' failed: {type(exc).__name__}: {exc}"
                )
                return None
        run.section_title = title  # type: ignore[attr-defined]
        return run
    return wrap


# ---------------------------------------------------------------------------
# Sections (all MT5 access lives here)
# ---------------------------------------------------------------------------
def main(argv: list[str] | None = None, mt5_module=None) -> int:
    ap = argparse.ArgumentParser(description="Read-only MT5 probe for Phase 2")
    ap.add_argument("--symbol", default=config.INSTRUMENT_SYMBOL)
    ap.add_argument("--bars", type=int, default=3000, help="how many M1 bars to pull")
    args = ap.parse_args(argv)

    if mt5_module is not None:
        mt5 = mt5_module            # injected by the tests
    else:
        try:
            import MetaTrader5 as mt5  # noqa: N813  (Windows-only, lazy on purpose)
        except Exception as exc:  # pragma: no cover - depends on host OS
            print("FATAL: the MetaTrader5 package is not importable here.")
            print(f"       {type(exc).__name__}: {exc}")
            print("       Run this on the Windows machine that hosts the terminal.")
            return 1

    findings = Findings()
    print("=" * 96)
    print("Project Chronos — Phase 2 live probe (read-only)")
    print("=" * 96)
    print(f"MetaTrader5 package version : {field(mt5, '__version__', 'unknown')}")
    print(f"Instrument requested        : {args.symbol}")

    if not mt5.initialize():
        print(f"FATAL: initialize() failed — last_error = {mt5.last_error()}")
        print("       Is the terminal installed, running and logged in?")
        return 1

    state: dict = {}

    # ---- 1. terminal + account ------------------------------------------
    @section("terminal and account", findings)
    def read_environment():
        ti = mt5.terminal_info()
        ai = mt5.account_info()
        if ti is None or ai is None:
            findings.problem(
                f"terminal_info()/account_info() returned None "
                f"(last_error={mt5.last_error()})"
            )
            return
        state["terminal"], state["account"] = ti, ai
        print(f"Terminal                    : {field(ti, 'name')} "
              f"build {field(ti, 'build')}, connected={field(ti, 'connected')}")
        login = field(ai, "login", "")
        masked = ("*" * max(0, len(str(login)) - 2)) + str(login)[-2:] if login else "n/a"
        print(f"Account                     : {field(ai, 'company')} / "
              f"server {field(ai, 'server')} / login {masked}")
        print(f"Account currency / leverage : {field(ai, 'currency')} / "
              f"1:{field(ai, 'leverage')}")
        print(f"trade_mode                  : {field(ai, 'trade_mode')}  "
              f"(0=demo, 1=contest, 2=real)")
        if field(ai, "trade_mode") != 0:
            findings.problem("account is NOT a demo account — Phase 2 must not run here")
        if field(ti, "connected") is not True:
            findings.problem("terminal reports disconnected")

    # ---- 2. symbol spec --------------------------------------------------
    @section("symbol spec", findings)
    def read_symbol():
        info = mt5.symbol_info(args.symbol)
        if info is None:
            findings.problem(
                f"symbol {args.symbol!r} not found on this terminal "
                f"(last_error={mt5.last_error()}) — do not substitute a lookalike"
            )
            return
        state["symbol"] = info
        print(f"name / path                 : {field(info, 'name')} / {field(info, 'path')}")
        print(f"digits / point              : {field(info, 'digits')} / "
              f"{field(info, 'point')}")
        print(f"trade_tick_size             : {field(info, 'trade_tick_size')}")
        print(f"trade_tick_value            : {field(info, 'trade_tick_value')}")
        print(f"volume_min / step / max     : {field(info, 'volume_min')} / "
              f"{field(info, 'volume_step')} / {field(info, 'volume_max')}")
        print(f"trade_mode / filling_mode   : {field(info, 'trade_mode')} / "
              f"{field(info, 'filling_mode')}")

        spread = field(info, "spread", None)
        point = field(info, "point", None)
        if spread is None:
            findings.note("`spread` is absent on this build")
        else:
            pts = f" = {spread * point:.2f} price points" if point else ""
            print(f"spread (now)                : {spread} points{pts}")
            findings.note(
                f"live spread measured at {spread} points{pts}. Phase 1 and Phase 2 "
                f"both assume zero spread, so this changes no Phase 2 number — but "
                f"it is the first real execution cost we have a number for, and "
                f"Phase 3 will need it."
            )

        # cosmetic session counters: present on some builds, absent on others
        deals = field(info, "session_deals", None)
        quotes = field(info, "session_quotes", None)
        if deals is None or quotes is None:
            print(f"session_deals / quotes      : "
                  f"{deals if deals is not None else ABSENT} / "
                  f"{quotes if quotes is not None else ABSENT}")
            findings.note(
                "SymbolInfo on this build lacks 'session_deals'/'session_quotes' "
                "(cosmetic counters, unused by the engine). Not an escalation; the "
                "probe now guards optional fields so their absence cannot abort the run."
            )
        else:
            print(f"session_deals / quotes      : {deals} / {quotes}")

        tick_size = field(info, "trade_tick_size", None) or point
        if tick_size is None:
            findings.problem("neither trade_tick_size nor point is available")
        elif abs(float(tick_size) - float(config.TICK_SIZE)) > 1e-12:
            findings.problem(
                f"trade_tick_size={tick_size} does not match Phase 1's "
                f"config.TICK_SIZE={config.TICK_SIZE} — quantization is built on 0.1"
            )

        names = getattr(getattr(info, "dtype", None), "names", None) or list(as_dict(info))
        if names:
            print(f"fields on this SymbolInfo   : {len(names)} -> {', '.join(map(str, names))}")

    # ---- 3. clock: measured, not inferred --------------------------------
    @section("clock (the load-bearing assumption)", findings)
    def read_clock():
        import time as _time
        tick = mt5.symbol_info_tick(args.symbol)
        ts = field(tick, "time", None) if tick is not None else None
        if not ts:
            findings.problem(
                "symbol_info_tick() gave no timestamp — the broker clock could not "
                "be measured, which is the one check the CSV cannot substitute for"
            )
            return
        state["now_broker"] = int(ts)
        now_utc = _time.time()
        off = measure_offset_hours(int(ts), now_utc)
        print(f"last tick, broker stamp     : {decode_broker_label(ts)} (raw broker label)")
        print(f"true UTC now (system clock) : "
              f"{datetime.fromtimestamp(now_utc, timezone.utc).replace(tzinfo=None)}")
        print(f"measured broker - UTC       : {off:+.2f} h   (expect +2.00 winter / +3.00 summer)")
        print(f"NY now, per TRD 4.1.1 (-7h) : {to_ny(ts)}")
        try:
            from zoneinfo import ZoneInfo
            ny_true = datetime.now(timezone.utc).astimezone(ZoneInfo("America/New_York")).replace(tzinfo=None)
            drift = abs((to_ny(ts) - ny_true).total_seconds())
            print(f"NY now, per system tz       : {ny_true}   (EST/EDT cross-check only)")
            print(f"agreement, -7h rule vs system tz: {drift:.0f} s"
                  f"{'  OK' if drift < 120 else '  <-- ESCALATE: they disagree'}")
            if drift >= 120:
                findings.problem(
                    f"constant -7h gives NY {to_ny(ts)} but the system's own "
                    f"America/New_York says {ny_true} ({drift:.0f}s apart)"
                )
        except Exception as exc:  # zoneinfo/tzdata missing is not fatal
            findings.note(f"system-timezone cross-check skipped: {type(exc).__name__}: {exc}")
        if not (1.5 <= off <= 3.5):
            findings.problem(
                f"measured broker-UTC offset {off:+.2f}h is outside 2-3h; the NY+7 "
                f"model does not hold on this feed"
            )
        else:
            print(f"NY+7 model                  : CONSISTENT with a US-DST broker")

    # ---- 4. bars ---------------------------------------------------------
    @section("M1 bars", findings)
    def read_rates():
        rates = mt5.copy_rates_from_pos(args.symbol, mt5.TIMEFRAME_M1, 0, args.bars)
        if rates is None or len(rates) == 0:
            findings.problem(
                f"copy_rates_from_pos returned nothing (last_error={mt5.last_error()})"
            )
            return
        state["rates"] = rates
        print(f"rows returned               : {len(rates)}")
        print(f"dtype names                 : {field(getattr(rates, 'dtype', None), 'names')}")
        try:
            print(f"first row                   : {tuple(rates[0])}")
            print(f"last row                    : {tuple(rates[-1])}")
        except Exception as exc:
            findings.note(f"row dump skipped: {type(exc).__name__}: {exc}")

        bar_times = rate_times(rates)
        if not bar_times:
            findings.problem("could not extract a 'time' column from the rates array")
            return
        state["bar_times"] = bar_times
        print(f"`time` is an integer epoch  : "
              f"{all(isinstance(t, int) for t in bar_times[:50])}")
        print(f"first / last bar (broker)   : {decode_broker_label(bar_times[0])} / "
              f"{decode_broker_label(bar_times[-1])}")

        gaps = [(a, b) for a, b in zip(bar_times, bar_times[1:]) if b - a > 60]
        print(f"gaps between consecutive M1 : {len(gaps)}"
              + (f" (largest {(max(b - a for a, b in gaps)) // 60} min)" if gaps else ""))
        for a, b in gaps[-3:]:
            print(f"    {decode_broker_label(a)} -> {decode_broker_label(b)}  "
                  f"({(b - a) // 60} min)")

        now_broker = state.get("now_broker")
        if now_broker is None:
            findings.note("forming-bar check skipped: the broker clock was not measured")
        else:
            fi = forming_bar_index(bar_times, now_broker)
            print(f"newest bar still forming?   : "
                  f"{'YES — index ' + str(fi) + ', must be dropped' if fi is not None else 'no'}")
            if fi is not None:
                print("    NOTE: the CSV never contained a forming bar. The live adapter")
                print("          must exclude it or Candle A/B can be built from an")
                print("          unfinished candle. This is a CSV->live difference, logged.")

    # ---- 5. invariants on live bars --------------------------------------
    @section("session invariants on live bars", findings)
    def check_invariants():
        bar_times = state.get("bar_times")
        if not bar_times:
            findings.note("invariant check skipped: no bars were read")
            return
        ny_times = [to_ny(t) for t in bar_times]
        print(f"NY range (constant -7h)     : {ny_times[0]} -> {ny_times[-1]}")
        print("")
        for name, ok, detail in session_invariants(ny_times):
            print(f"  [{'PASS' if ok else 'FAIL'}] {name:<36} {detail}")
            if not ok:
                findings.problem(f"live invariant failed: {name} ({detail})")

        seen: dict[str, int] = {}
        for t in ny_times:
            seen[t.strftime("%a")] = seen.get(t.strftime("%a"), 0) + 1
        print("\nweekday coverage in this window: "
              + "  ".join(f"{k}:{v}" for k, v in sorted(seen.items())))

    for fn in (read_environment, read_symbol, read_clock, read_rates, check_invariants):
        fn()

    try:
        mt5.shutdown()
    except Exception as exc:  # pragma: no cover
        findings.note(f"shutdown() raised {type(exc).__name__}: {exc}")

    # ---- verdict ---------------------------------------------------------
    print("\n" + "=" * 96)
    if findings.notes:
        print("NOTES (cosmetic, not escalations):")
        for n in findings.notes:
            print(f"  - {n}")
    if findings.problems:
        print("\nRESULT: ESCALATE — the live feed is not what Phase 1 assumed:")
        for p in findings.problems:
            print(f"  • {p}")
        print("Send this output back before any Phase 2 code is written against it.")
        return 1
    print("\nRESULT: all live checks passed. Phase 2 may be built against these facts.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
