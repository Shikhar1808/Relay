# Failures

What went wrong while building and testing the upstream registry and the MCP
gateway, what caused it, and what guards against it now. The last section lists
what is still open.

Test files named below: `backend/tests/` and `gateway/tests/test_*.py` are unit
tests; `backend/scripts/smoke_test.py` and `gateway/tests/integration_test.py`
run against the live compose stack. `./run_tests.sh all` runs all of them.

## Fixed: bugs in the code

### 1. Chat and dashboard shared one rate-limit bucket
- **Seen as:** the smoke test failed with `Rate limit exceeded` on a route that
  had barely been called.
- **Cause:** every limiter for a user wrote to the same Redis key, so calls to
  the chat route used up the dashboard's allowance, and the other way round.
- **Fix:** the key now carries the limiter's own numbers:
  `ratelimit:user:<id>:<capacity>:<refill_per_minute>` (`backend/app/core/deps.py`).
- **Guarded by:** `backend/tests/test_rate_limit.py` and a smoke test step.

### 2. A burst of registry changes made the gateway refresh many times
- **Seen as:** the gateway integration test timed out waiting for new tools.
- **Cause:** each change published on `upstreams:changed` queued its own
  refresh. With one hung upstream a refresh takes 5 seconds, so five changes
  took 25 seconds to show up.
- **Fix:** `Catalog.run` drains the queued messages first, so one refresh covers
  the whole burst (`gateway/app/catalog.py`).
- **Guarded by:** `gateway/tests/test_catalog.py`.

### 3. The gateway never recovered if Redis was down when it started
- **Found by:** code review.
- **Cause:** it subscribed to Redis once, before the loop. If that failed, the
  refresh loop died and the gateway served an empty tool list for good.
- **Fix:** the subscription is made inside the loop and retried. With Redis
  down the gateway still refreshes on its 30-second timer.
- **Guarded by:** `gateway/tests/test_catalog.py`.

### 4. Changing an upstream's URL kept its stored credential
- **Found by:** code review.
- **Cause:** `PATCH /upstream-servers/{id}` with only a new URL left the old
  credential in place. An admin could, by mistake or on purpose, point a
  server at another address and have the gateway send the secret there.
- **Fix:** a URL change on a server that has a credential is refused with 400
  unless the credential is sent again (or `""` to remove it).
- **Guarded by:** a smoke test step.

### 5. A URL with a login in it was accepted
- **Found by:** code review.
- **Cause:** `http://user:password@host/mcp` passed validation. The URL column
  is not encrypted and the API returns it, so the password would be stored and
  shown in plain text.
- **Fix:** URLs with a username or password are rejected; credentials go in the
  encrypted credential field only (`backend/app/schemas/upstreams.py`).
- **Guarded by:** `backend/tests/test_schemas.py` and a smoke test step.

### 6. A token sent in the URL was written to the access log
- **Found by:** code review.
- **Cause:** the gateway refuses `?token=...`, but uvicorn's access log printed
  the full URL of the refused request, key included.
- **Fix:** the access log is off on the public port. See "Still open": until
  the audit log exists, requests are not recorded anywhere.

### 7. Wrong claim about forwarded headers
- **Found by:** code review.
- **Cause:** a comment and the README said the gateway trusts the proxy's
  `X-Forwarded-*` headers. uvicorn only trusts them from the addresses in
  `FORWARDED_ALLOW_IPS`, which defaults to localhost. Behind an Ingress the
  gateway would believe it is served over plain http.
- **Fix:** comment and README corrected; the setting is in the README's table.

### 8. Mock upstreams were reported unhealthy
- **Seen as:** `docker compose up --wait` did not finish.
- **Cause:** the mocks run on the gateway image and inherited its health check,
  which asks port 9090. The mocks do not listen there.
- **Fix:** `healthcheck: disable: true` on the mock services. The three mock
  services, which were copies of each other, now share one YAML anchor.

## Not bugs, but they cost time

- **`421 Misdirected Request` from the `mcp` SDK.** A server built with the SDK
  only answers to the Host `localhost` unless told otherwise. The gateway
  needs its public hostname in `ALLOWED_HOSTS`, and every upstream needs its
  service name in `transport_security`. Written down in `gateway/README.md`.
- **Migration 0003 will not downgrade** once a member is in two stores. This is
  on purpose: the old schema cannot hold that data.
- **Two new tests were wrong, not the code.** One did not clear the environment
  before checking that secrets have no default; one used the wrong refill rate
  for the rate limiter. Both were rewritten.

## Checking that the tests can fail

The gateway code was broken on purpose in six places, one at a time. The unit
tests caught all six:

1. no store check when calling a tool
2. no store check when listing tools
3. the Redis startup bug from item 3 put back
4. a Redis failure stopping a refresh
5. accepting any reply from the backend as a valid caller
6. passing an upstream's error detail on to the caller

## Last full run

`./run_tests.sh all`, 11 Oct 2026, exit code 0:

| Suite | Result |
|---|---|
| Backend unit tests | 87 passed |
| Gateway unit tests | 62 passed |
| Backend end-to-end (smoke test) | all checks passed; the emailed-link step is skipped when `SMTP_PASSWORD` is set |
| Gateway end-to-end | 26 checks passed |

## Still open

- **No audit log yet.** With the access log off (item 6), nothing records who
  called which tool. Next phase.
- **No rate limit at the gateway.** A caller can try tokens as fast as the
  backend's resolve route answers. Next phase, with a failed-login backoff.
- **The 401 points to an OAuth server that does not exist.** The SDK adds the
  challenge header and metadata; OAuth for members is deferred.
- **Not tested:** several gateway replicas at once; Postgres going down while
  the gateway runs; the frontend (no tests, and the multi-store pages were not
  checked in a browser).
- **The end-to-end tests are not clean.** Each run leaves two members and two
  stores in the dev database, and with `SMTP_PASSWORD` set it sends 11 real
  emails to made-up `@example.com` addresses.
- **Every request asks the backend who the caller is.** Nothing is cached, so
  revocation is instant, but a backend outage refuses every MCP request.
