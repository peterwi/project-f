#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import csv
import io
import subprocess
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from providers.base import PriceEODRow, canonical_json, decimal_to_str
from providers.registry import get_provider

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


def _psql_exec(script: str) -> None:
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
    subprocess.run(cmd, input=script.encode("utf-8"), check=True)

def _load_fetch_universe() -> list[tuple[str, str, str | None]]:
    """
    Returns (internal_symbol, stooq_symbol, currency) for enabled symbols and explicit benchmarks.
    """
    rows: list[tuple[str, str, str | None]] = []
    raw = _psql_capture(
        """
        select internal_symbol || '|' || coalesce(stooq_symbol,'') || '|' || lower(coalesce(instrument_type,'')) || '|' || coalesce(currency,'')
        from config_universe
        where
          enabled = true
          -- Allow fetching explicit benchmarks/index rows even if disabled,
          -- but do NOT fetch every non-stock by accident.
          or lower(coalesce(instrument_type,'')) in ('benchmark','index')
        order by internal_symbol
        """
    )
    if not raw:
        return rows
    for line in raw.splitlines():
        internal_symbol, stooq_symbol, instrument_type, currency = line.split("|", 3)
        stooq_symbol = stooq_symbol.strip()
        if not stooq_symbol:
            raise ValueError(f"Missing stooq_symbol for {internal_symbol} in config_universe")
        rows.append((internal_symbol.strip(), stooq_symbol, (currency.strip() or None)))
    return rows


def _expected_asof_date(today: date) -> date:
    # Most recent weekday before today (T-1 weekday).
    d = today - timedelta(days=1)
    while d.weekday() >= 5:  # Sat/Sun
        d -= timedelta(days=1)
    return d


def _market_data_dir(env: dict[str, str]) -> Path:
    return Path(env.get("MARKET_DATA_DIR", "/data/trading-ops/data/market")).resolve()


def _artifacts_root(env: dict[str, str]) -> Path:
    return Path(env.get("ARTIFACTS_DIR", "/data/trading-ops/artifacts")).resolve()


def _input_hash(provider: str, start_date: date, end_date: date, symbols: list[str]) -> str:
    hasher = hashlib.sha256()
    hasher.update(b"market-fetch-v1\n")
    hasher.update(f"provider={provider}\n".encode("utf-8"))
    hasher.update(f"start={start_date.isoformat()}\n".encode("utf-8"))
    hasher.update(f"end={end_date.isoformat()}\n".encode("utf-8"))
    for s in symbols:
        hasher.update(f"symbol={s}\n".encode("utf-8"))
    return hasher.hexdigest()


def _write_manifest(cache_dir: Path, manifest: dict) -> None:
    cache_dir.mkdir(parents=True, exist_ok=True)
    (cache_dir / "manifest.json").write_text(canonical_json(manifest) + "\n", encoding="utf-8")


