BEGIN;

-- Secondary sink delivery receipts for alerts
CREATE TABLE IF NOT EXISTS alert_deliveries (
  delivery_id bigserial PRIMARY KEY,
  alert_id text NOT NULL REFERENCES alerts(alert_id) ON DELETE CASCADE,
  created_at timestamptz NOT NULL DEFAULT now(),
  sink text NOT NULL,
  dryrun boolean NOT NULL,
  status text NOT NULL CHECK (status IN ('SKIPPED', 'WOULD_SEND', 'SENT', 'FAILED')),
  error_text text,
  receipt_path text NOT NULL,
  UNIQUE (alert_id, sink)
);

CREATE INDEX IF NOT EXISTS alert_deliveries_created_at_idx ON alert_deliveries (created_at DESC);

COMMIT;

