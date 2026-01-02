#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
ENV_FILE = ROOT / "config" / "secrets.env"
COMPOSE_FILE = ROOT / "docker" / "compose.yml"
POLICY_FILE = ROOT / "config" / "policy.yml"


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


def _read_policy(path: Path) -> dict:
    if not path.exists():
        return {}
    policy = yaml.safe_load(path.read_text(encoding="utf-8"))
    return policy if isinstance(policy, dict) else {}


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


def _parse_bool(v: str | None, default: bool) -> bool:
    if v is None:
        return default
    s = v.strip().lower()
    if s in ("1", "true", "yes", "y", "on"):
        return True
    if s in ("0", "false", "no", "n", "off"):
        return False
    return default


def _load_sink_config() -> tuple[str, bool]:
    policy = _read_policy(POLICY_FILE)
    alerts = policy.get("alerts", {}) if isinstance(policy.get("alerts", {}), dict) else {}
    policy_sink = str(alerts.get("secondary_sink", "none"))
    policy_dryrun = bool(alerts.get("secondary_dryrun", True))

    file_env = _read_env_file(ENV_FILE) if ENV_FILE.exists() else {}
    sink_raw = os.environ.get("ALERT_SECONDARY_SINK") or file_env.get("ALERT_SECONDARY_SINK") or policy_sink or "none"
    dryrun_raw = os.environ.get("ALERT_SECONDARY_DRYRUN") or file_env.get("ALERT_SECONDARY_DRYRUN")
    sink = str(sink_raw).strip().lower()
    dryrun = _parse_bool(dryrun_raw, policy_dryrun)
    return sink, dryrun


def _render_delivery_md(payload: dict) -> str:
    lines: list[str] = []
    lines.append("# Alert Delivery Receipt")
    lines.append("")
    lines.append(f"- created_utc: `{payload['created_utc']}`")
    lines.append(f"- alert_id: `{payload['alert_id']}`")
    lines.append(f"- sink: `{payload['sink']}`")
    lines.append(f"- dryrun: `{payload['dryrun']}`")
    lines.append(f"- status: `{payload['status']}`")
    if payload.get("error_text"):
        lines.append(f"- error_text: `{payload['error_text']}`")
    lines.append("")
    lines.append("## Summary")
    lines.append("")
    lines.append(payload.get("summary", ""))
    lines.append("")
    lines.append("## Next operator action")
    lines.append("")
    lines.append(payload.get("next_operator_action", ""))
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Deliver an alert to the optional secondary sink (file-only primary remains mandatory).")
    parser.add_argument("--alert-json", required=True, help="Path to alert.json emitted by alert_emit.")
    args = parser.parse_args()

    alert_json_path = Path(args.alert_json)
    if not alert_json_path.exists():
        raise FileNotFoundError(f"Missing alert.json: {alert_json_path}")
    alert_dir = alert_json_path.parent
    alert = json.loads(alert_json_path.read_text(encoding="utf-8"))

    sink, dryrun = _load_sink_config()

    status = "FAILED"
    error_text = ""
    if sink in ("", "none"):
        sink = "none"
        status = "SKIPPED"
    elif dryrun:
        status = "WOULD_SEND"
    else:
        status = "FAILED"
        error_text = "secondary_delivery_not_implemented"

    created_utc = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    receipt = {
        "created_utc": created_utc,
        "alert_id": str(alert.get("alert_id") or ""),
        "sink": sink,
        "dryrun": dryrun,
        "status": status,
        "error_text": error_text or None,
        "summary": str(alert.get("summary") or ""),
        "next_operator_action": str(alert.get("next_operator_action") or ""),
        "alert_path": str(alert_dir),
    }

    delivery_json = alert_dir / "delivery.json"
    delivery_md = alert_dir / "delivery.md"
    delivery_json.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    delivery_md.write_text(_render_delivery_md(receipt), encoding="utf-8")
    print(f"Wrote {delivery_md}")

    # Best-effort DB insert/upsert.
    try:
        if not ENV_FILE.exists():
            raise FileNotFoundError(f"Missing {ENV_FILE}")
        receipt_str = json.dumps(receipt, sort_keys=True)
        dq = _dollar_quote("delivery", receipt_str)
        err_sql = "null" if not error_text else "'" + error_text.replace("'", "''") + "'"
        _psql_exec(
            f"""
            insert into alert_deliveries(alert_id, sink, dryrun, status, error_text, receipt_path)
            values (
              '{receipt['alert_id'].replace("'", "''")}',
              '{sink.replace("'", "''")}',
              {str(dryrun).lower()},
              '{status}',
              {err_sql},
              '{str(delivery_md).replace("'", "''")}'
            )
            on conflict (alert_id, sink) do update set
              dryrun = excluded.dryrun,
              status = excluded.status,
              error_text = excluded.error_text,
              receipt_path = excluded.receipt_path;
            """
        )
    except Exception as e:
        print(f"ALERT_DELIVERY_DB_INSERT_FAILED: {e}", file=sys.stderr)

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
