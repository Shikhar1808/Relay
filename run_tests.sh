#!/bin/sh
# Runs every test in the repo. From the repo root (Git Bash on Windows):
#
#   ./run_tests.sh          unit tests of the five services: fast, needs no running stack
#   ./run_tests.sh all      also starts the compose stack with the mock upstreams
#                           and runs both end-to-end tests (about four minutes), and
#                           fulfilment-mcp's tests against the real Postgres
#
# The end-to-end tests create members and stores in the dev database. With
# SMTP_PASSWORD set they send real emails to made-up @example.com addresses, and
# with GROQ_API_KEY set their chat messages (about fifteen) reach the model.
# To avoid both:   SMTP_PASSWORD= GROQ_API_KEY= ./run_tests.sh all
# Afterwards `docker compose up -d` puts the backend back on .env.
set -eu
cd "$(dirname "$0")"

python_in() {  # the venv's python: Scripts/ on Windows, bin/ elsewhere
    if [ -x "$1/.venv/Scripts/python.exe" ]; then echo "$1/.venv/Scripts/python.exe"; else echo "$1/.venv/bin/python"; fi
}

echo "== backend unit tests"
(cd backend && uv run pytest -q)
echo "== gateway unit tests"
(cd gateway && uv run pytest -q)
echo "== woo-mcp unit tests"
(cd woo-mcp && uv run pytest -q)
echo "== photos-mcp unit tests"
(cd photos-mcp && uv run pytest -q)
echo "== fulfilment-mcp unit tests"
(cd fulfilment-mcp && uv run pytest -q)

[ "${1:-}" = "all" ] || exit 0

echo "== starting the stack with the mock upstreams"
docker compose --profile test up -d --build --wait
# Each end-to-end test logs in and calls the admin API in a burst; start from full rate-limit buckets.
clear_rate_limits() {
    docker compose exec -T redis sh -c 'redis-cli --scan --pattern "ratelimit:*" | xargs -r redis-cli del' >/dev/null
}

echo "== backend end-to-end (smoke test)"
clear_rate_limits
"$(python_in backend)" backend/scripts/smoke_test.py
echo "== gateway end-to-end"
clear_rate_limits
"$(python_in gateway)" gateway/tests/integration_test.py
echo "== fulfilment-mcp against the real database"
# The address the container signs in with, as this machine reaches Postgres (its port is published).
url="$(docker compose exec -T fulfilment-mcp printenv DATABASE_URL | tr -d '\r' | sed 's/@postgres:/@localhost:/')"
(cd fulfilment-mcp && FULFILMENT_TEST_DATABASE_URL="$url" uv run pytest -q tests/test_database.py)
