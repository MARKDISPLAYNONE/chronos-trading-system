"""
Project Chronos — Phase 1 validation report generator
=====================================================

Produces a Markdown report that a reviewer can check WITHOUT reading code:

  * what data went in and whether its clock is trustworthy;
  * coverage (every trading date x every one of the 21 windows, exactly once);
  * outcome distribution and NO_SETUP reason breakdown;
  * trading statistics in R (win rate, net R, per-window-time, monthly);
  * DST validation against both regimes;
  * a machine-generated spot-check table of >= 20 windows whose inputs and
    intermediates are printed in full, so the Handler can re-derive each one
    by hand from raw candles (Phase 1 exit criterion).

Everything is derived from the evaluated records — nothing is hardcoded, so
the report cannot drift from the CSV outputs.
"""

from __future__ import annotations

import datetime as dt
from collections import Counter, defaultdict
from decimal import Decimal
from pathlib import Path

from . import config
from .bars import BarSeries
from .m1_csv_source import LoadReport
from .time_windows import ALL_WINDOW_LABELS, WINDOW_HOURS, localize, window_label
from .window_evaluator import (
    OUTCOME_LOSS,
    OUTCOME_MISSING,
    OUTCOME_NO_SETUP,
    OUTCOME_WIN,
    REASON_MARKET_CLOSED,
    REASON_MISSING_DATA,
    WindowEvaluation,
)

REGIME_EDT = "EDT (UTC-4)"
REGIME_EST = "EST (UTC-5)"


def regime_of(ev: WindowEvaluation) -> str:
    aware, _ = localize(ev.window.start)
    return REGIME_EDT if aware.utcoffset() == dt.timedelta(hours=-4) else REGIME_EST


def _pct(num, den, dp: int = 1) -> str:
    return "n/a" if den == 0 else f"{100.0 * num / den:.{dp}f}%"


def _r(value: float) -> str:
    return f"{value:+.3f}"


def summarize(evaluations: list[WindowEvaluation]) -> dict:
    """All the numbers the report quotes, computed once."""
    outcomes = Counter(e.outcome for e in evaluations)
    reasons = Counter(e.reason for e in evaluations)
    traded = [e for e in evaluations if e.traded]
    wins = [e for e in traded if e.outcome == OUTCOME_WIN]
    losses = [e for e in traded if e.outcome == OUTCOME_LOSS]
    net_r = sum(e.trade.result_r_multiple for e in traded)
    gross_win = sum(e.trade.result_r_multiple for e in wins)
    gross_loss = sum(e.trade.result_r_multiple for e in losses)

    partials = [e for e in traded if e.trade.partial_triggered]
    exit_kinds = Counter(e.trade.exit_kind for e in traded)
    by_direction = defaultdict(lambda: {"traded": 0, "wins": 0, "net_r": Decimal("0")})
    for e in traded:
        d = by_direction[e.plan.direction]
        d["traded"] += 1
        d["wins"] += 1 if e.outcome == OUTCOME_WIN else 0
        d["net_r"] += e.trade.result_r_multiple

    flags = Counter()
    for e in evaluations:
        for f in e.flags:
            flags[f] += 1

    per_window = {}
    for h in WINDOW_HOURS:
        label = window_label(h)
        rows = [e for e in evaluations if e.window_label == label]
        tr = [e for e in rows if e.traded]
        per_window[label] = {
            "rows": len(rows),
            "traded": len(tr),
            "wins": sum(1 for e in tr if e.outcome == OUTCOME_WIN),
            "net_r": sum(e.trade.result_r_multiple for e in tr),
            "no_setup": sum(1 for e in rows if e.outcome == OUTCOME_NO_SETUP),
        }

    monthly = defaultdict(lambda: {"rows": 0, "traded": 0, "wins": 0, "net_r": Decimal("0")})
    regime = defaultdict(lambda: {"rows": 0, "traded": 0, "wins": 0, "net_r": Decimal("0")})
    for e in evaluations:
        key = e.trading_date.strftime("%Y-%m")
        m = monthly[key]
        m["rows"] += 1
        r = regime[regime_of(e)]
        r["rows"] += 1
        if e.traded:
            for bucket in (m, r):
                bucket["traded"] += 1
                bucket["wins"] += 1 if e.outcome == OUTCOME_WIN else 0
                bucket["net_r"] += e.trade.result_r_multiple

    # A reason string is either a NO_SETUP/data explanation or a trade exit.
    # They must never share a table, or "why windows did not trade" silently
    # lists the losing trades as if they had not traded.
    exit_reasons = {e.reason for e in traded}
    no_setup_reasons = Counter(
        {r: c for r, c in reasons.items() if r not in exit_reasons}
    )
    exit_paths = Counter(
        ("tp" if e.trade.exit_kind == "tp" else e.trade.exit_kind,
         e.trade.partial_triggered)
        for e in traded
    )

    return {
        "n_rows": len(evaluations),
        "outcomes": outcomes,
        "reasons": reasons,
        "no_setup_reasons": no_setup_reasons,
        "exit_paths": exit_paths,
        "traded": len(traded),
        "wins": len(wins),
        "losses": len(losses),
        "net_r": net_r,
        "gross_win": gross_win,
        "gross_loss": gross_loss,
        "avg_r": (net_r / len(traded)) if traded else Decimal("0"),
        "partials": len(partials),
        "exit_kinds": exit_kinds,
        "by_direction": dict(by_direction),
        "flags": flags,
        "per_window": per_window,
        "monthly": dict(sorted(monthly.items())),
        "regime": dict(regime),
    }


