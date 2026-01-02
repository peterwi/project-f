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

- [ ] **M1.1 Enable Docker access in this session (required for all docker-first work)**
  - Objective: Ensure this orchestrator can run Docker commands (needed for Qlib runner + Compose changes).
  - Commands:
    - `docker ps`
    - `docker compose version`
  - Verification:
    - Commands succeed (no “permission denied” against `/var/run/docker.sock`).
  - Artifacts:
    - None.

- [ ] **M1.2 Postgres verified (running + healthy + bound to 127.0.0.1:5432)**
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

- [ ] **M1.3 /data directories exist for Postgres + backups + Qlib**
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

- [ ] **M2.1 Implement `qlib-runner` container (Dockerfile + compose service)**
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

- [ ] **M2.2 Build qlib-runner image**
  - Objective: Build the local Qlib runner image.
  - Commands:
    - `docker compose -f docker/compose.yml build qlib-runner`
  - Verification:
    - Build succeeds; image `trading-ops/qlib-runner:local` exists.
  - Artifacts:
    - Local Docker image: `trading-ops/qlib-runner:local`

- [ ] **M2.3 Qlib import + qrun available in container**
  - Objective: Ensure the container can import Qlib and run `qrun`.
  - Commands:
    - `docker compose -f docker/compose.yml run --rm qlib-runner python -c "import qlib; print('qlib_import_ok')"`
    - `docker compose -f docker/compose.yml run --rm qlib-runner qrun --help | head -n 40`
  - Verification:
    - Prints `qlib_import_ok`.
    - `qrun --help` prints usage (command exists).
  - Artifacts:
    - None.

---

## M3 — Qlib dataset (download US 1d in container, persist on /data, verify)

- [ ] **M3.1 Download US 1d dataset in container to `/data/qlib/...`**
  - Objective: Acquire Qlib’s US 1d dataset in the canonical docker runtime.
  - Commands:
    - `docker compose -f docker/compose.yml run --rm qlib-runner python -m qlib.cli.data qlib_data --region us --interval 1d --target_dir /data/qlib/qlib_data/us_data_1d`
  - Verification:
    - Command exits 0 and dataset directory is populated.
  - Artifacts:
    - `/data/qlib/qlib_data/us_data_1d/`

- [ ] **M3.2 Verify dataset structure + instruments universe (no guessing)**
  - Objective: Confirm required subfolders exist and discover valid instrument universes.
  - Commands:
    - `docker compose -f docker/compose.yml run --rm qlib-runner bash -lc "ls -la /data/qlib/qlib_data/us_data_1d | head -n 50"`
    - `docker compose -f docker/compose.yml run --rm qlib-runner bash -lc "ls -la /data/qlib/qlib_data/us_data_1d/instruments | head -n 200"`
  - Verification:
    - `calendars/`, `features/`, `instruments/` exist.
    - At least one instruments file exists to use for `market`/`instruments` in the workflow YAML.
  - Artifacts:
    - Dataset folders under `/data/qlib/qlib_data/us_data_1d/`

---

## M4 — Qlib baseline `qrun` (LightGBM YAML, run, produce artifacts on /data)

- [ ] **M4.1 Create shadow-mode workflow YAML for US dataset**
  - Objective: Create a baseline workflow config derived from Qlib’s LightGBM Alpha158 example, adapted to the downloaded US dataset.
  - Commands:
    - `test -f config/qlib_shadow/workflow_us_lightgbm_alpha158_shadow.yaml`
    - `rg -n "provider_uri|region|market|benchmark|instruments" config/qlib_shadow/workflow_us_lightgbm_alpha158_shadow.yaml`
  - Verification:
    - `provider_uri` points to `/data/qlib/qlib_data/us_data_1d` and `region: us`.
    - `market`/`benchmark`/`instruments` match what exists in `/data/qlib/qlib_data/us_data_1d/instruments`.
  - Artifacts:
    - `config/qlib_shadow/workflow_us_lightgbm_alpha158_shadow.yaml`

- [ ] **M4.2 Run a “golden” `qrun` in container and persist artifacts under `/data/.../qlib-shadow/<run_id>/`**
  - Objective: Produce a successful baseline backtest + recorder outputs in shadow mode.
  - Commands:
    - `bash -lc 'run_id="$(date -u +%Y%m%d-%H%M%SZ)-git$(git rev-parse --short HEAD)"; echo "$run_id"'`
    - `docker compose -f docker/compose.yml run --rm qlib-runner bash -lc "set -euo pipefail; run_id=$run_id; out=/data/artifacts/trading-ops/qlib-shadow/$run_id; mkdir -p $out; cp /repo/config/qlib_shadow/workflow_us_lightgbm_alpha158_shadow.yaml $out/workflow.yaml; qrun /repo/config/qlib_shadow/workflow_us_lightgbm_alpha158_shadow.yaml --experiment_name $run_id --uri_folder $out/mlruns >$out/stdout.log 2>$out/stderr.log"`
    - `ls -la /data/artifacts/trading-ops/qlib-shadow/$run_id | head -n 50`
  - Verification:
    - `stdout.log` exists and includes Qlib analysis output.
    - `mlruns/` exists under the run folder and is non-empty.
  - Artifacts:
    - `/data/artifacts/trading-ops/qlib-shadow/<run_id>/...`

