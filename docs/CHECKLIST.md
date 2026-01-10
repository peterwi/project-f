# Master Checklist (single source of truth)

Rule: execute items **top-to-bottom, in order**. Every item must be verified before moving on.

Legend:
- `[ ]` TODO
- `[x]` DONE

Non-negotiables (must remain true always):
- Long-only, underlying stocks only, x1 leverage only.
- No eToro scraping / browser automation.
- **No data / no reconcile / no confirm / no trade.**
- Deterministic gates win; LLM cannot bypass riskguard.
- Reproducibility: every run stamped with `run_id`, `git_commit`, config snapshot, logs, and artifacts stored under `/data`.

---

## M0 — Repo reproducibility (git tracks core files; secrets excluded)

- [x] **M0.1 Track the repo in git (baseline commit exists)**
  - Objective: Ensure `git_commit` is meaningful for audit/repro.
  - Commands:
    - `git status --porcelain=v1`
    - `git ls-files | wc -l`
    - `git ls-files | sed -n '1,80p'`
  - Verification:
    - Core files are tracked (at minimum: `PLAN.md`, `Makefile`, `docker/compose.yml`, `db/migrations/*.sql`, `scripts/*.py`, `docs/*.md`).
    - Untracked list does not include core code/docs.
  - Artifacts:
    - One git commit containing the tracked baseline repo state.

- [x] **M0.2 Secrets are local-only (never committed)**
  - Objective: Prevent accidental secret leakage while keeping local ops working.
  - Commands:
    - `git status --porcelain=v1`
    - `git check-ignore -v config/secrets.env || true`
    - `rg -n "POSTGRES_PASSWORD=" -S --glob '!config/secrets.env' . || true`
  - Verification:
    - `config/secrets.env` is ignored/untracked.
    - `rg` finds no secrets outside `config/secrets.env`.
  - Artifacts:
    - None.

- [x] **M0.3 eToro constraints + executor SOP are locked**
  - Objective: Freeze human execution constraints (no CFDs/leverage/ETFs as tradables).
  - Commands:
    - `test -f docs/ETORO_CONSTRAINTS.md && rg -n "CONFIRMED" docs/ETORO_CONSTRAINTS.md | head`
    - `test -f docs/EXECUTION_WORKFLOW_1PAGER.md`
  - Verification:
    - Both files exist and reflect underlying-stock-only execution reality.
  - Artifacts:
    - `docs/ETORO_CONSTRAINTS.md`
    - `docs/EXECUTION_WORKFLOW_1PAGER.md`

---

## M1 — Docker foundation (compose structure + /data mounts + postgres verified)

- [x] **M1.1 Enable Docker access in this session (required for all docker-first work)**
  - Objective: Ensure this orchestrator can run Docker commands (needed for Qlib runner + Compose changes).
  - Commands:
    - `docker ps`
    - `docker compose version`
  - Verification:
    - Commands succeed (no “permission denied” against `/var/run/docker.sock`).
  - Artifacts:
    - None.

- [x] **M1.2 Postgres verified (running + healthy + bound to 127.0.0.1:5432)**
  - Objective: Confirm the Postgres SoT container is healthy and reachable.
  - Commands:
    - `docker ps --format 'table {{.Names}}\\t{{.Status}}\\t{{.Ports}}' | rg -n "trading-ops-postgres|5432"`
    - `docker compose -f docker/compose.yml --env-file config/secrets.env ps`
    - `make health`
    - `make psql`
  - Verification:
    - Postgres container is `healthy`.
    - `make health` reports accepting connections; `make psql` returns `1`.
  - Artifacts:
    - Running container: `trading-ops-postgres`

- [x] **M1.3 /data directories exist for Postgres + backups + Qlib**
  - Objective: Ensure host persistence paths exist before any builds/runs.
  - Commands:
    - `sudo mkdir -p /data/qlib /data/artifacts/trading-ops/qlib-shadow`
    - `sudo chown -R "$(id -u)":"$(id -g)" /data/qlib /data/artifacts/trading-ops`
    - `ls -la /data/qlib /data/artifacts/trading-ops/qlib-shadow`
  - Verification:
    - Directories exist and are writable by the operator user.
  - Artifacts:
    - `/data/qlib/`
    - `/data/artifacts/trading-ops/qlib-shadow/`

