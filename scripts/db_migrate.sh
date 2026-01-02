#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${ROOT_DIR}/config/secrets.env"
COMPOSE_FILE="${ROOT_DIR}/docker/compose.yml"
MIGRATIONS_DIR="${ROOT_DIR}/db/migrations"

if [[ ! -f "${ENV_FILE}" ]]; then
  echo "Missing ${ENV_FILE}"
  echo "Create it with: cp config/secrets.env.example config/secrets.env"
  exit 2
fi

if [[ ! -d "${MIGRATIONS_DIR}" ]]; then
  echo "Missing migrations dir: ${MIGRATIONS_DIR}"
  exit 2
fi

set -a
source "${ENV_FILE}"
set +a

if [[ -z "${POSTGRES_USER:-}" || -z "${POSTGRES_DB:-}" || -z "${POSTGRES_PASSWORD:-}" ]]; then
  echo "POSTGRES_USER/POSTGRES_DB/POSTGRES_PASSWORD must be set in ${ENV_FILE}"
  exit 2
fi

DC=(docker compose -f "${COMPOSE_FILE}" --env-file "${ENV_FILE}")

echo "Ensuring schema_migrations table exists..."
"${DC[@]}" exec -T postgres psql -U "${POSTGRES_USER}" -d "${POSTGRES_DB}" -v ON_ERROR_STOP=1 <<'SQL'
CREATE TABLE IF NOT EXISTS schema_migrations (
  filename text PRIMARY KEY,
  applied_at timestamptz NOT NULL DEFAULT now()
);
SQL

shopt -s nullglob
migrations=( "${MIGRATIONS_DIR}"/*.sql )
if (( ${#migrations[@]} == 0 )); then
  echo "No migrations found in ${MIGRATIONS_DIR}"
  exit 2
fi

applied_any=0

for file in "${migrations[@]}"; do
  base="$(basename "${file}")"
  already="$("${DC[@]}" exec -T postgres psql -U "${POSTGRES_USER}" -d "${POSTGRES_DB}" -tA -v ON_ERROR_STOP=1 \
    -c "select 1 from schema_migrations where filename = '${base}' limit 1;")"

  if [[ "${already}" == "1" ]]; then
    echo "SKIP  ${base}"
    continue
  fi

  echo "APPLY ${base}"
  "${DC[@]}" exec -T postgres psql -U "${POSTGRES_USER}" -d "${POSTGRES_DB}" -v ON_ERROR_STOP=1 < "${file}"
  "${DC[@]}" exec -T postgres psql -U "${POSTGRES_USER}" -d "${POSTGRES_DB}" -v ON_ERROR_STOP=1 \
    -c "insert into schema_migrations(filename) values ('${base}');"
  applied_any=1
done

if [[ "${applied_any}" == "0" ]]; then
  echo "No migrations applied (already up to date)."
fi

