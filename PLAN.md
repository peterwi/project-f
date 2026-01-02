# PLAN.md — Human-in-the-loop Trading Ops (eToro Execution Desk) on MacPro Ubuntu + Postgres + Qlib + GenAI/MCP

> Goal: Build a sustainable, auditable, “startup-grade” trading operations system where **the machine does everything except clicking Buy/Sell on eToro**.  
> Your wife executes trades manually using **unambiguous, deterministic trade tickets**.  
> **Qlib is the central research+model engine** for ranking/scoring/backtesting — but it is always constrained by deterministic risk gates and manual execution reality.  
> GenAI acts as **project manager + analyst + ops writer**, but **cannot bypass deterministic risk gates**.

---

## 0) Non-negotiable principles

### 0.1 Safety + sustainability
- **No scraping eToro UI.** No browser automation. No unofficial endpoints. We treat eToro as a manual execution venue only.
- **Machine does analysis + instructions; humans execute.**
- **Deterministic risk engine is the final authority.** LLM can explain, format, and detect anomalies — but it cannot approve trades.
- **“No data / no reconcile / no confirm / no trade.”** Any gate failure produces a `NO-TRADE` ticket with reasons.

### 0.2 Reproducibility + auditability
Every “run” must be reconstructable and explainable after the fact:
- inputs (data snapshot), config hash, code version (git commit), model version, outputs (signals, targets, tickets), and final decision (approve/block) are all stored.
- tickets and confirmations are stored verbatim (including who executed and when).

### 0.3 Execution reality (eToro constraints)
Because eToro execution is manual and instrument availability can be tricky:
- Start **long-only**, **no leverage**, **no CFDs** (unless you explicitly accept CFD risk later).
- Avoid instruments likely blocked by UK PRIIPs/KID rules (many ETFs become CFDs). Prefer **large-cap stocks** as underlying.
- Strategy cadence should be **weekly rebalance** (default) to keep workload and turnover low.

---

## 1) Definition of success (v1)

A v1 “success” means:
1. A scheduled pipeline produces exactly one of:
   - a single **TRADE ticket** with unambiguous instructions, OR
   - a single **NO-TRADE ticket** with clear reasons.
2. The wife can execute without judgement calls:
   - every trade has a precise “do X” instruction, a “skip if Y” rule, and a confirmation step.
3. The system blocks trading automatically when:
   - data quality fails,
   - reconciliation drift is too high,
   - confirmations missing for last ticket,
   - any risk rule fails,
   - kill-switch triggers.
4. Portfolio truth is consistent:
   - ledger is close enough to reality to size trades safely.
5. System is operationally stable:
   - runs on schedule, produces reports, can restore from backups.

### 1.1 Definition of success (v1.5 — Qlib-enabled, still ops-first)
Qlib is “enabled” when all of the following are true:
- A versioned, **clean, adjusted EOD dataset** exists (exported for Qlib consumption, reproducible per run).
- Qlib can run a weekly scoring job that writes ranked signals to the database (`signals_ranked`) with:
  - `run_id`, `config_hash`, `git_commit`, `model_version` recorded
- Riskguard remains the final authority; Qlib cannot bypass gates.
- Execution remains manual; tickets remain deterministic.

---

## 2) High-level architecture (startup-grade)

### 2.1 Services (Docker)
- `postgres` — single source of truth (SoT)
- `migrations` — schema + versioning
- `mcp-market-data` — fetch + normalize EOD market data + quality report
- `qlib-runner` — runs Qlib training/scoring/backtests (compute) using versioned datasets on `/data`
- `mcp-qlib` — (later) exposes Qlib operations via MCP (external dependency; **do not implement in this repo yet**)
- `mcp-ledger` — portfolio ledger read/write (constrained)
- `mcp-riskguard` — deterministic risk + sizing + approvals
- `mcp-ticketing` — ticket rendering (Markdown + JSON) from approved trades
- `mcp-comms` — sends tickets and hosts confirmation UI
- `mcp-reporting` — daily/weekly performance reporting + alert evaluation
- `scheduler` — orchestration and scheduling (APScheduler or cron inside container)
- `llm-orchestrator` — “PM agent” that drives MCP tools, writes narrative, generates runbooks, and assembles artifacts

