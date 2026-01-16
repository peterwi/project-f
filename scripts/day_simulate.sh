#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${ROOT_DIR}/config/secrets.env"
COMPOSE_FILE="${ROOT_DIR}/docker/compose.yml"

usage() {
  cat <<'EOF'
Usage:
  bash scripts/day_simulate.sh --date YYYY-MM-DD [--dryrun-trades] [--seed-missing-fills]

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

set_kv_file() {
  local file="$1"
  local key="$2"
  local value="$3"
  if grep -qE "^${key}=" "$file"; then
    sed -i -E "s|^${key}=.*|${key}=${value}|" "$file"
  else
    echo "${key}=${value}" >>"$file"
  fi
}

set_kv() {
  local key="$1"
  local value="$2"
  set_kv_file "${OUT_DIR}/ids.env" "$key" "$value"
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
SEED_MISSING_FILLS="false"

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
    --seed-missing-fills)
      SEED_MISSING_FILLS="true"
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
set_kv "git_commit" "${GIT_COMMIT}"
set_kv "git_short" "${GIT_SHORT}"
set_kv "dryrun_trades" "${DRYRUN_TRADES}"
set_kv "seed_missing_fills" "${SEED_MISSING_FILLS}"

copy_into "${ROOT_DIR}/config/universe.csv" "${OUT_DIR}/inputs/universe.csv"
copy_into "${ROOT_DIR}/config/policy.yml" "${OUT_DIR}/inputs/policy.yml"

run_capture "make_health" make health

# Baseline DB state (global; affects confirmations gate).
baseline_db_latest_trade_ticket_id="$(psql_capture "select coalesce(ticket_id::text,'') from tickets where ticket_type='TRADE' order by created_at desc limit 1;")"
baseline_db_intended_count="0"
baseline_db_fills_count="0"
if [[ -n "$baseline_db_latest_trade_ticket_id" ]]; then
  baseline_counts="$(psql_capture "select (select count(*) from ledger_trades_intended where ticket_id='${baseline_db_latest_trade_ticket_id}'::uuid)::text || '|' || (select count(*) from ledger_trades_fills where ticket_id='${baseline_db_latest_trade_ticket_id}'::uuid)::text;")"
  baseline_db_intended_count="${baseline_counts%%|*}"
  baseline_db_fills_count="${baseline_counts##*|}"
fi
set_kv "baseline_db_latest_trade_ticket_id" "${baseline_db_latest_trade_ticket_id}"
set_kv "baseline_db_intended_count" "${baseline_db_intended_count}"
set_kv "baseline_db_fills_count" "${baseline_db_fills_count}"

# 08:00 scheduled (includes market-fetch)
run_capture "run_0800" python3 scripts/run_scheduled.py --cadence 0800 --asof-date "${DATE}"
RUN_0800_ID="$(extract_kv_from_file run_id "${OUT_DIR}/logs/run_0800.stdout.log")"
[[ -n "$RUN_0800_ID" ]] || die "Failed to capture 0800 run_id"
set_kv "run_0800_id" "${RUN_0800_ID}"
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
TICKET_0800_ID="$(psql_capture "select coalesce(ticket_id::text,'') from tickets where run_id = '${RUN_0800_ID}' order by created_at desc limit 1;")"
if [[ -n "$TICKET_0800_ID" ]]; then
  set_kv "ticket_0800_id" "${TICKET_0800_ID}"
fi

run_1400_once() {
  local capture_name="$1"
  if [[ "$DRYRUN_TRADES" == "true" ]]; then
    run_capture "${capture_name}" env DRYRUN_TRADES=true python3 scripts/run_scheduled.py --cadence 1400 --asof-date "${DATE}"
  else
    run_capture "${capture_name}" python3 scripts/run_scheduled.py --cadence 1400 --asof-date "${DATE}"
  fi
}

snapshot_1400_artifacts() {
  local label="$1"
  copy_into "${ARTIFACTS_DIR}/runs/${RUN_1400_ID}/run_summary.md" "${OUT_DIR}/runs/1400_run_summary${label}.md"
  copy_into "${ARTIFACTS_DIR}/runs/${RUN_1400_ID}/trades_intended.json" "${OUT_DIR}/runs/1400_trades_intended${label}.json"
  copy_into "${ARTIFACTS_DIR}/runs/${RUN_1400_ID}/no_trade.json" "${OUT_DIR}/runs/no_trade_1400${label}.json"
  copy_into "${ARTIFACTS_DIR}/tickets/${TICKET_1400_ID}/ticket.md" "${OUT_DIR}/tickets/ticket_1400${label}.md"
  copy_into "${ARTIFACTS_DIR}/tickets/${TICKET_1400_ID}/ticket.json" "${OUT_DIR}/tickets/ticket_1400${label}.json"
  copy_into "${ARTIFACTS_DIR}/tickets/${TICKET_1400_ID}/material_hash.txt" "${OUT_DIR}/tickets/material_hash_1400${label}.txt"
}

finalize_1400_vars_and_copy() {
  RUN_1400_ID="$(extract_kv_from_file run_id "${OUT_DIR}/logs/${RUN_1400_CAPTURE}.stdout.log")"
  [[ -n "$RUN_1400_ID" ]] || die "Failed to capture 1400 run_id"
  run_capture "tickets_last_${RUN_1400_CAPTURE}" make tickets-last
  TICKET_1400_ID="$(psql_capture "select coalesce(ticket_id::text,'') from tickets where run_id = '${RUN_1400_ID}' order by created_at desc limit 1;")"
  [[ -n "$TICKET_1400_ID" ]] || die "Failed to resolve ticket_id for run_id=${RUN_1400_ID}"

  local ticket_dir="${ARTIFACTS_DIR}/tickets/${TICKET_1400_ID}"
  material_hash_1400=""
  if [[ -f "${ticket_dir}/material_hash.txt" ]]; then
    material_hash_1400="$(cat "${ticket_dir}/material_hash.txt" | tr -d '\n' || true)"
  fi
  decision_type="$(python3 -c "import json; print((json.load(open('${ticket_dir}/ticket.json')) or {}).get('decision_type',''))")"

  copy_into "${ARTIFACTS_DIR}/runs/${RUN_1400_ID}/run_summary.md" "${OUT_DIR}/runs/1400_run_summary.md"
  copy_into "${ARTIFACTS_DIR}/runs/${RUN_1400_ID}/trades_intended.json" "${OUT_DIR}/runs/1400_trades_intended.json"
  copy_into "${ticket_dir}/ticket.md" "${OUT_DIR}/tickets/ticket_1400.md"
  copy_into "${ticket_dir}/ticket.json" "${OUT_DIR}/tickets/ticket_1400.json"
  copy_into "${ticket_dir}/material_hash.txt" "${OUT_DIR}/tickets/material_hash_1400.txt"

  if [[ "$decision_type" == "NO_TRADE" ]]; then
    copy_into "${ARTIFACTS_DIR}/runs/${RUN_1400_ID}/no_trade.json" "${OUT_DIR}/runs/no_trade_1400.json"
  fi
}

RUN_1400_CAPTURE="run_1400"
run_1400_once "${RUN_1400_CAPTURE}"
finalize_1400_vars_and_copy

set_kv "run_1400_id" "${RUN_1400_ID}"
set_kv "ticket_1400_id" "${TICKET_1400_ID}"
set_kv "material_hash_1400" "${material_hash_1400}"
set_kv "ticket_1400_decision_type" "${decision_type}"

# Optional confirmations unblocking (deterministically seeds fills for the prior TRADE ticket if blocked).
seeded_missing_fills="false"
seed_reason="not_triggered_no_confirmation_missing"
seed_target_ticket_id=""
seed_fills_intended_count="0"
seed_fills_written_count="0"
fills_seed_json=""
seed_db_before_intended="0"
seed_db_before_fills="0"
seed_db_after_intended="0"
seed_db_after_fills="0"

if [[ "$SEED_MISSING_FILLS" == "true" ]]; then
  # Candidate seed target: baseline latest TRADE ticket at sim start (may be overridden if no_trade.json specifies a different one).
  seed_target_ticket_id="${baseline_db_latest_trade_ticket_id}"
  if [[ -n "$seed_target_ticket_id" ]]; then
    seed_counts="$(psql_capture "select (select count(*) from ledger_trades_intended where ticket_id='${seed_target_ticket_id}'::uuid)::text || '|' || (select count(*) from ledger_trades_fills where ticket_id='${seed_target_ticket_id}'::uuid)::text;")"
    seed_db_before_intended="${seed_counts%%|*}"
    seed_db_before_fills="${seed_counts##*|}"
    seed_db_after_intended="${seed_db_before_intended}"
    seed_db_after_fills="${seed_db_before_fills}"
    if [[ "${seed_db_before_fills}" != "0" ]]; then
      seed_reason="already_had_fills"
    fi
  fi
fi

if [[ "$SEED_MISSING_FILLS" == "true" && "$decision_type" == "NO_TRADE" && -f "${OUT_DIR}/runs/no_trade_1400.json" ]]; then
  seeded_ticket_id="$(python3 - <<'PY' "${OUT_DIR}/runs/no_trade_1400.json"
import json
import sys

path = sys.argv[1]
j = json.loads(open(path, encoding="utf-8").read())

codes = sorted({str(r.get("code") or "") for r in (j.get("reasons") or [])})
if "CONFIRMATION_MISSING" not in codes:
  raise SystemExit(0)

conf = None
for rc in (j.get("risk_checks") or []):
  if rc.get("name") == "confirmations":
    conf = rc.get("detail") or {}
    break
tid = (conf or {}).get("latest_trade_ticket_id") or ""
print(tid)
PY
  )"
  if [[ -n "$seeded_ticket_id" ]]; then
    seed_target_ticket_id="${seeded_ticket_id}"
    seed_counts="$(psql_capture "select (select count(*) from ledger_trades_intended where ticket_id='${seed_target_ticket_id}'::uuid)::text || '|' || (select count(*) from ledger_trades_fills where ticket_id='${seed_target_ticket_id}'::uuid)::text;")"
    seed_db_before_intended="${seed_counts%%|*}"
    seed_db_before_fills="${seed_counts##*|}"
    seed_db_after_intended="${seed_db_before_intended}"
    seed_db_after_fills="${seed_db_before_fills}"

    if [[ "${seed_db_before_intended}" != "0" && "${seed_db_before_fills}" == "0" ]]; then
      RUN_1400_ID_INITIAL="${RUN_1400_ID}"
      TICKET_1400_ID_INITIAL="${TICKET_1400_ID}"

      fills_seed_json="${OUT_DIR}/inputs/fills_seed.json"
      FILLS_SEED_PATH="${fills_seed_json}"
      intended_rows="$(psql_capture "select sequence::text || '|' || internal_symbol || '|' || side || '|' || coalesce(units::text,'') || '|' || coalesce(notional_value_base::text,'') || '|' || coalesce(limit_price::text, reference_price::text,'') from ledger_trades_intended where ticket_id = '${seed_target_ticket_id}' order by sequence;")"
      [[ -n "$intended_rows" ]] || die "seed-missing-fills: no ledger_trades_intended rows found for ticket_id=${seed_target_ticket_id}"
      seed_fills_intended_count="${seed_db_before_intended}"
      INTENDED_ROWS="$intended_rows" python3 - <<'PY' "${FILLS_SEED_PATH}" "${seed_target_ticket_id}"
