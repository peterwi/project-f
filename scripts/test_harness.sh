#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${ROOT_DIR}/config/secrets.env"
COMPOSE_FILE="${ROOT_DIR}/docker/compose.yml"

MARKET_CACHE_ROOT="/data/trading-ops/data/market"

usage() {
  cat <<'EOF'
Usage:
  bash scripts/test_harness.sh --date YYYY-MM-DD [--dryrun-trades] [--seed-missing-fills] [--confirm-no-trade] [--no-confirm-no-trade] [--out-root /data/trading-ops/artifacts/test_runs]

Defaults:
  --dryrun-trades: false
  --seed-missing-fills: false
  --confirm-no-trade: true
  --out-root: /data/trading-ops/artifacts/test_runs
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

psql_to_file() {
  local out="$1"
  local sql="$2"
  docker compose -f "$COMPOSE_FILE" --env-file "$ENV_FILE" exec -T postgres \
    psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -v ON_ERROR_STOP=1 -c "$sql" >"$out"
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
CONFIRM_NO_TRADE="true"
OUT_ROOT="/data/trading-ops/artifacts/test_runs"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --)
      shift
      ;;
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
    --confirm-no-trade)
      CONFIRM_NO_TRADE="true"
      shift
      ;;
    --no-confirm-no-trade)
      CONFIRM_NO_TRADE="false"
      shift
      ;;
    --out-root)
      OUT_ROOT="${2:-}"
      shift 2
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

OUT_DIR="${OUT_ROOT}/${DATE}/${UTCSTAMP}-git${GIT_SHORT}"
mkdir -p "${OUT_DIR}/"{logs,runs,tickets,reconcile,db,market_cache,inputs,confirmations,reports}
echo "${OUT_DIR}" >"${OUT_DIR}/_path.txt"
echo "date=${DATE}" >"${OUT_DIR}/ids.env"
set_kv "git_commit" "${GIT_COMMIT}"
set_kv "git_short" "${GIT_SHORT}"
set_kv "dryrun_trades" "${DRYRUN_TRADES}"
set_kv "seed_missing_fills" "${SEED_MISSING_FILLS}"
set_kv "confirm_no_trade" "${CONFIRM_NO_TRADE}"

# 1) repo sanity (strict clean tree)
dirty="$(git -c safe.directory=* -C "$ROOT_DIR" status --porcelain=v1 || true)"
if [[ -n "$dirty" ]]; then
  printf "%s\n" "$dirty" >"${OUT_DIR}/logs/git_status_dirty.txt"
  die "Repo is dirty; refusing. See ${OUT_DIR}/logs/git_status_dirty.txt"
fi

# 2) migrate + schema snapshot
run_capture "migrate" make migrate
run_capture "db_tables" make db-tables
copy_into "${OUT_DIR}/logs/db_tables.stdout.log" "${OUT_DIR}/db/db_tables.txt"

run_capture "runs_last_pre" make runs-last
copy_into "${OUT_DIR}/logs/runs_last_pre.stdout.log" "${OUT_DIR}/db/runs_last_pre.txt"

# Baseline: latest TRADE ticket fill readiness (for optional seeding)
baseline_trade_ticket_id="$(psql_capture "select coalesce(ticket_id::text,'') from tickets where ticket_type='TRADE' order by created_at desc limit 1;")"
baseline_intended="0"
baseline_fills="0"
if [[ -n "$baseline_trade_ticket_id" ]]; then
  baseline_counts="$(psql_capture "select (select count(*) from ledger_trades_intended where ticket_id='${baseline_trade_ticket_id}'::uuid)::text || '|' || (select count(*) from ledger_trades_fills where ticket_id='${baseline_trade_ticket_id}'::uuid)::text;")"
  baseline_intended="${baseline_counts%%|*}"
  baseline_fills="${baseline_counts##*|}"
