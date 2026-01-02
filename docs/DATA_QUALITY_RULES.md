# Data Quality Rules (v1)

This document defines deterministic rules that must pass before any trade ticket can be produced.

Key rule: **NO DATA / NO RECONCILE / NO CONFIRM / NO TRADE**.

## Freshness (UK morning runs)
- Default expected `asof_date` is the most recent US trading weekday **before today** (T‑1 weekday).
- US holidays/half-days are not inferred automatically at v1.
  - If a holiday causes a false “stale” detection, rerun the gate with `--asof-date` explicitly set.

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

