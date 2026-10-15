# Fulfilment MCP server

Shipping one store's orders as MCP tools, over Streamable HTTP at `/mcp`:
"ship today's paid orders", "mark 41 and 42 delivered". A separate service: it
imports nothing from the rest of the repo, and it never talks to the store.

```
chat agent ──► gateway ──► woo-<store>      list_orders, update_order_status   (the store's own server)
                  └──────► fulfilment-mcp   start / advance / list  ──► Postgres, database "fulfilment"
                           (the gateway's token)                        (its own role)
```

An order moves **packing → shipped → delivered**, one stage at a time, and gets
a tracking number when it starts. The chat agent reads orders with the store's
server, passes their ids here, and makes the store's changes there (for
example `completed` once delivered). So every change is made as the signed-in
user and is in the gateway's audit log; this server holds no store key.

## Tools

| Tool | What it does | Hints |
|---|---|---|
| `start_fulfilment(order_ids)` | each new order gets stage `packing` and a tracking number (`TRK` and 10 hex characters); an order started before keeps its record | write, adds only, safe to repeat |
| `advance_fulfilment(order_ids, stage)` | `stage` is `shipped` or `delivered`: one step forward, never back; an order already there stays as it is | write, overwrites, safe to repeat |
| `list_fulfilments(stage?, order_ids?)` | the most recently changed first, 50 at most, and `total` | read only |

- **Batches,** 1 to 50 order ids per call, not a call per order: every call
  resends the tools and the chat to the model.
- **All or nothing.** If one order in a batch cannot move (never started, a
  skipped stage, going back), nothing changes and the error names each one:
  "Nothing was changed: order 8 is still packing, so it cannot be delivered yet."
- **Safe to repeat,** so a retry after a timeout does no harm: the primary key
  settles a second start, and moving to the stage an order is already at
  changes nothing.
- **Two copies at once are safe.** A start relies on the primary key, not on
  a check before the insert; an advance locks its rows (`FOR UPDATE`), so a
  second advance of the same orders waits and then sees the new stage.
- **Dates** are in the store's time zone (`TIMEZONE`, from `WP_TIMEZONE`).
- **The chat asks before a write:** both write tools say they are not
  read-only.

## Where the records are, and why

In the platform's Postgres, in a database of its own (`fulfilment`), made by
`gateway/deploy/db-init.sh`:

- **The pod keeps nothing,** so it can be killed or run as two copies and no
  record is lost. No volume, no third database engine.
- **Its own role,** `fulfilment`, can `SELECT`, `INSERT` and `UPDATE` its one
  table: no `DELETE`, no `DROP`, no other database. `db-init.sh` also takes
  the default "any role may connect" away from every database, so this role
  cannot even open `platform` or `mcp_logs`:

  ```
  $ docker compose exec postgres psql "postgresql://fulfilment:<password>@localhost/platform" -c 'select 1'
  FATAL:  permission denied for database "platform"
  ```
- **One store per instance:** the order id is the store's own, so a second
  store gets its own instance. `shortcut:` one store per database; add a store
  column when a second store gets fulfilment.

## Register it with the gateway (by hand)

As the admin, with the backend on http://localhost:8000:

```sh
TOKEN=$(curl -s localhost:8000/auth/login -H 'content-type: application/json' \
  -d '{"email":"<admin email>","password":"<admin password>"}' | python3 -c 'import json,sys;print(json.load(sys.stdin)["access_token"])')

curl -s localhost:8000/stores -H "authorization: Bearer $TOKEN"     # the demo store's "id"

curl -s localhost:8000/upstream-servers -H "authorization: Bearer $TOKEN" -H 'content-type: application/json' -d '{
  "name": "Demo Store shipping",
  "slug": "fulfilment-demo",
  "url": "http://fulfilment-mcp:8000/mcp",
  "credential": "Bearer <FULFILMENT_MCP_TOKEN from .env>",
  "store_id": "<the demo store id>"
}'
```

With `store_id` set, only the admin and that store's members see its tools,
as with `woo-demo`. They are then `fulfilment-demo__start_fulfilment` and so
on, in the next chat message's tool list.

## On Kubernetes

The pod belongs with its store, in the store's namespace, and reaches the
platform's Postgres in another. Two things make that explicit:

- **A NetworkPolicy** on Postgres that admits this pod on 5432, next to the
  backend, the gateway and db-init.
- **Its own Secret** with `FULFILMENT_DB_PASSWORD` (and `FULFILMENT_MCP_TOKEN`)
  in the store's namespace. It reaches only the `fulfilment` database: the
  store's namespace still gets none of the platform's secrets. db-init needs
  the same password, in the platform's namespace.

`DATABASE_URL` then names Postgres by its long name
(`postgres.<platform namespace>.svc.cluster.local`), and `ALLOWED_HOSTS` adds
this service's long name, as for `woo-mcp`.

## Settings

| Variable | Meaning |
|---|---|
| `DATABASE_URL` | `postgresql://fulfilment:<password>@postgres:5432/fulfilment`; compose builds it from `FULFILMENT_DB_PASSWORD` |
| `MCP_AUTH_TOKEN` | What the gateway must present (32+ characters); compose takes it from `FULFILMENT_MCP_TOKEN` |
| `TIMEZONE` | The store's time zone; default `UTC`, compose passes `WP_TIMEZONE` |
| `ALLOWED_HOSTS` | JSON list of Host headers it answers to; default `["localhost:*", "127.0.0.1:*", "fulfilment-mcp:*"]` |
| `TIMEOUT_SECONDS` | For connecting and for each query; default 20, under the gateway's 30 |

## Run and test

```
cd fulfilment-mcp
uv run pytest        # the stage rule, and the tools against a fake database: no stack needed
```

`tests/test_database.py` runs the tools against the real Postgres, signed in
as the `fulfilment` role: a repeated start, two starts and two advances at
once, a batch refused as a whole, the filters, and that the role can neither
delete nor open `platform` or `mcp_logs`. It is skipped without the stack;
`./run_tests.sh all` runs it once the stack is up. Alone, with the stack up:

```sh
cd fulfilment-mcp
FULFILMENT_TEST_DATABASE_URL="$(docker compose exec -T fulfilment-mcp printenv DATABASE_URL | sed 's/@postgres:/@localhost:/')" \
  uv run pytest tests/test_database.py
```

Its order ids start at 1,000,000,000, and it deletes them afterwards as the
database owner.