fi
set_kv "baseline_trade_ticket_id" "${baseline_trade_ticket_id}"
set_kv "baseline_trade_intended_count" "${baseline_intended}"
set_kv "baseline_trade_fills_count" "${baseline_fills}"

# 3) 08:00 run
run_capture "run_0800" make run-0800 -- --asof-date "${DATE}"
RUN_0800_ID="$(extract_kv_from_file run_id "${OUT_DIR}/logs/run_0800.stdout.log")"
[[ -n "$RUN_0800_ID" ]] || die "Failed to capture 0800 run_id"
set_kv "run_0800_id" "${RUN_0800_ID}"
copy_into "${ARTIFACTS_DIR}/runs/${RUN_0800_ID}/run_summary.md" "${OUT_DIR}/runs/run_0800_summary.md"

run_capture "runs_last_post_0800" make runs-last
copy_into "${OUT_DIR}/logs/runs_last_post_0800.stdout.log" "${OUT_DIR}/db/runs_last_post_0800.txt"

# 4) verify market cache presence (manifest + small samples)
MARKET_PROVIDER="$(extract_env_var MARKET_PROVIDER)"
if [[ -z "$MARKET_PROVIDER" ]]; then
  MARKET_PROVIDER="stooq"
fi
MARKET_PROVIDER="$(echo "$MARKET_PROVIDER" | tr '[:upper:]' '[:lower:]')"
cache_dir="${MARKET_CACHE_ROOT}/${MARKET_PROVIDER}/${DATE}"
manifest="${cache_dir}/manifest.json"
prices_csv="${cache_dir}/prices_eod.csv"
[[ -f "$manifest" ]] || die "Missing market cache manifest: ${manifest}"
[[ -f "$prices_csv" ]] || die "Missing market cache prices file: ${prices_csv}"

copy_into "$manifest" "${OUT_DIR}/market_cache/manifest.json"
head -n 40 "$prices_csv" >"${OUT_DIR}/market_cache/prices_eod.head.csv"
tail -n 40 "$prices_csv" >"${OUT_DIR}/market_cache/prices_eod.tail.csv"
sha_manifest="$(sha256sum "$manifest" | awk '{print $1}')"
sha_prices_head="$(sha256sum "${OUT_DIR}/market_cache/prices_eod.head.csv" | awk '{print $1}')"
sha_prices_tail="$(sha256sum "${OUT_DIR}/market_cache/prices_eod.tail.csv" | awk '{print $1}')"
set_kv "market_provider" "${MARKET_PROVIDER}"
set_kv "market_cache_dir" "${cache_dir}"
set_kv "market_manifest_sha256" "${sha_manifest}"
set_kv "prices_head_sha256" "${sha_prices_head}"
set_kv "prices_tail_sha256" "${sha_prices_tail}"

# 5) data-quality gate (explicit asof-date)
run_capture "data_quality" make data-quality -- --asof-date "${DATE}"
dq_report_path="$(awk '/^Wrote /{print $2}' "${OUT_DIR}/logs/data_quality.stdout.log" | tail -n 1)"
[[ -n "$dq_report_path" ]] || die "Failed to locate data_quality report path"
copy_into "$dq_report_path" "${OUT_DIR}/reports/data_quality.md"
set_kv "data_quality_report_path" "${dq_report_path}"

# 6) reconciliation pre-14:00 (deterministic: snapshot from current ledger if available, else 0 cash + empty)
ledger_cash="$(psql_capture "select coalesce(cash_base::text,'') from ledger_cash_current;")"
if [[ -z "$ledger_cash" ]]; then
  ledger_cash="0"
fi
ledger_positions_raw="$(psql_capture "select internal_symbol || '=' || units::text from ledger_positions_current order by internal_symbol;")"
set_kv "reconcile_pre_cash_gbp" "${ledger_cash}"

