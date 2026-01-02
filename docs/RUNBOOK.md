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

### Backup schedule
- Install daily systemd timer: `make install-backup-timer`
- Check timer: `systemctl status trading-ops-db-backup.timer --no-pager`

## Where data lives
- Postgres data: `/data/trading-ops/postgres-data`
- Backups: `/data/trading-ops/backups/postgres`
- A restore proof leaves the prior data dir at: `/data/trading-ops/postgres-data.bak-<timestamp>`

## TODO (next milestones)
- Add tickets + confirmation UI (Project 8 in `PLAN.md`).
