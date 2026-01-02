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

ALERTS_DIR = Path("/data/trading-ops/artifacts/alerts")

ALERT_TYPES = {
    "DATA_QUALITY_FAIL",
    "RECONCILIATION_FAIL",
    "CONFIRMATION_MISSING",
    "RISKGUARD_BLOCKED",
    "SCHEDULER_MISFIRE",
}

SEVERITIES = {"INFO", "WARN", "ERROR"}


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


def _dollar_quote(tag: str, content: str) -> str:
    t = tag
    i = 0
    while f"${t}$" in content:
        i += 1
        t = f"{tag}{i}"
        if i > 50:
            raise ValueError("Failed to find safe dollar-quote tag.")
    return f"${t}$"


def _alert_id(ts_utc: str, alert_type: str, run_id: str | None, ticket_id: str | None) -> str:
    suffix = (run_id or ticket_id or "none").strip() or "none"
    return f"{ts_utc}-{alert_type}-{suffix}"


def _next_operator_action(alert_type: str, *, run_id: str | None, ticket_id: str | None) -> str:
    if alert_type == "DATA_QUALITY_FAIL":
        return "Inspect data quality report; if US holiday/late data, rerun with --asof-date override; trading remains blocked until PASS."
    if alert_type == "RECONCILIATION_FAIL":
        return "Capture a fresh eToro snapshot and run reconcile SOP; trading remains blocked until RECONCILIATION_PASS."
    if alert_type == "CONFIRMATION_MISSING":
        if ticket_id:
            return f"Submit confirmation for ticket_id={ticket_id} (record in confirmations) then rerun 14:00 run."
        return "Submit missing ticket confirmation (record in confirmations) then rerun 14:00 run."
    if alert_type == "RISKGUARD_BLOCKED":
        if run_id:
            return f"Review no_trade.json for run_id={run_id}; do not trade until blockers cleared and riskguard approves."
        return "Review no_trade.json; do not trade until blockers cleared and riskguard approves."
    if alert_type == "SCHEDULER_MISFIRE":
        return "Inspect scheduler log artifact, fix underlying error, then rerun the missed job manually."
    return "Review alert details and follow the deterministic runbook."


def _render_md(payload: dict) -> str:
    lines: list[str] = []
    lines.append(f"# ALERT: {payload['alert_type']} ({payload['severity']})")
    lines.append("")
    lines.append(f"- alert_id: `{payload['alert_id']}`")
    lines.append(f"- created_utc: `{payload['created_utc']}`")
    lines.append(f"- severity: `{payload['severity']}`")
    lines.append(f"- run_id: `{payload.get('run_id') or ''}`")
    lines.append(f"- ticket_id: `{payload.get('ticket_id') or ''}`")
    lines.append(f"- checklist_item: `{payload.get('checklist_item') or ''}`")
    lines.append("")
    lines.append("## Summary")
    lines.append("")
    lines.append(payload.get("summary", ""))
    lines.append("")
    lines.append("## Pointers")
    lines.append("")
    for p in payload.get("artifact_paths", []):
        lines.append(f"- `{p}`")
    lines.append("")
    lines.append("## Details (verbatim)")
    lines.append("")
    lines.append("```json")
    lines.append(json.dumps(payload.get("details", {}), indent=2))
    lines.append("```")
    lines.append("")
    lines.append("## Next operator action")
    lines.append("")
    lines.append(payload.get("next_operator_action", ""))
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Emit a deterministic alert (file-only + Postgres index).")
    parser.add_argument("--alert-type", required=True, choices=sorted(ALERT_TYPES))
    parser.add_argument("--severity", required=True, choices=sorted(SEVERITIES))
    parser.add_argument("--run-id", default="", help="Optional run_id (uuid).")
    parser.add_argument("--ticket-id", default="", help="Optional ticket_id (uuid).")
    parser.add_argument("--summary", required=True, help="One-line summary.")
    parser.add_argument("--details-json", default="", help="JSON string (structured) for alert details.")
    parser.add_argument("--details-file", default="", help="Path to a JSON file for alert details.")
    parser.add_argument("--artifact-path", action="append", default=[], help="Relevant artifact path (repeatable).")
    parser.add_argument("--checklist-item", default="M8.2", help="Checklist item pointer.")
    args = parser.parse_args()

    if not ENV_FILE.exists():
        raise FileNotFoundError(f"Missing {ENV_FILE}; create it from config/secrets.env.example")

    run_id = args.run_id.strip() or None
    ticket_id = args.ticket_id.strip() or None

    details: dict = {}
    if args.details_file:
        details = json.loads(Path(args.details_file).read_text(encoding="utf-8"))
    elif args.details_json:
        details = json.loads(args.details_json)

    created_utc = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    ts_utc = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    alert_id = _alert_id(ts_utc, args.alert_type, run_id, ticket_id)

    alert_dir = ALERTS_DIR / alert_id
    alert_dir.mkdir(parents=True, exist_ok=True)
    alert_json_path = alert_dir / "alert.json"
    alert_md_path = alert_dir / "alert.md"

    artifact_paths = [p for p in (args.artifact_path or []) if p]
    artifact_paths += [str(alert_json_path), str(alert_md_path)]

    payload = {
        "alert_id": alert_id,
        "alert_type": args.alert_type,
        "created_utc": created_utc,
        "severity": args.severity,
        "run_id": run_id,
        "ticket_id": ticket_id,
        "checklist_item": args.checklist_item,
        "summary": args.summary,
        "details": details,
        "artifact_paths": artifact_paths,
        "next_operator_action": _next_operator_action(args.alert_type, run_id=run_id, ticket_id=ticket_id),
    }

    # File sink MUST succeed even if DB is down.
    alert_json_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    alert_md_path.write_text(_render_md(payload), encoding="utf-8")
    print(f"Wrote {alert_md_path}")

    # Best-effort DB insert.
    try:
        details_str = json.dumps(payload, sort_keys=True)
        dq = _dollar_quote("alert", details_str)
        run_sql = "null" if not run_id else f"'{run_id}'::uuid"
        ticket_sql = "null" if not ticket_id else f"'{ticket_id}'::uuid"
        summary_sql = args.summary.replace("'", "''")
        artifact_path_sql = str(alert_dir).replace("'", "''")
        alert_type_sql = args.alert_type.replace("'", "''")
        severity_sql = args.severity.replace("'", "''")
        _psql_exec(
            f"""
            insert into alerts(alert_id, alert_type, severity, run_id, ticket_id, summary, details, artifact_path)
            values (
              '{alert_id.replace("'", "''")}',
              '{alert_type_sql}',
              '{severity_sql}',
              {run_sql},
              {ticket_sql},
              '{summary_sql}',
              {dq}{details_str}{dq}::jsonb,
              '{artifact_path_sql}'
            )
            on conflict (alert_id) do nothing;
            """
            )
    except Exception as e:
        print(f"ALERT_DB_INSERT_FAILED: {e}", file=sys.stderr)

    # Best-effort secondary delivery (must never block file emission).
    try:
        subprocess.run(
            ["python3", "scripts/alert_deliver.py", "--alert-json", str(alert_json_path)],
            cwd=str(ROOT),
            check=False,
            capture_output=True,
            text=True,
        )
    except Exception as e:
        print(f"ALERT_DELIVERY_FAILED: {e}", file=sys.stderr)

    print(f"alert_id={alert_id}")
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
