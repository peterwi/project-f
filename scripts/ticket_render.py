#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ENV_FILE = ROOT / "config" / "secrets.env"
COMPOSE_FILE = ROOT / "docker" / "compose.yml"
PM_STATE_FILE = ROOT / "docs" / "PM_STATE.md"

RUNS_DIR = Path("/data/trading-ops/artifacts/runs")
TICKETS_DIR = Path("/data/trading-ops/artifacts/tickets")

TICKET_NAMESPACE = uuid.UUID("7d6dbdd0-3a1d-4ad9-a119-09b73a9a8db1")


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


def _load_run_inputs(run_id: str) -> RunInputs:
    run_dir = RUNS_DIR / run_id
    run_summary_md = run_dir / "run_summary.md"
    if not run_summary_md.exists():
        raise FileNotFoundError(f"Missing required input: {run_summary_md}")

    no_trade_json = run_dir / "no_trade.json"
    trades_proposed_json = run_dir / "trades_proposed.json"
    return RunInputs(
        run_id=run_id,
        run_dir=run_dir,
        run_summary_md=run_summary_md,
        no_trade_json=(no_trade_json if no_trade_json.exists() else None),
        trades_proposed_json=(trades_proposed_json if trades_proposed_json.exists() else None),
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
    return {"enabled_count": enabled, "benchmark_count": benchmarks, "total_count": enabled + benchmarks}


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


def _render_ticket_md(payload: dict) -> str:
    reasons_json = json.dumps(payload.get("blocking_reasons", []), indent=2)
    gate_statuses_json = json.dumps(payload.get("gate_statuses", {}), indent=2)

    lines: list[str] = []
    lines.append("# Trade Ticket")
    lines.append("")
    lines.append(f"## DECISION: {payload['decision_type'].replace('_', '-')}")
    lines.append("")
    lines.append(f"- ticket_id: `{payload['ticket_id']}`")
    lines.append(f"- run_id: `{payload['run_id']}`")
    lines.append(f"- asof_date: `{payload.get('asof_date','')}`")
    lines.append(f"- created_utc: `{payload['created_utc']}`")
    lines.append(f"- decision: `{payload['decision_type']}`")
    lines.append(f"- execution_window_uk: `{payload['execution_window_uk']}` (DST-aware: TBD)")
    lines.append("")
    lines.append("## Universe")
    lines.append("")
    lines.append(f"- total_count: `{payload['universe']['total_count']}`")
    lines.append(f"- enabled_count: `{payload['universe']['enabled_count']}`")
    lines.append(f"- benchmark_count: `{payload['universe']['benchmark_count']}`")
    lines.append("")
    lines.append("## Gate statuses")
    lines.append("")
    lines.append("```json")
    lines.append(gate_statuses_json)
    lines.append("```")
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

    if payload["decision_type"] == "NO_TRADE":
        lines.append("## NO-TRADE (blocked)")
        lines.append("")
        lines.append("Blocking reasons (verbatim from `no_trade.json`):")
        lines.append("")
        lines.append("```json")
        lines.append(reasons_json)
        lines.append("```")
        lines.append("")
        return "\n".join(lines)

    lines.append("## TRADE (not implemented in v1)")
    lines.append("")
    lines.append("This system does not yet support deterministic intended trade rendering.")
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
    universe = _universe_counts()
    ops_steps = _parse_run_summary_steps(inputs.run_summary_md)

    decision_type = "NO_TRADE" if inputs.no_trade_json else "TRADE"
    ticket_id = _get_or_create_ticket_id(run_id, decision_type)

    gate_statuses: dict = {"ops_steps": ops_steps}
    blocking_reasons: list[dict] = []
    no_trade_asof: str = ""
    if inputs.no_trade_json:
        no_trade = _read_json(inputs.no_trade_json)
        gate_statuses["risk_checks"] = no_trade.get("risk_checks", [])
        blocking_reasons = no_trade.get("reasons", [])
        no_trade_asof = str(no_trade.get("asof_date", "") or "")

    trades_proposed_asof: str = ""
    if inputs.trades_proposed_json:
        trades_proposed = _read_json(inputs.trades_proposed_json)
        trades_proposed_asof = str(trades_proposed.get("asof_date", "") or "")

    asof_date = run_meta.get("asof_date") or no_trade_asof or trades_proposed_asof

    ticket_dir = TICKETS_DIR / ticket_id
    ticket_dir.mkdir(parents=True, exist_ok=True)

    outputs = {
        "ticket_md": str(ticket_dir / "ticket.md"),
        "ticket_json": str(ticket_dir / "ticket.json"),
    }
    artifact_paths = {
        "run_dir": str(inputs.run_dir),
        "ticket_dir": str(ticket_dir),
        **outputs,
    }
    payload = {
        "ticket_id": ticket_id,
        "run_id": run_id,
        "asof_date": asof_date,
        "created_utc": created_utc,
        "decision_type": decision_type,
        "execution_window_uk": execution_window_uk,
        "universe": universe,
        "gate_statuses": gate_statuses,
        "blocking_reasons": blocking_reasons,
        "git_commit": run_meta.get("git_commit", ""),
        "config_hash": run_meta.get("config_hash", ""),
        "artifact_paths": artifact_paths,
        "inputs": {
            "run_summary_md": str(inputs.run_summary_md),
            "no_trade_json": (str(inputs.no_trade_json) if inputs.no_trade_json else ""),
            "trades_proposed_json": (str(inputs.trades_proposed_json) if inputs.trades_proposed_json else ""),
        },
        "outputs": outputs,
    }

    md = _render_ticket_md(payload)
    ticket_md_path = Path(outputs["ticket_md"])
    ticket_json_path = Path(outputs["ticket_json"])
    ticket_md_path.write_text(md, encoding="utf-8")
    ticket_json_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")

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
