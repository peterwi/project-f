#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${ROOT_DIR}/config/secrets.env"
COMPOSE_FILE="${ROOT_DIR}/docker/compose.yml"

usage() {
  cat <<'EOF'
Usage:
  bash scripts/day_simulate.sh --date YYYY-MM-DD [--dryrun-trades]

Simulates a full deterministic "day":
  08:00 fetch -> reconcile -> 14:00 ticket -> confirm -> final reconcile report

Writes a single self-contained folder under /data/trading-ops/artifacts/day_sim/<date>/...
EOF
}

die() {
  echo "ERROR: $*" >&2
  exit 2
}

require_file() {
  local path="$1"
  [[ -f "$path" ]] || die "Missing required file: $path"
}

extract_env_var() {
  local key="$1"
  local value
  value="$(grep -E "^${key}=" "$ENV_FILE" | head -n 1 | cut -d= -f2- || true)"
  echo "${value}"
}

extract_kv_from_file() {
  local key="$1"
  local file="$2"
  awk -F= -v k="$key" '$0 ~ ("^" k "=") {print substr($0, length(k)+2); exit}' "$file"
}

psql_capture() {
  local sql="$1"
  docker compose -f "$COMPOSE_FILE" --env-file "$ENV_FILE" exec -T postgres \
    psql -q -U "$POSTGRES_USER" -d "$POSTGRES_DB" -v ON_ERROR_STOP=1 -tA -c "$sql" | sed -e 's/[[:space:]]*$//'
}

run_capture() {
  local name="$1"; shift
  local stdout_path="${OUT_DIR}/logs/${name}.stdout.log"
  local stderr_path="${OUT_DIR}/logs/${name}.stderr.log"
  echo "==> ${name}" | tee -a "${OUT_DIR}/logs/steps.log" >/dev/null
  (
    cd "$ROOT_DIR"
    "$@"
  ) >"$stdout_path" 2>"$stderr_path"
}

copy_into() {
  local src="$1"
  local dst="$2"
  if [[ -e "$src" ]]; then
    cp -a "$src" "$dst"
  fi
}

DATE=""
DRYRUN_TRADES="false"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --date)
      DATE="${2:-}"
      shift 2
      ;;
    --dryrun-trades)
      DRYRUN_TRADES="true"
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      die "Unknown arg: $1"
      ;;
  esac
done

[[ -n "$DATE" ]] || die "--date is required"
python3 -c "from datetime import date; date.fromisoformat('${DATE}')" >/dev/null 2>&1 || die "Invalid --date: ${DATE}"

require_file "$ENV_FILE"
require_file "$COMPOSE_FILE"

POSTGRES_USER="$(extract_env_var POSTGRES_USER)"
POSTGRES_DB="$(extract_env_var POSTGRES_DB)"
[[ -n "$POSTGRES_USER" && -n "$POSTGRES_DB" ]] || die "POSTGRES_USER/POSTGRES_DB missing in ${ENV_FILE}"

ARTIFACTS_DIR="$(extract_env_var ARTIFACTS_DIR)"
if [[ -z "$ARTIFACTS_DIR" ]]; then
  ARTIFACTS_DIR="/data/trading-ops/artifacts"
fi

GIT_COMMIT="$(git -c safe.directory=* -C "$ROOT_DIR" rev-parse HEAD)"
GIT_SHORT="$(git -c safe.directory=* -C "$ROOT_DIR" rev-parse --short HEAD)"
UTCSTAMP="$(date -u +%Y%m%dT%H%M%SZ)"

OUT_DIR="${ARTIFACTS_DIR}/day_sim/${DATE}/${UTCSTAMP}-git${GIT_SHORT}"
mkdir -p "${OUT_DIR}/"{logs,runs,tickets,confirmations,reconcile,inputs}

echo "${OUT_DIR}" >"${OUT_DIR}/_path.txt"
echo "date=${DATE}" >"${OUT_DIR}/ids.env"
echo "git_commit=${GIT_COMMIT}" >>"${OUT_DIR}/ids.env"
echo "git_short=${GIT_SHORT}" >>"${OUT_DIR}/ids.env"
echo "dryrun_trades=${DRYRUN_TRADES}" >>"${OUT_DIR}/ids.env"

