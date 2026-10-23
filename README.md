# Project Chronos

An autonomous trading bot and read-only metrics dashboard implementing one
specific, fully-specified manual strategy ("Zeussy Macro Time" window model) on
**NAS100** via MetaTrader 5, starting on a Forex.com Global **demo** account.
Built strictly on free-tier tools.

> **Status: Phase 1 — logic validation.** No orders are placed, no broker is
> contacted, and no database is used. The bot engine does not exist yet beyond
> the historical-validation subset.

## Start here

Read in this order — they are written for someone with zero prior context:

1. [`handover/SESSION-HANDOVER.md`](handover/SESSION-HANDOVER.md) — living
   project state. **Read this first, and it is the file to update at the end of
   every session.**
2. [`docs/01-PRD.md`](docs/01-PRD.md) — what the system does; the strategy spec
   is §6 and is authoritative.
3. [`docs/02-TRD.md`](docs/02-TRD.md) — stack and architecture decisions.
4. [`docs/07-PHASE-PLAN.md`](docs/07-PHASE-PLAN.md) — the five phases and their
   gates. This decides what may be built now.
5. [`docs/03-APP-FLOW.md`](docs/03-APP-FLOW.md),
   [`docs/05-DATABASE-SCHEMA.md`](docs/05-DATABASE-SCHEMA.md),
   [`docs/06-IMPLEMENTATION-PLAN.md`](docs/06-IMPLEMENTATION-PLAN.md) — flows,
   schema (including the §8.1 intrabar rules), and build order.

## Layout

```
docs/                       the 7-project-document set
handover/SESSION-HANDOVER.md living project state (the "00" doc)
introductory/               role prompts for the Handler/Senior Dev/Agent
data/nas100_m1_history.csv  Phase 1 input: NAS100 M1, Jan-Oct 2026 (~13 MB)
bot/
  src/                      engine: windows, candles, context, sweep, sizing
  tests/                    96 unit tests (stdlib unittest)
  historical_validation/
    validate_against_history.py   Phase 1 driver
    export_history_csv.py         MT5 export (Windows-only, Handler runs it)
    verify_broker_timezone.py     broker clock cross-check
    output/                       generated report + schema-mirror CSVs
```

## Running Phase 1

```bash
cd bot
python3 -m unittest discover -s tests
python3 historical_validation/validate_against_history.py \
    --csv ../data/nas100_m1_history.csv
```

Outputs land in `bot/historical_validation/output/`:

| File | What it is |
|---|---|
| `phase1_validation_report.md` | the validation report — read this first |
| `window_evaluations.csv` | one row per window, columns mirroring the schema |
| `trades.csv` | one row per trade, columns mirroring the schema |
| `window_evaluations_audit.csv` | full derivation per window (sweep/entry/exit) |
| `spot_check.csv` | the ≥20-window manual verification set |
| `chronos_phase1.sqlite` | local mirror of the two tables (regenerable) |

Standard library only. No credentials, no network, no MT5 terminal.

## Hard rules for contributors

- **Free tier only.** No paid service, API, or dependency without explicit
  approval relayed through the Handler.
- **No secrets in the repo, ever.** `.env` files are git-ignored; variable
  *names* live in `bot/.env.example`.
- **Only build what the current phase scopes** (`docs/07-PHASE-PLAN.md`).
- **Docs before code.** If a change contradicts a document, correct the
  document first.
- **Stop and ask** when something is ambiguous — never guess silently.
