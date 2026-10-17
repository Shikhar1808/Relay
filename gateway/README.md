# MCP Gateway

One authenticated `/mcp` endpoint in front of every registered upstream MCP
server. A separate service: it imports nothing from `backend/` and shares only
configuration with it.

```
MCP client / chat agent ──► gateway :8080/mcp ──► upstream MCP servers
                               │   ▲
        who is this token? ────┘   └──── which servers exist? (read-only)
        backend /internal/auth/resolve   Platform DB: upstream_servers
                               │
                               └──► mcp_logs: audit_log (append-only)
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
  `unreachable`, and `upstream:<id>:tools` a JSON list of its tool names, for
  the dashboard's Servers page.
- **Records everything that matters** in an append-only audit log (below).

## Who gets in

Nobody without a bearer token. For each request the gateway asks the backend's
`/internal/auth/resolve` who the token belongs to and which stores they have.
Nothing is cached, so a revoked key or a removed store assignment is refused on
the very next request. If the backend cannot be reached, the request is refused.

- No token, an unknown token, or a token in the URL: `401`.
- A `Host` header that is not in `ALLOWED_HOSTS`: `421`. Add the Ingress
  hostname there, or every request through the Ingress is refused.
- The health routes are on a second port (9090) that is not published. The
  audit log's routes for the backend are there too, behind the service token.
- No access log on the public port: it would print full URLs, and with them
  any key a misconfigured client sends as `?token=...`. The audit log records
  refused logins instead, without the token.
- An address with 20 refused logins in a minute (`AUTH_FAILURES_PER_MINUTE`)
  gets `429` for the rest of that minute, before its token is looked at. The
  block is by address: behind a proxy, set `FORWARDED_ALLOW_IPS` (below), or
  every client shares the proxy's address and one bad client blocks them all.
- A user gets 120 tool calls a minute (`CALLS_PER_MINUTE`), as a token bucket:
  up to 120 at once, and 2 a second come back. A call with no token left is
  refused with a clear error and spends nothing, so the user can call again a
  moment later, not a minute later.

If Redis is down, the gateway keeps serving: it refreshes on its 30 s timer
and picks the subscription up again when Redis is back.

OAuth for team members is not built yet; `app/auth.py` says where it goes.

## Audit log

A separate database, `mcp_logs`, with one table, `audit_log`
(`deploy/db-init.sh` creates both). One row per event:

| Category | When | Severity |
|---|---|---|
| `tool_call` | every `tools/call` | `ok` INFO · `error` (the tool failed) and `denied` (unknown, or another store's) WARNING · `timeout` and `unreachable` ERROR · `rate_limited` WARNING, once a minute per user |
| `admin` | an admin adds, edits or removes an upstream server | NOTICE · WARNING for a change that was recorded and then failed |
| `registry` | the gateway itself sees a server added, removed or changed | NOTICE |
| `auth` | every refused login | NOTICE · WARNING for the one that trips the block |
| `upstream_health` | an upstream goes down or comes back (once, not per check) | ERROR down · NOTICE back |
| `system` | the gateway starts | INFO |

Each row has the time, user, upstream, tool, duration, caller's address and a
`detail` JSON. `AUDIT_DETAIL` sets how much of a tool call goes in there:

| Level | Kept |
|---|---|
| `minimal` | a hash of the arguments |
| `standard` (default) | also the arguments, with fields named like `password`, `token`, `api_key`, `secret` redacted, and the error text of a failed call |
| `full` | the arguments as sent, and the result text |

Texts are cut at 2000 characters. `tools/list` is not recorded.

A call made by the dashboard's chat agent also carries `chat_id` (the chat)
and `chat_turn` (the message in it that led to the call), from the
`X-Chat-Id` and `X-Chat-Turn` headers the backend sends. They are kept only
when the caller's token is a chat token and each is a well-formed UUID: an
API-key caller cannot plant them. A client such as Claude Code has no such
ids; its conversation never reaches the gateway.

**Append-only.** The gateway's role can INSERT and SELECT, nothing else. A
trigger refuses UPDATE, DELETE and TRUNCATE for every role, the owner included.

**Tamper-evident.** Each row holds `HMAC-SHA256(AUDIT_HMAC_KEY, previous row's
hash | this row)`. Change, remove or reorder a row and the chain breaks there:

```bash
docker compose exec gateway python -m app.audit
# OK: 412 events, chain intact, last hash 9f2c...
# BROKEN at seq 87: that row was changed, or a row before it was removed
```

Every event is also written to the container log as one JSON line with its
`seq` and `hash` (without the detail). Ship those logs somewhere else and you
have a second copy to compare `last hash` with; that is how rows cut off the
*end* of the table are noticed, which the chain alone cannot show.

**If the log cannot be written,** the event goes to the container log as
`NOT STORED`, and tool calls are refused until the database answers again. So
at most the one call in flight when the database went away is missing from
the table.

**Admin changes.** The registry is edited in the backend, but only the gateway
can sign a row. So the backend reports each change to `POST /internal/audit`
on the private port *before* it commits: who, from which address, which server,
and what was sent (a credential shows only as `set` or `none`). If the event is
not stored, the backend answers 503 and makes no change. The gateway fixes the
category itself, so nothing sent to that route can pass for a tool call.

Recording first means the log can say a change was made when the commit after
it failed. A row cannot be taken back, so the backend then adds a second event
for the same action with outcome `failed`.

**The registry, as the gateway sees it.** Each time the gateway re-reads the
registry it compares it with the last read and records what changed
(`upstream.added`, `upstream.removed`, `upstream.changed` with `url`, `store`,
`credential` or `recreated`; never the credential itself). This does not rely
on the backend, so the two kinds of event check each other:

- an `admin` event with no `registry` event after it: the change did not take effect
- a `registry` event with no `admin` event before it: someone changed the
  database directly, without the API

Match them by server and time; the `registry` event follows within one refresh
(30 s at most, usually under a second). What it cannot see: a name-only edit,
the difference between disabled and deleted (both are `removed`), separate
edits made within one refresh (they show as one), and anything that changed
while the gateway was not running. With several replicas each one writes its own row.

**Reading.** `GET /internal/audit` on the private port returns rows newest
first (`limit` up to 200, `before=<seq>` for the next page, optional
`category` and `severity`). `GET /internal/audit/verify` walks the whole chain
and says whether it is intact, like `python -m app.audit`; the dashboard's
Audit page has a button for it. The backend's `GET /audit-log` (admin only) calls
it and adds each user's email. Both routes need the service token.

**Limits.** Someone who has the HMAC key *and* can switch the trigger off can
rebuild the chain; the copy in the container logs is the defence. Nothing is
ever pruned yet. Team, API key and store changes are not in this log: the
backend keeps them in its own append-only table (`admin_events`), which the
same dashboard page shows.

## Files

```
app/
  config.py     Settings from env vars
  registry.py   Reads upstream_servers (read-only role) and decrypts credentials
  upstream.py   One MCP call to one upstream, with timeouts
  catalog.py    The refresh loop and the in-memory snapshot requests read
  auth.py       Token -> caller, by asking the backend
  tools.py      tools/list and tools/call: naming and the store rule
  audit.py      The audit log: writing, the hash chain, `python -m app.audit`
  guard.py      Refused logins: recorded, counted, blocked after too many
  limits.py     Rate limits in Redis: a token bucket for tool calls, a counter for refused logins
  main.py       The public app (/mcp) and the private app (health, audit log for the backend)
