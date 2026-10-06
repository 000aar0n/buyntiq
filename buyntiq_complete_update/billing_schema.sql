-- Run once in Supabase SQL Editor. Re-running preserves existing users/data.
-- account_schema.sql must also have been run for saved watchlists/searches.
BEGIN;
CREATE SCHEMA IF NOT EXISTS buyntiq_private;
REVOKE ALL ON SCHEMA buyntiq_private FROM PUBLIC;

CREATE TABLE IF NOT EXISTS buyntiq_private.billing_customers (
    owner_key text NOT NULL CHECK (owner_key ~ '^[0-9a-f]{64}$'),
    livemode boolean NOT NULL,
    stripe_customer_id text NOT NULL UNIQUE CHECK (stripe_customer_id LIKE 'cus\_%' ESCAPE '\'),
    checkout_session_id text,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (owner_key, livemode)
);
CREATE TABLE IF NOT EXISTS buyntiq_private.usage_daily (
    owner_key text NOT NULL CHECK (owner_key ~ '^[0-9a-f]{64}$'),
    usage_day date NOT NULL,
    feature text NOT NULL CHECK (feature IN ('research','build','review','forecasts')),
    used integer NOT NULL DEFAULT 0 CHECK (used >= 0),
    PRIMARY KEY (owner_key, usage_day, feature)
);

ALTER TABLE buyntiq_private.billing_customers ENABLE ROW LEVEL SECURITY;
ALTER TABLE buyntiq_private.usage_daily ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON buyntiq_private.billing_customers, buyntiq_private.usage_daily FROM PUBLIC;
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='anon') THEN
        REVOKE ALL ON SCHEMA buyntiq_private FROM anon;
        REVOKE ALL ON buyntiq_private.billing_customers, buyntiq_private.usage_daily FROM anon;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='authenticated') THEN
        REVOKE ALL ON SCHEMA buyntiq_private FROM authenticated;
        REVOKE ALL ON buyntiq_private.billing_customers, buyntiq_private.usage_daily FROM authenticated;
    END IF;
END;
$$;
COMMIT;
-- No browser/client policies. Only the private Streamlit server connection
-- may modify customer bindings or quotas. No client role can grant itself Pro.
-- Stripe remains the authority for subscription/payment state.