import json
import os
import sys
from decimal import Decimal, InvalidOperation
import math

out_path = sys.argv[1]
ticket_id = sys.argv[2]

def _stable_num_str(s: str) -> str:
  raw = (s or "").strip()
  if raw == "":
    return ""
  try:
    d = Decimal(raw)
  except InvalidOperation:
    return raw
  return format(d.normalize(), "f")

fills = []
for line in (os.environ.get("INTENDED_ROWS", "") or "").splitlines():
  if not line.strip():
    continue
  seq_s, sym, side, units_s, notional_s, price_s = line.split("|", 5)
  units_out = None
  units_raw = (units_s or "").strip()
  if units_raw != "":
    units_out = _stable_num_str(units_raw)
  else:
    notional_raw = (notional_s or "").strip()
    price_raw = (price_s or "").strip()
    if notional_raw != "" and price_raw != "":
      try:
        notional = float(notional_raw)
        price = float(price_raw)
        if price > 0:
          units_out = str(int(math.floor(abs(notional) / price)))
      except Exception:
        units_out = None

  fill_price_out = None
  price_raw = (price_s or "").strip()
  if price_raw != "":
    fill_price_out = _stable_num_str(price_raw)
  fills.append(
    {
      "sequence": int(seq_s),
      "internal_symbol": sym,
      "side": side,
      "executed_status": "SKIPPED",
      "units": units_out,
      "fill_price": fill_price_out,
      "executed_value_base": None,
      "filled_at": None,
      "notes": "SEEDED_FROM_LEDGER_TRADES_INTENDED",
    }
  )
