#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
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


def _docker_compose_base(env_file: Path, compose_file: Path) -> list[str]:
    return ["docker", "compose", "-f", str(compose_file), "--env-file", str(env_file)]


def _psql_capture(sql: str) -> str:
    env = _read_env_file(ENV_FILE)
    user = env.get("POSTGRES_USER", "").strip()
    db = env.get("POSTGRES_DB", "").strip()
    if not user or not db:
        raise ValueError("POSTGRES_USER and POSTGRES_DB must be set in config/secrets.env")

    cmd = _docker_compose_base(ENV_FILE, COMPOSE_FILE) + [
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


def _psql_exec(sql: str) -> None:
    env = _read_env_file(ENV_FILE)
    user = env.get("POSTGRES_USER", "").strip()
    db = env.get("POSTGRES_DB", "").strip()
    if not user or not db:
        raise ValueError("POSTGRES_USER and POSTGRES_DB must be set in config/secrets.env")

    cmd = _docker_compose_base(ENV_FILE, COMPOSE_FILE) + [
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
    ]
    subprocess.run(cmd, input=sql.encode("utf-8"), check=True)


def _expected_asof_date(today: date) -> date:
    # v1 rule: most recent weekday before today (T-1 weekday).
    d = today - timedelta(days=1)
    while d.weekday() >= 5:  # Sat/Sun
        d -= timedelta(days=1)
    return d


def _auto_asof_date(*, expected: date, benchmarks: list[str]) -> date:
    """
    Pick the most recent trading_date <= expected that exists in market_prices_eod for benchmark symbols.

    This is a deterministic holiday/weekend fallback: if the expected weekday is a US market holiday,
    it will select the prior available trading day.
    """
    if not benchmarks:
        return expected
    quoted = ",".join("'" + b.replace("'", "''") + "'" for b in benchmarks)
    raw = _psql_capture(
        f"""
        select coalesce(max(trading_date)::text, '')
        from market_prices_eod
        where internal_symbol in ({quoted})
          and trading_date <= '{expected.isoformat()}'
        """
    )
    if raw:
        try:
            return date.fromisoformat(raw)
        except ValueError:
            return expected
    return expected


def _artifacts_root(env: dict[str, str]) -> Path:
    return Path(env.get("ARTIFACTS_DIR", "/data/trading-ops/artifacts")).resolve()

def _emit_alert(*, run_id: str, asof: date, expected: date, coverage_pct: float, coverage_min_pct: float, issues: list[str], report_path: Path) -> None:
    details = {
        "asof_date": asof.isoformat(),
        "expected_date": expected.isoformat(),
        "coverage_pct": coverage_pct,
        "coverage_min_pct": coverage_min_pct,
        "issues": issues,
        "report_path": str(report_path),
    }
    artifact_paths = [str(report_path)]
    if run_id:
        artifact_paths.append(f"/data/trading-ops/artifacts/runs/{run_id}/run_summary.md")
    cmd = [
        "python3",
        "scripts/alert_emit.py",
        "--alert-type",
        "DATA_QUALITY_FAIL",
        "--severity",
        "ERROR",
        "--summary",
        f"DATA_QUALITY_FAIL asof_date={asof.isoformat()} coverage={coverage_pct:.2f}%<min={coverage_min_pct:.2f}%",
        "--details-json",
        json.dumps(details),
    ]
    if run_id:
        cmd += ["--run-id", run_id]
    for p in artifact_paths:
        cmd += ["--artifact-path", p]
    subprocess.run(cmd, cwd=str(ROOT), check=False, capture_output=True, text=True)


def main() -> int:
    parser = argparse.ArgumentParser(description="Deterministic data quality gate (blocks trading on failure).")
    parser.add_argument("--asof-date", help="Override asof_date (YYYY-MM-DD). Use for US holidays/late data.")
    parser.add_argument("--run-id", help="Optional run_id to link this gate result to a runs row.")
    args = parser.parse_args()

    if not ENV_FILE.exists():
        raise FileNotFoundError(f"Missing {ENV_FILE}; create it from config/secrets.env.example")
    env = _read_env_file(ENV_FILE)

    today = datetime.now(timezone.utc).date()
    run_id = (args.run_id or "").strip()

    coverage_min_pct = float(env.get("DATA_COVERAGE_MIN_PCT", "98"))

    enabled_symbols = _psql_capture(
        "select coalesce(string_agg(internal_symbol, ',' order by internal_symbol), '') from config_universe where enabled = true;"
    )
    enabled_list = [s for s in enabled_symbols.split(",") if s] if enabled_symbols else []

    benchmark_symbols = _psql_capture(
        "select coalesce(string_agg(internal_symbol, ',' order by internal_symbol), '') from config_universe where lower(coalesce(instrument_type,'')) <> 'stock';"
    )
    benchmark_list = [s for s in benchmark_symbols.split(",") if s] if benchmark_symbols else []

    expected = _expected_asof_date(today)
    auto_expected = _auto_asof_date(expected=expected, benchmarks=benchmark_list)
    asof = date.fromisoformat(args.asof_date) if args.asof_date else auto_expected

    issues: list[str] = []
    offenders: list[str] = []

    enabled_count = len(enabled_list)
    if enabled_count == 0:
        issues.append("No enabled symbols in config_universe (nothing tradable).")

    # Coverage: enabled symbols must have a bar for asof date.
    have_enabled = int(
        _psql_capture(
            f"""
            select count(distinct internal_symbol)
            from market_prices_eod
            where trading_date = '{asof.isoformat()}'
              and internal_symbol in (select internal_symbol from config_universe where enabled = true)
            """
        )
        or "0"
    )
    coverage_pct = (100.0 * have_enabled / enabled_count) if enabled_count > 0 else 0.0
    if enabled_count > 0 and coverage_pct + 1e-9 < coverage_min_pct:
        issues.append(f"Coverage {coverage_pct:.2f}% below threshold {coverage_min_pct:.2f}% for asof_date={asof}.")

    # Benchmarks must have asof_date present (benchmarks are ETFs, still needed for reporting).
    missing_benchmarks: list[str] = []
    for b in benchmark_list:
        have = int(
            _psql_capture(
                f"""
                select count(*)
                from market_prices_eod
                where trading_date = '{asof.isoformat()}'
                  and internal_symbol = '{b}'
                """
            )
            or "0"
        )
        if have == 0:
            missing_benchmarks.append(b)
    if missing_benchmarks:
        issues.append(f"Missing benchmark bars for asof_date={asof}: {', '.join(missing_benchmarks)}")

    # Critical NULL checks for asof_date (adj_close is required by downstream sizing/returns calculations).
    null_adj = int(
        _psql_capture(
            f"""
            select count(*)
            from market_prices_eod
            where trading_date = '{asof.isoformat()}'
              and internal_symbol in (
                select internal_symbol
                from config_universe
                where enabled = true
                   or lower(coalesce(instrument_type,'')) in ('benchmark','index')
              )
              and adj_close is null
            """
        )
        or "0"
    )
    if null_adj > 0:
        issues.append(f"adj_close NULL rows at asof_date={asof}: {null_adj}")
        offenders_raw = _psql_capture(
            f"""
            select internal_symbol || '|' || coalesce(source,'') || '|adj_close_null'
            from market_prices_eod
            where trading_date = '{asof.isoformat()}'
              and adj_close is null
            order by internal_symbol, source
            limit 25;
            """
        )
        offenders.extend([ln for ln in (offenders_raw.splitlines() if offenders_raw else []) if ln.strip()])

    # Price/volume sanity checks (only when both operands are non-null).
    bad_ohlc = int(
        _psql_capture(
            f"""
            select count(*)
            from market_prices_eod
            where trading_date = '{asof.isoformat()}'
              and (
                (high is not null and low is not null and high < low)
                or (volume is not null and volume < 0)
                or (open is not null and open < 0)
                or (high is not null and high < 0)
                or (low is not null and low < 0)
                or (close is not null and close < 0)
                or (adj_close is not null and adj_close < 0)
              )
            """
        )
        or "0"
    )
    if bad_ohlc > 0:
        issues.append(f"Price sanity failures at asof_date={asof}: {bad_ohlc}")
        offenders_raw = _psql_capture(
            f"""
            select internal_symbol || '|' || coalesce(source,'') || '|' ||
                   case
                     when (high is not null and low is not null and high < low) then 'high_lt_low'
                     when (volume is not null and volume < 0) then 'volume_lt_0'
                     when (open is not null and open < 0) then 'open_lt_0'
                     when (high is not null and high < 0) then 'high_lt_0'
                     when (low is not null and low < 0) then 'low_lt_0'
                     when (close is not null and close < 0) then 'close_lt_0'
                     when (adj_close is not null and adj_close < 0) then 'adj_close_lt_0'
                     else 'unknown'
                   end
            from market_prices_eod
            where trading_date = '{asof.isoformat()}'
              and (
                (high is not null and low is not null and high < low)
                or (volume is not null and volume < 0)
                or (open is not null and open < 0)
                or (high is not null and high < 0)
                or (low is not null and low < 0)
                or (close is not null and close < 0)
                or (adj_close is not null and adj_close < 0)
              )
            order by internal_symbol, source
            limit 25;
            """
        )
        offenders.extend([ln for ln in (offenders_raw.splitlines() if offenders_raw else []) if ln.strip()])

    # Staleness: benchmark max date should be >= asof_date used.
    if benchmark_list:
        quoted = ",".join("'" + b.replace("'", "''") + "'" for b in benchmark_list)
        max_bm_date = _psql_capture(
            f"""
            select coalesce(max(trading_date)::text,'')
            from market_prices_eod
            where internal_symbol in ({quoted});
            """
        )
        if max_bm_date and max_bm_date < asof.isoformat():
            issues.append(f"Benchmark staleness: max benchmark trading_date={max_bm_date} < asof_date={asof.isoformat()}")

    # Duplicate detection (should be impossible but verify).
    duplicates = int(
        _psql_capture(
            """
            select count(*)
            from (
              select internal_symbol, trading_date, source, count(*) c
              from market_prices_eod
              group by internal_symbol, trading_date, source
              having count(*) > 1
            ) d
            """
        )
        or "0"
    )
    if duplicates > 0:
        issues.append("Duplicate (internal_symbol, trading_date, source) rows detected in market_prices_eod.")

    passed = len(issues) == 0

    # Write markdown report
    artifacts = _artifacts_root(env)
    report_dir = artifacts / "reports"
    report_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    report_path = report_dir / f"data_quality_{asof.isoformat()}_{ts}.md"

    lines: list[str] = []
    lines.append("# Data Quality Gate Report")
    lines.append("")
    lines.append(f"- Generated at (UTC): `{ts}`")
    lines.append(f"- Expected date (weekday rule): `{expected.isoformat()}`")
    if auto_expected != expected:
        lines.append(f"- Auto expected date (holiday fallback): `{auto_expected.isoformat()}`")
    lines.append(f"- As-of date used: `{asof.isoformat()}`")
    lines.append(f"- Coverage threshold: `{coverage_min_pct:.2f}%`")
    lines.append(f"- Enabled symbols: `{enabled_count}`")
    lines.append(f"- Enabled with bars: `{have_enabled}`")
    lines.append(f"- Coverage: `{coverage_pct:.2f}%`")
    lines.append(f"- Benchmarks: `{len(benchmark_list)}`")
    lines.append("")
    lines.append("## Symbols")
    lines.append("")
    lines.append(f"- Enabled: `{', '.join(enabled_list) if enabled_list else ''}`")
    lines.append(f"- Benchmarks: `{', '.join(benchmark_list) if benchmark_list else ''}`")
    lines.append("")
    lines.append("## Result")
    lines.append("")
    lines.append(f"- Status: `{'PASS' if passed else 'FAIL'}`")
    lines.append("")
    lines.append("## Issues")
    lines.append("")
    if issues:
        for issue in issues:
            lines.append(f"- {issue}")
    else:
        lines.append("- None")
    lines.append("")
    lines.append("## Top Offending Rows (symbol|source|issue)")
    lines.append("")
    if offenders:
        for ln in sorted(set(offenders))[:50]:
            lines.append(f"- `{ln}`")
    else:
        lines.append("- None")
    lines.append("")
    lines.append("## Notes")
    lines.append("")
    lines.append("- For US holidays/half-days, rerun with `--asof-date YYYY-MM-DD` if needed.")
    lines.append("")

    report_path.write_text("\n".join(lines), encoding="utf-8")

    # Store summary in Postgres
    details = {
        "issues": issues,
        "offenders": sorted(set(offenders))[:200],
        "enabled_symbols": enabled_list,
        "benchmarks": benchmark_list,
        "duplicates_detected": duplicates,
        "null_adj_close_asof": null_adj,
        "price_sanity_failures_asof": bad_ohlc,
    }
    details_json = json.dumps(details, sort_keys=True).replace("'", "''")

    _psql_exec(
        f"""
        insert into data_quality_reports(
          run_id, asof_date, expected_date, passed, coverage_pct, enabled_symbols_count, benchmarks_count, details, report_path
        ) values (
          {("null" if not run_id else "'" + run_id.replace("'", "''") + "'")},
          '{asof.isoformat()}',
          '{expected.isoformat()}',
          {str(passed).lower()},
          {coverage_pct},
          {enabled_count},
          {len(benchmark_list)},
          '{details_json}'::jsonb,
          '{str(report_path).replace("'", "''")}'
        );
        """
    )

    print(f"Wrote {report_path}")
    if passed:
        print("DATA_QUALITY_PASS")
        return 0
    _emit_alert(
        run_id=run_id,
        asof=asof,
        expected=expected,
        coverage_pct=coverage_pct,
        coverage_min_pct=coverage_min_pct,
        issues=issues,
        report_path=report_path,
    )
    print("DATA_QUALITY_FAIL")
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
