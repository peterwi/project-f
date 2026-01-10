#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import subprocess
import sys
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Iterable, Optional


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
            ["git", "-c", "safe.directory=*", "rev-parse", "HEAD"],
            cwd=str(ROOT),
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        return out or "UNKNOWN"
    except Exception:
        return "UNKNOWN"


def _git_short() -> str:
    try:
        out = subprocess.run(
            ["git", "-c", "safe.directory=*", "rev-parse", "--short", "HEAD"],
            cwd=str(ROOT),
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        return out or "UNKNOWN"
    except Exception:
        return "UNKNOWN"


def _config_hash() -> str:
    hasher = hashlib.sha256()
    hasher.update(b"trading-ops-config-v1\n")
    config_files = [
        ROOT / "config" / "universe.csv",
        ROOT / "config" / "policy.yml",
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
    rc: int
    ok: bool
    outcome: str
    stdout: str
    stderr: str
    report_paths: list[str]


def _run_cmd(name: str, cmd: list[str], *, accept_rcs: Iterable[int] = (0,)) -> StepResult:
    proc = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True)
    report_paths: list[str] = []
    for line in proc.stdout.splitlines():
        if line.startswith("Wrote /data/") or line.startswith("Wrote /data"):
            report_paths.append(line.replace("Wrote ", "").strip())
    ok = proc.returncode in set(accept_rcs)
    outcome = "OK" if proc.returncode == 0 else ("FAIL" if proc.returncode == 2 else f"ERROR({proc.returncode})")
    return StepResult(
        name=name,
        rc=proc.returncode,
        ok=ok,
        outcome=outcome,
        stdout=proc.stdout,
        stderr=proc.stderr,
        report_paths=report_paths,
    )


def _write_summary(
    summary_path: Path,
    *,
    run_id: str,
    cadence: str,
    run_label: str,
    status: str,
    config_hash: str,
    git_commit: str,
    steps: list[StepResult],
) -> None:
    lines: list[str] = []
    lines.append("# Scheduled Run Summary")
    lines.append("")
    lines.append(f"- run_id: `{run_id}`")
    lines.append(f"- cadence: `{cadence}`")
    lines.append(f"- run_label: `{run_label}`")
    lines.append(f"- status: `{status}`")
    lines.append(f"- config_hash: `{config_hash}`")
    lines.append(f"- git_commit: `{git_commit}`")
    lines.append("")
    lines.append("## Steps")
    lines.append("")
    for s in steps:
        lines.append(f"- {s.name}: `{s.outcome}`")
        for rp in s.report_paths:
            lines.append(f"  - report: `{rp}`")
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
            lines.extend(out.splitlines()[:120])
            lines.append("```")
        if err:
            lines.append("```")
            lines.extend(err.splitlines()[:120])
            lines.append("```")
        lines.append("")
    summary_path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Run scheduled pipelines (08:00 / 14:00) deterministically.")
    parser.add_argument("--cadence", required=True, choices=["0800", "1400"], help="Schedule slot (UK time).")
    parser.add_argument("--asof-date", help="Optional override passed to data quality gate (YYYY-MM-DD).")
    parser.add_argument(
        "--scoring",
        default="stub",
        choices=["stub"],
        help="Scoring engine (v1: stub only; qlib integration later).",
    )
    args = parser.parse_args()

    if not ENV_FILE.exists():
        raise FileNotFoundError(f"Missing {ENV_FILE}; create it from config/secrets.env.example")
    env = _read_env_file(ENV_FILE)

    if args.asof_date:
        date.fromisoformat(args.asof_date)

    steps: list[StepResult] = []

    # Ensure DB schema exists.
    migrate = _run_cmd("migrate", ["make", "migrate"], accept_rcs=(0,))
    steps.append(migrate)
    if not migrate.ok:
        _write_summary(Path("/tmp/run_summary_failed.md"), run_id="UNKNOWN", cadence=args.cadence, run_label="UNKNOWN", status="failed", config_hash="UNKNOWN", git_commit="UNKNOWN", steps=steps)  # noqa: E501
        sys.stderr.write(migrate.stdout)
        sys.stderr.write(migrate.stderr)
        return 2

    config_hash = _config_hash()
    git_commit = _git_commit()
    run_label = f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-git{_git_short()}"

    cadence_db = f"scheduled-{args.cadence}"
    notes = f"run_scheduled:{args.cadence} {run_label}".replace("'", "''")
    asof_date: Optional[str] = args.asof_date

    run_id = _psql_capture(
        f"""
        insert into runs(config_hash, git_commit, status, asof_date, cadence, notes)
        values (
          '{config_hash}',
          '{git_commit}',
          'running',
          {("null" if not asof_date else "'" + asof_date + "'")},
          '{cadence_db}',
          '{notes}'
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
    # Create an initial run_summary early so downstream steps (e.g. ticket renderer)
    # can reference it even before the final summary is written.
    _write_summary(
        summary_path,
        run_id=run_id,
        cadence=args.cadence,
        run_label=run_label,
        status="running",
        config_hash=config_hash,
        git_commit=git_commit,
        steps=steps,
    )

    status = "failed"
    try:
        steps.append(_run_cmd("policy-validate", ["make", "policy-validate"], accept_rcs=(0,)))
        steps.append(_run_cmd("universe-import", ["make", "universe-import"], accept_rcs=(0,)))
        steps.append(_run_cmd("universe-validate", ["make", "universe-validate"], accept_rcs=(0,)))

        if args.cadence == "0800":
            steps.append(_run_cmd("market-fetch", ["make", "market-fetch"], accept_rcs=(0,)))

        dq_cmd = ["python3", "scripts/data_quality_gate.py", "--run-id", run_id]
        if args.asof_date:
            dq_cmd += ["--asof-date", args.asof_date]
        steps.append(_run_cmd("data-quality", dq_cmd, accept_rcs=(0, 2)))

        steps.append(_run_cmd("ledger-report", ["make", "ledger-report"], accept_rcs=(0,)))

        reconcile_count = int(_psql_capture("select count(*) from reconciliation_snapshots;") or "0")
        if reconcile_count > 0:
            steps.append(_run_cmd("reconcile-run", ["make", "reconcile-run"], accept_rcs=(0, 2)))
        else:
            steps.append(StepResult("reconcile-run", 0, True, "SKIPPED", "SKIPPED (no reconciliation snapshots present)\n", "", []))

        if args.cadence == "1400":
            steps.append(
                _run_cmd(
                    "confirmation-gate",
                    ["python3", "scripts/confirmation_gate.py", "--run-id", run_id],
                    accept_rcs=(0, 2),
                )
            )
            if args.scoring == "stub":
                steps.append(_run_cmd("score-stub", ["python3", "scripts/stub_signals.py", "--run-id", run_id], accept_rcs=(0,)))
            else:
                raise RuntimeError(f"Unsupported scoring engine: {args.scoring}")

            # Gate decision (NO_TRADE is expected in v1); treat rc=2 as valid execution outcome.
            steps.append(_run_cmd("riskguard", ["python3", "scripts/riskguard_run.py", "--run-id", run_id], accept_rcs=(0, 2)))

            # Refresh run_summary before ticket rendering so the ticket sees deterministic step outcomes
            # (otherwise it may parse only the initial bootstrap summary).
            _write_summary(
                summary_path,
                run_id=run_id,
                cadence=args.cadence,
                run_label=run_label,
                status="running",
                config_hash=config_hash,
                git_commit=git_commit,
                steps=steps,
            )
            steps.append(_run_cmd("ticket", ["python3", "scripts/ticket_render.py", "--run-id", run_id], accept_rcs=(0,)))

        steps.append(_run_cmd("report-daily", ["python3", "scripts/report_daily.py", "--run-id", run_id, "--cadence", args.cadence], accept_rcs=(0,)))

        # "passed" means the pipeline executed and produced artifacts/DB rows;
        # individual gate outcomes are expressed in their own artifacts/tables.
        status = "passed" if all(s.ok for s in steps) else "failed"
        _write_summary(
            summary_path,
            run_id=run_id,
            cadence=args.cadence,
            run_label=run_label,
            status=status,
            config_hash=config_hash,
            git_commit=git_commit,
            steps=steps,
        )
        print(f"Wrote {summary_path}")
        print(f"run_id={run_id}")
        print(f"cadence={args.cadence}")
        print(f"run_label={run_label}")
        return 0 if status == "passed" else 2
    finally:
        try:
            _psql_exec(
                f"""
                update runs
                set status = '{status}', finished_at = now(), notes = 'run_scheduled: {args.cadence} {status}'
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
