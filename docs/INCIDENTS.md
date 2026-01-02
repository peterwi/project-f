# Incidents

Record operational incidents and what was done to resolve them.

## Incident classes (v1)
- `DATA_STALE` — missing/late EOD bars, benchmark missing, coverage below threshold
- `CONFIRMATION_MISSING` — prior ticket not fully confirmed
- `RECONCILIATION_DRIFT` — snapshot drift above tolerance, unknown positions
- `UNTRADABLE_SYMBOL` — instrument not tradable as underlying on eToro
- `KILL_SWITCH` — drawdown/risk-off policy triggered

## Template

### YYYY-MM-DD — <INCIDENT_CLASS> — <short title>
- Impact:
- Detection (which gate failed, when):
- Root cause:
- Fix (what changed, where):
- Follow-ups (prevent recurrence):

## Templates

### YYYY-MM-DD — DATA_STALE — <short title>
- Impact:
- Detection:
- Missing/late symbols:
- Benchmark status:
- Fix:
- Follow-ups:

### YYYY-MM-DD — CONFIRMATION_MISSING — <short title>
- Impact:
- Detection:
- Missing confirmations:
- Fix:
- Follow-ups:

### YYYY-MM-DD — RECONCILIATION_DRIFT — <short title>
- Impact:
- Detection:
- Drift summary:
- Fix:
- Follow-ups:

### YYYY-MM-DD — UNTRADABLE_SYMBOL — <short title>
- Impact:
- Detection:
- Symbol(s):
- eToro notes (underlying vs CFD, availability):
- Fix:
- Follow-ups:

### YYYY-MM-DD — KILL_SWITCH — <short title>
- Impact:
- Detection:
- Trigger metric:
- Fix:
- Follow-ups:
