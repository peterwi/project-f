#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
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


def _policy_hash() -> str:
    b = POLICY_FILE.read_bytes() if POLICY_FILE.exists() else b""
    return hashlib.sha256(b).hexdigest()


def _artifacts_root(env: dict[str, str]) -> Path:
    return Path(env.get("ARTIFACTS_DIR", "/data/trading-ops/artifacts")).resolve()

def _emit_alert_blocked(*, run_id: str, asof: str, reasons: list[dict], risk_checks: list[tuple[str, bool, dict]], run_dir: Path, no_trade_path: Path, proposed_path: Path) -> None:
    details = {
        "run_id": run_id,
        "asof_date": asof,
        "reasons": reasons,
        "risk_checks": [{"name": n, "passed": ok, "detail": d} for (n, ok, d) in risk_checks],
        "no_trade_json": str(no_trade_path),
        "trades_proposed_json": str(proposed_path),
    }
    cmd = [
        "python3",
        "scripts/alert_emit.py",
        "--alert-type",
        "RISKGUARD_BLOCKED",
        "--severity",
        "WARN",
        "--run-id",
        run_id,
        "--summary",
        f"RISKGUARD_BLOCKED run_id={run_id} asof_date={asof}",
        "--details-json",
        json.dumps(details),
        "--artifact-path",
        str(run_dir / "run_summary.md"),
        "--artifact-path",
        str(no_trade_path),
        "--artifact-path",
        str(proposed_path),
    ]
    subprocess.run(cmd, cwd=str(ROOT), check=False, capture_output=True, text=True)


def _latest_ops_run_id() -> str:
    run_id = _psql_capture("select run_id from runs where cadence = 'ops' order by started_at desc limit 1;")
    if not run_id:
        raise RuntimeError("No runs found. Run: make run-ops")
    return run_id


def _load_policy() -> dict:
    if not POLICY_FILE.exists():
        raise RuntimeError(f"Missing {POLICY_FILE}")
    policy = yaml.safe_load(POLICY_FILE.read_text(encoding="utf-8"))
    if not isinstance(policy, dict):
        raise RuntimeError("policy.yml must be a mapping")
    return policy


