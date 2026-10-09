# Platform API — Implementation Notes

What this phase built, how it works, and how to run it. Read this before
reading the code.

## What's here

A FastAPI + PostgreSQL + Redis backend covering:

- Admin and team-member authentication (dashboard JWT), with strict RBAC
- Exactly one admin account, enforced at the database level — see "RBAC"
- Admin-managed team: create/list/deactivate members
- Gateway API key lifecycle: admin can issue/revoke any member's key; a
  member can self-service their own (one active key per user, enforced in
  the DB, not just the route)
- Store creation — **stub only**, returns a fake URL (see "What's a stub" below)
- Assigning members to stores
- A demo chat agent that mints short-lived chat tokens server-side (one
  session reused per user, not one per message)
- An internal token-resolve endpoint the future MCP Gateway will call
- Token-bucket rate limiting on login, signup, and all authenticated routes

Not built yet: the MCP Gateway itself, the WooCommerce MCP server, real
Kubernetes/Helm store provisioning, LiteLLM integration, the React dashboard.
Those are later phases — see the project plan doc.

## RBAC: admin vs member

There are exactly two roles, and exactly one admin account can ever exist.

**Admin can:**
- Create, list, and deactivate members (never the admin account itself —
  blocked with 409, since there's no recovery path if the only admin is
  deactivated)
- Issue and revoke **any** member's gateway API key
- Create stores and assign members to them
- Everything a member can do (chat, manage their own key)

**Admin cannot:**
- Create a second admin through `/team/members` (that route is hardcoded to
  `role=member`) or through `/auth/admin/signup` (409s once an admin exists)
- Deactivate the admin account

**Member can:**
- Log in, use the chat agent (one session token, reused across messages —
  see "known race" below for the one edge case)
- See only the stores they're assigned to
- Issue, list, and revoke **their own** gateway API key — never anyone
  else's (the self-service routes take no user-id path parameter; identity
  comes from the JWT, so there's no id to substitute for an IDOR attempt)
- At most one active key at a time: issuing a second while one is active is
  rejected with 409. Revoke, then issue, to rotate.

**Member cannot:**
- Create, list, or deactivate other members (`/team/members/*` is
  admin-only; a member gets 403)
- Create a store (`require_admin` on `POST /stores`)
- Touch another member's API key, even by guessing its id (404, not 403 —
  the route doesn't leak whether the id exists)

### How the single admin is created

Two entry points, same guarded helper (`create_admin_if_absent` in
`app/bootstrap.py`), same invariant — at most one admin, ever:

1. **Env vars** (`ADMIN_EMAIL` + `ADMIN_PASSWORD` in `.env`): created
   automatically on startup, before the app accepts any HTTP traffic
   (FastAPI's lifespan hook runs to completion before uvicorn starts
   serving). Convenient for docker-compose/local dev.
2. **`POST /auth/admin/signup`**: works only while no admin exists yet.
   Useful for a deployment that doesn't want admin credentials sitting in
   env vars. Once an admin exists — by either path — this always 409s.

Leave `ADMIN_EMAIL`/`ADMIN_PASSWORD` blank in `.env` to disable path 1 and
force the explicit signup flow instead.

## Three credential types in this codebase

This project has three distinct bearer-token concepts. Confusing them is the
most common mistake when reading the code, so here's the map:

| Credential | Issued by | Used for | Lives in |
|---|---|---|---|
| Dashboard session JWT | `/auth/login` | Browser → this API (dashboard, team mgmt, chat) | `app/security.py: create_access_token` |
| Gateway API key (`umk_...`) | Admin (for a member) via `/team/members/{id}/api-keys`, or a member for themself via `/team/me/api-keys` | A member's MCP client (Claude Code, Cursor) → the future Gateway | `app/models.py: ApiKey` (hash only) |
| Short chat token (`chat_...`) | This backend, automatically, on `/chat/message` | This backend → the future Gateway, on behalf of a chat session | `app/models.py: ChatToken` (hash only) + Redis |

The gateway API key and chat token are **never** returned to the browser
after their one-time creation response — only their SHA-256 hash is stored.
`/internal/auth/resolve` is how a trusted internal caller (the future
gateway) turns either one into a user identity.

## File-by-file

```
backend/
  app/
    config.py        Settings from env vars (pydantic-settings)
    database.py       Async SQLAlchemy engine + session dependency
    models.py          ORM tables: User, ApiKey, Store, StoreMember, ChatToken
    schemas.py           Pydantic request/response models (password bounds here)
    security.py            Password hashing (bcrypt directly), JWT, key/token generation
    rate_limit.py            Redis token-bucket primitive (see below)
    deps.py                   FastAPI dependencies: auth, admin gate, rate limits
    utils.py                   Store slug generation
    bootstrap.py                 Single-admin creation, shared by startup + signup route
    main.py                       App wiring: lifespan, CORS, routers, /health
    routers/
      auth.py                       POST /auth/login, /auth/admin/signup, GET /auth/me
      team.py                        Member CRUD + admin and self-service API key routes
      stores.py                       Store create (stub) + member assignment
      chat.py                          Demo chat agent + chat token minting
      internal.py                       POST /internal/auth/resolve
  scripts/
    smoke_test.py                       End-to-end check against a live stack
  Dockerfile
  pyproject.toml / uv.lock                Dependencies, managed with uv
```

