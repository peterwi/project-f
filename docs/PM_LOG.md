# PM Log (pause/resume journal)

This file is append-only. Each agent message appends a new entry so the project can resume with zero ambiguity.

---

## 2026-01-02T13:38:36Z

- Milestone / checklist: Planning + setup → next execution starts at `M0.1`
- What changed:
  - Created: `docs/PM_LOG.md`
  - Created: `docs/PM_STATE.md`
  - Previously created this session: `docs/QLIB_DOCKER_EXECUTION_SPEC.md`, `docs/CHECKLIST.md`
- Commands executed (high level):
  - `date -u +%Y-%m-%dT%H:%M:%SZ`
- Verification outputs (summary):
  - Timestamp captured: `2026-01-02T13:38:36Z`
- Result: PASS
  - Why: PM pause/resume logging is now in place; resume pointer exists.
- Next action:
  - Execute `M0.1`
- Blockers:
  - None recorded at this step; execution may reveal environment blockers (e.g., docker socket permissions).

## 2026-01-02T13:42:51Z

- Milestone / checklist: `M0` → completed `M0.1`, `M0.2`; next is `M1.1`
- What changed:
  - Updated: `.gitignore` (ignored `/artifacts/*` and local scratch files)
  - Removed (untracked): `agent-instructions-2026.txt`, `=1.24.0`, `artifacts/`
  - Removed embedded git dirs: `qlib/.git`, `python-sdk/.git` (to vendor contents for reproducible clones)
  - Created/updated: `docs/PM_LOG.md`, `docs/PM_STATE.md`, `docs/CHECKLIST.md`
  - Committed:
    - `da93ece` — baseline repo tracking
    - `cbabc8b` — vendor python-sdk contents (replaced gitlink)
- Commands executed (high level):
  - M0.1 verify: `git status`, `git ls-files | wc -l`, `git ls-files | sed -n ...`
  - Fix: `git add -A`, `git commit ...`, `git rm --cached python-sdk`, `git add python-sdk`, `git commit ...`
  - M0.2 verify: `git check-ignore -v config/secrets.env`, `rg -n "POSTGRES_PASSWORD=" ...`
- Verification outputs (summary):
  - `git ls-files | wc -l` is now `1083` (core files tracked)
  - `config/secrets.env` is ignored (via `.gitignore`)
  - No real secrets found outside `config/secrets.env` (only placeholders + checklist text)
- Result: PASS
  - Why: repo is now clonable/reproducible; secrets remain untracked; checklist updated to `[x]` for `M0.1` and `M0.2`.
- Next action:
  - Execute `M1.1`
- Blockers:
  - `docker ps` currently returns “permission denied … /var/run/docker.sock” for the agent session; resolve by approving escalated docker access (or ensuring this user is in the `docker` group for the harness).

## 2026-01-02T13:46:16Z

- Milestone / checklist: completed `M1.1`–`M1.3`; next is `M2.1`
- What changed:
  - Updated: `docs/CHECKLIST.md` (marked `M1.1`, `M1.2`, `M1.3` as DONE)
  - Updated: `docs/PM_STATE.md` (resume pointer advanced to `M2.1`)
- Commands executed (high level):
  - `docker ps` (required escalated docker socket access)
  - `docker compose version`
  - `make health`, `make psql` (required escalated docker socket access)
  - `sudo mkdir -p /data/qlib /data/artifacts/trading-ops/qlib-shadow`
  - `sudo chown -R <uid>:<gid> /data/qlib /data/artifacts/trading-ops`
  - `ls -la /data/qlib /data/artifacts/trading-ops/qlib-shadow`
- Verification outputs (summary):
  - Postgres container is healthy and reachable:
    - `make health` → `accepting connections`
    - `make psql` → `select 1` returns `1`
  - `/data` persistence paths created:
    - `/data/qlib/` exists and owned by `peter`
    - `/data/artifacts/trading-ops/qlib-shadow/` exists and owned by `peter`
- Result: PASS
  - Why: Docker + Postgres verified; required /data directories exist for docker-first Qlib work.
- Next action:
  - Execute `M2.1`
- Blockers:
  - Docker socket access still requires escalated execution for this agent session (non-escalated docker calls fail).

## 2026-01-02T13:48:01Z

- Milestone / checklist: completed `M2.1`; next is `M2.2`
- What changed:
  - Created: `docker/qlib-runner/Dockerfile`
  - Updated: `docker/compose.yml` (added `qlib-runner` service)
  - Updated: `docs/CHECKLIST.md` (marked `M2.1` as DONE)
  - Updated: `docs/PM_STATE.md` (resume pointer advanced to `M2.2`)
- Commands executed (high level):
  - `ls -la docs/QLIB_DOCKER_EXECUTION_SPEC.md`
  - `test -f docker/qlib-runner/Dockerfile`
  - `rg -n "qlib-runner" docker/compose.yml`
- Verification outputs (summary):
  - `docker/qlib-runner/Dockerfile` exists
  - `docker/compose.yml` contains `qlib-runner` service
- Result: PASS
  - Why: Qlib runner is now defined and ready to build.
- Next action:
  - Execute `M2.2` (build `qlib-runner` image)
- Blockers:
  - Docker build will require network access to fetch apt/pip deps (network is restricted; may require approval).

## 2026-01-02T13:59:18Z

