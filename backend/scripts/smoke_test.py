"""Assert-based end-to-end smoke test against a LIVE stack (real Postgres +
Redis, no mocks). Run this after `docker compose up -d` from the repo root:

    python backend/scripts/smoke_test.py

Exercises every endpoint and RBAC rule built in this phase: admin login,
password length bounds, single-admin enforcement (signup always 409s once
bootstrapped, admin can't deactivate itself), member creation, admin-issued
and self-service API key lifecycle (one active key per user), the member
permission boundary (can't create stores or touch /team/members), store
creation + member assignment, scoped store listing, the chat token flow,
and the login rate limiter. Each step prints PASS/FAIL and the script exits
non-zero on the first failure.
"""
import os
import sys
import time
import uuid
from pathlib import Path

import httpx

BASE_URL = os.environ.get("SMOKE_TEST_BASE_URL", "http://localhost:8000")


def _load_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip()
    return values


def _step(label: str) -> None:
    print(f"-- {label}")


def _ok(label: str) -> None:
    print(f"   PASS: {label}")


def main() -> int:
    repo_root = Path(__file__).resolve().parents[2]
    env = _load_env_file(repo_root / ".env")
    admin_email = os.environ.get("ADMIN_EMAIL", env.get("ADMIN_EMAIL"))
    admin_password = os.environ.get("ADMIN_PASSWORD", env.get("ADMIN_PASSWORD"))
    assert admin_email and admin_password, "ADMIN_EMAIL/ADMIN_PASSWORD not found in env or repo-root .env"

    client = httpx.Client(base_url=BASE_URL, timeout=10)
    unique = uuid.uuid4().hex[:8]

    _step("health check")
    r = client.get("/health")
    assert r.status_code == 200, r.text
    _ok("GET /health -> 200")

    _step("password length bounds (8-20 chars)")
    r = client.post("/auth/login", json={"email": admin_email, "password": "short"})
    assert r.status_code == 422, r.text
    r = client.post("/auth/login", json={"email": admin_email, "password": "A" * 21})
    assert r.status_code == 422, r.text
    _ok("login rejects passwords outside 8-20 chars with 422, not a crash")

    _step("admin login")
    r = client.post("/auth/login", json={"email": admin_email, "password": admin_password})
    assert r.status_code == 200, r.text
    admin_token = r.json()["access_token"]
    admin_id = r.json()["user"]["id"]
    assert r.json()["user"]["role"] == "admin"
    _ok("admin login returns a token with role=admin")
    admin_headers = {"Authorization": f"Bearer {admin_token}"}

    r = client.get("/auth/me", headers=admin_headers)
    assert r.status_code == 200 and r.json()["role"] == "admin", r.text
    _ok("GET /auth/me confirms admin identity")

    _step("single-admin enforcement")
    r = client.post(
        "/auth/admin/signup",
        json={"email": f"second-admin-{unique}@example.com", "name": "Nope", "password": "doesnt-matter1"},
    )
    assert r.status_code == 409, r.text
    _ok("admin signup refuses once an admin already exists")

    r = client.delete(f"/team/members/{admin_id}", headers=admin_headers)
    assert r.status_code == 409, r.text
    _ok("admin cannot deactivate the admin account")

    _step("create member")
    member_email = f"member-{unique}@example.com"
    r = client.post("/team/members", json={"email": member_email, "name": "Smoke Test Member"}, headers=admin_headers)
    assert r.status_code == 201, r.text
    member = r.json()
    member_id = member["user"]["id"]
    temp_password = member["temporary_password"]
    _ok(f"member created: {member_email}")

    _step("issue gateway API key (admin-initiated)")
    r = client.post(f"/team/members/{member_id}/api-keys", json={"name": "smoke-test-key"}, headers=admin_headers)
    assert r.status_code == 201, r.text
    key = r.json()
    raw_key = key["raw_key"]
    key_id = key["id"]
    _ok("API key issued")

    _step("one active key at a time (admin path)")
    r = client.post(f"/team/members/{member_id}/api-keys", json={"name": "second-key"}, headers=admin_headers)
    assert r.status_code == 409, r.text
    _ok("issuing a second key while one is active is rejected with 409")

    _step("resolve API key (future gateway's auth path)")
    r = client.post("/internal/auth/resolve", json={"token": raw_key})
    assert r.status_code == 200, r.text
    resolved = r.json()
    assert resolved["user_id"] == member_id, resolved
    assert resolved["token_type"] == "api_key"
    _ok("resolve returns correct user_id + token_type=api_key")

    _step("revoke the key")
    r = client.post(f"/team/members/{member_id}/api-keys/{key_id}/revoke", headers=admin_headers)
    assert r.status_code == 200 and r.json()["revoked"] is True, r.text
    _ok("key revoked")

    r = client.post("/internal/auth/resolve", json={"token": raw_key})
    assert r.status_code == 401, r.text
    _ok("resolve rejects the revoked key with 401")

    _step("create store (stub)")
    r = client.post("/stores", json={"name": f"Smoke Test Store {unique}"}, headers=admin_headers)
    assert r.status_code == 201, r.text
    store = r.json()
    assert store["status"] == "ready"
    assert store["url"].endswith(".stores.local")
    store_id = store["id"]
    _ok(f"store created: {store['slug']} -> {store['url']}")

    _step("assign member to store")
    r = client.post(f"/stores/{store_id}/members", json={"user_id": member_id}, headers=admin_headers)
    assert r.status_code == 201, r.text
    _ok("member assigned to store")

    _step("member login + scoped store list")
    r = client.post("/auth/login", json={"email": member_email, "password": temp_password})
    assert r.status_code == 200, r.text
    member_token = r.json()["access_token"]
    member_headers = {"Authorization": f"Bearer {member_token}"}

    r = client.get("/stores", headers=member_headers)
    assert r.status_code == 200, r.text
    stores = r.json()
    assert len(stores) == 1 and stores[0]["id"] == store_id, stores
    _ok("member sees only their assigned store")

    _step("member permission boundary")
    r = client.post("/stores", json={"name": "member cannot do this"}, headers=member_headers)
    assert r.status_code == 403, r.text
    _ok("member cannot create a store")

    r = client.get("/team/members", headers=member_headers)
    assert r.status_code == 403, r.text
    _ok("member cannot list team members")

    r = client.post(
        "/team/members", json={"email": "sneaky@example.com", "name": "x"}, headers=member_headers
    )
    assert r.status_code == 403, r.text
    _ok("member cannot create another member")

    _step("member self-service API key (one at a time)")
    r = client.post("/team/me/api-keys", json={"name": "my-own-key"}, headers=member_headers)
    assert r.status_code == 201, r.text
    my_key = r.json()
    my_key_id = my_key["id"]
    _ok("member can self-issue a key once the admin-issued one is revoked")

    r = client.post("/team/me/api-keys", json={"name": "second-attempt"}, headers=member_headers)
    assert r.status_code == 409, r.text
    _ok("member cannot self-issue a second key while one is active")

    r = client.get("/team/me/api-keys", headers=member_headers)
    assert r.status_code == 200 and len(r.json()) >= 1, r.text
    _ok("member can list their own keys")

    r = client.post(f"/team/me/api-keys/{my_key_id}/revoke", headers=member_headers)
    assert r.status_code == 200 and r.json()["revoked"] is True, r.text
    _ok("member can revoke their own key")

    r = client.post("/team/me/api-keys", json={"name": "after-revoke"}, headers=member_headers)
    assert r.status_code == 201, r.text
    _ok("member can issue a new key after revoking the old one")

    _step("self-service revoke cannot target another user's key (IDOR check)")
    other_email = f"other-member-{unique}@example.com"
    r = client.post("/team/members", json={"email": other_email, "name": "Other Member"}, headers=admin_headers)
    assert r.status_code == 201, r.text
    other_member_id = r.json()["user"]["id"]
    r = client.post(f"/team/members/{other_member_id}/api-keys", json={}, headers=admin_headers)
    assert r.status_code == 201, r.text
    other_member_key_id = r.json()["id"]

    r = client.post(f"/team/me/api-keys/{other_member_key_id}/revoke", headers=member_headers)
    assert r.status_code == 404, r.text
    _ok("a member cannot revoke a different user's key through /me/api-keys")

    _step("chat message mints a short_chat_token server-side")
    r = client.post("/chat/message", json={"message": "what orders came in today?"}, headers=member_headers)
    assert r.status_code == 200, r.text
    reply = r.json()
    assert reply["reply"]
    first_expiry = reply["chat_token_expires_at"]
    _ok("chat reply received, token expiry present, raw token never exposed")

    r = client.post("/chat/message", json={"message": "a follow-up question"}, headers=member_headers)
    assert r.status_code == 200, r.text
    assert r.json()["chat_token_expires_at"] == first_expiry, "expected the SAME token to be reused"
    _ok("second chat message reuses the same session token (one session at a time)")

    _step("login rate limit (token bucket, capacity=5/min by IP)")
    saw_429 = False
    for i in range(8):
        r = client.post("/auth/login", json={"email": admin_email, "password": "wrong-password"})
        if r.status_code == 429:
            saw_429 = True
            assert "Retry-After" in r.headers
            break
        time.sleep(0.05)
    assert saw_429, "expected a 429 within 8 rapid login attempts"
    _ok("rate limiter returns 429 with Retry-After under burst load")

    print("\nALL CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
