# Data Quality Rules (v1)

This document defines deterministic rules that must pass before any trade ticket can be produced.

Key rule: **NO DATA / NO RECONCILE / NO CONFIRM / NO TRADE**.

## Freshness (UK morning runs)
- Default expected `asof_date` starts from the most recent weekday **before today** (T‑1 weekday), then applies a deterministic holiday fallback:
  - If that expected date has no benchmark bars in `market_prices_eod`, the gate selects the most recent prior trading date (based on benchmark data) and uses that as `asof_date`.
  - You can still override with `--asof-date YYYY-MM-DD` for late data or investigations.

## Benchmark presence
- Benchmarks (ETFs) are **benchmarks only** (not tradable on eToro), but must be present for reporting/comparison.
- For the chosen `asof_date`, each benchmark symbol must have an EOD bar present in `market_prices_eod`.

## Coverage
- For the chosen `asof_date`, enabled tradable symbols must meet the configured coverage threshold.
- Default threshold: `98%` of enabled symbols (with 1 enabled symbol this implies `100%`).

## Duplicates
- No duplicate `(internal_symbol, trading_date, source)` rows are allowed.

## Outputs
- A markdown report is written to `/data/trading-ops/artifacts/reports/`.
- A summary row is stored in `data_quality_reports` in Postgres.
