#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
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


def _emit_alert(*, run_id: str, ticket_id: str, details: dict) -> None:
    summary = f"Previous ticket missing confirmation: ticket_id={ticket_id}"
    subprocess.run(
        [
            "python3",
            "scripts/alert_emit.py",
            "--alert-type",
            "CONFIRMATION_MISSING",
            "--severity",
            "ERROR",
            "--run-id",
            run_id,
            "--ticket-id",
            ticket_id,
            "--summary",
            summary,
            "--details-json",
            json.dumps(details),
            "--artifact-path",
            f"/data/trading-ops/artifacts/tickets/{ticket_id}/ticket.md",
            "--artifact-path",
            f"/data/trading-ops/artifacts/tickets/{ticket_id}/ticket.json",
        ],
        cwd=str(ROOT),
        check=False,
        capture_output=True,
        text=True,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Deterministic confirmation gate (blocks if previous ticket unconfirmed).")
    parser.add_argument("--run-id", required=True, help="Current run_id (uuid).")
    args = parser.parse_args()

    if not ENV_FILE.exists():
        raise FileNotFoundError(f"Missing {ENV_FILE}; create it from config/secrets.env.example")

    run_id = args.run_id.strip()
    now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    latest_ticket = _psql_capture(
        f"""
        select coalesce(ticket_id::text,'') || '|' || coalesce(run_id::text,'') || '|' ||
               coalesce(to_char(created_at at time zone 'utc','YYYY-MM-DD\"T\"HH24:MI:SS\"Z\"'),'') || '|' ||
               coalesce(status,'')
        from tickets
        where run_id <> '{run_id}'
          and ticket_type = 'TRADE'
        order by created_at desc
        limit 1;
        """
    )
    if not latest_ticket:
        print("CONFIRMATION_GATE_PASS (no previous TRADE ticket)")
        _psql_exec(
            f"""
            insert into risk_checks(run_id, check_name, passed, details)
            values ('{run_id}'::uuid, 'confirmation_deadline', true, '{{\"status\":\"no_previous_trade_ticket\",\"evaluated_at_utc\":\"{now_utc}\"}}'::jsonb)
            on conflict (run_id, check_name) do update set passed = excluded.passed, details = excluded.details;
            """
        )
        return 0

    ticket_id, prev_run_id, prev_created_utc, prev_status = latest_ticket.split("|", 3)
    conf_count = int(_psql_capture(f"select count(*) from confirmations where ticket_id = '{ticket_id}'::uuid;") or "0")
    ok = conf_count > 0

    last_conf = _psql_capture(
        f"""
        select
          coalesce(payload->>'confirmation_type','') || '|' ||
          coalesce(jsonb_array_length(coalesce(payload->'fills','[]'::jsonb))::text,'0')
        from confirmations
        where ticket_id = '{ticket_id}'::uuid
        order by created_at desc
        limit 1;
        """
    )
    last_conf_type, last_conf_fills = ("", "0")
    if last_conf:
        last_conf_type, last_conf_fills = last_conf.split("|", 1)

    details = {
        "evaluated_at_utc": now_utc,
        "previous_ticket_id": ticket_id,
        "previous_ticket_status": prev_status,
        "previous_ticket_created_utc": prev_created_utc,
        "previous_run_id": prev_run_id,
        "confirmations_count": conf_count,
        "latest_confirmation_type": last_conf_type,
        "latest_confirmation_fills_count": int(last_conf_fills or "0"),
        "rule": "previous TRADE ticket must have >=1 confirmation by start of 14:00 run",
    }

    _psql_exec(
        f"""
        insert into risk_checks(run_id, check_name, passed, details)
        values ('{run_id}'::uuid, 'confirmation_deadline', {str(ok).lower()}, '{json.dumps(details).replace("'", "''")}'::jsonb)
        on conflict (run_id, check_name) do update set passed = excluded.passed, details = excluded.details;
        """
    )

    if ok:
        print("CONFIRMATION_GATE_PASS")
        return 0

    _emit_alert(run_id=run_id, ticket_id=ticket_id, details=details)
    print("CONFIRMATION_GATE_FAIL")
    return 2


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except subprocess.CalledProcessError as e:
        print(f"ERROR: docker/psql failed: {e}", file=sys.stderr)
        raise SystemExit(1)
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        raise SystemExit(2)
