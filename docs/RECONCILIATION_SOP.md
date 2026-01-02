# Reconciliation SOP (eToro → Ledger)

Rule: **NO DATA / NO RECONCILE / NO CONFIRM / NO TRADE**.

This SOP defines how to capture an eToro snapshot and how to record it so the ledger can be trusted for sizing and risk gates.

## 1) What to capture (from eToro UI)

Capture one snapshot (same screen/time):

- `snapshot_date` (YYYY-MM-DD)
- `cash_gbp` = **Available cash (GBP)** (not total account value)
- `holdings` = **all non-zero positions** as `SYMBOL=UNITS` (fractional shares allowed)

Example (record of truth):

```yaml
snapshot_date: 2025-12-22
cash_gbp: 25.88
holdings:
  - MU: 2.64903
  - AAPL: 0.03655
```

## 2) Preconditions (must be true)

- Every symbol in the snapshot must exist in `config/universe.csv` (even if `enabled=false`).
- Postgres is running (`make up`) and healthy (`make health`).

## 3) How to record the snapshot (commands)

### 3.1 One-time ledger bootstrap (cash baseline)

Run once at system bootstrap:

```bash
make ledger-baseline CASH_GBP=<available_cash_gbp>
```

Notes:
- This creates exactly one `ledger_cash_movements` row with `movement_type=BASELINE`.
- If cash later changes (deposits/withdrawals/fees), record a separate cash movement (do not edit the baseline).

### 3.2 Add the snapshot

```bash
make reconcile-add -- \
  --snapshot-date YYYY-MM-DD \
  --cash-gbp <available_cash_gbp> \
  --position SYMBOL=UNITS \
  --position SYMBOL=UNITS
```

If the ledger has **no fills at all** (fresh DB), `reconcile-add` will create a single auditable **GENESIS** ticket and fills so `ledger_positions_current` matches the snapshot (positions only; cash remains controlled by the BASELINE row).

### 3.3 Run reconciliation gate

```bash
make reconcile-run
```

Expected:
- prints `RECONCILIATION_PASS`
- writes a report under `/data/trading-ops/artifacts/reports/reconcile_<snapshot_date>_*.md`

## 4) If reconciliation fails (what to do)

- Unknown symbols: add them to `config/universe.csv` and run `make universe-import`, then re-run `make reconcile-run`.
- Cash drift: confirm you used **Available cash**, not total value; then record missing deposits/withdrawals via cash movements.
- Units drift: confirm the snapshot includes **all** holdings and units copied exactly (including fractional shares).

