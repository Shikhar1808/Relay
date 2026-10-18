# Urumi: WooCommerce hosting with a team MCP gateway

A WooCommerce store on Kubernetes, with a team MCP gateway in front of it. An admin
registers MCP servers once in a dashboard. Every team member then uses all of them
through one MCP endpoint, with their own API key, and through a chat agent in the
dashboard. Every tool call is in an audit log, under the person who made it.

| Layer | What it is | Code |
|---|---|---|
| Store | WordPress and WooCommerce, with MySQL | `helm/store`, `woo-mcp/deploy/wp-init.sh` |
| WooCommerce MCP server | Our own server: products and orders as 7 tools, over Streamable HTTP | `woo-mcp/` |
| Gateway and dashboard | The gateway (one `/mcp` endpoint for every server), the platform API (FastAPI) and the dashboard (Next.js) | `gateway/`, `backend/`, `frontend/` |
| Chat agent | A LangGraph tool loop in the platform API, calling tools through the gateway as the signed-in user | `backend/app/agent/` |
| Servers 2 and 3 | Risk check (scores orders) and Fulfilment (ships them), our own | `risk-mcp/`, `fulfilment-mcp/` |

The two required notes:

- [System design and tradeoffs](docs/system-design.md): architecture, how tool listing,
  routing and failures work, and what changes for production.
- [Threat model](docs/threat-model.md): shared credentials, why client tokens stay at the
  gateway, untrusted servers, and what one member can do to another.

Each service has its own README with the details: [gateway](gateway/README.md),
[woo-mcp](woo-mcp/README.md) (including how it talks to the store and why),
[risk-mcp](risk-mcp/README.md), [fulfilment-mcp](fulfilment-mcp/README.md),
[platform API](backend/IMPLEMENTATION.md).

```
browser, MCP client ──► Traefik (Ingress): the only way in from outside
   app.<domain>   ──► dashboard (Next.js)                                  namespace urumi
   api.<domain>   ──► platform API (FastAPI): sign-in, team, stores,       namespace urumi
                      servers, audit, and the chat agent ─────┐
   mcp.<domain>   ──► gateway /mcp ◄──────────────────────────┘ (as the signed-in user)
                        ├──► risk-mcp                                      namespace urumi
                        ├──► woo-mcp ──► WordPress ──► MySQL               namespace store-demo
                        └──► fulfilment-mcp ──► Postgres (its own database)
   demo.<domain>  ──► WordPress: the storefront and wp-admin               namespace store-demo

gateway ──► platform API /internal/auth/resolve: whose key is this, and which stores?
platform API, gateway ──► Postgres and Redis                               namespace urumi
```

## Local setup (k3d)

