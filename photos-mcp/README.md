# Product photos MCP server

Stock photos from [Pexels](https://www.pexels.com/api/) as one MCP tool, over
Streamable HTTP at `/mcp`, so a team member can say "add a Blue Mug at 299
with a photo". A separate service: it imports nothing from the rest of the repo
and never talks to a store.

```
chat agent ──► gateway ──► photos-mcp :8000/mcp ──► api.pexels.com/v1/search
                  │  (the gateway's token)      (this server's Pexels key)
                  └──► woo-<store> create_product(image_url=…) ──► WooCommerce copies the photo
```

One instance serves every store: it holds no store data.

## Tool

| Tool | Call | Returns |
|---|---|---|
| `find_photos(query)` | `GET /v1/search?query=…&per_page=5&orientation=square` | up to five photos: `id`, `description` (Pexels's own), `photographer`, `photo` (the address to pass on, at most 940 × 650), `pexels_page` |

- **Hints:** `readOnlyHint: true`, so the chat agent runs it without asking;
  `openWorldHint: true`, because it reaches outside this platform.
- **Square photos,** because product photos are shown square in a shop.
- **The model picks** the photo whose description fits, and passes its `photo`
  to the store's `create_product` or `update_product` as `image_url`. That
  call is a write, so the chat asks first. The store downloads the file into
  its own media library, so the product does not depend on Pexels afterwards.
  Pexels's licence allows that; Unsplash's API terms do not, which is why
  Pexels.
- **The results panel** shows the photos as thumbnails
  (`frontend/components/chat-results.tsx`). Only addresses on
  `images.pexels.com` are shown as pictures.
- **Credit:** the tool asks the model to credit the photographer and Pexels.
- **Errors** come back as tool errors the model can read: "Pexels refused the
  search (HTTP 429)" when the hourly limit is used up (200 searches an hour on
  a free key), "Pexels did not answer" after 20 s. A redirect is not followed,
  so the key is never sent anywhere else.

## Who gets in, and the key

- Every request except `GET /healthz` must carry
  `Authorization: Bearer <MCP_AUTH_TOKEN>`; anything else gets `401`.
- No published port in `docker-compose.yml`.
- The Pexels key stays in this container. The gateway, the model and the
  team never see it. Without one (`PEXELS_API_KEY=` in `.env`) the server
  still starts and the tool answers "Photo search is not set up", like the
  chat without `GROQ_API_KEY`.

## Register it with the gateway (by hand)

As the admin, with the backend on http://localhost:8000:

```sh
TOKEN=$(curl -s localhost:8000/auth/login -H 'content-type: application/json' \
  -d '{"email":"<admin email>","password":"<admin password>"}' | python3 -c 'import json,sys;print(json.load(sys.stdin)["access_token"])')

curl -s localhost:8000/upstream-servers -H "authorization: Bearer $TOKEN" -H 'content-type: application/json' -d '{
  "name": "Product photos",
  "slug": "photos",
  "url": "http://photos-mcp:8000/mcp",
  "credential": "Bearer <PHOTOS_MCP_TOKEN from .env>"
}'
```

No `store_id`: every store's members see it. Its tool is then
`photos__find_photos`, in the next chat message's tool list.

## Settings

| Variable | Meaning |
|---|---|
| `PEXELS_API_KEY` | From https://www.pexels.com/api/ (free) |
| `MCP_AUTH_TOKEN` | What the gateway must present (32+ characters); compose takes it from `PHOTOS_MCP_TOKEN` |
| `ALLOWED_HOSTS` | JSON list of Host headers it answers to; default `["localhost:*", "127.0.0.1:*", "photos-mcp:*"]` |
| `TIMEOUT_SECONDS` | For one search; default 20, under the gateway's 30 |

## Run and test

```
cd photos-mcp
uv run pytest        # Pexels is a small fake: no stack, no internet
```
