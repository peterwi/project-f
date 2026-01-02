#!/usr/bin/env python3
from __future__ import annotations

import subprocess
import sys
from datetime import datetime, timezone
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


def main() -> int:
    if not ENV_FILE.exists():
        raise FileNotFoundError(f"Missing {ENV_FILE}; create it from config/secrets.env.example")

    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    marker = f"SELFTEST_{ts}"

    # Create minimal ledger state: +1000 cash, BUY AAPL £100, units 0.5
    run_id = _psql_capture(
        f"insert into runs(config_hash, git_commit, notes, status) values ('{marker}','{marker}','{marker}','selftest') returning run_id;"
    )
    ticket_id = _psql_capture(
        f"insert into tickets(run_id, ticket_type, status, rendered_md) values ('{run_id}','SELFTEST','DRAFT','{marker}') returning ticket_id;"
    )
    _psql_exec(
        f"""
        insert into ledger_cash_movements(occurred_at, amount_base, base_currency, movement_type, notes)
        values (now(), 1000, 'GBP', 'SELFTEST', '{marker}');
        insert into ledger_trades_fills(ticket_id, sequence, internal_symbol, side, executed_status, executed_value_base, units, fill_price, filled_at, notes)
        values ('{ticket_id}', 1, 'AAPL', 'BUY', 'DONE', 100, 0.5, 200, now(), '{marker}');
        """
    )

    # Snapshot that matches: cash 900, AAPL 0.5
    snapshot_ok = _psql_capture(
        f"insert into reconciliation_snapshots(snapshot_date, base_currency, cash_base, notes) values (current_date, 'GBP', 900, '{marker}_OK') returning snapshot_id;"
    )
    _psql_exec(
        f"insert into reconciliation_snapshot_positions(snapshot_id, internal_symbol, units) values ('{snapshot_ok}','AAPL',0.5);"
    )

    # Snapshot that fails: cash 800, AAPL 0.5
    snapshot_fail = _psql_capture(
        f"insert into reconciliation_snapshots(snapshot_date, base_currency, cash_base, notes) values (current_date, 'GBP', 800, '{marker}_FAIL') returning snapshot_id;"
    )
    _psql_exec(
        f"insert into reconciliation_snapshot_positions(snapshot_id, internal_symbol, units) values ('{snapshot_fail}','AAPL',0.5);"
    )

    # Run reconciliation gate for both. We expect OK snapshot pass and FAIL snapshot fail.
    ok_status = subprocess.run(
        ["python3", str(ROOT / "scripts" / "reconcile_run.py"), "--snapshot-id", snapshot_ok],
        check=False,
        capture_output=True,
        text=True,
    )
    fail_status = subprocess.run(
        ["python3", str(ROOT / "scripts" / "reconcile_run.py"), "--snapshot-id", snapshot_fail],
        check=False,
        capture_output=True,
        text=True,
    )

    if ok_status.returncode != 0:
        print("SELFTEST_FAIL: expected PASS snapshot to pass", file=sys.stderr)
        print(ok_status.stdout)
        print(ok_status.stderr, file=sys.stderr)
        return 2
    if fail_status.returncode == 0:
        print("SELFTEST_FAIL: expected FAIL snapshot to fail", file=sys.stderr)
        print(fail_status.stdout)
        print(fail_status.stderr, file=sys.stderr)
        return 2

    # Cleanup (best-effort, scoped by marker/run_id)
    _psql_exec(
        f"""
        delete from reconciliation_results where snapshot_id in ('{snapshot_ok}','{snapshot_fail}');
        delete from reconciliation_snapshot_positions where snapshot_id in ('{snapshot_ok}','{snapshot_fail}');
        delete from reconciliation_snapshots where snapshot_id in ('{snapshot_ok}','{snapshot_fail}');
        delete from ledger_trades_fills where notes = '{marker}';
        delete from ledger_cash_movements where movement_type = 'SELFTEST' and notes = '{marker}';
        delete from tickets where run_id = '{run_id}';
        delete from runs where run_id = '{run_id}';
        """
    )

    print("SELFTEST_OK")
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