reconcile_args=(-- --snapshot-date "${DATE}" --cash-gbp "${ledger_cash}" --notes "TEST_HARNESS_PRE_1400:${DATE}")
if [[ -n "$ledger_positions_raw" ]]; then
  while IFS= read -r line; do
    [[ -n "$line" ]] || continue
    reconcile_args+=(--position "$line")
  done <<<"$ledger_positions_raw"
fi
run_capture "reconcile_pre_1400" make reconcile-daily "${reconcile_args[@]}"
SNAPSHOT_PRE_ID="$(extract_kv_from_file snapshot_id "${OUT_DIR}/logs/reconcile_pre_1400.stdout.log")"
REPORT_PRE_PATH="$(awk '/^Wrote /{print $2}' "${OUT_DIR}/logs/reconcile_pre_1400.stdout.log" | tail -n 1)"
[[ -n "$SNAPSHOT_PRE_ID" && -n "$REPORT_PRE_PATH" ]] || die "Failed to capture pre-14:00 reconciliation outputs"
set_kv "reconcile_snapshot_pre_id" "${SNAPSHOT_PRE_ID}"
set_kv "reconcile_report_pre_path" "${REPORT_PRE_PATH}"
copy_into "${REPORT_PRE_PATH}" "${OUT_DIR}/reconcile/reconcile_pre_1400.md"

# Optional: seed missing fills for the latest TRADE ticket (unblocks confirmations gate deterministically).
seeded_missing_fills="false"
seed_target_ticket_id=""
seed_before_intended="0"
seed_before_fills="0"
seed_after_intended="0"
seed_after_fills="0"
fills_seed_json=""
if [[ "$SEED_MISSING_FILLS" == "true" && -n "$baseline_trade_ticket_id" && "${baseline_intended}" != "0" && "${baseline_fills}" != "${baseline_intended}" ]]; then
  seed_target_ticket_id="${baseline_trade_ticket_id}"
  seed_before_intended="${baseline_intended}"
  seed_before_fills="${baseline_fills}"
  fills_seed_json="${OUT_DIR}/inputs/fills_seed.json"

  intended_rows="$(psql_capture "select sequence::text || '|' || internal_symbol || '|' || side || '|' || coalesce(units::text,'') || '|' || coalesce(notional_value_base::text,'') || '|' || coalesce(limit_price::text, reference_price::text,'') from ledger_trades_intended where ticket_id = '${seed_target_ticket_id}'::uuid order by sequence;")"
  [[ -n "$intended_rows" ]] || die "seed-missing-fills: no ledger_trades_intended rows found for ticket_id=${seed_target_ticket_id}"
  INTENDED_ROWS="$intended_rows" python3 - <<'PY' "${fills_seed_json}" "${seed_target_ticket_id}"
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

  run_capture "seed_missing_fills_confirm" env FILLS_JSON="${fills_seed_json}" make confirm-fills -- --ticket-id "${seed_target_ticket_id}" --submitted-by "test_harness" --notes "SEEDED_BY_TEST_HARNESS"
  seed_counts_after="$(psql_capture "select (select count(*) from ledger_trades_intended where ticket_id='${seed_target_ticket_id}'::uuid)::text || '|' || (select count(*) from ledger_trades_fills where ticket_id='${seed_target_ticket_id}'::uuid)::text;")"
  seed_after_intended="${seed_counts_after%%|*}"
  seed_after_fills="${seed_counts_after##*|}"
  seeded_missing_fills="true"
fi
set_kv "seeded_missing_fills" "${seeded_missing_fills}"
set_kv "seed_target_ticket_id" "${seed_target_ticket_id}"
set_kv "seed_before_intended" "${seed_before_intended}"
set_kv "seed_before_fills" "${seed_before_fills}"
set_kv "seed_after_intended" "${seed_after_intended}"
set_kv "seed_after_fills" "${seed_after_fills}"

# 7) 14:00 run
if [[ "$DRYRUN_TRADES" == "true" ]]; then
  run_capture "run_1400" env DRYRUN_TRADES=true make run-1400 -- --asof-date "${DATE}"
