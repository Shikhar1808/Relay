#!/bin/sh
# Creates the gateway's database role in the Platform DB: it can read the
# upstream registry and nothing else. Safe to run again (it also resets the
# password to the current GATEWAY_DB_PASSWORD). Run it after the backend's
# migrations, because the table has to exist to be granted.
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
SQL
echo "db-init: role 'gateway' can read upstream_servers"