Everything runs in a local Kubernetes cluster made with [k3d](https://k3d.io), which runs
k3s inside Docker. The same charts run on a VPS with k3s, with other values
([below](#production-like-setup-k3s-on-a-vps)).

You need Docker (with at least 4 GB of memory for it), `k3d`, `kubectl`, Helm (checked with
4.1) and Python 3. Every command runs from the repo root.

**1. A cluster** with the host's ports 80 and 443 going to its Ingress controller (Traefik,
which k3s includes):

```sh
k3d cluster create urumi -p "80:80@loadbalancer" -p "443:443@loadbalancer"
kubectl get nodes
```

**2. The six images.** The cluster runs images, so build ours and copy them in. The stock
images (Postgres, Redis, MySQL, WordPress) are downloaded by the cluster itself.

```sh
docker build -t urumi-backend:0.1.0 backend
docker build -t urumi-gateway:0.1.0 gateway
docker build -t urumi-frontend:0.1.0 frontend
docker build -t urumi-woo-mcp:0.1.0 woo-mcp
docker build -t urumi-risk-mcp:0.1.0 risk-mcp
docker build -t urumi-fulfilment-mcp:0.1.0 fulfilment-mcp
k3d image import -c urumi urumi-backend:0.1.0 urumi-gateway:0.1.0 urumi-frontend:0.1.0 urumi-woo-mcp:0.1.0 urumi-risk-mcp:0.1.0 urumi-fulfilment-mcp:0.1.0
```

**3. The secrets.** No secret is in the charts or in git. This writes two git-ignored files
with new random values, one per namespace; the Groq key is for the chat agent (a free key
from [console.groq.com](https://console.groq.com)), and both keys may be left out:

```sh
GROQ_API_KEY=<your key> python scripts/make_k8s_secrets.py
kubectl create namespace urumi
kubectl create namespace store-demo
kubectl label namespace store-demo urumi.io/store=true
kubectl -n urumi create secret generic urumi-platform --from-env-file=.env.k8s-platform
kubectl -n store-demo create secret generic store-demo --from-env-file=.env.k8s-store-demo
```

The label marks `store-demo` as a store's namespace: only then may its fulfilment server
reach the platform's Postgres. A store's namespace is always `store-<subdomain>`, and its
Secret has the same name.

**4. The two charts.** `helm/platform` is the dashboard, API, gateway, risk server and
databases; `helm/store` is one store with its two MCP servers. The setup scripts are passed
in from the repo, so Compose and Kubernetes run the very same ones:

```sh
helm upgrade --install platform ./helm/platform -n urumi -f helm/platform/values-local.yaml --set-file scripts.dbInit=gateway/deploy/db-init.sh
helm upgrade --install store-demo ./helm/store -n store-demo -f helm/store/values-local.yaml --set-file scripts.wpInit=woo-mcp/deploy/wp-init.sh
kubectl get pods -A -w
```

The first install takes 3 to 6 minutes, mostly MySQL setting itself up and WooCommerce being
downloaded. Pods start in any order and retry until what they need is there, so a few
restarts and failed `db-init` or `wp-init` runs on the way are normal. It is ready when every
pod is `Running` and both Jobs are `Completed`.

**5. Open it.**

| What | Address | Sign in |
|---|---|---|
| Dashboard | http://app.127.0.0.1.nip.io | `admin@example.com`, password `ADMIN_PASSWORD` in `.env.k8s-platform` |
| Storefront | http://demo.127.0.0.1.nip.io | |
| WooCommerce admin | http://demo.127.0.0.1.nip.io/wp-admin | `admin`, password `WP_ADMIN_PASSWORD` in `.env.k8s-store-demo` |
| MCP gateway | http://mcp.127.0.0.1.nip.io/mcp | a member's API key |
| Platform API | http://api.127.0.0.1.nip.io/docs | |

The WooCommerce server is already registered as the first server, `woo-demo`: the API does
that when it starts, as it would for any new store.

**6. Register servers 2 and 3**, as the admin, on the dashboard's **Servers** page. This is
the "add a server by URL and credential" step, done by hand on purpose:

| Name | Slug | Address | Credential | Store |
|---|---|---|---|---|
| Risk check | `risk` | `http://risk-mcp:8000/mcp` | `Bearer ` + `RISK_MCP_TOKEN` from `.env.k8s-platform` | Every store |
| Demo Store shipping | `fulfilment-demo` | `http://fulfilment-mcp.store-demo.svc.cluster.local:8000/mcp` | `Bearer ` + `FULFILMENT_MCP_TOKEN` from `.env.k8s-store-demo` | Demo Store |

Paste only the value after `=` from those files, after the word `Bearer` and a space. Each
server shows **Connected** with its tools within a few seconds.

**7. Check it.**

```sh
helm test store-demo -n store-demo     # a pod that is not the gateway must be refused by the store's MCP servers
```

On Windows: run the commands in PowerShell, and set `$env:GROQ_API_KEY="<your key>"` before
`python scripts/make_k8s_secrets.py` instead of the inline form. If `kubectl` cannot reach a
new k3d cluster, its kubeconfig may name `host.docker.internal` at an old address: point it at
the local port with `kubectl config set-cluster k3d-urumi --server=https://127.0.0.1:<port>`
(the port is in `docker ps`, on `k3d-urumi-serverlb`).

### Local domain approach

The names are under [nip.io](https://nip.io), a public DNS service where
`anything.127.0.0.1.nip.io` resolves to `127.0.0.1`. That gives stable names with no hosts
file and no setup, and Traefik routes each name to its service. The dashboard (`app.`) and
the API (`api.`) must be on the same site, because the sign-in cookie is `SameSite=Strict`:
two subdomains of one domain are. A cluster on another machine uses that machine's address
instead: `--set domain=192.168.1.20.nip.io` on both charts.

Without internet access (nip.io needs DNS), add the four names to the hosts file instead:
`127.0.0.1 app.127.0.0.1.nip.io api.127.0.0.1.nip.io mcp.127.0.0.1.nip.io demo.127.0.0.1.nip.io`.

## Use it

### Place an order

On the storefront, open a product, **Add to cart**, then **Checkout**. Fill in the address,
choose **Cash on delivery** and **Place order**. The order is in WooCommerce admin under
**WooCommerce > Orders**, and the chat or any MCP client finds it with "what orders came in
today?". The store is set up in Indian rupees and starts checkout on India; both are store
settings in `helm/store/values.yaml`.

### Invite a member and give them a key

As the admin, on **Team**: **Add member** with a name and an email. The member is emailed a
link to set a password (when `SMTP_PASSWORD` is set) and the page shows a temporary password
once. On **Stores**, **Manage members** adds them to a store: a member sees a store's
servers and data only after that. The member signs in and issues their own key on
**API key**. The admin can issue or revoke any member's key on **Team**.

On plain http the **Copy** button does nothing (browsers allow the clipboard only on https
or localhost): select the key and copy it by hand.

### Connect an external MCP client

Only the gateway's address and your own key are needed. Every enabled server's tools appear,
named `<server>__<tool>`, for example `woo-demo__list_orders` and `risk__score_orders`.

Claude Code:

```sh
claude mcp add --transport http urumi http://mcp.127.0.0.1.nip.io/mcp --header "Authorization: Bearer umk_..."
```

Cursor, in `~/.cursor/mcp.json`:

```json
{ "mcpServers": { "urumi": { "url": "http://mcp.127.0.0.1.nip.io/mcp", "headers": { "Authorization": "Bearer umk_..." } } } }
```

MCP Inspector: `npx @modelcontextprotocol/inspector`, transport **Streamable HTTP**, the same
URL, and an `Authorization` header with `Bearer umk_...`.

Then ask "what orders came in today?", or something that needs two servers: "score today's
orders for risk" lists the orders on `woo-demo` and scores them on `risk`. A revoked key gets
`401` on its next call.

### Use the chat agent

Open **Chat** and ask about the store: "what orders came in today?", "create a product called
Blue Mug for 299". The agent answers by calling the same tools through the gateway, with your
permissions, so its calls are in the audit log under your name. Results show on the left, with
the tools it called; the conversation is on the right. Before any change (a new product, an
order's status) it asks, and nothing changes until you click **Approve**.

The chat model is Groq's `openai/gpt-oss-20b` on its free tier (8,000 tokens a minute). A
question that needs many tool calls in a row can hit that limit and get "the chat model is not
available right now"; asking again a minute later works. The model is three settings
(`llmBaseUrl`, `llmModel`, and the key in the Secret), so another OpenAI-compatible provider
is a change of values.

### Manage servers

**Servers** lists each server with its status (Connected, Unreachable, Disabled) and its tools,
from the gateway's last check (every 30 seconds, and right after a change). **Add a server**
takes a name, a slug, an address and an optional credential (any header, usually
`Authorization`); **Edit**, **Disable** and **Remove** are on each card. A disabled server's
tools disappear from the chat and from every API key at once. The credential is stored
encrypted and is never shown again.

To see a dead server: `kubectl -n urumi scale deploy/risk-mcp --replicas=0`. Within 30 seconds
it shows **Unreachable**, a call to it fails at once with a clear error, and the other servers
keep working. `--replicas=1` brings it back. (Deleting the pod alone is not enough to see it:
Kubernetes starts a new one within seconds.)

### The audit log

**Audit** (admin only) has every tool call with who made it, the server and tool, when, how
long it took and how it ended, plus refused sign-ins, server changes, server health and team
changes. **Check now** tells whether any row of the gateway's log was changed or removed.

## Production-like setup (k3s on a VPS)

The same two charts, with `values-prod.yaml` instead of `values-local.yaml`. Only values
change: the domain (`shikharsaxena.space`), `https`, and TLS certificates from Let's Encrypt
through cert-manager. HTTPS and the http-to-https redirect were checked on k3d by switching
`tls.enabled` on; the VPS install itself is the next step of the project.

What the server needs: Ubuntu 24.04 with a public IPv4 address, at least 4 GB of memory and
2 cores, x86-64 (the same as the machine the images are built on), and SSH with a key.

1. **Firewall:** only SSH, 80 and 443 from outside; the cluster's own ranges inside.

   ```sh
   sudo ufw default deny incoming
   sudo ufw allow 22/tcp && sudo ufw allow 80/tcp && sudo ufw allow 443/tcp
   sudo ufw allow from 10.42.0.0/16 to any && sudo ufw allow from 10.43.0.0/16 to any
   sudo ufw enable
   ```

2. **DNS:** one wildcard `A` record, `*` → the server's IPv4 address. It covers `app`, `api`,
   `mcp`, `demo` and every later store.

3. **k3s**, with Traefik keeping each visitor's real address (`externalTrafficPolicy: Local`),
   so the audit log and the sign-in limit see real addresses rather than the node's:

   ```sh
   curl -sfL https://get.k3s.io | sh -
   sudo tee /var/lib/rancher/k3s/server/manifests/traefik-config.yaml >/dev/null <<'EOF'
   apiVersion: helm.cattle.io/v1
   kind: HelmChartConfig
   metadata: { name: traefik, namespace: kube-system }
   spec:
     valuesContent: |-
       service:
         spec:
           externalTrafficPolicy: Local
   EOF
   ```

4. **kubectl from your machine** through an SSH tunnel, so the Kubernetes API (6443) is never
   open to the internet: copy `/etc/rancher/k3s/k3s.yaml` from the server, run
   `ssh -N -L 6443:127.0.0.1:6443 <user>@<server>`, and point `KUBECONFIG` at the copy.

5. **The images:** build them as in local step 2, then copy them to the server:

   ```sh
   docker save -o urumi-images.tar urumi-backend:0.1.0 urumi-gateway:0.1.0 urumi-frontend:0.1.0 urumi-woo-mcp:0.1.0 urumi-risk-mcp:0.1.0 urumi-fulfilment-mcp:0.1.0
   scp urumi-images.tar <user>@<server>:
   ssh <user>@<server> 'sudo k3s ctr images import urumi-images.tar && rm urumi-images.tar'
   ```

6. **cert-manager and two ClusterIssuers**, Let's Encrypt's staging server (test certificates,
   generous limits) and its production server (5 certificates a week for the same names), both
   answering the HTTP-01 challenge through Traefik:

   ```sh
   helm install cert-manager oci://quay.io/jetstack/charts/cert-manager --version v1.21.1 --namespace cert-manager --create-namespace --set crds.enabled=true
   kubectl apply -f helm/cluster-issuers.yaml    # put your email in it first
   ```

7. **Fresh secrets** (never the laptop's), the namespaces, the label and the two Secrets: local
   step 3, with `SMTP_PASSWORD` set too if invites should be emailed. Keep the two files safe.

8. **Install** with the production values:

   ```sh
   helm upgrade --install platform ./helm/platform -n urumi -f helm/platform/values-prod.yaml --set-file scripts.dbInit=gateway/deploy/db-init.sh --wait --timeout 15m
   helm upgrade --install store-demo ./helm/store -n store-demo -f helm/store/values-prod.yaml --set-file scripts.wpInit=woo-mcp/deploy/wp-init.sh --wait --timeout 15m
   kubectl get certificates -A    # platform-tls and storefront-tls: READY True after a minute or two
   ```

9. **Real certificates:** once the staging ones work, set `clusterIssuer: letsencrypt-prod` in
   both `values-prod.yaml` files and run step 8 again.

10. **Email:** verify the domain in [Resend](https://resend.com) (it gives DNS records to add);
    `smtpFrom` in `values-prod.yaml` already uses that domain.

What differs from local, and why, is in the [design note](docs/system-design.md#what-changes-for-production).

## Upgrade and roll back

An upgrade is the install command again, after building and importing new images (with a new
tag in `images:`, or the same tag and `kubectl rollout restart`). Helm keeps every revision:

```sh
helm history platform -n urumi
helm rollback platform <revision> -n urumi
```

What makes this safe:

- **Data is not in a release.** Postgres, Redis and MySQL keep their data on volumes made from
  `volumeClaimTemplates`, and WordPress's volume is marked `helm.sh/resource-policy: keep`, so
  neither a rollback nor `helm uninstall` deletes it.
- **The setup Jobs run again on every revision** (`db-init-r<revision>`, `wp-init-r<revision>`),
  and both scripts are safe to repeat.
- **The platform API applies database migrations when it starts**, one copy at a time
  (`strategy: Recreate`). Migrations only move forward: rolling back to an image older than a
  migration needs that migration undone first (`alembic downgrade`).
- **After a `kubectl edit` or `patch`** of an object the chart owns, Helm 4 refuses the next
  upgrade until `--force-conflicts` is added.

## Development without Kubernetes

`docker-compose.yml` runs the same services for development: copy `.env.example` to `.env`,
fill it in (each value says how to generate it), then `docker compose up -d --build`. The
dashboard is on http://localhost:3000, the API on :8000, the gateway on
http://localhost:8080/mcp and the store on :8081.

Tests: `./run_tests.sh` runs the unit tests of all five services (456, no running stack
needed); `./run_tests.sh all` also starts the stack and runs the end-to-end tests, and
fulfilment-mcp's tests against the real Postgres.

## Bonus and stand-out items

| Item | State | Where |
|---|---|---|
| Security hardening | Default-deny NetworkPolicies in both namespaces, each path opened on purpose, proven by `helm test`; containers run as non-root with no unused Kubernetes API token; server credentials encrypted at rest (AES-256-GCM); keys and tokens stored hashed; the API's internal route refused at the Ingress. No guard yet on the address an admin types (see the threat model) | `helm/*/templates/networkpolicies.yaml`, `helm/store/templates/tests/`, `backend/app/core/crypto.py` |
| Rate limits per user and per key | The gateway allows each user 120 tool calls a minute (token bucket); the API limits sign-ins by address and requests by user | `gateway/app/limits.py`, `backend/app/core/rate_limit.py` |
| Upgrades and rollback | Documented above; Jobs per revision; volumes outlive releases | this README |
| Production-like VPS | `values-prod.yaml` for both charts; https checked on k3d; the VPS install is pending | `helm/*/values-prod.yaml` |
| Observability, in part | The Servers page shows each server's status and tools; the audit log says why a call failed ("the server could not be reached", timeouts) and records each server going down and coming back. No metrics endpoint | `gateway/app/catalog.py`, `gateway/app/audit.py` |
| Multi-store, in part | A store is one install of `helm/store` in its own namespace with its own Secret, volumes and Ingress host, and its tools are named after it (`woo-<subdomain>__…`). The dashboard lists and creates store records; installing the chart from the dashboard is not built | `helm/store`, `backend/app/api/routes/stores.py` |
| Not from the brief | A tamper-evident audit log (each row chained with an HMAC; append-only by trigger); the chat asks before any change; each member sees only the servers of their stores | `gateway/README.md`, `backend/app/agent/` |

Not attempted: OAuth on the gateway, per-role tool permissions, tool search for many tools,
servers added by container image.

## Known limits

The main ones are in the two notes. The full list, with what each would take, is in
`TIMELINE.md` under "Future expansion".