else
  run_capture "run_1400" make run-1400 -- --asof-date "${DATE}"
fi
RUN_1400_ID="$(extract_kv_from_file run_id "${OUT_DIR}/logs/run_1400.stdout.log")"
[[ -n "$RUN_1400_ID" ]] || die "Failed to capture 1400 run_id"
set_kv "run_1400_id" "${RUN_1400_ID}"

copy_into "${ARTIFACTS_DIR}/runs/${RUN_1400_ID}/run_summary.md" "${OUT_DIR}/runs/run_1400_summary.md"
copy_into "${ARTIFACTS_DIR}/runs/${RUN_1400_ID}/no_trade.json" "${OUT_DIR}/runs/no_trade_1400.json"
copy_into "${ARTIFACTS_DIR}/runs/${RUN_1400_ID}/trades_proposed.json" "${OUT_DIR}/runs/trades_proposed_1400.json"
copy_into "${ARTIFACTS_DIR}/runs/${RUN_1400_ID}/trades_intended.json" "${OUT_DIR}/runs/trades_intended_1400.json"

# 8) ticket generation proof (must match run_1400_id)
run_capture "tickets_last" make tickets-last
copy_into "${OUT_DIR}/logs/tickets_last.stdout.log" "${OUT_DIR}/db/tickets_last.txt"

TICKET_1400_ID="$(psql_capture "select coalesce(ticket_id::text,'') from tickets where run_id='${RUN_1400_ID}'::uuid;")"
[[ -n "$TICKET_1400_ID" ]] || die "Failed to resolve ticket_id for run_id=${RUN_1400_ID}"
set_kv "ticket_1400_id" "${TICKET_1400_ID}"

ticket_dir="${ARTIFACTS_DIR}/tickets/${TICKET_1400_ID}"
copy_into "${ticket_dir}/ticket.md" "${OUT_DIR}/tickets/ticket_1400.md"
copy_into "${ticket_dir}/ticket.json" "${OUT_DIR}/tickets/ticket_1400.json"
copy_into "${ticket_dir}/material_hash.txt" "${OUT_DIR}/tickets/material_hash_1400.txt"

decision_type="$(python3 -c "import json; print((json.load(open('${OUT_DIR}/tickets/ticket_1400.json', encoding='utf-8')) or {}).get('decision_type',''))")"
[[ -n "$decision_type" ]] || die "README cannot determine decision_type (ticket_1400.json missing decision_type)"
set_kv "ticket_1400_decision_type" "${decision_type}"

material_hash_1400="$(cat "${OUT_DIR}/tickets/material_hash_1400.txt" | tr -d '\n' || true)"
[[ -n "$material_hash_1400" ]] || die "Missing material_hash_1400"
set_kv "material_hash_1400" "${material_hash_1400}"

# Determinism proof: rerender ticket for same run_id and ensure material_hash stays identical.
mh_before="$(cat "${ticket_dir}/material_hash.txt" | tr -d '\n' || true)"
sha_before="$(sha256sum "${ticket_dir}/ticket.json" | awk '{print $1}')"
run_capture "ticket_rerender_proof" python3 scripts/ticket_render.py --run-id "${RUN_1400_ID}"
mh_after="$(cat "${ticket_dir}/material_hash.txt" | tr -d '\n' || true)"
sha_after="$(sha256sum "${ticket_dir}/ticket.json" | awk '{print $1}')"
{
  echo "ticket_id=${TICKET_1400_ID}"
  echo "run_id=${RUN_1400_ID}"
  echo "material_hash_before=${mh_before}"
  echo "material_hash_after=${mh_after}"
  echo "ticket_json_sha256_before=${sha_before}"
  echo "ticket_json_sha256_after=${sha_after}"
} >"${OUT_DIR}/db/ticket_determinism.txt"
if [[ "$mh_before" != "$mh_after" ]]; then
  die "Ticket determinism proof failed (material_hash changed). See ${OUT_DIR}/db/ticket_determinism.txt"
