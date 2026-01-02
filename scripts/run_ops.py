#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import os
import subprocess
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Optional


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


def _config_hash() -> str:
    # Hash config inputs that affect deterministic behavior (exclude secrets.env).
    hasher = hashlib.sha256()
    hasher.update(b"trading-ops-config-v1\n")

    config_files = [
        ROOT / "config" / "universe.csv",
        ROOT / "config" / "policy.yml",  # may not exist yet
    ]

    for p in config_files:
        hasher.update(f"\nFILE:{p.relative_to(ROOT)}\n".encode("utf-8"))
        if p.exists():
            hasher.update(p.read_bytes())
        else:
            hasher.update(b"MISSING\n")

    return hasher.hexdigest()


def _artifacts_root(env: dict[str, str]) -> Path:
    return Path(env.get("ARTIFACTS_DIR", "/data/trading-ops/artifacts")).resolve()


@dataclass(frozen=True)
class StepResult:
    name: str
    ok: bool
    stdout: str
    stderr: str
    report_paths: list[str]


def _run_cmd(name: str, cmd: list[str]) -> StepResult:
    proc = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True)
    report_paths: list[str] = []
    for line in proc.stdout.splitlines():
        if line.startswith("Wrote /data/") or line.startswith("Wrote /data"):
            report_paths.append(line.replace("Wrote ", "").strip())
    return StepResult(
        name=name,
        ok=(proc.returncode == 0),
        stdout=proc.stdout,
        stderr=proc.stderr,
        report_paths=report_paths,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Run ops gates end-to-end (no tickets, no trades).")
    parser.add_argument("--asof-date", help="Override asof_date passed to data quality gate (YYYY-MM-DD).")
    args = parser.parse_args()

    if not ENV_FILE.exists():
        raise FileNotFoundError(f"Missing {ENV_FILE}; create it from config/secrets.env.example")
    env = _read_env_file(ENV_FILE)

    steps: list[StepResult] = []

    # Bootstrap: ensure schema exists before we attempt to write a run record.
    migrate = _run_cmd("migrate", ["make", "migrate"])
    steps.append(migrate)
    if not migrate.ok:
        sys.stderr.write(migrate.stdout)
        sys.stderr.write(migrate.stderr)
        return 2

    # Create run_id row in DB.
    config_hash = _config_hash()
    git_commit = _git_commit()
    asof_date: Optional[str] = args.asof_date
    if asof_date:
        date.fromisoformat(asof_date)  # validate

    run_id = _psql_capture(
        f"""
        insert into runs(config_hash, git_commit, status, asof_date, cadence, notes)
        values (
          '{config_hash}',
          '{git_commit}',
          'running',
          {("null" if not asof_date else "'" + asof_date + "'")},
          'ops',
          'run_ops'
        )
        returning run_id;
        """
    )
    if not run_id:
        raise RuntimeError("Failed to create runs row.")

    artifacts = _artifacts_root(env)
    run_dir = artifacts / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    summary_path = run_dir / "run_summary.md"

    steps.append(_run_cmd("policy-validate", ["make", "policy-validate"]))
    steps.append(_run_cmd("universe-import", ["make", "universe-import"]))
    steps.append(_run_cmd("universe-validate", ["make", "universe-validate"]))
    steps.append(_run_cmd("market-fetch", ["make", "market-fetch"]))
    dq_cmd = ["python3", "scripts/data_quality_gate.py", "--run-id", run_id]
    if args.asof_date:
        dq_cmd += ["--asof-date", args.asof_date]
    steps.append(_run_cmd("data-quality", dq_cmd))
    steps.append(_run_cmd("ledger-report", ["make", "ledger-report"]))

    # Reconciliation is optional for ops run, but required before any trading stage later.
    reconcile_count = int(_psql_capture("select count(*) from reconciliation_snapshots;") or "0")
    if reconcile_count > 0:
        steps.append(_run_cmd("reconcile-run", ["make", "reconcile-run"]))
    else:
        steps.append(StepResult("reconcile-run", True, "SKIPPED (no reconciliation snapshots present)\n", "", []))

    status = "failed"
    try:
        all_ok = all(s.ok for s in steps)
        status = "passed" if all_ok else "failed"

        # Write summary.
        lines: list[str] = []
        lines.append("# Ops Run Summary")
        lines.append("")
        lines.append(f"- run_id: `{run_id}`")
        lines.append(f"- status: `{status}`")
        lines.append(f"- config_hash: `{config_hash}`")
        lines.append(f"- git_commit: `{git_commit}`")
        if asof_date:
            lines.append(f"- asof_date (override): `{asof_date}`")
        lines.append("")
        lines.append("## Steps")
        lines.append("")
        for s in steps:
            lines.append(f"- {s.name}: `{'OK' if s.ok else 'FAIL'}`")
            for rp in s.report_paths:
                lines.append(f"  - report: `{rp}`")
        lines.append("")
        lines.append("## Trade readiness (informational)")
        lines.append("")
        if reconcile_count == 0:
            lines.append("- Reconciliation snapshot: `MISSING` (trading must remain blocked until added and gate passes)")
        else:
            lines.append("- Reconciliation snapshot: `PRESENT` (see reconcile report for PASS/FAIL)")
        lines.append("")
        lines.append("## Raw outputs (truncated)")
        lines.append("")
        for s in steps:
            out = (s.stdout or "").strip()
            err = (s.stderr or "").strip()
            if not out and not err:
                continue
            lines.append(f"### {s.name}")
            if out:
                lines.append("```")
                lines.extend(out.splitlines()[:80])
                lines.append("```")
            if err:
                lines.append("```")
                lines.extend(err.splitlines()[:80])
                lines.append("```")
            lines.append("")

        summary_path.write_text("\n".join(lines), encoding="utf-8")
        print(f"Wrote {summary_path}")
        return 0 if all_ok else 2
    finally:
        # Best-effort: don't mask the real failure if DB is down here.
        try:
            _psql_exec(
                f"""
                update runs
                set status = '{status}', finished_at = now(), notes = 'run_ops: {status}'
                where run_id = '{run_id}';
                """
            )
        except Exception:
            pass


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except subprocess.CalledProcessError as e:
        print(f"ERROR: command failed: {e}", file=sys.stderr)
        raise SystemExit(1)
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        raise SystemExit(2)
