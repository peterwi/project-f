# Market Data Sources (M13)

This system supports **deterministic market-data ingestion** with a provider abstraction and a **local, reproducible cache** under `/data/trading-ops/data/`.

## Data Types

- **Prices (EOD)**: OHLCV + `adj_close`
- **Corporate actions**:
  - Dividends (ex-date, pay-date, amount, currency)
  - Splits (ex-date, ratio)
- **Fundamentals (optional)**: provider-specific (not required for M13 core)
- **Calendar**: trading-day selection is derived from benchmark availability in the DB (see `scripts/data_quality_gate.py`).

## Provider Selection

Provider selection is done via config/env and is safe-by-default:

- Default free provider: **Stooq** (daily CSV)
- Optional paid providers: **plugin provider modules** (not shipped with embedded keys)

Environment variables (read from `config/secrets.env`, never committed):

- `MARKET_PROVIDER` (default: `stooq`)
- `MARKET_FETCH_MODE` (`online` or `offline`; default `online`)
- `MARKET_DATA_DIR` (default: `/data/trading-ops/data/market`)

## Provider Contract

Provider modules live in `scripts/providers/` and implement:

- `fetch_prices_eod(symbols, start_date, end_date, offline) -> canonical rows`
- `fetch_corporate_actions(symbols, start_date, end_date, offline) -> dividends/splits`
- `normalize_to_canonical_schema(...)` (provider-specific parsing + canonical typing)

Canonical prices row fields:

- `internal_symbol`, `trading_date`
- `open`, `high`, `low`, `close`, `adj_close`, `volume`
- `currency` (nullable)
- `source` (provider name)
- `quality_flags` (JSON; deterministic key ordering)

## Deterministic On-Disk Cache

The canonical cache layout is:

`/data/trading-ops/data/market/<provider>/<YYYY-MM-DD>/...`

Within a date partition, files have **stable names** and **stable formats**:

- `manifest.json` (inputs, symbol list, date range)
- `prices_eod.csv` (canonical rows, stable header + stable row ordering)
- `raw/` (provider raw payloads, stable names)

The `offline` mode uses only this cache and will fail if required cache files are missing.

## Licensing / Secrets Policy

- Do **not** commit provider API keys to git.
- Provider credentials (if any) must be read from `config/secrets.env` or runtime environment only.
- Avoid scraping or browser automation; use documented provider APIs/endpoints only.

