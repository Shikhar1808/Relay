# Timeline

Progress against the plan in `extras/Urumi FDE — Full Implementation Guide.pdf`
and the assignment in `extras/Urumi AI FDE Internship - Round 1_ MCP Gateway on Kubernetes.pdf`.

- **Deadline:** 26 Oct 2026
- **Last updated:** 11 Oct 2026
- **Now:** the gateway runs (`gateway/`) and passes its end-to-end test; next is its audit log (MCP logs database, append-only, hash chain)

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
- [x] Upstream server endpoints: list, add, edit, disable, remove (`/upstream-servers`)
- [x] AES-256-GCM encrypt/decrypt helper, used when saving and loading upstream credentials
- [ ] MCP logs database with `audit_log` table
- [x] **Checkpoint:** create a user, issue an API key, add an upstream server with encrypted credentials

## Oct 12–13: MCP gateway core

- [x] Streamable HTTP server at `/mcp` (separate service, official `mcp` SDK 2.3, stateless)
- [x] Resolve an API key or chat token to a user (`POST /internal/auth/resolve`)
- [x] Gateway auth that uses it on every request, with a service token on `/internal/*`
- [x] Upstream registry: load enabled upstreams at startup and on change (every 30 s and on a Redis message)
- [x] `tools/list` with concurrent fan-out, 5 s timeout per upstream, `slug__tool` names (fan-out runs in the background; requests read a snapshot)
- [x] `tools/call` routing: strip prefix, find upstream, add credential, 30 s timeout, store check
- [ ] Audit log entry on every tool call
- [ ] **Checkpoint:** call a mock upstream through the gateway and see the audit entry

## Oct 14: WooCommerce MCP server

- [ ] WordPress and WooCommerce running in Docker
- [ ] MCP server over Streamable HTTP with 7 tools (list/get/create/update product, list/get order, update order status)
- [ ] Settings from `WOOCOMMERCE_URL`, `WOOCOMMERCE_KEY`, `WOOCOMMERCE_SECRET`
- [ ] **Checkpoint:** `list_products` through the gateway, with an audit entry

## Oct 15: Chat agent, LiteLLM, dashboard shell

- [ ] LiteLLM proxy with one provider
- [x] Short chat token issued and stored in Redis and the database (inside `POST /chat/message`)
- [ ] `/chat/message` calls the LLM with the gateway's tools (currently a stub reply)
- [ ] Streaming replies, and showing which tools were called
- [x] Dashboard shell: sign in, side navigation, Team, API key, Chat, Account, Stores pages
- [ ] Dashboard: Servers page
- [ ] Dashboard: Audit log page
- [ ] **Checkpoint:** ask a question in chat, the agent calls a WooCommerce tool, the reply streams back

## Oct 16: Buffer and integration test

- [ ] Full definition-of-done flow by hand
- [ ] Second and third upstream servers (GitHub MCP, one small custom server)
- [x] API key revocation rejected at the gateway
- [x] Disabling an upstream removes its tools

## Oct 17–18: Kubernetes core

- [ ] Helm charts: Postgres, Redis, gateway/backend, dashboard, WordPress with MySQL
- [ ] `values-local.yaml`
- [ ] Readiness and liveness probes on every workload
- [ ] Resource requests and limits on every container
- [ ] All secrets in Kubernetes Secrets
- [x] Container images for backend, frontend and gateway (non-root, health endpoints)
- [ ] **Checkpoint:** everything running in local Kubernetes

## Oct 19–20: Ingress and WordPress init

- [ ] NGINX Ingress controller, Ingress for storefront and dashboard
- [ ] Local domain instructions (`/etc/hosts`)
- [ ] wp-cli init Job (Helm post-install hook)
- [ ] `values-prod.yaml`
- [ ] **Checkpoint:** definition-of-done flow over Ingress with two users

## Oct 21: Health checks and audit log UI

- [x] Background upstream health check (30 s, cached in Redis as `upstream:<id>:status`)
- [ ] Servers page shows Connected / Unreachable / Disabled
- [ ] Kill-pod test: dead server shows Unreachable, others keep working
- [ ] Audit log page: user, server, tool, time, duration, outcome

## Oct 22–23: Bonus, multi-store provisioning

- [x] Stores page and endpoints: create and list (stub, no real provisioning)
- [ ] Delete a store
- [ ] Create Store Service: Helm install per store in its own namespace
- [ ] Provisioning state machine with retry and recovery
- [ ] Automatic MCP registration per store
- [ ] NetworkPolicies, ResourceQuota, LimitRange per store namespace
- [ ] Clean teardown

## Oct 24: Stand-out items and notes

- [x] Rate limits per user and per IP
- [ ] Per-role tool permissions
- [ ] Prompt injection check on tool descriptions
- [ ] Audit log heatmap
- [ ] Confirmation step for write tools in chat
- [ ] Threat model note
- [ ] System design and tradeoffs note

## Oct 25–26: Polish, docs, video

- [ ] Root `README.md`: local setup, VPS setup, placing an order, connecting an MCP client, using chat
- [ ] Demo video
- [ ] Final definition-of-done run with two users
- [ ] Submit the form

## Done outside the plan

- Invite, password reset and notice emails (Resend over SMTP)
- Change password, forgot password, set password by emailed link
- Members can belong to several stores (for cross-store questions); chat and `/internal/auth/resolve` return `store_ids`, and the gateway must filter on them
- Chat and dashboard rate limits no longer share one bucket per user
- Gateway hardening beyond the guide: no anonymous path, tokens in the URL refused, Host allowlist, health on a separate unpublished port, read-only database role, read-only container filesystem
- Tests: `./run_tests.sh` runs the unit tests of both services (pytest), `./run_tests.sh all` adds the two end-to-end tests (`backend/scripts/smoke_test.py`, `gateway/tests/integration_test.py` with mock upstreams)

## Open questions

- The assignment says every member can use every enabled server. A member can now be in several stores, but is still limited to those. Decide before the demo whether both demo users get both stores (then the cross-store question works for either, not only the admin).
- The gateway is our own (not a fork of `dunialabs/peta-core`, which was tried in a spike on 11 Oct). Still to build in it: the audit log (levels, severity, append-only, hash chain), per-user rate limits. Deferred on purpose: OAuth for members and log retention; the code leaves room for both.
- The registry endpoint is `PATCH /upstream-servers/{id}` (partial edit), where the guide says PUT.
- Chat tokens are not revoked on sign-out (the guide says they should be).