- [ ] **M4.3 Export ranked signals from Qlib recorder artifacts**
  - Objective: Extract model outputs into a portable ranked signals file (shadow artifact only).
  - Commands:
    - `test -f scripts/qlib_export_signals.py`
    - `docker compose -f docker/compose.yml run --rm qlib-runner python /repo/scripts/qlib_export_signals.py --mlruns /data/artifacts/trading-ops/qlib-shadow/<run_id>/mlruns --out /data/artifacts/trading-ops/qlib-shadow/<run_id>/signals_ranked.parquet`
  - Verification:
    - `signals_ranked.parquet` (or `.csv`) exists under the run folder and is non-empty.
  - Artifacts:
    - `/data/artifacts/trading-ops/qlib-shadow/<run_id>/signals_ranked.parquet`

- [ ] **M4.4 Write minimal backtest summary markdown**
  - Objective: Produce a human-readable summary with key metrics and artifact locations.
  - Commands:
    - `test -f /data/artifacts/trading-ops/qlib-shadow/<run_id>/backtest_summary.md`
  - Verification:
    - File exists and references the run_id and paths for `mlruns/` and `signals_ranked.*`.
  - Artifacts:
    - `/data/artifacts/trading-ops/qlib-shadow/<run_id>/backtest_summary.md`

---

## M5 — Integrate with Postgres (later; do not block M2–M4)

- [ ] **M5.1 Define `signals_ranked` ingest contract (schema + run metadata)**
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

- [ ] **M6.1 Deterministic data-quality gate blocks trading**
  - Objective: Enforce “NO DATA / NO TRADE” with stored report artifacts.
  - Commands:
    - `make data-quality`
  - Verification:
    - Prints `DATA_QUALITY_PASS` or `DATA_QUALITY_FAIL` and writes a report under `/data/trading-ops/artifacts/reports/`.
  - Artifacts:
    - `/data/trading-ops/artifacts/reports/data_quality_*.md`

- [ ] **M6.2 Deterministic riskguard blocks without trade-builder (LLM cannot approve)**
  - Objective: Ensure riskguard remains the final deterministic authority.
  - Commands:
    - `make riskguard`
  - Verification:
    - Until trade-builder is implemented, prints `RISKGUARD_BLOCKED` and writes `/data/trading-ops/artifacts/runs/<run_id>/no_trade.json`.
  - Artifacts:
    - `/data/trading-ops/artifacts/runs/<run_id>/no_trade.json`

---

## M7 — Ticketing + confirmations loop (manual eToro execution only)

- [ ] **M7.1 Deterministic ticket renderer (Markdown + JSON)**
  - Objective: Render unambiguous trade instructions and store as artifacts + DB rows.
  - Commands:
    - (to be implemented)
  - Verification:
    - Ticket artifacts exist under `/data/trading-ops/artifacts/tickets/`.
  - Artifacts:
    - `/data/trading-ops/artifacts/tickets/<ticket_id>.md`
    - `/data/trading-ops/artifacts/tickets/<ticket_id>.json`

- [ ] **M7.2 Confirmation capture persists fills and reconciles ledger**
  - Objective: Close the loop: intended → executed → ledger truth.
  - Commands:
    - (to be implemented)
  - Verification:
    - Confirmations stored; fills written; reconciliation gate reflects reality.
  - Artifacts:
    - DB rows + reconciliation reports.

---

## M8 — Reporting / alerts

- [ ] **M8.1 Daily report (even on NO-TRADE)**
  - Objective: Always produce an operator report with gate status and ledger snapshot.
  - Commands:
    - (to be implemented)
  - Verification:
    - `daily_*.md` exists under `/data/trading-ops/artifacts/reports/`.
  - Artifacts:
    - `/data/trading-ops/artifacts/reports/daily_*.md`

---

## M9 — Custom MCPs buildout (market-data, qlib, ledger, riskguard, ticketing, comms, reporting)

- [ ] **M9.1 Implement local MCP servers with strict boundaries**
  - Objective: Add MCP servers only after core deterministic loop is stable.
  - Commands:
    - (to be implemented)
  - Verification:
    - Each server is runnable locally and enforces allowlisted operations.
  - Artifacts:
    - `services/mcp-*/...`
