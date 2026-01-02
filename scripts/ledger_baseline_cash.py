#!/usr/bin/env python3
from __future__ import annotations

import argparse
import subprocess
import sys
from decimal import Decimal, InvalidOperation
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


def _parse_money(value: str) -> Decimal:
    v = value.strip().replace(",", "")
    if v.startswith("+"):
        v = v[1:].strip()
    try:
        d = Decimal(v)
    except InvalidOperation:
        raise ValueError(f"Invalid cash amount: {value!r}")
    if d.is_nan() or d.is_infinite():
        raise ValueError(f"Invalid cash amount: {value!r}")
    return d


def main() -> int:
    parser = argparse.ArgumentParser(description="Create a single idempotent BASELINE cash movement (GBP).")
    parser.add_argument("--cash-gbp", required=True, help="eToro Available cash (GBP) at ledger bootstrap.")
    args = parser.parse_args()

    if not ENV_FILE.exists():
        raise FileNotFoundError(f"Missing {ENV_FILE}; create it from config/secrets.env.example")

    cash = _parse_money(args.cash_gbp)

    existing = _psql_capture(
        """
        select count(*)::text || '|' || coalesce(min(amount_base)::text,'') || '|' || coalesce(max(amount_base)::text,'')
        from ledger_cash_movements
        where movement_type = 'BASELINE';
        """
    )
    if not existing:
        raise RuntimeError("Failed to query existing BASELINE cash movements.")
    count_s, min_s, max_s = existing.split("|", 2)
    count = int(count_s or "0")

    if count == 1:
        existing_amount = Decimal(min_s)
        if existing_amount == cash:
            print(f"baseline_exists=true amount_gbp={existing_amount}")
            return 0
        raise RuntimeError(
            f"BASELINE already exists with amount {existing_amount} GBP (refusing to change to {cash} GBP). "
            "If cash changed, record a separate cash movement instead."
        )
    if count > 1:
        raise RuntimeError("Multiple BASELINE rows exist; expected exactly one. Fix ledger_cash_movements manually.")

    _psql_exec(
        f"""
        insert into ledger_cash_movements(occurred_at, amount_base, base_currency, movement_type, notes)
        values (now(), {cash}, 'GBP', 'BASELINE', 'INITIAL_AVAILABLE_CASH');
        """
    )
    print(f"baseline_created=true amount_gbp={cash}")
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

