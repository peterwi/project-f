#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ENV_FILE = ROOT / "config" / "secrets.env"
COMPOSE_FILE = ROOT / "docker" / "compose.yml"
PM_STATE_FILE = ROOT / "docs" / "PM_STATE.md"

RUNS_DIR = Path("/data/trading-ops/artifacts/runs")
TICKETS_DIR = Path("/data/trading-ops/artifacts/tickets")

TICKET_NAMESPACE = uuid.UUID("7d6dbdd0-3a1d-4ad9-a119-09b73a9a8db1")

def _fmt_decimal(value: object, decimals: int) -> str | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        d = Decimal(value)
    elif isinstance(value, float):
        d = Decimal(str(value))
    elif isinstance(value, str):
        if not value.strip():
            return None
        d = Decimal(value.strip())
    else:
        return None

    q = Decimal(1).scaleb(-max(0, int(decimals)))
    d = d.quantize(q, rounding=ROUND_HALF_UP)
    s = format(d, "f")
    if decimals > 0:
        s = s.rstrip("0").rstrip(".")
    return s


def _economic_material_input(payload: dict) -> dict:
    universe = payload.get("universe") or {}
    enabled_symbols = list(universe.get("enabled_symbols") or [])
    benchmark_symbols = list(universe.get("benchmark_symbols") or [])
    enabled_symbols.sort()
    benchmark_symbols.sort()

    order = {
        "data_quality": 10,
        "reconciliation": 20,
        "confirmations": 30,
        "universe_verified": 40,
        "ledger_ready": 50,
        "trade_builder": 60,
    }
    risk_checks_in = (payload.get("gate_statuses") or {}).get("risk_checks") or []
    risk_checks: list[dict] = []
    for rc in risk_checks_in:
        if not isinstance(rc, dict):
            continue
        name = str(rc.get("name") or "").strip()
        if not name:
            continue
        passed = bool(rc.get("passed", False))
        risk_checks.append({"name": name, "passed": passed})
    risk_checks = [rc for rc in risk_checks if rc["name"] in order]
    risk_checks.sort(key=lambda r: (order.get(r["name"], 999), r["name"]))

    reason_codes: list[str] = []
    for r in payload.get("blocking_reasons") or []:
        if not isinstance(r, dict):
            continue
        code = str(r.get("code") or "").strip()
        if code:
            reason_codes.append(code)
    reason_codes = sorted(set(reason_codes))

    base_currency = str(payload.get("base_currency") or "GBP")

    intended_in = list(payload.get("intended_trades") or [])
    intended: list[dict] = []
    for t in intended_in:
        if not isinstance(t, dict):
            continue
        side = str(t.get("side") or "").upper().strip() or None
        sym = str(t.get("internal_symbol") or "").strip() or None
        if not sym or not side:
            continue
        units_s = _fmt_decimal(t.get("units"), 6)
        notional_s = _fmt_decimal(t.get("notional_value_base"), 2)
        limit_s = _fmt_decimal(t.get("limit_price"), 4)
        ref_s = _fmt_decimal(t.get("reference_price"), 4)
        intended.append(
            {
                "internal_symbol": sym,
                "side": side,
                "order_type": (str(t.get("order_type") or "").strip() or None),
                "units": units_s,
                "notional_value_base": notional_s,
                "limit_price": limit_s,
                "reference_price": ref_s,
                "max_slippage_bps": (int(t["max_slippage_bps"]) if isinstance(t.get("max_slippage_bps"), int) else None),
            }
        )
    side_order = {"BUY": 0, "SELL": 1}
    intended.sort(
        key=lambda t: (
            t["internal_symbol"],
            side_order.get(t["side"], 9),
            str(t.get("order_type") or ""),
            str(t.get("units") or ""),
            str(t.get("notional_value_base") or ""),
            str(t.get("limit_price") or ""),
            str(t.get("reference_price") or ""),
            int(t.get("max_slippage_bps") or -1),
        )
    )

    fills_in = list(payload.get("confirmed_fills") or [])
    fills: list[dict] = []
    for f in fills_in:
        if not isinstance(f, dict):
            continue
        sym = str(f.get("internal_symbol") or "").strip() or None
        side = str(f.get("side") or "").upper().strip() or None
        status = str(f.get("executed_status") or "").strip() or None
        if not sym or not side or not status:
            continue
        fills.append(
            {
                "internal_symbol": sym,
                "side": side,
                "executed_status": status,
                "units": _fmt_decimal(f.get("units"), 6),
                "fill_price": _fmt_decimal(f.get("fill_price"), 4),
                "executed_value_base": _fmt_decimal(f.get("executed_value_base"), 2),
            }
        )
    fills.sort(
        key=lambda x: (
            x["internal_symbol"],
            x["side"],
            x["executed_status"],
            str(x.get("units") or ""),
            str(x.get("fill_price") or ""),
            str(x.get("executed_value_base") or ""),
        )
    )

    return {
        "schema": "economic_v1",
        "decision_type": str(payload.get("decision_type") or ""),
        "asof_date": str(payload.get("asof_date") or ""),
        "base_currency": base_currency,
        "universe": {"enabled_symbols": enabled_symbols, "benchmark_symbols": benchmark_symbols},
        "risk_checks": risk_checks,
        "blocking_reason_codes": reason_codes,
        "intended_trades": intended,
        "confirmed_fills": fills,
    }