copy_into "${ROOT_DIR}/config/universe.csv" "${OUT_DIR}/inputs/universe.csv"
copy_into "${ROOT_DIR}/config/policy.yml" "${OUT_DIR}/inputs/policy.yml"

run_capture "make_health" make health

# 08:00 scheduled (includes market-fetch)
run_capture "run_0800" python3 scripts/run_scheduled.py --cadence 0800 --asof-date "${DATE}"
RUN_0800_ID="$(extract_kv_from_file run_id "${OUT_DIR}/logs/run_0800.stdout.log")"
[[ -n "$RUN_0800_ID" ]] || die "Failed to capture 0800 run_id"
echo "run_0800_id=${RUN_0800_ID}" >>"${OUT_DIR}/ids.env"
copy_into "${ARTIFACTS_DIR}/runs/${RUN_0800_ID}/run_summary.md" "${OUT_DIR}/runs/0800_run_summary.md"

# Reconcile snapshot (SIMULATED) -> reconcile gate PASS
ledger_cash="$(psql_capture "select cash_base from ledger_cash_current;")"
if [[ -z "$ledger_cash" ]]; then
  ledger_cash="0"
fi

ledger_positions_raw="$(psql_capture "select internal_symbol || '=' || units::text from ledger_positions_current order by internal_symbol;")"
reconcile_args=(-- --snapshot-date "${DATE}" --cash-gbp "${ledger_cash}" --notes "SIM_DAY_SIMULATE:${DATE}")
if [[ -n "$ledger_positions_raw" ]]; then
  while IFS= read -r line; do
    [[ -n "$line" ]] || continue
    reconcile_args+=(--position "$line")
  done <<<"$ledger_positions_raw"
fi
run_capture "reconcile_daily_pre_1400" make reconcile-daily "${reconcile_args[@]}"
SNAPSHOT_PRE_ID="$(extract_kv_from_file snapshot_id "${OUT_DIR}/logs/reconcile_daily_pre_1400.stdout.log")"
REPORT_PRE_PATH="$(awk '/^Wrote /{print $2}' "${OUT_DIR}/logs/reconcile_daily_pre_1400.stdout.log" | tail -n 1)"
echo "reconcile_snapshot_pre_id=${SNAPSHOT_PRE_ID}" >>"${OUT_DIR}/ids.env"
echo "reconcile_report_pre_path=${REPORT_PRE_PATH}" >>"${OUT_DIR}/ids.env"
copy_into "${REPORT_PRE_PATH}" "${OUT_DIR}/reconcile/reconcile_pre_1400.md"

# 14:00 scheduled (ticket)
if [[ "$DRYRUN_TRADES" == "true" ]]; then
  run_capture "run_1400" env DRYRUN_TRADES=true python3 scripts/run_scheduled.py --cadence 1400 --asof-date "${DATE}"
else
  run_capture "run_1400" python3 scripts/run_scheduled.py --cadence 1400 --asof-date "${DATE}"
fi
RUN_1400_ID="$(extract_kv_from_file run_id "${OUT_DIR}/logs/run_1400.stdout.log")"
[[ -n "$RUN_1400_ID" ]] || die "Failed to capture 1400 run_id"
echo "run_1400_id=${RUN_1400_ID}" >>"${OUT_DIR}/ids.env"
copy_into "${ARTIFACTS_DIR}/runs/${RUN_1400_ID}/run_summary.md" "${OUT_DIR}/runs/1400_run_summary.md"
copy_into "${ARTIFACTS_DIR}/runs/${RUN_1400_ID}/trades_intended.json" "${OUT_DIR}/runs/1400_trades_intended.json"

run_capture "tickets_last" make tickets-last
TICKET_1400_ID="$(psql_capture "select coalesce(ticket_id::text,'') from tickets where run_id = '${RUN_1400_ID}' order by created_at desc limit 1;")"
[[ -n "$TICKET_1400_ID" ]] || die "Failed to resolve ticket_id for run_id=${RUN_1400_ID}"
echo "ticket_1400_id=${TICKET_1400_ID}" >>"${OUT_DIR}/ids.env"

