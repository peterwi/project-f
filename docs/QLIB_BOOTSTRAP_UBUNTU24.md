# Qlib Bootstrap (Ubuntu 24.04) — SHADOW MODE (No trading)

This document is the dedicated bootstrap sub-plan required by `PLAN.md` to begin running Qlib safely in **shadow mode**.

Progress source of truth: `docs/IMPLEMENTATION_STATUS.md`

## Scope (SHADOW MODE, hard boundary)
- Qlib may **train / score / backtest** and write artifacts **ONLY**.
- Outputs MUST NOT feed any trade decision, tickets, or execution.
- No LLM may approve trades. Deterministic gates win (future milestone).
- No eToro automation. Manual execution only (per `docs/ETORO_CONSTRAINTS.md`).
- No Postgres writes at this milestone; outputs are filesystem artifacts only.

## Python environment (MANDATORY)
- Do **NOT** assume system Python versions.
- Use the existing venv at: `/home/peter/venvs/python-venv/`
- Python version is **frozen by that venv**.
- Qlib install must work **inside this venv**.
- No new system Python installs.

## Goal of bootstrap
Produce a reproducible baseline Qlib run on Ubuntu 24.04 that creates a run folder and artifacts:
- the exact YAML config used
- dataset path and dataset identifier
- ranked signals output (`signals_ranked.csv` or `.parquet`)
- backtest summary (`backtest_summary.md`)
- run logs (`stdout.log`, `stderr.log`)

All outputs go to: `artifacts/qlib-shadow/<run_id>/` (repo-local, not DB).

## run_id format (MANDATORY)
Use a deterministic, sortable run id:
- Format: `YYYYMMDD-HHMMSSZ-git<shortsha>`
- Example: `20251222-143015Z-git66b72b7`

---

## Step A — Confirm venv + install prerequisites (within venv only)
**Action:** Activate venv, confirm Python, then install Qlib into the venv from this repo.

Commands (to be executed later, in the execution step):
- `. /home/peter/venvs/python-venv/bin/activate`
- `python -V`
- `python -m pip install -U pip wheel setuptools`
- `python -m pip install -e ./qlib`

## Step B — Dataset acquisition (DEFAULT US; CN ONLY fallback)
**Default (required): US dataset**
- Download Qlib’s official dataset using the repo’s helper:
  - `python qlib/scripts/get_data.py qlib_data --region us --interval 1d --target_dir ~/.qlib/qlib_data/us_data_1d`

**Fallback ONLY if the US download fails:** CN dataset
- `python qlib/scripts/get_data.py qlib_data --region cn --interval 1d --target_dir ~/.qlib/qlib_data/cn_data_1d`

Notes:
- Dataset download requires network access during execution.
- If US download fails, we will record the exact failure in the run artifacts and then use CN to validate the tooling path.

## Step C — Baseline run config (shadow-mode YAML)
**Plan:** Start from an existing Qlib example YAML and create a dedicated shadow-mode config file for this repo.

Source example (known-good structure):
- `qlib/examples/benchmarks/LightGBM/workflow_config_lightgbm_Alpha158.yaml`

Create (planned new file):
- `config/qlib_shadow/workflow_us_lightgbm_alpha158_shadow.yaml`

Required edits (US default):
- `qlib_init.provider_uri`: `~/.qlib/qlib_data/us_data_1d`
- `qlib_init.region`: `us`
- `market`, `benchmark`, and `instruments`: **must match what exists in the downloaded US dataset**
- time ranges: must match dataset coverage
- ensure any “analysis/recording” writes into `artifacts/qlib-shadow/<run_id>/` (no DB)

## Step D — Dataset discovery (no guessing)
Before finalizing the YAML, discover what instrument universes and benchmarks exist in the downloaded dataset.

Planned checks (commands to run later):
- `ls -ლა ~/.qlib/qlib_data/us_data_1d`
- `find ~/.qlib/qlib_data/us_data_1d -maxdepth 2 -type f | head`

Decision rule:
- Use whatever “market” instrument list the dataset ships with (e.g., an index constituent list), rather than inventing one.

## Step E — Shadow-mode run procedure (baseline)
Planned run interface:
- Prefer Qlib’s CLI `qrun <config.yaml>` once Qlib is installed in the venv.

Outputs policy:
- Every run creates a new folder: `artifacts/qlib-shadow/<run_id>/`
- Copy into that folder:
  - the YAML used
  - a short `RUN.md` describing dataset path + run timestamp + git commit (if available)
  - extracted ranked signals file
  - backtest summary
  - logs

## Gate to proceed
We proceed to executing Qlib only after:
- This bootstrap doc is agreed and frozen.
- `docs/IMPLEMENTATION_STATUS.md` reflects: “Qlib bootstrap planning” is the current in-progress item.
