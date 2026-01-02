# Trade-Builder Contract (Deterministic)

Status: **v1 (contract-only)**  
Scope: Defines a deterministic “trade-builder” that converts portfolio targets into **intended trades** (no execution authority).

This contract is written to align with the current Postgres schema:
- `portfolio_targets` (source of desired weights/values)
- `ledger_trades_intended` (output rows used to render TRADE tickets)
- `market_prices_eod` (price source for sizing)
- `reconciliation_results` (precondition gate: `passed=true`)

## Purpose

Trade-builder deterministically produces **trade intentions** (BUY/SELL + units + reference prices) for a given `run_id` and an agreed `asof_date_used`. It must not place orders, call external services, or depend on nondeterministic inputs.

## Inputs (Required)

### Primary identifiers
- `run_id` (UUID): the run to attach outputs to.
- `asof_date_used` (DATE): the effective market “as-of” date used for prices and targets. This must match the date used by the data-quality gate for that run.

### Portfolio targets
Source: Postgres table `portfolio_targets`
- Rows filtered by `run_id` (must exist for TRADE intent):
  - `asof_date` (must equal `asof_date_used`)
  - `internal_symbol` (FK to `config_universe`)
  - `target_weight` (numeric, required)
  - `target_value_base` (numeric, optional)
  - `base_currency` (text, optional; default is `GBP` at runtime if null)

### Prices
Source: Postgres table `market_prices_eod`
- For each `internal_symbol` in `portfolio_targets` for the run:
  - Must have a price row at `trading_date = asof_date_used`
  - Uses `close` as `reference_price` (unless policy specifies otherwise)
  - Must record which `source` was used in artifacts

### Reconciliation (prerequisite for approving TRADE)
Source: Postgres table `reconciliation_results`
- Riskguard must only approve a TRADE decision when a recent reconciliation exists and is passing (`passed=true`).
- Trade-builder may still run deterministically in `DRYRUN_TRADES=true` mode even if reconciliation is missing; in that case:
  - It must mark `prerequisites.reconciliation_passed=false` in the artifact.
  - It must use ledger-derived positions/cash (`ledger_positions_current`, `ledger_cash_current`) as a draft sizing source.
  - Riskguard remains responsible for blocking TRADE until reconciliation passes.

## Inputs (Policy / Config)

Trade-builder uses deterministic policy constants (defaults may be overridden by config, but never by network calls):
- `base_currency`: default `GBP`
- `MIN_NOTIONAL_BASE`: default `25` (GBP)
- `DEFAULT_ORDER_TYPE`: default `MKT`
- `DEFAULT_MAX_SLIPPAGE_BPS`: default `50`

## Outputs (Required)

Trade-builder must always write an artifact:
- `/data/trading-ops/artifacts/runs/<run_id>/trades_intended.json`
  - Must conform to `docs/TRADE_BUILDER_CONTRACT.schema.json`
  - Must include:
    - `run_id`, `asof_date_used`, `base_currency`
    - policy values used (min notional, slippage bps)
    - prerequisites status + reason (if blocked)
    - ordered `intended_trades` array (possibly empty)
    - determinism metadata (`git_commit`, `config_hash` if available)

### DB writes (when prerequisites pass)
If prerequisites pass and the builder runs successfully, it must insert rows into `ledger_trades_intended`:
- Required columns:
  - `run_id` (uuid)
  - `sequence` (integer; 1..N)
  - `internal_symbol` (text)
  - `side` (text; constrained to `BUY`/`SELL`)
- Optional but strongly recommended (when available):
  - `notional_value_base` (numeric)
  - `units` (numeric; must represent an integer number of shares)
  - `order_type` (text; default `MKT`)
  - `limit_price` (numeric; null unless order type requires it)
  - `reference_price` (numeric; close price used for sizing)
  - `max_slippage_bps` (int; default `50`)
  - `ticket_id` (uuid; may be null until ticket is created)

Idempotency requirement (deterministic re-runs):
- Re-running trade-builder for the same `run_id` must be deterministic and must not create duplicate intentions.
- Implementation may:
  - delete existing `ledger_trades_intended` rows for `run_id` before inserting, or
  - update/replace using the unique constraint `(run_id, sequence)`.

## Deterministic Calculation Rules (v1)

### Trade list ordering
To ensure stable diffs and stable ticket rendering, intentions must be emitted in deterministic order:
1) `SELL` trades, then `BUY` trades
2) within each side: `internal_symbol` ascending
3) `sequence` assigned in that order (1..N)

### Units and rounding
- Units must be integer shares (store as numeric but represent an integer).
- Reference price comes from `market_prices_eod.close` at `asof_date_used`.
- If computing from notional:
  - `units = floor(abs(notional_value_base) / reference_price)`
- If `units == 0` after rounding, the trade must be omitted.

### Minimum trade size filter
Skip any trade with `abs(notional_value_base) < MIN_NOTIONAL_BASE`.

## Failure / Block Reasons (Contract)

Trade-builder must set `trade_builder_ok=false` and include a machine-readable reason string in the artifact when blocked:
- `TARGETS_MISSING` (no `portfolio_targets` rows for run)
- `TARGETS_ASOF_MISMATCH` (`portfolio_targets.asof_date != asof_date_used`)
- `PRICES_MISSING` (missing price bars for one or more target symbols on `asof_date_used`)
- `INTERNAL_ERROR` (unexpected exception; include sanitized error summary)

If prerequisites pass but the computed intended trades are empty due to thresholds / already at target:
- `trade_builder_ok=true` and `intended_trades=[]` (this is a successful “no-op”)

## Safety Constraints
- No external calls (no HTTP, no broker calls, no LLM calls).
- No randomness; no time-of-day dependence beyond explicitly passed `run_id/asof_date_used`.
- Default system behavior remains safe (NO_TRADE) unless explicitly enabled via a dry-run toggle in orchestration (to be implemented in M11.2.b+).
