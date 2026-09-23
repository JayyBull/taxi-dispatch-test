CREATE TABLE IF NOT EXISTS schema_migrations (
  version text PRIMARY KEY,
  applied_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS drivers (
  callsign text PRIMARY KEY,
  name text NOT NULL,
  pin_salt text NOT NULL,
  pin_hash text NOT NULL,
  enabled boolean NOT NULL DEFAULT true,
  status text NOT NULL DEFAULT 'Offline' CHECK (status IN ('Available','Busy','Offline')),
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS driver_sessions (
  token_hash text PRIMARY KEY,
  driver_callsign text NOT NULL REFERENCES drivers(callsign) ON DELETE CASCADE,
  created_at timestamptz NOT NULL DEFAULT now(),
  last_seen_at timestamptz NOT NULL DEFAULT now(),
  expires_at timestamptz NOT NULL
);
CREATE INDEX IF NOT EXISTS driver_sessions_driver_idx ON driver_sessions(driver_callsign);
CREATE INDEX IF NOT EXISTS driver_sessions_expiry_idx ON driver_sessions(expires_at);

CREATE TABLE IF NOT EXISTS bookings (
  booking_ref text PRIMARY KEY,
  driver_callsign text REFERENCES drivers(callsign) ON DELETE SET NULL,
  driver_name text,
  pickup text NOT NULL DEFAULT '',
  destination text NOT NULL DEFAULT '',
  pickup_time text,
  fare numeric(12,2),
  status text NOT NULL DEFAULT 'Unassigned',
  payload jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS bookings_driver_status_idx ON bookings(driver_callsign,status);

CREATE TABLE IF NOT EXISTS driver_offers (
  id bigserial PRIMARY KEY,
  booking_ref text NOT NULL,
  driver_callsign text NOT NULL REFERENCES drivers(callsign) ON DELETE CASCADE,
  status text NOT NULL DEFAULT 'offered' CHECK (status IN ('offered','accepted','declined','timed_out','closed')),
  timeout_seconds integer NOT NULL DEFAULT 30 CHECK (timeout_seconds >= 5),
  payload jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at timestamptz NOT NULL DEFAULT now(),
  expires_at timestamptz NOT NULL,
  responded_at timestamptz,
  UNIQUE (booking_ref, driver_callsign, created_at)
);
CREATE UNIQUE INDEX IF NOT EXISTS one_active_offer_per_booking ON driver_offers(booking_ref) WHERE status='offered';
CREATE INDEX IF NOT EXISTS driver_offers_driver_idx ON driver_offers(driver_callsign,status,expires_at);

CREATE TABLE IF NOT EXISTS job_status_history (
  id bigserial PRIMARY KEY,
  booking_ref text NOT NULL,
  driver_callsign text REFERENCES drivers(callsign) ON DELETE SET NULL,
  from_status text,
  to_status text NOT NULL,
  source text NOT NULL DEFAULT 'driver',
  details jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS job_status_history_booking_idx ON job_status_history(booking_ref,created_at);