> NOTE: LLM orchestrator must never connect directly to Postgres. It talks only to MCP tools.

### 2.2 Data flows
1. Market data fetched and normalized → Postgres (`market_prices_eod`)
2. **Qlib dataset export** (clean + adjusted) → `/data/...` and/or Postgres-backed provider (versioned per run)
3. Qlib scores universe → Postgres (`signals_ranked`)
3. Riskguard transforms scores + ledger + policy → targets + intended trades; runs checks → Postgres
4. Ticketing renders trade ticket → Postgres + filesystem artifact
5. Comms sends ticket + hosts `/confirm/<ticket_id>` UI
6. Wife executes and submits fills → Postgres (`ledger_trades_fills`)
7. Reporting computes P&L and exposure; alerts if issues

---

## 3) Build strategy: “Ops loop first, Qlib central”

### 3.1 Critical path (must be stable before ML)
Build the boring loop first:
- Postgres + schema
- Universe management
- Ledger + reconciliation
- Ticketing + confirmations
- Reporting + alerts
- Riskguard with a **stub signal generator** (e.g., equal-weight top-N from a static list)

Only once this is stable for multiple weeks:
- integrate Qlib training/scoring and the model.

### 3.2 Why
- Most failures are operational (data gaps, wrong symbols, missing confirmations, cash drift), not model-related.
- A “perfect model” is useless without reliable execution + bookkeeping.

### 3.3 Qlib readiness rule (do early, without “turning on ML”)
To keep Qlib central without taking risk:
- Build the **Qlib-ready data layer early** (clean + adjusted EOD, versioned exports), and keep Qlib runs in **shadow mode** until ops gates are stable.
- Shadow mode means: Qlib can score and log results, but tickets are still produced from stub signals (or are blocked) until explicitly enabled.

---

## 4) Project plan — Build phase (Step 1: Starting)

> Each project below has: Objective, Deliverables, Acceptance Criteria, and Checklist.

---

### Project A — Pre-flight: Reality checks (eToro + UK constraints)

**Objective:** Confirm constraints that determine universe, sizing, and workflows.

**Deliverables:**
- `docs/ETORO_CONSTRAINTS.md` with confirmed facts:
  - base currency (GBP/USD)
  - can you trade underlying stocks (yes) and ETFs as underlying (often no)
  - minimum order sizes / fractional shares behavior
  - order types available (market/limit) and availability per instrument
  - how fees/spreads appear in execution
  - what fields can wife reliably record (fill price, units, executed value)
- `docs/EXECUTION_WORKFLOW_1PAGER.md` (wife’s click-by-click SOP)

**Acceptance criteria:**
- Wife can execute a single test trade in demo/small size and capture:
  - symbol, side, executed value/units, fill price, timestamp
- You can list at least 50 tradable large-cap stocks verified on eToro.

**Checklist:**
- [ ] Decide: underlying-only / no leverage / no CFDs (default YES)
- [ ] Decide: position sizing by **£ value** (recommended) vs units
- [ ] Decide: execution window relative to US open (DST-aware)
- [ ] Confirm: what happens on holidays / half-days (document only)

---

### Project B — Repo + environment + Docker baseline

**Objective:** Create a reproducible platform on MacPro Ubuntu.

**Deliverables:**
- `docker/compose.yml`
- `config/secrets.env.example`
- `Makefile` or `scripts/run_pipeline.sh` that runs the entire pipeline end-to-end and returns non-zero on failure
- baseline docs: `docs/RUNBOOK.md`, `docs/INCIDENTS.md`, `docs/CHANGELOG.md`

**Acceptance criteria:**
- `make up` starts all services
- `make health` confirms postgres + comms UI reachable
- backups run daily and restore is tested (see Project C)

**Checklist:**
- [ ] Install Ubuntu 22.04/24.04 on MacPro
- [ ] Install Docker + docker-compose plugin
- [ ] Enable NTP time sync
- [ ] Create git repo and directory layout (see Section 9)
- [ ] Configure firewall: allow only required ports (e.g., comms UI local/LAN)

---

### Project C — PostgreSQL schema + migrations + backups (non-negotiable)

**Objective:** Postgres is the SoT; every run is auditable.