## Every endpoint

| Method | Path | Auth | Rate limit | What it does |
|---|---|---|---|---|
| GET | `/health` | none | none | Liveness/readiness probe target |
| POST | `/auth/login` | none | IP, 5/min | Returns a dashboard JWT |
| POST | `/auth/admin/signup` | none | IP, 5/min | Creates the one admin; 409 if one already exists |
| GET | `/auth/me` | JWT | user, 60/min | Current user identity |
| POST | `/team/members` | JWT (admin) | user, 60/min | Creates a member, returns a one-time temp password |
| GET | `/team/members` | JWT (admin) | user, 60/min | Lists all users |
| DELETE | `/team/members/{id}` | JWT (admin) | user, 60/min | Deactivates a member; 409 if target is the admin |
| POST | `/team/members/{id}/api-keys` | JWT (admin) | user, 60/min | Issues a key for that member; 409 if they already have an active one |
| GET | `/team/members/{id}/api-keys` | JWT (admin) | user, 60/min | Lists a member's keys (hash never returned) |
| POST | `/team/members/{id}/api-keys/{key_id}/revoke` | JWT (admin) | user, 60/min | Revokes a key |
| POST | `/team/me/api-keys` | JWT | user, 60/min | Self-issue own key; 409 if already have an active one |
| GET | `/team/me/api-keys` | JWT | user, 60/min | List own keys |
| POST | `/team/me/api-keys/{key_id}/revoke` | JWT | user, 60/min | Revoke own key; 404 if the id belongs to someone else |
| POST | `/stores` | JWT (admin) | user, 60/min | **Stub.** Creates a DB row, returns a fake URL |
| GET | `/stores` | JWT | user, 60/min | Admin sees all stores; member sees only assigned stores |
| POST | `/stores/{id}/members` | JWT (admin) | user, 60/min | Assigns a member to a store |
| GET | `/stores/{id}/members` | JWT (admin) | user, 60/min | Lists a store's assigned members |
| POST | `/chat/message` | JWT | user, 10/min | Demo agent reply; mints/reuses a short_chat_token |
| POST | `/internal/auth/resolve` | none (internal) | none | Resolves an API key or chat token to a user identity |

## Rate limiting: why token bucket

Implemented in `app/rate_limit.py` as a single Redis Lua script (atomic
read-refill-decrement — without atomicity, two concurrent requests could
both read "1 token left" and both proceed).

Considered and rejected:
- **Fixed window** (e.g. "5 requests per minute-clock") lets a client send
  2x the limit across a window boundary — 5 requests at 0:59, 5 more at
  1:00. Token bucket has no such edge.
- **Sliding-window log** (sorted set of timestamps per key) is accurate but
  costs memory proportional to requests-in-window per key. Token bucket is
  a fixed 2-field hash per key regardless of traffic.

Token bucket also allows a legitimate burst (dashboard loading several
panels at once) while still enforcing a steady average rate — the traffic
shape this API actually sees. It's the same family of algorithm used by
Stripe/GitHub/AWS API gateways.

Buckets in use:
- Login and admin signup: 5 capacity, refills 5/min, keyed by client IP
  (brute-force guard, applies even to failed attempts since the dependency
  runs before the route body)
- General authenticated routes: 60 capacity, refills 60/min, keyed by user id
- Chat message: 10 capacity, refills 10/min, keyed by user id (stricter —
  this is the route that will eventually cost an LLM call)

A `429` response includes a `Retry-After` header computed from how many
tokens are missing and the refill rate.

**Known limitation:** the IP key comes from `request.client.host`, which is
correct today (clients hit this backend directly) but will become the
reverse proxy's IP once this sits behind a Kubernetes Ingress — collapsing
every user into one shared bucket. Read `X-Forwarded-For` from a trusted
proxy when Ingress lands, not before (trusting that header without a
trusted-proxy allowlist is its own vulnerability).

## Race conditions: what's backed by a DB constraint vs. just a check

Every "does this already exist?" check in this codebase is check-then-write,
which has a race window under concurrent requests. Three are backed by a
real database constraint, so the race can never produce bad data — it just
turns into a clean `409` instead of a `500`:

- Member email uniqueness (`users.email` is `UNIQUE`) — `team.py: create_member`
- Store slug uniqueness (`stores.slug` is `UNIQUE`) — `stores.py: create_store`
  retries up to 3 times with a fresh slug before giving up
