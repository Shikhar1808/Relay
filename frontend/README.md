# Urumi frontend

The web console for the Urumi platform: Next.js (App Router), TypeScript and
Tailwind CSS. It talks to the API in `../backend`.

## Run it

With everything else, from the repo root:

```bash
docker compose up -d --build     # frontend on http://localhost:3000
```

On its own, against a backend already running on port 8000:

```bash
npm install
npm run dev                      # http://localhost:3000
```

## How it connects to the backend

Pages call the API **straight from the browser**; there is no proxy in
between. The browser learns the API's address from `GET /runtime-config`,
which returns the `API_URL` environment variable as read when the request
arrives. So one built image works in every environment: set `API_URL` on
the container, not at build time.

`API_URL` must be the address a *browser* can reach (for example
`https://api.example.com`), not an in-cluster name like `http://backend:8000`.
The backend must allow this site's origin in `CORS_ORIGINS`.

The session token from sign in is kept in `localStorage` and sent as a
`Bearer` header.

## What's where

```
app/
  runtime-config/route.ts   Hands the browser the API address
  login, forgot-password,   Signed-out pages. Emailed invite and reset links
  set-password                open /set-password#token=...
  (console)/                Signed-in pages, behind the guard in layout.tsx
    stores/                   A member's one store; for the admin, all stores and who is in each
    team/                     Admin only: add, deactivate, resend invite, manage a member's API key
    chat/  api-key/  account/
components/                 Buttons, fields, the one-time secret box, the API key manager
lib/
  api.ts                    Typed fetch client and the API's types
  auth.tsx                  Who is signed in
  use-data.ts               Load-on-mount hook
```

## Checks

```bash
npx tsc --noEmit
npm run lint
npm run build
```

There are no automated UI tests in this folder yet.

## Kubernetes notes

- The image runs as a non-root user, listens on port 3000, and needs only
  `API_URL`.
- `GET /runtime-config` works as a liveness and readiness probe.
- It is stateless, so it can run as several replicas.
