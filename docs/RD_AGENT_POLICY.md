# RD-Agent Policy (ADVISORY/DEV-ONLY)

This project may optionally use **Microsoft RD-Agent** as an engineering accelerator. RD-Agent is **not** a trading agent and **must not** be given capabilities that could impact live operations.

## Mode

- **Mode:** ADVISORY/DEV-ONLY
- **Authority:** Deterministic gates (data-quality / reconciliation / riskguard) remain the sole authority. RD-Agent cannot override or bypass them.

## Allowed actions (explicit)

RD-Agent may:
- Read repo files and produce summaries of findings.
- Propose code changes as patches/PR drafts (no direct merges).
- Write or update documentation.
- Add or improve tests (unit/integration) and run them locally.
- Run lint/formatting and run verification commands.
- Run **Qlib shadow-mode** research runs locally/in Docker (bootstrap only).
- Propose refactors for readability/maintainability (must preserve behavior).

## Disallowed actions (non-negotiable)

RD-Agent must not:
- Place trades, execute trades, or automate eToro.
- Create/approve trade tickets or confirmations (can only propose code that would render them).
- Bypass or weaken deterministic gates (data-quality, reconciliation, riskguard, policy).
- Modify `config/secrets.env` or any production secrets.
- Scrape/bot eToro or perform browser automation.
- Change scheduler behavior (cron/supercronic schedules, service definitions, or job wiring) **without explicit human approval**.
- Write directly to Postgres outside of the normal deterministic scripts/migrations workflow.
- Change risk policy (`config/policy.yml`) without explicit human approval and checklist verification.

## Required boundaries / sandboxing

When running RD-Agent:
- Prefer read-only access to secrets (do not mount `config/secrets.env` into the agent environment).
- Do not provide database credentials. If DB access is needed for debugging, keep it **read-only** and human-mediated.
- Any code modifications must go through the normal checklist workflow.

## Review workflow (mandatory)

1. RD-Agent outputs: analysis + proposed patch(es) only.
2. Human reviews patch.
3. Apply patch to repo.
4. Run the **verification commands** from `docs/CHECKLIST.md` for the affected item(s).
5. Update:
   - `docs/CHECKLIST.md` (mark DONE/BLOCKED as appropriate)
   - `docs/PM_LOG.md` (append entry with commands + outcomes)
   - `docs/PM_STATE.md` (advance resume pointer)

## Source of truth

- Repo filesystem is the source of truth.
- `docs/CHECKLIST.md` is the only tracking mechanism.
- `docs/PM_STATE.md` is the single resume pointer.
- `docs/PM_LOG.md` is the append-only project journal.

