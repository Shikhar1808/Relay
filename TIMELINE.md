# Timeline

Progress against the implementation guide and the assignment brief (both kept outside the repo).

- **Deadline:** 26 Oct 2026
- **Last updated:** 20 Oct 2026
- **Now:** the chat agent is built: a LangGraph graph in the backend that answers by calling tools through the gateway as the signed-in user, checked live against Groq and in a headless browser. It answers each message in one piece (no streaming). It reaches Groq directly; the LLM gateway in front of it is not built. The Servers page is built. On k3d (18 Oct) the two Helm charts (`helm/platform`, `helm/store`) run everything, and all three servers (woo-demo, risk, fulfilment) show Connected. NetworkPolicies are in and tested: only the gateway reaches the store MCP servers, and only fulfilment-mcp from a store namespace reaches Postgres. `values-prod.yaml` is written for the VPS. Stores are now made from the dashboard (20 Oct, branch `feat/multi-store`): the store worker installs `helm/store` per store and registers its server; `second` was made on k3d and is ready, with `woo-second` Connected. Isolation between the two stores checked from inside the pods. Next: the rest of the bonus checks (two stores at once, recovery, delete), then the VPS (k3s, DNS, cert-manager)

Tick an item only when it is built and checked. Update "Last updated" and "Now" with every change.

## Oct 9–11: Database and platform backend

