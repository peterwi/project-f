#!/usr/bin/env bash
set +x
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

if [[ ! -f "./open-ai.key" ]]; then
  echo "ERROR: ./open-ai.key is missing" >&2
  exit 2
fi

OPENAI_API_KEY="$(tr -d '\n' < ./open-ai.key)"
export OPENAI_API_KEY

GIT_SHA="$(git rev-parse --short HEAD 2>/dev/null || echo nogit)"
RD_AGENT_RUN_ID="${RD_AGENT_RUN_ID:-$(date -u +%Y%m%d-%H%M%SZ)-rd-audit-git${GIT_SHA}}"
OUT="/data/trading-ops/artifacts/rd-agent/${RD_AGENT_RUN_ID}"
mkdir -p "$OUT"

main_before="$(git status --porcelain=v1 || true)"
printf '%s\n' "$main_before" >"$OUT/git_status_main_before.txt"
if [[ -n "$main_before" ]]; then
  echo "ERROR: main repo is not clean; refusing to run audit." >&2
  echo "See: $OUT/git_status_main_before.txt" >&2
  exit 3
fi

WT="/tmp/rdagent-wt-${RD_AGENT_RUN_ID}"
if [[ -e "$WT" ]]; then
  echo "ERROR: worktree path already exists: $WT" >&2
  exit 4
fi

VENV="/data/trading-ops/venvs/rdagent/bin/activate"
if [[ ! -f "$VENV" ]]; then
  echo "ERROR: RD-Agent venv not found: $VENV" >&2
  exit 5
fi

