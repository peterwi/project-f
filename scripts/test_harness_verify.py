#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def _die(msg: str) -> None:
    raise SystemExit(f"VERIFY_FAIL: {msg}")


def _read_ids(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or "=" not in line:
            continue
        k, v = line.split("=", 1)
        out[k.strip()] = v.strip()
    return out


def _parse_run_summary_steps(path: Path) -> dict[str, str]:
    txt = path.read_text(encoding="utf-8").splitlines()
    in_steps = False
    steps: dict[str, str] = {}
    for line in txt:
        if line.startswith("## "):
            in_steps = line.strip() == "## Steps"
            continue
        if not in_steps:
            continue
        s = line.strip()
        if not s.startswith("- "):
            continue
        if s.startswith("- report:"):
            continue
        if ": `" not in s or not s.endswith("`"):
            continue
        name, rest = s[2:].split(": ", 1)
        if not rest.startswith("`"):
            continue
        steps[name.strip()] = rest.strip("`")
    return steps


def main() -> int:
    ap = argparse.ArgumentParser(description="Validate a test harness output folder deterministically.")
    ap.add_argument("--out-dir", required=True, help="Path to /data/trading-ops/artifacts/test_runs/<date>/... folder")
    args = ap.parse_args()

    out_dir = Path(args.out_dir).resolve()
    required_paths = [
        out_dir / "README.md",
        out_dir / "ids.env",
        out_dir / "logs",
        out_dir / "runs",
        out_dir / "tickets",
        out_dir / "reconcile",
        out_dir / "db",
    ]
    for p in required_paths:
        if not p.exists():
            _die(f"missing required path: {p}")

    ids = _read_ids(out_dir / "ids.env")
    run_0800_id = ids.get("run_0800_id", "")
    run_1400_id = ids.get("run_1400_id", "")
    ticket_1400_id = ids.get("ticket_1400_id", "")
    if not run_0800_id or not run_1400_id or not ticket_1400_id:
        _die("ids.env missing one of: run_0800_id, run_1400_id, ticket_1400_id")

    rs_0800 = out_dir / "runs" / "run_0800_summary.md"
    rs_1400 = out_dir / "runs" / "run_1400_summary.md"
    if not rs_0800.exists() or not rs_1400.exists():
        _die("missing run summaries under runs/")

    s0800 = _parse_run_summary_steps(rs_0800)
    s1400 = _parse_run_summary_steps(rs_1400)

    if s0800.get("market-fetch") != "OK":
        _die(f"0800 must have market-fetch OK (got {s0800.get('market-fetch')!r})")

    mf1400 = s1400.get("market-fetch")
    if mf1400 not in (None, "", "SKIPPED"):
        _die(f"1400 must not refetch; expected market-fetch absent or SKIPPED (got {mf1400!r})")

    # Ticket artifacts + material hash consistency.
    ticket_json = out_dir / "tickets" / "ticket_1400.json"
    mh_path = out_dir / "tickets" / "material_hash_1400.txt"
    if not ticket_json.exists() or not mh_path.exists():
        _die("missing ticket_1400.json or material_hash_1400.txt")

    payload = json.loads(ticket_json.read_text(encoding="utf-8"))
    decision_type = str(payload.get("decision_type") or "")
    mh_json = str((payload.get("meta") or {}).get("material_hash") or "")
    mh_txt = mh_path.read_text(encoding="utf-8").strip()
    if not decision_type:
        _die("ticket_1400.json missing decision_type")
    if not mh_json or not mh_txt or mh_json != mh_txt:
        _die("material_hash mismatch between ticket_1400.json and material_hash_1400.txt")

    # Required proofs present.
    proof_files = [
        out_dir / "db" / "db_tables.txt",
        out_dir / "db" / "runs_last.txt",
        out_dir / "db" / "tickets_last.txt",
        out_dir / "db" / "counts.txt",
    ]
    for p in proof_files:
        if not p.exists():
            _die(f"missing DB proof file: {p}")

    # Reconcile reports copied.
    if not (out_dir / "reconcile" / "reconcile_pre_1400.md").exists():
        _die("missing reconcile/reconcile_pre_1400.md")
    if not (out_dir / "reconcile" / "reconcile_final.md").exists():
        _die("missing reconcile/reconcile_final.md")

    print("VERIFY_OK")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as e:
        _die(str(e))

