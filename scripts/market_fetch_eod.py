#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import subprocess
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
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


@dataclass(frozen=True)
class UniverseRow:
    internal_symbol: str
    stooq_symbol: str
    instrument_type: str


def _load_fetch_universe() -> list[UniverseRow]:
    rows: list[UniverseRow] = []
    raw = _psql_capture(
        """
        select internal_symbol || '|' || coalesce(stooq_symbol,'') || '|' || lower(coalesce(instrument_type,''))
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
        internal_symbol, stooq_symbol, instrument_type = line.split("|", 2)
        stooq_symbol = stooq_symbol.strip()
        if not stooq_symbol:
            raise ValueError(f"Missing stooq_symbol for {internal_symbol} in config_universe")
        rows.append(UniverseRow(internal_symbol=internal_symbol.strip(), stooq_symbol=stooq_symbol, instrument_type=instrument_type))
    return rows


def _stooq_url(stooq_symbol: str) -> str:
    # Example: https://stooq.com/q/d/l/?s=aapl.us&i=d
    return f"https://stooq.com/q/d/l/?s={stooq_symbol}&i=d"


def _download(url: str, timeout_s: int) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "trading-ops/1.0"})
    with urllib.request.urlopen(req, timeout=timeout_s) as resp:
        return resp.read()


def _raw_dir(env: dict[str, str]) -> Path:
    return Path(env.get("DATA_RAW_DIR", "/data/trading-ops/data/raw")).resolve()


def _write_raw(env: dict[str, str], internal_symbol: str, provider: str, content: bytes) -> Path:
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_dir = _raw_dir(env) / provider / internal_symbol
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{ts}.csv"
    out_path.write_bytes(content)
    return out_path


def _parse_stooq_csv(content: bytes) -> list[dict[str, str]]:
    text = content.decode("utf-8", errors="replace")
    reader = csv.DictReader(text.splitlines())
    rows: list[dict[str, str]] = []
    for r in reader:
        # Stooq headers: Date,Open,High,Low,Close,Volume
        if not r.get("Date"):
            continue
        rows.append(r)
    return rows


def _csv_escape(value: str) -> str:
    # Minimal CSV escaping for our expected values.
    if value is None:
        return ""
    if any(ch in value for ch in [",", "\"", "\n", "\r"]):
        return '"' + value.replace('"', '""') + '"'
    return value


def main() -> int:
    parser = argparse.ArgumentParser(description="Fetch Stooq EOD CSVs and load into Postgres market_prices_eod.")
    parser.add_argument("--max-rows", type=int, default=400, help="Keep only the last N rows per symbol.")
    parser.add_argument("--timeout-seconds", type=int, default=20)
    args = parser.parse_args()

    if not ENV_FILE.exists():
        raise FileNotFoundError(f"Missing {ENV_FILE}; create it from config/secrets.env.example")
    env = _read_env_file(ENV_FILE)

    universe = _load_fetch_universe()
    if not universe:
        print("No symbols to fetch (no enabled symbols and no benchmarks).")
        return 0

    all_price_rows: list[list[str]] = []

    for row in universe:
        url = _stooq_url(row.stooq_symbol)
        try:
            content = _download(url, timeout_s=args.timeout_seconds)
        except urllib.error.URLError as e:
            raise RuntimeError(f"Failed to download {url}: {e}") from e

        raw_path = _write_raw(env, row.internal_symbol, "stooq", content)
        parsed = _parse_stooq_csv(content)
        if not parsed:
            raise RuntimeError(f"No rows parsed from Stooq for {row.internal_symbol} ({row.stooq_symbol})")

        keep = parsed[-args.max_rows :] if args.max_rows > 0 else parsed
        # Stooq daily CSV does not include adj_close. For v1 we set adj_close = close
        # and mark it clearly; this keeps downstream schema consistent.
        quality_flags = _csv_escape('{"provider":"stooq","adj_close":"synthetic_close"}')
        source = "stooq"

        for r in keep:
            trading_date = _csv_escape(r["Date"])
            open_ = _csv_escape(r.get("Open", ""))
            high = _csv_escape(r.get("High", ""))
            low = _csv_escape(r.get("Low", ""))
            close = _csv_escape(r.get("Close", ""))
            adj_close = close  # synthetic adj_close = close (v1)
            volume = _csv_escape(r.get("Volume", ""))
            all_price_rows.append(
                [
                    _csv_escape(row.internal_symbol),
                    trading_date,
                    open_,
                    high,
                    low,
                    close,
                    adj_close,
                    volume,
                    _csv_escape(source),
                    quality_flags,
                ]
            )

        print(f"Fetched {row.internal_symbol} ({row.stooq_symbol}) rows={len(keep)} raw={raw_path}")

    # Build a COPY script. Values are pre-escaped; join as CSV.
    copy_lines = []
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
    for r in all_price_rows:
        copy_lines.append(",".join(r) + "\n")
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
    print(f"Loaded rows into market_prices_eod: {len(all_price_rows)}")
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
