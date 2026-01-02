#!/usr/bin/env python3
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ENV_FILE = ROOT / "config" / "secrets.env"
COMPOSE_FILE = ROOT / "docker" / "compose.yml"
UNIVERSE_CSV = ROOT / "config" / "universe.csv"


def _read_env_file(path: Path) -> dict[str, str]:
    env: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise ValueError(f"Invalid env line (no '='): {raw_line}")
        key, value = line.split("=", 1)
        env[key.strip()] = value.strip()
    return env


def _run_psql(script: str) -> None:
    if not ENV_FILE.exists():
        raise FileNotFoundError(f"Missing {ENV_FILE}; create it from config/secrets.env.example")
    if not COMPOSE_FILE.exists():
        raise FileNotFoundError(f"Missing {COMPOSE_FILE}")
    if not UNIVERSE_CSV.exists():
        raise FileNotFoundError(f"Missing {UNIVERSE_CSV}")

    env = _read_env_file(ENV_FILE)
    user = env.get("POSTGRES_USER", "").strip()
    db = env.get("POSTGRES_DB", "").strip()
    if not user or not db:
        raise ValueError("POSTGRES_USER and POSTGRES_DB must be set in config/secrets.env")

    cmd = [
        "docker",
        "compose",
        "-f",
        str(COMPOSE_FILE),
        "--env-file",
        str(ENV_FILE),
        "exec",
        "-T",
        "postgres",
        "psql",
        "-U",
        user,
        "-d",
        db,
        "-v",
        "ON_ERROR_STOP=1",
    ]
    subprocess.run(cmd, input=script.encode("utf-8"), check=True)


def main() -> int:
    psql_script = r"""
BEGIN;

CREATE TEMP TABLE universe_stage (
  internal_symbol text NOT NULL,
  stooq_symbol text,
  yahoo_symbol text,
  etoro_search_name text,
  currency text,
  instrument_type text,
  tradable_underlying boolean,
  enabled boolean,
  notes text
);

\copy universe_stage (internal_symbol, stooq_symbol, yahoo_symbol, etoro_search_name, currency, instrument_type, tradable_underlying, enabled, notes) FROM '/app/config/universe.csv' WITH (FORMAT csv, HEADER true);

-- Hard fail if duplicates exist in the CSV.
DO $$
DECLARE dup_count integer;
BEGIN
  SELECT count(*) INTO dup_count
  FROM (
    SELECT internal_symbol
    FROM universe_stage
    GROUP BY internal_symbol
    HAVING count(*) > 1
  ) d;

  IF dup_count > 0 THEN
    RAISE EXCEPTION 'Duplicate internal_symbol values found in config/universe.csv';
  END IF;
END $$;

INSERT INTO config_universe (
  internal_symbol,
  stooq_symbol,
  yahoo_symbol,
  etoro_search_name,
  currency,
  instrument_type,
  tradable_underlying,
  enabled,
  notes
)
SELECT
  btrim(internal_symbol),
  nullif(btrim(stooq_symbol), ''),
  nullif(btrim(yahoo_symbol), ''),
  nullif(btrim(etoro_search_name), ''),
  nullif(btrim(currency), ''),
  nullif(btrim(instrument_type), ''),
  coalesce(tradable_underlying, true),
  coalesce(enabled, false),
  nullif(btrim(notes), '')
FROM universe_stage
ON CONFLICT (internal_symbol) DO UPDATE SET
  stooq_symbol = excluded.stooq_symbol,
  yahoo_symbol = excluded.yahoo_symbol,
  etoro_search_name = excluded.etoro_search_name,
  currency = excluded.currency,
  instrument_type = excluded.instrument_type,
  tradable_underlying = excluded.tradable_underlying,
  enabled = excluded.enabled,
  notes = excluded.notes;

COMMIT;

SELECT
  count(*) AS total_rows,
  sum(CASE WHEN enabled THEN 1 ELSE 0 END) AS enabled_rows
FROM config_universe;
"""
    _run_psql(psql_script)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except subprocess.CalledProcessError as e:
        print(f"ERROR: psql failed: {e}", file=sys.stderr)
        raise SystemExit(1)
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        raise SystemExit(2)
