# WooCommerce MCP server

One WooCommerce store's products and orders as MCP tools, over Streamable HTTP
at `/mcp`. Written for this project; no existing WooCommerce MCP server or
plugin is used. A separate service: it imports nothing from `backend/` or
`gateway/`.

```
MCP client / chat agent ──► gateway ──► woo-mcp :8000/mcp ──► WordPress /wp-json/wc/v3
   (their own API key)        │  (the gateway's token)     (the store's key pair)
                              └── never the caller's key
```

One instance serves one store. A second store gets a second instance of the
same image with its own address, key pair and token; the gateway registers it
as `woo-<subdomain>`, so its tools read `woo-<subdomain>__list_orders`.

## Tools

| Tool | Call to the store | Notes |
|---|---|---|
| `list_products` | `GET products` | `search`, `status`, `page`, `per_page` (20, at most 100) |
| `get_product` | `GET products/{id}` | also returns the description |
| `create_product` | `POST products` | a simple product, published unless `status` is `draft`; a name already taken is refused |
| `update_product` | `PUT products/{id}` | only the fields given are changed; a new name already taken is refused |
| `list_orders` | `GET orders` | `status`, `after`, `before`, `page`, `per_page` |
| `get_order` | `GET orders/{id}` | |
| `update_order_status` | `PUT orders/{id}` | one of WooCommerce's seven statuses |

- **Short records.** A tool returns a few fields of WooCommerce's large
  objects: enough to answer with, small enough for a model's context, and no
  more customer data than a question needs (name and email, no address, phone
  or IP). Product links are left out: see "Talking to the store".
- **No duplicate products.** An agent that gets no answer in time may ask
  again, and the first request may have gone through. WooCommerce's REST API
  has no transactions and no idempotency keys, but it refuses a second product
  with a SKU that is taken, also when two requests arrive at once (eight
  simultaneous creates made one product). So `create_product` sends a SKU made
  from the name (`mcp-` and a hash): the repeat is refused with the id of the
  product that is already there. Renaming moves that SKU with the name; a SKU
  the merchant chose is never changed. The other write tools are safe to
  repeat as they are: they set a value, they do not add anything.
- **Hints.** Every tool says what it does to the store, so a client can ask
  before a write and knows what is safe to repeat (MCP tool annotations; the
  gateway passes them on unchanged):

  | Tools | `readOnlyHint` | `destructiveHint` | `idempotentHint` |
  |---|---|---|---|
  | `list_products`, `get_product`, `list_orders`, `get_order` | true | | |
  | `create_product` | false | false: it only adds | true: the store refuses a repeat |
  | `update_product`, `update_order_status` | false | true: they overwrite | true |

  All seven carry `openWorldHint: false`: a tool reaches this one store and
  nothing else.
- **Lists** return one page and `total`, how many match in all (WooCommerce's
  `X-WP-Total`), so a caller knows when to ask for the next `page`.
- **Dates** are in the store's time zone. For `after` and `before` a date
  (`2026-10-12`) means that whole day; a date-time is passed on as given. For
  "today's orders", pass today's date as both.
