BEGIN;

-- Optional but useful metadata for downstream reporting and multi-currency setups.
ALTER TABLE market_prices_eod
  ADD COLUMN IF NOT EXISTS currency text;

-- Corporate actions: dividends
CREATE TABLE IF NOT EXISTS market_actions_dividends (
  dividend_id bigserial PRIMARY KEY,
  internal_symbol text NOT NULL REFERENCES config_universe(internal_symbol),
  ex_date date NOT NULL,
  pay_date date,
  amount numeric NOT NULL,
  currency text,
  source text NOT NULL DEFAULT 'unknown',
  quality_flags jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (internal_symbol, ex_date, amount, source)
);

CREATE INDEX IF NOT EXISTS market_actions_dividends_symbol_ex_date_idx
  ON market_actions_dividends (internal_symbol, ex_date);

CREATE INDEX IF NOT EXISTS market_actions_dividends_ex_date_idx
  ON market_actions_dividends (ex_date);

-- Corporate actions: splits
CREATE TABLE IF NOT EXISTS market_actions_splits (
  split_id bigserial PRIMARY KEY,
  internal_symbol text NOT NULL REFERENCES config_universe(internal_symbol),
  ex_date date NOT NULL,
  ratio numeric NOT NULL,
  source text NOT NULL DEFAULT 'unknown',
  quality_flags jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (internal_symbol, ex_date, ratio, source)
);

CREATE INDEX IF NOT EXISTS market_actions_splits_symbol_ex_date_idx
  ON market_actions_splits (internal_symbol, ex_date);

CREATE INDEX IF NOT EXISTS market_actions_splits_ex_date_idx
  ON market_actions_splits (ex_date);

COMMIT;