TICKET_0800_ID="$(psql_capture "select coalesce(ticket_id::text,'') from tickets where run_id = '${RUN_0800_ID}' order by created_at desc limit 1;")"
if [[ -n "$TICKET_0800_ID" ]]; then
  echo "ticket_0800_id=${TICKET_0800_ID}" >>"${OUT_DIR}/ids.env"
fi

TICKET_DIR="${ARTIFACTS_DIR}/tickets/${TICKET_1400_ID}"
copy_into "${TICKET_DIR}/ticket.md" "${OUT_DIR}/tickets/ticket_1400.md"
copy_into "${TICKET_DIR}/ticket.json" "${OUT_DIR}/tickets/ticket_1400.json"
copy_into "${TICKET_DIR}/material_hash.txt" "${OUT_DIR}/tickets/material_hash_1400.txt"

material_hash_1400=""
if [[ -f "${TICKET_DIR}/material_hash.txt" ]]; then
  material_hash_1400="$(cat "${TICKET_DIR}/material_hash.txt" | tr -d '\n' || true)"
fi
echo "material_hash_1400=${material_hash_1400}" >>"${OUT_DIR}/ids.env"

decision_type="$(python3 -c "import json; print((json.load(open('${TICKET_DIR}/ticket.json')) or {}).get('decision_type',''))")"
echo "ticket_1400_decision_type=${decision_type}" >>"${OUT_DIR}/ids.env"

# Confirm
FILLS_JSON_PATH=""
if [[ "$decision_type" == "NO_TRADE" ]]; then
  run_capture "confirm_no_trade" python3 scripts/confirmations_submit.py --ticket-id "${TICKET_1400_ID}" --ack-no-trade --notes "SIM_DAY_SIMULATE:${DATE}"
elif [[ "$decision_type" == "TRADE" ]]; then
  FILLS_JSON_PATH="${OUT_DIR}/confirmations/fills_${TICKET_1400_ID}.json"
  python3 - <<PY
import json
from pathlib import Path

run_id = "${RUN_1400_ID}"
date_s = "${DATE}"
out_path = Path("${FILLS_JSON_PATH}")
src = Path("${ARTIFACTS_DIR}") / "runs" / run_id / "trades_intended.json"
payload = json.loads(src.read_text(encoding="utf-8"))
trades = payload.get("intended_trades") or []

fills = []
for i, t in enumerate(trades, start=1):
  sym = t.get("internal_symbol")
  side = (t.get("side") or "").upper()
  units = int(t.get("units") or 0)
  px = float(t.get("reference_price") or 0.0)
  fills.append({
    "sequence": i,
    "internal_symbol": sym,
    "side": side,
    "executed_status": "DONE",
    "units": units,
    "fill_price": px,
    "executed_value_base": abs(units * px),
    "filled_at": f"{date_s}T14:00:00Z",
    "notes": "SIMULATED_FILL_FROM_TRADES_INTENDED"
  })

out = {"schema_version": "v1", "run_id": run_id, "asof_date": date_s, "fills": fills}
out_path.write_text(json.dumps(out, indent=2, sort_keys=True) + "\n", encoding="utf-8")
PY
  run_capture "confirm_fills" env FILLS_JSON="${FILLS_JSON_PATH}" make confirm-fills -- --ticket-id "${TICKET_1400_ID}" --notes "SIM_DAY_SIMULATE:${DATE}"
else
  die "Unexpected decision_type in ticket.json: ${decision_type}"
fi

CONFIRM_STDOUT=""
if [[ -f "${OUT_DIR}/logs/confirm_no_trade.stdout.log" ]]; then
  CONFIRM_STDOUT="${OUT_DIR}/logs/confirm_no_trade.stdout.log"
elif [[ -f "${OUT_DIR}/logs/confirm_fills.stdout.log" ]]; then
  CONFIRM_STDOUT="${OUT_DIR}/logs/confirm_fills.stdout.log"
fi

CONFIRMATION_UUID="$(extract_kv_from_file confirmation_uuid "${CONFIRM_STDOUT}")"
CONFIRMATION_DIR="$(extract_kv_from_file confirmation_dir "${CONFIRM_STDOUT}")"
[[ -n "$CONFIRMATION_UUID" && -n "$CONFIRMATION_DIR" ]] || die "Failed to capture confirmation details"
echo "confirmation_uuid=${CONFIRMATION_UUID}" >>"${OUT_DIR}/ids.env"

