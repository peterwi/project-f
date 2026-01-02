BEGIN;

CREATE TABLE IF NOT EXISTS data_quality_reports (
  report_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  run_id uuid REFERENCES runs(run_id) ON DELETE SET NULL,
  asof_date date NOT NULL,
  expected_date date,
  passed boolean NOT NULL,
  coverage_pct numeric,
  enabled_symbols_count integer,
  benchmarks_count integer,
  details jsonb NOT NULL DEFAULT '{}'::jsonb,
  report_path text,
  generated_at timestamptz NOT NULL DEFAULT now(),
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS data_quality_reports_asof_idx
  ON data_quality_reports (asof_date);

CREATE INDEX IF NOT EXISTS data_quality_reports_generated_idx
  ON data_quality_reports (generated_at);

COMMIT;

