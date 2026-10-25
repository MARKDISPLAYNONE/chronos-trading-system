"""
Project Chronos — Phase 1 output writers
========================================

Phase 1 writes locally only (Q1/Q2: no Supabase, no auth user, no DB). Outputs
are deliberately shaped to be a *straight schema match* so Phase 2 can copy
rows into Supabase without a redesign:

  * `window_evaluations.csv` / `trades.csv` — column names, order and types
    mirror `docs/05-DATABASE-SCHEMA.md` §4.2 / §4.3 exactly.
  * `chronos_phase1.sqlite` — same two tables, same column names.

Three documented deviations from a real Supabase instance, none of which
changes a column name:

  1. `user_id` is NULL on every row. There is no `auth.users` row in Phase 1
     (that FK is satisfied only once Supabase exists). Documented, not hidden.
  2. `instrument_id` / `trade_id` / `window_evaluation_id` are local UUIDs
     generated with `uuid5` from (trading_date, window_label). They are stable
     across reruns, so diffs are meaningful — but they are NOT the UUIDs a
     Supabase insert would produce. Phase 2 re-keys on insert.
  3. `evaluated_at` / `created_at` / `opened_at` / `closed_at` carry the New
     York UTC offset explicitly, since the local mirror stores text.

`result_currency_amount` is NULL: Phase 1 reports R only (Q9), because fills
are modelled as zero-spread and `instruments.point_value` is still unconfirmed.
"""

from __future__ import annotations

import csv
import datetime as dt
import sqlite3
import uuid
from pathlib import Path

from . import config
from .pricing import round_price
from .time_windows import localize
from .window_evaluator import WindowEvaluation

# Deterministic namespace so local ids are stable across runs.
_NS = uuid.UUID("6f9619ff-8b86-d011-b42d-00c04fc964ff")

WINDOW_COLUMNS = [
    "id",
    "user_id",
    "instrument_id",
    "trading_date",
    "window_label",
    "candle_a_open",
    "candle_a_high",
    "candle_a_low",
    "candle_a_close",
    "candle_a_mid",
    "candle_b_open",
    "context",
    "outcome",
    "reason",
    "trade_id",
    "evaluated_at",
    "created_at",
]

TRADE_COLUMNS = [
    "id",
    "user_id",
    "window_evaluation_id",
    "instrument_id",
    "direction",
    "entry_price",
    "stop_loss_price",
    "take_profit_price",
    "initial_contracts",
    "partial_exit_triggered",
    "partial_exit_price",
    "breakeven_sl_price",
    "final_exit_price",
    "final_outcome",
    "result_r_multiple",
    "result_currency_amount",
    "opened_at",
    "closed_at",
    "created_at",
    "updated_at",
]

AUDIT_COLUMNS = [
    "trading_date",
    "window_label",
    "context",
    "outcome",
    "reason",
    "candle_a_open",
    "candle_a_high",
    "candle_a_low",
    "candle_a_close",
    "candle_a_mid",
    "candle_a_range",
    "candle_b_open",
    "sweep_time",
    "sweep_price",
    "reversal_time",
    "reversal_price",
    "direction",
    "entry_price",
    "stop_loss_price",
    "take_profit_price",
    "partial_trigger_price",
    "risk_per_unit",
    "raw_stop_loss_price",
    "raw_take_profit_price",
    "raw_partial_trigger_price",
    "prices_quantized",
    "quantize_direction",
    "partial_triggered",
    "partial_exit_time",
    "partial_exit_price",
    "final_exit_time",
    "final_exit_price",
    "exit_kind",
    "result_r_multiple",
    "bars_held",
    "bars_candle_a",
    "bars_candle_b",
    "flags",
]


def window_id(ev: WindowEvaluation) -> str:
    return str(uuid.uuid5(_NS, f"{ev.trading_date.isoformat()}|{ev.window_label}"))


def trade_id(ev: WindowEvaluation) -> str | None:
    return str(uuid.uuid5(_NS, f"trade|{ev.trading_date.isoformat()}|{ev.window_label}")) if ev.trade else None


def _ny_iso(when: dt.datetime | None) -> str:
    """Naive NY wall clock -> ISO-8601 with explicit offset."""
    if when is None:
        return ""
    aware, _ = localize(when)
    return aware.isoformat()


def _fmt(value) -> str:
    return "" if value is None else f"{round_price(value):.5f}"


