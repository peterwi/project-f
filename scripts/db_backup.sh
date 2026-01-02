#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${ROOT_DIR}/config/secrets.env"
COMPOSE_FILE="${ROOT_DIR}/docker/compose.yml"

if [[ ! -f "${ENV_FILE}" ]]; then
  echo "Missing ${ENV_FILE}"
  echo "Create it with: cp config/secrets.env.example config/secrets.env"
  exit 2
fi

set -a
source "${ENV_FILE}"
set +a

: "${POSTGRES_USER:?Missing POSTGRES_USER in config/secrets.env}"
: "${POSTGRES_PASSWORD:?Missing POSTGRES_PASSWORD in config/secrets.env}"
: "${POSTGRES_DB:?Missing POSTGRES_DB in config/secrets.env}"

RETENTION_DAYS="${POSTGRES_BACKUP_RETENTION_DAYS:-30}"

backup_dir="${POSTGRES_BACKUP_DIR:-/data/trading-ops/backups/postgres}"
if [[ "${backup_dir}" != /data/* ]]; then
  echo "Refusing to write backups outside /data (POSTGRES_BACKUP_DIR=${backup_dir})"
  exit 2
fi

mkdir -p "${backup_dir}"

ts="$(date -u +%Y%m%dT%H%M%SZ)"
outfile="${backup_dir}/${POSTGRES_DB}_${ts}.dump"

DC=(docker compose -f "${COMPOSE_FILE}" --env-file "${ENV_FILE}")

echo "Writing backup: ${outfile}"
"${DC[@]}" exec -T postgres pg_dump -U "${POSTGRES_USER}" -d "${POSTGRES_DB}" -Fc > "${outfile}"
sha256sum "${outfile}" > "${outfile}.sha256"

echo "Enforcing retention: ${RETENTION_DAYS} days"
find "${backup_dir}" -maxdepth 1 -type f -name "*.dump" -mtime "+${RETENTION_DAYS}" -print -delete || true
find "${backup_dir}" -maxdepth 1 -type f -name "*.dump.sha256" -mtime "+${RETENTION_DAYS}" -print -delete || true

echo "OK"
