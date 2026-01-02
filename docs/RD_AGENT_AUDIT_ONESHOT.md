# RD-Agent Audit Pack (One-shot)

This repo includes a safe, repeatable “audit pack” runner that:
- Reads the OpenAI API key from `./open-ai.key` (never prints it)
- Runs in an isolated `git worktree` under `/tmp` (no repo writes)
- Writes artifacts to `/data/trading-ops/artifacts/rd-agent/<run_id>/`
- Produces `outputs.md` + `run.log` (sanitized) + `VERIFY.md` (proof of clean git state)

## One command

Run from repo root:

- `bash scripts/rd_agent_audit.sh`

It prints only:
- `RD_AGENT_RUN_ID=<run_id>`
- `OUT=/data/trading-ops/artifacts/rd-agent/<run_id>`

## Output folder layout

Under `OUT`:
- `outputs.md` (audit results + optional proposed patch diff section)
- `patch.diff` (optional; extracted if the model provides `PATCH_DIFF`)
- `prompt.md` (the exact prompt/context provided to the model)
- `run.log` (sanitized stdout/stderr from the audit run)
- `VERIFY.md` (records main repo + worktree `git status --porcelain=v1` before/after)
- `git_status_main_before.txt`
- `git_status_main_after.txt`
- `git_status_worktree_before.txt`
- `git_status_worktree_after.txt`

## “No repo writes” guarantee (and proof)

Guarantee:
- The audit runs inside a detached-head worktree at `/tmp/rdagent-wt-<run_id>`.
- No patches are applied; any diff is saved only as a suggestion under `OUT/patch.diff`.

Proof:
- The script refuses to run if the main repo is dirty.
- The script records `git status --porcelain=v1` BEFORE/AFTER for both the main repo and the worktree into `OUT/VERIFY.md`.
- The script exits non-zero if either repo becomes dirty.

## Troubleshooting

- **HTTP 401 / unauthorized**: `./open-ai.key` is invalid; regenerate a new key and replace the file (do not paste it into terminals/logs).
- **HTTP 429 / insufficient_quota / rate limit**: billing/limits are not active or you’re rate-limited; check account limits and retry later.
- **Model not found (404)**: set `RD_AGENT_MODEL` to an available model, e.g.:
  - `RD_AGENT_MODEL=gpt-4o-mini bash scripts/rd_agent_audit.sh`

## Advanced options

- Override model:
  - `RD_AGENT_MODEL=gpt-4o-mini bash scripts/rd_agent_audit.sh`
- Override output token budget:
  - `RD_AGENT_MAX_TOKENS=1100 bash scripts/rd_agent_audit.sh`