fills.sort(key=lambda r: int(r["sequence"]))

out = {"schema_version": "v1", "ticket_id": ticket_id, "fills": fills}
with open(out_path, "w", encoding="utf-8") as f:
  f.write(json.dumps(out, indent=2, sort_keys=True) + "\n")
PY

      seed_fills_written_count="$(python3 -c "import json; import sys; j=json.load(open('${FILLS_SEED_PATH}', encoding='utf-8')); print(len(j.get('fills') or []))")"

      echo "before intended=${seed_db_before_intended} fills=${seed_db_before_fills}" >"${OUT_DIR}/logs/seed_fills_db_counts.txt"
      run_capture "seed_missing_fills_confirm" env FILLS_JSON="${FILLS_SEED_PATH}" make confirm-fills -- --ticket-id "${seed_target_ticket_id}" --submitted-by "day_simulate" --notes "SEEDED_BY_DAY_SIMULATE"
      seed_counts_after="$(psql_capture "select (select count(*) from ledger_trades_intended where ticket_id='${seed_target_ticket_id}'::uuid)::text || '|' || (select count(*) from ledger_trades_fills where ticket_id='${seed_target_ticket_id}'::uuid)::text;")"
      seed_db_after_intended="${seed_counts_after%%|*}"
      seed_db_after_fills="${seed_counts_after##*|}"
      echo "after intended=${seed_db_after_intended} fills=${seed_db_after_fills}" >>"${OUT_DIR}/logs/seed_fills_db_counts.txt"

      seeded_missing_fills="true"
      seed_reason="seeded_now"

      copy_into "${OUT_DIR}/runs/1400_run_summary.md" "${OUT_DIR}/runs/1400_run_summary_initial.md"
      copy_into "${OUT_DIR}/runs/1400_trades_intended.json" "${OUT_DIR}/runs/1400_trades_intended_initial.json"
      copy_into "${OUT_DIR}/tickets/ticket_1400.md" "${OUT_DIR}/tickets/ticket_1400_initial.md"
      copy_into "${OUT_DIR}/tickets/ticket_1400.json" "${OUT_DIR}/tickets/ticket_1400_initial.json"
      copy_into "${OUT_DIR}/tickets/material_hash_1400.txt" "${OUT_DIR}/tickets/material_hash_1400_initial.txt"
      copy_into "${OUT_DIR}/runs/no_trade_1400.json" "${OUT_DIR}/runs/no_trade_1400_initial.json"

      RUN_1400_CAPTURE="run_1400_after_seed"
      run_1400_once "${RUN_1400_CAPTURE}"
      finalize_1400_vars_and_copy

      set_kv "run_1400_id_initial" "${RUN_1400_ID_INITIAL}"
      set_kv "ticket_1400_id_initial" "${TICKET_1400_ID_INITIAL}"
      set_kv "run_1400_id" "${RUN_1400_ID}"
      set_kv "ticket_1400_id" "${TICKET_1400_ID}"
      set_kv "material_hash_1400" "${material_hash_1400}"
      set_kv "ticket_1400_decision_type" "${decision_type}"

      if [[ "$decision_type" != "NO_TRADE" ]]; then
        rm -f "${OUT_DIR}/runs/no_trade_1400.json"
      fi
    else
      if [[ "${seed_db_before_fills}" != "0" ]]; then
        seed_reason="already_had_fills"
      fi
    fi
  fi
