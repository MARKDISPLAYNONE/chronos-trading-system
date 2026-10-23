# Project Chronos — Technical Requirements Document (TRD)

> **Audience note:** This document assumes the reader is a competent developer
> with ZERO prior context on this project. Every term, constraint, and decision
> is explained — nothing is assumed to be "obvious" from outside knowledge of
> the strategy, the team's prior conversations, or the business context. If
> something in this doc is unclear, that is a documentation bug — flag it via
> the Handler rather than guessing.

## 1. Purpose of This Document

This TRD translates the requirements in `01-PRD.md` into concrete technical
decisions: what tools are used, why, how they connect, what their limits are,
and what rules govern how code is written and organized. It does not contain
strategy logic detail (see PRD Section 6) — it contains **how the system is
built**, not **what it does**.

## 2. Glossary (Read This First)

Terms used throughout all project docs, defined once here for consistency:

| Term | Meaning |
|---|---|
| **Window** | One of 21 fixed 30-minute daily time slots (e.g., 01:45–02:15 NY time) during which the strategy evaluates for a trade |
| **Candle A** | The 15-minute reference candle at the start of a window (HH:45–HH:00+1) |
| **Candle B** | The following 15-minute candle (HH:00+1–HH:15+1), where entry logic is evaluated |
| **Premium context** | Candle B opens in the upper half of Candle A's range (between midpoint and high) — only short trades allowed |
| **Discount context** | Candle B opens in the lower half of Candle A's range (between low and midpoint) — only long trades allowed |
| **Sweep** | Price trading beyond Candle A's high or low by at least 1 tick |
| **Reversal** | Price trading back through that same level in the opposite direction by at least 1 tick — this is the entry trigger |
| **Tick** | The smallest price increment the instrument can move (defined by the broker/instrument spec) |
| **Handle** | A custom unit used in this strategy: 1 handle = 4 ticks |
| **R-multiple** | Trade result expressed as a multiple of the amount risked (e.g., trade risked $100, made $200 → R-multiple = 2R) |
| **NO_SETUP** | A logged outcome meaning the window was evaluated but no valid trade occurred, with a specific reason attached |
| **Handler** | The human project owner/intermediary — relays instructions between Senior Dev and Agent |
| **Senior Dev** | The AI chat responsible for architecture/logic decisions and guidance (this role may be filled by different AI sessions over time — hence the handover doc) |
| **Agent** | The coding agent/tool that executes implementation tasks in the codebase |
| **Forward test** | Running the strategy on live/demo data in real time (not historical replay) to observe behavior without the logic having "seen" the outcome in advance |
| **Backtest** | Running the strategy against historical data to evaluate what it would have done |
| **Dry-run** | The bot evaluates and logs what it WOULD do, without submitting real orders |

## 3. System Architecture — High-Level Overview

Project Chronos consists of **three independent but connected components**:
┌─────────────────────┐ ┌──────────────────────┐ ┌─────────────────────┐
│ BOT ENGINE │ │ DATABASE │ │ DASHBOARD │
│ (Python) │─────▶│ (Supabase/Postgres) │◀─────│ (Next.js/Vercel) │
│ │write │ │read │ │
│ - Connects to MT5 │ │ - Stores window logs │ │ - Visualizes metrics │
│ - Detects windows │ │ - Stores trade records │ │ - Trade log table │
│ - Evaluates strategy │ │ - RLS secured │ │ - Auth protected │
│ - Executes trades │ │ │ │ │
│ - Logs every outcome │ │ │ │ │
└─────────────────────┘ └──────────────────────┘ └─────────────────────┘
│
▼
┌─────────────────────┐
│ MetaTrader 5 │
│ (Terminal + broker │
│ demo/live account) │
└─────────────────────┘

text


**Key architectural principle:** The Bot Engine and the Dashboard **never talk
to each other directly**. They are fully decoupled, connected only through the
database. This means:
- The dashboard can be developed, tested, and deployed independently of the
  bot
