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


def _artifacts_root(env: dict[str, str]) -> Path:
    return Path(env.get("ARTIFACTS_DIR", "/data/trading-ops/artifacts")).resolve()


def main() -> int:
    if not ENV_FILE.exists():
        raise FileNotFoundError(f"Missing {ENV_FILE}; create it from config/secrets.env.example")
    env = _read_env_file(ENV_FILE)

    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    artifacts = _artifacts_root(env)
    report_dir = artifacts / "reports"
    report_dir.mkdir(parents=True, exist_ok=True)
    report_path = report_dir / f"ledger_{ts}.md"

    cash = _psql_capture("select cash_base from ledger_cash_current;") or "0"
    positions = _psql_capture("select internal_symbol || '|' || units from ledger_positions_current order by internal_symbol;")

    last_reconcile = _psql_capture(
        """
        select coalesce(r.passed::text,'') || '|' || coalesce(r.evaluated_at::text,'') || '|' || coalesce(r.report_path,'')
        from reconciliation_results r
        order by r.evaluated_at desc
        limit 1;
        """
    )

    lines: list[str] = []
    lines.append("# Ledger Report (v1)")
    lines.append("")
    lines.append(f"- Generated at (UTC): `{ts}`")
    lines.append("")
    lines.append("## Cash")
    lines.append("")
    lines.append(f"- Cash (GBP, derived): `{float(cash):.2f}`")
    lines.append("")
    lines.append("## Positions (units, derived)")
    lines.append("")
    if positions:
        for line in positions.splitlines():
            sym, units = line.split("|", 1)
            lines.append(f"- {sym}: `{float(units):.6f}`")
    else:
        lines.append("- None")
    lines.append("")
    lines.append("## Last reconciliation result")
    lines.append("")
    if last_reconcile:
        passed_s, evaluated_at, report_path_s = last_reconcile.split("|", 2)
        lines.append(f"- passed: `{passed_s}`")
        lines.append(f"- evaluated_at: `{evaluated_at}`")
        lines.append(f"- report_path: `{report_path_s}`")
    else:
        lines.append("- None")
    lines.append("")

    report_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"Wrote {report_path}")
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
