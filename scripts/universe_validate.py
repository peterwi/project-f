#!/usr/bin/env python3
from __future__ import annotations

import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
import re


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


def _psql_capture(sql: str) -> str:
    env = _read_env_file(ENV_FILE)
    user = env.get("POSTGRES_USER", "").strip()
    db = env.get("POSTGRES_DB", "").strip()
    if not user or not db:
        raise ValueError("POSTGRES_USER and POSTGRES_DB must be set in config/secrets.env")

    cmd = [
        "docker",
        "compose",
        "-f",
        str(COMPOSE_FILE),
        "--env-file",
        str(ENV_FILE),
        "exec",
        "-T",
        "postgres",
        "psql",
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
    if not COMPOSE_FILE.exists():
        raise FileNotFoundError(f"Missing {COMPOSE_FILE}")

    env = _read_env_file(ENV_FILE)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    total_rows = int(_psql_capture("select count(*) from config_universe;") or "0")
    enabled_rows = int(_psql_capture("select count(*) from config_universe where enabled = true;") or "0")
    benchmark_rows = int(
        _psql_capture("select count(*) from config_universe where lower(instrument_type) <> 'stock';") or "0"
    )
    pending_verification_rows = int(
        _psql_capture(
            """
            select count(*)
            from config_universe
            where lower(instrument_type) = 'stock'
              and (enabled = false or enabled is null)
            """
        )
        or "0"
    )

    issues: list[str] = []

    missing_required = _psql_capture(
        """
        select count(*)
        from config_universe
        where internal_symbol is null
           or btrim(internal_symbol) = ''
           or currency is null
           or btrim(currency) = ''
           or instrument_type is null
           or btrim(instrument_type) = ''
        """
    )
    if int(missing_required or "0") > 0:
        issues.append("Missing required fields on one or more rows (internal_symbol/currency/instrument_type).")

    # Enforced policy: any enabled row must be a tradable underlying stock.
    enabled_non_stock = _psql_capture(
        "select count(*) from config_universe where enabled = true and lower(instrument_type) <> 'stock';"
    )
    if int(enabled_non_stock or "0") > 0:
        issues.append("Enabled rows include non-stock instruments (ETFs are benchmarks only; must not be enabled).")

    enabled_not_underlying = _psql_capture(
        "select count(*) from config_universe where enabled = true and tradable_underlying <> true;"
    )
    if int(enabled_not_underlying or "0") > 0:
        issues.append("Enabled rows include non-underlying instruments (CFD/untradable must not be enabled).")

    enabled_missing_search = _psql_capture(
        "select count(*) from config_universe where enabled = true and (etoro_search_name is null or btrim(etoro_search_name) = '');"
    )
    if int(enabled_missing_search or "0") > 0:
        issues.append("Enabled rows missing etoro_search_name.")

    # Enabled rows must have explicit eToro verification marker in notes.
    enabled_notes = _psql_capture(
        "select coalesce(string_agg(coalesce(notes,''), '\n'), '') from config_universe where enabled = true;"
    )
    verification_pattern = re.compile(r"ETORO_VERIFIED.*\d{4}-\d{2}-\d{2}")
    if enabled_rows > 0 and not verification_pattern.search(enabled_notes):
        issues.append("Enabled rows must include an ETORO_VERIFIED_YYYY-MM-DD marker in notes.")

    # Generate report
    artifacts = _artifacts_root(env)
    report_path = artifacts / "reports" / "universe_validation.md"
    report_path.parent.mkdir(parents=True, exist_ok=True)

    enabled_symbols = _psql_capture(
        "select string_agg(internal_symbol, ', ' order by internal_symbol) from config_universe where enabled = true;"
    )
    benchmark_symbols = _psql_capture(
        "select string_agg(internal_symbol, ', ' order by internal_symbol) from config_universe where lower(instrument_type) <> 'stock';"
    )

    lines: list[str] = []
    lines.append("# Universe Validation Report")
    lines.append("")
    lines.append(f"- Generated at (UTC): `{now}`")
    lines.append(f"- Total rows in `config_universe`: `{total_rows}`")
    lines.append(f"- Enabled (tradable) rows: `{enabled_rows}`")
    lines.append(f"- Benchmark (non-stock) rows: `{benchmark_rows}`")
    lines.append(f"- Pending verification (disabled stocks): `{pending_verification_rows}`")
    lines.append("")
    lines.append("## Enabled symbols")
    lines.append("")
    lines.append(enabled_symbols if enabled_symbols else "_None enabled yet._")
    lines.append("")
    lines.append("## Benchmarks (non-tradable)")
    lines.append("")
    lines.append(benchmark_symbols if benchmark_symbols else "_None._")
    lines.append("")
    lines.append("## Issues")
    lines.append("")
    if issues:
        for issue in issues:
            lines.append(f"- {issue}")
    else:
        lines.append("- None")
    lines.append("")
    lines.append("## Policy notes")
    lines.append("")
    lines.append("- Per `docs/ETORO_CONSTRAINTS.md`: tradable universe is **underlying stocks only** (no ETFs, no CFDs, no leverage).")
    lines.append("- ETFs may exist later as benchmarks, but must not be enabled for execution.")
    lines.append("")

    report_path.write_text("\n".join(lines), encoding="utf-8")

    print(f"Wrote {report_path}")
    if issues:
        print("VALIDATION_FAILED")
        return 2
    print("VALIDATION_OK")
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
