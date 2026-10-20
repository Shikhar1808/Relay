# System design and tradeoffs

How Relay is built, why, and what that costs. Setup is in the [README](../README.md); the
security side is in the [threat model](threat-model.md).

## What runs where

Two Helm charts, two kinds of namespace.

| Namespace | Chart | Runs |
|---|---|---|
| `urumi` | `helm/platform` | dashboard (Next.js), platform API (FastAPI, with the chat agent), MCP gateway, risk-mcp, the store worker, Postgres, Redis, the `db-init` Job |
| `store-<subdomain>` (`store-demo`, and one per store made on the dashboard) | `helm/store` | WordPress, MySQL, the `wp-init` Job, woo-mcp, and for the demo store fulfilment-mcp (two copies) |

The first store is installed by hand. Every later one is made from the dashboard: the store
worker installs the same chart into `store-<its subdomain>`, with its own Secret, volumes,
Ingress host and limits ([below](#stores-from-the-dashboard)).

**Public:** three names, all through Traefik, the Ingress controller k3s ships with: `app.`
(dashboard), `api.` (platform API) and `mcp.` (the gateway's `/mcp`), plus each store's own
name (storefront and wp-admin). Nothing else has an Ingress rule.

**Internal only:** everything else, held there in three ways at once:

1. No Ingress rule. The MCP servers, Postgres, Redis, MySQL and the gateway's private port
   (9090: health, and the audit log for the API) have none.
2. NetworkPolicies. Each namespace refuses every incoming connection by default, and each
   allowed path is opened on its own: woo-mcp and fulfilment-mcp accept only the gateway's
   pods, MySQL only WordPress and `wp-init`, Postgres only the API, the gateway, `db-init`, the
   store worker, and fulfilment-mcp from a namespace labelled as a store's. `helm test
   store-<subdomain>` checks the first of these from a pod that is not the gateway.
3. Their own checks. Every MCP server wants a bearer token and answers only to its own host
   name; the API's `/internal` routes want a service token, and the Ingress refuses that path
   for everyone (Traefik answers 403).

## Architecture choices

Each with its reason and its cost.

- **Our own gateway, as its own service.** It is written with the MCP SDK's low-level server,
  stateless (`stateless_http`), so any copy can answer any request. It imports nothing from
  the platform API and reads the server list from Postgres through a database role that can
  read that one table and nothing else.
  *Cost:* we wrote and test the aggregation ourselves (an open-source gateway was tried in a
  spike on 11 Oct and dropped).
- **The gateway asks the API who every token belongs to.** On each request it calls
  `/internal/auth/resolve`, which returns the user, the role and the user's stores. A revoked
  key or a removed store assignment is refused on the very next request.
  *Cost:* if the API is down, every MCP request is refused. A short cache cleared by a
  revocation message would soften that; it is not built.
- **Tool names are `<server slug>__<tool>`**, for example `woo-demo__list_orders`. Two servers
  can both offer `list_orders`, and a person or a model can tell which store a tool acts on.
  Slugs are unique, are lowercase letters, digits and single hyphens, and cannot change once
  made, so a name always splits at its first `__`. A store's WooCommerce server is always
  `woo-<subdomain>`.
- **Servers belong to a store, or to every store.** A server with a store is listed and
  callable only for that store's members (and the admin); risk-mcp has none and serves
  everyone. The gateway takes a caller's stores from the API's answer and nothing else, never
  from a tool argument or a header.
- **Upstream credentials are encrypted.** The API stores each server's credential with
  AES-256-GCM and never returns it; the gateway decrypts it to make a call. The key is in a
  Secret, outside the database.
- **The audit log has its own database** (`mcp_logs`). The gateway's role can add and read
  rows only, a trigger refuses changes and deletions for every role (the owner too), and each
  row carries an HMAC of the one before, so a changed or missing row shows. While the log
  cannot record, the gateway refuses tool calls: nothing runs unrecorded.
  *Cost:* an audit-database outage stops every tool call.
- **WooCommerce through its REST API**, not WP-CLI or the database: stock, status rules and
  hooks run as they do from wp-admin, and woo-mcp needs no access to WordPress's files or
  database. The REST key belongs to a Shop manager, not the administrator. The key pair is
  generated first and inserted by `wp-init`, because WooCommerce has no supported way to make
  one without a person clicking in wp-admin.
  *Cost:* the insert relies on WooCommerce's table layout, so WooCommerce is pinned (11.2.1).
- **The chat agent's loop runs in the platform API** (LangGraph), not in an LLM proxy. The
  model only says which tool it wants; the API makes every call through the gateway with the
  user's chat token, so the agent can do nothing the user could not, and its calls are in the
  audit log under the user. Having our code between the model and the tools is also what lets
  it ask before a change. The state of each chat is saved in Postgres after every step, so a
  pause for confirmation and a page reload work; chats are deleted one to two days after their
  last use.
  *Cost:* a loop of our own to keep, where an LLM proxy with MCP support could run the tools.
- **The model is Groq's `openai/gpt-oss-20b`**, reached in the OpenAI chat format, so a
  provider is three settings. *Cost:* Groq's free tier allows 8,000 tokens a minute, and a
  question that needs four or more tool calls in a row can run out of it (measured 18 Oct:
  about 8,500). An LLM gateway with a fallback provider is planned.
- **Servers 2 and 3 are ours and about the store.** Risk check scores orders with fixed rules
  and keeps nothing, so one copy serves every store. Fulfilment ships a store's orders and
  keeps its records in the platform's Postgres, in a database and role of its own, so its pods
  keep nothing and two copies run side by side.
- **Two charts, not one.** The platform is installed once; the store chart once per store.
  The setup scripts (`db-init.sh`, `wp-init.sh`) are passed in with `--set-file`, so Compose
  and Kubernetes run the same files (the store worker's image carries both the chart and
  `wp-init.sh`).
- **Plain Jobs per revision, not Helm hooks.** `db-init-r<revision>` and `wp-init-r<revision>`
  run on every install and upgrade and are safe to repeat. A hook would wait for every pod to
  be ready first, and the gateway cannot be ready before `db-init` has made its role.
- **Secrets are made outside the charts** with `kubectl`: from git-ignored files for the
  platform and the first store, by the store worker for later stores. The charts only name
  them, so no secret is in a values file, in git or in Helm's release record.
- **Stores are made by a worker, not by the API.** The API only writes a store's row; a
  separate pod runs `kubectl` and `helm`. So the public API never holds Kubernetes rights, and
  its image has no cluster tools.
  *Cost:* one more Deployment and image, and the page learns of progress by reloading, not
  from a stream.
- **Local names under nip.io.** No hosts file, and the dashboard and the API share one site,
  which the `SameSite=Strict` sign-in cookie needs.

## How a tool call travels

From Claude Code (or the chat agent) to the store and back, for `woo-demo__list_orders`:

1. The client sends `tools/call` to `https://mcp.<domain>/mcp` with `Authorization: Bearer
   umk_…`. Traefik passes it to a gateway pod.
2. The gateway asks the API whose key it is, and gets the user and their stores, or a refusal
   (`401`). A `Host` header it does not know gets `421`.
3. The user's rate limit (120 calls a minute, in Redis) is checked.
4. The name is split into `woo-demo` and `list_orders`. The server is looked up in the
   gateway's in-memory list, and the user must have its store. If the server lists the tool as
   admin-only (`admin_only_tools`, set on the Servers page) the user must be the admin. An
   unknown tool, another store's tool and a tool kept for admins get the same answer, "Unknown
   tool", so nobody can map other stores' servers or locks. `tools/list` applies the same two
   rules, so a tool that is hidden is also refused.
5. The gateway opens an MCP session to `http://woo-mcp.store-demo.svc.cluster.local:8000/mcp`
   with that server's stored credential. The client's own key is never sent on.
6. woo-mcp checks its token and its host name, and calls WooCommerce's REST API at
   `http://wordpress` with the store's key; WooCommerce reads MySQL.
7. The answer comes back the same way. The gateway writes one audit row (user, server, tool,
   time, duration, outcome, and the arguments with secret-looking fields removed) and returns
   the result.

## Tool aggregation, routing and failure handling

**Listing.** A background loop in the gateway reads the enabled servers from Postgres and asks
each one for its tools, all at once, each with a 5-second limit. It does this every 30 seconds,
and at once when the API publishes `upstreams:changed` in Redis after an admin's change. The
result is swapped in as one snapshot. `tools/list` only reads that snapshot, so a slow or dead
server can never slow it down: a server that did not answer at the last check is simply not
listed. Each tool keeps its own description and hints (read-only, destructive), renamed with
its server's slug.

**Calling.** A call goes to the server named in the tool, with a 30-second limit. What the
caller gets:

| What happened | Caller sees | Audit outcome |
|---|---|---|
| The tool answered | its result | `ok` (INFO), or `error` (WARNING) if the tool reported an error |
| No such tool, or another store's | "Unknown tool" | `denied` (WARNING) |
| No answer in 30 s | "<server> did not answer within 30 seconds" | `timeout` (ERROR) |
| Refused, crashed, or a bad answer | "<server> is unreachable" | `unreachable` (ERROR) |
| Over the user's rate limit | "Rate limit: at most 120 tool calls a minute" | `rate_limited` (WARNING, once a minute) |

**When a server goes down,** the next check marks it Unreachable on the Servers page, drops its
tools from every list, and writes one audit row (and one more when it comes back). The other
servers are untouched. **When an admin disables or removes one,** its tools are gone on the
next request: the API's change message makes the gateway re-read at once.

**Other failures:**

| What fails | What happens |
|---|---|
| The platform API | The gateway refuses every request, since it cannot tell who is calling |
| Redis | The gateway keeps serving without rate limits and keeps its 30-second timer; the API's first request after Redis restarts waits for it (up to about 2 seconds) instead of failing |
| The audit database | Tool calls are refused, and admin changes to the server list too |
| Postgres, briefly | The gateway keeps its last good snapshot and tries again |
| A pod | Its Deployment or StatefulSet starts a new one. fulfilment-mcp runs two copies, so one can die without a failed call |
| The chat model | The chat answers "the chat model is not available right now", never a 500 |
| The store worker | Stores already made keep working; new ones wait in `pending` until it is back, then it finishes them |

## Stores from the dashboard

A store is a row in the `stores` table, and the row is the truth. The API writes it; the
store worker (`backend/app/worker.py`, one replica) does the cluster work and moves it along:

```
POST /stores ──► pending ──► provisioning ──► ready ──DELETE──► deleting ──► (row removed)
                    ▲              │                               │
                    └── Retry ── failed ◄──────────────────────────┘
```

Every 3 seconds the worker reads the rows in `pending`, `provisioning` or `deleting`, and
runs each store in its own task, so several are made at once and one store never has two
tasks. It saves the step it is on before each one, and any error into the row, so the Stores
page (which reloads every 3 seconds while a store is busy) shows exactly where it is.

**Creating**, each step safe to repeat:

1. `kubectl apply` the namespace `store-<subdomain>`, labelled as a store's (the platform's
   NetworkPolicies key on that label), and a RoleBinding in it (below).
2. Make the store's Secret if it does not exist: six random values, sent to `kubectl` on
   stdin, never as arguments. An existing Secret is never replaced, because MySQL keeps the
   password it was first given on its volume.
3. `helm upgrade --install` the store chart, with the subdomain and name as a values document
   on stdin, `--wait --wait-for-jobs --timeout 10m --rollback-on-failure`. A release left
   "pending" by a worker killed in the middle is uninstalled or rolled back first, since Helm
   would refuse any change to it.
4. Register `woo-<subdomain>` for that store the way an admin's change is made: recorded in the
   gateway's audit log, committed, announced to the gateway. Skipped if it already exists.
5. Mark the store ready.

Because every step is safe to repeat, Retry, a restarted worker and a second create of the same
release are all the same code path: they finish the store, they never make a second one.

**Deleting** (only a ready or failed store, never the first one, which the API registers again
on every start; the name typed again and a reason): remove the
store's servers (audited, announced), so its tools leave every list first; delete the
namespace, which holds everything the chart made, the Helm release record included, and takes
each volume with its claim; then delete the row (members and servers cascade).

**What the worker may do in the cluster.** RBAC alone cannot say "only namespaces named
`store-*`", so it takes three pieces:

- a ClusterRole bound cluster-wide that only creates and removes namespaces and RoleBindings,
  and may `bind` exactly one role, `relay-store-installer`;
- `relay-store-installer`, which holds what installing the store chart needs and is bound by
  the worker inside each namespace it makes, so those rights exist only there;
- a ValidatingAdmissionPolicy that makes the API server refuse the worker's account any
  namespace or RoleBinding change outside `store-*`. Without it, the worker could bind the
  installer role in `kube-system` and take over the cluster from there.

**Per-store limits:** each store namespace has a ResourceQuota (CPU and memory requests,
memory limits, storage, claims, pods) and a LimitRange (defaults for a container that names
none, and a per-container ceiling). The demo store uses about 350m CPU, 1Gi of memory requests,
2.3Gi of limits and 7Gi of disk, under 1 CPU, 2Gi, 4Gi and 10Gi.

## Kubernetes

| Workload | Kind | Ready when | Requests / memory limit | Disk |
|---|---|---|---|---|
| Postgres | StatefulSet | `pg_isready` | 100m, 128Mi / 512Mi | 5Gi |
| Redis | StatefulSet | `redis-cli ping` | 50m, 32Mi / 128Mi | 1Gi, append-only file |
| Platform API | Deployment, `Recreate` | `/health` | 100m, 256Mi / 512Mi | |
| Gateway | Deployment | `/readyz` on 9090: the server list has been read | 100m, 96Mi / 256Mi | |
| Dashboard | Deployment | `/runtime-config` | 50m, 96Mi / 256Mi | |
| risk-mcp, woo-mcp, fulfilment-mcp (×2) | Deployment | `/healthz` | 50m, 96Mi / 256Mi each | |
| MySQL | StatefulSet | `mysqladmin ping`, with a startup check of up to 5 minutes | 100m, 512Mi / 1Gi | 5Gi |
| WordPress | Deployment, `Recreate` | `/wp-login.php` | 100m, 256Mi / 512Mi | 2Gi, kept on uninstall |
| `db-init`, `wp-init` | Job per revision | | 50m, 32Mi / 128Mi; 50m, 128Mi / 512Mi | |
| Store worker | Deployment, `Recreate`, one copy | live while it keeps touching a heartbeat file (it serves nothing, so no readiness check) | 50m, 128Mi / 1Gi | |

Every long-running workload has a readiness or a liveness check, most both. There are memory
limits and no CPU limits, so a busy pod is slowed rather than killed. Our seven images run as
user 10001 (the dashboard as `node`, 1000), with a read-only root filesystem where the image
allows it, all capabilities dropped and the default seccomp profile. Only the store worker has
a Kubernetes API token mounted; nothing else talks to the Kubernetes API.

**Start order without `depends_on`.** Every pod starts at once and retries. The API's
migrations fail until Postgres answers, so it restarts once or twice. `db-init` fails until those
migrations have made the tables; the gateway is not ready until `db-init` has made its role.
`wp-init` waits in a loop until MySQL answers, then installs WordPress and WooCommerce once.

## What changes for production

The same charts, images and Secret names. Only `values-prod.yaml` and the cluster around them
change.

| | Local (k3d) | VPS (k3s) |
|---|---|---|
| DNS | `*.127.0.0.1.nip.io`, nothing to set up | one wildcard `A` record for `*.shikharsaxena.space`, so every later store needs no new record |
| Ingress | Traefik, plain http | Traefik with TLS: cert-manager gets certificates from Let's Encrypt (staging first, then prod), `http://` gets a permanent redirect to `https://`, and a NetworkPolicy lets Traefik reach cert-manager's challenge pod |
| Client addresses | every visitor looks like the node (`10.42.0.1`) | Traefik's Service set to `externalTrafficPolicy: Local`, so the audit log and the sign-in limit see real addresses |
| Storage class | `local-path` (k3s's own), on the laptop's disk | `local-path` on the server's disk: fine for one node, with no copy elsewhere. A cloud cluster would name its block storage in `storageClass`, and backups are not built |
| Secrets | random values from `scripts/make_k8s_secrets.py` | new values from the same script, never the laptop's; the same two `kubectl create secret` commands. k3s can also encrypt Secrets at rest (`--secrets-encryption`) |
| Images | `k3d image import` | `docker save` and `k3s ctr images import`, or a registry named in `images:` |
| Sign-in cookie | not `Secure` (plain http) | `Secure`, decided by the dashboard's address being https |
| Email | written to the API's log, or Resend's test sender | Resend, from an address on the verified domain (`smtpFrom`) |
| Kubernetes API | local | reached through an SSH tunnel; port 6443 stays closed |
| New stores | installed with `helm/store/values-local.yaml` | installed with `values-prod.yaml` (https, the domain), named by the platform's `storeWorker.valuesFile` |

## Known limits

- **One fulfilment database for every store.** Its table is keyed by the store's own order
  number, so a second store's fulfilment server would share it. So stores made on the
  dashboard get no fulfilment server; one database per store is needed first (marked in
  `fulfilment-mcp/README.md`).
- **One store worker.** Two copies would pick up the same store. Claiming rows with
  `FOR UPDATE SKIP LOCKED` would allow more.
- **Docker Compose has no store worker**, so a store created there stays pending.
- **One copy of Postgres and of MySQL**, with no backups.
- **The chat on Groq's free tier** can run out of tokens on long questions (above).
- **No OAuth for members, no guard on the address an admin registers** (the last is in the
  threat model). Tool permissions are per role and name the tools to keep back; there are none
  per person.
