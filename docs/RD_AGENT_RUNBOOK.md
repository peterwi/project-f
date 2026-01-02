# RD-Agent Runbook (Optional)

This runbook describes how to run **Microsoft RD-Agent** locally in **ADVISORY/DEV-ONLY** mode for this repo.

RD-Agent is optional. If you do not have RD-Agent installed, skip `M9.x`.

## Operating principles

- RD-Agent proposes patches; humans apply them.
- All progress is tracked only in `docs/CHECKLIST.md`.
- Resume pointer is `docs/PM_STATE.md`.
- Every run should produce artifacts under `/data/trading-ops/artifacts/rd-agent/<run_id>/`.

## Artifacts layout

For each RD-Agent run:
- `/data/trading-ops/artifacts/rd-agent/<run_id>/`
  - `prompt.md` (the exact prompt/context given)
  - `outputs.md` (analysis + proposed changes)
  - `patch.diff` (if produced)
  - `verification.md` (commands run + outputs)

Suggested `run_id` format (UTC, deterministic):
- `<UTCtimestamp>-git<short_sha>-rdagent`
  - Example: `20260102T161500Z-git6fe590a-rdagent`

## Local run (examples)

These commands are placeholders because the exact RD-Agent CLI varies by install. The key requirement is: **no secrets, no direct DB write authority, and no scheduler changes without approval**.

1) Create a run folder:
- `run_id="$(date -u +%Y%m%dT%H%M%SZ)-git$(git rev-parse --short HEAD)-rdagent"`
- `mkdir -p "/data/trading-ops/artifacts/rd-agent/${run_id}"`

2) Capture resume context:
- `cp docs/PM_STATE.md "/data/trading-ops/artifacts/rd-agent/${run_id}/PM_STATE.md"`
- `cp docs/CHECKLIST.md "/data/trading-ops/artifacts/rd-agent/${run_id}/CHECKLIST.md"`

3) Run RD-Agent in audit mode (dry-run; no changes applied):
- Run RD-Agent with repo read access and output redirected to:
  - `/data/trading-ops/artifacts/rd-agent/<run_id>/outputs.md`

4) If RD-Agent produces a patch:
- Save it as:
  - `/data/trading-ops/artifacts/rd-agent/<run_id>/patch.diff`
- Human review and apply via the normal workflow.

## “Repo audit” task template (M9.2)

Give RD-Agent a task like:
- Audit current repo state vs `docs/CHECKLIST.md`, identify the first failing verification command(s), and propose minimal fixes as a patch.

Constraints to include in the prompt:
- Do not touch `config/secrets.env`.
- Do not introduce network delivery for alerts.
- Do not change scheduler behavior unless explicitly asked.
- Do not weaken deterministic gates.
- No eToro automation.

## How RD-Agent must reference our process

RD-Agent output should always cite:
- Which checklist item(s) it is helping with (e.g., `M9.2`).
- Exact commands to verify changes (copy from `docs/CHECKLIST.md`).
- Which files would change.

## Safety checklist before applying any RD-Agent patch

- Patch only touches intended scope.
- No secrets edited/added.
- No gate bypass.
- No scheduler modifications unless explicitly approved.
- Verification commands pass.
- Update `docs/PM_LOG.md` and advance `docs/PM_STATE.md`.

