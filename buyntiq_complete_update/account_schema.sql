-- Run once in the Supabase SQL Editor as the database owner.
-- Private schema: do not add buyntiq_private to the exposed Data API schemas.
CREATE SCHEMA IF NOT EXISTS buyntiq_private;
REVOKE ALL ON SCHEMA buyntiq_private FROM PUBLIC;

CREATE TABLE IF NOT EXISTS buyntiq_private.preferences (
    owner_key text PRIMARY KEY CHECK (owner_key ~ '^[0-9a-f]{64}$'),
    watchlist jsonb NOT NULL DEFAULT '[]'::jsonb
        CHECK (jsonb_typeof(watchlist) = 'array' AND jsonb_array_length(watchlist) <= 30),
    recent_searches jsonb NOT NULL DEFAULT '[]'::jsonb
        CHECK (jsonb_typeof(recent_searches) = 'array' AND jsonb_array_length(recent_searches) <= 12),
    updated_at timestamptz NOT NULL DEFAULT now()
);
ALTER TABLE buyntiq_private.preferences ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON buyntiq_private.preferences FROM PUBLIC;

-- No browser/client policies: the Streamlit server uses its private database
-- owner connection. Every application query is scoped to the verified Google
-- subject's hashed key. Never send this database connection to the browser.
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN
        REVOKE ALL ON SCHEMA buyntiq_private FROM anon;
        REVOKE ALL ON buyntiq_private.preferences FROM anon;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN
        REVOKE ALL ON SCHEMA buyntiq_private FROM authenticated;
        REVOKE ALL ON buyntiq_private.preferences FROM authenticated;
    END IF;
END;
$$;
