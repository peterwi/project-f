#!/usr/bin/env python3
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from datetime import date
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


def _validate_symbol(symbol: str) -> str:
    s = symbol.strip().upper()
    if not re.fullmatch(r"[A-Z0-9.\-]{1,15}", s):
        raise ValueError(f"Invalid internal_symbol: {symbol!r}")
    return s


def _parse_decimal(value: str, *, name: str) -> Decimal:
    raw = value.strip().replace(",", "")
    if raw.startswith("+"):
        raw = raw[1:].strip()
    try:
        d = Decimal(raw)
    except InvalidOperation:
        raise ValueError(f"Invalid {name}: {value!r}")
    if d.is_nan() or d.is_infinite():
        raise ValueError(f"Invalid {name}: {value!r}")
    return d


def _git_commit() -> str:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(ROOT),
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        return out or "UNKNOWN"
    except Exception:
        return "UNKNOWN"


def main() -> int:
    parser = argparse.ArgumentParser(description="Add a reconciliation snapshot (manual eToro capture).")
    parser.add_argument("--snapshot-date", required=True, help="YYYY-MM-DD")
    parser.add_argument("--cash-gbp", required=True, help="Cash in GBP shown by eToro at snapshot time.")
    parser.add_argument(
        "--position",
        action="append",
        default=[],
        help="Position as INTERNAL_SYMBOL=UNITS (repeatable), e.g. --position AAPL=0.03655",
    )
    parser.add_argument("--notes", default="", help="Optional notes.")
    args = parser.parse_args()

    if not ENV_FILE.exists():
        raise FileNotFoundError(f"Missing {ENV_FILE}; create it from config/secrets.env.example")

    snapshot_date = date.fromisoformat(args.snapshot_date)
    cash_gbp = _parse_decimal(args.cash_gbp, name="cash_gbp")

    positions: list[tuple[str, Decimal]] = []
    for p in args.position:
        if "=" not in p:
            raise ValueError(f"Invalid --position value (expected SYMBOL=UNITS): {p!r}")
        sym, units = p.split("=", 1)
        sym = _validate_symbol(sym)
        units_d = _parse_decimal(units, name=f"units for {sym}")
        positions.append((sym, units_d))

    # Insert snapshot and positions. Snapshot positions are required to include all non-zero holdings;
    # unknown/missing will be handled by reconciliation gate.
    notes_sql = "null" if not args.notes else "'" + args.notes.replace("'", "''") + "'"
    snapshot_id = _psql_capture(
        f"""
        insert into reconciliation_snapshots(snapshot_date, base_currency, cash_base, notes)
        values ('{snapshot_date.isoformat()}', 'GBP', {cash_gbp}, {notes_sql})
        returning snapshot_id;
        """
    )
    if not snapshot_id:
        raise RuntimeError("Failed to create reconciliation snapshot.")

    for sym, units in positions:
        _psql_capture(
            f"""
            insert into reconciliation_snapshot_positions(snapshot_id, internal_symbol, units)
            values ('{snapshot_id}', '{sym}', {units})
            on conflict (snapshot_id, internal_symbol) do update set units = excluded.units;
            """
        )

    fills_count = int((_psql_capture("select count(*) from ledger_trades_fills;") or "0").strip() or "0")
    if fills_count == 0 and positions:
        # Bootstrap ledger positions from the snapshot only when the ledger has no fills at all.
        # This is a one-time "genesis" to allow reconciliation + sizing to work from reality.
        git_commit = _git_commit().replace("'", "''")
        genesis_note = f"GENESIS_FROM_SNAPSHOT_{snapshot_id}".replace("'", "''")
        values_sql = ",\n              ".join(
            [f"({i + 1}, '{sym}', {units})" for i, (sym, units) in enumerate(positions)]
        )
        _psql_exec(
            f"""
            begin;
            with new_run as (
              insert into runs(cadence, asof_date, config_hash, git_commit, status, finished_at, notes)
              values ('genesis', '{snapshot_date.isoformat()}', 'GENESIS', '{git_commit}', 'finished', now(), '{genesis_note}')
              returning run_id
            ), new_ticket as (
              insert into tickets(run_id, ticket_type, status, rendered_md, rendered_json)
              select run_id, 'GENESIS', 'CLOSED', 'GENESIS', '{{}}'::jsonb
              from new_run
              returning ticket_id
            )
            insert into ledger_trades_fills(
              ticket_id, sequence, internal_symbol, executed_status, side, executed_value_base, units, fill_price, filled_at, notes
            )
            select (select ticket_id from new_ticket),
                   v.sequence,
                   v.internal_symbol,
                   'DONE',
                   'BUY',
                   null,
                   v.units,
                   null,
                   null,
                   '{genesis_note}'
            from (values
              {values_sql}
            ) as v(sequence, internal_symbol, units);
            commit;
            """
        )
        print("ledger_bootstrap=GENESIS_FROM_SNAPSHOT")

    print(f"snapshot_id={snapshot_id}")
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
