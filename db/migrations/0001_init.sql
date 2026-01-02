BEGIN;

CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- Runs (one pipeline invocation)
CREATE TABLE runs (
  run_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  started_at timestamptz NOT NULL DEFAULT now(),
  finished_at timestamptz,
  status text NOT NULL DEFAULT 'running',
  asof_date date,
  cadence text,
  config_hash text NOT NULL,
  git_commit text NOT NULL,
  notes text,
  created_at timestamptz NOT NULL DEFAULT now()
);

-- Config: policy (single active row)
CREATE TABLE config_policy (
  policy_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  name text NOT NULL DEFAULT 'default',
  policy_yaml text NOT NULL,
  active boolean NOT NULL DEFAULT false,
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX config_policy_one_active_idx
  ON config_policy (active)
  WHERE active = true;

-- Config: tradable universe
CREATE TABLE config_universe (
  universe_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  internal_symbol text NOT NULL UNIQUE,
  stooq_symbol text,
  yahoo_symbol text,
  etoro_search_name text,
  currency text,
  instrument_type text,
  tradable_underlying boolean NOT NULL DEFAULT true,
  enabled boolean NOT NULL DEFAULT true,
  notes text,
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX config_universe_enabled_idx ON config_universe (enabled);

-- Market data: EOD prices (normalized)
CREATE TABLE market_prices_eod (
  price_id bigserial PRIMARY KEY,
  internal_symbol text NOT NULL REFERENCES config_universe(internal_symbol),
  trading_date date NOT NULL,
  open numeric,
  high numeric,
  low numeric,
  close numeric,
  adj_close numeric,
  volume bigint,
  source text NOT NULL DEFAULT 'unknown',
  quality_flags jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (internal_symbol, trading_date, source)
);

CREATE INDEX market_prices_eod_symbol_date_idx
  ON market_prices_eod (internal_symbol, trading_date);

CREATE INDEX market_prices_eod_date_idx
  ON market_prices_eod (trading_date);

-- Signals: ranked list per run (stub or ML later)
CREATE TABLE signals_ranked (
  signal_id bigserial PRIMARY KEY,
  run_id uuid NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
  asof_date date NOT NULL,
  internal_symbol text NOT NULL REFERENCES config_universe(internal_symbol),
  score numeric NOT NULL,
  rank integer,
  model_version text,
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (run_id, internal_symbol)
);

CREATE INDEX signals_ranked_run_rank_idx ON signals_ranked (run_id, rank);

-- Targets: desired portfolio
CREATE TABLE portfolio_targets (
  target_id bigserial PRIMARY KEY,
  run_id uuid NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
  asof_date date NOT NULL,
  internal_symbol text NOT NULL REFERENCES config_universe(internal_symbol),
  target_weight numeric NOT NULL,
  target_value_base numeric,
  base_currency text,
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (run_id, internal_symbol)
);

-- Tickets: one per run (either TRADE or NO-TRADE)
CREATE TABLE tickets (
  ticket_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  run_id uuid NOT NULL UNIQUE REFERENCES runs(run_id) ON DELETE CASCADE,
  ticket_type text NOT NULL,
  status text NOT NULL DEFAULT 'DRAFT',
  rendered_md text,
  rendered_json jsonb,
  sent_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT now()
);

-- Intended trades (produced by riskguard)
CREATE TABLE ledger_trades_intended (
  intended_id bigserial PRIMARY KEY,
  run_id uuid NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
  ticket_id uuid REFERENCES tickets(ticket_id) ON DELETE SET NULL,
  sequence integer NOT NULL,
  internal_symbol text NOT NULL REFERENCES config_universe(internal_symbol),
  side text NOT NULL CHECK (side IN ('BUY', 'SELL')),
  notional_value_base numeric,
  units numeric,
  order_type text,
  limit_price numeric,
  reference_price numeric,
  max_slippage_bps integer,
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (run_id, sequence)
);

CREATE INDEX ledger_trades_intended_run_idx ON ledger_trades_intended (run_id);

-- Executed fills (human-entered confirmations)
CREATE TABLE ledger_trades_fills (
  fill_id bigserial PRIMARY KEY,
  ticket_id uuid NOT NULL REFERENCES tickets(ticket_id) ON DELETE CASCADE,
  sequence integer NOT NULL,
  internal_symbol text NOT NULL REFERENCES config_universe(internal_symbol),
  executed_status text NOT NULL CHECK (executed_status IN ('DONE', 'SKIPPED', 'FAILED', 'PARTIAL')),
  executed_value_base numeric,
  units numeric,
  fill_price numeric,
  filled_at timestamptz,
  notes text,
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (ticket_id, sequence)
);

-- Cash movements (deposits/withdrawals/fees adjustments)
CREATE TABLE ledger_cash_movements (
  movement_id bigserial PRIMARY KEY,
  occurred_at timestamptz NOT NULL,
  amount_base numeric NOT NULL,
  base_currency text,
  movement_type text NOT NULL,
  notes text,
  created_at timestamptz NOT NULL DEFAULT now()
);

-- Risk checks: deterministic gate outputs
CREATE TABLE risk_checks (
  check_id bigserial PRIMARY KEY,
  run_id uuid NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
  check_name text NOT NULL,
  passed boolean NOT NULL,
  details jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (run_id, check_name)
);

-- Decision: approved or blocked + reasons
CREATE TABLE decisions (
  decision_id bigserial PRIMARY KEY,
  run_id uuid NOT NULL UNIQUE REFERENCES runs(run_id) ON DELETE CASCADE,
  approved boolean NOT NULL,
  decision_type text NOT NULL CHECK (decision_type IN ('TRADE', 'NO_TRADE')),
  reasons jsonb NOT NULL DEFAULT '[]'::jsonb,
  created_at timestamptz NOT NULL DEFAULT now()
);

-- Confirmation submissions (raw payloads)
CREATE TABLE confirmations (
  confirmation_id bigserial PRIMARY KEY,
  ticket_id uuid NOT NULL REFERENCES tickets(ticket_id) ON DELETE CASCADE,
  submitted_by text,
  submitted_at timestamptz NOT NULL DEFAULT now(),
  payload jsonb NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now()
);

-- Audit log for ops events
CREATE TABLE audit_log (
  audit_id bigserial PRIMARY KEY,
  run_id uuid REFERENCES runs(run_id) ON DELETE SET NULL,
  ticket_id uuid REFERENCES tickets(ticket_id) ON DELETE SET NULL,
  actor text,
  action text NOT NULL,
  object_type text,
  object_id text,
  details jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX audit_log_created_at_idx ON audit_log (created_at);

COMMIT;