def window_row(ev: WindowEvaluation) -> dict:
    a, b = ev.candle_a, ev.candle_b
    end_iso = _ny_iso(ev.window.end)
    return {
        "id": window_id(ev),
        "user_id": "",
        "instrument_id": "",
        "trading_date": ev.trading_date.isoformat(),
        "window_label": ev.window_label,
        "candle_a_open": _fmt(a.open) if a else "",
        "candle_a_high": _fmt(a.high) if a else "",
        "candle_a_low": _fmt(a.low) if a else "",
        "candle_a_close": _fmt(a.close) if a else "",
        "candle_a_mid": _fmt(a.mid) if a else "",
        "candle_b_open": _fmt(b.open) if b else "",
        "context": ev.context,
        "outcome": ev.outcome,
        "reason": ev.reason,
        "trade_id": trade_id(ev) or "",
        "evaluated_at": end_iso,
        "created_at": end_iso,
    }


def trade_row(ev: WindowEvaluation) -> dict:
    t, p = ev.trade, ev.plan
    end_iso = _ny_iso(ev.window.end)
    return {
        "id": trade_id(ev) or "",
        "user_id": "",
        "window_evaluation_id": window_id(ev),
        "instrument_id": "",
        "direction": p.direction,
        "entry_price": _fmt(p.entry),
        "stop_loss_price": _fmt(p.stop_loss),
        "take_profit_price": _fmt(p.take_profit),
        "initial_contracts": config.CONTRACTS_TOTAL,
        "partial_exit_triggered": str(bool(t and t.partial_triggered)).lower(),
        "partial_exit_price": _fmt(t.partial_exit_price) if t else "",
        "breakeven_sl_price": _fmt(t.breakeven_sl_price) if t else "",
        "final_exit_price": _fmt(t.final_exit_price) if t else "",
        "final_outcome": t.final_outcome if (t and t.resolved) else "",
        "result_r_multiple": "" if not (t and t.resolved) else f"{t.result_r_multiple:.6f}",
        "result_currency_amount": "",
        "opened_at": _ny_iso(ev.sweep.trigger_time) if ev.sweep and ev.sweep.trigger_time else "",
        "closed_at": _ny_iso(t.final_exit_time) if t else "",
        "created_at": _ny_iso(ev.sweep.trigger_time) if ev.sweep and ev.sweep.trigger_time else end_iso,
        "updated_at": _ny_iso(t.final_exit_time) if (t and t.final_exit_time) else end_iso,
    }


def audit_row(ev: WindowEvaluation) -> dict:
    a, b, s, p, t = ev.candle_a, ev.candle_b, ev.sweep, ev.plan, ev.trade
    return {
        "trading_date": ev.trading_date.isoformat(),
        "window_label": ev.window_label,
        "context": ev.context,
        "outcome": ev.outcome,
        "reason": ev.reason,
        "candle_a_open": _fmt(a.open) if a else "",
        "candle_a_high": _fmt(a.high) if a else "",
        "candle_a_low": _fmt(a.low) if a else "",
        "candle_a_close": _fmt(a.close) if a else "",
        "candle_a_mid": _fmt(a.mid) if a else "",
        "candle_a_range": _fmt(a.range) if a else "",
        "candle_b_open": _fmt(b.open) if b else "",
        "sweep_time": _ny_iso(s.sweep_time) if (s and s.sweep_time) else "",
        "sweep_price": _fmt(s.sweep_price) if (s and s.sweep_price is not None) else "",
        "reversal_time": _ny_iso(s.reversal_time) if (s and s.reversal_time) else "",
        "reversal_price": _fmt(s.reversal_price) if (s and s.reversal_price is not None) else "",
        "direction": p.direction if p else "",
        "entry_price": _fmt(p.entry) if p else "",
        "stop_loss_price": _fmt(p.stop_loss) if p else "",
        "take_profit_price": _fmt(p.take_profit) if p else "",
        "partial_trigger_price": _fmt(p.partial_trigger) if p else "",
        "risk_per_unit": _fmt(p.risk_per_unit) if p else "",
        "raw_stop_loss_price": _fmt(p.raw_stop_loss) if p and p.raw_stop_loss is not None else "",
        "raw_take_profit_price": _fmt(p.raw_take_profit) if p and p.raw_take_profit is not None else "",
        "raw_partial_trigger_price": _fmt(p.raw_partial_trigger) if p and p.raw_partial_trigger is not None else "",
        "prices_quantized": str(bool(p and p.quantized)).lower() if p else "",
        "quantize_direction": p.quantization_direction if p else "",
        "partial_triggered": str(bool(t and t.partial_triggered)).lower() if t else "",
        "partial_exit_time": _ny_iso(t.partial_exit_time) if (t and t.partial_exit_time) else "",
        "partial_exit_price": _fmt(t.partial_exit_price) if (t and t.partial_exit_price) else "",
        "final_exit_time": _ny_iso(t.final_exit_time) if (t and t.final_exit_time) else "",
        "final_exit_price": _fmt(t.final_exit_price) if (t and t.final_exit_price is not None) else "",
        "exit_kind": t.exit_kind if (t and t.exit_kind) else "",
        "result_r_multiple": f"{t.result_r_multiple:.6f}" if (t and t.resolved) else "",
        "bars_held": t.bars_held if t else "",
        "bars_candle_a": ev.bars_a,
        "bars_candle_b": ev.bars_b,
        "flags": ";".join(ev.flags),
    }