fi

# 9) optional confirmation step (default: confirm NO_TRADE only)
CONFIRMATION_UUID=""
CONFIRMATION_DIR=""
if [[ "$decision_type" == "NO_TRADE" && "$CONFIRM_NO_TRADE" == "true" ]]; then
  run_capture "confirm_no_trade" python3 scripts/confirmations_submit.py --ticket-id "${TICKET_1400_ID}" --ack-no-trade --submitted-by "test_harness" --notes "TEST_HARNESS:${DATE}"
  CONFIRMATION_UUID="$(extract_kv_from_file confirmation_uuid "${OUT_DIR}/logs/confirm_no_trade.stdout.log")"
  CONFIRMATION_DIR="$(extract_kv_from_file confirmation_dir "${OUT_DIR}/logs/confirm_no_trade.stdout.log")"
  [[ -n "$CONFIRMATION_UUID" && -n "$CONFIRMATION_DIR" ]] || die "Failed to capture confirmation details"
  set_kv "confirmation_uuid" "${CONFIRMATION_UUID}"
  mkdir -p "${OUT_DIR}/confirmations/${CONFIRMATION_UUID}"
  copy_into "${CONFIRMATION_DIR}/confirmation.json" "${OUT_DIR}/confirmations/${CONFIRMATION_UUID}/confirmation.json"
  copy_into "${CONFIRMATION_DIR}/confirmation.md" "${OUT_DIR}/confirmations/${CONFIRMATION_UUID}/confirmation.md"
else
  set_kv "confirmation_uuid" ""
fi

# 10) reconciliation final
ledger_cash_final="$(psql_capture "select coalesce(cash_base::text,'') from ledger_cash_current;")"
if [[ -z "$ledger_cash_final" ]]; then
  ledger_cash_final="0"
fi
ledger_positions_final_raw="$(psql_capture "select internal_symbol || '=' || units::text from ledger_positions_current order by internal_symbol;")"
reconcile_final_args=(-- --snapshot-date "${DATE}" --cash-gbp "${ledger_cash_final}" --notes "TEST_HARNESS_FINAL:${DATE}")
if [[ -n "$ledger_positions_final_raw" ]]; then
  while IFS= read -r line; do
    [[ -n "$line" ]] || continue
    reconcile_final_args+=(--position "$line")
  done <<<"$ledger_positions_final_raw"
fi
run_capture "reconcile_final" make reconcile-daily "${reconcile_final_args[@]}"
SNAPSHOT_FINAL_ID="$(extract_kv_from_file snapshot_id "${OUT_DIR}/logs/reconcile_final.stdout.log")"
REPORT_FINAL_PATH="$(awk '/^Wrote /{print $2}' "${OUT_DIR}/logs/reconcile_final.stdout.log" | tail -n 1)"
[[ -n "$SNAPSHOT_FINAL_ID" && -n "$REPORT_FINAL_PATH" ]] || die "Failed to capture final reconciliation outputs"
set_kv "reconcile_snapshot_final_id" "${SNAPSHOT_FINAL_ID}"
set_kv "reconcile_report_final_path" "${REPORT_FINAL_PATH}"
copy_into "${REPORT_FINAL_PATH}" "${OUT_DIR}/reconcile/reconcile_final.md"

# DB proofs (schema-safe, no select *)
run_capture "runs_last" make runs-last
copy_into "${OUT_DIR}/logs/runs_last.stdout.log" "${OUT_DIR}/db/runs_last.txt"

