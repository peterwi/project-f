BEGIN;

-- Alerts: deterministic operator notifications (file sink + DB index)
CREATE TABLE IF NOT EXISTS alerts (
  alert_id text PRIMARY KEY,
  created_at timestamptz NOT NULL DEFAULT now(),
  alert_type text NOT NULL,
  severity text NOT NULL CHECK (severity IN ('INFO', 'WARN', 'ERROR')),
  run_id uuid REFERENCES runs(run_id) ON DELETE SET NULL,
  ticket_id uuid REFERENCES tickets(ticket_id) ON DELETE SET NULL,
  summary text NOT NULL,
  details jsonb NOT NULL DEFAULT '{}'::jsonb,
  artifact_path text NOT NULL
);

CREATE INDEX IF NOT EXISTS alerts_created_at_idx ON alerts (created_at DESC);
CREATE INDEX IF NOT EXISTS alerts_type_created_at_idx ON alerts (alert_type, created_at DESC);

COMMIT;

