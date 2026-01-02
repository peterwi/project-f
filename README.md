# project-f

Target operating model for a human-in-the-loop trading workflow where the machine handles data, models, risk, tickets, and reporting; your wife executes on eToro with deterministic guardrails.

## Roles

**The machine does (automated)**
- Downloads market data (EOD)
- Maintains a clean symbol universe (tradable on eToro)
- Runs model + risk rules
- Generates trade tickets with exact steps and position sizes
- Sends tickets + records confirmations
- Tracks P&L from the ledger

**Your wife does (manual, guided)**
- Executes trade tickets on eToro
- Copies back fill prices / status using a simple form

## Definition of success (v1)

- A scheduled run produces exactly one unambiguous ticket and (later) one set of confirmations; no “judgement calls” required during execution.
- Hard safety gates stop trading automatically: data quality fail, reconciliation fail, risk rule fail, kill switch, or missing confirmations.
- The ledger matches reality closely enough to size trades safely (cash + positions), with a regular reconciliation routine.
- Every run is reproducible + auditable: config + code hash, inputs/outputs saved, decisions explainable after the fact.

## Recommended build order (critical path)

- Build the boring ops loop first: Projects 0–4 and 6–9 (use a stub signal generator before ML is ready).
- Add Qlib/ML only after the ops loop is stable for multiple weeks: Project 5.
- Treat MCP/GenAI as optional/last-mile automation: Project 10 (only once everything is deterministic).

## Operating rules (to keep it from failing)

- “No data / no reconcile / no trade”: if any gate fails, emit a `NO-TRADE` ticket that explains why.
- Freeze strategy + risk config in live phases; allow only ops bugfixes (and keep a short changelog).
- Always keep rollback paths: last known-good ticket, last known-good config, and tested DB restore from backups.

## Suggested default go/no-go gates (tune later)

- **Data quality:** latest trading day present for ≥ 98% of universe; zero duplicate dates; hard-fail if benchmark is missing
- **Reconciliation:** cash + position drift stays within a small tolerance; hard-fail on unknown/unmatched positions
- **Confirmations:** hard-fail the next run if the previous ticket has missing/ambiguous execution status
- **Ticket sanity:** hard-fail if any trade implies going short, exceeds max weight, violates cash buffer, or uses unknown symbols

## Projects and deliverables

### Project 0 — Non-negotiables and guardrails
- Decide run cadence: daily UK morning (recommended) or weekly
- Decide rebalance cadence: weekly (recommended to start) or daily (only with strict turnover limits)
- Decide strategy type: long-only (recommended for eToro manual)
- Decide instrument constraints: underlying only (no CFDs), no leverage
- Decide risk-off policy: allow moving partly/fully to cash when conditions are bad (recommended) vs always invested
- Decide max positions: start with 10–20
- Decide max position weight: 5–10%
- Decide kill switch: e.g., pause trading if drawdown > 20%
- Decide machine run time (UK): e.g., every weekday 08:00–09:00 UK (ticket ready well before US open)
- Decide execution window (relative to US open; handle DST shifts): e.g., 5–60 minutes after open
- Decide distribution policy: reinvest all (recommended) vs periodic distributions (later)
- **Deliverable:** `config/policy.yml` with these values

### Project 1 — System setup on Mac Pro Ubuntu
- Install Ubuntu 22.04/24.04
- Install Docker + docker-compose
- Create repo `trading-ops/` with directories:
  - `config/`, `data/raw/`, `data/clean/`, `db/`, `reports/`, `tickets/`, `logs/`, `services/`
- DB: Postgres is the source of truth (see `PLAN.md`)
- Add a single “one-command run” entrypoint (Makefile/script) that executes the whole pipeline in order and returns non-zero on any gate failure
- **Deliverable:** running host + repo skeleton + automatic backups

### Project 2 — Data sources (EOD) done properly

**2.1 Minimum data per symbol/day**
- Date, open, high, low, close, adjusted close (if available), volume
- Corporate actions (splits/dividends) or adjusted prices that include them

**2.2 Data source options**
- **Option A (recommended starter): Stooq** — simple to fetch; adjusted data often available; watch symbol naming and gaps
- **Option B: Yahoo-style (yfinance)** — convenient, broad coverage; unofficial API so add caching/retries
- **Option C: Paid data** — stable but costs money; usually overkill at v1
- **Recommendation:** Stooq primary + yfinance fallback + cleaning layer that normalizes both