**Deliverables:**
- `db/migrations/0001_init.sql` (or Alembic)
- automated backup:
  - daily `pg_dump` to `backups/`
  - retention policy (e.g., 30 days)
- tested restore script `scripts/restore_db.sh`

**Acceptance criteria:**
- Can restore DB to a fresh container and reproduce last reports and last ticket.
- Schema includes required run-tracking metadata (run_id, config_hash, git_commit).

**Checklist (schema minimum):**
- [ ] config tables:
  - [ ] `config_policy` (active policy row)
  - [ ] `config_universe` (symbol mapping + tradable flags)
- [ ] market data:
  - [ ] `market_prices_eod` with source + quality flags
- [ ] ledger:
  - [ ] `ledger_trades_intended`, `ledger_trades_fills`, `ledger_cash_movements`
- [ ] decisions:
  - [ ] `signals_ranked`, `portfolio_targets`, `risk_checks`, `decisions`
- [ ] ops artifacts:
  - [ ] `tickets`, `confirmations`, `audit_log`
- [ ] all tables have created_at timestamps and foreign keys where appropriate

---

### Project D — Universe management (tradable set is king)

**Objective:** Prevent model suggesting untradable instruments.

**Deliverables:**
- `config/universe.csv` (authoritative)
  - internal_symbol
  - stooq_symbol
  - yahoo_symbol
  - etoro_search_name
  - currency
  - instrument_type (stock/etf)
  - tradable_underlying (true/false)
  - enabled (true/false)
  - notes
- Import job populates `config_universe` in Postgres.
- A “universe validation report” listing enabled symbols and data coverage.

**Acceptance criteria:**
- Universe contains at least:
  - 50–100 verified large-cap stocks
  - benchmark symbol(s): e.g., QQQ/SPY (or an alternative if ETFs not underlying)

**Checklist:**
- [ ] Wife verifies each symbol is findable on eToro
- [ ] Define naming conventions and keep stable (avoid ambiguous tickers)
- [ ] Decide what to do if symbol becomes untradable:
  - auto-disable + raise incident

---

### Project E — Market data pipeline (EOD) with quality gates

**Objective:** Produce a clean, adjusted EOD dataset; block trading if stale or incomplete.

**Deliverables:**
- Market data ingestion service (MCP tool or internal module):
  - pulls EOD from Stooq primary, yfinance fallback
  - writes to Postgres `market_prices_eod`
- Data quality report stored in Postgres and emitted in daily report:
  - coverage %, missing symbols, stale bar detection, duplicates
- Corporate action adjustment rule implemented:
  - factor = adj_close/close; apply to OHLC
- `docs/DATA_QUALITY_RULES.md`

**Acceptance criteria:**
- For last T-1 US trading day:
  - ≥ 98% universe coverage (default; configurable)
  - benchmark present
  - no duplicates
  - consistent date alignment

**Checklist:**
- [ ] Implement “freshness” logic:
  - running UK morning means “latest bar” should be previous US trading day (T-1)
- [ ] Add retry/backoff and per-symbol error logging
- [ ] Store source + quality_flags per row
- [ ] Block trading on data gate fail

---

### Project F — Ledger + reconciliation (truth about cash/positions)

**Objective:** Your ledger must match reality enough to size trades and enforce risk.

**Deliverables:**
- Ledger service that:
  - computes positions from fills
  - computes cash from starting cash + fills + cash movements
  - produces valuation using latest EOD prices
- Reconciliation workflow:
  - Wife captures an eToro “snapshot” weekly (cash + holdings summary)
  - You enter snapshot into system (manual form)
  - System checks drift vs ledger; blocks trading if above tolerance
- `docs/RECONCILIATION_SOP.md`

**Acceptance criteria:**
- Weekly drift check works and blocks trading when drift above threshold.
- You can reconstruct portfolio state from DB from day 1.

**Checklist:**
- [ ] Decide tolerance thresholds:
  - cash drift, position drift (units/value)
- [ ] Create a “cash movements” input method (deposits/withdrawals/fees adjustments)
- [ ] Ensure ledger can represent partial fills / failed executions

---

### Project G — Riskguard (deterministic sizing + approvals)

