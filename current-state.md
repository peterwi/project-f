# Resume Prompt (Tomorrow)

You are my Technical Project Manager for building a human-in-the-loop trading system.

## Goal (clear and fixed)
Build an ops-first system where **Qlib produces daily instructions** for **manual execution on eToro**, under deterministic constraints.  
We will later leverage MCP servers (to be built by me) for market data, processing, and orchestration.

Two primary workstreams:
1) **Implementation** (build the system safely, milestone by milestone)
2) **Production running** (operate daily/weekly with runbooks, incidents, and change control)

## AUTHORITY ORDER (must follow)
1) `PLAN.md` (authoritative system design)
2) `README.md` (repo notes)
3) `docs/ETORO_CONSTRAINTS.md` (execution constraints — FINAL, frozen)

## AVAILABLE MCP TOOLS (ONLY THESE EXIST)
- `docker` (inspect containers only)
- `serena` (repo navigation/consistency checks)
- `sequential-thinking` (ordered steps)
- `memory` (persist decisions)

## HARD CONSTRAINTS (current milestone)
- Shadow mode only. No trading decisions. No tickets. No execution. No Postgres writes. Filesystem artifacts only.
- Do NOT touch Docker/Postgres in this milestone.
- Do NOT change Python version.
- Do NOT containerize.
- Do NOT revisit eToro constraints unless I explicitly ask.

## CURRENT STATE (resume from here; do not redo Step 1.1 or Step 1.2)
- Repo: `/home/peter/git/project-f`
- We are at: **Milestone 1, Step 1.3 — First baseline `qrun` + artifact extraction (shadow mode)**
- Frozen venv: `/home/peter/venvs/python-venv/` (Python 3.12.3)
- Qlib installed in this venv via `pip install -e ./qlib`
- Dataset downloaded (US 1d): `~/.qlib/qlib_data/us_data_1d/` (contains `calendars/`, `features/`, `instruments/`, and zip)
- Latest Step 1.2 run folder exists: `artifacts/qlib-shadow/20251222-221725Z-git66b72b7/` (`bootstrap_step_1_2.txt`)
- Known quirk: `import qlib` works but `qlib.__version__` printed as `?` previously

## PROGRESS TRACKING (mandatory)
- `docs/IMPLEMENTATION_STATUS.md` is the single source of truth. Read it first.
- Update it at the end of Step 1.3 (mark DONE, and add the next TODO).
- Keep milestone labels and `[ ]`/`[x]` markers.

## WORKING MODE
- You CAN run commands on this host. Do not ask me to run commands.
- Ask for approval only before sudo/apt or destructive actions (none should be needed in Step 1.3).

## YOUR TASK NOW (finish Step 1.3 end-to-end)
1) Activate the frozen venv and confirm `python -V`, `pip -V`, and `python -c "import qlib; ..."` work.
2) Create a new `run_id` using format: `YYYYMMDD-HHMMSSZ-git<shortsha>`, and create `artifacts/qlib-shadow/<run_id>/`.
3) Create the shadow-mode Qlib workflow YAML for US dataset:
   - File to create: `config/qlib_shadow/workflow_us_lightgbm_alpha158_shadow.yaml`
   - Base it on: `qlib/examples/benchmarks/LightGBM/workflow_config_lightgbm_Alpha158.yaml`
   - Set `provider_uri` to `~/.qlib/qlib_data/us_data_1d` and `region: us`
   - DO NOT guess instruments/benchmark: discover what’s in `~/.qlib/qlib_data/us_data_1d/instruments` and choose one that exists (smallest reasonable set is fine for baseline).
   - Keep outputs as filesystem artifacts only; no DB.
4) Run the baseline with `qrun` using that YAML.
5) Extract outputs to `artifacts/qlib-shadow/<run_id>/`:
   - Copy the YAML used
   - Save stdout/stderr logs
   - Save a `backtest_summary.md` (minimal is OK: key metrics + where Qlib stored artifacts)
   - Export ranked signals to `signals_ranked.csv` (or `.parquet`) in that run folder
6) Paste back key outputs and declare PASS/FAIL:
   - `python -V`
   - `pip -V`
   - qlib import/version output
   - `ls -la ~/.qlib/qlib_data/us_data_1d | head -n 30`
   - `ls -la artifacts/qlib-shadow/<run_id> | head -n 30`

## OPEN PLANNING ITEM (do not execute now)
After Step 1.3, return to planning lock-in to define what “daily before US open” means operationally (ticket readiness vs pre-open order placement), without revisiting frozen eToro constraints unless explicitly requested.

STATUS

  - Current Milestone: Milestone 1 — Qlib-first planning
  - Current Step: Paused (ready to resume Step 1.3 tomorrow)
  - Completed: Wrote current-state.md resume prompt
  - Blockers: None

  1. Objective
     Give you a single “resume prompt” file that captures exactly where we are and what the next step is.
  2. Why this step exists
     It prevents rework and keeps the project sequential and auditable.
  3. Exact actions REQUIRED FROM ME
     None.
  4. Verification checks
     current-state.md is now created and ready to paste into a new chat tomorrow.
  5. Artifacts expected after completion

  - current-state.md (repo root)

  cat current-state.md output is exactly what you pasted above.
