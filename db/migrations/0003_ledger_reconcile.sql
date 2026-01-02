BEGIN;

-- ledger_trades_fills needs side to compute positions/cash deterministically.
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1
    FROM information_schema.columns
    WHERE table_schema = 'public'
      AND table_name = 'ledger_trades_fills'
      AND column_name = 'side'
  ) THEN
    ALTER TABLE ledger_trades_fills
      ADD COLUMN side text CHECK (side IN ('BUY', 'SELL'));
  END IF;
END $$;

-- Reconciliation snapshots (manual eToro capture)
CREATE TABLE IF NOT EXISTS reconciliation_snapshots (
  snapshot_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  snapshot_date date NOT NULL,
  captured_at timestamptz,
  base_currency text NOT NULL DEFAULT 'GBP',
  cash_base numeric NOT NULL,
  notes text,
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS reconciliation_snapshots_date_idx
  ON reconciliation_snapshots (snapshot_date);

CREATE TABLE IF NOT EXISTS reconciliation_snapshot_positions (
  snapshot_id uuid NOT NULL REFERENCES reconciliation_snapshots(snapshot_id) ON DELETE CASCADE,
  internal_symbol text NOT NULL REFERENCES config_universe(internal_symbol),
  units numeric NOT NULL,
  value_base numeric,
  created_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (snapshot_id, internal_symbol)
);

-- Reconciliation results (deterministic gate output)
CREATE TABLE IF NOT EXISTS reconciliation_results (
  result_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  snapshot_id uuid NOT NULL REFERENCES reconciliation_snapshots(snapshot_id) ON DELETE CASCADE,
  evaluated_at timestamptz NOT NULL DEFAULT now(),
  passed boolean NOT NULL,
  cash_ledger numeric,
  cash_snapshot numeric,
  cash_diff numeric,
  cash_diff_abs numeric,
  cash_tolerance_abs numeric,
  max_units_diff numeric,
  units_tolerance_abs numeric,
  unknown_symbols jsonb NOT NULL DEFAULT '[]'::jsonb,
  details jsonb NOT NULL DEFAULT '{}'::jsonb,
  report_path text,
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS reconciliation_results_eval_idx
  ON reconciliation_results (evaluated_at);

-- Ledger views (cash/positions from fills + cash movements)
CREATE OR REPLACE VIEW ledger_positions_current AS
SELECT
  internal_symbol,
  sum(
    CASE
      WHEN executed_status IN ('DONE', 'PARTIAL') AND side = 'BUY' THEN coalesce(units, 0)
      WHEN executed_status IN ('DONE', 'PARTIAL') AND side = 'SELL' THEN -coalesce(units, 0)
      ELSE 0
    END
  ) AS units
FROM ledger_trades_fills
GROUP BY internal_symbol
HAVING sum(
  CASE
    WHEN executed_status IN ('DONE', 'PARTIAL') AND side = 'BUY' THEN coalesce(units, 0)
    WHEN executed_status IN ('DONE', 'PARTIAL') AND side = 'SELL' THEN -coalesce(units, 0)
    ELSE 0
  END
) <> 0;

CREATE OR REPLACE VIEW ledger_cash_current AS
SELECT
  coalesce((SELECT sum(amount_base) FROM ledger_cash_movements), 0)
  + coalesce((
      SELECT sum(
        CASE
          WHEN executed_status IN ('DONE', 'PARTIAL') AND side = 'SELL' THEN coalesce(executed_value_base, 0)
          WHEN executed_status IN ('DONE', 'PARTIAL') AND side = 'BUY' THEN -coalesce(executed_value_base, 0)
          ELSE 0
        END
      )
      FROM ledger_trades_fills
    ), 0) AS cash_base;

COMMIT;