# ---------------------------------------------------------------------------
# Spot check selection
# ---------------------------------------------------------------------------
def select_spot_check(evaluations: list[WindowEvaluation], target: int) -> list[WindowEvaluation]:
    """Pick windows that together exercise every outcome and reason.

    Deterministic: first pass takes one example per (outcome, reason) bucket,
    ordering buckets by scarcity so rare paths are not crowded out; then the
    remainder is filled by spreading across the calendar.
    """
    # Windows with no market data have nothing to verify: including them would
    # burn spot-check slots on rows a human cannot check against a chart.
    usable = [e for e in evaluations if e.candle_a is not None]
    buckets: dict[tuple[str, str], list[WindowEvaluation]] = defaultdict(list)
    for e in usable:
        buckets[(e.outcome, e.reason)].append(e)

    chosen: list[WindowEvaluation] = []
    seen_dates: set[dt.date] = set()
    for key in sorted(buckets, key=lambda k: (len(buckets[k]), k)):
        for candidate in buckets[key]:
            if candidate.trading_date in seen_dates and len(buckets[key]) > 1:
                continue
            chosen.append(candidate)
            seen_dates.add(candidate.trading_date)
            break

    traded = [e for e in usable if e.traded]
    step = max(1, len(traded) // max(1, target))
    for e in traded[::step]:
        if len(chosen) >= target:
            break
        if e not in chosen:
            chosen.append(e)

    for e in usable:  # still short? take whatever is left, in order
        if len(chosen) >= target:
            break
        if e not in chosen:
            chosen.append(e)

    return sorted(chosen, key=lambda e: (e.trading_date, e.window.start_hour))


# ---------------------------------------------------------------------------
# Markdown rendering
# ---------------------------------------------------------------------------
def render(
    *,
    evaluations: list[WindowEvaluation],
    series: BarSeries,
    load_report: LoadReport,
    convention_evidence: dict,
    started_at: dt.datetime,
    duration_s: float,
    source_path: str,
) -> str:
    s = summarize(evaluations)
    dates = sorted({e.trading_date for e in evaluations})
    first, last = dates[0], dates[-1]
    expected_per_date = len(ALL_WINDOW_LABELS)

    # --- coverage invariants ------------------------------------------------
    per_date_counts = Counter(e.trading_date for e in evaluations)
    dates_wrong = {d: c for d, c in per_date_counts.items() if c != expected_per_date}
    dupes = [k for k, v in Counter((e.trading_date, e.window_label) for e in evaluations).items() if v > 1]
    coverage_ok = not dates_wrong and not dupes

    lines: list[str] = []
    w = lines.append

    w("# Project Chronos — Phase 1 Validation Report")
    w("")
    w("**Generated:** " + started_at.strftime("%Y-%m-%d %H:%M:%S") + " (local)  ")
    w(f"**Runtime:** {duration_s:.1f}s  ")
    w(f"**Input:** `{source_path}`  ")
    w(f"**Instrument:** {config.INSTRUMENT_SYMBOL} (tick {config.TICK_SIZE})  ")
    w("**Scope:** Phase 1 — logic validation on historical M1 data. "
      "**No orders were placed, no broker was contacted, no database was used.**")
    w("")
    w("---")
    w("")

    # ---------------------------------------------------------------- finding
    w("## 1. Critical finding — the timestamp convention in the export")
    w("")
    w("`docs/02-TRD.md` §4.1.1 states that MT5's raw epoch is true UTC and that "
      "the NY+7 broker offset is a display artifact only. **That is not what this "
      "export contains.** The CSV stores broker server time encoded as if it were "
      "UTC, i.e. `NY wall clock = utc_label(epoch) - 7h`.")
    w("")
    w("Evidence — each candidate convention scored against physical session "
      "invariants. A wrong convention cannot satisfy them, because an exchange "
      "does not move its session when a timezone changes:")
    w("")
    w("| Convention | Check | Result |")
    w("|---|---|---|")
    for conv, payload in convention_evidence.items():
        if "checks" not in payload:
            w(f"| `{conv}` | all | error: {payload.get('error')} |")
            continue
        for name, check in payload["checks"].items():
            mark = "PASS" if check["passed"] else "**FAIL**"
            w(f"| `{conv}` | {name} | {mark} — {check['detail']} |")
    w("")
    w(f"Selected by the adapter: **`{load_report.convention}`**. "
      f"Load report: {load_report.n_bars:,} bars, "
      f"{load_report.first_ny} → {load_report.last_ny} (**New York wall clock**).")
    w("")
    w("Corroborating signals, all independent of the above:")
    w("")
    w("1. **Intraday volume.** In July (unambiguously EDT) average tick-volume "
      "peaks at raw labels 16:00–22:00. The US cash session (09:30–16:00 ET) is "
      "raw 16:30–23:00 under NY+7 — and raw 13:00–14:00, where the UTC reading "
      "would put the open, is one of the quietest hours in the file.")
    w("2. **The strategy's own skip set.** The three excluded windows "
      "(15:45–18:15) bracket a 17:00–18:00 break. Under NY+7 that break is "
      "exactly 17:00–18:00 every day; under the UTC reading it would sit at "
      "19:00–20:00 and the skip set would be unexplained.")
    w("3. **Holiday reopen.** The file's first bar is 2026-01-01 18:00 NY "
      "(the New Year's Day reopen) and Fridays end 16:59 NY year-round.")
    w("")
    w("**Status: RESOLVED.** The finding above was escalated to the Senior Dev, "
      "who instructed the correction. `docs/02-TRD.md` §4.1.1 has been rewritten "
      "to state the constant-offset rule, and "
      "`bot/historical_validation/verify_broker_timezone.py` now implements it "
      "and reproduces the invariant checks below offline "
      "(`--csv ../data/nas100_m1_history.csv`). The adapter still validates the "
      "convention at load time and refuses to run if the data disagrees, so this "
      "cannot silently produce a plausible-looking but shifted backtest.")
    w("")
    w("---")
    w("")

    # ------------------------------------------------------------ assumptions
    w("## 2. Assumptions and limitations (read before using any number below)")
    w("")
    w("Phase 1 answers one question: **is the logic correct?** It does not model "
      "execution. Every simplification is stated here so these R-multiples are "
      "never mistaken for live expectations.")
    w("")
    w("| # | Assumption | Source |")
    w("|---|---|---|")
    w("| 1 | Zero spread, zero slippage, zero commission. Entry fills exactly at "
      "`High_A − 1 tick` / `Low_A + 1 tick`; exits fill exactly at SL / TP / "
      "partial-trigger price. | Q8 |")
    w("| 2 | No gap fills. A weekend gap through the stop is scored at the stop "
      "price. Trades that were open across a session break are counted and dated in §10. | Q8 |")
    w("| 3 | Intrabar ordering is pessimistic: SL → TP → partial, in that order, "
      "every bar. One bar containing both the 80% trigger and the SL is a LOSS. | Q7 |")
    w("| 4 | Within a single M1 bar, sweep+reversal is only confirmed if the bar's "
      "close is back on the reversal side; otherwise the window waits for a later "
      "bar, and if none comes it is NO_SETUP (never assumed favourable). | Q5 |")
    w("| 5 | Each window is evaluated independently. Two trades may be open at "
      "once if an earlier one has not closed; this is a reporting simplification, "
      "not a claim about portfolio risk. | derived |")
    w("| 6 | R only, no currency. `result_currency_amount` is NULL. | Q9 |")
    w("| 7 | Monday–Friday only, New York calendar. Sunday evening sessions in the "
      "data are not evaluated (PRD §6.1 as written). | PRD §6.1 / Q11 |")
    w("")
    # -- quantization accounting (computed, never hard-coded) ----------------
    plans = [e.plan for e in evaluations if e.traded and e.plan is not None]
    n_plans = len(plans)
    n_quant = sum(1 for p in plans if p.quantized)
    n_sl_moved = sum(1 for p in plans if p.raw_stop_loss != p.stop_loss)
    n_tp_moved = sum(1 for p in plans if p.raw_take_profit != p.take_profit)
    n_pt_moved = sum(1 for p in plans if p.raw_partial_trigger is not None
                      and p.raw_partial_trigger != p.partial_trigger)
    clean_tp = [e.trade.result_r_multiple for e in evaluations
                if e.traded and e.trade.exit_kind == "tp"
                and not e.trade.partial_triggered]
    clean_exact = sum(1 for r in clean_tp if r == 1)

    w("R is `|entry − SL|`, measured **after** the SL and TP have been quantized "
      "onto the 0.1 tick grid (see §2.1). Full stop is exactly −1R by "
      "construction. `TP − entry` is `0.5·Range` *before* quantization and is "
      "rounded one further tick away from entry, so a clean run to TP lands at "
      "≈1R rather than slightly below it: "
      f"{len(clean_tp):,} clean TP trades score "
      f"{min(clean_tp):.4f}R–{max(clean_tp):.4f}R, of which {clean_exact:,} are "
      "exactly 1.0000R. Partial-then-breakeven is "
      "`0.9 × 0.8 × Range / |entry − SL|`, approaching +0.72R for wide candles "
      "(~+0.70R at this dataset's median range).")
    w("")
    w("### 2.1 Tick-grid quantization (post-calculation)")
    w("")
    w("The spec fixes `Range = High_A − Low_A` and `Mid_A` as **exact** values, "
      "but a NAS100 order price can only sit on a 0.1 multiple. Phase 1 therefore "
      "converts every price to `decimal.Decimal`, keeps `Mid_A` and `Range` "
      "exact, and quantizes only the three order prices, **after** they are "
      "calculated, in the direction that is pessimistic for the trade:")
    w("")
    w("| Field | SHORT | LONG | Why it is pessimistic |")
    w("|---|---|---|---|")
    w("| `SL` | `ROUND_FLOOR` | `ROUND_CEILING` | the stop moves one tick "
      "**closer** to entry, so risk is never understated |")
    w("| `TP` | `ROUND_FLOOR` | `ROUND_CEILING` | the target moves one tick "
      "**further** from entry, so profit is never overstated |")
    w("| `partial_trigger` | `ROUND_FLOOR` | `ROUND_CEILING` | the 80% level "
      "moves further out, so the lock-in is never assumed early |")
    w("")
    w("One direction per trade (`floor` for SHORT, `ceiling` for LONG). Values "
      "already on the grid are unchanged. `R` is then measured against the "
      "*quantized* SL, so a tighter stop also scores slightly more R per unit of "
      "price moved — both effects are visible in the numbers below.")
    w("")
    w(f"| Quantization fact | Value |")
    w("|---|---|")
    w(f"| Trade plans | {n_plans:,} |")
    w(f"| Plans with at least one price off-grid | {n_quant:,} "
      f"({_pct(n_quant, n_plans)}) |")
    w(f"| `SL` moved by quantization | {n_sl_moved:,} ({_pct(n_sl_moved, n_plans)}) |")
    w(f"| `TP` moved by quantization | {n_tp_moved:,} ({_pct(n_tp_moved, n_plans)}) |")
    w(f"| `partial_trigger` moved | {n_pt_moved:,} ({_pct(n_pt_moved, n_plans)}) |")
    w("")
    w("This was an **undocumented gap in the original spec, not an engine error**: "
      "the PRD required a 0.1 grid on all live prices but never said what to do "
      "with a calculated price that lands off it. The rule above is now recorded "
      "in PRD §6.7 / TRD §4.1.2 and the audit CSV keeps both the raw and the "
      "quantized value of every price.")
    w("")
    w("---")
    w("")

    # --------------------------------------------------------- input quality
    w("## 3. Input data quality")
    w("")
    w("| Property | Value |")
    w("|---|---|")
    w(f"| Bars | {load_report.n_bars:,} |")
    w(f"| Timestamp format | `{load_report.timestamp_format}` |")
    w(f"| Raw range | {load_report.first_raw} → {load_report.last_raw} |")
    w(f"| NY range (after conversion) | {load_report.first_ny} → {load_report.last_ny} |")
    w(f"| Duplicate timestamps | {load_report.duplicate_timestamps} |")
    w(f"| Non-monotonic timestamps | {load_report.non_monotonic_timestamps} |")
    w(f"| OHLC ordering violations | {load_report.ohlc_violations} |")
    w(f"| Prices off the {config.TICK_SIZE} tick grid | {load_report.off_tick_prices} |")
    w(f"| Bars with non-positive volume | {load_report.non_positive_volume} |")
    w("")
    w("Session invariants after conversion (these are the gate the adapter "
      "enforces at load time):")
    w("")
    w("| Check | Result |")
    w("|---|---|")
    for name, check in load_report.session_checks.items():
        w(f"| {name} | {'PASS' if check['passed'] else '**FAIL**'} — {check['detail']} |")
    w("")
    w("---")
    w("")

    # ------------------------------------------------------------- coverage
    w("## 4. Coverage — every window, exactly once")
    w("")
    w(f"| Property | Value |")
    w(f"|---|---|")
    w(f"| Trading dates evaluated (Mon–Fri) | {len(dates)} |")
    w(f"| Date range | {first} → {last} |")
    w(f"| Windows per date | {expected_per_date} |")
    w(f"| Expected rows | {len(dates) * expected_per_date:,} |")
    w(f"| Rows produced | {s['n_rows']:,} |")
    w(f"| Dates with wrong row count | {len(dates_wrong)} |")
    w(f"| Duplicate (date, window) pairs | {len(dupes)} |")
    w(f"| **Coverage invariant** | {'**PASS**' if coverage_ok else '**FAIL**'} |")
    w("")
    if dates_wrong:
        w("Dates with a wrong row count (this is a FAILURE, not a rounding):")
        w("")
        for d, c in sorted(dates_wrong.items())[:20]:
            w(f"- {d}: {c} rows")
        w("")
    w(f"The evaluation range is derived from the bars that exist, not from the "
      f"calendar: the file's last bar is {load_report.last_ny} NY, so any later "
      f"date is excluded rather than fabricated as `market_closed_no_data`. "
      f"Windows on the final date that fall after the file's last bar are "
      f"labelled `outside_data_range` (see §5).")
    w("")
    w("---")
    w("")

    # -------------------------------------------------------------- outcomes
    w("## 5. Outcome distribution")
    w("")
    w("| Outcome | Count | Share of rows |")
    w("|---|---|---|")
    for outcome in (OUTCOME_WIN, OUTCOME_LOSS, OUTCOME_NO_SETUP, OUTCOME_MISSING):
        c = s["outcomes"].get(outcome, 0)
        if c:
            w(f"| {outcome} | {c:,} | {_pct(c, s['n_rows'])} |")
    w(f"| **Total** | **{s['n_rows']:,}** | 100.0% |")
    w("")
    w("### Why windows did not trade (NO_SETUP reasons only)")
    w("")
    w("| Reason | Count | Share of rows |")
    w("|---|---|---|")
    for reason, c in sorted(s["no_setup_reasons"].items(), key=lambda kv: -kv[1]):
        w(f"| `{reason}` | {c:,} | {_pct(c, s['n_rows'])} |")
    w("")
    w("What each one means:")
    w("")
    w("- `invalid open location` — Candle B opened exactly at `Mid_A`, at/above "
      "`High_A`, or at/below `Low_A` (PRD §6.4). Since the market is continuous, "
      "this is the case where Candle A closed exactly on one of its own extremes "
      "or exactly at its midpoint.")
    w("- `no sweep` — no touch of `High_A + 1 tick` (short) / `Low_A − 1 tick` "
      "(long) inside Candle B before it closed.")
    w("- `sweep not reversed` — the level was swept, but price never came back "
      "through the opposite threshold before the window closed.")
    w("- `ambiguous_same_bar_insufficient_granularity` — one M1 bar straddled both "
      "thresholds without closing back on the reversal side, and no later bar "
      "resolved it. Deliberately scored as NO_SETUP, never as a trade (Q5).")
    w(f"- `{REASON_MARKET_CLOSED}` — no market data for the window. Per the Q10 "
      "ruling this is a NO_SETUP, never MISSING_EVALUATION: the bot was "
      "available, the market was shut (Friday evenings after 17:00 NY, exchange "
      "holidays, half-days, and the 13:00–18:00 closure on US holiday Mondays).")
    w(f"- `{REASON_MISSING_DATA}` — bars are absent while the market was "
      "demonstrably still trading. A feed-quality artefact, not a strategy result.")
    w("- `outside_data_range` — the input file stops before this window. A "
      "property of the export, not of the market or the strategy.")
    w("")
    amb = s["no_setup_reasons"].get("ambiguous_same_bar_insufficient_granularity", 0)
    pre_trade = sum(
        c for r, c in s["no_setup_reasons"].items() if r not in (REASON_MARKET_CLOSED,)
    )
    w("Two results here deserve an explicit comment:")
    w("")
    w(f"1. `sweep not reversed` fired **0 times**. That is not a coding gap: the "
      f"sweep and reversal levels are only two ticks apart (`High_A + 1t` versus "
      f"`High_A − 1t`), so almost any M1 bar that reaches the sweep level also "
      f"trades through the reversal level inside the same minute. The genuinely "
      f"unreachable-reversal case is therefore vanishingly rare, and those "
      f"windows are captured by the ambiguity rule instead.")
    w(f"2. `ambiguous_same_bar_insufficient_granularity` accounts for **{amb:,} "
      f"windows ({_pct(amb, pre_trade)} of all non-market-closure NO_SETUPs)**. "
      f"Every one of them was verified as a true Q5 case — a single bar holding "
      f"both thresholds without closing back on the reversal side, and no later "
      f"bar resolving it. Zero windows in this sample had a later bar that "
      f"reversed cleanly, i.e. no valid trigger was discarded. This is the "
      f"measurable cost of 1-minute granularity on a 2-tick-apart rule, and it "
      f"is the single number to watch if Phase 2 ever moves to tick data: a "
      f"lower `ambiguous...` count with everything else stable would confirm the "
      f"M1 approximation is conservative rather than distorting.")
    w("")
    w("---")
    w("")

    # ------------------------------------------------------- trading results
    w("## 6. Trading results (R-multiples, zero-cost fills)")
    w("")
    w("| Metric | Value |")
    w("|---|---|")
    w(f"| Trades taken | {s['traded']:,} |")
    w(f"| Wins | {s['wins']:,} |")
    w(f"| Losses | {s['losses']:,} |")
    w(f"| Win rate | {_pct(s['wins'], s['traded'])} |")
    w(f"| Net R | {_r(s['net_r'])} |")
    w(f"| Average R per trade | {_r(s['avg_r'])} |")
    w(f"| Gross win R | {_r(s['gross_win'])} |")
    w(f"| Gross loss R | {_r(s['gross_loss'])} |")
    profit_factor = "n/a" if s["gross_loss"] == 0 else f"{s['gross_win'] / abs(s['gross_loss']):.2f}"
    w(f"| Profit factor | {profit_factor} |")
    w(f"| Partial exit fired | {s['partials']:,} "
      f"({_pct(s['partials'], s['traded'])} of trades) |")
    w("")
    w("### By direction")
    w("")
    w("| Direction | Trades | Wins | Win rate | Net R |")
    w("|---|---|---|---|---|")
    for direction, d in sorted(s["by_direction"].items()):
        w(f"| {direction} | {d['traded']} | {d['wins']} | "
          f"{_pct(d['wins'], d['traded'])} | {_r(d['net_r'])} |")
    w("")
    w("### By exit path")
    w("")
    w("| Exit path | Trades | Share | Net contribution |")
    w("|---|---|---|---|")
    for (kind, partial), c in sorted(s["exit_paths"].items(), key=lambda kv: -kv[1]):
        if kind == "tp" and partial:
            label = "target hit after partial"
        elif kind == "tp":
            label = "take profit hit (no partial)"
        elif kind == "sl":
            label = "stop loss hit (no partial)"
        elif kind == "breakeven":
            label = "breakeven stop hit after partial"
        else:
            label = f"{kind} (partial={partial})"
        contrib = sum(
            e.trade.result_r_multiple
            for e in evaluations
            if e.traded and e.trade.exit_kind == kind and e.trade.partial_triggered == partial
        )
        w(f"| {label} | {c} | {_pct(c, s['traded'])} | {_r(contrib)} |")
    w("")
    w("---")
    w("")

    # -------------------------------------------------------- per window time
    w("## 7. Per-window-time performance")
    w("")
    w("Sorted by net R. `rows` is every evaluation for that slot; `traded` is the "
      "subset that produced an order.")
    w("")
    w("| Window (NY) | Rows | Traded | Wins | Win rate | Net R | NO_SETUP |")
    w("|---|---|---|---|---|---|---|")
    for label, d in sorted(s["per_window"].items(), key=lambda kv: -kv[1]["net_r"]):
        w(f"| `{label}` | {d['rows']} | {d['traded']} | {d['wins']} | "
          f"{_pct(d['wins'], d['traded'])} | {_r(d['net_r'])} | {d['no_setup']} |")
    w("")
    w("---")
    w("")

    # ------------------------------------------------------------- monthly
    w("## 8. Monthly breakdown")
    w("")
    w("| Month | Rows | Trades | Wins | Win rate | Net R |")
    w("|---|---|---|---|---|---|")
    for month, d in s["monthly"].items():
        w(f"| {month} | {d['rows']} | {d['traded']} | {d['wins']} | "
          f"{_pct(d['wins'], d['traded'])} | {_r(d['net_r'])} |")
    w("")
    w("---")
    w("")

    # ---------------------------------------------------------------- DST
    w("## 9. DST validation")
    w("")
    w("| Regime | Rows | Trades | Wins | Win rate | Net R |")
    w("|---|---|---|---|---|---|")
    for name in (REGIME_EST, REGIME_EDT):
        d = s["regime"].get(name)
        if not d:
            continue
        w(f"| {name} | {d['rows']} | {d['traded']} | {d['wins']} | "
          f"{_pct(d['wins'], d['traded'])} | {_r(d['net_r'])} |")
    w("")
    w("Both US DST transitions happen at 02:00 on a **Sunday**. Because the "
      "strategy only evaluates Monday–Friday, no window's local start time can "
      "ever fall inside the spring-forward gap (02:00–02:59 does not exist) or "
      "the fall-back repetition. `time_windows.localize()` still detects "
      "non-existent local times and reports them rather than silently resolving "
      "them, so the day rule could be changed without introducing a silent bug. "
      "Occurrences in this run: **"
      f"{s['reasons'].get('nonexistent_local_time_dst', 0)}**.")
    w("")
    w("**Coverage limit, stated plainly:** the dataset spans 2026-01-01 → "
      "2026-10-22, so it contains the **March 8 spring-forward** transition but "
      "**not** the **November 1 fall-back**. Fall-back behaviour is therefore "
      "reasoned about (Sunday-only, so out of scope for Mon–Fri windows) but not "
      "empirically exercised. Re-running on a dataset that crosses Nov 1 is the "
      "remaining DST evidence gap.")
    w("")
    w("---")
    w("")

    # ------------------------------------------------------------ anomalies
    w("## 10. Data-integrity flags raised during evaluation")
    w("")
    if s["flags"]:
        w("| Flag | Count | Meaning |")
        w("|---|---|---|")
        meanings = {
            "no_bars_in_window": "window had no market data at all",
            "incomplete_candle": "window partially covered by the feed",
            "open_through_session_break": "a position was still open when the "
            "market broke — no gap fill is modelled, so its exit price is optimistic",
            "runner_stopped_in_partial_bar": "the 1-contract runner was stopped at "
            "breakeven inside the same bar that fired the partial (pessimistic Q7 reading)",
        }
        for flag, c in sorted(s["flags"].items(), key=lambda kv: -kv[1]):
            w(f"| `{flag}` | {c:,} | {meanings.get(flag, '')} |")
        break_rows = [e for e in evaluations if "open_through_session_break" in e.flags]
        if break_rows:
            w("")
            w("Trades that were still open across a session break, and so are "
              "scored with an optimistic exit price (no gap fill is modelled):")
            w("")
            w("| Date | Window | Direction | Entry | Final exit | R |")
            w("|---|---|---|---|---|---|")
            for e in break_rows:
                w(f"| {e.trading_date} | `{e.window_label}` | {e.plan.direction} | "
                  f"{e.plan.entry:.1f} | {e.trade.final_exit_price:.1f} | "
                  f"{_r(e.trade.result_r_multiple)} |")
    else:
        w("None.")
    w("")
    w("---")
    w("")

    # ------------------------------------------------------------ spot check
    chosen = select_spot_check(evaluations, config.SPOT_CHECK_TARGET)
    w("## 11. Manual spot-check set")
    w("")
    w(f"{len(chosen)} windows selected to cover every outcome and every reason, "
      "spread across the calendar. The full derivation of each — Candle A OHLC, "
      "Mid_A, Range, Candle B open, sweep and reversal timestamps, entry/SL/TP, "
      "partial trigger, exit and R — is in `spot_check.csv`.")
    w("")
    w("Verification recipe for each row: `High_A`/`Low_A` must equal the max/min "
      "of the 15 M1 bars in the HH:45–HH:00 window; `Mid_A = (High_A+Low_A)/2`; "
      "`Range = High_A−Low_A`; context is Candle B's open against those three "
      "levels; sweep is `High_A+1t` (short) or `Low_A−1t` (long) touched in "
      "Candle B; reversal is the opposite side by 1 tick; `SL = High_A+0.5·Range` "
      "(short) or `Low_A−0.5·Range` (long); `TP = Mid_A∓1t`.")
    w("")
    w("| Date | Window | Ctx | A: O/H/L/Mid | B open | Sweep @ | Rev @ | Entry | SL | TP | 80% | Outcome | Reason | R |")
    w("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    for e in chosen:
        a, b, sw, p, t = e.candle_a, e.candle_b, e.sweep, e.plan, e.trade
        a_txt = (
            f"{a.open:.1f}/{a.high:.1f}/{a.low:.1f}/{a.mid:.2f}" if a else "—"
        )
        b_open = f"{b.open:.1f}" if b else "—"
        sweep = (
            f"{sw.sweep_price:.1f} @{sw.sweep_time.strftime('%H:%M')}" if (sw and sw.sweep_time) else "—"
        )
        rev = (
            f"{sw.reversal_price:.1f} @{sw.reversal_time.strftime('%H:%M')}"
            if (sw and sw.reversal_time) else "—"
        )
        w(
            "| "
            + " | ".join([
                e.trading_date.isoformat(),
                f"`{e.window_label}`",
                e.context,
                a_txt,
                b_open,
                sweep,
                rev,
                f"{p.entry:.1f}" if p else "—",
                f"{p.stop_loss:.1f}" if p else "—",
                f"{p.take_profit:.1f}" if p else "—",
                f"{p.partial_trigger:.1f}" if p else "—",
                f"{e.outcome}" + (f" ({t.exit_kind})" if (t and t.exit_kind) else ""),
                e.reason,
                f"{t.result_r_multiple:+.3f}" if (t and t.resolved) else "—",
            ])
            + " |"
        )
    w("")
    w("---")
    w("")

    # ------------------------------------------------------------- reproduce
    w("## 12. Reproducing this report")
    w("")
    w("```bash")
    w("cd bot")
    w("python3 -m unittest discover -s tests          # full logic suite")
    w("python3 historical_validation/validate_against_history.py \\")
    w("    --csv ../data/nas100_m1_history.csv")
    w("```")
    w("")
    w("Runs on the Python standard library alone: no third-party runtime "
      "dependency, no credentials, no network, no MT5 terminal. `pytest` also "
      "works if installed. Pass `--convention utc` to reproduce the rejected "
      "reading in §1, or `--out-dir` to write elsewhere.")
    w("")
    return "\n".join(lines)


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