fi

# If seeding was requested but never wrote db counts (e.g., seed not triggered), still emit a stable proof file when we have a target.
if [[ "$SEED_MISSING_FILLS" == "true" && -n "$seed_target_ticket_id" && ! -f "${OUT_DIR}/logs/seed_fills_db_counts.txt" ]]; then
  echo "before intended=${seed_db_before_intended} fills=${seed_db_before_fills}" >"${OUT_DIR}/logs/seed_fills_db_counts.txt"
  echo "after intended=${seed_db_after_intended} fills=${seed_db_after_fills}" >>"${OUT_DIR}/logs/seed_fills_db_counts.txt"
fi

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
    "executed_status": "SKIPPED",
    "units": units,
    "fill_price": px,
    "notes": "SIMULATED_FILL_FROM_TRADES_INTENDED"
  })

out = {"schema_version": "v1", "run_id": run_id, "asof_date": date_s, "fills": fills}
out_path.write_text(json.dumps(out, indent=2, sort_keys=True) + "\n", encoding="utf-8")
PY
  run_capture "confirm_fills" env FILLS_JSON="${FILLS_JSON_PATH}" make confirm-fills -- --ticket-id "${TICKET_1400_ID}" --submitted-by "day_simulate" --notes "SIM_DAY_SIMULATE:${DATE}"
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
- seed-missing-fills: \`${SEED_MISSING_FILLS}\`

## Baseline DB (global state; affects confirmations gate)

- baseline_db_latest_trade_ticket_id: \`${baseline_db_latest_trade_ticket_id}\`
- baseline_db_intended_count: \`${baseline_db_intended_count}\`
- baseline_db_fills_count: \`${baseline_db_fills_count}\`

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

## Seeded fills (confirmations unblock helper)

- seeded_missing_fills: \`${seeded_missing_fills}\`
- seed_reason: \`${seed_reason}\`
- seed_target_ticket_id: \`${seed_target_ticket_id}\`
- seed_fills_intended_count: \`${seed_fills_intended_count}\`
- seed_fills_written_count: \`${seed_fills_written_count}\`
- fills_seed_json: \`${fills_seed_json}\`

## Confirmation

- confirmation artifacts:
  - \`${OUT_DIR}/confirmations/${CONFIRMATION_UUID}/confirmation.json\`
  - \`${OUT_DIR}/confirmations/${CONFIRMATION_UUID}/confirmation.md\`
  - \`${OUT_DIR}/confirmations/${CONFIRMATION_UUID}/fills_used.json\` (if TRADE)

## Logs

- stdout/stderr captured under: \`${OUT_DIR}/logs/\`
EOF

if [[ "$decision_type" == "NO_TRADE" ]]; then
  {
    echo ""
    echo "## Why NO_TRADE"
    echo ""
    if [[ -f "${OUT_DIR}/runs/no_trade_1400.json" ]]; then
      python3 - <<'PY' "${OUT_DIR}/runs/no_trade_1400.json"
import json
import sys

path = sys.argv[1]
j = json.loads(open(path, encoding="utf-8").read())

reasons = []
for r in (j.get("reasons") or []):
  code = str(r.get("code") or "")
  detail = str(r.get("detail") or "")
  reasons.append((code, detail))
reasons.sort(key=lambda x: (x[0], x[1]))

print("Blocking reasons (verbatim):")
for code, detail in reasons:
  print(f"- {code}: {detail}")

conf = None
for rc in (j.get("risk_checks") or []):
  if rc.get("name") == "confirmations":
    conf = rc.get("detail") or {}
    break

if conf is not None:
  latest = str(conf.get("latest_trade_ticket_id") or "")
  fills_count = conf.get("fills_count")
  intended_count = conf.get("intended_count")
  print("")
  print("Confirmations gate (if relevant):")
  print(f"- latest_trade_ticket_id: {latest}")
  print(f"- fills_count: {'' if fills_count is None else fills_count}")
  print(f"- intended_count: {'' if intended_count is None else intended_count}")
PY
    else
      echo "Blocking reasons (verbatim):"
      echo "- (missing \`${OUT_DIR}/runs/no_trade_1400.json\`)"
    fi
  } >>"${OUT_DIR}/README.md"
fi

echo "OK: ${OUT_DIR}"
