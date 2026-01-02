#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${ROOT_DIR}/config/secrets.env"
COMPOSE_FILE="${ROOT_DIR}/docker/compose.yml"

usage() {
  cat <<'EOF'
Usage:
  scripts/db_restore.sh [--file /path/to/backup.dump] [--to-db DBNAME]

Notes:
  - Restores into an existing database using pg_restore.
  - Requires RESTORE_CONFIRM=YES in the environment.
EOF
}

backup_file=""
target_db=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --file)
      backup_file="${2:-}"; shift 2;;
    --to-db)
      target_db="${2:-}"; shift 2;;
    -h|--help)
      usage; exit 0;;
    *)
      echo "Unknown arg: $1"; usage; exit 2;;
  esac
done

if [[ "${RESTORE_CONFIRM:-NO}" != "YES" ]]; then
  echo "Refusing to restore without RESTORE_CONFIRM=YES"
  exit 2
fi

if [[ ! -f "${ENV_FILE}" ]]; then
  echo "Missing ${ENV_FILE}"
  exit 2
fi

set -a
source "${ENV_FILE}"
set +a

: "${POSTGRES_USER:?Missing POSTGRES_USER in config/secrets.env}"
: "${POSTGRES_PASSWORD:?Missing POSTGRES_PASSWORD in config/secrets.env}"
: "${POSTGRES_DB:?Missing POSTGRES_DB in config/secrets.env}"
POSTGRES_BACKUP_DIR="${POSTGRES_BACKUP_DIR:-/data/trading-ops/backups/postgres}"
CONTAINER_BACKUP_DIR="/backups"

if [[ -z "${target_db}" ]]; then
  target_db="${POSTGRES_DB}"
fi

if [[ -z "${backup_file}" ]]; then
  backup_file="$(ls -1 "${POSTGRES_BACKUP_DIR}"/*.dump 2>/dev/null | sort | tail -n 1 || true)"
fi

if [[ -z "${backup_file}" || ! -f "${backup_file}" ]]; then
  echo "Backup file not found. Provide --file or ensure ${POSTGRES_BACKUP_DIR} contains *.dump"
  exit 2
fi

if [[ "${backup_file}" == */* ]]; then
  if [[ "${backup_file}" != "${POSTGRES_BACKUP_DIR%/}/"* ]]; then
    echo "Refusing to restore from outside POSTGRES_BACKUP_DIR (${POSTGRES_BACKUP_DIR})"
    exit 2
  fi
  backup_base="$(basename "${backup_file}")"
else
  backup_base="${backup_file}"
fi

container_backup_file="${CONTAINER_BACKUP_DIR}/${backup_base}"

echo "Restoring ${backup_base} -> database ${target_db}"

DC=(docker compose -f "${COMPOSE_FILE}" --env-file "${ENV_FILE}")

"${DC[@]}" exec -T postgres sh -lc "test -f '${container_backup_file}'"
"${DC[@]}" exec -T postgres pg_restore -U "${POSTGRES_USER}" -d "${target_db}" --no-owner --no-privileges "${container_backup_file}"

echo "OK"