- **Errors** come back as tool errors the model can read. WooCommerce's own
  message is passed on (`Invalid ID.`); a store that does not answer within
  20 s (`TIMEOUT_SECONDS`, under the gateway's 30 s) says so.

## Talking to the store: the REST API, and why

Each tool is one call to WooCommerce's REST API v3.

- **It is WooCommerce's supported interface.** Stock, order status rules, hooks
  and emails run exactly as they do from wp-admin. Writing to the database
  would skip all of that.
- **It works over the network.** This server is its own container with no
  access to WordPress's files or database. WP-CLI would have to run inside the
  WordPress container, or share its volume and database password.
- **Access is a revocable key** tied to one WordPress user, not a database
  login.

Calls are authenticated with the store's consumer key and secret as HTTP Basic
auth. WooCommerce accepts that only when WordPress believes the request came
over HTTPS; inside the cluster the call is plain HTTP, so every request also
sends `X-Forwarded-Proto: https`, which the official WordPress image honours.
Without that header the store answers `401`. The header also makes WordPress
write `https://` into the links it returns, which is wrong for a store served
over plain HTTP, so tools do not return links. No OAuth is used anywhere.

## Who gets in

Only the gateway.

- Every request except `GET /healthz` must carry
  `Authorization: Bearer <MCP_AUTH_TOKEN>`; anything else gets `401`. The
  backend stores the same token, encrypted, as the upstream's credential.
- The service has no published port in `docker-compose.yml`. On Kubernetes it
  gets no Ingress, and a NetworkPolicy that admits the gateway only.
- A `Host` header that is not in `ALLOWED_HOSTS` gets `421`.

## Credentials

| Credential | Who uses it | Held here? |
|---|---|---|
| Consumer key and secret (`ck_…`, `cs_…`), Read/Write | this server, on every call to the store | yes |
| `MCP_AUTH_TOKEN` | the gateway, on every call to this server | yes, to compare |
| WordPress admin password | `wp-init.sh` once; a person signing in to wp-admin | no |
| MySQL passwords | MySQL, WordPress, `wp-init.sh` | no |

The key pair is two random values from `.env`, not something fetched from
WordPress: `deploy/wp-init.sh` registers them in WooCommerce and this server
signs in with the same two. The key belongs to a WordPress user `mcp` with the
Shop manager role, not to the administrator. A leaked key can change products,
orders and WooCommerce settings, but cannot install plugins or themes or touch
administrator accounts.

To make a pair by hand instead (for a quick test): wp-admin, WooCommerce >
Settings > Advanced > REST API > Add key, permissions Read/Write. The secret is
shown once.

## The store itself

`docker-compose.yml` runs the first store: `mysql`, `wordpress` (storefront and
wp-admin on http://localhost:8081) and the one-shot `wp-init`, which runs
`deploy/wp-init.sh` on WordPress's volume. The script runs on every start and
is safe to run again.

On every run, from `.env`:

- installs WordPress and WooCommerce 11.2.1 if they are missing (pinned,
  because the script writes the key pair straight into WooCommerce's own table);
- sets the wp-admin password to `WP_ADMIN_PASSWORD` if it differs, so the
  password is changed in `.env`, not in wp-admin;
- puts pretty permalinks back if they were set to "Plain", because `/wp-json/`
  needs them;
- creates the `mcp` user and registers the key pair for it.

Once, and from then on left to the store's admin, so a change made in wp-admin
survives a restart:

- turns off the "coming soon" page and the setup wizard, and sets the time zone;
- disables shipping, so checkout needs no shipping zone;
- creates the shop pages, enables cash on delivery, adds one sample product.

The backend registers the store and this server on startup
(`backend/app/services/bootstrap.py`): a store with the slug
`FIRST_STORE_SUBDOMAIN` and an upstream `woo-<subdomain>` that belongs to it.
The admin sees its tools at once; a member sees them once assigned to the store.

## Settings

| Variable | Meaning |
|---|---|
| `WOOCOMMERCE_URL` | The store as this service reaches it, without a path (`http://wordpress`) |
| `WOOCOMMERCE_KEY`, `WOOCOMMERCE_SECRET` | The key pair: `ck_` or `cs_`, then 40 hex characters |
| `MCP_AUTH_TOKEN` | What the gateway must present (32+ characters) |
| `ALLOWED_HOSTS` | JSON list of Host headers it answers to; default `["localhost:*", "127.0.0.1:*", "woo-mcp:*"]` |
| `TIMEOUT_SECONDS` | For one call to the store; default 20 |

## Run and test

```
cd woo-mcp
uv run pytest                      # unit tests; WooCommerce is a small fake, no stack needed
cd .. && docker compose up -d --build --wait
```

## Layout

```
app/
  config.py     Settings from the environment
  main.py       The seven tools, the call to the store, and the token check
deploy/
  wp-init.sh    Turns a bare WordPress into the store (run by the wp-init service)
tests/
  test_main.py  The tools against a fake WooCommerce, and who may reach the server
```

## Known limits

- The no-duplicate rule holds among products made by `create_product`. A
  product made in wp-admin without a SKU does not hold its name, and one in
  the trash holds its name until it is deleted for good.
- Changing `WOO_MCP_TOKEN` or the server's address after the first start does
  not update the registered upstream: the backend never touches a store that
  already exists. Edit the server in the dashboard, or with
  `PATCH /upstream-servers/{id}`.
- The hints are only declared here. Nothing acts on them until the chat
  agent's confirmation step for write tools is built.
- There is no automated end-to-end test with a real WordPress yet.