---

## M2 — Qlib docker runner (build image, install deps, import qlib)

- [x] **M2.1 Implement `qlib-runner` container (Dockerfile + compose service)**
  - Objective: Create a reproducible container that can run `python -m qlib.cli.data` and `qrun`.
  - Commands:
    - `ls -la docs/QLIB_DOCKER_EXECUTION_SPEC.md`
    - `test -f docker/qlib-runner/Dockerfile`
    - `rg -n "qlib-runner" docker/compose.yml`
  - Verification:
    - `docker/qlib-runner/Dockerfile` exists and `docker/compose.yml` contains `qlib-runner` service definition per spec.
  - Artifacts:
    - `docker/qlib-runner/Dockerfile`
    - `docker/compose.yml` updated

- [x] **M2.2 Build qlib-runner image**
  - Objective: Build the local Qlib runner image.
  - Commands:
    - `docker compose -f docker/compose.yml --env-file config/secrets.env build qlib-runner`
  - Verification:
    - Build succeeds; image `trading-ops/qlib-runner:local` exists.
  - Artifacts:
    - Local Docker image: `trading-ops/qlib-runner:local`

- [x] **M2.3 Qlib import + qrun available in container**
  - Objective: Ensure the container can import Qlib and run `qrun`.
  - Commands:
    - `docker compose -f docker/compose.yml --env-file config/secrets.env run --rm qlib-runner python -c "import qlib; print('qlib_import_ok')"`
    - `docker compose -f docker/compose.yml --env-file config/secrets.env run --rm qlib-runner qrun --help | head -n 40`
  - Verification:
    - Prints `qlib_import_ok`.
    - `qrun --help` prints usage (command exists).
  - Artifacts:
    - None.

---

## M3 — Qlib dataset (download US 1d in container, persist on /data, verify)

- [x] **M3.1 Download US 1d dataset in container to `/data/qlib/...`**
  - Objective: Acquire Qlib’s US 1d dataset in the canonical docker runtime.
  - Commands:
    - `docker compose -f docker/compose.yml --env-file config/secrets.env run --rm qlib-runner python -m qlib.cli.data qlib_data --region us --interval 1d --target_dir /data/qlib/qlib_data/us_data_1d`
  - Verification:
    - Command exits 0 and dataset directory is populated.
  - Artifacts:
    - `/data/qlib/qlib_data/us_data_1d/`

- [x] **M3.2 Verify dataset structure + instruments universe (no guessing)**
  - Objective: Confirm required subfolders exist and discover valid instrument universes.
  - Commands:
    - `docker compose -f docker/compose.yml --env-file config/secrets.env run --rm qlib-runner bash -lc "ls -la /data/qlib/qlib_data/us_data_1d | head -n 50"`
    - `docker compose -f docker/compose.yml --env-file config/secrets.env run --rm qlib-runner bash -lc "ls -la /data/qlib/qlib_data/us_data_1d/instruments | head -n 200"`
  - Verification:
    - `calendars/`, `features/`, `instruments/` exist.
    - At least one instruments file exists to use for `market`/`instruments` in the workflow YAML.
  - Artifacts:
    - Dataset folders under `/data/qlib/qlib_data/us_data_1d/`

---

## M4 — Qlib baseline `qrun` (LightGBM YAML, run, produce artifacts on /data)

- [x] **M4.1 Create shadow-mode workflow YAML for US dataset**
  - Objective: Create a baseline workflow config derived from Qlib’s LightGBM Alpha158 example, adapted to the downloaded US dataset.
  - Commands:
    - `test -f config/qlib_shadow/workflow_us_lightgbm_alpha158_shadow.yaml`
    - `rg -n "provider_uri|region|market|benchmark|instruments" config/qlib_shadow/workflow_us_lightgbm_alpha158_shadow.yaml`
  - Verification:
    - `provider_uri` points to `/data/qlib/qlib_data/us_data_1d` and `region: us`.
    - `market`/`benchmark`/`instruments` match what exists in `/data/qlib/qlib_data/us_data_1d/instruments`.
  - Artifacts:
    - `config/qlib_shadow/workflow_us_lightgbm_alpha158_shadow.yaml`