- The bot can run completely standalone (even with no dashboard built yet)
  and still be useful via raw database records
- Either component can go down without affecting the other's core function

## 4. Technology Stack (Full Breakdown)

### 4.1 Bot Engine
| Component | Choice | Why |
|---|---|---|
| Language | Python 3.11+ | Mature ecosystem, official MT5 package support, easy to read/maintain |
| Market data & execution | `MetaTrader5` (official Python package) | Only officially supported way to programmatically connect to MT5 terminal for live/demo data and order execution |
| Historical data (validation phase) | Dukascopy free tick data (or MT5's own historical data export) | Free, tick-level granularity needed to validate sweep/reversal sequencing (see PRD 6.5) |
| Scheduling/time handling | Python `zoneinfo` / `pytz` (America/New_York) | Must handle DST transitions correctly — NY time does not map to a fixed UTC offset year-round |
| Database client | `supabase-py` (official Supabase Python client) | Writes window/trade logs directly to Postgres via Supabase |

**Critical constraint:** The `MetaTrader5` Python package is a **wrapper
around the MT5 terminal's native Windows API and only functions on Windows**.
This is not a library limitation that can be worked around with
configuration — it is a hard OS dependency.

**Implication for this project:**
- Phases 1–3 (logic validation, dry-run, demo execution) run on a Windows
  machine with the MT5 terminal installed and logged into the demo account
- For eventual 24/7 autonomous operation without requiring a dedicated
  always-on Windows PC, the planned path is **MetaApi.cloud**, a third-party
  cloud bridge service with a free tier, which exposes MT4/MT5 accounts over
  a REST/WebSocket API usable from any OS or serverless environment. This
  migration happens only AFTER core logic is validated locally (see
  `07-PHASE-PLAN.md`) — do not attempt this prematurely.

### 4.1.1 Timezone Handling (Critical — Amendment, Session 2)

MT5's `copy_rates_*` functions return timestamps in **broker server time**,
NOT UTC, despite official documentation claiming otherwise (confirmed via
direct consultation with an MQL5/MT5 specialist developer, cross-referenced
against community-reported discrepancies). This must be handled as follows:

1. **Never hardcode a fixed hour offset** between broker time and NY time.
   Both zones may independently observe DST, on different calendar dates,
   which breaks any fixed-offset assumption at least twice a year.
2. **Empirically determine the broker's actual timezone** (not assumed from
   the Handler's own geographic location) using a one-time diagnostic
   script (see `bot/historical_validation/verify_broker_timezone.py`,
   to be created in Implementation Step 3) that compares a known bar's raw
   epoch timestamp against real-world UTC and NY time.
3. Represent the broker's timezone as a proper DST-aware zone object
   (e.g., `zoneinfo.ZoneInfo("Europe/Athens")` if confirmed as EET), not a
   fixed offset like `UTC+2` or `UTC+3`.
4. Represent NY time as `zoneinfo.ZoneInfo("America/New_York")` — DST
   handled automatically.
5. All window-boundary logic (`time_windows.py`) operates in NY time only.
   Broker timestamps are converted to NY time at the point of ingestion,
   never the reverse.
6. A verification test must be run and documented BEFORE Phase 1 exit,
   confirming correct conversion across a known session boundary (e.g.,
   confirming a 16:00 NY bar maps to the correct UTC equivalent for the
   current DST state, per the dev's Step 4 methodology).

**Library choice:** Python's built-in `zoneinfo` (3.9+, no extra
dependency) is preferred over `pytz` for new code, functionally
equivalent for this purpose — use whichever the Agent finds better
documented/supported at implementation time, consistency within the
codebase matters more than which specific library.

### 4.2 Database & Backend
| Component | Choice | Why |
|---|---|---|
| Database | Supabase (hosted Postgres) | Free tier, includes Auth and Row Level Security natively, no separate backend server needed for basic reads/writes |
| Auth | Supabase Auth | Built-in, free, integrates directly with RLS policies |
| Row Level Security (RLS) | Supabase/Postgres native RLS | Even though this is single-user, RLS is implemented correctly from day one for security hygiene and future extensibility — see `05-DATABASE-SCHEMA.md` |
| API layer | Next.js API routes (where needed) | Avoids standing up a separate backend server; serverless functions handle any logic the frontend needs beyond direct Supabase queries |

### 4.3 Dashboard (Frontend)
| Component | Choice | Why |
|---|---|---|
| Framework | Next.js (React) | Free hosting compatibility with Vercel, strong ecosystem, server + client rendering flexibility |
| Styling | Tailwind CSS | Fast to build with, no design system licensing cost |
| Charts | Recharts or Chart.js (free, open-source) | Equity curve, per-window performance visualizations |
| Hosting | Vercel (free tier) | Native Next.js support, automatic deployments from GitHub, free SSL/CDN |

### 4.4 Infrastructure & DevOps
| Component | Choice | Why |
|---|---|---|
| Version control | Git + GitHub | Industry standard, free for public/private repos at this scale |
| CI/CD | GitHub Actions (free tier: 2,000 minutes/month for private repos) | Automated checks/deployments on push |
| Error tracking | Sentry (free tier) | Captures runtime errors in both bot and dashboard |
| Secrets management | `.env` files (local, git-ignored) + GitHub Secrets (CI/CD) | No secret ever committed to the repo or pasted in any AI chat |

## 5. The 13 Layers — Detailed Mapping

This section maps the Handler's originally specified 13 architectural layers
to concrete implementation decisions, so nothing is ambiguous.

| # | Layer | Implementation | Notes |
|---|---|---|---|
| 1 | Frontend foundations | Next.js + Tailwind | Single-page dashboard app, no multi-tenant complexity |
| 2 | APIs and backend logic | Next.js API routes + Python bot engine (separate process) | Two distinct codebases: `/dashboard` (Next.js) and `/bot` (Python), both in the same repo |
| 3 | Database and storage | Supabase Postgres | Schema defined in `05-DATABASE-SCHEMA.md` |
| 4 | Auth and permissions | Supabase Auth | Single user account; permissions structure built correctly but minimal in practice |
| 5 | Hosting and deployment | Vercel (dashboard); local Windows machine → MetaApi.cloud (bot) | Two different hosting stories for two different components — this is intentional, not an inconsistency |
| 6 | Cloud and compute | Supabase (managed) + MetaApi.cloud (future) | No custom server infrastructure to manage |
| 7 | CI/CD and version control | GitHub + GitHub Actions | Pipeline defined in `06-IMPLEMENTATION-PLAN.md` |
| 8 | Security and row level security | Supabase RLS policies on every table | Detailed per-table in `05-DATABASE-SCHEMA.md` |
| 9 | Rate limiting | Minimal — Supabase's built-in API rate limits are sufficient at single-user scale | Not custom-built; flagged explicitly as out-of-scope-for-now in PRD |
| 10 | Caching and CDN | Vercel's built-in Edge Network | No custom caching layer needed |
| 11 | Load balancing and scaling | Vercel's automatic serverless scaling | No manual configuration needed; scale is trivial at single-user volume |
| 12 | Error tracking and logs | Sentry (both bot and dashboard) + Supabase's own query logs | Bot also writes its own structured logs to the database (see PRD 6.10) separate from error tracking |
| 13 | Availability and recovery | GitHub (code backup/version history) + Supabase automatic backups (free tier retention limits apply) | No custom disaster recovery system — relying on providers' built-in free-tier backup features |

**Important honesty note for incoming devs:** Layers 9 and 11 exist in this
table because the Handler's original architecture template included them for
completeness, but this is explicitly a **single-user system**, not a
multi-tenant SaaS product. Do not over-engineer these layers. If a future
pivot to multi-user is decided, these layers would need real design work at
that point — not now.

## 6. Data Flow (Detailed, End-to-End)

1. **Bot engine starts** (manually run locally in Phases 1–3, later
   automated via scheduled cloud execution)
2. Bot connects to MT5 terminal via the `MetaTrader5` Python package,
   authenticated against the demo (later live) account
3. Bot continuously checks current time (NY timezone, DST-aware) against the
   21 defined windows
4. When a window becomes active:
   a. Bot waits for Candle A to close, records `High_A`, `Low_A`, `Mid_A`
   b. Bot monitors Candle B's open price, classifies context (Premium/
      Discount/Void) per PRD 6.4
   c. If void → bot writes a `NO_SETUP` record to the database immediately
      with reason, evaluation ends for this window
   d. If valid context → bot monitors 1-minute/tick data for sweep +
      reversal per PRD 6.5
   e. If window closes with no valid sweep+reversal → bot writes `NO_SETUP`
      with appropriate reason
   f. If sweep+reversal completes → bot calculates entry, SL, TP (PRD 6.6,
      6.7), submits market order via MT5
