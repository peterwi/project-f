# Runbook

## Purpose
Operator instructions for running the trading ops pipeline and handling routine checks.

## Quick links
- Decisions log: `docs/CHANGELOG.md`
- Incidents: `docs/INCIDENTS.md`
- eToro constraints: `docs/ETORO_CONSTRAINTS.md`
- Execution workflow: `docs/EXECUTION_WORKFLOW_1PAGER.md`

## Non-negotiable governance
- **NO DATA / NO RECONCILE / NO CONFIRM / NO TRADE**
- eToro execution is **manual**. This system must never automate clicks/orders.
- Deterministic gates decide; humans execute and confirm.

## Common commands (Milestone 1)
All commands run from repo root.

### Prereqs (local scripts)
- Python: `python3 -m venv .venv && . .venv/bin/activate && pip install -r requirements.txt`

### Service control
- Start Postgres: `make up`
- Stop Postgres: `make down`
- Logs: `make logs`
- Status: `make ps`

### Health + connect
- Healthcheck (pg_isready): `make health`
- DB connectivity sanity: `make psql`

### Schema/migrations
- Apply migrations: `make migrate`
- List tables: `make db-tables`

### Backups
- Create a backup now: `make backup`
- Restore proof (fresh data dir; destructive to current DB state): `make restore-fresh-test`

## One-command ops run (no trading)
Runs the current deterministic gates end-to-end. Produces a run summary under `/data/trading-ops/artifacts/runs/<run_id>/`.

- Run: `make run-ops`

## Daily ops (08:00 + 14:00 UK)

### 08:00 — fetch EOD + gates
- Run: `make run-0800`
- Expect: a new run summary under `/data/trading-ops/artifacts/runs/<run_id>/run_summary.md`

### Reconciliation (required before any TRADE ticket)
- Capture eToro snapshot and record it: `docs/RECONCILIATION_SOP.md`
- Recommended one-liner (add snapshot + run gate):
  - `make reconcile-daily -- --snapshot-date YYYY-MM-DD --cash-gbp <cash> --position SYMBOL=UNITS --notes "manual_etoro_snapshot"`
- Expect: `RECONCILIATION_PASS` + report under `/data/trading-ops/artifacts/reports/`

### 14:00 — ticket generation (safe by default)
- Run: `make run-1400`
- Expect: exactly one deterministic ticket; default must be `NO_TRADE` unless all gates pass.
- Test-only: `DRYRUN_TRADES=true make run-1400` may emit a `TRADE` ticket when gates pass.

## Confirmations (operator acknowledgement)
- NO_TRADE ack (daily close-out): `make confirm`
- TRADE fills (after manual execution): `FILLS_JSON=/path/to/fills.json make confirm-fills`

### Backup schedule
- Install daily systemd timer: `make install-backup-timer`
- Check timer: `systemctl status trading-ops-db-backup.timer --no-pager`

## Where data lives
- Postgres data: `/data/trading-ops/postgres-data`
- Backups: `/data/trading-ops/backups/postgres`
- A restore proof leaves the prior data dir at: `/data/trading-ops/postgres-data.bak-<timestamp>`

## TODO (next milestones)
- Add tickets + confirmation UI (Project 8 in `PLAN.md`).