**2.3 Data pipeline architecture**
- **Stage 1: Raw download (immutable)**
  - Save exactly what you download per symbol per run (never overwrite), e.g., `data/raw/stooq/AAPL/2025-12-18.csv`
  - `fetch_eod.py`: loop universe tickers, download last N days, write raw with timestamp, retry failures, log errors
- **Stage 2: Normalize + clean (truth feed)**
  - Unified schema: symbol, date (UTC), open/high/low/close/volume/adj_close, source
  - `normalize_eod.py`: parse raw, enforce types, sort by date, drop duplicates, flag missing days, output `data/clean/eod.parquet` (or CSV)
- **Stage 3: Corporate actions handling**
  - Compute adjustment factor = `adj_close / close`; multiply OHLC by factor to get adjusted OHLC
  - Add validation: if split occurs, close jumps but adjusted series smooths
  - Store adjusted OHLC in the clean dataset
- **Stage 4: Market calendar sanity**
  - Validate each symbol shares trading dates with benchmark; log missing bars
  - Prefer excluding degraded symbols over forward-filling entire bars
- **Stage 5: “freshness” for pre-open runs**
  - When running in the UK morning, treat “latest available bar” as the previous US trading day (T-1), not “today”
  - Hard-fail if the benchmark’s latest bar is stale (prevents trading on missing/partial EOD updates)
- **Deliverable:** `data/clean/eod.parquet` updated daily + data quality report with hard thresholds (pass/fail) and a “no trade if failed” status

### Project 3 — Universe: “only things your wife can trade on eToro”
- Build `config/universe.csv` with: internal_symbol, data_source_symbol (stooq/yahoo), etoro_search_name, exchange/currency, instrument_type (stock/ETF), tradable_as_underlying (true/false), notes
- Pick universe size: 50 / 100 / 200; add ETF benchmarks (SPY/QQQ)
- Wife verifies each symbol is findable/tradable on eToro
- **Deliverable:** curated `config/universe.csv`

### Project 4 — Ledger DB (portfolio truth) + workflow
- Tables: accounts, positions, trades (intended), fills (actual), prices (optional cache), tickets, events (audit)
- Implement `ledger.py` functions: record_ticket, record_intended_trade, record_fill, compute_positions, compute_equity_curve, compute_pnl_realized_unrealized
- Add reconciliation: a periodic “ledger vs eToro snapshot” check that blocks trading if drift is above a tolerance
- **Deliverable:** reconstruct portfolio state purely from DB + reconciliation report

### Project 5 — Strategy engine: Qlib used correctly
- V1 model: LightGBM ranker/regression-to-ranking; weekly rebalance to start; objective: predict next-week return
- Daily run cadence can still produce a daily “should we trade?” decision (usually “no”) via turnover and “tiny deltas” rules
- Features: momentum (1w/4w/12w), volatility, volume/turnover proxy, mean reversion
- Prepare Qlib dataset from `data/clean/eod.parquet`; train rolling window; output ranked list to `signals/YYYY-MM-DD.csv`
- Generate backtest report weekly (`reports/backtest_latest.html` or markdown)
- **Deliverables:** `reports/backtest_latest.html`, `signals/latest.csv`

### Project 6 — Risk guard: deterministic approvals
- Rules: max positions, max position weight, min cash buffer, max turnover per rebalance, optional no-buy list, kill switch on drawdown
- Inputs: ranked list + current ledger positions + prices; outputs: approved target portfolio → trade list
- Validate trade list with explicit reasons; if any check fails, block trades and emit a human-readable explanation
- **Deliverables:** `trades/proposed_YYYY-MM-DD.json` + `trades/approved_YYYY-MM-DD.json`

### Project 7 — Ticket generator for your wife (ops-grade)
- Ticket contents: ticket ID + date + version hash; ordered trades with side, units or value, order type, limit, execution window, slippage rule; “if blocked” instructions
- Include checklists: pre-trade (market open, cash buffer, kill switch not triggered) and post-trade (confirm fills entered, report reviewed)
- For daily cadence, include a “do nothing if tiny deltas” section (so daily runs don’t force unnecessary trades)
- Generate Markdown (readable) and JSON (machine) artifacts
- **Deliverable:** `tickets/TICKET-YYYYMMDD.md`

### Project 8 — Comms + confirmation workflow (no scraping)
- Send ticket via email or Slack
- Confirmation form (local or Google Form) at `/confirm/TICKET-ID` with executed?, fill price?, notes?, optional screenshot
- Support partial fills / “couldn’t execute” states so the ledger stays truthful even when reality is messy
- Ingest confirmations → writes fills to ledger; recompute positions and P&L
- **Deliverable:** closed loop from ticket → fills → ledger