5. **Trade management:** bot monitors open position, executes the partial
   exit + breakeven rule at 80% of distance-to-TP (PRD 6.8)
6. **On trade close** (TP hit, SL hit, or breakeven stop hit): bot writes
   the full trade record to the database (entry, SL, TP, exit, result,
   R-multiple, partial flag)
7. **Dashboard** independently queries the database (via Supabase client,
   authenticated) to display all logged windows and trades — it does not
   know or care that a bot exists; it only reads data

## 7. Environment Setup Requirements

A new developer joining this project needs:

1. **Windows machine** (required for Phases 1–3 bot execution and testing;
   dashboard development can happen on any OS)
2. **Python 3.11+** installed, with a virtual environment per the
   repo's `/bot` folder instructions (to be detailed in
   `06-IMPLEMENTATION-PLAN.md`)
3. **MetaTrader 5 terminal** installed, logged into the project's demo
   account (credentials shared securely outside of any chat/doc — never
   committed to the repo)
4. **Node.js** (LTS version) for dashboard development
5. **GitHub access** to the `chronos-trading-system` repository
6. **Supabase project access** (credentials shared securely, never
   committed)
7. A **`.env.local`** file (dashboard) and **`.env`** file (bot), both
   git-ignored, containing all secrets (Supabase URL/keys, MT5 login
   details) — exact required variables documented in
   `06-IMPLEMENTATION-PLAN.md`

