"""
Project Chronos — Phase 2 append-only CSV emitter
=================================================

Writes the same three files Phase 1 produced, with the same columns in the same
order, so a Phase 2 log can be diffed, concatenated or read by the same tooling:

    window_evaluations.csv           schema mirror (docs/05-DATABASE-SCHEMA.md §4.2)
    trades.csv                       schema mirror (§4.3)
    window_evaluations_audit.csv     audit trail (§8.1 fields included)

plus one Phase-2-only operational file:

    runner_events.csv                startup/shutdown/gap/reconnect events

The row builders are **imported from `src.outputs`**, not re-implemented. A live
row and a backtest row therefore cannot drift apart in shape, only in content —
and the audit CSV carries `evaluated_at`, so "when was this decided" is always
answerable.

APPEND-ONLY, AND WHAT THAT COSTS
--------------------------------
`open(..., "a")` only. Nothing rewrites, re-sorts or back-fills a row, per the
Handler's Q13/Q14 ruling. Two consequences, both deliberate:

1. **A row is written once, when its outcome is determined.** NO_SETUP rows are
   written at window close; traded rows are written only when `simulate()`
   resolves, which can be days later. So the file is ordered by *decision* time,
   not by window time — `evaluated_at` and `trading_date` disagree, on purpose.
2. **Rows are not in window order.** A window at 14:45 that resolved at 15:02
   appears before a window at 09:45 that resolved tomorrow. Consumers must sort;
   nothing here pretends otherwise.

`evaluated_at` / `created_at` are set to the **emit** time rather than the window
close (which is what the Phase 1 backtest writes, because there the two are the
same). That is the only semantic difference from Phase 1, and it is the
information the live run adds.

On restart the emitter re-reads the existing CSVs to learn which window ids have
already been written, so a crash cannot duplicate a row. The CSV is the source of
truth — there is no separate "emitted" index that could disagree with it.
"""

from __future__ import annotations

import csv
import datetime as dt
import pathlib

from src import outputs
from src.context_classifier import Context
from src.time_windows import Window
from src.window_evaluator import (
    OUTCOME_NO_SETUP,
    WindowEvaluation,
)

#: Reason string for a window the runner did not observe (Q14, Option A).
REASON_RUNNER_DOWN = "dry_run_gap_runner_down"

#: Reason string for a window whose own bars never arrived even though the
#: runner was connected and watching it. Deliberately distinct from
#: `dry_run_gap_runner_down`: in one case we were blind, in the other we were
#: looking and the feed did not deliver. Conflating them would misattribute a
#: feed fault to a crash and hide which of the two actually happened.
REASON_LIVE_FEED_STALLED = "live_feed_stalled"

EVENT_COLUMNS = [
    "event_utc",
    "ny_wall",
    "kind",
    "detail",
]


def _append(path: pathlib.Path, columns: list[str], row: dict) -> None:
    """Append one row, writing the header only when the file is new/empty."""
    path.parent.mkdir(parents=True, exist_ok=True)
    new = not path.exists() or path.stat().st_size == 0
    with path.open("a", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=columns, extrasaction="ignore")
        if new:
            writer.writeheader()
        writer.writerow({c: row.get(c, "") for c in columns})


def gap_evaluation(
    window: Window,
    *,
    detail: str = "",
    source: WindowEvaluation | None = None,
    reason: str = REASON_RUNNER_DOWN,
    flag: str = "runner_down",
) -> WindowEvaluation:
    """A NO_SETUP row for a window the runner did not fully observe.

    Shaped like a real NO_SETUP so it flows through the identical row builders,
    and flagged so the phase report can separate "the bot was not running" from
    every market fact.

    `source`, when given, is an evaluation of the *same* window that did observe
    something before the runner died — typically the candles, the sweep and the
    entry plan. Those fields are carried over because they are facts that were
    seen live; only the **exit** is dropped, because it was not. Nothing is
    inferred: `trade` stays None, so the audit row has no exit price, no exit
    kind and no R. That is the difference between "we do not know" and a
    wrongly-scored WIN or LOSS.
    """
    flags = [flag]
    if detail:
        flags.append(detail)
    if source is None:
        return WindowEvaluation(
            window=window,
            context=Context.VOID.value,
            outcome=OUTCOME_NO_SETUP,
            reason=reason,
            flags=flags,
        )
    return WindowEvaluation(
        window=window,
        context=source.context,
        outcome=OUTCOME_NO_SETUP,
        reason=reason,
        candle_a=source.candle_a,
        candle_b=source.candle_b,
        sweep=source.sweep,
        plan=source.plan,
        trade=None,
        bars_a=source.bars_a,
        bars_b=source.bars_b,
        flags=flags,
    )


