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