## 8. Security Requirements (Non-Negotiable)

1. **No secrets in code, commits, chat, or documentation.** Ever. This
   includes MT5 login/password, Supabase service role keys, API keys of any
   kind.
2. All secrets live in `.env` files (local) or GitHub Secrets (CI/CD),
   referenced by name only in documentation (e.g., "requires
   `MT5_LOGIN` environment variable").
3. Row Level Security must be enabled on every Supabase table from the
   moment it is created — never left open "temporarily."
4. The MT5 account used during development phases must be a **demo
   account** until the project explicitly and deliberately moves to Phase
   4 (live), per `07-PHASE-PLAN.md` — this transition requires explicit
   Handler approval, not an agent's unilateral decision.
5. Dashboard authentication must be in place before any real trade data is
   displayed, even though it's single-user — no public/unauthenticated
   access to trade data or account performance.

## 9. Free-Tier Limits to Be Aware Of

These are real constraints the system must operate within. If any of these
limits become a blocker, it must be flagged to the Handler — do not silently
upgrade to a paid tier.

| Service | Free tier limit (approximate, verify current values) |
|---|---|
| Supabase | 500MB database, 1GB file storage, 2GB bandwidth/month, projects pause after 1 week of inactivity on free tier |
| Vercel | 100GB bandwidth/month, generous build minutes for hobby projects |
| GitHub Actions | 2,000 minutes/month (private repos) |
| MetaApi.cloud | Limited number of connected accounts/throughput on free tier (verify current limits when this phase is reached) |
| Sentry | Limited error events/month on free tier |

**Note on Supabase pausing:** Free-tier Supabase projects pause after a
period of inactivity. Since the bot will be writing data regularly during
active development/testing, this is unlikely to be hit during build phases,
but could occur during long gaps (e.g., between sessions). If a project
appears unresponsive, check for this first before assuming a bug.

## 10. Naming Conventions

| Item | Convention | Example |
|---|---|---|
| Git branches | `phase-{n}/{short-description}` | `phase-1/logic-validation` |
| Commit messages | `{type}: {short description}` (type = feat/fix/docs/chore/refactor) | `feat: add window detection module` |
| Database tables | `snake_case`, plural | `trade_logs`, `window_evaluations` |
| Python files/modules | `snake_case` | `window_detector.py` |
| React components | `PascalCase` | `TradeLogTable.tsx` |
| Environment variables | `UPPER_SNAKE_CASE` | `MT5_LOGIN`, `SUPABASE_URL` |

## 11. Non-Functional Requirements

- **Reliability:** The bot must never silently fail a window evaluation —
  every window produces a logged outcome, even in error scenarios (log the
  error itself as the "reason" if something breaks).
- **Timezone correctness:** All window/time logic must be tested across a
  DST transition to confirm correctness (NY time shifts relative to UTC
  twice yearly).
- **Auditability:** Every trade and every NO_SETUP must be traceable back
  to the exact rule/condition that produced it — this is a product
  requirement (PRD 6.10), not just a technical nicety.
- **No real-money risk during development:** Live account connection only
  happens in Phase 4, with explicit Handler sign-off.

## 12. Testing Strategy (High-Level)

- **Logic validation (Phase 1):** Run the strategy engine against known
  historical price sequences with manually pre-calculated expected
  outcomes (calculated by hand/spreadsheet for a handful of sample windows)
  to confirm the code matches the specification exactly.
- **Dry-run validation (Phase 2):** Run against live data feed, comparing
  bot's logged decisions against the Handler's own manual observation of
  the same windows in real time, to catch any live-data-handling
  discrepancies not visible in historical replay.
- **Demo execution validation (Phase 3):** Confirm real order placement,
  fill behavior, partial exit, and breakeven logic all function correctly
  with actual (demo) broker execution, which can behave differently than
  simulated fills.

Detailed test cases will be defined per-module during implementation, not
exhaustively listed here.

## 13. Decision Log (Key Technical Choices & Rationale)

| Decision | Rationale |
|---|---|
| Python for bot engine | Required for MT5 package compatibility; also widely supported for data analysis if strategy refinement is needed later |
| Supabase over custom backend | Free tier covers all current needs (DB, Auth, RLS) without maintaining a separate server |
| Next.js + Vercel for dashboard | Free, fast to build, strong free hosting tier, good fit for a metrics-focused single-page app |
| Decoupled bot/dashboard via database only | Allows independent development/testing of each component; reduces complexity and failure coupling |
| MetaApi.cloud deferred to later phase | Avoids adding a third-party dependency before core logic is even proven; local Windows execution is sufficient for Phases 1–3 |
| Single-user scope, but RLS/Auth built properly | Correct security hygiene from day one, avoids costly retrofitting if the project ever expands |

## 14. Open Items / Unresolved Technical Questions

These must be resolved during Implementation Planning (`06-IMPLEMENTATION-
PLAN.md`) before relevant code is written — do not proceed on assumptions:

- Confirm exact instrument symbol name as it appears in the MT5 demo
  account (may differ from "NAS100" naming depending on broker)
- Confirm tick size and point value for that instrument as shown in MT5
  (must reconcile against PRD's original CFD specification)
- Confirm source and format of historical tick/1-minute data available for
  Phase 1 validation (Dukascopy export format vs. MT5's own historical
  data export)
- Confirm exact Supabase free-tier pause behavior and whether a keep-alive
  mechanism is needed during long gaps between development sessions