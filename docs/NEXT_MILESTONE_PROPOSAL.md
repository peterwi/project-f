# Next Milestone Proposal (toward “daily automated trade instructions”)

Target end-state: a deterministic, daily, reproducible pipeline that produces a human-reviewable “daily ticket” with trade instructions and evidence, without bypassing gates.

Below are three milestone options; pick one to execute next.

---

## Option A — M10 Hardening + Safety (recommended after data is stable)

### Objective
Reduce operational risk and make daily runs safer and more reproducible (secrets hygiene, backups/restore proof, permissions, and regression checks).

### Checklist items (5–10)
1) Confirm secrets are local-only (`open-ai.key`, `config/secrets.env`) and never logged.
2) Add a “sanitized logging” rule for any new scripts that touch env vars.
3) Prove Docker access is stable in the operator session (no sudo; no hidden blockers).
4) Prove DB backup + restore procedure works (dry-run, non-destructive where possible).
5) Add minimal smoke checks for the daily pipeline entrypoints (lint optional; runtime checks preferred).
6) Add artifact retention/pruning guidance for `/data/trading-ops/artifacts/*` (avoid disk exhaustion).

### Verification commands
- `git status --porcelain=v1`
- `docker ps`
- `git check-ignore -v config/secrets.env open-ai.key || true`
- `rg -n \"OPENAI_API_KEY|Authorization: Bearer\" -S . || true` (ensure no logs/scripts print secrets)
- `ls -la /data/trading-ops/artifacts | head`
- Backup/restore proof (use existing scripts if present):
  - `ls -la scripts | rg -n \"backup|restore\"`
  - `bash scripts/db_backup.sh --help || true`
  - `bash scripts/db_restore.sh --help || true`

### Artifacts
- `/data/trading-ops/artifacts/<...>/backup_*.log` (or equivalent)
- `docs/PM_LOG.md` entry with commands + outputs

### Why it matters for “daily ticket”
Daily automation without hardening tends to fail silently (permissions, disk, secrets leakage). This option makes “daily ticket” runs safe to schedule and debug.

---

## Option B — M6.3 Data Quality PASS + stable EOD pipeline (recommended first)

### Objective
Make the daily EOD ingestion + data-quality gate consistently PASS so the “daily ticket” can be produced without frequent operator intervention.

### Checklist items (5–10)
1) Identify the first failing data-quality check(s) in the current daily path.
2) Make EOD ingest deterministic (same inputs → same outputs) and idempotent.
3) Ensure coverage: `adj_close` populated for 100% ingested rows (no nulls).
4) Ensure universe fetch isn’t over-inclusive (enabled symbols only + explicit benchmarks).
5) Ensure failures write actionable artifacts under `/data/.../artifacts/...` (not just console).
6) Add a single “daily pipeline” smoke command that runs ingest + gate + report in dry-run/shadow mode.

### Verification commands
- `git status --porcelain=v1`
- `python scripts/market_fetch_eod.py --max-rows 5`
- `python scripts/data_quality_gate.py --help || true`
- `python scripts/report_daily.py --help || true`
- If Postgres is used:
  - `docker compose -f docker/compose.yml --env-file config/secrets.env ps`
  - `docker compose -f docker/compose.yml --env-file config/secrets.env exec -T postgres psql -U \"$POSTGRES_USER\" -d \"$POSTGRES_DB\" -tA -c \"select count(*) from market_prices_eod where adj_close is null;\"`

### Artifacts
- `/data/trading-ops/artifacts/<...>/data_quality_*.md` (or equivalent)
- `/data/trading-ops/artifacts/<...>/daily_*.md` (daily ticket inputs)
- `docs/PM_LOG.md` entry with run_id + verification outputs

### Why it matters for “daily ticket”
If data-quality fails frequently, the daily ticket can’t be generated reliably. This option addresses the most common blocker for daily automation: “pipeline doesn’t reach the ticket step.”

---

## Option C — M8.4 Real secondary alert sink (Slack/ntfy/email)

### Objective
Ensure operational failures are visible quickly by delivering alerts to a real channel (while keeping dry-run defaults and respecting “no live trading” constraints).

### Checklist items (5–10)
1) Decide the sink (ntfy preferred for simplicity; Slack/email optional).
2) Implement a dry-run mode that writes delivery artifacts but does not send.
3) Implement a send mode gated behind explicit operator confirmation.
4) Add retry/backoff + failure artifact capture.
5) Ensure secrets for alert delivery are stored locally-only and never logged.

### Verification commands
- `git status --porcelain=v1`
- `python scripts/alert_emit.py --help || true`
- `python scripts/alert_deliver.py --help || true`
- Dry-run delivery:
  - `python scripts/alert_emit.py ...`
  - `python scripts/alert_deliver.py ... --dryrun`
- Verify artifacts:
  - `ls -la /data/trading-ops/artifacts/alerts | tail`

### Artifacts
- `/data/trading-ops/artifacts/alerts/<alert_id>/delivery.md`
- `/data/trading-ops/artifacts/alerts/<alert_id>/delivery.json`
- `docs/PM_LOG.md` entry with verification outputs

### Why it matters for “daily ticket”
Daily automation without alerting can fail unnoticed. This option improves operator awareness but does not, by itself, make the pipeline reach “daily ticket.”

---

## Recommendation
Pick **Option B first** (stabilize EOD + data-quality PASS). Then do **Option A** (hardening) before putting anything on a schedule. Option C is valuable but best after the core daily run is stable.