**Objective:** Produce an approved target portfolio and trade list; be the final authority.

**Deliverables:**
- Deterministic risk engine that:
  - takes ranked signals (or stub), ledger positions, prices, and policy
  - produces `portfolio_targets` and `ledger_trades_intended`
  - runs `risk_checks`
  - sets `decisions.approved` true/false with reason
- `docs/RISK_POLICY.md` describing each check

**Default v1 risk checks:**
- Max positions: 10–20
- Max position weight: 5–10%
- Cash buffer: 2–5%
- Turnover cap per rebalance (e.g., 30% of equity)
- Kill switch on drawdown (e.g., >20% from peak)
- No shorting; no leverage; no CFDs (unless explicitly allowed)
- “Ticket sanity” check: unknown symbols, impossible trade sizes, etc.

**Acceptance criteria:**
- Given a stub signal, riskguard generates intended trades that respect all limits.
- Riskguard blocks and emits clear reasons when it should.

---

### Project H — Ticketing + Comms + Confirmations (execution desk)

**Objective:** Convert intended trades into a professional trade ticket and capture execution.

**Deliverables:**
- Ticket format:
  - Markdown + JSON
  - includes: ticket_id, run_id, config hash, git commit, execution window, numbered trade instructions
  - includes: slippage rule, “skip if …” rules, what to do when blocked
- Confirmation UI:
  - `/confirm/<ticket_id>` showing ticket and per-trade confirmation fields:
    - executed status (DONE/SKIPPED/FAILED/PARTIAL)
    - executed value and/or units
    - fill price
    - timestamp
    - notes
- Comms channel:
  - email or Slack (pick one)
  - sends ticket link + summary

**Acceptance criteria:**
- A ticket is sent automatically on schedule.
- Wife can submit confirmations and system updates ledger positions.

**Checklist:**
- [ ] Ticket must never include ambiguous language
- [ ] Include instrument identifiers and eToro search name exactly
- [ ] Include DST-aware execution time (US market open shifts vs UK time)
- [ ] Confirmation deadline with reminders/escalation

---

### Project I — Reporting + Monitoring + Alerts (ops visibility)

**Objective:** Produce daily and weekly reports and alert on failures.

**Deliverables:**
- Daily report (auto):
  - equity, realized/unrealized P&L, drawdown, exposures, cash buffer, next rebalance date
  - gates status: data, reconcile, confirmations, risk checks
- Weekly report:
  - intended vs executed trades
  - execution compliance
  - turnover
  - benchmark comparison
- Alerts:
  - missing confirmations
  - data gate fail
  - reconciliation drift
  - kill switch triggered
  - “untradable drift” (symbols missing on eToro)

**Acceptance criteria:**
- Reports generated even if no trade (NO-TRADE run still produces report).
- Alerts fire for each class of failure.

---

### Project J — ML/Qlib integration (only after ops loop stable)

**Objective:** Replace stub signals with Qlib-ranked signals and backtests.

**Deliverables:**
- Qlib dataset built from Postgres EOD prices (or exported parquet)
- Weekly scoring job that writes to `signals_ranked`
- Backtest report artifact saved per run
- Model registry fields in DB:
  - model_version, training window, feature set id

**Acceptance criteria:**
- Backtest runs and saves summary.
- Live scoring produces ranked signals and feeds riskguard.

**Checklist:**
- [ ] Start with LightGBM ranker/regression-to-ranking
- [ ] Use stable features (momentum/vol/volume)
- [ ] Avoid “hyperparameter mania” in live phase
- [ ] Keep turnover low by policy (the model can be good yet churny)

---

### Project K — MCP suite + GenAI orchestrator (PM-first)

**Objective:** GenAI acts as project manager, ops writer, anomaly detector, explainer.

**MCP servers to build (startup-grade boundaries):**
1. `mcp-market-data`
   - fetch_eod(), get_eod(), data_quality(), latest_trading_day()
2. `mcp-ledger`
   - get_positions(), get_cash(), write_fills(), reconciliation_report(), equity_curve()
3. `mcp-qlib`
   - run_train(), run_score(), run_backtest()
4. `mcp-riskguard`
   - build_targets(), build_trades(), run_checks(), approve_or_block()
