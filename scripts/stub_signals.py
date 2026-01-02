#!/usr/bin/env python3
from __future__ import annotations

import argparse
import subprocess
import sys
from datetime import date
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ENV_FILE = ROOT / "config" / "secrets.env"
COMPOSE_FILE = ROOT / "docker" / "compose.yml"


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


def _docker_compose_base() -> list[str]:
    return ["docker", "compose", "-f", str(COMPOSE_FILE), "--env-file", str(ENV_FILE)]


def _psql_capture(sql: str) -> str:
    env = _read_env_file(ENV_FILE)
    user = env.get("POSTGRES_USER", "").strip()
    db = env.get("POSTGRES_DB", "").strip()
    if not user or not db:
        raise ValueError("POSTGRES_USER and POSTGRES_DB must be set in config/secrets.env")
    cmd = _docker_compose_base() + [
        "exec",
        "-T",
        "postgres",
        "psql",
        "-q",
        "-U",
        user,
        "-d",
        db,
        "-v",
        "ON_ERROR_STOP=1",
        "-tA",
        "-c",
        sql,
    ]
    result = subprocess.run(cmd, check=True, capture_output=True, text=True)
    return result.stdout.strip()


def _psql_exec(sql: str) -> None:
    env = _read_env_file(ENV_FILE)
    user = env.get("POSTGRES_USER", "").strip()
    db = env.get("POSTGRES_DB", "").strip()
    if not user or not db:
        raise ValueError("POSTGRES_USER and POSTGRES_DB must be set in config/secrets.env")
    cmd = _docker_compose_base() + [
        "exec",
        "-T",
        "postgres",
        "psql",
        "-q",
        "-U",
        user,
        "-d",
        db,
        "-v",
        "ON_ERROR_STOP=1",
    ]
    subprocess.run(cmd, input=sql.encode("utf-8"), check=True)


def _latest_ops_run_id() -> str:
    run_id = _psql_capture("select run_id from runs where cadence = 'ops' order by started_at desc limit 1;")
    if not run_id:
        raise RuntimeError("No runs found. Run: make run-ops")
    return run_id


def main() -> int:
    parser = argparse.ArgumentParser(description="Write deterministic stub signals into signals_ranked.")
    parser.add_argument("--run-id", help="Target run_id (defaults to latest ops run).")
    args = parser.parse_args()

    if not ENV_FILE.exists():
        raise FileNotFoundError(f"Missing {ENV_FILE}; create it from config/secrets.env.example")

    run_id = args.run_id or _latest_ops_run_id()

    asof = _psql_capture(
        f"""
        select asof_date::text
        from data_quality_reports
        where run_id = '{run_id}'
        order by generated_at desc
        limit 1;
        """
    )
    if not asof:
        asof = _psql_capture(f"select coalesce(asof_date::text,'') from runs where run_id = '{run_id}';")
    if not asof:
        raise RuntimeError("Missing asof_date for run; run data-quality gate first.")
    date.fromisoformat(asof)

    enabled_symbols_raw = _psql_capture(
        """
        select coalesce(string_agg(internal_symbol, ',' order by internal_symbol), '')
        from config_universe
        where enabled = true
          and lower(coalesce(instrument_type,'')) = 'stock'
          and tradable_underlying = true;
        """
    )
    enabled_symbols = [s for s in enabled_symbols_raw.split(",") if s] if enabled_symbols_raw else []
    if not enabled_symbols:
        raise RuntimeError("No enabled tradable symbols; enable at least one stock in config/universe.csv.")

    # Deterministic stub: rank by alphabetical internal_symbol.
    values: list[str] = []
    for i, sym in enumerate(enabled_symbols, start=1):
        score = 1.0 / float(i)
        values.append(f"('{run_id}','{asof}','{sym}',{score},{i},'stub_v1')")

    _psql_exec(
        f"""
        insert into signals_ranked(run_id, asof_date, internal_symbol, score, rank, model_version)
        values {", ".join(values)}
        on conflict (run_id, internal_symbol) do update set
          asof_date = excluded.asof_date,
          score = excluded.score,
          rank = excluded.rank,
          model_version = excluded.model_version;
        """
    )

    count = _psql_capture(f"select count(*) from signals_ranked where run_id = '{run_id}';") or "0"
    print(f"run_id={run_id}")
    print(f"asof_date={asof}")
    print(f"signals_written={count}")
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

