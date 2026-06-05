-- Voice + owner settings integration (Reservation Lab reference schema)

ALTER TABLE restaurants
  ADD COLUMN IF NOT EXISTS custom_greeting text,
  ADD COLUMN IF NOT EXISTS twilio_phone text,
  ADD COLUMN IF NOT EXISTS agent_style_notes text,
  ADD COLUMN IF NOT EXISTS voice_settings jsonb DEFAULT '{}'::jsonb,
  ADD COLUMN IF NOT EXISTS min_party integer DEFAULT 1,
  ADD COLUMN IF NOT EXISTS max_party integer DEFAULT 20,
  ADD COLUMN IF NOT EXISTS large_group_threshold integer DEFAULT 8,
  ADD COLUMN IF NOT EXISTS max_advance_days integer DEFAULT 60,
  ADD COLUMN IF NOT EXISTS min_lead_hours integer DEFAULT 2;

ALTER TABLE restaurant_reservations
  ADD COLUMN IF NOT EXISTS pending_expires_at timestamptz;

CREATE INDEX IF NOT EXISTS idx_reservations_pending_expires
  ON restaurant_reservations (pending_expires_at)
  WHERE status = 'pending';

CREATE TABLE IF NOT EXISTS voice_call_logs (
  call_log_id text PRIMARY KEY,
  restaurant_id text REFERENCES restaurants(restaurant_id),
  reservation_id text REFERENCES restaurant_reservations(reservation_id),
  twilio_call_sid text,
  caller_phone text,
  started_at timestamptz DEFAULT now(),
  duration_seconds integer DEFAULT 0,
  outcome text DEFAULT 'info_provided',
  call_status text DEFAULT 'unknown',
  transcript text,
  summary text,
  audio_storage_path text,
  created_at timestamptz DEFAULT now(),
  updated_at timestamptz DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_voice_call_logs_restaurant_started
  ON voice_call_logs (restaurant_id, started_at DESC);

CREATE INDEX IF NOT EXISTS idx_voice_call_logs_twilio_sid
  ON voice_call_logs (twilio_call_sid);