def _read_kv_file(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if "=" not in line:
            continue
        k, v = line.split("=", 1)
        out[k.strip()] = v.strip()
    return out


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


def _dollar_quote(tag: str, content: str) -> tuple[str, str]:
    t = tag
    i = 0
    while f"${t}$" in content:
        i += 1
        t = f"{tag}{i}"
        if i > 50:
            raise ValueError("Failed to find safe dollar-quote tag.")
    return t, f"${t}$"


@dataclass(frozen=True)
class RunInputs:
    run_id: str
    run_dir: Path
    run_summary_md: Path
    no_trade_json: Path | None
    trades_proposed_json: Path | None
    trades_intended_json: Path | None


def _load_run_inputs(run_id: str) -> RunInputs:
    run_dir = RUNS_DIR / run_id
    run_summary_md = run_dir / "run_summary.md"
    if not run_summary_md.exists():
        raise FileNotFoundError(f"Missing required input: {run_summary_md}")

    no_trade_json = run_dir / "no_trade.json"
    trades_proposed_json = run_dir / "trades_proposed.json"
    trades_intended_json = run_dir / "trades_intended.json"
    return RunInputs(
        run_id=run_id,
        run_dir=run_dir,
        run_summary_md=run_summary_md,
        no_trade_json=(no_trade_json if no_trade_json.exists() else None),
        trades_proposed_json=(trades_proposed_json if trades_proposed_json.exists() else None),
        trades_intended_json=(trades_intended_json if trades_intended_json.exists() else None),
    )


def _resolve_run_id(cli_run_id: str | None) -> str:
    if cli_run_id:
        rid = cli_run_id.strip()
        uuid.UUID(rid)
        return rid
    if not PM_STATE_FILE.exists():
        raise FileNotFoundError(f"Missing {PM_STATE_FILE}; provide --run-id explicitly.")
    state = _read_kv_file(PM_STATE_FILE)
    run_id = state.get("LAST_RUN_ID", "").strip()
    if not run_id:
        raise RuntimeError("LAST_RUN_ID is empty in docs/PM_STATE.md; provide --run-id explicitly.")
    uuid.UUID(run_id)
    return run_id


@dataclass(frozen=True)
class ExistingTicket:
    ticket_id: str
    created_at_utc: str


def _get_existing_ticket(run_id: str) -> ExistingTicket | None:
    raw = _psql_capture(
        f"""
        select
          coalesce(ticket_id::text,'') || '|' ||
          coalesce(to_char(created_at at time zone 'utc','YYYY-MM-DD\"T\"HH24:MI:SS\"Z\"'),'')
        from tickets
        where run_id = '{run_id}';
        """
    )
    if not raw:
        return None
    ticket_id, created_at_utc = raw.split("|", 1)
    if not ticket_id:
        return None
    return ExistingTicket(ticket_id=ticket_id, created_at_utc=created_at_utc)


def _get_or_create_ticket_id(run_id: str, decision_type: str) -> str:
    existing = _get_existing_ticket(run_id)
    if existing:
        return existing.ticket_id
    name = f"{run_id}:{decision_type}"
    return str(uuid.uuid5(TICKET_NAMESPACE, name))


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _run_metadata(run_id: str) -> dict[str, str]:
    raw = _psql_capture(
        f"""
        select
          coalesce(config_hash,'') || '|' ||
          coalesce(git_commit,'') || '|' ||
          coalesce(asof_date::text,'')
        from runs
        where run_id = '{run_id}';
        """
    )
    if not raw:
        return {"config_hash": "", "git_commit": "", "asof_date": ""}
    config_hash, git_commit, asof_date = raw.split("|", 2)
    return {"config_hash": config_hash, "git_commit": git_commit, "asof_date": asof_date}


def _universe_counts() -> dict[str, int]:
    enabled = int(_psql_capture("select count(*) from config_universe where enabled = true;") or "0")
    benchmarks = int(
        _psql_capture(
            "select count(*) from config_universe where lower(coalesce(instrument_type,'')) in ('benchmark','index');"
        )
        or "0"
    )
    total = int(
        _psql_capture(
            """
            select count(*)
            from config_universe
            where enabled = true
               or lower(coalesce(instrument_type,'')) in ('benchmark','index');
            """
        )
        or "0"
    )
    return {"enabled_count": enabled, "benchmark_count": benchmarks, "total_count": total}


def _universe_symbols() -> dict[str, list[str]]:
    enabled_raw = _psql_capture(
        """
        select internal_symbol
        from config_universe
        where enabled = true
        order by internal_symbol;
        """
    )
    benchmark_raw = _psql_capture(
        """
        select internal_symbol
        from config_universe
        where lower(coalesce(instrument_type,'')) in ('benchmark','index')
        order by internal_symbol;
        """
    )
    enabled = [s.strip() for s in (enabled_raw.splitlines() if enabled_raw else []) if s.strip()]
    benchmarks = [s.strip() for s in (benchmark_raw.splitlines() if benchmark_raw else []) if s.strip()]
    return {"enabled_symbols": enabled, "benchmark_symbols": benchmarks}


def _load_risk_checks_for_run(run_id: str) -> list[dict]:
    raw = _psql_capture(
        f"""
        select
          check_name || '|' ||
          passed::text || '|' ||
          coalesce(details::text,'{{}}')
        from risk_checks
        where run_id = '{run_id}'::uuid
        order by check_name;
        """
    )
    checks: list[dict] = []
    for line in (raw.splitlines() if raw else []):
        name, passed_s, detail_s = line.split("|", 2)
        passed = passed_s.strip().lower() in ("t", "true", "1", "yes")
        try:
            detail = json.loads(detail_s) if detail_s else {}
        except Exception:
            detail = {"raw": (detail_s or "")}
        checks.append({"name": name.strip(), "passed": passed, "detail": detail})

    order = {
        "data_quality": 10,
        "reconciliation": 20,
        "confirmations": 30,
        "universe_verified": 40,
        "ledger_ready": 50,
        "trade_builder": 60,
    }
    checks = [c for c in checks if str(c.get("name") or "") in order]
    checks.sort(key=lambda c: (order.get(str(c.get("name") or ""), 999), str(c.get("name") or "")))
    return checks

def _load_confirmed_fills(ticket_id: str) -> list[dict]:
    raw = _psql_capture(
        f"""
        select
          sequence::text || '|' ||
          internal_symbol || '|' ||
          coalesce(side,'') || '|' ||
          executed_status || '|' ||
          coalesce(executed_value_base::text,'') || '|' ||
          coalesce(units::text,'') || '|' ||
          coalesce(fill_price::text,'') || '|' ||
          coalesce(to_char(filled_at at time zone 'utc','YYYY-MM-DD\"T\"HH24:MI:SS\"Z\"'),'') || '|' ||
          coalesce(notes,'')
        from ledger_trades_fills
        where ticket_id = '{ticket_id}'::uuid
        order by sequence;
        """
    )
    fills: list[dict] = []
    for line in (raw.splitlines() if raw else []):
        seq_s, sym, side, status, value_s, units_s, px_s, filled_at, notes = line.split("|", 8)
        fills.append(
            {
                "sequence": int(seq_s),
                "internal_symbol": sym,
                "side": side,
                "executed_status": status,
                "executed_value_base": (float(value_s) if value_s else None),
                "units": (float(units_s) if units_s else None),
                "fill_price": (float(px_s) if px_s else None),
                "filled_at": (filled_at if filled_at else None),
                "notes": (notes if notes else None),
            }
        )
    return fills


def _parse_run_summary_steps(path: Path) -> dict[str, str]:
    """
    Deterministically parse the `## Steps` section of run_summary.md.
    Expected lines like: `- data-quality: `OK``.
    """
    txt = path.read_text(encoding="utf-8").splitlines()
    in_steps = False
    steps: dict[str, str] = {}
    for line in txt:
        if line.startswith("## "):
            in_steps = line.strip() == "## Steps"
            continue
        if not in_steps:
            continue
        line = line.strip()
        if not line.startswith("- "):
            if steps and (not line):
                # allow blank line after section ends
                continue
            continue
        # Ignore sub-bullets like "- report: ..."
        if line.startswith("- report:"):
            continue
        # Pattern: - <name>: `<status>`
        if ": `" not in line or not line.endswith("`"):
            continue
        name, rest = line[2:].split(": ", 1)
        if not rest.startswith("`") or not rest.endswith("`"):
            continue
        status = rest.strip("`")
        steps[name.strip()] = status
    return steps


def _parse_run_summary_step_reports(path: Path) -> dict[str, list[str]]:
    """
    Parse report paths under each step in the `## Steps` section of run_summary.md.
    Expected lines like:
      - market-fetch: `OK`
        - report: `/data/...`
    """
    txt = path.read_text(encoding="utf-8").splitlines()
    in_steps = False
    current_step: str | None = None
    reports: dict[str, list[str]] = {}
    for raw in txt:
        if raw.startswith("## "):
            in_steps = raw.strip() == "## Steps"
            current_step = None
            continue
        if not in_steps:
            continue
        line = raw.strip()
        if not line:
            continue
        if line.startswith("- ") and ": `" in line and line.endswith("`") and not line.startswith("- report:"):
            name, rest = line[2:].split(": ", 1)
            if rest.startswith("`") and rest.endswith("`"):
                current_step = name.strip()
                reports.setdefault(current_step, [])
            continue
        if line.startswith("- report:") and current_step:
            # Pattern: - report: `<path>`
            if "`" in line:
                parts = line.split("`")
                if len(parts) >= 2:
                    p = parts[1].strip()
                    if p:
                        reports.setdefault(current_step, []).append(p)
    # Deterministic de-dupe + ordering per step.
    out: dict[str, list[str]] = {}
    for k in sorted(reports.keys()):
        out[k] = sorted(set(reports[k]))
    return out


def _render_ticket_md(payload: dict) -> str:
    reasons_json = json.dumps(payload.get("blocking_reasons", []), indent=2, sort_keys=True)
    gate_statuses_json = json.dumps(payload.get("gate_statuses", {}), indent=2, sort_keys=True)
    intended_trades = payload.get("intended_trades") or []
    confirmed_fills = payload.get("confirmed_fills") or []

    lines: list[str] = []
    lines.append("# Trade Ticket")
    lines.append("")
    lines.append(f"## DECISION: {payload['decision_type']}")
    lines.append("")
    lines.append(f"- ticket_id: `{payload['ticket_id']}`")
    lines.append(f"- run_id: `{payload['run_id']}`")
    lines.append(f"- asof_date: `{payload.get('asof_date','')}`")
    lines.append(f"- created_utc: `{payload['created_utc']}`")
    if payload.get("meta", {}).get("material_hash"):
        lines.append(f"- material_hash: `{payload['meta']['material_hash']}`")
    lines.append(f"- decision: `{payload['decision_type']}`")
    lines.append(f"- execution_window_uk: `{payload['execution_window_uk']}`")
    lines.append("")
    lines.append("## Universe")
    lines.append("")
    lines.append(f"- total_count: `{payload['universe']['total_count']}`")
    lines.append(f"- enabled_count: `{payload['universe']['enabled_count']}`")
    lines.append(f"- benchmark_count: `{payload['universe']['benchmark_count']}`")
    enabled_syms = payload["universe"].get("enabled_symbols") or []
    bench_syms = payload["universe"].get("benchmark_symbols") or []
    if enabled_syms is not None:
        lines.append(f"- enabled_symbols: `{', '.join(enabled_syms)}`")
    if bench_syms is not None:
        lines.append(f"- benchmark_symbols: `{', '.join(bench_syms)}`")
    lines.append("")
    lines.append("## Gate statuses")
    lines.append("")
    lines.append("```json")
    lines.append(gate_statuses_json)
    lines.append("```")
    lines.append("")
    step_reports = (payload.get("gate_statuses") or {}).get("ops_step_reports") or {}
    if isinstance(step_reports, dict) and step_reports:
        lines.append("## Data & model artifacts")
        lines.append("")
        for step_name in sorted(step_reports.keys()):
            paths = step_reports.get(step_name) or []
            if not isinstance(paths, list) or not paths:
                continue
            lines.append(f"- {step_name}:")
            for p in sorted({str(x) for x in paths if x}):
                lines.append(f"  - `{p}`")
        lines.append("")
    lines.append("## Inputs (pointers)")
    lines.append("")
    for k, v in payload.get("inputs", {}).items():
        lines.append(f"- {k}: `{v}`")
    lines.append("")
    lines.append("## Outputs (pointers)")
    lines.append("")
    for k, v in payload.get("outputs", {}).items():
        lines.append(f"- {k}: `{v}`")
    lines.append("")

    if intended_trades:
        lines.append("## Intended trades (draft)")
        lines.append("")
        lines.append("These are deterministic intended trades sized from the current ledger/snapshot and target weights.")
        lines.append("Do not execute unless DECISION=TRADE and reconciliation is passing.")
        lines.append("")
        lines.append("Execution rules:")
        lines.append("- Skip a line if the instrument is not findable as a stock on the broker (CFD-only / not supported).")
        lines.append("- Record any skipped line and reason in the confirmations flow.")
        lines.append("")
        for t in intended_trades:
            side = str(t.get("side", "")).upper()
            sym = str(t.get("internal_symbol", ""))
            units = t.get("units", None)
            notional = t.get("notional_value_base", None)
            ref_px = t.get("reference_price", None)
            slippage = t.get("max_slippage_bps", None)
            if units is None:
                units_s = "N/A"
            elif isinstance(units, (int, float)):
                units_s = f"{float(units):.6f}".rstrip("0").rstrip(".")
            else:
                units_s = str(units)
            notional_s = (f"{float(notional):.2f}" if isinstance(notional, (int, float)) else str(notional)) if notional is not None else ""
            ref_s = (f"{float(ref_px):.4f}" if isinstance(ref_px, (int, float)) else str(ref_px)) if ref_px is not None else ""
            slip_s = (str(slippage) if slippage is not None else "")
            parts = [side, sym]
            if units_s and units_s != "N/A":
                parts.append(f"units={units_s}")
            if notional_s:
                parts.append(f"~{payload.get('base_currency','GBP')}{notional_s}")
            if ref_s:
                parts.append(f"ref={ref_s}")
            if slip_s:
                parts.append(f"slip={slip_s}bps")
            lines.append(f"- {' '.join(parts)}")
        lines.append("")

    if payload["decision_type"] == "NO_TRADE":
        lines.append("## NO_TRADE (blocked)")
        lines.append("")
        lines.append("Blocking reasons (verbatim from `no_trade.json`):")
        lines.append("")
        lines.append("```json")
        lines.append(reasons_json)
        lines.append("```")
        lines.append("")
        if confirmed_fills:
            lines.append("## Confirmed fills (recorded)")
            lines.append("")
            lines.append("Fills were recorded for this ticket. Ensure this is intended; NO-TRADE normally implies no execution.")
            lines.append("")
            for f in confirmed_fills:
                side = str(f.get("side", "")).upper()
                sym = str(f.get("internal_symbol", ""))
                status = str(f.get("executed_status", ""))
                units = f.get("units", None)
                value = f.get("executed_value_base", None)
                px = f.get("fill_price", None)
                filled_at = f.get("filled_at", None)
                units_s = (f"{units:g}" if isinstance(units, (int, float)) else str(units)) if units is not None else ""
                value_s = (f"{float(value):.2f}" if isinstance(value, (int, float)) else str(value)) if value is not None else ""
                px_s = (f"{float(px):.4f}" if isinstance(px, (int, float)) else str(px)) if px is not None else ""
                at_s = str(filled_at or "")
                parts = [status, side, sym]
                if units_s:
                    parts.append(f"units={units_s}")
                if value_s:
                    parts.append(f"value={payload.get('base_currency','GBP')}{value_s}")
                if px_s:
                    parts.append(f"px={px_s}")
                if at_s:
                    parts.append(f"at={at_s}")
                lines.append(f"- {' '.join(parts)}")
            lines.append("")
        return "\n".join(lines)

    if confirmed_fills:
        lines.append("## Confirmed fills")
        lines.append("")
        for f in confirmed_fills:
            side = str(f.get("side", "")).upper()
            sym = str(f.get("internal_symbol", ""))
            status = str(f.get("executed_status", ""))
            units = f.get("units", None)
            value = f.get("executed_value_base", None)
            px = f.get("fill_price", None)
            filled_at = f.get("filled_at", None)
            units_s = (f"{units:g}" if isinstance(units, (int, float)) else str(units)) if units is not None else ""
            value_s = (f"{float(value):.2f}" if isinstance(value, (int, float)) else str(value)) if value is not None else ""
            px_s = (f"{float(px):.4f}" if isinstance(px, (int, float)) else str(px)) if px is not None else ""
            at_s = str(filled_at or "")
            parts = [status, side, sym]
            if units_s:
                parts.append(f"units={units_s}")
            if value_s:
                parts.append(f"value={payload.get('base_currency','GBP')}{value_s}")
            if px_s:
                parts.append(f"px={px_s}")
            if at_s:
                parts.append(f"at={at_s}")
            lines.append(f"- {' '.join(parts)}")
        lines.append("")

    lines.append("## Confirmations")
    lines.append("")
    lines.append("Submit fills for TRADE tickets after manual execution (or SKIPPED for dry-run).")
    lines.append("")
    lines.append(f"- ticket_id: `{payload['ticket_id']}`")
    lines.append(f"- confirmations_dir: `{payload.get('artifact_paths', {}).get('ticket_dir','')}/confirmations/`")
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Render a deterministic trade ticket (Markdown + JSON) for a run_id.")
    parser.add_argument("--run-id", help="Target run_id (defaults to LAST_RUN_ID in docs/PM_STATE.md).")
    args = parser.parse_args()

    if not ENV_FILE.exists():
        raise FileNotFoundError(f"Missing {ENV_FILE}; create it from config/secrets.env.example")

    run_id = _resolve_run_id(args.run_id)
    inputs = _load_run_inputs(run_id)

    existing_ticket = _get_existing_ticket(run_id)
    created_utc = (
        existing_ticket.created_at_utc
        if (existing_ticket and existing_ticket.created_at_utc)
        else datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    )
    execution_window_uk = "UK time 14:30–16:00"

    run_meta = _run_metadata(run_id)
    universe = {**_universe_counts(), **_universe_symbols()}
    ops_steps = _parse_run_summary_steps(inputs.run_summary_md)
    ops_step_reports = _parse_run_summary_step_reports(inputs.run_summary_md)

    decision_type = "NO_TRADE" if inputs.no_trade_json else "TRADE"
    ticket_id = _get_or_create_ticket_id(run_id, decision_type)

    gate_statuses: dict = {"ops_steps": ops_steps, "ops_step_reports": ops_step_reports}
    blocking_reasons: list[dict] = []
    no_trade_asof: str = ""
    risk_checks = _load_risk_checks_for_run(run_id)
    if risk_checks:
        gate_statuses["risk_checks"] = risk_checks
    if inputs.no_trade_json:
        no_trade = _read_json(inputs.no_trade_json)
        if not risk_checks:
            gate_statuses["risk_checks"] = no_trade.get("risk_checks", [])
        blocking_reasons = no_trade.get("reasons", [])
        no_trade_asof = str(no_trade.get("asof_date", "") or "")

    trades_proposed_asof: str = ""
    if inputs.trades_proposed_json:
        trades_proposed = _read_json(inputs.trades_proposed_json)
        trades_proposed_asof = str(trades_proposed.get("asof_date", "") or "")

    trades_intended_asof: str = ""
    intended_trades: list[dict] = []
    base_currency = "GBP"
    if inputs.trades_intended_json:
        trades_intended = _read_json(inputs.trades_intended_json)
        trades_intended_asof = str(trades_intended.get("asof_date_used", "") or "")
        base_currency = str(trades_intended.get("base_currency", "") or base_currency)
        intended_trades = list(trades_intended.get("intended_trades", []) or [])
        side_order = {"BUY": 0, "SELL": 1}
        intended_trades.sort(
            key=lambda t: (
                str(t.get("internal_symbol") or ""),
                side_order.get(str(t.get("side") or "").upper(), 9),
                int(t.get("sequence") or 0),
            )
        )

    asof_date = run_meta.get("asof_date") or no_trade_asof or trades_intended_asof or trades_proposed_asof

    ticket_dir = TICKETS_DIR / ticket_id
    ticket_dir.mkdir(parents=True, exist_ok=True)

    outputs = {
        "ticket_md": str(ticket_dir / "ticket.md"),
        "ticket_json": str(ticket_dir / "ticket.json"),
    }
    material_hash_path = ticket_dir / "material_hash.txt"
    artifact_paths = {
        "run_dir": str(inputs.run_dir),
        "ticket_dir": str(ticket_dir),
        "material_hash_txt": str(material_hash_path),
        **outputs,
    }
    payload = {
        "ticket_id": ticket_id,
        "run_id": run_id,
        "asof_date": asof_date,
        "base_currency": base_currency,
        "created_utc": created_utc,
        "decision_type": decision_type,
        "execution_window_uk": execution_window_uk,
        "universe": universe,
        "gate_statuses": gate_statuses,
        "blocking_reasons": blocking_reasons,
        "intended_trades": intended_trades,
        "confirmed_fills": _load_confirmed_fills(ticket_id),
        "git_commit": run_meta.get("git_commit", ""),
        "config_hash": run_meta.get("config_hash", ""),
        "artifact_paths": artifact_paths,
        "inputs": {
            "run_summary_md": str(inputs.run_summary_md),
            "no_trade_json": (str(inputs.no_trade_json) if inputs.no_trade_json else "-"),
            "trades_proposed_json": (str(inputs.trades_proposed_json) if inputs.trades_proposed_json else "-"),
            "trades_intended_json": (str(inputs.trades_intended_json) if inputs.trades_intended_json else "-"),
        },
        "outputs": outputs,
    }

    material_input = _economic_material_input(payload)
    material_hash = hashlib.sha256(
        json.dumps(material_input, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    payload.setdefault("meta", {})["material_hash"] = material_hash
    payload.setdefault("meta", {})["material_schema"] = str(material_input.get("schema") or "economic_v1")

    md = _render_ticket_md(payload)
    ticket_md_path = Path(outputs["ticket_md"])
    ticket_json_path = Path(outputs["ticket_json"])
    ticket_md_path.write_text(md, encoding="utf-8")
    ticket_json_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    material_hash_path.write_text(material_hash + "\n", encoding="utf-8")

    md_tag, md_dq = _dollar_quote("md", md)
    json_str = json.dumps(payload, sort_keys=True)
    json_tag, json_dq = _dollar_quote("json", json_str)

    sql = f"""
    insert into tickets(ticket_id, run_id, ticket_type, status, rendered_md, rendered_json)
    values (
      '{ticket_id}'::uuid,
      '{run_id}'::uuid,
      '{decision_type}',
      '{decision_type}',
      {md_dq}{md}{md_dq},
      {json_dq}{json_str}{json_dq}::jsonb
    )
    on conflict (run_id) do update set
      ticket_type = excluded.ticket_type,
      status = excluded.status,
      rendered_md = excluded.rendered_md,
      rendered_json = excluded.rendered_json;
    """
    _psql_exec(sql)

    # Link intended trades to the ticket_id (enables deterministic confirmation gating + fill matching).
    _psql_exec(
        f"""
        update ledger_trades_intended
        set ticket_id = '{ticket_id}'::uuid
        where run_id = '{run_id}'::uuid
          and (ticket_id is null or ticket_id <> '{ticket_id}'::uuid);
        """
    )

    print(f"ticket_id={ticket_id}")
    print(f"run_id={run_id}")
    print(f"decision_type={decision_type}")
    print(f"ticket_dir={ticket_dir}")
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