psql_to_file "${OUT_DIR}/db/counts.txt" "
select 'market_prices_eod_rows_for_date' as metric, count(*)::bigint as value
from market_prices_eod where trading_date = '${DATE}';
select 'signals_ranked_rows_for_date' as metric, count(*)::bigint as value
from signals_ranked where asof_date = '${DATE}';
select 'reconcile_latest' as metric,
       coalesce(to_char(r.evaluated_at at time zone 'utc','YYYY-MM-DD\"T\"HH24:MI:SS\"Z\"'),'') ||
       ' passed=' || r.passed::text ||
       ' snapshot_date=' || coalesce(s.snapshot_date::text,'') ||
       ' report_path=' || coalesce(r.report_path,'') as value
from reconciliation_results r
join reconciliation_snapshots s on s.snapshot_id = r.snapshot_id
order by r.evaluated_at desc
limit 3;
"

# Deterministic README
why_no_trade_json=""
if [[ -f "${OUT_DIR}/runs/no_trade_1400.json" ]]; then
  why_no_trade_json="$(python3 -c "import json; j=json.load(open('${OUT_DIR}/runs/no_trade_1400.json', encoding='utf-8')); print(json.dumps(j.get('reasons') or [], indent=2, sort_keys=True))" || true)"
fi

cat >"${OUT_DIR}/README.md" <<EOF
# Test Harness Report

## Summary

- date: \`${DATE}\`
- git_commit: \`${GIT_COMMIT}\`
- out_dir: \`${OUT_DIR}\`
- dryrun_trades (14:00): \`${DRYRUN_TRADES}\`
- seed_missing_fills: \`${SEED_MISSING_FILLS}\`
- confirm_no_trade: \`${CONFIRM_NO_TRADE}\`

## IDs

- run_0800_id: \`${RUN_0800_ID}\`
- run_1400_id: \`${RUN_1400_ID}\`
- ticket_1400_id: \`${TICKET_1400_ID}\`
- confirmation_uuid: \`${CONFIRMATION_UUID}\`
- reconcile_snapshot_pre_id: \`${SNAPSHOT_PRE_ID}\`
- reconcile_snapshot_final_id: \`${SNAPSHOT_FINAL_ID}\`

## Decision

- decision_type: \`${decision_type}\`
- material_hash: \`${material_hash_1400}\`

## Data proofs

- market_cache_manifest: \`${cache_dir}/manifest.json\` (sha256=\`${sha_manifest}\`)
- prices_eod.csv samples:
  - head: \`market_cache/prices_eod.head.csv\` (sha256=\`${sha_prices_head}\`)
  - tail: \`market_cache/prices_eod.tail.csv\` (sha256=\`${sha_prices_tail}\`)

## Artifacts copied

- runs:
  - \`runs/run_0800_summary.md\`
  - \`runs/run_1400_summary.md\`
- ticket (14:00):
  - \`tickets/ticket_1400.md\`
  - \`tickets/ticket_1400.json\`
  - \`tickets/material_hash_1400.txt\`
- reconcile:
  - \`reconcile/reconcile_pre_1400.md\`
  - \`reconcile/reconcile_final.md\`
- db proofs:
  - \`db/db_tables.txt\`
  - \`db/runs_last.txt\`
  - \`db/tickets_last.txt\`
  - \`db/counts.txt\`
  - \`db/ticket_determinism.txt\`

## Why NO_TRADE (verbatim)

EOF

if [[ "$decision_type" == "NO_TRADE" ]]; then
  {
    echo '```json'
    echo "${why_no_trade_json:-[]}"
    echo '```'
    echo ""
  } >>"${OUT_DIR}/README.md"
else
  echo "- decision_type is TRADE; harness does not auto-confirm trade fills." >>"${OUT_DIR}/README.md"
  echo "" >>"${OUT_DIR}/README.md"
fi

cat >>"${OUT_DIR}/README.md" <<'EOF'
## Logs

- All stdout/stderr captured under `logs/` (start with `logs/*.stderr.log` on failures).
EOF

# Invariants + structure verification
run_capture "verify" python3 scripts/test_harness_verify.py --out-dir "${OUT_DIR}"

echo "OK: ${OUT_DIR}"
