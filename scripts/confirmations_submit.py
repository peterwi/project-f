#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ENV_FILE = ROOT / "config" / "secrets.env"
COMPOSE_FILE = ROOT / "docker" / "compose.yml"
PM_STATE_FILE = ROOT / "docs" / "PM_STATE.md"

TICKETS_DIR = Path("/data/trading-ops/artifacts/tickets")


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


def _resolve_ticket_id(cli_ticket_id: str | None, cli_run_id: str | None) -> str:
    if cli_ticket_id:
        tid = cli_ticket_id.strip()
        uuid.UUID(tid)
        return tid

    if cli_run_id:
        rid = cli_run_id.strip()
        uuid.UUID(rid)
        tid = _psql_capture(f"select coalesce(ticket_id::text,'') from tickets where run_id = '{rid}';")
        if not tid:
            raise RuntimeError(f"No ticket found for run_id={rid}")
        uuid.UUID(tid)
        return tid

    if not PM_STATE_FILE.exists():
        raise FileNotFoundError(f"Missing {PM_STATE_FILE}; provide --ticket-id or --run-id explicitly.")
    state = _read_kv_file(PM_STATE_FILE)
    tid = state.get("LAST_TICKET_ID", "").strip()
    if tid:
        uuid.UUID(tid)
        return tid
    rid = state.get("LAST_RUN_ID", "").strip()
    if not rid:
        raise RuntimeError("docs/PM_STATE.md missing LAST_TICKET_ID and LAST_RUN_ID; provide --ticket-id/--run-id explicitly.")
    uuid.UUID(rid)
    tid = _psql_capture(f"select coalesce(ticket_id::text,'') from tickets where run_id = '{rid}';")
    if not tid:
        raise RuntimeError(f"No ticket found for LAST_RUN_ID={rid}")
    uuid.UUID(tid)
    return tid


def _load_ticket_payload(ticket_id: str) -> dict:
    ticket_json = TICKETS_DIR / ticket_id / "ticket.json"
    if not ticket_json.exists():
        raise FileNotFoundError(f"Missing ticket artifact: {ticket_json}")
    return json.loads(ticket_json.read_text(encoding="utf-8"))


def _load_fills(path: Path) -> list[dict]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(raw, list):
        fills = raw
    elif isinstance(raw, dict) and isinstance(raw.get("fills"), list):
        fills = raw["fills"]
    else:
        raise ValueError("fills JSON must be a list or an object with a top-level 'fills' list")

    out: list[dict] = []
    for i, f in enumerate(fills):
        if not isinstance(f, dict):
            raise ValueError(f"fills[{i}] must be an object")

        seq = f.get("sequence")
        sym = f.get("internal_symbol")
        side = str(f.get("side") or "").upper().strip()
        status = str(f.get("executed_status") or "").upper().strip()
        if not isinstance(seq, int) or seq < 1:
            raise ValueError(f"fills[{i}].sequence must be int >= 1")
        if not isinstance(sym, str) or not sym.strip():
            raise ValueError(f"fills[{i}].internal_symbol must be non-empty string")
        if side not in ("BUY", "SELL"):
            raise ValueError(f"fills[{i}].side must be BUY or SELL")
        if status not in ("DONE", "SKIPPED", "FAILED", "PARTIAL"):
            raise ValueError(f"fills[{i}].executed_status must be DONE|SKIPPED|FAILED|PARTIAL")

        executed_value_base = f.get("executed_value_base", None)
        units = f.get("units", None)
        fill_price = f.get("fill_price", None)
        filled_at = str(f.get("filled_at") or "").strip()
        notes = str(f.get("notes") or "").strip()

        if executed_value_base is None and units is not None and fill_price is not None:
            executed_value_base = float(units) * float(fill_price)
        if executed_value_base is not None and float(executed_value_base) < 0:
            raise ValueError(f"fills[{i}].executed_value_base must be >= 0 (store magnitude; side encodes direction)")
        if units is not None and float(units) < 0:
            raise ValueError(f"fills[{i}].units must be >= 0")
        if fill_price is not None and float(fill_price) < 0:
            raise ValueError(f"fills[{i}].fill_price must be >= 0")
        if status in ("DONE", "PARTIAL") and filled_at:
            # Basic ISO-8601 sanity: allow "Z" suffix.
            if "T" not in filled_at:
                raise ValueError(f"fills[{i}].filled_at must be ISO-8601 datetime (got {filled_at!r})")

        out.append(
            {
                "sequence": int(seq),
                "internal_symbol": sym.strip(),
                "side": side,
                "executed_status": status,
                "executed_value_base": (float(executed_value_base) if executed_value_base is not None else None),
                "units": (float(units) if units is not None else None),
                "fill_price": (float(fill_price) if fill_price is not None else None),
                "filled_at": (filled_at if filled_at else None),
                "notes": (notes if notes else None),
            }
        )

    out.sort(key=lambda r: int(r["sequence"]))
    return out


