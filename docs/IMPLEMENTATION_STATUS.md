# Implementation Status (Source of Truth)

Update rule: **At the end of every completed step**, update this file and commit the change.

Legend:
- `[ ] TODO`
- `[x] DONE`

## Current focus
- [x] DONE Milestone 1 Step 1.1 — Qlib bootstrap planning — `docs/QLIB_BOOTSTRAP_UBUNTU24.md`
- [x] DONE Milestone 1 Step 1.2 — Qlib bootstrap execution (install + dataset + first run)
- [ ] TODO Milestone 1 Step 1.3 — First baseline `qrun` + artifact extraction (shadow mode)

---

## Last known working state (to resume quickly)

### Environment (frozen)
- Venv: `/home/peter/venvs/python-venv/` (Python `3.12.3`)
- Qlib installed into this venv from repo: `pip install -e ./qlib` (dependencies installed)
- OS deps installed to fix `Python.h` build error: `python3.12-dev`, `build-essential` (plus `libssl-dev`)

### Dataset (shadow bootstrap)
- Target dataset: US 1d
- Dataset directory: `~/.qlib/qlib_data/us_data_1d/`
- Verified non-empty and contains `calendars/`, `features/`, `instruments/`, and the downloaded zip:
  - `20251222221734_qlib_data_us_1d_latest.zip`

### Shadow artifacts (filesystem only; no Postgres writes)
- Latest Step 1.2 run_id: `20251222-221725Z-git66b72b7`
- Run folder: `artifacts/qlib-shadow/20251222-221725Z-git66b72b7/`
- Contents:
  - `bootstrap_step_1_2.txt`

### Notes / quirks
- `import qlib` succeeds, but `qlib.__version__` printed as `?` in Step 1.2 verification output; investigate during Step 1.3 if needed.

---

## PLAN.md milestones and steps

### Milestone 1 — Platform
- [ ] TODO Project B — Repo + environment + Docker baseline
- [ ] TODO Project C — PostgreSQL schema + migrations + backups
- [ ] TODO Confirm restore works

### Milestone 2 — Tradable universe
- [x] DONE Project A — Pre-flight: Reality checks (eToro + UK constraints) — `docs/ETORO_CONSTRAINTS.md`
- [ ] TODO Project D — Universe management

### Milestone 3 — Data + ledger
- [ ] TODO Project E — Market data pipeline (EOD) with quality gates
- [ ] TODO Project F — Ledger + reconciliation

### Milestone 4 — Risk + tickets + confirmations
- [ ] TODO Project G — Riskguard (deterministic sizing + approvals)
- [ ] TODO Project H — Ticketing + Comms + Confirmations

### Milestone 5 — Reporting + alerts
- [ ] TODO Project I — Reporting + Monitoring + Alerts

### Milestone 6 — Dry-run operations
- [ ] TODO Run on schedule (2–4 weeks) with stub signals
- [ ] TODO Fix ops defects only

### Milestone 7 — ML
- [ ] TODO Project J — ML/Qlib integration (shadow mode)
- [ ] TODO Re-run dry-run (2 weeks, shadow mode)
- [ ] TODO Enable ML in live (explicit go/no-go)

### Milestone 8 — MCP + GenAI
- [ ] TODO Project K — MCP suite + GenAI orchestrator
- [ ] TODO Enable LLM PM mode + analyst summaries