def main() -> int:
    parser = argparse.ArgumentParser(description="Deterministic riskguard (v1) driven by policy + signals.")
    parser.add_argument("--run-id", help="Target run_id (defaults to latest ops run).")
    args = parser.parse_args()

    if not ENV_FILE.exists():
        raise FileNotFoundError(f"Missing {ENV_FILE}; create it from config/secrets.env.example")

    env = _read_env_file(ENV_FILE)
    policy = _load_policy()

    run_id = args.run_id or _latest_ops_run_id()
    git_commit = _git_commit()
    policy_hash = _policy_hash()

    artifacts = _artifacts_root(env)
    run_dir = artifacts / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    asof = _psql_capture(
        f"""
        select asof_date::text
        from data_quality_reports
        where run_id = '{run_id}'
        order by generated_at desc
        limit 1;
        """
    )
    if not asof:
        asof = _psql_capture(f"select coalesce(asof_date::text,'') from runs where run_id = '{run_id}';")

    reasons: list[dict] = []
    risk_checks: list[tuple[str, bool, dict]] = []

    dq_pass = _psql_capture(
        f"select coalesce(passed::text,'') from data_quality_reports where run_id = '{run_id}' order by generated_at desc limit 1;"
    )
    dq_ok = dq_pass.lower() in ("t", "true", "1", "yes")
    risk_checks.append(("data_quality", dq_ok, {"run_id": run_id}))
    if not dq_ok:
        reasons.append({"code": "DATA_QUALITY_FAIL", "detail": "Data quality gate missing or failed for run."})

    reconcile_required = bool(policy.get("reconcile", {}).get("required", True))
    reconcile_ok = True
    reconcile_detail: dict = {"required": reconcile_required}
    if reconcile_required:
        last_rec = _psql_capture(
            "select coalesce(passed::text,'') || '|' || coalesce(report_path,'') from reconciliation_results order by evaluated_at desc limit 1;"
        )
        if not last_rec:
            reconcile_ok = False
            reconcile_detail["status"] = "missing"
        else:
            passed_s, report_path = last_rec.split("|", 1)
            reconcile_ok = passed_s == "t"
            reconcile_detail["status"] = "present"
            reconcile_detail["passed"] = reconcile_ok
            reconcile_detail["report_path"] = report_path
        if not reconcile_ok:
            reasons.append(
                {
                    "code": "RECONCILIATION_REQUIRED",
                    "detail": "Reconciliation required by policy but no passing reconciliation result exists.",
                }
            )
    risk_checks.append(("reconciliation", reconcile_ok, reconcile_detail))

    latest_ticket = _psql_capture("select coalesce(ticket_id::text,'') from tickets order by created_at desc limit 1;")
    confirm_ok = True
    confirm_detail: dict = {"latest_ticket_id": latest_ticket or None}
    if latest_ticket:
        intended_count = int(_psql_capture(f"select count(*) from ledger_trades_intended where ticket_id = '{latest_ticket}';") or "0")
        confirmed_fills = int(_psql_capture(f"select count(*) from ledger_trades_fills where ticket_id = '{latest_ticket}';") or "0")
        confirm_detail.update({"intended_count": intended_count, "fills_count": confirmed_fills})
        if intended_count > 0 and confirmed_fills < intended_count:
            confirm_ok = False
            reasons.append({"code": "CONFIRMATION_MISSING", "detail": "Previous ticket has missing confirmations/fills."})
    risk_checks.append(("confirmations", confirm_ok, confirm_detail))

    unverified_enabled = int(
        _psql_capture(
            """
            select count(*)
            from config_universe
            where enabled = true
              and (notes is null or notes not like '%ETORO_VERIFIED%');
            """
        )
        or "0"
    )
    universe_ok = unverified_enabled == 0
    risk_checks.append(("universe_verified", universe_ok, {"unverified_enabled_count": unverified_enabled}))
    if not universe_ok:
        reasons.append({"code": "UNIVERSE_NOT_VERIFIED", "detail": "Enabled symbols must be eToro-verified before trading."})

    cash_movements = int(_psql_capture("select count(*) from ledger_cash_movements;") or "0")
    fills = int(_psql_capture("select count(*) from ledger_trades_fills;") or "0")
    ledger_ok = (cash_movements + fills) > 0
    risk_checks.append(("ledger_ready", ledger_ok, {"cash_movements": cash_movements, "fills": fills}))
    if not ledger_ok:
        reasons.append({"code": "LEDGER_EMPTY", "detail": "Ledger has no starting cash movements or fills; cannot size trades safely."})

    # v1 safety: we do not yet compute a real intended trade list (deltas + sizing).
    # Until implemented, riskguard must never approve a TRADE decision.
    trade_builder_ok = False
    risk_checks.append(("trade_builder", trade_builder_ok, {"status": "not_implemented"}))
    reasons.append(
        {
            "code": "TRADE_BUILDER_NOT_IMPLEMENTED",
            "detail": "Riskguard currently produces target weights only; intended trades + sizing are not implemented yet.",
        }
    )

    max_positions = int(policy.get("portfolio", {}).get("max_positions", 15))
    max_w = float(policy.get("portfolio", {}).get("max_position_weight", 0.075))
    cash_buffer = float(policy.get("portfolio", {}).get("min_cash_buffer", 0.03))
    budget = max(0.0, 1.0 - cash_buffer)
    n = max(1, min(max_positions, 9999))

    signals_raw = _psql_capture(
        f"""
        select internal_symbol || '|' || score::text || '|' || coalesce(rank::text,'')
        from signals_ranked
        where run_id = '{run_id}'
        order by rank nulls last, internal_symbol;
        """
    )
    signals: list[dict] = []
    for line in (signals_raw.splitlines() if signals_raw else []):
        sym, score_s, rank_s = line.split("|", 2)
        signals.append({"symbol": sym, "score": float(score_s), "rank": (int(rank_s) if rank_s else None)})

    top = signals[:n]
    targets: list[dict] = []
    if top:
        per = min(max_w, budget / float(len(top)))
        for s in top:
            targets.append({"symbol": s["symbol"], "target_weight": per})

    proposed = {
        "run_id": run_id,
        "asof_date": asof or None,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "git_commit": git_commit,
        "policy_hash": policy_hash,
        "policy": policy,
        "signals_count": len(signals),
        "targets": targets,
        "notes": "Proposed targets are weights only. Notional sizing requires ledger + reconciliation readiness.",
    }

    proposed_path = run_dir / "trades_proposed.json"
    proposed_path.write_text(json.dumps(proposed, indent=2, sort_keys=True), encoding="utf-8")

    approved = len(reasons) == 0 and all(p for _, p, _ in risk_checks)
    decision_type = "TRADE" if approved else "NO_TRADE"

    _psql_exec(f"delete from risk_checks where run_id = '{run_id}';")
    for name, passed, detail in risk_checks:
        detail_json = json.dumps(detail).replace("'", "''")
        _psql_exec(
            f"""
            insert into risk_checks(run_id, check_name, passed, details)
            values ('{run_id}', '{name}', {str(passed).lower()}, '{detail_json}'::jsonb)
            on conflict (run_id, check_name) do update set passed = excluded.passed, details = excluded.details;
            """
        )

    reasons_json = json.dumps(reasons).replace("'", "''")
    _psql_exec(
        f"""
        insert into decisions(run_id, approved, decision_type, reasons)
        values ('{run_id}', {str(approved).lower()}, '{decision_type}', '{reasons_json}'::jsonb)
        on conflict (run_id) do update set approved = excluded.approved, decision_type = excluded.decision_type, reasons = excluded.reasons;
        """
    )

    if approved:
        approved_payload = {
            "run_id": run_id,
            "asof_date": asof or None,
            "approved": True,
            "decision_type": "TRADE",
            "targets": targets,
            "trades": [],
        }
        out = run_dir / "trades_approved.json"
        out.write_text(json.dumps(approved_payload, indent=2, sort_keys=True), encoding="utf-8")
        print(f"Wrote {proposed_path}")
        print(f"Wrote {out}")
        print("RISKGUARD_APPROVED")
        return 0

    no_trade = {
        "run_id": run_id,
        "asof_date": asof or None,
        "approved": False,
        "decision_type": "NO_TRADE",
        "reasons": reasons,
        "risk_checks": [{"name": n, "passed": p, "detail": d} for n, p, d in risk_checks],
        "proposed_targets": targets,
    }
    out = run_dir / "no_trade.json"
    out.write_text(json.dumps(no_trade, indent=2, sort_keys=True), encoding="utf-8")
    print(f"Wrote {proposed_path}")
    print(f"Wrote {out}")
    _emit_alert_blocked(
        run_id=run_id,
        asof=asof,
        reasons=reasons,
        risk_checks=risk_checks,
        run_dir=run_dir,
        no_trade_path=out,
        proposed_path=proposed_path,
    )
    print("RISKGUARD_BLOCKED")
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