- Store-member assignment (`UniqueConstraint(store_id, user_id)`) — `stores.py: assign_member`
- **One active API key per user** — a partial unique index,
  `UNIQUE (user_id) WHERE NOT revoked`, on `api_keys` (`app/models.py`).
  This is enforced at the DB level, not just the application's pre-check in
  `_assert_no_active_key` — two concurrent issue requests for the same user
  cannot both succeed even if both pass the pre-check.

**One exception, left as a documented shortcut:** the chat token's "one
session at a time" (`chat.py: _get_or_create_chat_token`) is check-then-
insert with no DB constraint behind it. A partial unique index doesn't fit
cleanly here because reuse depends on `expires_at` (a moving target), not
just `revoked` — a naive index would block minting a replacement once the
old token expires. Two `/chat/message` calls from the same user in the same
instant could each mint a token; both are valid and belong to that same
user, so there's no security impact, just a brief window where "exactly
one" isn't exactly true. Upgrade this only if the demo needs it to be exact.

## What's a stub (and what replaces it later)

- **Store creation** writes a DB row and returns `https://{slug}.stores.local`
  immediately with `status=ready`. No Kubernetes namespace, no Helm release,
  no WordPress/WooCommerce, no MCP server registration happens. The real
  version is a separate Create Store Service that drives `helm install` per
  store and updates this same `stores` table through a
  `pending → provisioning → ready/failed` state machine.
- **Chat agent** returns a canned string. No LiteLLM call, no MCP Gateway
  call happens. The token-minting and rate-limiting around it are real and
  will not need to change when the real agent is wired in — only
  `_demo_agent_reply` in `app/routers/chat.py` gets replaced.
- **Admin invites** have no email delivery. `POST /team/members` returns a
  system-generated temporary password directly in the response; the admin
  shares it out-of-band. A real email flow (or a signed invite-link flow)
  is future work.
- **No Alembic migrations.** Tables are created via
  `Base.metadata.create_all()` on startup. Fine while the schema is still
  moving fast across phases; introduce Alembic before this schema is
  treated as stable (e.g. once the Gateway and audit-log tables land).
- **No refresh tokens.** The dashboard JWT is a flat 12-hour token. Add a
  refresh flow only if the demo needs longer-lived sessions.
- **CORS is wide open** (`cors_origins: ["*"]` by default) for local dev
  against the not-yet-built React dashboard. Restrict this via the
  `CORS_ORIGINS` env var before any real deployment. (`allow_credentials`
  is deliberately NOT set — this API is Bearer-JWT only, no cookies, so
  there's nothing for it to protect.)
- **`/internal/auth/resolve` has no auth of its own.** It's meant to be
  called only by other backend services over the cluster-internal network
  (the future MCP Gateway). It's open here because there's no cluster to
  restrict it with yet — a NetworkPolicy should scope this in Kubernetes,
  consistent with the rest of the project's "deny by default" posture. It
  also writes `last_used_at` on every call — fine at today's volume (only
  the smoke test calls it), but once the real gateway routes every tool
  call through it, that becomes a write on the hottest path in the system.
  Worth debouncing then, not now.

## Review history

This backend went through a ponytail code review after the initial build.
Fixed in that pass (all verified against the live stack, not just read):

- Login crashed with `500` on a password over 72 bytes (bcrypt's hard limit,
  uncaught) — now bounded to 8-20 chars by `LoginRequest`/`AdminSignupRequest`
- Admin could deactivate the only admin account, bricking the system — now
  blocked, and tightened further to "exactly one admin, ever"
- Three check-then-insert races (email, store slug, store-member assignment)
  could 500 instead of 409 under concurrent requests — now caught and
  converted to clean 409s
- Two check-then-insert races had no DB constraint behind them at all
  (not just a 500-vs-409 problem — the bad outcome could actually happen):
  two concurrent key-issue requests could both succeed, giving a user two
  active keys; two concurrent admin-signup requests could both succeed,
  giving the system two admins. Both got real partial unique indexes
  (`uq_api_keys_one_active_per_user`, `uq_users_single_admin` — see "Race
  conditions" above), not just exception handling
- Dead `allow_credentials=True` in CORS config — removed

## How to run it

```bash
cp .env.example .env
# edit .env: set POSTGRES_PASSWORD, JWT_SECRET
# ADMIN_EMAIL/ADMIN_PASSWORD are optional — see "RBAC" above

docker compose up -d --build
docker compose ps        # wait for all three services healthy

cd backend
uv sync                  # installs runtime + dev deps (httpx) into .venv
uv run scripts/smoke_test.py
```

The smoke test is the self-check for this phase: it exercises every
endpoint and RBAC rule above against the real running stack (no mocks) —
the full credential lifecycle, the single-admin invariant, the member
permission boundary, the one-key-at-a-time rule from both issuance paths,
an IDOR check on self-service key revocation, and the rate limiter actually
tripping under burst load.

Interactive API docs: `http://localhost:8000/docs` once the stack is up.
