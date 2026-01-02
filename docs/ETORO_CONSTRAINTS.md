# eToro Constraints (Manual Execution Desk) — v1

This document is the source of truth for what is executable on eToro **manually**.
All items below are confirmed via real UI inspection and a real micro-trade.

Key rule: **NO DATA / NO RECONCILE / NO CONFIRM / NO TRADE**.

---

## A) Account + currency (CONFIRMED)

- Base account currency: **GBP**
- If base is GBP and instrument is USD:
  - FX conversion occurs: **YES** (automatic)
  - FX fee model: **Implicit via spread; not itemized**
- Portfolio and position values are displayed in GBP; prices for US stocks are displayed in USD.

---

## B) Instrument constraints (CONFIRMED)

- Allowed product type: **UNDERLYING ONLY (no CFDs)**: **CONFIRMED for stocks**
  - x1 leverage corresponds to buying the underlying stock
- Leverage: **DISALLOWED (must be x1 only)**: **CONFIRMED**
  - Higher leverage options (x2, x5) exist but are avoidable
- Short selling: **DISALLOWED**: **CONFIRMED**
  - Short tab exists but will not be used
- Fractional shares supported for stocks: **CONFIRMED**
  - Example: AAPL trade executed with fractional shares (0.03655)

---

### B1) ETFs and PRIIPs / KID reality check (CONFIRMED)

- US ETFs available as underlying for this account: **NO**
- Observed behavior:
  - ETFs (e.g. QQQ) are offered as **CFD only**
  - Explicit warning shown: *“You are not holding the underlying asset”*
- Decision:
  - ETFs are **NOT tradable instruments** in this system
  - ETFs may be used **ONLY as benchmarks** for reporting and comparison

---

## C) Order types + execution (CONFIRMED)

- Order types available for stocks:
  - Market: **CONFIRMED**
  - Limit: **CONFIRMED**
  - Stop / Stop-limit: **NOT REQUIRED FOR V1** (not confirmed)
- Execution context:
  - Trades executed during **regular market hours**
  - Prices sourced from NASDAQ (for US stocks)
- Typical spreads/slippage visibility:
  - Spread is implicit; no explicit spread number shown pre-trade
- Minimum order size (by value):
  - Small trades accepted (example: ~$10 USD AAPL trade executed successfully)
- Maximum order size / position limits:
  - No explicit UI limits observed; constrained by available balance

---

## D) What the executor can reliably record (CONFIRMED)

For each executed line item, the executor can reliably record:

- Ticket ID: **CONFIRMED** (from internal system)
- eToro instrument search name used: **CONFIRMED** (e.g. "AAPL")
- Executed status: **DONE / SKIPPED / FAILED / PARTIAL** — **CONFIRMED**
- Units/shares: **CONFIRMED**
  - Shown as “Shares” in position details
- Fill price: **CONFIRMED**
  - Shown as “Open Price” (in USD)
- Timestamp: **CONFIRMED**
  - Shown in position details, local time (e.g. `22/12/2025 12:29`)
- Executed value: **CONFIRMED**
  - Shown as “Value” in base currency (GBP)
- Notes: **CONFIRMED**
  - Manual free-text notes supported

These fields are sufficient for ledger accuracy, reconciliation, and P&L tracking.

---

## E) Operational workflow constraints (POLICY)

- Preferred execution window (UK time): **After US market open**
  - Typical window: **~14:35–15:30 UK time**
- US market open alignment:
  - Determined using NYSE market hours (DST-aware)
- Holidays / half-days:
  - Documented externally; no automation assumed
- 24/5 trading:
  - **NOT USED**; regular market hours only

---

## F) eToro search + symbol mapping rules (POLICY)

For every tradable instrument, the system must store:

- `internal_symbol` — stable internal key
- `etoro_search_name` — exact string used in eToro search
- Underlying tradable flag (true/false)
- Notes for any ambiguity (e.g. CFD-only, multiple listings)

Only instruments explicitly verified as **underlying stocks** may be traded.

---

## G) Test checklist (COMPLETED)

Test instrument: **AAPL (Apple)**

Observed and confirmed:
- Base currency: **GBP**
- Underlying stock available at x1: **YES**
- Fractional shares supported: **YES**
- Minimum order size: **small values accepted (~$10 USD)**
- Order types visible: **Market, Limit**
- Fill price visible: **YES (Open Price)**
- Timestamp visible: **YES (local time)**
- Units and invested value visible: **YES**

ETF inspection:
- QQQ:
  - Underlying available: **NO**
  - CFD-only: **YES**

---

## H) Decision impacts (LOCKED)

- Tradable universe = **STOCKS ONLY**
- ETFs excluded from execution; allowed only as benchmarks
- Long-only, no leverage, no CFDs
- Fractional-share, value-based position sizing supported
- Manual execution with deterministic trade tickets is feasible

Any change to these constraints requires repeating the confirmation process.