def stall_evaluation(window: Window, *, detail: str = "",
                     source: WindowEvaluation | None = None) -> WindowEvaluation:
    """A window the runner watched and the feed still failed to deliver.

    Same shape as `gap_evaluation`, different reason and flag, so the two are
    never counted together: `runner_down` means "we were not looking",
    `live_feed_stalled` means "we were looking and nothing arrived".
    """
    return gap_evaluation(window, detail=detail, source=source,
                          reason=REASON_LIVE_FEED_STALLED,
                          flag="live_feed_stalled")


class CsvEmitter:
    """Append-only writer for the Phase 2 dry-run log."""

    def __init__(self, out_dir: pathlib.Path) -> None:
        self.out_dir = pathlib.Path(out_dir)
        self.paths = {
            "window_evaluations": self.out_dir / "window_evaluations.csv",
            "trades": self.out_dir / "trades.csv",
            "audit": self.out_dir / "window_evaluations_audit.csv",
            "events": self.out_dir / "runner_events.csv",
        }
        self.emitted: set[str] = set()
        self._load_emitted()

    # -- restart safety ---------------------------------------------------
    def _load_emitted(self) -> None:
        """Learn which windows are already logged, from the log itself."""
        path = self.paths["window_evaluations"]
        if not path.exists():
            return
        with path.open(newline="", encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                if row.get("id"):
                    self.emitted.add(row["id"])

    def already_emitted(self, ev: WindowEvaluation) -> bool:
        return outputs.window_id(ev) in self.emitted

    # -- writing ----------------------------------------------------------
    def emit(self, ev: WindowEvaluation, *, emit_time: dt.datetime,
             note: str = "") -> bool:
        """Write one evaluation to all three files. Returns False if duplicate."""
        wid = outputs.window_id(ev)
        if wid in self.emitted:
            return False

        stamp, _ = _ny_iso(emit_time)

        window_row = outputs.window_row(ev)
        window_row["evaluated_at"] = stamp
        window_row["created_at"] = stamp
        _append(self.paths["window_evaluations"], outputs.WINDOW_COLUMNS, window_row)

        if ev.trade is not None:
            trade_row = outputs.trade_row(ev)
            _append(self.paths["trades"], outputs.TRADE_COLUMNS, trade_row)

        audit_row = outputs.audit_row(ev)
        if note:
            audit_row["flags"] = ";".join(
                [f for f in (audit_row.get("flags", ""), note) if f]
            )
        _append(self.paths["audit"], outputs.AUDIT_COLUMNS, audit_row)

        self.emitted.add(wid)
        return True

    def event(self, kind: str, detail: str, *, when: dt.datetime) -> None:
        """Operational event: startup, shutdown, gap fill, reconnect, warning."""
        ny_wall, aware = _ny_iso(when)
        _append(
            self.paths["events"],
            EVENT_COLUMNS,
            {
                "event_utc": aware.astimezone(dt.timezone.utc).isoformat(),
                "ny_wall": ny_wall,
                "kind": kind,
                "detail": detail,
            },
        )


def _ny_iso(naive_ny: dt.datetime) -> tuple[str, dt.datetime]:
    """Naive NY wall clock -> (ISO string with offset, aware datetime)."""
    from src.time_windows import localize

    aware, _exists = localize(naive_ny)
    return aware.isoformat(), aware