mkdir -p "${OUT_DIR}/confirmations/${CONFIRMATION_UUID}"
copy_into "${CONFIRMATION_DIR}/confirmation.json" "${OUT_DIR}/confirmations/${CONFIRMATION_UUID}/confirmation.json"
copy_into "${CONFIRMATION_DIR}/confirmation.md" "${OUT_DIR}/confirmations/${CONFIRMATION_UUID}/confirmation.md"
if [[ -n "$FILLS_JSON_PATH" ]]; then
  copy_into "${FILLS_JSON_PATH}" "${OUT_DIR}/confirmations/${CONFIRMATION_UUID}/fills_used.json"
fi

# Final reconcile after confirmation (SIMULATED snapshot from current ledger state)
ledger_cash_final="$(psql_capture "select cash_base from ledger_cash_current;")"
ledger_positions_final_raw="$(psql_capture "select internal_symbol || '=' || units::text from ledger_positions_current order by internal_symbol;")"
reconcile_final_args=(-- --snapshot-date "${DATE}" --cash-gbp "${ledger_cash_final}" --notes "SIM_DAY_SIMULATE_FINAL:${DATE}")
if [[ -n "$ledger_positions_final_raw" ]]; then
  while IFS= read -r line; do
    [[ -n "$line" ]] || continue
    reconcile_final_args+=(--position "$line")
  done <<<"$ledger_positions_final_raw"
fi
run_capture "reconcile_daily_final" make reconcile-daily "${reconcile_final_args[@]}"
SNAPSHOT_FINAL_ID="$(extract_kv_from_file snapshot_id "${OUT_DIR}/logs/reconcile_daily_final.stdout.log")"
REPORT_FINAL_PATH="$(awk '/^Wrote /{print $2}' "${OUT_DIR}/logs/reconcile_daily_final.stdout.log" | tail -n 1)"
echo "reconcile_snapshot_final_id=${SNAPSHOT_FINAL_ID}" >>"${OUT_DIR}/ids.env"
echo "reconcile_report_final_path=${REPORT_FINAL_PATH}" >>"${OUT_DIR}/ids.env"
copy_into "${REPORT_FINAL_PATH}" "${OUT_DIR}/reconcile/reconcile_final.md"

cat >"${OUT_DIR}/README.md" <<EOF
# Day Simulation Report

- date: \`${DATE}\`
- git_commit: \`${GIT_COMMIT}\`
- artifacts_dir: \`${ARTIFACTS_DIR}\`
- simulation_dir: \`${OUT_DIR}\`
- dryrun_trades (14:00): \`${DRYRUN_TRADES}\`

## IDs

- run_id (0800): \`${RUN_0800_ID}\`
- run_id (1400): \`${RUN_1400_ID}\`
- ticket_id (1400): \`${TICKET_1400_ID}\`
- ticket_id (0800, if any): \`${TICKET_0800_ID:-}\`
- confirmation_uuid: \`${CONFIRMATION_UUID}\`

## Reconciliation

- snapshot_id (pre-14:00): \`${SNAPSHOT_PRE_ID}\`
- report (pre-14:00): \`${OUT_DIR}/reconcile/reconcile_pre_1400.md\`
- snapshot_id (final): \`${SNAPSHOT_FINAL_ID}\`
- report (final): \`${OUT_DIR}/reconcile/reconcile_final.md\`

## Ticket

- decision_type: \`${decision_type}\`
- material_hash (1400): \`${material_hash_1400}\`
- ticket artifacts:
  - \`${OUT_DIR}/tickets/ticket_1400.md\`
  - \`${OUT_DIR}/tickets/ticket_1400.json\`
  - \`${OUT_DIR}/tickets/material_hash_1400.txt\`

## Confirmation

- confirmation artifacts:
  - \`${OUT_DIR}/confirmations/${CONFIRMATION_UUID}/confirmation.json\`
  - \`${OUT_DIR}/confirmations/${CONFIRMATION_UUID}/confirmation.md\`
  - \`${OUT_DIR}/confirmations/${CONFIRMATION_UUID}/fills_used.json\` (if TRADE)

## Logs

- stdout/stderr captured under: \`${OUT_DIR}/logs/\`
EOF

echo "OK: ${OUT_DIR}"
