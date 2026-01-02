# M11 — Main Goal: Deterministic TRADE Ticket (safe dry-run first)

Goal: fully automated daily workflow that produces deterministic trade instructions (“ticket”) once per day:
- 08:00 UK: data/status + gates + reports
- 14:00 UK: ticket for manual execution near US open + confirmations capture

Safety defaults:
- **No live trading is enabled by this milestone.**
- The default remains **NO_TRADE** unless the operator explicitly enables a dry-run trade mode toggle.
- Deterministic gates win; LLM cannot approve trades.

Run references (current known-good baseline):
- Data-quality PASS report: `/data/trading-ops/artifacts/reports/data_quality_2025-12-31_20260102T214740Z.md`
- Run-1400 reference: `/data/trading-ops/artifacts/runs/85828a71-6dff-438f-afa0-ead2033e3692/run_summary.md`

---

## M11.1 Universe readiness (benchmarks + enough symbols + ingest coverage)

- [x] **M11.1.a Confirm enabled universe and benchmark rows**
  - Objective: Ensure `config_universe` contains (1) enabled tradables and (2) benchmark rows (e.g. `SPY`, `QQQ`) for reporting/gates.
  - Commands:
    - `python scripts/universe_import.py`
    - `python scripts/universe_validate.py`
    - `set -a; source config/secrets.env; set +a; docker compose -f docker/compose.yml --env-file config/secrets.env exec -T postgres psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -tA -c "select internal_symbol, enabled, stooq_symbol, instrument_type from config_universe where enabled=true or lower(coalesce(instrument_type,''))<>'stock' order by internal_symbol;"`
  - Verification:
    - Enabled symbols count is >0.
    - Benchmarks include at least `SPY` and `QQQ` (or documented alternatives).
  - Artifacts:
    - `/data/trading-ops/artifacts/reports/universe_validation.md`
  - Done when:
    - Enabled + benchmark rows exist and validation passes.

- [x] **M11.1.b Prove market-fetch populates EOD bars for enabled + benchmarks**
  - Objective: Ensure `make market-fetch` writes rows into `market_prices_eod` for enabled symbols and benchmarks.
  - Commands:
    - `make market-fetch`
    - `set -a; source config/secrets.env; set +a; docker compose -f docker/compose.yml --env-file config/secrets.env exec -T postgres psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -tA -c "select internal_symbol, max(trading_date) from market_prices_eod where internal_symbol in (select internal_symbol from config_universe where enabled=true or lower(coalesce(instrument_type,''))<>'stock') group by 1 order by 1;"`
  - Verification:
    - Each enabled symbol has at least one bar.
    - Benchmarks have at least one bar.
  - Artifacts:
    - Raw CSVs under `/data/trading-ops/data/raw/stooq/<SYMBOL>/...`
  - Done when:
    - EOD bars exist for enabled + benchmarks in Postgres.

- [x] **M11.1.c Ensure data-quality PASS with current universe**
  - Objective: Ensure the deterministic gate passes with the enabled universe and benchmarks.
  - Commands:
    - `make run-0800`
    - `make run-1400`
  - Verification:
    - Data-quality report is PASS for the chosen as-of date (weekday expected + possible holiday fallback).
  - Artifacts:
    - `/data/trading-ops/artifacts/reports/data_quality_*.md`
    - `/data/trading-ops/artifacts/runs/<run_id>/run_summary.md`
  - Done when:
    - Data-quality PASS and runs complete without refetch at 14:00.

- [x] **M11.1.d Schema-safe DB verification queries (no `run_label` / `tickets.asof_date`)**
  - Objective: Provide stable, schema-correct verification commands so the pipeline is never blocked by mismatched column names.
  - Commands:
    - `make runs-last`
    - `make tickets-last`
  - Verification:
    - Commands succeed and print recent runs/tickets without SQL errors.
  - Artifacts:
    - None (read-only DB queries).
  - Done when:
    - `make runs-last` and `make tickets-last` succeed in the current schema.

---

## M11.2 Trade-builder implementation (deterministic; dry-run mode)

- [x] **M11.2.a Define trade-builder contract**
  - Objective: Define deterministic inputs/outputs and safety constraints for trade building.
  - Commands:
    - Inspect existing tables: `portfolio_targets`, `ledger_cash_movements`, `ledger_trades_intended`, `market_prices_eod`
    - Add a short spec doc or update existing docs (if present).
  - Verification:
    - Contract explicitly states: no external calls; deterministic; no trading authority; produces intended trades only.
  - Artifacts:
    - `docs/TRADE_BUILDER_CONTRACT.md`
    - `docs/TRADE_BUILDER_CONTRACT.schema.json`
  - Done when:
    - Contract is written and agreed by checklist.

