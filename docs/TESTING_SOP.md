# Testing SOP (one command)

This repo provides a deterministic, safe-by-default test harness that runs a full “day” pipeline and captures all proofs under `/data/trading-ops/artifacts/test_runs/`.

## Run one command

Safe mode (expected: `NO_TRADE`, but full loop completes):

```bash
cd /home/peter/git/project-f
make test-harness -- --date YYYY-MM-DD
```

Dry-run trades mode (still no broker automation; may remain `NO_TRADE` depending on gates):

```bash
cd /home/peter/git/project-f
make test-harness -- --date YYYY-MM-DD --dryrun-trades
```

## Where outputs go

Each run writes one folder:

`/data/trading-ops/artifacts/test_runs/<date>/<timestamp>-git<shortsha>/`

Key contents:

- `README.md`: human report (deterministic sections)
- `ids.env`: machine-readable ids and hashes
- `logs/`: stdout/stderr per step
- `runs/`: run summaries + key run artifacts
- `tickets/`: ticket markdown/json + material hash (14:00)
- `reconcile/`: reconcile reports (pre-14:00 + final)
- `db/`: DB proof outputs (tables, counts, determinism proof)
- `market_cache/`: manifest + small samples of `prices_eod.csv`

## How to interpret the README

Start at:

- **Decision**: `decision_type` and `material_hash`
- **Why NO_TRADE**: reasons copied verbatim from `no_trade.json` (if present)
- **Data proofs**: confirms the market cache manifest and price samples used
- **DB proofs**: counts and latest reconciliation entries for the test date

## If it fails

1) Read `logs/*.stderr.log` first.
2) Check `db/db_tables.txt` for missing tables/migrations.
3) Check `db/ticket_determinism.txt` for ticket stability proofs.
4) Re-run in the same working tree only after `git status --porcelain=v1` is clean (the harness refuses to run on a dirty repo).