### Project 9 — Monitoring and reporting (profitability tracking)
- **Daily report:** equity, unrealized/realized P&L, drawdown, exposure by symbol/sector, next scheduled rebalance
- **Weekly report:** trades executed vs intended, slippage summary, strategy performance vs benchmark
- Alerts: missing confirmations, drawdown triggers, data quality issues, reconciliation drift
- **Deliverables:** `reports/daily.md`, `reports/weekly.md`

### Project 10 — MCP + GenAI integration (safe design)

**MCP servers to build**
- `mcp-market-data` (read-only): `get_prices()`, `get_returns()`, `data_quality_report()`
- `mcp-qlib` (compute): `run_training()`, `run_scoring()`, `run_backtest()`
- `mcp-ledger` (constrained read/write): `read_positions()`, `write_fills()`, `compute_pnl()`
- `mcp-riskguard` (deterministic): `build_target_portfolio()`, `approve_trades()`
- `mcp-ticketing`: `render_ticket_md()`, `send_ticket()`, `receive_confirmation()`

**Agent behavior**
- Quant: calls qlib + riskguard
- Ops: produces ticket + instructions
- Explainer: generates “why” section and highlights anomalies
- No agent can bypass riskguard

### Project 11 — Rollout plan (so you don’t blow it up)
**Phase 0: Pre-flight (1 week)**
- Confirm eToro execution constraints (fractional shares, minimum order size, order types available, “underlying vs CFD” defaults, base currency/FX fees)
- UK reality check: which ETFs (if any) are available as underlying (PRIIPs/KID constraints often force ETF CFDs); if ETFs aren’t available, design universe around stocks + cash
- Confirm US dividend withholding setup (W-8BEN status) so “income mode” assumptions aren’t surprised by taxes
- Write a 1-page runbook for the human workflow (where to click, what to copy/paste, what “blocked” means)
- Decide reconciliation method (manual snapshot fields to capture) and set tolerances that will block trading
- Set the daily schedule: machine run in UK morning; execution at/after US open (DST-aware); confirmation deadline same day

**Phase A: Dry-run (2–4 weeks)**
- Run end-to-end on the real schedule; generate tickets; paper-execute (use a consistent fill assumption); ingest confirmations; produce reports
- Track operational metrics: time-to-ticket, % symbols passing data quality, # blocked trades (and why), confirmation latency, reconciliation drift
- **Exit criteria:** 4 consecutive runs with (1) data quality pass, (2) reconciliation pass, (3) ticket generated, (4) confirmations ingested, (5) reports produced, and zero “critical” ticket defects

**Phase B: Small capital (4–8 weeks)**
- Trade with 10–20% allocation
- Freeze strategy + risk configuration; only ops bugfixes allowed (no “just tweaking the model”)
- Weekly reconciliation vs eToro snapshot; review slippage and execution compliance
- **Exit criteria:** reconciliation passes every week, no kill switch triggers, and execution compliance stays high enough that the system’s assumptions remain valid

**Phase C: Scale**
- Increase allocation in small steps (e.g., +10% every 4 weeks) only if Phase B exit criteria continue to hold
- Introduce controlled change windows (e.g., one planned strategy update per month, always preceded by backtest + dry-run)

## Data sources decision (recommended now)
- Start with Stooq primary, yfinance fallback; use adjusted close to generate adjusted OHLC
- Store unified clean dataset in Parquet
- Produce a data quality report every run
- Maintain curated `config/universe.csv` for eToro tradability

## Inputs needed to lock exact build order
- Run cadence: daily UK morning or weekly?
- Rebalance: weekly or daily?
- Universe size: 50 / 100 / 200?
- Positions held: 10 / 20?
- Base currency in eToro: GBP or USD?
- Position sizing: £ value (recommended) or units?
- CFDs/leverage: allowed or banned? (recommended: banned)
- Execution style: market orders at fixed time vs limit orders with a rule?
- Reconciliation snapshot: what fields can be captured reliably each run (cash, positions, fees)?
- Machine run time (UK) and latest acceptable “staleness” for EOD data (e.g., benchmark must have T-1)

_If you don’t answer, defaults will be: daily run cadence (UK morning), weekly rebalance, no CFDs/leverage, universe=100, positions=15, GBP, £-value sizing, reinvest all._