- [x] **M11.2.b Implement `trade_builder` (writes ledger_trades_intended)**
  - Objective: Deterministically convert `portfolio_targets` + latest prices + cash constraints into `ledger_trades_intended`.
  - Commands:
    - `make run-1400` with `DRYRUN_TRADES=true` (trade-builder runs inside riskguard step)
    - Optional: `make trade-builder RUN_ID=<run_id>` (manual re-run for a specific run)
  - Verification:
    - With `DRYRUN_TRADES=true`, run artifacts include `trades_intended.json` and `risk_checks.trade_builder` is present.
    - With default settings, still produces `NO_TRADE` (trade-builder disabled by default).
  - Artifacts:
    - `/data/trading-ops/artifacts/runs/<run_id>/trades_intended.json`
    - DB: `ledger_trades_intended` rows for the run (may be 0 if no rebalancing / below min notional)
  - Done when:
    - Intended trades are produced deterministically in dry-run mode only.

---

## M11.3 Ticket rendering for TRADE (GBP sizing + “skip if not tradable” rules)

- [x] **M11.3.a Extend ticket renderer to include TRADE lines**
  - Objective: Render intended trades into human-executable ticket lines with GBP sizing guidance.
  - Commands:
    - Run: `make run-1400` with `DRYRUN_TRADES=true`
    - Inspect ticket artifact directory.
  - Verification:
    - Ticket includes BUY/SELL lines (rendered from `trades_intended.json`) and explicit “skip if only CFD / not found” rules.
  - Artifacts:
    - `/data/trading-ops/artifacts/tickets/<ticket_id>/ticket.md`
  - Done when:
    - Ticket renders intended trade lines deterministically (even if DECISION remains NO_TRADE due to gating).

---

## M11.4 Confirmation capture for TRADE (fills → ledger_trades_fills)

- [x] **M11.4.a Extend confirmation payload to capture fills**
  - Objective: Capture executed fills (units/value, price, timestamp) and persist into `ledger_trades_fills`.
  - Commands:
    - Submit fills (writes confirmation artifact + DB rows):
      - `make confirm-fills FILLS_JSON=/path/to/fills.json`
      - or `python3 scripts/confirmations_submit.py --fills-json /path/to/fills.json --ticket-id <ticket_id>`
  - Verification:
    - Confirmation artifact exists under the ticket dir.
    - DB rows exist under `ledger_trades_fills` for that `ticket_id`.
    - Ticket renderer shows a “Confirmed fills” section when fills exist (or links to confirmation artifacts).
  - Artifacts:
    - `/data/trading-ops/artifacts/tickets/<ticket_id>/confirmations/<confirmation_uuid>/confirmation.json`
    - `/data/trading-ops/artifacts/tickets/<ticket_id>/confirmations/<confirmation_uuid>/confirmation.md`
    - DB: `ledger_trades_fills` rows for `<ticket_id>`
  - Done when:
    - Fill capture is reproducible and auditable.

---

## M11.5 End-to-end daily loop (08:00 status, 14:00 ticket; dry-run toggle)

- [x] **M11.5.a Wire dry-run trade ticket into 14:00**
  - Objective: Make `make run-1400` produce a TRADE ticket only when `DRYRUN_TRADES=true` and gates PASS.
  - Commands:
    - `DRYRUN_TRADES=true make run-1400`
    - `make run-1400` (default)
  - Verification:
    - Dry-run mode produces TRADE ticket; default produces NO_TRADE ticket.
  - Artifacts:
    - `/data/trading-ops/artifacts/runs/<run_id>/run_summary.md`
    - `/data/trading-ops/artifacts/tickets/<ticket_id>/ticket.md`
  - Done when:
    - Daily loop is deterministic and safe by default.

---

## DEFERRED (nice-to-have): alerting / secondary sinks

Alerting work is deferred for now; file artifacts remain primary. Revisit later.

- **Deferred:** docs/ALERTS.md + configuration keys (formerly M10.C.2)
- **Deferred:** NTFY secondary sink implementation + receipts (formerly M10.C.3–M10.C.7)

Notes:
- Alert migrations were applied and tables exist (`alerts`, `alert_deliveries`), but no further delivery feature work is required for the main goal.