- [x] **M4.2 Run a “golden” `qrun` in container and persist artifacts under `/data/.../qlib-shadow/<run_id>/`**
  - Objective: Produce a successful baseline backtest + recorder outputs in shadow mode.
  - Commands:
    - `bash -lc 'set -euo pipefail; run_id="$(date -u +%Y%m%d-%H%M%SZ)-git$(git rev-parse --short HEAD)"; out="/data/artifacts/trading-ops/qlib-shadow/${run_id}"; docker compose -f docker/compose.yml --env-file config/secrets.env run --rm qlib-runner bash -lc "set -euo pipefail; mkdir -p \"${out}\"; cp /repo/config/qlib_shadow/workflow_us_lightgbm_alpha158_shadow.yaml \"${out}/workflow.yaml\"; cd /work; qrun /repo/config/qlib_shadow/workflow_us_lightgbm_alpha158_shadow.yaml --experiment_name \"${run_id}\" --uri_folder \"${out}/mlruns\" >\"${out}/stdout.log\" 2>\"${out}/stderr.log\""; echo \"run_id=${run_id}\"; ls -la \"${out}\" | head -n 50'`
  - Verification:
    - `stdout.log` exists and includes Qlib analysis output.
    - `mlruns/` exists under the run folder and is non-empty.
  - Artifacts:
    - `/data/artifacts/trading-ops/qlib-shadow/<run_id>/...`

- [x] **M4.3 Export ranked signals from Qlib recorder artifacts**
  - Objective: Extract model outputs into a portable ranked signals file (shadow artifact only).
  - Commands:
    - `test -f scripts/qlib_export_signals.py`
    - `docker compose -f docker/compose.yml --env-file config/secrets.env run --rm qlib-runner python /repo/scripts/qlib_export_signals.py --mlruns /data/artifacts/trading-ops/qlib-shadow/<run_id>/mlruns --out /data/artifacts/trading-ops/qlib-shadow/<run_id>/signals_ranked.parquet`
  - Verification:
    - `signals_ranked.parquet` (or `.csv`) exists under the run folder and is non-empty.
  - Artifacts:
    - `/data/artifacts/trading-ops/qlib-shadow/<run_id>/signals_ranked.parquet`

- [x] **M4.4 Write minimal backtest summary markdown**
  - Objective: Produce a human-readable summary with key metrics and artifact locations.
  - Commands:
    - `test -f /data/artifacts/trading-ops/qlib-shadow/<run_id>/backtest_summary.md`
  - Verification:
    - File exists and references the run_id and paths for `mlruns/` and `signals_ranked.*`.
  - Artifacts:
    - `/data/artifacts/trading-ops/qlib-shadow/<run_id>/backtest_summary.md`

---

## M5 — Integrate with Postgres (later; do not block M2–M4)

- [x] **M5.1 Define `signals_ranked` ingest contract (schema + run metadata)**
  - Objective: Specify how Qlib shadow outputs will be ingested into Postgres only after gates exist.
  - Commands:
    - `rg -n "signals_ranked" db/migrations/*.sql`
    - `test -f docs/QLIB_DOCKER_EXECUTION_SPEC.md`
  - Verification:
    - Contract is documented; no changes required to run Qlib in docker.
  - Artifacts:
    - Updated docs (as needed).

---

## M6 — Deterministic gates + stub signals + riskguard

- [x] M6.0a EOD ingestion writes adj_close for 100% rows
  - Objective: Ensure `market_prices_eod.adj_close` is never empty for ingested providers (v1 uses synthetic close).
  - Commands:
      - make fetch-eod
      - docker compose -f docker/compose.yml --env-file config/secrets.env exec -T postgres \
        psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -v ON_ERROR_STOP=1 -tA -c \
        "select count(*) from market_prices_eod where adj_close is null;"
      - docker compose -f docker/compose.yml --env-file config/secrets.env exec -T postgres \
        psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -v ON_ERROR_STOP=1 -tA -c \
        "select quality_flags->>'adj_close' as adj_flag, count(*) from market_prices_eod group by 1 order by 2 desc;"
  - Verification:
      - First query returns `0`
      - Second query shows `synthetic_close` for Stooq-loaded rows
  - Artifacts:
      - Raw snapshots under /data/trading-ops/data/raw/stooq/...

