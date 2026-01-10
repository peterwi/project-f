#!/usr/bin/env python3
from __future__ import annotations

import argparse
import shutil
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path


DEFAULT_ROOT = Path("/data/trading-ops/artifacts")


@dataclass(frozen=True)
class PlanItem:
    action: str
    path: Path
    reason: str


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=(
            "File-only retention helper for /data trading-ops artifacts.\n"
            "Dry-run by default; use --apply to delete."
        )
    )
    p.add_argument("--root", default=str(DEFAULT_ROOT), help="Artifacts root (default: /data/trading-ops/artifacts)")
    p.add_argument("--keep-days-0800-runs", type=int, default=14, help="Keep scheduled-0800 run dirs for N days")
    p.add_argument("--keep-days-reports", type=int, default=30, help="Keep non-reconcile reports for N days")
    p.add_argument("--apply", action="store_true", help="Actually delete; default is dry-run")
    return p.parse_args()


def _is_older_than(path: Path, cutoff: datetime) -> bool:
    try:
        mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
    except FileNotFoundError:
        return False
    return mtime < cutoff


def _run_cadence_from_summary(run_dir: Path) -> str | None:
    summary = run_dir / "run_summary.md"
    if not summary.exists():
        return None
    for line in summary.read_text(encoding="utf-8").splitlines()[:40]:
        line = line.strip()
        if line.startswith("- cadence:"):
            # Format: - cadence: `1400`
            if "`" in line:
                parts = line.split("`")
                if len(parts) >= 2:
                    return parts[1].strip()
    return None


def build_retention_plan(
    *,
    root: Path,
    keep_days_0800_runs: int,
    keep_days_reports: int,
    now_utc: datetime,
) -> list[PlanItem]:
    items: list[PlanItem] = []

    runs_dir = root / "runs"
    reports_dir = root / "reports"

    cutoff_0800 = now_utc - timedelta(days=max(0, keep_days_0800_runs))
    cutoff_reports = now_utc - timedelta(days=max(0, keep_days_reports))

    if runs_dir.exists():
        for run_dir in sorted([p for p in runs_dir.iterdir() if p.is_dir()]):
            cadence = _run_cadence_from_summary(run_dir)
            if cadence != "0800":
                continue
            if _is_older_than(run_dir, cutoff_0800):
                items.append(
                    PlanItem(
                        action="DELETE_DIR",
                        path=run_dir,
                        reason=f"scheduled-0800 run older than {keep_days_0800_runs} days",
                    )
                )

    if reports_dir.exists():
        for rp in sorted([p for p in reports_dir.iterdir() if p.is_file()]):
            name = rp.name
            if name.startswith("reconcile_"):
                continue  # keep reconciliation reports (audit-critical)
            if name == "universe_validation.md":
                continue  # keep canonical validation report
            if _is_older_than(rp, cutoff_reports):
                items.append(
                    PlanItem(
                        action="DELETE_FILE",
                        path=rp,
                        reason=f"report older than {keep_days_reports} days",
                    )
                )

    return items


def apply_plan(plan: list[PlanItem], *, apply: bool) -> None:
    for item in plan:
        if not apply:
            print(f"DRYRUN {item.action} {item.path}  # {item.reason}")
            continue

        if item.action == "DELETE_DIR":
            shutil.rmtree(item.path)
            print(f"DELETED_DIR {item.path}  # {item.reason}")
        elif item.action == "DELETE_FILE":
            item.path.unlink(missing_ok=True)
            print(f"DELETED_FILE {item.path}  # {item.reason}")
        else:
            raise RuntimeError(f"Unknown action: {item.action}")


def main() -> int:
    args = _parse_args()
    root = Path(args.root).resolve()
    now = _utc_now()

    plan = build_retention_plan(
        root=root,
        keep_days_0800_runs=args.keep_days_0800_runs,
        keep_days_reports=args.keep_days_reports,
        now_utc=now,
    )

    print(f"ARTIFACTS_ROOT={root}")
    print(f"NOW_UTC={now.strftime('%Y-%m-%dT%H:%M:%SZ')}")
    print(f"PLAN_ITEMS={len(plan)}")
    apply_plan(plan, apply=bool(args.apply))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        raise SystemExit(2)
