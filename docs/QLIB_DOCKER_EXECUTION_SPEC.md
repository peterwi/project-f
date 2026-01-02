# Qlib Docker Execution Spec (Shadow Mode)

## Purpose
Define the **docker-first** execution design for running Qlib in **shadow mode**:
- dataset download (US 1d)
- baseline `qrun` LightGBM Alpha158 workflow
- backtest + analysis artifacts
- ranked signals export

Non-negotiables:
- Qlib outputs are **shadow-only** until deterministic gates + ledger are implemented.
- No eToro automation.
- Reproducible runs: `run_id`, `git_commit`, config snapshot, logs, artifacts **persisted on `/data`**.

Authoritative references to align with (repo-local):
- Qlib Docker guidance + `qrun` workflow: `qlib/README.md` (“Docker images”, “Auto Quant Research Workflow”)
- Baseline workflow YAML example: `qlib/examples/benchmarks/LightGBM/workflow_config_lightgbm_Alpha158.yaml`
- Qlib runner behavior: `qlib/qlib/cli/run.py` (`uri_folder` controls `mlruns` location)

---

## Data & artifact paths (host ↔ container)

### Persistent dataset directory
- Host: `/data/qlib/`
- Container: `/data/qlib/`
- Dataset target: `/data/qlib/qlib_data/us_data_1d/`

Rationale: Qlib dataset must persist across container rebuilds and runs.

### Persistent artifacts directory (shadow mode)
- Host: `/data/artifacts/trading-ops/qlib-shadow/`
- Container: `/data/artifacts/trading-ops/qlib-shadow/`

Per-run folder:
- `/data/artifacts/trading-ops/qlib-shadow/<run_id>/`
  - `workflow.yaml` (the exact YAML used)
  - `stdout.log`, `stderr.log`
  - `mlruns/` (Qlib experiment store, forced via `uri_folder`)
  - `backtest_summary.md`
  - `signals_ranked.parquet` (or `.csv`)
  - `RUN_METADATA.json` (run_id, git_commit, config hash, timestamps, dataset path)

---

## docker-compose: `qlib-runner` service (exact definition)

Add this service to `docker/compose.yml` (alongside existing `postgres`).

```yaml
  qlib-runner:
    build:
      context: ..
      dockerfile: docker/qlib-runner/Dockerfile
    image: trading-ops/qlib-runner:local
    container_name: trading-ops-qlib-runner
    working_dir: /work
    # Run as the host user to avoid root-owned files under /data.
    # The Makefile will export UID/GID when running docker compose commands.
    user: "${UID:-1000}:${GID:-1000}"
    environment:
      TZ: ${TZ:-UTC}
    volumes:
      # Persistent Qlib datasets
      - "/data/qlib:/data/qlib"
      # Persistent Qlib shadow artifacts
      - "/data/artifacts/trading-ops/qlib-shadow:/data/artifacts/trading-ops/qlib-shadow"
      # Read-only repo mount for configs and version stamping (avoid running from repo root)
      - "..:/repo:ro"
    entrypoint: ["/bin/bash", "-lc"]
```

Notes:
- We mount the repo read-only at `/repo` and run with `working_dir=/work` to avoid Qlib’s “don’t run from a directory containing `qlib`” footgun.
- Postgres is intentionally **not** required for Qlib bootstrap (DB integration is a later milestone).

---

## Dockerfile: `docker/qlib-runner/Dockerfile` (design)

Goal: small, reproducible runner image that can:
- install Qlib from our vendored `qlib/` subtree (editable install not required in container)
- run `python -m qlib.cli.data ...` and `qrun ...`

High-level requirements:
- Python + build tooling for Qlib dependencies
- `pyqlib` installed from `./qlib`

Recommended approach (local build, no external base image dependency):
- Base: `python:3.12-slim` (aligns with known-working Python 3.12.3 on host venv)
- Install OS deps needed for common scientific wheels:
  - `build-essential`, `git`, `curl`, `libgomp1`
- `pip install -U pip wheel setuptools`
- `pip install -e /opt/qlib-src` (copy `qlib/` subtree into image)

We will implement this Dockerfile in Milestone `M2`.

---

## Dataset acquisition (US 1d) inside the container

Use Qlib’s supported CLI (preferred over repo-relative scripts):

```bash
python -m qlib.cli.data qlib_data \
  --region us \
  --interval 1d \
  --target_dir /data/qlib/qlib_data/us_data_1d
```

Verification inside the container:
- Dataset folder exists and is non-empty:
  - `/data/qlib/qlib_data/us_data_1d/calendars/`
  - `/data/qlib/qlib_data/us_data_1d/features/`
  - `/data/qlib/qlib_data/us_data_1d/instruments/`
- Instruments list discovery (no guessing):
  - `/data/qlib/qlib_data/us_data_1d/instruments/*.txt`

---

## Baseline workflow: `qrun` LightGBM Alpha158 (shadow)

### Workflow YAML source
Base example:
- `qlib/examples/benchmarks/LightGBM/workflow_config_lightgbm_Alpha158.yaml` (CN defaults)

We will create a US shadow config in-repo:
- `config/qlib_shadow/workflow_us_lightgbm_alpha158_shadow.yaml`

Required adaptations (no guessing):
- `qlib_init.provider_uri`: `/data/qlib/qlib_data/us_data_1d`
- `qlib_init.region`: `us`
- `market` / `instruments`: choose an instruments universe that **actually exists** in `/data/qlib/qlib_data/us_data_1d/instruments/` (e.g., `sp500`, `nasdaq100`, or `all` if present).
- `benchmark`: choose an instrument that exists in the dataset’s instruments universe (discover after download).
- Update time ranges to match dataset coverage (discover from calendar or simply start with a conservative recent range once we see available dates).

### Forcing Qlib experiment artifacts into the per-run folder
`qrun` ultimately calls `qlib/qlib/cli/run.py`, which sets:
- MLflow URI to `file:<cwd>/<uri_folder>` (unless overridden in config)

We will invoke `qrun` with:
- `--experiment_name <run_id>`
- `--uri_folder /data/artifacts/trading-ops/qlib-shadow/<run_id>/mlruns`

This guarantees:
- every run’s experiment artifacts are persisted on `/data`
- runs are isolated by `run_id`

---

## “Golden run” definition (acceptance criteria for Qlib docker runner)

One run is considered **PASS** when all of the following are true:
1. Dataset exists on host under:
   - `/data/qlib/qlib_data/us_data_1d/` (and is non-empty)
2. `qrun` completes successfully inside `qlib-runner` and writes:
   - `/data/artifacts/trading-ops/qlib-shadow/<run_id>/mlruns/` (non-empty)
3. We persist run artifacts:
   - `workflow.yaml`, `stdout.log`, `stderr.log`
   - `backtest_summary.md` (minimal summary + where artifacts live)
   - `signals_ranked.parquet` (or `.csv`) extracted from Qlib recorder outputs
4. Reproducibility stamp exists:
   - `/data/artifacts/trading-ops/qlib-shadow/<run_id>/RUN_METADATA.json` includes:
     - `run_id`, `git_commit`, `config_hash`, `dataset_path`, `started_at_utc`, `finished_at_utc`

Shadow-only guarantee:
- No database writes are required for the golden run.
- No ticketing, no execution, no trade decisions.