- [x] M6.0b Universe fetch query is not over-inclusive
  - Objective: Fetch only enabled symbols + explicit benchmarks/index (not every disabled non-stock).
  - Commands:
      - python scripts/market_fetch_eod.py --max-rows 5
  - Verification:
      - Output does not include unexpected disabled non-stock symbols (spot-check list vs config_universe)
  - Artifacts:
      - Console output + DB rows in market_prices_eod

- [x] **M6.1 Deterministic data-quality gate blocks trading**
  - Objective: Enforce “NO DATA / NO TRADE” with stored report artifacts.
  - Commands:
    - `make data-quality`
  - Verification:
    - Prints `DATA_QUALITY_PASS` or `DATA_QUALITY_FAIL` and writes a report under `/data/trading-ops/artifacts/reports/`.
  - Artifacts:
    - `/data/trading-ops/artifacts/reports/data_quality_*.md`

- [x] **M6.2 Deterministic riskguard blocks without trade-builder (LLM cannot approve)**
  - Objective: Ensure riskguard remains the final deterministic authority.
  - Commands:
    - `make riskguard`
  - Verification:
    - Until trade-builder is implemented, prints `RISKGUARD_BLOCKED` and writes `/data/trading-ops/artifacts/runs/<run_id>/no_trade.json`.
  - Artifacts:
    - `/data/trading-ops/artifacts/runs/<run_id>/no_trade.json`

---

## M7 — Ticketing + confirmations loop (manual eToro execution only)

 - [x] **M7.1 Deterministic ticket renderer (Markdown + JSON)**
  - Objective: Convert deterministic run artifacts into an unambiguous human ticket (NO-TRADE included), and persist ticket to Postgres.
  - Commands:
    - `cat docs/PM_STATE.md`
    - `make ticket` (defaults to `LAST_RUN_ID` from `docs/PM_STATE.md`)
    - `make ticket RUN_ID=<run_id>` (optional override)
    - `ls -la /data/trading-ops/artifacts/tickets | tail -n 30`
    - `set -a; source config/secrets.env; set +a; docker compose -f docker/compose.yml --env-file config/secrets.env exec -T postgres \
      psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -v ON_ERROR_STOP=1 -c \
      "select ticket_id, run_id, status, created_at from tickets order by created_at desc limit 5;"`
  - Verification:
    - `make ticket` prints `ticket_id=...` and writes:
      - `/data/trading-ops/artifacts/tickets/<ticket_id>/ticket.md`
      - `/data/trading-ops/artifacts/tickets/<ticket_id>/ticket.json`
    - Postgres shows a `tickets` row for the `run_id` with `status` in (`TRADE`,`NO_TRADE`).
  - Artifacts:
    - `/data/trading-ops/artifacts/tickets/<ticket_id>/ticket.md`
    - `/data/trading-ops/artifacts/tickets/<ticket_id>/ticket.json`

- [x] **M7.2 Confirmation capture persists fills and reconciles ledger**
  - Objective: Close the loop: intended → executed → ledger truth.
  - Commands:
    - `cat docs/PM_STATE.md`
    - `make confirm` (submits a `NO_TRADE` acknowledgement for `LAST_TICKET_ID`)
    - One-time ledger bootstrap (if missing):
      - `make ledger-baseline CASH_GBP=<available_cash_gbp>`
    - Reconciliation snapshot + gate:
      - `make reconcile-add -- --snapshot-date <asof_date> --cash-gbp <available_cash_gbp>`
      - `make reconcile-run`
    - Verify DB rows:
      - `set -a; source config/secrets.env; set +a; docker compose -f docker/compose.yml --env-file config/secrets.env exec -T postgres \
        psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -v ON_ERROR_STOP=1 -c \
        "select confirmation_id, ticket_id, submitted_by, submitted_at from confirmations order by submitted_at desc limit 5;"`
      - `set -a; source config/secrets.env; set +a; docker compose -f docker/compose.yml --env-file config/secrets.env exec -T postgres \
        psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -v ON_ERROR_STOP=1 -c \
        "select passed, evaluated_at, report_path from reconciliation_results order by evaluated_at desc limit 5;"`
  - Verification:
    - Confirmation artifact exists under `/data/trading-ops/artifacts/tickets/<ticket_id>/confirmations/<confirmation_uuid>/`.
    - Postgres has a new `confirmations` row for the ticket.
    - `make reconcile-run` prints `RECONCILIATION_PASS` and writes `/data/trading-ops/artifacts/reports/reconcile_*.md`.
  - Artifacts:
    - `/data/trading-ops/artifacts/tickets/<ticket_id>/confirmations/<confirmation_uuid>/confirmation.json`
    - `/data/trading-ops/artifacts/tickets/<ticket_id>/confirmations/<confirmation_uuid>/confirmation.md`
    - `/data/trading-ops/artifacts/reports/reconcile_*.md`