5. `mcp-ticketing`
   - render_ticket_md(), render_ticket_json()
6. `mcp-comms`
   - send_ticket(), send_alert(), confirmation_ui_link()
7. `mcp-reporting`
   - daily_report(), weekly_report(), benchmark_compare()

**LLM orchestrator responsibilities (PM mode):**
- Drives the build checklist step-by-step
- Produces “next action” tasks and validates completion via tool outputs
- Writes/updates docs:
  - RUNBOOK, SOPs, incident templates, change log entries
- Generates human-friendly explanations for tickets and NO-TRADE reasons
- Flags anomalies:
  - unusual score shifts, data gaps, large price moves, unexpected turnover

**Acceptance criteria:**
- Orchestrator can run a full “rebalance simulation” end-to-end using stub signals and produce:
  - ticket + report + “PM summary” message.

---

## 5) Day-to-day operations (Step 2: Running)

### 5.1 Daily schedule (recommended)
**UK morning (e.g., 08:00 UK):** “Data + health + report”
1. Market data fetch + normalize for latest available bar (T-1 US day)
2. Data quality gate
3. Valuation update (ledger + EOD prices)
4. Generate daily report + alerts

> Output: daily status report. Usually no trading actions.

### 5.2 Weekly schedule (recommended)
**Friday after US close or Saturday morning:** “Plan run”
1. Freeze run inputs:
   - run_id, config_hash, git_commit, data_snapshot_id, asof_date
2. Qlib scoring (or stub)
3. Riskguard builds target portfolio and intended trades
4. Gate checks:
   - data quality PASS
   - reconciliation PASS
   - previous confirmations PASS
   - risk checks PASS
5. Ticket generation:
   - TRADE ticket if approved
   - NO-TRADE ticket if blocked
6. Send ticket to wife (+ you)

**Monday execution window (DST-aware):** “Execution”
- Wife executes ticket trades and submits confirmations.
- Confirmation deadline same day; reminders if missing.

**After confirmations:** “Reconcile”
- System ingests fills and produces:
  - execution reconciliation report (intended vs executed)
  - updated positions and valuation

---

## 6) Gate rules (default v1) — explicit and enforceable

### 6.1 Data Quality Gate
- benchmark bar present for asof_date
- universe coverage ≥ 98%
- no duplicate dates per symbol
- no “stale bar” (latest bar older than expected T-1)
- if fail → NO-TRADE ticket + incident logged

### 6.2 Confirmation Gate
- previous ticket must have confirmation status for every trade line item
- if missing → block next run

### 6.3 Reconciliation Gate
- weekly snapshot drift within tolerance
- unknown/unmatched positions hard-fail
- if fail → NO-TRADE + escalate to you

### 6.4 Risk Gate
- max position weight, max positions, turnover cap, cash buffer, kill-switch
- no short/leverage/CFD (unless allowed)
- if fail → NO-TRADE + reasons

---

## 7) Execution ticket spec (what “unambiguous” means)

Every trade line item must include:
- Sequence number
- eToro search name (exact)
- Ticker (internal symbol)
- Side: BUY/SELL
- Size: **£ value** (recommended) and/or units
- Order type: Market or Limit
- Slippage rule:
  - “If price is > X% above reference, SKIP and mark SKIPPED”
- Execution window (UK time + note about DST)
- Failure instructions:
  - “If instrument not found or only CFD: mark FAILED and continue”
- Confirmation fields required:
  - executed status, executed value/units, fill price, timestamp

---

## 8) Operational documents to produce (GenAI heavy lifting)

GenAI PM must generate these early and keep updated:
- `docs/RUNBOOK.md` — how to operate daily/weekly, what to do when alerts fire
- `docs/EXECUTION_WORKFLOW_1PAGER.md` — wife’s instructions
- `docs/RECONCILIATION_SOP.md` — snapshot process and drift resolution
- `docs/DATA_QUALITY_RULES.md`
- `docs/RISK_POLICY.md`
- `docs/INCIDENTS.md` — templates:
  - DATA_STALE
  - CONFIRMATION_MISSING
  - RECONCILIATION_DRIFT
  - UNTRADABLE_SYMBOL
  - KILL_SWITCH
- `docs/CHANGELOG.md` — one change window per week/month policy