def _render_confirmation_md(payload: dict) -> str:
    lines: list[str] = []
    lines.append("# Confirmation Submission")
    lines.append("")
    lines.append(f"- confirmation_uuid: `{payload['confirmation_uuid']}`")
    lines.append(f"- ticket_id: `{payload['ticket_id']}`")
    lines.append(f"- run_id: `{payload['run_id']}`")
    lines.append(f"- asof_date: `{payload.get('asof_date','')}`")
    lines.append(f"- created_utc: `{payload['created_utc']}`")
    lines.append(f"- submitted_by: `{payload.get('submitted_by','')}`")
    lines.append(f"- confirmation_type: `{payload['confirmation_type']}`")
    lines.append("")
    lines.append("## Notes")
    lines.append("")
    lines.append(payload.get("notes", "") or "")
    lines.append("")
    lines.append("## Pointers")
    lines.append("")
    for k, v in payload.get("pointers", {}).items():
        lines.append(f"- {k}: `{v}`")
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Submit a deterministic confirmation payload for a ticket.")
    parser.add_argument("--ticket-id", help="Target ticket_id (uuid). Defaults to LAST_TICKET_ID in docs/PM_STATE.md.")
    parser.add_argument("--run-id", help="Alternative lookup by run_id (uuid).")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "--ack-no-trade",
        action="store_true",
        help="Acknowledge a NO-TRADE ticket (no eToro automation; no fills).",
    )
    mode.add_argument("--fills-json", help="Path to a fills JSON file (writes into ledger_trades_fills).")
    parser.add_argument(
        "--allow-non-trade-ticket",
        action="store_true",
        help="Allow submitting fills even if ticket decision_type is not TRADE (testing only).",
    )
    parser.add_argument("--submitted-by", default="operator", help="Human identifier (free text).")
    parser.add_argument("--notes", default="", help="Optional notes.")
    args = parser.parse_args()

    if not ENV_FILE.exists():
        raise FileNotFoundError(f"Missing {ENV_FILE}; create it from config/secrets.env.example")

    ticket_id = _resolve_ticket_id(args.ticket_id, args.run_id)
    ticket = _load_ticket_payload(ticket_id)

    decision_type = str(ticket.get("decision_type") or "")
    if args.ack_no_trade and decision_type != "NO_TRADE":
        raise RuntimeError(f"--ack-no-trade requires ticket decision_type=NO_TRADE (got {decision_type!r})")
    if args.fills_json and decision_type != "TRADE" and not args.allow_non_trade_ticket:
        raise RuntimeError(f"--fills-json requires ticket decision_type=TRADE (got {decision_type!r})")

    created_utc = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    confirmation_uuid = str(uuid.uuid4())

    ticket_dir = TICKETS_DIR / ticket_id
    conf_dir = ticket_dir / "confirmations" / confirmation_uuid
    conf_dir.mkdir(parents=True, exist_ok=True)

    fills: list[dict] = []
    confirmation_type = "ACK_NO_TRADE"
    fills_path: Path | None = None
    if args.fills_json:
        confirmation_type = "TRADE_FILLS"
        fills_path = Path(args.fills_json).expanduser()
        if not fills_path.exists():
            raise FileNotFoundError(f"Missing fills JSON: {fills_path}")
        fills = _load_fills(fills_path)

    payload = {
        "confirmation_uuid": confirmation_uuid,
        "confirmation_type": confirmation_type,
        "ticket_id": ticket_id,
        "run_id": str(ticket.get("run_id") or ""),
        "asof_date": str(ticket.get("asof_date") or ""),
        "created_utc": created_utc,
        "submitted_by": args.submitted_by,
        "notes": args.notes,
        "acknowledged": True,
        "fills": fills,
        "pointers": {
            "ticket_json": str(ticket_dir / "ticket.json"),
            "ticket_md": str(ticket_dir / "ticket.md"),
            "confirmation_dir": str(conf_dir),
            "fills_json": (str(fills_path) if fills_path else ""),
        },
    }

    conf_json = conf_dir / "confirmation.json"
    conf_md = conf_dir / "confirmation.md"
    conf_json.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    conf_md.write_text(_render_confirmation_md(payload), encoding="utf-8")

    payload_str = json.dumps(payload, sort_keys=True)
    payload_tag, payload_dq = _dollar_quote("payload", payload_str)

    submitted_by_sql = "null" if not args.submitted_by else "'" + args.submitted_by.replace("'", "''") + "'"
    action_details = json.dumps(
        {"confirmation_uuid": confirmation_uuid, "confirmation_dir": str(conf_dir), "ticket_id": ticket_id}
    ).replace("'", "''")

    fills_sql_lines: list[str] = []
    for f in fills:
        seq = int(f["sequence"])
        sym = str(f["internal_symbol"]).replace("'", "''")
        side = str(f["side"])
        executed_status = str(f["executed_status"])
        executed_value_base = f.get("executed_value_base", None)
        units = f.get("units", None)
        fill_price = f.get("fill_price", None)
        filled_at = f.get("filled_at", None)
        notes = f.get("notes", None)

        executed_value_sql = "null" if executed_value_base is None else str(float(executed_value_base))
        units_sql = "null" if units is None else str(float(units))
        fill_price_sql = "null" if fill_price is None else str(float(fill_price))
        filled_at_sql = "null" if not filled_at else "'" + str(filled_at).replace("'", "''") + "'::timestamptz"
        notes_sql = "null" if not notes else "'" + str(notes).replace("'", "''") + "'"

        fills_sql_lines.append(
            f"""
            insert into ledger_trades_fills(
              ticket_id, sequence, internal_symbol, side,
              executed_status, executed_value_base, units, fill_price, filled_at, notes
            )
            values (
              '{ticket_id}'::uuid, {seq}, '{sym}', '{side}',
              '{executed_status}', {executed_value_sql}, {units_sql}, {fill_price_sql}, {filled_at_sql}, {notes_sql}
            )
            on conflict (ticket_id, sequence) do update set
              internal_symbol = excluded.internal_symbol,
              side = excluded.side,
              executed_status = excluded.executed_status,
              executed_value_base = excluded.executed_value_base,
              units = excluded.units,
              fill_price = excluded.fill_price,
              filled_at = excluded.filled_at,
              notes = excluded.notes;
            """.strip()
        )

    sql = f"""
    begin;
    insert into confirmations(ticket_id, submitted_by, payload)
    values ('{ticket_id}'::uuid, {submitted_by_sql}, {payload_dq}{payload_str}{payload_dq}::jsonb);

    {'\n'.join(fills_sql_lines) if fills_sql_lines else ''}

    insert into audit_log(ticket_id, actor, action, object_type, object_id, details)
    values (
      '{ticket_id}'::uuid,
      {submitted_by_sql},
      'CONFIRMATION_SUBMITTED',
      'confirmation',
      '{confirmation_uuid}',
      '{action_details}'::jsonb
    );
    commit;
    """
    _psql_exec(sql)

    print(f"confirmation_uuid={confirmation_uuid}")
    print(f"ticket_id={ticket_id}")
    print(f"confirmation_dir={conf_dir}")
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
