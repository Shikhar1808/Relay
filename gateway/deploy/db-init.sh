#!/bin/sh
# Prepares the two databases the gateway uses. Safe to run again (it also
# resets the role's password to the current GATEWAY_DB_PASSWORD). Run it after
# the backend's migrations, because upstream_servers has to exist to be granted.
#
#   Platform DB: role `gateway` can read the upstream registry and nothing else.
#   mcp_logs:    the audit log. `gateway` can add rows and read them; a trigger
#                refuses UPDATE, DELETE and TRUNCATE for everyone, the owner included.
#
# Needs: PGHOST, PGUSER, PGPASSWORD, PGDATABASE (an admin of the Platform DB)
# and GATEWAY_DB_PASSWORD.
set -eu

psql -v ON_ERROR_STOP=1 -v gateway_password="$GATEWAY_DB_PASSWORD" <<'SQL'
SELECT 'CREATE ROLE gateway LOGIN' WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'gateway') \gexec
ALTER ROLE gateway PASSWORD :'gateway_password';
SELECT format('GRANT CONNECT ON DATABASE %I TO gateway', current_database()) \gexec
GRANT USAGE ON SCHEMA public TO gateway;
GRANT SELECT ON upstream_servers TO gateway;

SELECT 'CREATE DATABASE mcp_logs' WHERE NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = 'mcp_logs') \gexec
\connect mcp_logs

-- The columns, in this order, are what gateway/app/audit.py hashes into the chain.
CREATE TABLE IF NOT EXISTS audit_log (
    seq         bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ts          timestamptz NOT NULL,
    severity    text NOT NULL CHECK (severity IN ('DEBUG', 'INFO', 'NOTICE', 'WARNING', 'ERROR', 'CRITICAL')),
    category    text NOT NULL,  -- tool_call, auth, admin, registry, upstream_health, system
    action      text NOT NULL,
    outcome     text NOT NULL,
    user_id     text,
    upstream    text,
    tool        text,
    duration_ms integer,
    client_ip   text,
    detail      text,           -- JSON; how much is in it depends on AUDIT_DETAIL
    prev_hash   text NOT NULL,
    hash        text NOT NULL
);

CREATE OR REPLACE FUNCTION audit_log_is_append_only() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'audit_log is append-only';
END $$;
CREATE OR REPLACE TRIGGER audit_log_no_change BEFORE UPDATE OR DELETE ON audit_log
    FOR EACH ROW EXECUTE FUNCTION audit_log_is_append_only();
CREATE OR REPLACE TRIGGER audit_log_no_truncate BEFORE TRUNCATE ON audit_log
    FOR EACH STATEMENT EXECUTE FUNCTION audit_log_is_append_only();

GRANT CONNECT ON DATABASE mcp_logs TO gateway;
GRANT USAGE ON SCHEMA public TO gateway;
GRANT INSERT, SELECT ON audit_log TO gateway;
SQL
echo "db-init: role 'gateway' can read upstream_servers and append to mcp_logs.audit_log"