---

## M8 — Reporting / alerts

- [x] **M8.1 Daily report (even on NO-TRADE)**
  - Objective: Fully automate 08:00/14:00 UK daily ops with artifacts + DB rows, including daily operator reports (NO-TRADE included).
  - Commands:
    - `make run-0800`
    - `make run-1400`
    - `docker compose -f docker/compose.yml --env-file config/secrets.env ps`
    - `ls -la /data/trading-ops/artifacts/runs | tail -n 20`
    - `ls -la /data/trading-ops/artifacts/reports | tail -n 20`
    - Query latest runs/tickets:
      - `set -a; source config/secrets.env; set +a; docker compose -f docker/compose.yml --env-file config/secrets.env exec -T postgres \
        psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -v ON_ERROR_STOP=1 -c \
        "select run_id, created_at, status from runs order by created_at desc limit 10;"`
      - `set -a; source config/secrets.env; set +a; docker compose -f docker/compose.yml --env-file config/secrets.env exec -T postgres \
        psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -v ON_ERROR_STOP=1 -c \
        "select ticket_id, run_id, status, created_at from tickets order by created_at desc limit 10;"`
    - Start scheduler:
      - `docker compose -f docker/compose.yml --env-file config/secrets.env up -d scheduler`
  - Verification:
    - `make run-0800` writes `/data/trading-ops/artifacts/runs/<run_id>/run_summary.md` and `/data/trading-ops/artifacts/reports/daily_0800_*.md`.
    - `make run-1400` writes `/data/trading-ops/artifacts/runs/<run_id>/run_summary.md`, generates a ticket, and writes `/data/trading-ops/artifacts/reports/daily_1400_*.md`.
    - `scheduler` container is running and will execute at 08:00 and 14:00 Europe/London without refetching at 14:00.
  - Artifacts:
    - `/data/trading-ops/artifacts/reports/daily_*.md`
    - `/data/trading-ops/artifacts/logs/scheduler/*.log`

- [x] **M8.2 File-only alerts wiring (deterministic)**
  - Objective: Emit deterministic alert artifacts (file sink) and record alerts in Postgres for key failure/block events.
  - Commands:
    - `make run-0800` (expect: if `DATA_QUALITY_FAIL` then alert)
    - `make run-1400` (expect: `RISKGUARD_BLOCKED` alert)
    - `ls -la /data/trading-ops/artifacts/alerts | tail -n 50`
    - `find /data/trading-ops/artifacts/alerts -maxdepth 2 -name alert.md -type f | tail -n 5`
    - `tail -n 80 "$(find /data/trading-ops/artifacts/alerts -maxdepth 2 -name alert.md -type f | tail -n 1)"`
    - `make alerts-last`
  - Verification:
    - At least two alert folders exist under `/data/trading-ops/artifacts/alerts/<alert_id>/` with `alert.md` + `alert.json`.
    - `make alerts-last` shows recent alerts in Postgres.
  - Artifacts:
    - `/data/trading-ops/artifacts/alerts/<alert_id>/alert.json`
    - `/data/trading-ops/artifacts/alerts/<alert_id>/alert.md`