- Milestone / checklist: completed `M2.2`, `M2.3`; next is `M3.1`
- What changed:
  - Updated: `docker/qlib-runner/Dockerfile` (fix Qlib install without upstream git metadata via `SETUPTOOLS_SCM_PRETEND_VERSION_FOR_PYQLIB`)
  - Updated: `docker/compose.yml` (removed `qlib-runner` entrypoint override so `docker compose run ... qlib-runner <cmd>` behaves normally)
  - Updated: `docs/CHECKLIST.md` (marked `M2.2` and `M2.3` as DONE; fixed compose commands to include `--env-file config/secrets.env`)
  - Updated: `docs/QLIB_DOCKER_EXECUTION_SPEC.md` (documented setuptools-scm requirement + explicit shell usage)
  - Updated: `docs/PM_STATE.md` (resume pointer advanced to `M3.1`)
- Commands executed (high level):
  - `docker compose ... build qlib-runner` (first failed due to missing `--env-file`; then succeeded)
  - `docker image ls trading-ops/qlib-runner:local`
  - `docker compose ... run --rm qlib-runner python -c "import qlib; ..."`
  - `docker compose ... run --rm qlib-runner qrun --help`
- Verification outputs (summary):
  - Image built: `trading-ops/qlib-runner:local` (size ~2.75GB)
  - Qlib import in container: prints `qlib_import_ok`
  - `qrun --help` prints CLI help (supports `--experiment_name` and `--uri_folder`)
- Result: PASS
  - Why: docker-first Qlib runner now builds and runs; baseline CLI entrypoints verified.
- Next action:
  - Execute `M3.1` (download US 1d dataset into `/data/qlib/...` inside container)
- Blockers:
  - Docker socket access still requires escalated execution for this agent session.

## 2026-01-02T14:03:53Z

- Milestone / checklist: completed `M3.1`, `M3.2`; next is `M4.1`
- What changed:
  - Updated: `docs/CHECKLIST.md` (marked `M3.1` and `M3.2` as DONE)
  - Updated: `docs/PM_STATE.md` (resume pointer advanced to `M4.1`)
- Commands executed (high level):
  - `docker compose ... run --rm qlib-runner python -m qlib.cli.data qlib_data --region us --interval 1d --target_dir /data/qlib/qlib_data/us_data_1d`
  - `ls -la /data/qlib/qlib_data/us_data_1d | head -n 50`
  - `docker compose ... run --rm qlib-runner bash -lc "ls -la ..."`
  - `docker compose ... run --rm qlib-runner bash -lc "ls -la .../instruments ..."`
  - Benchmark discovery: `grep -n '^SPY\\b' .../instruments/all.txt`
- Verification outputs (summary):
  - Dataset created on host: `/data/qlib/qlib_data/us_data_1d/`
  - Structure present: `calendars/`, `features/`, `instruments/`, and downloaded zip
  - Available universes: `all.txt`, `nasdaq100.txt`, `sp500.txt`
  - Benchmark symbol confirmed present in `all.txt`: `SPY` (date range shown in file)
- Result: PASS
  - Why: US dataset is downloaded and persistent; valid instrument universes discovered for workflow config.
- Next action:
  - Execute `M4.1` (create `config/qlib_shadow/workflow_us_lightgbm_alpha158_shadow.yaml`)
- Blockers:
  - Docker socket access still requires escalated execution for this agent session.

## 2026-01-02T14:29:10Z

- Milestone / checklist: completed `M4.1`–`M4.4` (baseline qrun + signals export + summary); next is `M5.1`
- What changed:
  - Updated: `config/qlib_shadow/workflow_us_lightgbm_alpha158_shadow.yaml` (US dataset config; fixed end_time to avoid backtest calendar OOB)
  - Created: `scripts/qlib_export_signals.py` (exports ranked signals from `mlruns/.../artifacts/pred.pkl`)
  - Created: `docker/qlib-runner/constraints.txt` and updated `docker/qlib-runner/Dockerfile` (pinned `mlflow<3` for Qlib compatibility)
  - Updated: `docs/CHECKLIST.md` (marked `M4.1`–`M4.4` as DONE)
  - Updated: `docs/PM_STATE.md` (set `LAST_RUN_ID=20260102-141853Z-git07e5377`, advanced to `M5.1`)
  - Wrote runtime artifacts under `/data`:
    - `/data/artifacts/trading-ops/qlib-shadow/20260102-141853Z-git07e5377/` (workflow snapshot, logs, mlruns, signals export)
    - `/data/artifacts/trading-ops/qlib-shadow/20260102-141853Z-git07e5377/backtest_summary.md`
- Commands executed (high level):
  - `qrun` baseline (initially failed due to MLflow version + end_time; fixed and re-ran successfully)
  - Signals export: `python /repo/scripts/qlib_export_signals.py --mlruns ... --out ...`
  - Dataset/calendar validation: `/data/qlib/qlib_data/us_data_1d/calendars/day.txt` tail check
- Verification outputs (summary):
  - Successful golden run folder exists: `/data/artifacts/trading-ops/qlib-shadow/20260102-141853Z-git07e5377/`
  - `stdout.log` contains Qlib analysis output; `mlruns/` is non-empty
  - Exported signals: `signals_ranked.parquet` exists (rows=`103`, asof_date=`2020-11-09`)
  - `backtest_summary.md` exists and references paths + key metrics
- Result: PASS
  - Why: baseline dockerized Qlib workflow now runs end-to-end in shadow mode with persistent artifacts and portable signals export.
- Next action:
  - Execute `M5.1`
- Blockers:
  - Docker socket access still requires escalated execution for this agent session.
