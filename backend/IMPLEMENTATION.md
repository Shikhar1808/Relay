# Platform API — Implementation Notes

What this phase built, how it works, and how to run it. Read this before
reading the code.

## What's here

A FastAPI + PostgreSQL + Redis backend covering:

- Admin and team-member authentication (dashboard JWT), with strict RBAC
- Exactly one admin account, enforced at the database level — see "RBAC"
- Admin-managed team: create/list/deactivate members
- Email over SMTP (Resend by default): member invites, password reset, and
  account notices — see "Email and passwords"
- Password change (logged in) and emailed single-use set-password links
- Gateway API key lifecycle: admin can issue/revoke any member's key; a
  member can self-service their own (one active key per user, enforced in
  the DB, not just the route)
- Store creation — **stub only**, returns a fake URL (see "What's a stub" below)
- Assigning members to one or more stores, with chat and the gateway
  token-resolve scoped to those stores
- A demo chat agent that mints short-lived chat tokens server-side (one
  session reused per user, not one per message)
- An internal token-resolve endpoint the MCP Gateway (`gateway/`) calls on
  every request, guarded by a shared service token
- The registry of upstream MCP servers the gateway forwards to, with
  credentials encrypted at rest. Every change is recorded in the gateway's
  audit log before it is committed, and refused (503) if it cannot be
- `GET /audit-log` (admin only): the gateway's audit log for the dashboard,
  newest first, with each user's email added; `GET /audit-log/verify` asks the
  gateway whether that log is untouched
- An append-only record of team, API key and store changes (`admin_events`),
  written in the same transaction as each change and read through
  `GET /audit-log/platform`
- Token-bucket rate limiting on login, signup, and all authenticated routes

Not built yet: the WooCommerce MCP server, real
Kubernetes/Helm store provisioning, LiteLLM integration. The web console
lives in `frontend/` (Next.js) and has its own README.
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
- See only the stores they're assigned to — in the dashboard, in chat,
  and through any MCP client (see "Stores and members")
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
`app/services/bootstrap.py`), same invariant — at most one admin, ever:

1. **Env vars** (`ADMIN_EMAIL` + `ADMIN_PASSWORD` in `.env`): created
   automatically on startup, before the app accepts any HTTP traffic
   (FastAPI's lifespan hook runs to completion before uvicorn starts
   serving). Convenient for docker-compose/local dev.
2. **`POST /auth/admin/signup`**: works only while no admin exists yet.
   Useful for a deployment that doesn't want admin credentials sitting in
   env vars. Once an admin exists — by either path — this always 409s.

Leave `ADMIN_EMAIL`/`ADMIN_PASSWORD` blank in `.env` to disable path 1 and
force the explicit signup flow instead.

## Stores and members: several stores each, and how scoping works

A member can belong to **several stores**, which is what lets one question
span stores through the gateway. A database rule
(`UNIQUE (store_id, user_id)` on `store_members`) only stops adding the
same member to the same store twice, which returns 409. To take someone out
of a store: `DELETE /stores/{id}/members/{user_id}`. The admin is never
assigned to a store (400) — the admin can act on every store.

