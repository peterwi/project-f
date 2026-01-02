#!/usr/bin/env bash
set -euo pipefail

# PLAN.md compatibility wrapper (canonical implementation lives in scripts/db_restore.sh)
exec "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/db_restore.sh" "$@"