def _write_prices_csv(cache_dir: Path, rows: list[PriceEODRow]) -> Path:
    out = cache_dir / "prices_eod.csv"
    header = [
        "internal_symbol",
        "trading_date",
        "open",
        "high",
        "low",
        "close",
        "adj_close",
        "volume",
        "currency",
        "source",
        "quality_flags_json",
    ]
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(header)
    for r in sorted(rows, key=lambda x: (x.internal_symbol, x.trading_date.isoformat(), x.source)):
        w.writerow(
            [
                r.internal_symbol,
                r.trading_date.isoformat(),
                decimal_to_str(r.open),
                decimal_to_str(r.high),
                decimal_to_str(r.low),
                decimal_to_str(r.close),
                decimal_to_str(r.adj_close),
                (str(r.volume) if r.volume is not None else ""),
                (r.currency or ""),
                r.source,
                canonical_json(r.quality_flags or {}),
            ]
        )
    out.write_text(buf.getvalue(), encoding="utf-8")
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description="Fetch provider EOD prices and load into Postgres market_prices_eod (with deterministic cache).")
    parser.add_argument("--provider", default="", help="Market data provider (default: env MARKET_PROVIDER or stooq).")
    parser.add_argument("--end-date", help="End date (YYYY-MM-DD). Default: T-1 weekday (UTC).")
    parser.add_argument("--lookback-days", type=int, default=1200, help="Calendar days to fetch before end-date.")
    parser.add_argument("--mode", default="", choices=["", "online", "offline"], help="Fetch mode (default: env MARKET_FETCH_MODE or online).")
    args = parser.parse_args()

    if not ENV_FILE.exists():
        raise FileNotFoundError(f"Missing {ENV_FILE}; create it from config/secrets.env.example")
    env = _read_env_file(ENV_FILE)

    mode = (args.mode or env.get("MARKET_FETCH_MODE", "") or "online").strip().lower()
    offline = mode == "offline"
    provider_name = (args.provider or env.get("MARKET_PROVIDER", "") or "stooq").strip().lower()
    provider = get_provider(provider_name)

    end = date.fromisoformat(args.end_date) if args.end_date else _expected_asof_date(datetime.now(timezone.utc).date())
    if args.lookback_days < 1:
        raise ValueError("--lookback-days must be >= 1")
    start = end - timedelta(days=int(args.lookback_days))

    universe = _load_fetch_universe()
    if not universe:
        print("No symbols to fetch (no enabled symbols and no benchmarks).")
        return 0

    internal_symbols = [u[0] for u in universe]
    stooq_map = {u[0]: u[1] for u in universe}
    currency_map = {u[0]: u[2] for u in universe}

    cache_day_dir = _market_data_dir(env) / provider.name / end.isoformat()
    symbols_sorted = sorted(internal_symbols)
    manifest = {
        "provider": provider.name,
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "symbols": symbols_sorted,
        "input_hash": _input_hash(provider.name, start, end, symbols_sorted),
        "mode": ("offline" if offline else "online"),
    }
    _write_manifest(cache_day_dir, manifest)

    prices = provider.fetch_prices_eod(
        symbols=symbols_sorted,
        start_date=start,
        end_date=end,
        offline=offline,
        cache_dir=str(cache_day_dir),
        symbol_map=stooq_map,
    )
    # Attach currency from config_universe where available (provider may override in future).
    prices_final: list[PriceEODRow] = []
    for r in prices:
        prices_final.append(
            PriceEODRow(
                internal_symbol=r.internal_symbol,
                trading_date=r.trading_date,
                open=r.open,
                high=r.high,
                low=r.low,
                close=r.close,
                adj_close=r.adj_close,
                volume=r.volume,
                currency=(r.currency or currency_map.get(r.internal_symbol)),
                source=r.source,
                quality_flags=r.quality_flags,
            )
        )

    prices_csv_path = _write_prices_csv(cache_day_dir, prices_final)
    actions = provider.fetch_corporate_actions(
        symbols=symbols_sorted,
        start_date=start,
        end_date=end,
        offline=offline,
        cache_dir=str(cache_day_dir),
        symbol_map=stooq_map,
    )

    # Build a COPY script. Values are pre-escaped; join as CSV.
    copy_lines: list[str] = []
    copy_lines.append(
        """
BEGIN;
CREATE TEMP TABLE prices_stage (
  internal_symbol text NOT NULL,
  trading_date date NOT NULL,
  open numeric,
  high numeric,
  low numeric,
  close numeric,
  adj_close numeric,
  volume bigint,
  source text NOT NULL,
  quality_flags jsonb NOT NULL
);

COPY prices_stage (internal_symbol, trading_date, open, high, low, close, adj_close, volume, source, quality_flags)
FROM STDIN WITH (FORMAT csv);
""".lstrip()
    )
    for r in sorted(prices_final, key=lambda x: (x.internal_symbol, x.trading_date.isoformat(), x.source)):
        buf = io.StringIO()
        w = csv.writer(buf, lineterminator="")
        w.writerow(
            [
                r.internal_symbol,
                r.trading_date.isoformat(),
                decimal_to_str(r.open),
                decimal_to_str(r.high),
                decimal_to_str(r.low),
                decimal_to_str(r.close),
                decimal_to_str(r.adj_close),
                (str(r.volume) if r.volume is not None else ""),
                r.source,
                canonical_json(r.quality_flags or {}),
            ]
        )
        copy_lines.append(buf.getvalue() + "\n")
    copy_lines.append(r"\." + "\n")
    copy_lines.append(
        """
INSERT INTO market_prices_eod (
  internal_symbol, trading_date, open, high, low, close, adj_close, volume, source, quality_flags
)
SELECT
  internal_symbol, trading_date, open, high, low, close, adj_close, volume, source, quality_flags
FROM prices_stage
ON CONFLICT (internal_symbol, trading_date, source) DO UPDATE SET
  open = excluded.open,
  high = excluded.high,
  low = excluded.low,
  close = excluded.close,
  adj_close = excluded.adj_close,
  volume = excluded.volume,
  quality_flags = excluded.quality_flags;
COMMIT;
""".lstrip()
    )
    _psql_exec("".join(copy_lines))
    loaded_rows = len(prices_final)

    # Deterministic report artifact
    artifacts = _artifacts_root(env)
    report_dir = artifacts / "reports"
    report_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    report_path = report_dir / f"market_fetch_{provider.name}_{end.isoformat()}_{ts}.md"
    report_lines: list[str] = []
    report_lines.append("# Market Fetch Report")
    report_lines.append("")
    report_lines.append(f"- Generated at (UTC): `{ts}`")
    report_lines.append(f"- Provider: `{provider.name}`")
    report_lines.append(f"- Mode: `{'offline' if offline else 'online'}`")
    report_lines.append(f"- Start date: `{start.isoformat()}`")
    report_lines.append(f"- End date: `{end.isoformat()}`")
    report_lines.append(f"- Symbols: `{len(symbols_sorted)}`")
    report_lines.append(f"- Price rows loaded: `{loaded_rows}`")
    report_lines.append(f"- Prices cache: `{prices_csv_path}`")
    report_lines.append(f"- Cache manifest: `{cache_day_dir / 'manifest.json'}`")
    report_lines.append(f"- Corporate actions: `dividends={len(actions.dividends)} splits={len(actions.splits)}`")
    report_lines.append("")
    report_lines.append("## Symbols")
    report_lines.append("")
    for s in symbols_sorted:
        report_lines.append(f"- `{s}`")
    report_lines.append("")
    report_path.write_text("\n".join(report_lines), encoding="utf-8")

    print(f"Wrote {report_path}")
    print(f"Loaded rows into market_prices_eod: {loaded_rows}")
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
