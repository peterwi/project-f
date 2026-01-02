# M10 — Data Quality PASS + Real Alerts Delivery (Execution Checklist)

This milestone combines:
- **M10.B — Data Quality PASS** (pipeline reliability)
- **M10.C — Real secondary alert sink** (visibility + receipts)

Hard boundaries:
- No trading is enabled by this milestone (NO-TRADE only).
- Changes must be minimal and deterministic; failures must produce artifacts under `/data`.

Pass definitions:
- **M10.B PASS**
  - `make run-0800` produces `DATA_QUALITY_PASS` and writes a PASS report under `/data/trading-ops/artifacts/reports/`
  - AND `make run-1400` can run without refetch and does not fail the data-quality gate.
- **M10.C PASS**
  - A secondary sink is enabled with `ALERT_SECONDARY_DRYRUN=false`
  - A delivery receipt is written under `/data/trading-ops/artifacts/alerts/<alert_id>/delivery.md` showing `SENT` (or deterministic `FAILED` with HTTP/status reason)
  - Deliveries are recorded in Postgres (`alert_deliveries`).

---

## M10.B — Data Quality PASS

- [x] **M10.B.1 Identify the current failing rule(s)**
  - Objective: Use the latest data-quality report artifact to pinpoint the exact failing rule(s) and inputs.
  - Commands:
    - `ls -1t /data/trading-ops/artifacts/reports/*data_quality* | head -n 5`
    - `sed -n '1,220p' <latest_report>`
    - If no report exists: `make run-0800` then re-check.
  - Verification:
    - A concrete rule name/error text and the relevant symbol/date context are identified.
  - Artifacts:
    - Existing report under `/data/trading-ops/artifacts/reports/`
    - `docs/PM_LOG.md` entry recording the failing rule and report path.
  - Done when:
    - The failing rule(s) are identified and logged, with the report path.

- [ ] **M10.B.2 Capture a fresh baseline run (0800)**
  - Objective: Run the 08:00 pipeline and capture a reproducible baseline artifact set for debugging.
  - Commands:
    - `git status --porcelain=v1`
    - `make run-0800`
    - `ls -1t /data/trading-ops/artifacts/reports/*data_quality* | head -n 3`
  - Verification:
    - A new data-quality report is written under `/data/trading-ops/artifacts/reports/`.
  - Artifacts:
    - `/data/trading-ops/artifacts/reports/<new_report>`
  - Done when:
    - The newest report corresponds to this run and clearly shows PASS/FAIL with reasons.

- [ ] **M10.B.3 Fix universe/benchmark emptiness issues (if present)**
  - Objective: Ensure universe is non-empty, enabled symbols are used, and benchmarks/index rows exist when required by gates.
  - Commands:
    - `python scripts/universe_validate.py`
    - `python scripts/market_fetch_eod.py --max-rows 20`
    - Re-run: `make run-0800`
  - Verification:
    - Universe validation passes and data-quality report no longer fails for “empty universe” / missing benchmark.
  - Artifacts:
    - Updated data-quality report(s) under `/data/trading-ops/artifacts/reports/`
  - Done when:
    - The report no longer cites universe/benchmark emptiness as a failure cause.

- [ ] **M10.B.4 Fix symbol mapping issues (stooq symbol suffix, etc.)**
  - Objective: Ensure provider symbol mapping is correct so ingestion produces rows for expected symbols.
  - Commands:
    - `python scripts/market_fetch_eod.py --max-rows 50`
    - Inspect the failing symbols from the report and verify mapping config/code.
    - Re-run: `make run-0800`
  - Verification:
    - Previously missing symbols now ingest, and the report no longer fails on missing/mismatched symbol mapping.
  - Artifacts:
    - Updated report(s) under `/data/trading-ops/artifacts/reports/`
  - Done when:
    - The gate stops failing due to symbol mapping.

- [ ] **M10.B.5 Fix “as-of/freshness” day alignment (US T-1 expectation)**
  - Objective: Ensure freshness logic aligns with US trading calendar for UK-morning runs (typically expects last US trading day).
  - Commands:
    - Inspect rule implementation and current “as-of” date logic.
    - Re-run: `make run-0800`
  - Verification:
    - Data-quality report passes freshness checks on normal weekdays and handles holidays deterministically.
  - Artifacts:
    - Updated report(s) under `/data/trading-ops/artifacts/reports/`
    - If behavior changes: update `docs/DATA_QUALITY_RULES.md`
  - Done when:
    - Freshness checks are consistently PASS given correct upstream data.

- [ ] **M10.B.6 Fix duplicates/conflicting rows (if present)**
  - Objective: Remove deterministic-gate failures caused by duplicate/conflicting rows per symbol/date/source.
  - Commands:
    - Use report evidence to identify the duplication source.
    - Run reconciliation/selftest where applicable:
      - `python scripts/reconcile_selftest.py`
    - Re-run: `make run-0800`
  - Verification:
    - Data-quality report no longer fails due to duplicates/conflicts.
  - Artifacts:
    - Updated report(s) under `/data/trading-ops/artifacts/reports/`
  - Done when:
    - Duplicate/conflict related failures are eliminated.

