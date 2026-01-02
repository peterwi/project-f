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
    parser = argparse.ArgumentParser(description="Run reconciliation gate vs latest snapshot.")
    parser.add_argument("--snapshot-id", help="Optional snapshot_id; default uses latest snapshot by created_at.")
    args = parser.parse_args()

    if not ENV_FILE.exists():
        raise FileNotFoundError(f"Missing {ENV_FILE}; create it from config/secrets.env.example")
    env = _read_env_file(ENV_FILE)

    cash_tol = float(env.get("RECONCILE_CASH_TOLERANCE_GBP", "5"))
    units_tol = float(env.get("RECONCILE_UNITS_TOLERANCE_ABS", "0.0001"))

    snapshot_id = args.snapshot_id or _psql_capture(
        "select snapshot_id from reconciliation_snapshots order by created_at desc limit 1;"
    )
    if not snapshot_id:
        print("RECONCILIATION_FAIL: no snapshots found", file=sys.stderr)
        return 2

    snapshot_meta = _psql_capture(
        f"select snapshot_date || '|' || cash_base from reconciliation_snapshots where snapshot_id = '{snapshot_id}';"
    )
    if not snapshot_meta:
        print("RECONCILIATION_FAIL: snapshot not found", file=sys.stderr)
        return 2
    snapshot_date, cash_snapshot_s = snapshot_meta.split("|", 1)
    cash_snapshot = float(cash_snapshot_s)

    cash_ledger_s = _psql_capture("select cash_base from ledger_cash_current;") or "0"
    cash_ledger = float(cash_ledger_s)
    cash_diff = cash_ledger - cash_snapshot
    cash_diff_abs = abs(cash_diff)

    # Build union of symbols between ledger and snapshot.
    ledger_pos_raw = _psql_capture("select internal_symbol || '|' || units from ledger_positions_current order by internal_symbol;")
    snapshot_pos_raw = _psql_capture(
        f"""
        select internal_symbol || '|' || units
        from reconciliation_snapshot_positions
        where snapshot_id = '{snapshot_id}'
        order by internal_symbol;
        """
    )

    ledger_positions: dict[str, float] = {}
    if ledger_pos_raw:
        for line in ledger_pos_raw.splitlines():
            sym, units = line.split("|", 1)
            ledger_positions[sym] = float(units)

    snapshot_positions: dict[str, float] = {}
    if snapshot_pos_raw:
        for line in snapshot_pos_raw.splitlines():
            sym, units = line.split("|", 1)
            snapshot_positions[sym] = float(units)

    # Unknown snapshot symbols (not in config_universe) should hard-fail.
    unknown_snapshot_symbols_raw = _psql_capture(
        f"""
        select coalesce(string_agg(p.internal_symbol, ',' order by p.internal_symbol), '')
        from reconciliation_snapshot_positions p
        left join config_universe u on u.internal_symbol = p.internal_symbol
        where p.snapshot_id = '{snapshot_id}'
          and u.internal_symbol is null;
        """
    )
    unknown_symbols = [s for s in unknown_snapshot_symbols_raw.split(",") if s] if unknown_snapshot_symbols_raw else []

    # Missing snapshot symbols for ledger non-zero positions should fail.
    missing_in_snapshot = sorted([s for s, u in ledger_positions.items() if abs(u) > units_tol and s not in snapshot_positions])

    max_units_diff = 0.0
    per_symbol_diffs: dict[str, float] = {}
    union = sorted(set(ledger_positions.keys()) | set(snapshot_positions.keys()))
    for sym in union:
        diff = ledger_positions.get(sym, 0.0) - snapshot_positions.get(sym, 0.0)
        per_symbol_diffs[sym] = diff
        max_units_diff = max(max_units_diff, abs(diff))

    issues: list[str] = []
    if unknown_symbols:
        issues.append(f"Unknown snapshot symbols (not in config_universe): {', '.join(unknown_symbols)}")
    if missing_in_snapshot:
        issues.append(f"Ledger holds positions missing from snapshot: {', '.join(missing_in_snapshot)}")
    if cash_diff_abs > cash_tol:
        issues.append(f"Cash drift {cash_diff_abs:.2f} GBP exceeds tolerance {cash_tol:.2f} GBP.")
    if max_units_diff > units_tol:
        issues.append(f"Max units drift {max_units_diff:.6f} exceeds tolerance {units_tol:.6f}.")

    passed = len(issues) == 0

    # Write report
    artifacts = _artifacts_root(env)
    report_dir = artifacts / "reports"
    report_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    report_path = report_dir / f"reconcile_{snapshot_date}_{ts}.md"

    lines: list[str] = []
    lines.append("# Reconciliation Gate Report")
    lines.append("")
    lines.append(f"- Generated at (UTC): `{ts}`")
    lines.append(f"- Snapshot ID: `{snapshot_id}`")
    lines.append(f"- Snapshot date: `{snapshot_date}`")
    lines.append("")
    lines.append("## Cash")
    lines.append("")
    lines.append(f"- Snapshot cash (GBP): `{cash_snapshot:.2f}`")
    lines.append(f"- Ledger cash (GBP): `{cash_ledger:.2f}`")
    lines.append(f"- Drift (GBP): `{cash_diff:.2f}`")
    lines.append(f"- Tolerance (GBP abs): `{cash_tol:.2f}`")
    lines.append("")
    lines.append("## Positions (units)")
    lines.append("")
    lines.append(f"- Units tolerance (abs): `{units_tol:.6f}`")
    lines.append(f"- Max units drift (abs): `{max_units_diff:.6f}`")
    lines.append("")
    lines.append("## Result")
    lines.append("")
    lines.append(f"- Status: `{'PASS' if passed else 'FAIL'}`")
    lines.append("")
    lines.append("## Issues")
    lines.append("")
    if issues:
        for i in issues:
            lines.append(f"- {i}")
    else:
        lines.append("- None")
    lines.append("")
    lines.append("## Per-symbol diffs (ledger - snapshot)")
    lines.append("")
    if union:
        for sym in union:
            lines.append(f"- {sym}: `{per_symbol_diffs[sym]:.6f}`")
    else:
        lines.append("- None")
    lines.append("")
    report_path.write_text("\n".join(lines), encoding="utf-8")

    details = {
        "issues": issues,
        "missing_in_snapshot": missing_in_snapshot,
        "per_symbol_diffs": per_symbol_diffs,
        "unknown_symbols": unknown_symbols,
    }
    details_json = json.dumps(details).replace("'", "''")

    _psql_exec(
        f"""
        insert into reconciliation_results(
          snapshot_id, passed, cash_ledger, cash_snapshot, cash_diff, cash_diff_abs, cash_tolerance_abs,
          max_units_diff, units_tolerance_abs, unknown_symbols, details, report_path
        ) values (
          '{snapshot_id}',
          {str(passed).lower()},
          {cash_ledger},
          {cash_snapshot},
          {cash_diff},
          {cash_diff_abs},
          {cash_tol},
          {max_units_diff},
          {units_tol},
          '{json.dumps(unknown_symbols).replace("'", "''")}'::jsonb,
          '{details_json}'::jsonb,
          '{str(report_path).replace("'", "''")}'
        );
        """
    )

    print(f"Wrote {report_path}")
    if passed:
        print("RECONCILIATION_PASS")
        return 0
    print("RECONCILIATION_FAIL")
    return 2


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except subprocess.CalledProcessError as e:
        print(f"ERROR: psql failed: {e}", file=sys.stderr)
        raise SystemExit(1)
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        raise SystemExit(2)