- [x] Repo with backend, frontend and gateway (`backend/`, `frontend/`, `gateway/`; the guide's `/woo-mcp`, `/helm`, `/create-store-service` come later)
- [x] Postgres platform database
- [x] Redis
- [x] Tables: `users`, `api_keys` (hashed), `short_chat_tokens`
- [x] Store membership table (`store_members`, a member can be in several stores)
- [x] Database migrations (Alembic)
- [x] Auth endpoints: admin signup, login, issue and revoke API keys
- [x] Table: `upstream_servers` with encrypted credentials (and an optional `store_id`)
- [x] Table: `admin_events`, an append-only record of team, API key and store changes, written in the same transaction as the change
- [x] Upstream server endpoints: list, add, edit, disable, remove (`/upstream-servers`)
- [x] AES-256-GCM encrypt/decrypt helper, used when saving and loading upstream credentials
- [x] MCP logs database with `audit_log` table (`mcp_logs`, append-only by role and trigger, hash chain)
- [x] **Checkpoint:** create a user, issue an API key, add an upstream server with encrypted credentials

## Oct 12–13: MCP gateway core

- [x] Streamable HTTP server at `/mcp` (separate service, official `mcp` SDK 2.3, stateless)
- [x] Resolve an API key or chat token to a user (`POST /internal/auth/resolve`)
- [x] Gateway auth that uses it on every request, with a service token on `/internal/*`
- [x] Upstream registry: load enabled upstreams at startup and on change (every 30 s and on a Redis message)
- [x] `tools/list` with concurrent fan-out, 5 s timeout per upstream, `slug__tool` names (fan-out runs in the background; requests read a snapshot)
- [x] `tools/call` routing: strip prefix, find upstream, add credential, 30 s timeout, store check
- [x] Audit log entry on every tool call (also refused logins and upstreams going down or coming back; severity and detail levels)
- [x] **Checkpoint:** call a mock upstream through the gateway and see the audit entry

## Oct 14: WooCommerce MCP server

- [x] WordPress and WooCommerce running in Docker (`mysql`, `wordpress` on http://localhost:8081, and the one-shot `wp-init`, which runs `woo-mcp/deploy/wp-init.sh`; MySQL, not MariaDB)
- [x] MCP server over Streamable HTTP with 7 tools (list/get/create/update product, list/get order, update order status) (`woo-mcp/`, with the SDK's `MCPServer`; REST API v3; no published port, and a token only the gateway holds)
- [x] Settings from `WOOCOMMERCE_URL`, `WOOCOMMERCE_KEY`, `WOOCOMMERCE_SECRET` (plus `MCP_AUTH_TOKEN`)
- [x] The first store and its server registered at startup, like any later store: a store row with the subdomain `demo` and an upstream `woo-demo` that belongs to it
- [x] **Checkpoint:** `list_products` through the gateway, with an audit entry (all seven tools as a member, a product created and seen on the storefront, a guest checkout with cash on delivery returned by `list_orders` for today, each call in the audit log under that member)

## Oct 15: Chat agent, LLM gateway, dashboard shell

- [ ] LLM gateway in front of the model: Bifrost, chosen on 13 Oct over LiteLLM after measuring both (about a fifth of the memory). Moved to the end on 17 Oct, after Kubernetes. Until it is in, the chat reaches Groq directly; the model's address, key and name are settings (`LLM_BASE_URL`, `GROQ_API_KEY`, `LLM_MODEL`), so this is a change of settings
- [x] Short chat token issued and stored in Redis and the database: one per chat, opened by `POST /chat/message`, kept encrypted so the agent can present it on every message
- [x] `/chat/message` calls the LLM with the gateway's tools: a LangGraph graph in `backend/app/agent` (seven steps, its state saved in Postgres), which calls the gateway with the signed-in user's chat token, so each call is in the audit log under that user, with the chat's id and the message's id (13 Oct)
  - The agent reads the hints the WooCommerce tools declare: a tool that does not say it only reads waits for the user's yes or no.
- [x] Showing which tools were called: each answer comes with its tool calls and their results, and from the second answer on with every tool the chat has used
- [ ] Streaming replies. Built on 13 Oct (tool calls and text as they happen) and taken out the same day at the user's request: a message is answered in one piece
- [x] Chat page layout after `refernces/chatBotDesign.png`: the results on the left (each tool result as a table, a card or a notice, with the tool it came from, then the calls in words, and from the second answer on the list of every tool the chat has used), the chat on the right. Checked in headless Chrome at desktop and phone widths (13 Oct): a read, a write with its confirmation, a reload, ending the chat, reading an earlier one
- [x] Dashboard shell: sign in, side navigation, Team, API key, Chat, Account, Stores pages
- [x] Dashboard: Servers page (`/servers`, admin only; 17 Oct): add, edit, disable, remove, and each server's status and tools from the gateway's last probe (it now also writes `upstream:<id>:tools` to Redis). Checked in headless Chrome at desktop and phone widths, with `woo-demo` connected and its 7 tools
- [x] Dashboard: Audit log page (`/audit`, admin only; checked in a browser at desktop and phone widths)
- [x] Dashboard look after `refernces/frontend desgin ref/` (19 Oct): ink on paper, thin frames with one corner cut, tabs along the top in place of the side navigation, a row of counts at the top of Stores, Team and Servers, statuses as a coloured dot. Then made livelier: a paper colour per section (Servers dark), a headline per page with the section and stores above it, a rail with the name read upwards, an outline that turns between pages, icon squares on store and server tiles. Checked in headless Edge at desktop width against a stand-in API with sample data
- [x] **Checkpoint:** ask a question in chat, the agent calls a WooCommerce tool, the reply comes back (in one piece, see above; 13 Oct, with Groq's `openai/gpt-oss-20b`: "what orders came in today?", and a product created after a confirmation and seen on the storefront)

## Oct 16: Buffer and integration test

- [ ] Full definition-of-done flow by hand
- [ ] Second and third upstream servers, chosen 16 Oct, written and unit-tested; tick once both are registered by hand and the chat flow below has run:
  - Risk check (`risk-mcp/`): `score_orders` scores a batch of orders with five fixed rules (total, bulk quantity, throwaway email domain, cash on delivery on a large order, one email on several orders) and gives a level and the reasons. Reads nothing, stores nothing, calls nothing. Shared by every store.
  - Fulfilment (`fulfilment-mcp/`): `start_fulfilment`, `advance_fulfilment` (packing → shipped → delivered, never back, a batch all or nothing), `list_fulfilments`. Records in Postgres in a database and role of their own (`gateway/deploy/db-init.sh`), so the pod keeps nothing. One per store.
  - Chat flow to check: "check today's orders, put the risky ones on hold and ship the rest" (three servers in one answer), then "mark them delivered" (the orders end `completed` in the store).
    Tried 18 Oct on k3d: the chat lists and scores the orders, then Groq's free tier refuses the third model call (8,000 tokens a minute; this question needs about 8,500). Replayed with pauses, the model's first change was the right one (the risky order on hold). Left as is on purpose; Bifrost with a fallback provider is the planned way past the limit.
  - Product photos (Pexels, then Pixabay) was built first and dropped the same day: neither would issue the user an API key. `woo-mcp`'s `image_url` went with it, on purpose: WordPress downloads whatever address it is given onto the public shop.
- [x] API key revocation rejected at the gateway
- [x] Disabling an upstream removes its tools

## Oct 17–18: Kubernetes core

- [x] Helm charts: Postgres, Redis, gateway/backend, dashboard, WordPress with MySQL (`helm/platform` in namespace `urumi`, `helm/store` in `store-demo`; 18 Oct)
- [x] `values-local.yaml`
- [x] Readiness and liveness probes on every workload
- [x] Resource requests and limits on every container (wp-init needs 512Mi: at 256Mi it was OOM-killed installing WooCommerce)
- [x] All secrets in Kubernetes Secrets (`urumi-platform`, `store-demo`, made with kubectl from git-ignored env files)
- [x] Container images for backend, frontend and gateway (non-root, health endpoints)
- [x] **Checkpoint:** everything running in local Kubernetes (k3d on Windows, 18 Oct)

## Oct 19–20: Ingress and WordPress init

- [x] Traefik (k3s's own, instead of NGINX) with Ingresses for `app.`, `api.`, `mcp.` and the storefront `demo.` under `127.0.0.1.nip.io` (18 Oct). Ingress for storefront and dashboard. The dashboard and the API must end up on one site, or sign in breaks (the session cookie is `SameSite=Strict`): one host with the API under a path such as `/api`, or two subdomains of one domain (`app.relay.local` and `api.relay.local`). Not `dashboard.local` and `api.local`. Set `APP_BASE_URL` to the dashboard's address; never set `CORS_ORIGINS` to `"*"`, the backend refuses to start with it
- [x] Local domain instructions: names under nip.io, no hosts file needed (README, "Local domain approach", with hosts-file lines for working offline; 18 Oct)
- [x] wp-cli init Job: a plain Job named per release revision, not a Helm hook (a hook would deadlock with `--wait`)
- [x] `values-prod.yaml` (both charts: `shikharsaxena.space`, https, cert-manager with Let's Encrypt staging first; rendered and dry-run only, 18 Oct)
- [x] **Checkpoint:** definition-of-done flow over Ingress with two users (18 Oct on k3d, by script: the admin's chat created a product and found its order, a member's own key listed all 11 tools and called woo-demo and risk, disabling risk hid its tools from both, the revoked key got 401, every call in the audit log under the right user. The storefront order went through the shop's own checkout API and showed in WooCommerce admin. Still by hand for the video: clicking through checkout, and Claude Code as the external client)

## Oct 21: Health checks and audit log UI

- [x] Background upstream health check (30 s, cached in Redis as `upstream:<id>:status`)
- [x] Servers page shows Connected / Unreachable / Disabled (and Unchecked before the first probe)
- [x] Kill-pod test: dead server shows Unreachable, others keep working (18 Oct on k3d: a deleted risk-mcp pod is replaced at once; scaled to zero it showed Unreachable after 20 s, its call failed with "risk is unreachable", woo-demo and fulfilment kept answering; one of fulfilment's two pods killed, calls still worked)
- [x] Audit log page: user, server, tool, time, duration, outcome (also level, refused sign-ins, server changes, server health, team/key/store changes, and a "has the log been tampered with" check)

## Before the bonus: the graded notes

Moved ahead of the bonus on 12 Oct: these are graded, the bonus is optional.

- [x] Root `README.md`: local setup, VPS setup, placing an order, connecting an MCP client, using chat, upgrade and rollback, the bonus and stand-out list (18 Oct; with `scripts/make_k8s_secrets.py` and `helm/cluster-issuers.yaml`, which it uses)
- [x] System design and tradeoffs note: `docs/system-design.md` (18 Oct)
- [x] Threat model note: `docs/threat-model.md` (18 Oct)

## Oct 22–23: Bonus, multi-store provisioning

- [x] Stores page and endpoints: create, list, retry, delete; status, step, error, address and created time, reloaded every 3 s while a store is busy (20 Oct)
- [ ] Delete a store (built 20 Oct: name typed again and a reason, recorded; live check pending)
- [x] Create Store Service: the store worker (`backend/app/worker.py`, its own pod and image) installs `helm/store` per store in `store-<subdomain>` (20 Oct: `second` made on k3d, storefront up)
- [ ] Provisioning state machine with retry and recovery (built 20 Oct: pending → provisioning → ready/failed, deleting. Retry checked live: `second` failed on a Helm 4 flag, then reached ready after Retry, reusing its namespace and Secret. A restarted worker resuming a store is unit-tested; live check pending)
- [ ] Creating or registering a store that already exists reconnects to it (`helm upgrade --install`): no second store, its pods started again if they are down, its volumes kept; only an explicit delete removes them (built 20 Oct: a used subdomain gets 409, every worker step is safe to repeat; pods started again not checked live)
- [x] Automatic MCP registration per store: `woo-<subdomain>`, recorded in the gateway's audit log (20 Oct: `woo-second` Connected with its 7 tools)
- [x] NetworkPolicies, ResourceQuota, LimitRange per store namespace (NetworkPolicies 18 Oct; quota and limit range 20 Oct, store-demo using 350m CPU, 1056Mi, 2304Mi of limits and 7Gi under 1, 2Gi, 4Gi and 10Gi)
- [ ] Clean teardown (built 20 Oct: the store's servers, then its namespace with volumes and Secrets, then the row; live check pending)
- [x] Isolation between stores (20 Oct, from inside the pods on k3d): store-second's woo-mcp reaches its own WordPress but is refused by store-demo's MySQL and WordPress; store-demo's woo-mcp is refused by store-second's MySQL, WordPress and woo-mcp

## Oct 24: Stand-out items

- [x] Rate limits per user and per IP
- [x] Per-role tool permissions (20 Oct): each server has `admin_only_tools`; the gateway hides them from a member's list and refuses a direct call with the same "Unknown tool", recorded as `denied` with the real server and tool; the admin toggles them on the Servers page; WooCommerce's three write tools start locked on every store. Checked on the Compose stack end to end (list, refused call, audit row, unlock without a restart); the k3d check comes after the image upgrade
- [ ] Prompt injection check on tool descriptions
- [ ] Audit log heatmap
- [x] Confirmation step for write tools in chat: a write waits for a yes or no, one question for all the writes in a reply; a tool with no hints counts as a write (13 Oct)

## Oct 25–26: Polish, docs, video

- [ ] Demo video
- [ ] Final definition-of-done run with two users
- [ ] Submit the form

## Done outside the plan

- Invite, password reset and notice emails (Resend over SMTP)
- Change password, forgot password, set password by emailed link
- Members can belong to several stores (for cross-store questions); chat and `/internal/auth/resolve` return `store_ids`, and the gateway must filter on them
- Chat and dashboard rate limits no longer share one bucket per user
- The dashboard session is a cookie page scripts cannot read (`HttpOnly`, `SameSite=Strict`), no longer a token in `localStorage`; only the frontend's own address may call the API with it, and a change must come from one of its pages
- The rate limiters call their Redis script by its id instead of sending it on every check, and send it again if Redis has forgotten it
- Signing out ends the session at once: the token is revoked (remembered in Redis until it would have expired) and so are the user's chat tokens, as the guide asks
- `GET /auth/session` says who is signed in without ever answering 401, so a signed-out page load prints nothing in the browser console
- A request with no token gets 401 (not signed in), no longer 403
- Gateway blocks an address after 20 refused logins in a minute; `python -m app.audit` verifies the audit chain
- Gateway limits each user to 120 tool calls a minute with a token bucket: a burst up to 120, then 2 a second come back, so a user who ran dry can call again a moment later (the first version was a fixed one-minute window)
- Team, API key and store changes are recorded in the Platform DB (`admin_events`, append-only by trigger), each with who made it and from where
- The Audit page can ask whether the gateway's log is untouched (`GET /audit-log/verify` walks the hash chain)
- Registry changes are recorded in the audit log (who, which server, what was sent) and refused if they cannot be
- `GET /audit-log` on the backend (admin only) reads the gateway's audit log for the dashboard, and `GET /audit-log/platform` the team, key and store changes
- A registry change that is recorded and then fails to commit gets a second, `failed` event
- The gateway records the registry changes it sees itself (`registry` events), so a change made in the database without the API shows up in the audit log
- The store worker's rights stop at `store-*` namespaces: a ValidatingAdmissionPolicy makes the API server refuse it any namespace or RoleBinding elsewhere, which RBAC alone cannot say (20 Oct; `evil` and a RoleBinding in `kube-system` refused)
- A worker killed in the middle of `helm` leaves no stuck release: the next run uninstalls a `pending-install` release or rolls back a `pending-upgrade` one first
- Gateway hardening beyond the guide: no anonymous path, tokens in the URL refused, Host allowlist, health on a separate unpublished port, read-only database role, read-only container filesystem
- The first store is a store like any other, not a special case: it has a row in `stores`, a subdomain checked by the one rule later stores will use (a DNS label of at most 30 characters, platform names such as `api` refused), and its server is `woo-<subdomain>`, limited to the admin and that store's members
- The store's REST key belongs to a WordPress user with the Shop manager role, not to the administrator; the key pair comes from `.env` and the init script registers it, so nothing is clicked and nothing is read back out of WordPress
- The WooCommerce tools return short records (no address, phone or IP of a customer), and a refusal from the store reaches the model as a readable tool error
- The list tools also return `total`, how many match in all, so a full page is not taken for the whole answer
- A repeated `create_product` cannot make a duplicate: the SKU is made from the name and the store itself refuses a second one, also when requests arrive at once (eight at once made one product); the repeat is refused with the id of the product already there
- The store's install script sets the store's own settings once and then leaves them to its admin; what `.env` holds (the wp-admin password, the REST key) is applied on every start; WooCommerce is pinned to 11.2.1
- Each WooCommerce tool says whether it reads, adds or overwrites (MCP tool annotations), and the gateway passes that on; the chat's confirmation step acts on it
- A chat is one conversation at a time: it stays open across reloads until the member ends it, signs out or leaves it idle for 30 minutes. Earlier chats can be read, not continued, and are deleted one to two days after their last use (`CHAT_RETENTION_DAYS` is 1; the clean-up runs once a day)
- The gateway's audit log records which chat and which message a chat tool call belongs to; only for the chat agent's token, so an API-key caller cannot plant them
- A first store registered by the admin's signup is recorded as the signup's doing, with the caller's address, and the signup answers normally when the store was already registered
- Tests: `./run_tests.sh` runs the unit tests of the three services (pytest), `./run_tests.sh all` adds the two end-to-end tests (`backend/scripts/smoke_test.py`, `gateway/tests/integration_test.py` with mock upstreams)
- The project is called Relay (renamed 19 Oct): the dashboard, emails, the API and gateway names, the charts (`relay-platform`, `relay-store`), the images (`relay-*`), the Compose project (`name: relay`, its data copied over from the old volumes), the session cookie, and the store's setup flag and REST key label. The Kubernetes names stay as they were: namespace `urumi`, Secret `urumi-platform`, label `urumi.io/store`, k3d cluster `urumi`

## Future expansion

Everything that was left out on purpose, with what exists today. None of it is needed for the assignment. Assignment work that is not built yet is in the dated sections above (for example the chat page layout, under Oct 15).

### Signing in to the dashboard

- **A frontend and an API on unrelated domains.** The cookie is `SameSite=Strict`, so both must be on one host or on subdomains of one domain. A cookie that crosses sites is blocked by Safari and by Chrome's stricter modes, so the real fix is to serve the API through the frontend's own address (a proxy, or one Ingress host with the API under a path). Not needed here: the Ingress can simply use one site.
- **End every session when a password changes, or "sign out everywhere".** Signing out revokes the one session it is used from. A password change leaves other signed-in browsers signed in until their tokens expire (12 hours).
- **Other origins for the API.** `CORS_ORIGINS` exists but is not passed through `docker-compose.yml`; only `APP_BASE_URL` is allowed. `"*"` is refused on purpose: with a cookie session it would let any site act as the signed-in user.

### Signing in to the gateway

- **OAuth for team members.** Today the gateway accepts API keys and chat tokens only. The 401 it sends already advertises an OAuth server, which does not exist. Room is left for it: `gateway/app/auth.py` takes a list of authenticators, and the SDK already serves the challenge and the protected-resource metadata. To build: the backend becomes the authorization server (client registration, a consent page, PKCE, refresh rotation), and the gateway checks the token's audience (`validate_token_resource=True`).
- **Cache who a token belongs to.** The gateway asks the backend on every request. A revoked key stops at once, but a backend outage refuses every MCP request. A cache of a few seconds, cleared by a revocation notice over Redis, would loosen that.
- **Block refused logins per token prefix too.** The block is by address, so callers behind one NAT, or behind a proxy that `FORWARDED_ALLOW_IPS` does not name, share it: one bad client gets everyone a 429 for a minute.
- **Refused-login counter as a token bucket.** It is still a fixed one-minute window, so a burst across the boundary can reach twice the limit. Only the per-user call limit was moved to a bucket.

### Rate limits

- **A hard cap per minute.** The token bucket allows a burst: with a full bucket a user can make about 240 calls in the first minute (120 at once, 120 refilled), then 120 a minute. If the limit must hold for every 60 seconds, use a sliding window.
- **Limits per role, per tool or per server.** There is one number for everyone (`CALLS_PER_MINUTE`), and it is not passed through `docker-compose.yml`, so the default of 120 applies there.
- **Limit requests other than tool calls.** Only `tools/call` is limited. Listing tools is cheap for the gateway, but every request still makes the backend look the token up.

### The gateway's audit log

- **Retention.** Nothing is ever pruned. Needs a way to close a period (monthly partitions, a chain per period, a signed export) and a legal hold. The planned `chain_id` column was not added.
- **A second copy outside the database.** Each event is also printed to the container log with its hash, but nothing ships those logs anywhere. Until something does, rows cut off the end of the table go unnoticed. Signed checkpoints written to object storage were planned and not built.
- **Batch the writes.** Events are written one transaction at a time, in turn. Fine for a team (30 calls at once took under a second); batch them when calls reach hundreds a second.
- **Write the intent before the call.** If the log database dies while a call is in flight, that one call is only in the container log (`NOT STORED`). A row written before the call and one after it would close that.
- **Calls the client abandons.** Not checked: a call whose client disconnects midway may leave no row.
- **Detail level per category.** `AUDIT_DETAIL` is one setting for every tool call.
- **A quieter level for routine events.** Listing tools and successful sign-ins are not recorded. A DEBUG level with a minimum-level setting would allow them without noise.
- **A request id** on each row, to match it with the gateway's and the upstream's own logs.
- **Metrics** (`/metrics` on the private port: calls, errors, durations, queue size). Left out because nothing scrapes them yet.
- **A break-glass switch.** Registry changes are refused while the audit log cannot record them. So an upstream cannot be disabled during such an outage.
- **Outbox table for admin events.** Write the event into the Platform DB in the same transaction as the change, and deliver it to the gateway in the background. That closes the last gap (the backend dies between recording a change and committing it, so no `failed` event is sent). It costs a table, a delivery loop with retries and duplicate handling in the gateway, and it gives up "no change while the audit log is down".

### Registry watch

Basic version built 11 Oct: the gateway compares each registry read with the last one and records the difference. Its limits were weighed on 11 Oct and none needs fixing for the assignment: every change made through the API is already recorded with who made it, and the watch only adds a second, independent view. To grow it:

- remember the last-seen registry across restarts (in Redis), so a direct database edit made while the gateway was down is noticed; today the first read after a start is never compared
- write one row per change when several replicas run; today each replica writes its own
- tell "disabled" from "deleted", and see name-only edits; today the gateway loads only enabled servers, without their names
- link an `admin` event to the `registry` event it caused with an id, instead of matching by server and time
- raise an alert when a `registry` change has no `admin` event; today only someone reading the log sees it
- record each edit when several land within one refresh; today they show as one net change

### Adding an MCP server

- **A guard on the address an admin types.** Only the scheme is checked today (`http` or `https`). So an admin, or someone with a stolen admin session, can register the database (`http://postgres:5432`), the gateway's private port or a cloud machine's credentials address (`http://169.254.169.254/`) as a "server". The gateway then connects from inside the cluster, the Servers page says whether it answered, and that server's stored credential header is sent there. This is server-side request forgery (SSRF). To build:
  - look the host name up and judge the address it points to, not the name;
  - never allow loopback or link-local addresses (the credentials address is link-local);
  - allow a private address only when the host name is on an operator's list (a setting; our own servers such as `woo-mcp` go on it), because a plain "no private addresses" rule would block them;
  - check when a server is added or edited and again on every connection, and connect to the address that passed, because a name can point somewhere else later;
  - keep refusing redirects. The gateway's HTTP client does not follow them today (the library's default, `gateway/app/upstream.py`); say so in code when the guard is built.

  The Kubernetes step's NetworkPolicy on the gateway's outgoing traffic draws the same line in the network and is the stronger of the two. Name both in the threat model note.

### Team, key and store changes (`admin_events`)

- **Sign-ins and password changes** are not recorded, only team, key and store changes.
- **A hash chain.** The table is append-only by trigger, but unlike the gateway's log nothing shows whether the database owner switched the trigger off and edited a row. The Audit page's tamper check does not cover it.

### The Audit page

- **One list for both sources.** Gateway events and team, key and store changes are separate views, each with its own paging.
- **Search and more filters:** by user, server, tool or date. Today it is one category and one level at a time.
- **Export** to CSV or JSON, and **live updates**; today there is a Refresh button.
- **Automated tests.** The frontend has none. The page was checked by hand in a headless browser, and its "the log was changed" message was never seen there, only the endpoint behind it.

### The chat agent

- **The confirmation trusts each server's own word.** A tool runs without asking when its server marks it "read only", and that mark is a hint the server sends about itself. A server an admin adds later could mark a tool that deletes things as read only. The cure is a "trusted" switch per server in the registry, with hints believed only from trusted servers. Say so in the threat model note.
- **A planted instruction still steers the model.** The prompt says tool descriptions and results are data, never instructions (added 13 Oct). Tried the same day with an instruction planted in a customer's name, 5 runs each on `openai/gpt-oss-20b`: the model asked to cancel an order 5 times of 5 without the line and 5 of 5 with it. Stronger wording, and a reminder after each tool result, brought it down to about half at best. What stops the change is the confirmation before a write: a planted instruction ends in a box a person must approve. A planted instruction that only needs read tools has no such stop. To try once Bifrost is in: a stronger model. Say so in the threat model note.
- **No second model to fall back on.** Groq's free tier allows 8,000 tokens a minute, and a busy minute ends in "the chat model is not available". Earlier turns are already sent as text only to keep requests small.
- **No streaming and no stop button.** A long answer shows only the typing dots until it is whole. Behind an Ingress, a run longer than the proxy's timeout (often 60 seconds) would be cut.
- **The frontend's check in headless Chrome was done by hand,** not kept as a test.
- **What a tool returned** (customer names and emails included) is stored in plain text for as long as a chat is kept: one to two days.

### Stores from the dashboard

- **More than one store worker.** Two copies would pick up the same store; claim rows with `FOR UPDATE SKIP LOCKED` first.
- **Fulfilment for dashboard stores.** They get none, because one fulfilment database serves one store (keyed by the store's order number). One database per store, made by `db-init` or the worker, would allow it.
- **Pod Security admission on store namespaces** (`pod-security.kubernetes.io/enforce: baseline`, set by the worker and required by the admission policy), so a compromised worker could not start a privileged pod there.
- **A live progress stream** instead of the page reloading every 3 seconds.
- **The store cap is count-then-insert:** two creates at the same instant can pass it by one.
- **A server already called `woo-<subdomain>`** (added by hand) makes the worker skip registration.
- **ARM servers:** the worker image downloads amd64 `helm` and `kubectl`.
- **Docker Compose has no worker,** so stores made there stay pending.
- **Upgrading stores made on the dashboard** is by hand, one `helm upgrade --reset-then-reuse-values` per store (README, "Upgrade and roll back"); nothing rolls a chart change out to them, and the worker image carries its own copy of the chart.

### Tests and housekeeping

- **A throwaway database for the end-to-end tests.** Each run leaves members, stores and audit rows in the dev database; with `SMTP_PASSWORD` set it sends real emails to made-up addresses, and with `GROQ_API_KEY` set its chat messages reach the model (`SMTP_PASSWORD= GROQ_API_KEY= ./run_tests.sh all` avoids both).
- **Not tested:** several gateway replicas at once; Postgres going down while the gateway runs. The Stores page was used by hand on k3d (20 Oct), not kept as a test. (`fulfilment-mcp`'s SQL is tested against the real Postgres by `./run_tests.sh all`, in `fulfilment-mcp/tests/test_database.py`; not yet run, 16 Oct: Docker was not on the machine the code was written on.)
- **No automated end-to-end test with a real WordPress.** The WooCommerce checkpoint was run once by script on 12 Oct and is not in `./run_tests.sh all`; only the store's registration is checked there. Add the scripted run with the chat step.
- **A proper HTTP client for the audit calls.** `backend/app/services/gateway_audit.py` still calls the gateway's private port with the standard library in a worker thread. The chat agent brought an async client in (the MCP SDK's); move these calls onto it.

### Already marked in the code

Each of these has a `shortcut:` comment where it lives, saying when to upgrade.

- No filter on where an upstream URL may point (`backend/app/api/routes/upstreams.py`): only the admin can add one today. What to build is under "Adding an MCP server" above.
- Two first chat messages sent in the same instant can each open a chat (`backend/app/services/conversations.py`).
- Two messages sent to one chat in the same instant run side by side and can interleave in the saved state (`backend/app/api/routes/chat.py`); the page sends one at a time.
- An email that fails to send is logged and dropped, with no retry (`backend/app/services/mailer.py`).
- `fulfilment-mcp` opens one database connection per tool call, and one fulfilment database serves one store (`fulfilment-mcp/app/main.py`).

## Open questions

- The assignment says every member can use every enabled server. A member can now be in several stores, but is still limited to those, and since 12 Oct that includes the first store: a member sees `woo-demo`'s tools only once assigned to it. Decide before the demo whether both demo users get both stores (then the cross-store question works for either, not only the admin).
- The gateway is our own (not a fork of `dunialabs/peta-core`, which was tried in a spike on 11 Oct). OAuth for members and log retention are deferred on purpose; see "Future expansion".
- The registry endpoint is `PATCH /upstream-servers/{id}` (partial edit), where the guide says PUT.

## WooCommerce New Store and MCP
#### Design choices

- A separate worker runs Helm, not the backend: the backend's code in its own image with helm and kubectl added (`backend/Dockerfile.worker`), with no HTTP port and its own Kubernetes permissions, so the public API never holds the right to create namespaces.
- Progress: the page reloads the store list every 3 seconds while a store is busy (the user's choice, 20 Oct). This replaced the SSE stream over Redis pub/sub decided on 12 Oct: much less code, and the row already holds the step.
- Each step is safe to repeat (helm upgrade --install), so a restarted worker continues from the saved step.

#### Decided on 12 Oct

- One WooCommerce MCP server per store (the same image, that store's key pair), not one server for all stores.
- Every store's server is named `woo-<subdomain>`, the first store included.
- MCP tool servers are written with the official SDK's `MCPServer` (what was called FastMCP before SDK 2). No OAuth yet, on the server or towards WooCommerce.
- Nothing about domains is configured until the whole project works locally; domain, DNS and TLS come afterwards with the production changes. No GoDaddy integration: two wildcard DNS records, set by hand, cover every store.
- Chat: the backend runs the tool loop as a LangGraph graph and calls the gateway as the signed-in user; conversations are saved in Postgres and deleted a day or two after last use; results on the left, chat on the right. The model is Groq for now; Bifrost in front of it with OpenRouter as the fallback is decided (13 Oct) and not built.
- Local Kubernetes: k3d (13 Oct), the same distribution as a k3s VPS.
- Deleting a store asks for its name again and a reason, and both are recorded.
- A store that already exists is never created a second time. Registering it again connects to the one that is there; on Kubernetes its pods are started again if they are down, and its data is kept. Only an explicit delete removes a store's pods. Built on 12 Oct for the registry (the first store and its server are left as they are when they already exist), and on 20 Oct for the cluster: every worker step is safe to repeat.
- Servers 2 and 3 are Risk check and Fulfilment (16 Oct; Product photos was chosen first and dropped the same day, when no image provider would issue a key). Both are ours, written like `woo-mcp`, and registered by hand as the admin, which is also the demo's "add a server by URL and credential" step. Fulfilment keeps its records in the platform's Postgres, in a database and role of its own, not in a third database engine. On hold until said otherwise: Prometheus and Grafana.


## Keys

Every secret in the project is a random value from the operating system's cryptographic random generator, reached through Python's secrets module (or os.urandom). None is typed by hand or derived from another value.

Generated once, kept in .env

┌────────────────────────────────────────┬────────────────────────────────────────────────────────────┬───────────────────────────┐
│                 Secret                 │                          Command                           │           Size            │
├────────────────────────────────────────┼────────────────────────────────────────────────────────────┼───────────────────────────┤
│ WOOCOMMERCE_KEY, WOOCOMMERCE_SECRET    │ 'ck_' + secrets.token_hex(20), same with 'cs_'             │ 160 bits each             │
├────────────────────────────────────────┼────────────────────────────────────────────────────────────┼───────────────────────────┤
│ WOO_MCP_TOKEN                          │ secrets.token_urlsafe(48)                                  │ 384 bits                  │
├────────────────────────────────────────┼────────────────────────────────────────────────────────────┼───────────────────────────┤
│ MYSQL_PASSWORD, MYSQL_ROOT_PASSWORD    │ secrets.token_urlsafe(24)                                  │ 192 bits                  │
├────────────────────────────────────────┼────────────────────────────────────────────────────────────┼───────────────────────────┤
│ WP_ADMIN_PASSWORD                      │ secrets.token_urlsafe(12)                                  │ 96 bits                   │
├────────────────────────────────────────┼────────────────────────────────────────────────────────────┼───────────────────────────┤
│ INTERNAL_SERVICE_TOKEN, AUDIT_HMAC_KEY │ secrets.token_urlsafe(48)                                  │ 384 bits                  │
├────────────────────────────────────────┼────────────────────────────────────────────────────────────┼───────────────────────────┤
│ GATEWAY_ENCRYPTION_KEY                 │ base64(os.urandom(32))                                     │ 256 bits, the AES-256 key │
├────────────────────────────────────────┼────────────────────────────────────────────────────────────┼───────────────────────────┤
│ GATEWAY_DB_PASSWORD                    │ 48 hex characters, because it sits inside a connection URL │ 192 bits                  │
├────────────────────────────────────────┼────────────────────────────────────────────────────────────┼───────────────────────────┤
│ RISK_MCP_TOKEN, FULFILMENT_MCP_TOKEN   │ secrets.token_urlsafe(48)                                  │ 384 bits                  │
├────────────────────────────────────────┼────────────────────────────────────────────────────────────┼───────────────────────────┤
│ FULFILMENT_DB_PASSWORD                 │ secrets.token_hex(24), because it sits inside a URL        │ 192 bits                  │
└────────────────────────────────────────┴────────────────────────────────────────────────────────────┴───────────────────────────┘
I generated the first four rows in this session with one short Python script that appended them to .env without printing them. The last three rows were already in your .env; their lengths match the commands written in .env.example.

Generated by the backend while it runs

┌─────────────────────────────────┬──────────────────────────────────────────────────────────┬──────────────────────────────────────────────────────┐
│             Secret              │           Code (backend/app/core/security.py)            │               What the database keeps                │
├─────────────────────────────────┼──────────────────────────────────────────────────────────┼──────────────────────────────────────────────────────┤
│ Member's gateway API key        │ "umk_" + secrets.token_hex(24)                           │ SHA-256 hash and the first 12 characters for display │
├─────────────────────────────────┼──────────────────────────────────────────────────────────┼──────────────────────────────────────────────────────┤
│ Chat token                      │ "chat_" + secrets.token_urlsafe(32)                      │ SHA-256 hash; the token never reaches the browser    │
├─────────────────────────────────┼──────────────────────────────────────────────────────────┼──────────────────────────────────────────────────────┤
│ Invite and password-reset links │ secrets.token_urlsafe(32)                                │ SHA-256 hash                                         │
├─────────────────────────────────┼──────────────────────────────────────────────────────────┼──────────────────────────────────────────────────────┤
│ Member's temporary password     │ secrets.token_urlsafe(12)                                │ bcrypt hash                                          │
├─────────────────────────────────┼──────────────────────────────────────────────────────────┼──────────────────────────────────────────────────────┤
│ Login session                   │ A JWT signed with JWT_SECRET (HS256); signed, not random │ Nothing; only revoked ones are noted in Redis        │
└─────────────────────────────────┴──────────────────────────────────────────────────────────┴──────────────────────────────────────────────────────┘

How each is stored

- Hashed, when the system only has to check it: API keys, chat tokens, link tokens (SHA-256) and passwords (bcrypt). A stolen database gives nothing usable.
- Encrypted, when the system has to send it onward: the gateway must present each server's MCP token, so it is stored with AES-256-GCM. The key is in GATEWAY_ENCRYPTION_KEY, outside the database.
- Plain in .env, when a service needs it at start-up. .env is gitignored; in Kubernetes these become Secrets.

A likely follow-up is why API keys use plain SHA-256 and not bcrypt. bcrypt is slow on purpose to protect short human passwords from guessing. A 192-bit random key cannot be guessed, so a fast hash is enough and keeps every gateway call quick.


## Woocommerce Key and Secrets
WooCommerce only provides a key when a person clicks "Add key" in wp-admin, and this store has to set itself up with nobody clicking. That is the whole reason.

- No person in the loop. docker compose up must bring the store up ready to use, and later stores will be created from the dashboard. Neither has someone sitting in wp-admin.
- WooCommerce shows the secret only once. It appears on the screen at creation and is not shown again, so a script cannot fetch it afterwards. Something would have to catch it at that moment and hand it to woo-mcp.
- There is no command for it. WooCommerce has no supported command-line or API way to create a REST key; the only built-in path is that wp-admin button.
- Generating first makes re-runs safe. The script inserts the same pair every time, so running it twice changes nothing. Letting WooCommerce generate would make a new key on every run.
- woo-mcp knows its credentials from the start. Both sides read the same two values from .env, so nothing waits for a key that does not exist yet.

The key is no weaker for it. I checked the installed plugin's source, and the "Add key" button does this:

$consumer_key    = 'ck_' . wc_rand_hash();   // bin2hex( random_bytes( 20 ) )
$consumer_secret = 'cs_' . wc_rand_hash();

That is 20 random bytes as 40 hex characters, the same as secrets.token_hex(20). The script then stores the pair the way WooCommerce does, with the key hashed.

The cost is that the script writes straight into WooCommerce's table instead of going through a supported interface, so a future WooCommerce version could break it.

The manual way still works too: click "Add key" in wp-admin, paste the two values into .env as WOOCOMMERCE_KEY and WOOCOMMERCE_SECRET, and restart woo-mcp. Do not run wp-init again after that: it would register the .env pair a second time under the label relay-mcp.


A repeated create cannot make a duplicate product. A true transaction is not possible here, because WooCommerce's REST API has no transactions or idempotency keys. What it does have is a rule that refuses a second product with the same SKU, even when requests arrive at once. So create_product now sends a SKU made from the product name, and a repeat is refused with the id of the product already there. Against a real store:
   - Eight creates of the same name at once made one product.
   - A create that I abandoned after 50 ms still went through; the retry was refused with that product's id, and the store held exactly one.
   - Renaming a product frees its old name and takes the new one.
   - A SKU the merchant chose is never changed.

Before. wp-init.sh runs every time you do docker compose up. Each time, it set the time zone, turned shipping off, enabled cash on delivery, turned "coming soon" off, and added Sample Mug if the store had no products. So if you changed any of those in wp-admin (say, enabled shipping), the next start silently put it back.
Now. The script does those five things once. When it finishes, it saves a small flag in the store's own database (an option named relay_store_set_up with the value yes). On every later start it sees the flag and skips that whole block, so whatever you set in wp-admin stays.
"On a throwaway store I changed those settings…" That was my test. I built a second, temporary store from scratch, turned "coming soon" on, changed the time zone to UTC and deleted every product, then ran the script again. All three stayed as I had left them and no Sample Mug came back. I then deleted that temporary store; your real one was not touched by the test.
"Your own store ran them one last time…" Your demo store was created before this change, so it had no flag yet. When I restarted your stack, the script found no flag, applied the five settings one more time, and then wrote the flag. From the next start onward your store's settings are left alone.
Two things the script still does on every start, on purpose, because they come from .env: it sets the wp-admin password to WP_ADMIN_PASSWORD if it differs, and it registers the REST key pair.