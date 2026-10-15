#!/bin/sh
# Prepares the databases next to the platform's own: the two the gateway uses,
# and fulfilment-mcp's. Safe to run again (it also resets each role's password
# to the current value). Run it after the backend's migrations, because
# upstream_servers has to exist to be granted.
#
#   Platform DB: role `gateway` can read the upstream registry and nothing else.
#   mcp_logs:    the audit log. `gateway` can add rows and read them; a trigger
#                refuses UPDATE, DELETE and TRUNCATE for everyone, the owner included.
#   fulfilment:  fulfilment-mcp's records. Role `fulfilment` can read, add and
#                change rows of its one table: no DELETE, no DDL.
#   Each role can connect only to the databases granted to it here.
#
# Needs: PGHOST, PGUSER, PGPASSWORD, PGDATABASE (an admin of the Platform DB),
# GATEWAY_DB_PASSWORD and FULFILMENT_DB_PASSWORD.
set -eu

psql -v ON_ERROR_STOP=1 -v gateway_password="$GATEWAY_DB_PASSWORD" \
    -v fulfilment_password="$FULFILMENT_DB_PASSWORD" -v platform_db="$PGDATABASE" <<'SQL'
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

-- fulfilment-mcp keeps its records here, not in its pod, so the pod can be killed or run twice.
SELECT 'CREATE ROLE fulfilment LOGIN' WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'fulfilment') \gexec
ALTER ROLE fulfilment PASSWORD :'fulfilment_password';
SELECT 'CREATE DATABASE fulfilment' WHERE NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = 'fulfilment') \gexec
\connect fulfilment

-- One store's orders (one fulfilment-mcp per store): the order id is the store's own.
CREATE TABLE IF NOT EXISTS fulfilments (
    order_id        bigint PRIMARY KEY,
    stage           text NOT NULL DEFAULT 'packing' CHECK (stage IN ('packing', 'shipped', 'delivered')),
    tracking_number text NOT NULL UNIQUE,
    date_started    timestamptz NOT NULL DEFAULT now(),
    date_updated    timestamptz NOT NULL DEFAULT now()
);

GRANT CONNECT ON DATABASE fulfilment TO fulfilment;
GRANT USAGE ON SCHEMA public TO fulfilment;
GRANT SELECT, INSERT, UPDATE ON fulfilments TO fulfilment;

-- Postgres lets every role connect to every database unless told otherwise. From here on only the
-- grants above count: fulfilment cannot open the platform or the audit log, the gateway not fulfilment.
-- The backend and this script sign in as the owner, which needs no grant.
REVOKE CONNECT ON DATABASE :"platform_db", mcp_logs, fulfilment FROM PUBLIC;
SQL
echo "db-init: role 'gateway' can read upstream_servers and append to mcp_logs.audit_log;" \
    "role 'fulfilment' can read, add and change fulfilment.fulfilments"