---

## 9) Repo layout (final)

trading-ops/
docker/
compose.yml
config/
policy.yml
universe.csv
secrets.env.example
db/
migrations/
backups/
services/
scheduler/
llm-orchestrator/
mcp-market-data/
mcp-ledger/
mcp-qlib/
mcp-riskguard/
mcp-ticketing/
mcp-comms/
mcp-reporting/
artifacts/
tickets/
reports/
runs/
logs/
docs/
RUNBOOK.md
EXECUTION_WORKFLOW_1PAGER.md
RECONCILIATION_SOP.md
DATA_QUALITY_RULES.md
RISK_POLICY.md
INCIDENTS.md
CHANGELOG.md

yaml
Copy code

---

## 10) Implementation order (the step-by-step “build recipe”)

### Milestone 1 — Platform
1. Project B (Docker baseline)
2. Project C (Postgres schema + backups)
3. Confirm restore works

### Milestone 2 — Tradable universe
4. Project A (eToro constraints + SOP)
5. Project D (universe.csv + import + validation report)

### Milestone 3 — Data + ledger
6. Project E (market data + gates)
7. Project F (ledger + reconciliation)

### Milestone 4 — Risk + tickets + confirmations
8. Project G (riskguard + approvals)
9. Project H (tickets + comms + confirmation UI)

### Milestone 5 — Reporting + alerts
10. Project I (daily/weekly reports + alerts)

### Milestone 6 — Dry-run operations
11. Run on schedule for 2–4 weeks with **stub signals**
12. Fix ops defects only

### Milestone 7 — ML
13. Project J (Qlib scoring/backtests)
14. Re-run dry-run for 2 weeks (shadow mode)
15. Enable ML in live

### Milestone 8 — MCP + GenAI
16. Project K (MCP suite)
17. Enable LLM PM mode and analyst summaries

---

## 11) Rollout phases and exit criteria

### Phase 0: Pre-flight (1 week)
- constraints documented
- universe validated
- runbook created
- DB backups/restores tested

**Exit criteria:** Wife can execute and confirm one test ticket end-to-end.

### Phase A: Dry-run (2–4 weeks)
- real schedule
- no real trades
- confirmations entered
- reports generated
- gates tested

**Exit criteria:** 4 consecutive weekly cycles with:
- data gate PASS
- reconcile PASS
- confirmation PASS
- ticket generated with zero critical defects
- reports produced

### Phase B: Small capital (4–8 weeks)
- trade 10–20% allocation
- freeze strategy + risk config
- weekly reconciliation

**Exit criteria:** consistent reconciliation + no operational incidents.

### Phase C: Scale
- increase allocation gradually with monthly review packs.

---

## 12) Open decisions (defaults if not chosen)

If you do not choose, defaults:
- Run cadence: daily UK morning (data/report), weekly rebalance
- Strategy: long-only, underlying-only, no CFDs/leverage
- Universe: 100 large-cap US stocks verified on eToro
- Positions: 15
- Sizing: £-value
- Execution: market orders inside a window after US open
- Kill switch: drawdown > 20% triggers pause

---

## 13) “PM mode” checklist (how GenAI guides you)

At any moment, the PM agent must output:
- Current milestone
- Next 3 tasks
- Completion criteria for each
- Commands/checks you run to verify completion
- What artifacts should exist after completion

**Example PM message format:**
- Task: Apply DB migrations
- Verify: `psql` shows tables exist, migrations table updated
- Artifacts: `db/migrations/0001_init.sql`, backup job created
- Next: Universe import + validation report

---

## 14) First actionable next steps (start building now)

1) Create repo skeleton + docker compose with Postgres (Project B)
2) Write initial migrations for schema skeleton (Project C)
3) Write `config/policy.yml` with defaults (Project A/0)
4) Create `config/universe.csv` draft (Project D) and have wife validate first 20 symbols

When you finish step (1), you should be able to:
- start postgres container
- apply migrations
- connect and see schema

---

## 15) What you should NOT do (common failure modes)

- Do not start with Qlib first.
- Do not allow LLM to “decide trades”.
- Do not trade daily initially.
- Do not expand universe without eToro validation.
- Do not proceed without DB backups and restore test.

---
