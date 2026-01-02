# Changelog

This project aims to build a human-in-the-loop trading ops system per `PLAN.md` (source of truth).

## Unreleased

### Decisions (defaults from `PLAN.md`)
- Trading constraints: **long-only**, **no leverage**, **no CFDs**.
- Database: **Postgres is the single source of truth (SoT)**.
- Data location: persistent data under `/data/trading-ops/...`

### Milestone 1 progress
- Step 1.1: Added `docker/compose.yml` and `Makefile` targets to run Postgres locally (bound to `127.0.0.1`).
- Step 1.2: Added SQL migrations (`db/migrations/0001_init.sql`) and a migration runner (`scripts/db_migrate.sh`).
- Step 1.3: Added backups (`scripts/db_backup.sh`), restore (`scripts/db_restore.sh`), and installed `trading-ops-db-backup.timer` (daily).

### Environment evidence (fill in)
- Host OS (`cat /etc/os-release | sed -n '1,12p'`):
  - TODO: paste output here after Step M1-S1 verification