Everything that shows a member store data asks one function,
`stores_for_user` in `app/services/stores.py` (every store for the admin, a member's own for a member), which reads the database fresh each
time. So an assignment or removal takes effect on the member's very next
request, including for a chat session or API key issued earlier.

| Where a member could see store data | How it is limited to their stores |
|---|---|
| Dashboard: `GET /stores` | Returns their stores, oldest first, or `[]` |
| Chat: `POST /chat/message` | The stores are taken from the logged-in user, never from the message. A member with no store gets 403 |
| Any MCP client (Claude Code, Cursor, the chat agent) through the gateway | `/internal/auth/resolve` returns `store_ids` for the API key or chat token. A member with no store gets `store_ids: []`; the admin gets every store, so the gateway filters the same way for everyone and never special-cases the role |

**Who enforces it at the gateway:** `gateway/app/tools.py`. It takes the
stores from `store_ids` in the resolve response and nothing else — never from
a tool argument, a header, or anything the LLM wrote. An upstream with a
`store_id` is listed and callable only for callers who have that store; one
with no `store_id` is shared. Tool names are `slug__tool`, so a member in
several stores can tell which store a tool acts on.

**What is NOT enforced yet, because it does not exist yet:**

- The real chat agent. `_demo_agent_reply` is a stub. The real one must put
  only those stores' data into the LLM prompt and tool results.

## Database migrations

The schema is managed by Alembic. `app/db/models/` describes the tables;
the files in `backend/migrations/versions/` are what actually change a
database. The backend container runs `alembic upgrade head` before it
starts serving, so `docker compose up -d --build` applies anything pending.
If a migration fails, the container exits instead of serving.

**Changing the schema**

1. Edit `app/db/models/`.
2. Put `DATABASE_URL` (pointing at `localhost:5432`) and `JWT_SECRET` in
   `backend/.env`, so Alembic can reach the dev database from your machine.
3. From `backend/`: `uv run alembic revision --autogenerate -m "what changed"`
4. **Read the generated file.** Autogenerate misses some things: it does
   not drop enum types, it cannot tell a rename from a drop-and-add (which
   loses the data), and a new NOT NULL column or unique rule fails on
   existing rows unless you handle them in the migration.
5. `docker compose up -d --build backend` applies it. Commit the migration
   with the model change.

Other commands, all from `backend/`: `uv run alembic current` (where this
database is), `uv run alembic check` (do the models and migrations agree),
`uv run alembic downgrade -1` (undo the last one).

**A database created before Alembic** (by the old `create_all` on startup)
already has the tables, so the baseline would fail on it. Bring it to the
baseline schema, then mark it as done, once:

```
ALTER TABLE store_members ADD CONSTRAINT uq_store_members_one_store_per_user UNIQUE (user_id);
ALTER TABLE store_members DROP CONSTRAINT uq_store_member;
```

then `uv run alembic stamp 0001`. The first statement fails if a member is
in two stores — fix that data first. A fresh database needs none of this.

## Email and passwords

`app/services/mailer.py` sends plain-text email with stdlib `smtplib` (no SDK). The
defaults in `app/core/config.py` point at Resend's SMTP relay, so the only thing
to set is `SMTP_PASSWORD` — your Resend API key — in the repo-root `.env`.

- **`SMTP_PASSWORD` unset (dev default):** nothing is sent. Each email,
  including its set-password link, is written to the backend log
  (`docker compose logs backend`). Never deploy like this.
- **Default sender `onboarding@resend.dev`:** Resend only delivers this to
  the address that owns the Resend account. To email real team members,
  verify a domain in Resend and set `SMTP_FROM` to an address on it.
- Sending runs in a FastAPI background task after the response, so a slow
  or failing mail server never fails a request. A failed send is logged and
  dropped — there is no retry queue. `resend-invite` and `forgot-password`
  are the recovery paths.

**Set-password links.** Invites and resets are the same mechanism
(`app/services/auth_tokens.py`, table `auth_tokens`): a random token is emailed, only
its SHA-256 is stored, and `POST /auth/set-password` redeems it with one
`DELETE ... RETURNING`, which is what makes it single-use under concurrent
requests. Invite links last 72 hours, reset links 30 minutes. Issuing a new
link replaces the previous one of the same kind, and any password change
deletes all of the user's outstanding links.

Links point at the frontend (`APP_BASE_URL/set-password#token=...`), whose
page posts the token to `POST /auth/set-password`. The token sits after
`#` so the browser never sends it to any server and it stays out of access
logs. `APP_BASE_URL` must be the address people open the frontend at.

**Who gets emailed, and when:**

| Event | Recipient |
|---|---|
| Admin creates a member, or resends the invite | the member (set-password link) |
| `POST /auth/forgot-password` for an active account | that user (reset link) |
| Password changed (by change, invite, or reset) | that user |
| Admin deactivates a member | the member |
| Admin issues or revokes a member's API key | the member (never the key itself) |
| Admin assigns a member to a store, or removes them | the member |

`POST /team/members` still returns the temporary password as well, as a
fallback while there is no dashboard. Drop it once invites are trusted.

## Three credential types in this codebase

This project has three distinct bearer-token concepts. Confusing them is the
most common mistake when reading the code, so here's the map:

| Credential | Issued by | Used for | Lives in |
|---|---|---|---|
| Dashboard session JWT | `/auth/login` | Browser → this API (dashboard, team mgmt, chat). The browser holds it as a cookie page scripts cannot read (`HttpOnly`, `SameSite=Strict`); scripts and tests send it as a `Bearer` header | `app/core/security.py: create_access_token`; `app/core/deps.py: session_token` |
| Gateway API key (`umk_...`) | Admin (for a member) via `/team/members/{id}/api-keys`, or a member for themself via `/team/me/api-keys` | A member's MCP client (Claude Code, Cursor) → the future Gateway | `app/db/models/api_key.py: ApiKey` (hash only) |
| Short chat token (`chat_...`) | This backend, automatically, on `/chat/message` | This backend → the future Gateway, on behalf of a chat session | `app/db/models/tokens.py: ChatToken` (hash only) + Redis |

The gateway API key and chat token are **never** returned to the browser
after their one-time creation response — only their SHA-256 hash is stored.
`/internal/auth/resolve` is how a trusted internal caller (the future
gateway) turns either one into a user identity.

## File-by-file

```
backend/
  app/
    main.py              App wiring: lifespan, CORS, routers, /health
    core/                Cross-cutting plumbing, no business rules
      config.py            Settings from env vars (pydantic-settings)
      security.py          Password hashing (bcrypt directly), JWT, key/token generation
      rate_limit.py        Redis token-bucket primitive (see below)
      crypto.py            AES-256-GCM for upstream credentials (the gateway has the matching decrypt)
      deps.py              FastAPI dependencies: auth, admin gate, rate limits
    db/
      session.py           Async SQLAlchemy engine + session dependency
      models/              ORM tables; import them from `app.db.models`
        base.py              Declarative Base
        user.py              User, Role
        api_key.py           ApiKey
        store.py             Store, StoreMember, StoreStatus
        tokens.py            AuthToken, TokenPurpose, ChatToken
        upstream.py          UpstreamServer (an MCP server the gateway forwards to)
        admin_event.py       AdminEvent (who changed what in the team, keys and stores; append-only)
    schemas/             Pydantic request/response models, one file per route file
      auth.py              Login, signup, password schemas; the Password rule; UserOut
      team.py              Member and API key schemas
      stores.py  chat.py  internal.py  upstreams.py  audit.py
    services/            Logic shared by more than one route
      bootstrap.py         Single-admin creation, shared by startup + signup route
      mailer.py            SMTP sender + plain-text email templates
      auth_tokens.py       Issue/redeem single-use invite and reset tokens
      stores.py            Store slug generation; stores_for_user (the scoping rule)
      gateway_audit.py     Writes to and reads from the gateway's audit log (its private port)
      admin_audit.py       Adds an admin_events row to the caller's transaction
    api/
      routes/
        auth.py            Login, admin signup, /auth/me, change/forgot/set password
        team.py            Member CRUD + admin and self-service API key routes
        stores.py          Store create (stub) + member assignment
        chat.py            Demo chat agent + chat token minting
        upstreams.py       Upstream MCP server registry (admin only); tells the gateway on change
        audit.py           GET /audit-log, /audit-log/platform, /audit-log/verify: the audit log, for the dashboard (admin only)
        internal.py        POST /internal/auth/resolve (service token required)
  scripts/
    smoke_test.py                       End-to-end check against a live stack
  tests/                                Unit tests: `uv run pytest` (settings, passwords and tokens,
                                        encryption, input rules, the rate limiter, the audit log client,
                                        recording admin events)
  migrations/                           Alembic: env.py + one file per schema change
  alembic.ini
  Dockerfile                            Runs `alembic upgrade head`, then uvicorn
  pyproject.toml / uv.lock                Dependencies, managed with uv
```

## Every endpoint

| Method | Path | Auth | Rate limit | What it does |
|---|---|---|---|---|
| GET | `/health` | none | none | Liveness/readiness probe target |
| POST | `/auth/login` | none | IP, 5/min | Returns a dashboard JWT and sets it as the session cookie |
| POST | `/auth/logout` | none | none | Signs out: removes the session cookie, revokes the token that was in it and the user's chat tokens. 204 even if nobody is signed in |
| GET | `/auth/session` | none | none | `{"user": ...}` for whoever is signed in, `{"user": null}` if nobody is. Never 401, so a page can ask on every load |
| POST | `/auth/admin/signup` | none | IP, 5/min | Creates the one admin; 409 if one already exists |
| GET | `/auth/me` | JWT | user, 60/min | Current user identity |
| POST | `/auth/change-password` | JWT | IP, 5/min | Changes own password; 400 if the current one is wrong |
| POST | `/auth/forgot-password` | none | IP, 5/min | Emails a reset link; identical 202 whether or not the email exists |
| POST | `/auth/set-password` | none | IP, 5/min | Redeems an invite/reset token, sets the password, returns a JWT and sets the session cookie; 400 if invalid, used, or expired |
| POST | `/team/members` | JWT (admin) | user, 60/min | Creates a member, emails an invite link, returns a one-time temp password |
| POST | `/team/members/{id}/resend-invite` | JWT (admin) | user, 60/min | Emails a fresh invite link; the old one stops working |
| GET | `/team/members` | JWT (admin) | user, 60/min | Lists all users |
| DELETE | `/team/members/{id}` | JWT (admin) | user, 60/min | Deactivates a member; 409 if target is the admin |
| POST | `/team/members/{id}/api-keys` | JWT (admin) | user, 60/min | Issues a key for that member; 409 if they already have an active one |
| GET | `/team/members/{id}/api-keys` | JWT (admin) | user, 60/min | Lists a member's keys (hash never returned) |
| POST | `/team/members/{id}/api-keys/{key_id}/revoke` | JWT (admin) | user, 60/min | Revokes a key |
| POST | `/team/me/api-keys` | JWT | user, 60/min | Self-issue own key; 409 if already have an active one |
| GET | `/team/me/api-keys` | JWT | user, 60/min | List own keys |
| POST | `/team/me/api-keys/{key_id}/revoke` | JWT | user, 60/min | Revoke own key; 404 if the id belongs to someone else |
| POST | `/stores` | JWT (admin) | user, 60/min | **Stub.** Creates a DB row, returns a fake URL |
| GET | `/stores` | JWT | user, 60/min | Admin sees all stores; member sees only their stores |
| POST | `/stores/{id}/members` | JWT (admin) | user, 60/min | Assigns a member to a store; 409 if they already belong to one, 400 for the admin |
| DELETE | `/stores/{id}/members/{user_id}` | JWT (admin) | user, 60/min | Removes a member from a store, effective immediately |
| GET | `/stores/{id}/members` | JWT (admin) | user, 60/min | Lists a store's assigned members |
| POST | `/chat/message` | JWT | user, 10/min | Demo agent reply scoped to the caller's store; mints/reuses a short_chat_token; 403 for a member with no store |
| GET | `/upstream-servers` | JWT (admin) | user, 60/min | Lists registered MCP servers; `has_credential` only, never the credential |
| POST | `/upstream-servers` | JWT (admin) | user, 60/min | Registers a server; 409 on a duplicate slug, 404 for an unknown store |
| PATCH | `/upstream-servers/{id}` | JWT (admin) | user, 60/min | Changes only the fields sent (disable with `enabled: false`; `credential: ""` removes it). The slug is fixed |
| DELETE | `/upstream-servers/{id}` | JWT (admin) | user, 60/min | Removes a server |
| POST | `/internal/auth/resolve` | service token | none | Resolves an API key or chat token to a user identity and the stores they may touch |

## Rate limiting: why token bucket

Implemented in `app/core/rate_limit.py` as a single Redis Lua script (atomic
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
- Login, admin signup, and change/forgot/set password: 5 capacity, refills
  5/min, keyed by client IP and path (brute-force guard, applies even to
  failed attempts since the dependency runs before the route body).
  `forgot-password` is limited per IP only, not per target address, so one
  client can still send a victim up to 5 reset emails a minute.
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
- A member can be added to a store only once (`UniqueConstraint(store_id, user_id)` on `store_members`) — `stores.py: assign_member`
- **One active API key per user** — a partial unique index,
  `UNIQUE (user_id) WHERE NOT revoked`, on `api_keys` (`app/db/models/api_key.py`).
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
  `_demo_agent_reply` in `app/api/routes/chat.py` gets replaced.
- **Email has no retry queue and no HTML templates** — see "Email and
  passwords".
- **Changing a password does not end existing sessions.** There is no
  session store, so a JWT issued before the change stays valid until it
  expires (12 hours).
- **Migrations run on container start**, in every backend container. That
  is right for the single backend in docker-compose. With several replicas
  on Kubernetes, move `alembic upgrade head` into a one-off Job or init
  step so two pods never migrate at the same moment.
- **No refresh tokens.** The dashboard JWT is a flat 12-hour token. Add a
  refresh flow only if the demo needs longer-lived sessions.
- **The browser session is a cookie, so who may call the API matters.** Only
  the frontend's own address (`APP_BASE_URL`, or the list in `CORS_ORIGINS`)
  may call the API from a browser with the session; `"*"` is refused at
  startup. A change sent with the cookie must also name one of those addresses
  in its `Origin` header, or it gets 403. The cookie is `SameSite=Strict`, so
  the frontend and the API must be on the same site (one host, or subdomains
  of one domain). Signing out removes the cookie and revokes the token that
  was in it: its hash is kept in Redis until the token would have expired, and
  every request checks that list. A request with no token and no cookie gets
  401 (it used to be 403).
- **`/internal/auth/resolve` trusts one shared secret.** Callers must send
  `INTERNAL_SERVICE_TOKEN` as a bearer token (compared in constant time), so
  reaching the backend is not enough to probe tokens. A NetworkPolicy should
  still limit callers to the gateway in Kubernetes. The route also writes
  `last_used_at` on every call, and the gateway calls it on every MCP
  request, so that is a write on the hottest path in the system — worth
  debouncing if it shows up under load.
- **No SSRF filter on upstream URLs.** Only the scheme is checked, because
  upstream servers legitimately live on cluster-internal addresses and only
  the admin can register one.

## Review history

This backend went through a ponytail code review after the initial build.
Fixed in that pass (all verified against the live stack, not just read):

- Login crashed with `500` on a password over 72 bytes (bcrypt's hard limit,
  uncaught) — now bounded to 8-20 chars by `LoginRequest`/`AdminSignupRequest`.
  That fix counted characters, so 20 emoji (80 bytes) still crashed; the
  shared `Password` type in `app/schemas/auth.py` now also requires printable
  ASCII, on every endpoint that takes a password
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

A second review, after email, store scoping and migrations landed, fixed:

- Emails were matched exactly, so `Bob@x.com` could not log in or reset a
  password for an account stored as `bob@x.com`, and both could be
  registered as separate accounts. Every email is now lowercased on the way
  in (`Email` in `app/schemas/auth.py`); migration `0002` lowercases
  existing rows
- A member, admin, API key or store name longer than its column crashed
  with `500` — now bounded in the schemas and rejected with `422`
- Login answered faster for an unknown email than for a wrong password (no
  bcrypt check ran), which revealed which emails have accounts — bcrypt now
  runs either way
- A deactivated member could still be assigned to a store, and was emailed
  that they had access — now `404`
- Set-password link expiry had no test — the smoke test now back-dates a
  link in the database and checks it is refused
- Unused `ApiKey.user` relationship — removed

## How to run it

```bash
cp .env.example .env
# edit .env: set POSTGRES_PASSWORD, JWT_SECRET, GATEWAY_ENCRYPTION_KEY,
#   INTERNAL_SERVICE_TOKEN, GATEWAY_DB_PASSWORD (the file says how to generate them)
# ADMIN_EMAIL/ADMIN_PASSWORD are optional — see "RBAC" above
# SMTP_PASSWORD (Resend API key) is optional — see "Email and passwords"

docker compose up -d --build
docker compose ps        # wait for the services to be healthy (db-init exits, that is normal)
# the web console is now on http://localhost:3000, the API on :8000,
# the MCP gateway on http://localhost:8080/mcp (see gateway/README.md)

cd backend
uv sync                  # installs runtime + dev deps (httpx, pytest) into .venv
uv run pytest            # unit tests, no stack needed (the rate limiter ones use the compose Redis)
uv run scripts/smoke_test.py   # end to end, against the running stack
# or, from the repo root: ./run_tests.sh (unit) and ./run_tests.sh all (everything)
```

The smoke test is the self-check for this phase: it exercises every
endpoint and RBAC rule above against the real running stack (no mocks) —
the full credential lifecycle, the single-admin invariant, the member
permission boundary, the one-key-at-a-time rule from both issuance paths,
an IDOR check on self-service key revocation, and the rate limiter actually
tripping under burst load.

Interactive API docs: `http://localhost:8000/docs` once the stack is up.
