# MCP Gateway

One authenticated `/mcp` endpoint in front of every registered upstream MCP
server. A separate service: it imports nothing from `backend/` and shares only
configuration with it.

```
MCP client / chat agent ──► gateway :8080/mcp ──► upstream MCP servers
                               │   ▲
        who is this token? ────┘   └──── which servers exist? (read-only)
        backend /internal/auth/resolve   Platform DB: upstream_servers
```

## What it does

- **Lists tools** from every enabled upstream as `slug__tool`, so two servers
  can both offer `list_orders` and a client can tell which store a tool acts on.
- **Routes a call** to the right upstream with that upstream's stored
  credential. The caller's own token is never forwarded.
- **Scopes by store.** An upstream with a `store_id` is only for callers who
  have that store; one without is shared. Applied when listing and again when
  calling, because a client can call a name it was never shown.
- **Survives bad upstreams.** A background loop re-reads the registry and every
  upstream's tools every 30 s, and at once when the backend reports a change.
  Requests read the finished snapshot, so a hung or dead upstream cannot slow
  `tools/list`. Calls time out after 30 s with a clear error.
- **Publishes health**: `upstream:<id>:status` in Redis is `connected` or
  `unreachable`, for the dashboard.

## Who gets in

Nobody without a bearer token. For each request the gateway asks the backend's
`/internal/auth/resolve` who the token belongs to and which stores they have.
Nothing is cached, so a revoked key or a removed store assignment is refused on
the very next request. If the backend cannot be reached, the request is refused.

- No token, an unknown token, or a token in the URL: `401`.
- A `Host` header that is not in `ALLOWED_HOSTS`: `421`. Add the Ingress
  hostname there, or every request through the Ingress is refused.
- The health routes are on a second port (9090) that is not published.
- No access log on the public port: it would print full URLs, and with them
  any key a misconfigured client sends as `?token=...`.

If Redis is down, the gateway keeps serving: it refreshes on its 30 s timer
and picks the subscription up again when Redis is back.

OAuth for team members is not built yet; `app/auth.py` says where it goes.

## Files

```
app/
  config.py     Settings from env vars
  registry.py   Reads upstream_servers (read-only role) and decrypts credentials
  upstream.py   One MCP call to one upstream, with timeouts
  catalog.py    The refresh loop and the in-memory snapshot requests read
  auth.py       Token -> caller, by asking the backend
  tools.py      tools/list and tools/call: naming and the store rule
  main.py       The public app (/mcp) and the private app (health)
deploy/db-init.sh   Creates the gateway's read-only database role
tests/
  test_*.py            Unit tests (pytest); the database, Redis, backend and upstreams are faked
  integration_test.py  End-to-end test against the live compose stack
  mock_upstream.py     The fake upstream server that test uses
```

## Settings

| Variable | Meaning |
|---|---|
| `PLATFORM_DATABASE_URL` | Read-only connection to the Platform DB (`postgresql://gateway:...`) |
| `REDIS_URL` | For change notices from the backend and for upstream status |
| `RESOLVE_URL` | The backend's `/internal/auth/resolve` |
| `INTERNAL_SERVICE_TOKEN` | Shared secret that route demands |
| `GATEWAY_ENCRYPTION_KEY` | Same key the backend encrypts credentials with |
| `PUBLIC_BASE_URL` | The gateway's address as clients reach it |
| `ALLOWED_HOSTS` | JSON list of Host headers it answers to, e.g. `["mcp.example.com"]` |
| `REFRESH_SECONDS`, `LIST_TIMEOUT_SECONDS`, `CALL_TIMEOUT_SECONDS` | 30, 5, 30 by default |
| `FORWARDED_ALLOW_IPS` | Read by uvicorn. Behind a TLS-terminating Ingress, set it to the proxy's address (or `*` if only the proxy can reach the pod); otherwise the gateway believes it is served over plain http |

## Run and test

From the repo root, with `.env` filled in (see `.env.example`):

```bash
docker compose up -d --build          # gateway on http://localhost:8080/mcp
```

Connect a client with a member's API key (issued in the dashboard):

```bash
npx @modelcontextprotocol/inspector --cli http://localhost:8080/mcp \
  --transport http --method tools/list --header "Authorization: Bearer umk_..."
```

Tests. `./run_tests.sh` in the repo root runs the unit tests of both services,
and `./run_tests.sh all` adds the end-to-end tests. By hand:

```bash
cd gateway && uv sync && uv run pytest                       # unit tests, about a second
cd .. && docker compose --profile test up -d --build         # adds three mock upstreams
gateway/.venv/Scripts/python gateway/tests/integration_test.py   # .venv/bin/python on Linux/macOS
```

The unit tests cover the naming and store rules, both handlers, the refresh
loop (including Redis or the database being down), token resolution and
credential decryption.

The end-to-end test (about two minutes) registers upstreams through the
backend, then checks: authentication; store scoping; a member in two stores;
the admin's view; a name collision; a hung, a dead and a wrongly-credentialed
upstream; the call timeout; 30 concurrent calls; disabling, re-scoping and
removing an upstream; an upstream that is killed and comes back; and revoking
a key and deactivating a member.

## Writing an upstream server

An upstream built with the `mcp` SDK only answers to `localhost` by default.
Inside a cluster it is reached by its service name, so it must allow that name
in `transport_security` (see `tests/mock_upstream.py`), or the gateway will see
it as unreachable.

## Not built yet

Audit log, per-user rate limits at the gateway, OAuth for members, the Helm
chart. See `TIMELINE.md`.