deploy/db-init.sh   Creates the gateway's database role and the audit log database, and fulfilment-mcp's database and role
tests/
  test_*.py            Unit tests (pytest); the database, Redis, backend and upstreams are faked
  integration_test.py  End-to-end test against the live compose stack
  mock_upstream.py     The fake upstream server that test uses
```

## Settings

| Variable | Meaning |
|---|---|
| `PLATFORM_DATABASE_URL` | Read-only connection to the Platform DB (`postgresql://gateway:...`) |
| `LOGS_DATABASE_URL` | The audit log database (`postgresql://gateway:.../mcp_logs`) |
| `AUDIT_HMAC_KEY` | Signs the audit chain, 32+ characters. Only the gateway holds it; changing it makes earlier rows fail verification |
| `AUDIT_DETAIL` | `minimal`, `standard` (default) or `full` |
| `AUTH_FAILURES_PER_MINUTE` | Refused logins per address before `429`; 20 by default |
| `CALLS_PER_MINUTE` | Tool calls per user per minute, and the size of a burst; 120 by default |
| `REDIS_URL` | For change notices from the backend, upstream status and counting refused logins |
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
cd gateway && uv sync && uv run pytest                       # unit tests, a few seconds
cd .. && docker compose --profile test up -d --build         # adds three mock upstreams
gateway/.venv/Scripts/python gateway/tests/integration_test.py   # .venv/bin/python on Linux/macOS
```

The unit tests need no running stack, except the call limit's token bucket:
it is a Redis script, so those tests use the compose Redis and skip without it.
They cover the naming and store rules, both handlers, the refresh
loop (including Redis or the database being down), token resolution,
credential decryption, the audit chain and detail levels, the audit routes on
the private port, the block on refused logins and the per-user call limit.

The end-to-end test (about two minutes) registers upstreams through the
backend, then checks: authentication; store scoping; a member in two stores;
the admin's view; a name collision; a hung, a dead and a wrongly-credentialed
upstream; the call timeout; 30 concurrent calls; disabling, re-scoping and
removing an upstream; an upstream that is killed and comes back; and revoking
a key and deactivating a member. For the audit log it checks that each call is
recorded with the right severity, that no role can change or delete a row,
that a row edited behind the trigger's back is found by `python -m app.audit`
(it edits one as the owner and puts it back), the block on refused logins, the
per-user call limit, and that the registry cannot be changed while the gateway
is stopped (it stops the gateway for a moment).

## Writing an upstream server

An upstream built with the `mcp` SDK only answers to `localhost` by default.
Inside a cluster it is reached by its service name, so it must allow that name
in `transport_security` (see `tests/mock_upstream.py`), or the gateway will see
it as unreachable.

## Not built yet

Log retention, OAuth for members, the Helm chart.
See `TIMELINE.md`.