- [ ] **M10.B.7 Prove 14:00 run does not refetch and still passes**
  - Objective: Ensure `make run-1400` works without refetch and does not fail data-quality.
  - Commands:
    - `make run-0800`
    - `make run-1400`
    - `ls -1t /data/trading-ops/artifacts/reports/*data_quality* | head -n 6`
  - Verification:
    - Both runs complete and the newest data-quality reports indicate PASS.
  - Artifacts:
    - PASS report(s) under `/data/trading-ops/artifacts/reports/`
  - Done when:
    - **M10.B PASS** definition is satisfied.

---

## M10.C — Real secondary alert sink (enable delivery; file-only remains primary)

- [ ] **M10.C.1 Inventory current alert pipeline + DB table**
  - Objective: Confirm existing alert scripts, delivery artifacts, and Postgres table `alert_deliveries` are present and reachable.
  - Commands:
    - `ls -la scripts | rg -n \"alert_(emit|deliver)\"`
    - `docker compose -f docker/compose.yml --env-file config/secrets.env exec -T postgres psql -U \"$POSTGRES_USER\" -d \"$POSTGRES_DB\" -v ON_ERROR_STOP=1 -c \"\\d alert_deliveries\"`
  - Verification:
    - Table exists and alert scripts are present.
  - Artifacts:
    - `docs/PM_LOG.md` entry capturing the table existence check.
  - Done when:
    - Alert scripts and DB table are confirmed.

- [ ] **M10.C.2 Add docs/ALERTS.md and configuration keys**
  - Objective: Document configuration for secondary sink delivery (default to NTFY) with explicit dry-run controls.
  - Commands:
    - Edit docs and add a one-command smoke test section.
  - Verification:
    - Docs clearly state required env vars and safety defaults.
  - Artifacts:
    - `docs/ALERTS.md`
  - Done when:
    - Docs exist and include a one-command smoke test.

- [ ] **M10.C.3 Implement NTFY secondary sink delivery (real HTTPS)**
  - Objective: Add an NTFY sender to `scripts/alert_deliver.py` controlled by env vars and `ALERT_SECONDARY_DRYRUN`.
  - Commands:
    - Update code to POST to `ALERT_NTFY_URL` with sanitized content.
  - Verification:
    - Delivery attempt produces deterministic receipts (`SENT` or `FAILED` with HTTP/status reason).
  - Artifacts:
    - `/data/trading-ops/artifacts/alerts/<alert_id>/delivery.json`
    - `/data/trading-ops/artifacts/alerts/<alert_id>/delivery.md`
  - Done when:
    - Receipts include timestamp, sink, HTTP status, and response summary (no secrets).

- [ ] **M10.C.4 Persist delivery receipts to Postgres**
  - Objective: Ensure `alert_deliveries` records are written for secondary sink attempts.
  - Commands:
    - Run a delivery and query:
      - `docker compose -f docker/compose.yml --env-file config/secrets.env exec -T postgres psql -U \"$POSTGRES_USER\" -d \"$POSTGRES_DB\" -tA -c \"select sink, dryrun, status, coalesce(error_text,'') from alert_deliveries order by created_at desc limit 5;\"`
  - Verification:
    - A row exists for the most recent delivery attempt with correct `sink`, `dryrun`, and `status`.
  - Artifacts:
    - DB rows + delivery receipts under `/data/.../alerts/...`
  - Done when:
    - DB rows reflect delivery attempts deterministically.

- [ ] **M10.C.5 Add “secondary delivery smoke test” (no trading)**
  - Objective: Provide a safe one-command test that emits a synthetic alert and delivers it.
  - Commands:
    - Document and run a test command that:
      - emits an alert (synthetic)
      - delivers with `ALERT_SECONDARY_DRYRUN=false`
  - Verification:
    - Receipt shows `SENT` or deterministic `FAILED` with reason, and DB row exists.
  - Artifacts:
    - `/data/trading-ops/artifacts/alerts/<alert_id>/delivery.md`
  - Done when:
    - Smoke test is repeatable and produces receipts + DB row.

- [ ] **M10.C.6 Ensure file-only remains primary sink**
  - Objective: Keep existing file-only receipts as the primary mechanism; secondary sink is additive.
  - Commands:
    - Confirm primary artifacts still written even if secondary fails.
  - Verification:
    - Local artifact generation does not depend on secondary sink success.
  - Artifacts:
    - `/data/trading-ops/artifacts/alerts/<alert_id>/delivery.md`
  - Done when:
    - Primary artifacts always exist; secondary is best-effort.

- [ ] **M10.C.7 End-to-end proof**
  - Objective: Prove **M10.C PASS** definition with one real delivery attempt.
  - Commands:
    - Run smoke test and verify:
      - receipts exist
      - Postgres rows exist
  - Verification:
    - **M10.C PASS** definition is satisfied.
  - Artifacts:
    - Delivery receipts + DB rows + `docs/PM_LOG.md` evidence.
  - Done when:
    - **M10.C PASS** definition is satisfied.