def _write_csv(path: Path, columns: list[str], rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=columns)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def write_all(out_dir: Path, evaluations: list[WindowEvaluation]) -> dict[str, Path]:
    """Write the schema-mirror CSVs, the audit CSV and the SQLite mirror."""
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = {
        "window_evaluations": out_dir / "window_evaluations.csv",
        "trades": out_dir / "trades.csv",
        "audit": out_dir / "window_evaluations_audit.csv",
    }

    _write_csv(paths["window_evaluations"], WINDOW_COLUMNS, [window_row(e) for e in evaluations])
    _write_csv(paths["trades"], TRADE_COLUMNS, [trade_row(e) for e in evaluations if e.trade])
    _write_csv(paths["audit"], AUDIT_COLUMNS, [audit_row(e) for e in evaluations])

    db_path = out_dir / "chronos_phase1.sqlite"
    if db_path.exists():
        db_path.unlink()
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE window_evaluations (
                id TEXT PRIMARY KEY,
                user_id TEXT,
                instrument_id TEXT,
                trading_date TEXT NOT NULL,
                window_label TEXT NOT NULL,
                candle_a_open REAL, candle_a_high REAL, candle_a_low REAL,
                candle_a_close REAL, candle_a_mid REAL,
                candle_b_open REAL,
                context TEXT NOT NULL,
                outcome TEXT NOT NULL,
                reason TEXT NOT NULL,
                trade_id TEXT,
                evaluated_at TEXT NOT NULL,
                created_at TEXT NOT NULL,
                UNIQUE (trading_date, window_label)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE trades (
                id TEXT PRIMARY KEY,
                user_id TEXT,
                window_evaluation_id TEXT NOT NULL UNIQUE,
                instrument_id TEXT,
                direction TEXT NOT NULL,
                entry_price REAL NOT NULL,
                stop_loss_price REAL NOT NULL,
                take_profit_price REAL NOT NULL,
                initial_contracts INTEGER NOT NULL DEFAULT 10,
                partial_exit_triggered INTEGER NOT NULL DEFAULT 0,
                partial_exit_price REAL,
                breakeven_sl_price REAL,
                final_exit_price REAL,
                final_outcome TEXT,
                result_r_multiple REAL,
                result_currency_amount REAL,
                opened_at TEXT NOT NULL,
                closed_at TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.executemany(
            f"INSERT INTO window_evaluations ({','.join(WINDOW_COLUMNS)}) VALUES ({','.join('?' * len(WINDOW_COLUMNS))})",
            [[_sql(row[c]) for c in WINDOW_COLUMNS] for row in (window_row(e) for e in evaluations)],
        )
        conn.executemany(
            f"INSERT INTO trades ({','.join(TRADE_COLUMNS)}) VALUES ({','.join('?' * len(TRADE_COLUMNS))})",
            [[_sql(row[c]) for c in TRADE_COLUMNS] for row in (trade_row(e) for e in evaluations if e.trade)],
        )
        conn.commit()
    paths["sqlite"] = db_path
    return paths


def _sql(value):
    """Empty CSV cells -> NULL; numeric strings -> numbers; bools -> ints."""
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, str) and value in ("true", "false"):
        return 1 if value == "true" else 0
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return value
    return value
