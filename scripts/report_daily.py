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


def _artifacts_root(env: dict[str, str]) -> Path:
    return Path(env.get("ARTIFACTS_DIR", "/data/trading-ops/artifacts")).resolve()


def main() -> int:
    parser = argparse.ArgumentParser(description="Write a deterministic daily operator report (even on NO-TRADE).")
    parser.add_argument("--run-id", required=True, help="runs.run_id (uuid)")
    parser.add_argument("--cadence", default="", help="Optional cadence label (e.g. 0800, 1400).")
    args = parser.parse_args()

    if not ENV_FILE.exists():
        raise FileNotFoundError(f"Missing {ENV_FILE}; create it from config/secrets.env.example")
    env = _read_env_file(ENV_FILE)

    run_id = args.run_id.strip()
    cadence = (args.cadence or "").strip()

    run_row = _psql_capture(
        f"""
        select
          coalesce(status,'') || '|' ||
          coalesce(config_hash,'') || '|' ||
          coalesce(git_commit,'') || '|' ||
          coalesce(asof_date::text,'') || '|' ||
          coalesce(cadence,'') || '|' ||
          coalesce(to_char(created_at at time zone 'utc','YYYY-MM-DD\"T\"HH24:MI:SS\"Z\"'),'')
        from runs
        where run_id = '{run_id}';
        """
    )
    if not run_row:
        raise RuntimeError(f"Run not found: {run_id}")
    status, config_hash, git_commit, asof_date, cadence_db, created_utc = run_row.split("|", 5)

    dq_row = _psql_capture(
        f"""
        select
          coalesce(passed::text,'') || '|' ||
          coalesce(asof_date::text,'') || '|' ||
          coalesce(report_path,'')
        from data_quality_reports
        where run_id = '{run_id}'
        order by generated_at desc
        limit 1;
        """
    )
    dq_passed, dq_asof, dq_report_path = ("", "", "")
    if dq_row:
        dq_passed, dq_asof, dq_report_path = dq_row.split("|", 2)

    ticket_row = _psql_capture(
        f"""
        select
          coalesce(ticket_id::text,'') || '|' ||
          coalesce(status,'') || '|' ||
          coalesce(to_char(created_at at time zone 'utc','YYYY-MM-DD\"T\"HH24:MI:SS\"Z\"'),'')
        from tickets
        where run_id = '{run_id}'
        limit 1;
        """
    )
    ticket_id, ticket_status, ticket_created = ("", "", "")
    if ticket_row:
        ticket_id, ticket_status, ticket_created = ticket_row.split("|", 2)

    last_rec = _psql_capture(
        """
        select
          coalesce(passed::text,'') || '|' ||
          coalesce(to_char(evaluated_at at time zone 'utc','YYYY-MM-DD\"T\"HH24:MI:SS\"Z\"'),'') || '|' ||
          coalesce(report_path,'')
        from reconciliation_results
        order by evaluated_at desc
        limit 1;
        """
    )
    rec_passed, rec_evaluated, rec_report_path = ("", "", "")
    if last_rec:
        rec_passed, rec_evaluated, rec_report_path = last_rec.split("|", 2)

    artifacts = _artifacts_root(env)
    report_dir = artifacts / "reports"
    report_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    cadence_label = cadence or cadence_db or "daily"
    asof_label = dq_asof or asof_date or "UNKNOWN"
    report_path = report_dir / f"daily_{cadence_label}_{asof_label}_{ts}.md"

    lines: list[str] = []
    lines.append("# Daily Operator Report")
    lines.append("")
    lines.append(f"- Generated at (UTC): `{ts}`")
    lines.append(f"- cadence: `{cadence_label}`")
    lines.append("")
    lines.append("## Run")
    lines.append("")
    lines.append(f"- run_id: `{run_id}`")
    lines.append(f"- created_utc: `{created_utc}`")
    lines.append(f"- status: `{status}`")
    lines.append(f"- config_hash: `{config_hash}`")
    lines.append(f"- git_commit: `{git_commit}`")
    lines.append(f"- asof_date: `{asof_label}`")
    lines.append("")
    lines.append("## Data quality")
    lines.append("")
    if dq_row:
        lines.append(f"- passed: `{dq_passed}`")
        lines.append(f"- report: `{dq_report_path}`")
    else:
        lines.append("- No data_quality_reports row found for this run_id.")
    lines.append("")
    lines.append("## Reconciliation (latest)")
    lines.append("")
    if last_rec:
        lines.append(f"- passed: `{rec_passed}`")
        lines.append(f"- evaluated_at_utc: `{rec_evaluated}`")
        lines.append(f"- report: `{rec_report_path}`")
    else:
        lines.append("- None")
    lines.append("")
    lines.append("## Ticket")
    lines.append("")
    if ticket_id:
        lines.append(f"- ticket_id: `{ticket_id}`")
        lines.append(f"- status: `{ticket_status}`")
        lines.append(f"- created_utc: `{ticket_created}`")
    else:
        lines.append("- None (no decision/ticket generated for this run)")
    lines.append("")
    report_path.write_text("\n".join(lines), encoding="utf-8")

    details = {
        "report_path": str(report_path),
        "cadence": cadence_label,
        "run_id": run_id,
        "ticket_id": ticket_id or None,
        "asof_date": asof_label,
    }
    details_json = json.dumps(details).replace("'", "''")
    ticket_sql = "null" if not ticket_id else f"'{ticket_id}'::uuid"
    _psql_exec(
        f"""
        insert into audit_log(run_id, ticket_id, actor, action, object_type, object_id, details)
        values (
          '{run_id}'::uuid,
          {ticket_sql},
          'system',
          'DAILY_REPORT_WRITTEN',
          'report',
          '{str(report_path).replace("'", "''")}',
          '{details_json}'::jsonb
        );
        """
    )

    print(f"Wrote {report_path}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except subprocess.CalledProcessError as e:
        print(f"ERROR: docker/psql failed: {e}", file=sys.stderr)
        raise SystemExit(1)
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        raise SystemExit(2)