- [x] **M8.3 Secondary sink abstraction + dry-run (no network delivery yet)**
  - Objective: Keep file-only as mandatory primary sink, and add optional secondary sink config + dry-run receipts without sending.
  - Commands:
    - `make run-1400` (with `ALERT_SECONDARY_SINK=none` → delivery `SKIPPED`)
    - Set `ALERT_SECONDARY_SINK=slack` and `ALERT_SECONDARY_DRYRUN=true`, then:
      - `make run-1400` (delivery `WOULD_SEND`)
    - `find /data/trading-ops/artifacts/alerts -maxdepth 2 -name delivery.md -type f | tail -n 5`
    - `set -a; source config/secrets.env; set +a; docker compose -f docker/compose.yml --env-file config/secrets.env exec -T postgres \
      psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -v ON_ERROR_STOP=1 -c \
      "select created_at, alert_id, sink, dryrun, status, coalesce(error_text,'') from alert_deliveries order by created_at desc limit 20;"`
  - Verification:
    - `delivery.md` exists for new alerts and DB contains `alert_deliveries` rows with `SKIPPED` (none) and `WOULD_SEND` (dry-run).
  - Artifacts:
    - `/data/trading-ops/artifacts/alerts/<alert_id>/delivery.json`
    - `/data/trading-ops/artifacts/alerts/<alert_id>/delivery.md`

---

## M9 — Optional RD-Agent integration + future MCPs

- [x] **M9.1 RD-Agent policy + runbook (ADVISORY/DEV-ONLY)**
  - Objective: Document strict boundaries so RD-Agent can accelerate engineering without any decision/trade authority.
  - Commands:
    - `ls -la docs/RD_AGENT_POLICY.md docs/RD_AGENT_RUNBOOK.md`
  - Verification:
    - Both docs exist and explicitly state allowed vs disallowed actions.
  - Artifacts:
    - `docs/RD_AGENT_POLICY.md`
    - `docs/RD_AGENT_RUNBOOK.md`

- [x] **M9.2 Run RD-Agent in dry-run repo audit mode (no changes applied)**
  - Objective: Prove RD-Agent can analyze and propose without modifying repo state.
  - Commands:
    - Install RD-Agent into a dedicated `/data` venv:
      - `sudo mkdir -p /data/trading-ops/venvs && sudo chown "$USER:$USER" /data/trading-ops/venvs`
      - `python3 -m venv /data/trading-ops/venvs/rdagent`
      - `source /data/trading-ops/venvs/rdagent/bin/activate && pip install -U pip && pip install rdagent`
    - Run audit-only (write artifacts only; no repo changes applied):
      - `RD_AGENT_RUN_ID="$(date -u +%Y%m%d-%H%M%SZ)-rd-audit-git$(git rev-parse --short HEAD 2>/dev/null || echo nogit)"`
      - `mkdir -p "/data/trading-ops/artifacts/rd-agent/$RD_AGENT_RUN_ID"`
      - `git status --porcelain=v1`
      - Run RD-Agent in an isolated copy (`git worktree` or `/tmp` rsync) and write:
        - `/data/trading-ops/artifacts/rd-agent/$RD_AGENT_RUN_ID/outputs.md`
        - `/data/trading-ops/artifacts/rd-agent/$RD_AGENT_RUN_ID/patch.diff` (optional)
  - Verification:
    - RD-Agent produces audit artifacts under `/data/trading-ops/artifacts/rd-agent/<run_id>/` and no repo files change.
    - `git status --porcelain=v1` remains clean (or only expected local-only files).
  - Artifacts:
    - `/data/trading-ops/artifacts/rd-agent/<run_id>/outputs.md`
    - `/data/trading-ops/artifacts/rd-agent/<run_id>/patch.diff` (optional)
- [x] **M9.3 RD-Agent audit pack runner (propose patch; no repo writes)**
  - Objective: Provide a repeatable “audit pack” command that can propose changes as a diff, but never modifies the repo.
  - Commands:
    - Run audit pack (writes artifacts under `/data` and prints the OUT dir):
      - `bash scripts/rd_agent_audit.sh`
    - Optional overrides:
      - `RD_AGENT_MODEL=gpt-4o-mini bash scripts/rd_agent_audit.sh`
      - `RD_AGENT_MAX_TOKENS=1100 bash scripts/rd_agent_audit.sh`
  - Verification:
    - Script prints `OUT=...` and `RD_AGENT_RUN_ID=...`.
    - `outputs.md` and `VERIFY.md` exist under the printed `OUT`.
    - `OUT/VERIFY.md` shows empty `git status --porcelain=v1` for both main repo and worktree (before/after).
  - Artifacts:
    - `/data/trading-ops/artifacts/rd-agent/<run_id>/outputs.md`
    - `/data/trading-ops/artifacts/rd-agent/<run_id>/run.log`
    - `/data/trading-ops/artifacts/rd-agent/<run_id>/VERIFY.md`
    - `/data/trading-ops/artifacts/rd-agent/<run_id>/patch.diff` (optional)
    - `docs/RD_AGENT_AUDIT_ONESHOT.md`
    - `docs/PM_LOG.md` entry with run_id + OUT path

