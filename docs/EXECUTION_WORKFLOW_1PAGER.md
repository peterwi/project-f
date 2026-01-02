# Execution Workflow (1‑pager) — Manual eToro Desk

This SOP assumes the system produced a deterministic ticket.  
If anything is unclear, stop and mark the line item `FAILED` with notes.

Key rule: **NO DATA / NO RECONCILE / NO CONFIRM / NO TRADE**.

---

## 0) Before you start (pre-trade checklist)

- Confirm you received exactly one ticket (TRADE or NO‑TRADE) for this run.
- Confirm the ticket shows:
  - ticket_id
  - execution window (UK time)
  - per‑trade “skip if …” / slippage rule
- If the previous ticket is not fully confirmed in our system: **STOP** and escalate (do not trade).

## 1) For each trade line item (repeat)

For line item `#<sequence>`:

1. Search eToro using **exact** `etoro_search_name` from the ticket.
2. Validate:
   - Instrument matches the intended company.
   - Product type is **UNDERLYING** (not CFD).
   - Leverage is `x1` (no leverage).
3. Choose side exactly as specified: `BUY` or `SELL`.
4. Enter size exactly as specified:
   - Prefer value in base currency if the ticket specifies it; otherwise units.
5. Apply order type as specified:
   - Market (default) or Limit (if provided).
6. Apply the slippage rule:
   - If current price violates the rule, do not execute; mark `SKIPPED`.
7. Execute (manual click).

## 2) Record the result (required fields)

For each line item, record:
- `ticket_id`
- `sequence`
- `status`: `DONE / SKIPPED / FAILED / PARTIAL`
- `executed_value_base` (if available)
- `units` (if available)
- `fill_price` (if available)
- `filled_at` timestamp (state timezone used)
- `notes` (required if not DONE)

Common note values:
- `CFD_ONLY` (only CFD available; not executed)
- `NOT_FOUND` (instrument not findable)
- `MIN_SIZE_BLOCK` (minimum order size prevented execution)
- `SLIPPAGE_RULE` (skipped due to slippage rule)

## 3) Submit confirmations (post-trade)

- Submit confirmations for all line items the same day.
- If any line item is `FAILED` or `PARTIAL`, include notes sufficient to reconcile the ledger.

## 4) If you receive a NO‑TRADE ticket

- Do not trade.
- Confirm receipt in the confirmations workflow (status `NO_TRADE_ACK` if provided later).

