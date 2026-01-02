#!/usr/bin/env bash
set -euo pipefail

job="${1:?job name required}"
ts_utc="$(date -u +%Y%m%dT%H%M%SZ)"

log_dir="/data/trading-ops/artifacts/logs/scheduler"
mkdir -p "$log_dir"
log_path="${log_dir}/${job}_${ts_utc}.log"

{
  echo "ts_utc=${ts_utc}"
  echo "job=${job}"
  echo "pwd=$(pwd)"
} >>"$log_path"

cd /repo
rc=0
make "$job" >>"$log_path" 2>&1 || rc=$?
echo "exit_code=${rc}" >>"$log_path"

if [[ "$rc" -ne 0 ]]; then
  # Best-effort alert on scheduler misfire. Do not block on failures here.
  run_id="$(grep -E '^run_id=' "$log_path" | tail -n 1 | cut -d= -f2- || true)"
  ticket_id="$(grep -E '^LAST_TICKET_ID=' docs/PM_STATE.md 2>/dev/null | cut -d= -f2- || true)"
  details="$(python3 - <<PY
import json
print(json.dumps({
  "job": "${job}",
  "exit_code": int("${rc}"),
  "log_path": "${log_path}",
  "run_id_detected": "${run_id}",
}))
PY
)"
  python3 scripts/alert_emit.py \
    --alert-type SCHEDULER_MISFIRE \
    --severity ERROR \
    --summary "SCHEDULER_MISFIRE job=${job} exit_code=${rc}" \
    ${run_id:+--run-id "$run_id"} \
    ${ticket_id:+--ticket-id "$ticket_id"} \
    --details-json "$details" \
    --artifact-path "$log_path" \
    >/dev/null 2>&1 || true
fi

exit "$rc"