### Future (not built in M9.1)

- **Future MCPs (optional):**
  - `mcp-repo` (safe file ops abstraction)
  - `mcp-ci` (run make targets + capture artifacts)
  - `mcp-db-readonly` (read-only queries for reporting; no writes)

---

## M12 — Production daily ops hardening (deterministic, file-first)

- [x] **M12.1 One-command daily reconciliation helper + operator steps**
  - Objective: Make reconciliation fast and repeatable; make it hard to accidentally skip.
  - Commands:
    - `make reconcile-daily -- --snapshot-date YYYY-MM-DD --cash-gbp <cash> --position SYMBOL=UNITS --notes "manual_etoro_snapshot"`
    - `make reconcile-selftest` (optional proof of PASS/FAIL behavior)
  - Verification:
    - Command prints `RECONCILIATION_PASS` and writes a report under `/data/trading-ops/artifacts/reports/`.
    - Runbook/SOP clearly define daily operator flow (08:00 → reconcile → 14:00 → confirm).
  - Artifacts:
    - `/data/trading-ops/artifacts/reports/reconcile_<snapshot_date>_*.md`
    - Updated `docs/RECONCILIATION_SOP.md`, `docs/RUNBOOK.md`

- [x] **M12.2 Ticket final polish + stable schema-safe queries**
  - Objective: Ensure ticket format is stable and operator-friendly; all DB read queries remain schema-safe.
  - Commands:
    - `make run-1400 && make tickets-last`
    - `DRYRUN_TRADES=true make run-1400 && make tickets-last`
  - Verification:
    - Ticket rendering is deterministic (ordering/formatting stable across re-runs for same inputs).
    - Queries avoid `select *` and do not assume optional columns exist.
  - Artifacts:
    - `/data/trading-ops/artifacts/tickets/<ticket_id>/ticket.md`

- [x] **M12.3 Scheduler validation + log retention (file-only)**
  - Objective: Validate scheduled 08:00/14:00 runs are operating and keep logs/artifacts bounded on disk.
  - Commands:
    - `docker ps`
    - `docker logs --tail 200 trading-ops-scheduler`
  - Verification:
    - Scheduler is running and emitting expected cadence logs.
    - Retention mechanism exists (file-only) and is documented (what it deletes, what it keeps).

- [x] **M12.4.a Ticket determinism polish + material hash**
  - Objective: Ensure ticket material content is stable and auditable; add a deterministic material hash that ignores volatile timestamps.
  - Commands:
    - `DRYRUN_TRADES=true make run-1400 && make tickets-last`
    - Confirm `material_hash` exists: `/data/trading-ops/artifacts/tickets/<ticket_id>/material_hash.txt`
  - Verification:
    - Two back-to-back TRADE tickets show no ordering/format drift (ignoring run/ticket ids, pointers, and timestamps).
    - Two consecutive DRYRUN TRADE runs with identical intended trades produce the same `meta.material_hash` (economic-only; ignores ids/paths/timestamps).
    - `ticket.json` includes `meta.material_hash` and `ticket.md` displays it.
  - Artifacts:
    - `/data/trading-ops/artifacts/tickets/<ticket_id>/material_hash.txt`

- [ ] **M12.4.b End-to-end “day simulation” script**
  - Objective: One deterministic command to simulate a full day: 08:00 fetch → reconcile → 14:00 ticket → confirm → reconcile report.
  - Commands:
    - `bash scripts/day_simulate.sh --date YYYY-MM-DD --dryrun-trades`
  - Verification:
    - Script produces a single folder under `/data/trading-ops/artifacts/` containing run ids + ticket ids + confirmation + final reconcile report.
