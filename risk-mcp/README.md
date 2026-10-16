# Risk check MCP server

Scores a store's orders for risk with a few fixed rules, as one MCP tool over
Streamable HTTP at `/mcp`. A separate service: it imports nothing from the rest
of the repo, holds no data, has no secret but its gateway token, and calls
nothing.

```
chat agent ──► gateway ──► woo-<store>      list_orders            the orders
                  ├──────► risk-mcp         score_orders           which look risky, and why
                  ├──────► woo-<store>      update_order_status    hold the risky ones   (asks first)
                  └──────► fulfilment-<store> start_fulfilment     ship the rest         (asks first)
```

The prompt it is made for: "Check today's orders, put the risky ones on hold
and ship the rest". Three servers in one answer. One instance serves every
store: it keeps nothing about any of them.

## Tool

`score_orders(orders)`: 1 to 50 orders, each with the fields the rules read,
copied from the store's `list_orders`: `id`, `total`, `email`,
`payment_code` and `items` (each with `name` and `quantity`). Other fields
are accepted and ignored. For each order: `order_id`, `score` (0 to 100),
`level`, `advice` and `reasons`. A malformed order is refused with its place
in the batch and the field: "Order 2 of the batch: total: …".

| Rule | Points |
|---|---|
| Total over 20,000 | 40 |
| One item ordered 10 times or more | 30 |
| Email on a throwaway domain (mailinator, yopmail, guerrillamail, …) | 40 |
| Cash on delivery (WooCommerce's code `cod`, so a renamed title still counts) and a total over 5,000 | 20 |
| The same email on 3 or more orders of the batch | 30 |

| Score | Level | Advice |
|---|---|---|
| 60 and up | high | put it on hold |
| 30 to 59 | medium | check it by hand |
| under 30 | low | ship it |

- **It only reads** (`readOnlyHint: true`), so the chat runs it without
  asking. The changes it leads to (a hold, a shipment) are made with the
  store's and the fulfilment server's tools, and each one asks first.
- **A batch, not one call per order:** the repeated-email rule needs to see
  the day's orders together, and every call resends the tools and the chat to
  the model.
- **Only the five fields, in a flat input format.** The model copies each
  order into the call, so asking for five fields instead of the whole record
  halves those tokens (Groq allows 8,000 a minute). The input is a list of
  plain objects, checked inside the tool, because nested models would put
  `$ref` into the format every chat message sends to the model, and Groq does
  not document `$ref`.
- **Amounts are in the store's own currency.** The thresholds are plain
  numbers: 20,000 means rupees in an INR store.
- **Totals are read as exact decimals** (the store sends `"38.00"` as text),
  never as floats.

## Limits

- The model passes the orders in, so it could copy a number wrong. The score
  is advice, and the hold it leads to still needs a person's yes.
- The rules are fixed, few and simple: it does not learn, and someone who
  knows them can stay under them. `shortcut:` the throwaway domains are a
  short list in the code.

## Register it with the gateway (by hand)

As the admin, with the backend on http://localhost:8000:

```sh
TOKEN=$(curl -s localhost:8000/auth/login -H 'content-type: application/json' \
  -d '{"email":"<admin email>","password":"<admin password>"}' | python3 -c 'import json,sys;print(json.load(sys.stdin)["access_token"])')

curl -s localhost:8000/upstream-servers -H "authorization: Bearer $TOKEN" -H 'content-type: application/json' -d '{
  "name": "Risk check",
  "slug": "risk",
  "url": "http://risk-mcp:8000/mcp",
  "credential": "Bearer <RISK_MCP_TOKEN from .env>"
}'
```

No `store_id`: every store's members see it. Its tool is then
`risk__score_orders`, in the next chat message's tool list.

## Who gets in

- Every request except `GET /healthz` must carry
  `Authorization: Bearer <MCP_AUTH_TOKEN>`; anything else gets `401`.
- No published port in `docker-compose.yml`; on Kubernetes, a NetworkPolicy
  that admits the gateway only.

## Settings

| Variable | Meaning |
|---|---|
| `MCP_AUTH_TOKEN` | What the gateway must present (32+ characters); compose takes it from `RISK_MCP_TOKEN` |
| `ALLOWED_HOSTS` | JSON list of Host headers it answers to; default `["localhost:*", "127.0.0.1:*", "risk-mcp:*"]` |

## Run and test

```
cd risk-mcp
uv run pytest        # every rule, the levels, the batch rule, and the token check: no stack needed
```
