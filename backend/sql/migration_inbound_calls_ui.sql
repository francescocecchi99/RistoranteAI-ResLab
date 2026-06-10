-- Inbound calls owner UI: confidence + optional denormalized booking hints

ALTER TABLE voice_call_logs
  ADD COLUMN IF NOT EXISTS confidence_level smallint,
  ADD COLUMN IF NOT EXISTS high_chairs_requested integer DEFAULT 0;

CREATE UNIQUE INDEX IF NOT EXISTS idx_voice_call_logs_twilio_sid_unique
  ON voice_call_logs (twilio_call_sid)
  WHERE twilio_call_sid IS NOT NULL;

COMMENT ON COLUMN voice_call_logs.confidence_level IS
  '0-100: how likely the call completed a correct booking (owner review).';
COMMENT ON COLUMN voice_call_logs.audio_storage_path IS
  'Relative path or object key (not raw audio bytes). Use Supabase Storage / S3 / local disk.';