cleanup() {
  set +e
  if declare -f deactivate >/dev/null 2>&1; then
    deactivate >/dev/null 2>&1 || true
  fi
  if git -C "$REPO_ROOT" worktree list --porcelain 2>/dev/null | grep -Fq "worktree $WT"; then
    git -C "$REPO_ROOT" worktree remove "$WT" --force >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT

git -C "$REPO_ROOT" worktree add --detach "$WT" HEAD >/dev/null 2>&1

wt_before="$(git -C "$WT" status --porcelain=v1 || true)"
printf '%s\n' "$wt_before" >"$OUT/git_status_worktree_before.txt"
if [[ -n "$wt_before" ]]; then
  echo "ERROR: worktree is not clean; refusing to run audit." >&2
  echo "See: $OUT/git_status_worktree_before.txt" >&2
  exit 6
fi

source "$VENV"

RD_AGENT_MODEL="${RD_AGENT_MODEL:-gpt-4o-mini}"
RD_AGENT_MAX_TOKENS="${RD_AGENT_MAX_TOKENS:-1100}"
RUN_STARTED_UTC="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

( cd "$WT" && OUT_DIR="$OUT" MODEL="$RD_AGENT_MODEL" MAX_TOKENS="$RD_AGENT_MAX_TOKENS" python - <<'PY'
import datetime
import os
import re
import subprocess
from pathlib import Path

from litellm import completion

out_dir = Path(os.environ["OUT_DIR"]).resolve()
repo_dir = Path.cwd().resolve()
model = os.environ.get("MODEL") or "gpt-4o-mini"
max_tokens = int(os.environ.get("MAX_TOKENS") or "1100")


def read_text(path: Path, max_bytes: int) -> str:
    try:
        data = path.read_bytes()
    except FileNotFoundError:
        return f"[MISSING] {path}"
    if len(data) > max_bytes:
        data = data[:max_bytes]
        suffix = f"\n\n[TRUNCATED to {max_bytes} bytes]"
    else:
        suffix = ""
    return data.decode("utf-8", errors="replace") + suffix


def redact(s: str) -> str:
    s = re.sub(r"sk-[A-Za-z0-9]{20,}", "sk-[REDACTED]", s)
    s = re.sub(r"Bearer\s+[A-Za-z0-9_-]+", "Bearer [REDACTED]", s)
    s = re.sub(r"[A-Za-z0-9_-]{40,}", "[REDACTED]", s)
    return s


def git_short_sha() -> str:
    try:
        return subprocess.check_output(["git", "rev-parse", "--short", "HEAD"], text=True).strip()
    except Exception:
        return "nogit"


git_sha = git_short_sha()

try:
    tracked_files = subprocess.check_output(["git", "ls-files"], text=True).splitlines()
except Exception:
    tracked_files = []
tracked_preview = "\n".join(tracked_files[:220])
if len(tracked_files) > 220:
    tracked_preview += f"\n... ({len(tracked_files) - 220} more)"

checklist = read_text(repo_dir / "docs/CHECKLIST.md", max_bytes=80_000)
m9_start = checklist.find("## M9")
checklist_focus = checklist[m9_start : m9_start + 16_000] if m9_start != -1 else checklist[:16_000]

pm_state = read_text(repo_dir / "docs/PM_STATE.md", max_bytes=10_000)
policy = read_text(repo_dir / "docs/RD_AGENT_POLICY.md", max_bytes=25_000)
runbook = read_text(repo_dir / "docs/RD_AGENT_RUNBOOK.md", max_bytes=25_000)

prompt = f"""# RD-Agent repo audit pack

You are Microsoft RD-Agent in ADVISORY/DEV-ONLY mode. Your job is to audit and propose improvements.

Hard constraints:
- Do NOT apply patches or modify the repo.
- Do NOT output any secrets (API keys, credentials). If you see something that looks like a secret, redact it.
- Do NOT propose changes that weaken deterministic gates or add live trading / data delivery authority.

Context:
- Repo: {repo_dir}
- Git: {git_sha}

## PM_STATE
{pm_state}

## RD_AGENT_POLICY
{policy}

## RD_AGENT_RUNBOOK
{runbook}

## CHECKLIST (focus: M9)
{checklist_focus}

## Tracked files (preview)
{tracked_preview}

Task:
1) Audit the repo at a high level and summarize what it does.
2) Identify risks, missing docs, or inconsistencies relevant to the checklist/process.
3) Provide a prioritized backlog of improvements with concrete verification commands.
4) If you propose code/documentation changes, include a minimal unified diff under a section titled exactly 'PATCH_DIFF'. If no patch, write 'PATCH_DIFF: NONE'.
"""

out_dir.mkdir(parents=True, exist_ok=True)
(out_dir / "prompt.md").write_text(redact(prompt), encoding="utf-8")

started = datetime.datetime.now(datetime.UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")

resp = completion(
    model=model,
    api_key=os.environ.get("OPENAI_API_KEY"),
    messages=[
        {"role": "system", "content": "You are an engineering repo auditor. Follow the provided constraints."},
        {"role": "user", "content": prompt},
    ],
    temperature=0.2,
    max_tokens=max_tokens,
)

content = redact(resp["choices"][0]["message"]["content"])

outputs = f"""# RD-Agent outputs (SUCCESS)

- started_utc: {started}
- model: {model}
- git: {git_sha}

---

{content}
"""
(out_dir / "outputs.md").write_text(outputs, encoding="utf-8")

m = re.search(r"^PATCH_DIFF\s*:\s*(.*)$", content, flags=re.MULTILINE)
if m and m.group(1).strip().upper() != "NONE":
    idx = content.find("PATCH_DIFF")
    (out_dir / "patch.diff").write_text(content[idx:].strip() + "\n", encoding="utf-8")
PY
) >"$OUT/run.raw.log" 2>&1

{
  echo "RD-Agent audit pack runner"
  echo "run_id=$RD_AGENT_RUN_ID"
  echo "started_utc=$RUN_STARTED_UTC"
  echo "model=$RD_AGENT_MODEL"
  echo "git_sha=$GIT_SHA"
  echo "---"
} >"$OUT/run.log"

sed -E \
  -e 's/sk-[A-Za-z0-9]{20,}/sk-[REDACTED]/g' \
  -e 's/Bearer[[:space:]]+[A-Za-z0-9_-]+/Bearer [REDACTED]/g' \
  -e 's/[A-Za-z0-9_-]{40,}/[REDACTED]/g' \
  "$OUT/run.raw.log" >>"$OUT/run.log"

rm -f "$OUT/run.raw.log"

if [[ ! -f "$OUT/outputs.md" ]]; then
  echo "ERROR: missing required artifact: $OUT/outputs.md" >&2
  exit 7
fi

wt_after="$(git -C "$WT" status --porcelain=v1 || true)"
printf '%s\n' "$wt_after" >"$OUT/git_status_worktree_after.txt"

main_after="$(git -C "$REPO_ROOT" status --porcelain=v1 || true)"
printf '%s\n' "$main_after" >"$OUT/git_status_main_after.txt"

cat >"$OUT/VERIFY.md" <<EOF
# VERIFY (no repo writes)

- run_id: $RD_AGENT_RUN_ID
- out_dir: $OUT
- repo_root: $REPO_ROOT
- git_sha: $GIT_SHA
- model: $RD_AGENT_MODEL

## Git status (main repo)

### BEFORE

\`\`\`
$(cat "$OUT/git_status_main_before.txt")
\`\`\`

### AFTER

\`\`\`
$(cat "$OUT/git_status_main_after.txt")
\`\`\`

## Git status (worktree)

### BEFORE

\`\`\`
$(cat "$OUT/git_status_worktree_before.txt")
\`\`\`

### AFTER

\`\`\`
$(cat "$OUT/git_status_worktree_after.txt")
\`\`\`

## Assertion

- PASS if all 4 blocks above are empty and \`outputs.md\` exists.
EOF

if [[ -n "$main_after" || -n "$wt_after" ]]; then
  echo "ERROR: git status not clean after run (see $OUT/VERIFY.md)" >&2
  exit 8
fi

git -C "$REPO_ROOT" worktree remove "$WT" --force >/dev/null

echo "RD_AGENT_RUN_ID=$RD_AGENT_RUN_ID"
echo "OUT=$OUT"
