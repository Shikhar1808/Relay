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
- **Fix:** the access log is off on the public port. The audit log records
  tool calls and refused logins instead, never the token.

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

- **Team, key and store changes have no hash chain.** `admin_events` is
  append-only by trigger, but nothing like the gateway's chain shows whether
  the owner switched the trigger off and edited a row. Sign-ins and password
  changes are not recorded at all.
- **Registry changes now need the gateway.** If the gateway or its log database
  is down, an admin cannot add, edit, disable or remove an upstream (503). That
  is on purpose, but it also means an upstream cannot be switched off then.
- **An admin event can be wrong in one narrow case.** The event is stored before
  the commit. If the commit fails, a second `failed` event corrects it. If the
  backend process dies in between, there is no correction; the only sign is
  that no `registry` event follows.
- **The gateway's registry watch has blind spots.** It does not see name-only
  edits, cannot tell disabled from deleted, merges edits made within one
  refresh, misses changes made while it was not running, and writes one row per
  replica. See "Future expansion" in `TIMELINE.md`.
- **The audit chain does not show rows cut off its end.** Only comparing with
  the copy in the container logs does, and nothing ships those logs anywhere yet.
- **One call can go unrecorded.** If the log database dies while a call is in
  flight, that call is only in the container log (`NOT STORED`). Calls after it
  are refused until the database is back.
- **The block on refused logins is by address.** Behind a proxy without
  `FORWARDED_ALLOW_IPS`, all clients share one address, so one bad client gets
  everyone a 429 for a minute.
- **The refused-login counter is still a fixed minute.** A burst across the
  boundary can reach twice the limit. The per-user call limit is a token
  bucket now; with a full bucket a user can make 120 calls at once and then 2
  a second, so up to about 240 in the first minute and 120 a minute after that.
- **The 401 points to an OAuth server that does not exist.** The SDK adds the
  challenge header and metadata; OAuth for members is deferred.
- **Not tested:** several gateway replicas at once; Postgres going down while
  the gateway runs; the frontend has no automated tests (the Audit page was
  checked by hand in a headless browser, the multi-store pages were not).
- **The end-to-end tests are not clean.** Each run leaves two members and two
  stores in the dev database, and with `SMTP_PASSWORD` set it sends 11 real
  emails to made-up `@example.com` addresses.
- **Every request asks the backend who the caller is.** Nothing is cached, so
  revocation is instant, but a backend outage refuses every MCP request.

## Audit log phase (11 Oct 2026)

Two test failures while adding the audit log, neither a bug in the gateway:

- **A new check looked for two fields in the wrong order.** The stored `detail`
  JSON has sorted keys; the check expected `token_type` before `args_hash`.
- **An existing check was a race that had been passing by luck.** With the hung
  mock registered, a registry reload takes 5 seconds, and a change that lands
  behind a running reload shows up after about 10. The test waited exactly 10.
  It now waits up to 20.

Last run after this phase: 96 gateway unit tests passed; the gateway end-to-end
test passed with 37 checks. The backend tests were not re-run (no backend change).

After adding the audit read and write routes and the per-user call limit:
`./run_tests.sh all` passed. 95 backend and 117 gateway unit tests, the smoke
test, and the gateway end-to-end test with 40 checks. No test failed on the way.

After the `failed` correction for admin events and the gateway's registry watch:
`./run_tests.sh all` passed. 99 backend and 121 gateway unit tests, the smoke
test, and the gateway end-to-end test with 42 checks. No test failed on the way.

After the Audit page, `admin_events`, the chain check and the token bucket:
`./run_tests.sh all` passed. 101 backend and 129 gateway unit tests, the smoke
test, and the gateway end-to-end test with 43 checks. One unit test failed on
the way, as expected: it lists the private port's routes and a route was added.
The Audit page was opened in headless Chrome at 1440 and 390 pixels wide, with
no console errors. That is where the level filter was found sitting alone on
its own row; it was moved beside the view's description.

After moving the session to a cookie and calling the rate-limit script by id:
`./run_tests.sh all` passed. 126 backend and 130 gateway unit tests, the smoke
test, and the gateway end-to-end test with 43 checks. No test failed on the way.
The session was also checked in headless Chrome through the real sign-in form:
the cookie is HttpOnly and SameSite=Strict, no script on the page can see the
token, a reload stays signed in, and sign out removes the cookie.

After making sign out revoke the session and adding `GET /auth/session`:
`./run_tests.sh all` passed. 131 backend and 130 gateway unit tests, the smoke
test, and the gateway end-to-end test with 43 checks. No test failed on the way.
In headless Chrome a signed-out page load now causes no 401 and prints nothing
in the console, and the token that was in the cookie is refused after sign out.
