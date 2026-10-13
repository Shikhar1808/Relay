"""Assert-based end-to-end smoke test against a LIVE stack (real Postgres +
Redis, no mocks; the gateway must be up too, because registry changes are
recorded in its audit log). Run this after `docker compose up -d` from the repo root:

    python backend/scripts/smoke_test.py

Exercises every endpoint and RBAC rule built in this phase: admin login,
password length bounds, single-admin enforcement (signup always 409s once
bootstrapped, admin can't deactivate itself), member creation, admin-issued
and self-service API key lifecycle (one active key per user), the member
permission boundary (can't create stores or touch /team/members), store
creation + member assignment, scoped store listing, members in several
stores with chat and gateway resolve scoped to them, password change, the
emailed invite/reset links (only when the backend logs emails instead of
sending them — otherwise skipped), the chat token flow, and the login rate
limiter.

With SMTP_PASSWORD set, one run really sends 10 emails (2 invites, 1 reset
link, 7 notices), all to made-up @example.com addresses. 10 is the agreed
ceiling — a new step that emails must replace one, not add to them.

Each step prints PASS/FAIL and the script exits
non-zero on the first failure.
"""
import http.cookiejar
import os
import re
import subprocess
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


def _emailed_token(repo_root: Path, to: str, subject_prefix: str) -> str | None:
    """Newest set-password token emailed to `to`, read from the backend log.
    Only there when SMTP_PASSWORD is unset (emails are logged, not sent).
    """
    time.sleep(0.5)  # the email is "sent" by a background task after the response
    try:
        logs = subprocess.run(
            ["docker", "compose", "logs", "--no-color", "--since", "5m", "backend"],
            cwd=repo_root, capture_output=True, text=True, timeout=20,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    pattern = rf"to={re.escape(to)} subject={re.escape(subject_prefix)}.*?token=([\w-]+)"
    tokens = re.findall(pattern, logs, re.DOTALL)
    return tokens[-1] if tokens else None


def _expire_auth_tokens(repo_root: Path, user_id: str) -> None:
    """Back-dates a user's set-password links in the database, since the API
    (rightly) has no way to make a link expire early.
    """
    sql = f"UPDATE auth_tokens SET expires_at = now() - interval '1 minute' WHERE user_id = '{uuid.UUID(user_id)}'"
    done = subprocess.run(
        ["docker", "compose", "exec", "-T", "postgres", "sh", "-c", f'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "{sql}"'],
        cwd=repo_root, capture_output=True, text=True, timeout=20,
    )
    assert "UPDATE 1" in done.stdout, (done.stdout, done.stderr)


def _psql(repo_root: Path, sql: str, ok: bool = True) -> str:
    """Runs one statement in the compose Postgres and returns its output; `ok=False` expects it to be refused."""
    done = subprocess.run(
        ["docker", "compose", "exec", "-T", "postgres", "sh", "-c", f'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -At -c "{sql}"'],
        cwd=repo_root, capture_output=True, text=True, timeout=20,
    )
    assert (done.returncode == 0) == ok, (sql, done.stdout, done.stderr)
    return done.stdout + ("" if ok else done.stderr)


def _redis(repo_root: Path, *command: str) -> str:
    done = subprocess.run(
        ["docker", "compose", "exec", "-T", "redis", "redis-cli", *command],
        cwd=repo_root, capture_output=True, text=True, timeout=20,
    )
    assert done.returncode == 0, (command, done.stderr)
    return done.stdout.strip()


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
    service_token = os.environ.get("INTERNAL_SERVICE_TOKEN", env.get("INTERNAL_SERVICE_TOKEN"))
    assert service_token, "INTERNAL_SERVICE_TOKEN not found in env or repo-root .env"
    service_headers = {"Authorization": f"Bearer {service_token}"}

    app_origin = os.environ.get("APP_BASE_URL", env.get("APP_BASE_URL", "http://localhost:3000")).rstrip("/")

    # Like a script, not a browser: this client keeps no cookies, so every request below is
    # signed in only by the header it is given. The session cookie has its own step.
    no_cookies = http.cookiejar.CookieJar(http.cookiejar.DefaultCookiePolicy(allowed_domains=[]))
    client = httpx.Client(base_url=BASE_URL, timeout=10, cookies=no_cookies)
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
    # Upper-cased on purpose: emails are matched case-insensitively.
    r = client.post("/auth/login", json={"email": admin_email.upper(), "password": admin_password})
    assert r.status_code == 200, r.text
    admin_token = r.json()["access_token"]
    admin_id = r.json()["user"]["id"]
    session_cookie, set_cookie = r.cookies["urumi_session"], r.headers["set-cookie"].lower()
    assert r.json()["user"]["role"] == "admin"
    _ok("admin login returns a token with role=admin")
    admin_headers = {"Authorization": f"Bearer {admin_token}"}

    r = client.get("/auth/me", headers=admin_headers)
    assert r.status_code == 200 and r.json()["role"] == "admin", r.text
    _ok("GET /auth/me confirms admin identity")

    _step("the dashboard's session is a cookie that page scripts cannot read")
    assert "httponly" in set_cookie and "samesite=strict" in set_cookie and "max-age=" in set_cookie, set_cookie
    _ok("sign in sets the session as an HttpOnly, SameSite=Strict cookie")
    browser = httpx.Client(base_url=BASE_URL, timeout=10, cookies={"urumi_session": session_cookie})
    r = browser.get("/auth/me")
    assert r.status_code == 200 and r.json()["id"] == admin_id, r.text
    r = httpx.get(f"{BASE_URL}/auth/me")
    assert r.status_code == 401, r.text
    _ok("the cookie alone signs a request in; with neither cookie nor header the answer is 401")
    r = browser.get("/auth/session")
    assert r.status_code == 200 and r.json()["user"]["id"] == admin_id, r.text
    r = httpx.get(f"{BASE_URL}/auth/session")
    assert r.status_code == 200 and r.json() == {"user": None}, r.text
    _ok("a page can ask who is signed in and gets a plain answer either way, never an error")
    # A change that is refused by 404 (no such key) reached the route; one refused by 403 did not.
    harmless_change = f"/team/me/api-keys/{uuid.uuid4()}/revoke"
    for foreign in ("http://evil.example", "null"):
        r = browser.post(harmless_change, headers={"Origin": foreign})
        assert r.status_code == 403 and "dashboard" in r.text, (foreign, r.text)
    r = browser.post(harmless_change, headers={"Origin": app_origin})
    assert r.status_code == 404, r.text
    r = browser.get("/auth/me", headers={"Origin": "http://evil.example"})
    assert r.status_code == 200 and "access-control-allow-origin" not in r.headers, r.headers
    _ok("a change sent with the cookie from another site's page is refused; another site's read gets no permission to see the answer")
    preflight = {"Access-Control-Request-Method": "POST", "Access-Control-Request-Headers": "content-type"}
    r = client.options("/stores", headers={"Origin": app_origin, **preflight})
    assert r.headers.get("access-control-allow-origin") == app_origin and r.headers.get("access-control-allow-credentials") == "true", r.headers
    r = client.options("/stores", headers={"Origin": "http://evil.example", **preflight})
    assert "access-control-allow-origin" not in r.headers, r.headers
    _ok("only the frontend's own address may call the API from a browser with the session")

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

    r = client.post(
        "/team/members", json={"email": f"long-{unique}@example.com", "name": "x" * 300}, headers=admin_headers
    )
    assert r.status_code == 422, r.text
    r = client.post("/stores", json={"name": "x" * 300}, headers=admin_headers)
    assert r.status_code == 422, r.text
    _ok("names too long for their column are rejected with 422, not a crash")

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

    _step("resolve API key (the gateway's auth path)")
    r = client.post("/internal/auth/resolve", json={"token": raw_key})
    assert r.status_code == 401, r.text
    r = client.post("/internal/auth/resolve", json={"token": raw_key}, headers={"Authorization": "Bearer wrong"})
    assert r.status_code == 401, r.text
    _ok("resolve refuses callers without the service token, even with a valid API key")

    r = client.post("/internal/auth/resolve", json={"token": raw_key}, headers=service_headers)
    assert r.status_code == 200, r.text
    resolved = r.json()
    assert resolved["user_id"] == member_id, resolved
    assert resolved["token_type"] == "api_key"
    assert resolved["store_ids"] == [], resolved  # not assigned to a store yet
    _ok("resolve returns correct user_id + token_type=api_key, and no store before assignment")

    _step("revoke the key")
    r = client.post(f"/team/members/{member_id}/api-keys/{key_id}/revoke", headers=admin_headers)
    assert r.status_code == 200 and r.json()["revoked"] is True, r.text
    _ok("key revoked")

    r = client.post("/internal/auth/resolve", json={"token": raw_key}, headers=service_headers)
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

    _step("member login; unassigned, they see no store and cannot chat")
    r = client.post("/auth/login", json={"email": member_email, "password": temp_password})
    assert r.status_code == 200, r.text
    member_token = r.json()["access_token"]
    member_headers = {"Authorization": f"Bearer {member_token}"}

    r = client.get("/stores", headers=member_headers)
    assert r.status_code == 200 and r.json() == [], r.text
    r = client.post("/chat/message", json={"message": "hello"}, headers=member_headers)
    assert r.status_code == 403, r.text
    _ok("a member with no store sees an empty store list and gets 403 from chat")

    _step("assign member to store")
    r = client.post(f"/stores/{store_id}/members", json={"user_id": member_id}, headers=admin_headers)
    assert r.status_code == 201, r.text
    _ok("member assigned to store")

    _step("scoped store list")
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
    member_key = r.json()["raw_key"]
    _ok("member can issue a new key after revoking the old one")

    _step("a member can be in several stores; chat and gateway see those and no others")
    r = client.post("/stores", json={"name": f"Second Store {unique}"}, headers=admin_headers)
    assert r.status_code == 201, r.text
    second_store_id = r.json()["id"]
    r = client.post("/stores", json={"name": f"Third Store {unique}"}, headers=admin_headers)
    assert r.status_code == 201, r.text
    third_store_id = r.json()["id"]  # the member is never added here

    r = client.post(f"/stores/{second_store_id}/members", json={"user_id": member_id}, headers=admin_headers)
    assert r.status_code == 201, r.text
    _ok("a member already in one store can be added to a second")

    r = client.post(f"/stores/{second_store_id}/members", json={"user_id": member_id}, headers=admin_headers)
    assert r.status_code == 409, r.text
    _ok("adding the same member to the same store twice is rejected with 409")

    r = client.post(f"/stores/{second_store_id}/members", json={"user_id": admin_id}, headers=admin_headers)
    assert r.status_code == 400, r.text
    _ok("the admin cannot be pinned to a single store")

    both = [store_id, second_store_id]
    r = client.post("/internal/auth/resolve", json={"token": member_key}, headers=service_headers)
    assert r.status_code == 200 and r.json()["store_ids"] == both, r.text
    r = client.post("/chat/message", json={"message": "hello"}, headers=member_headers)
    assert r.status_code == 200 and r.json()["store_ids"] == both, r.text
    r = client.get("/stores", headers=member_headers)
    assert [s["id"] for s in r.json()] == both and third_store_id not in both, r.text
    _ok("gateway resolve, chat and the store list all report exactly the member's two stores")

    r = client.delete(f"/stores/{store_id}/members/{member_id}", headers=admin_headers)
    assert r.status_code == 204, r.text
    r = client.post("/internal/auth/resolve", json={"token": member_key}, headers=service_headers)
    assert r.status_code == 200 and r.json()["store_ids"] == [second_store_id], r.text
    r = client.post("/chat/message", json={"message": "hello"}, headers=member_headers)
    assert r.status_code == 200 and r.json()["store_ids"] == [second_store_id], r.text
    r = client.get("/stores", headers=member_headers)
    assert [s["id"] for s in r.json()] == [second_store_id], r.text
    _ok("removing one store cuts it off at once, for the same API key and login, and keeps the other")

    r = client.post("/chat/message", json={"message": "hello"}, headers=admin_headers)
    assert r.status_code == 200 and {store_id, second_store_id, third_store_id} <= set(r.json()["store_ids"]), r.text
    _ok("the admin's chat gets every store, so the gateway never needs to special-case the role")

    _step("upstream MCP server registry (admin only, credentials encrypted)")
    slug = f"smoke-{unique}"
    new_upstream = {"name": "Smoke upstream", "slug": slug, "url": "http://upstream.invalid/mcp"}
    r = client.get("/upstream-servers", headers=member_headers)
    assert r.status_code == 403, r.text
    _ok("a member cannot see the registry")

    r = client.post("/upstream-servers", json={**new_upstream, "credential": "Bearer smoke-secret"}, headers=admin_headers)
    assert r.status_code == 201, r.text
    upstream = r.json()
    assert upstream["has_credential"] is True and upstream["store_id"] is None and upstream["enabled"] is True, upstream
    assert "smoke-secret" not in r.text and "credential_ciphertext" not in r.text, r.text
    r = client.get("/upstream-servers", headers=admin_headers)
    assert r.status_code == 200 and "smoke-secret" not in r.text and slug in r.text, r.text
    found = _psql(repo_root, f"SELECT position(convert_to('smoke-secret', 'UTF8') in credential_ciphertext) FROM upstream_servers WHERE slug = '{slug}'")
    assert found.split() == ["0"], found
    _ok("a credential is never returned and is not readable in the database")

    r = client.post("/upstream-servers", json=new_upstream, headers=admin_headers)
    assert r.status_code == 409, r.text
    for bad_slug in ("a__b", "a_b", "Shop", "-shop", ""):
        r = client.post("/upstream-servers", json={**new_upstream, "slug": bad_slug}, headers=admin_headers)
        assert r.status_code == 422, (bad_slug, r.text)
    for bad_url in ("ftp://example.com/mcp", "http://user:pw@example.com/mcp"):
        r = client.post("/upstream-servers", json={**new_upstream, "slug": f"x-{unique}", "url": bad_url}, headers=admin_headers)
        assert r.status_code == 422, (bad_url, r.text)
    r = client.post("/upstream-servers", json={**new_upstream, "slug": f"x-{unique}", "credential": "a\r\nEvil: 1"}, headers=admin_headers)
    assert r.status_code == 422, r.text
    r = client.post("/upstream-servers", json={**new_upstream, "slug": f"x-{unique}", "store_id": str(uuid.uuid4())}, headers=admin_headers)
    assert r.status_code == 404, r.text
    _ok("duplicate slugs, slugs with underscores, non-http URLs, a login inside the URL, header injection and unknown stores are rejected")

    moved = "http://elsewhere.invalid/mcp"
    r = client.patch(f"/upstream-servers/{upstream['id']}", json={"url": moved}, headers=admin_headers)
    assert r.status_code == 400, r.text
    r = client.get("/upstream-servers", headers=admin_headers)
    assert moved not in r.text, r.text
    r = client.patch(f"/upstream-servers/{upstream['id']}", json={"url": new_upstream["url"], "name": "Renamed"}, headers=admin_headers)
    assert r.status_code == 200 and r.json()["name"] == "Renamed", r.text
    r = client.patch(f"/upstream-servers/{upstream['id']}", json={"url": moved, "credential": "Bearer smoke-secret"}, headers=admin_headers)
    assert r.status_code == 200 and r.json()["url"] == moved and r.json()["has_credential"] is True, r.text
    _ok("a server's URL cannot be changed while keeping its stored credential, unless the credential is sent again")

    r = client.patch(f"/upstream-servers/{upstream['id']}", json={"enabled": False, "store_id": second_store_id}, headers=admin_headers)
    assert r.status_code == 200, r.text
    assert r.json()["enabled"] is False and r.json()["store_id"] == second_store_id and r.json()["has_credential"] is True, r.text
    r = client.patch(f"/upstream-servers/{upstream['id']}", json={"credential": "", "store_id": None}, headers=admin_headers)
    assert r.status_code == 200 and r.json()["has_credential"] is False and r.json()["store_id"] is None, r.text
    assert r.json()["enabled"] is False, r.text
    _ok("edit changes only what is sent: disable, scope to a store, then remove the credential and the scope")

    r = client.delete(f"/upstream-servers/{upstream['id']}", headers=admin_headers)
    assert r.status_code == 204, r.text
    r = client.delete(f"/upstream-servers/{upstream['id']}", headers=admin_headers)
    assert r.status_code == 404, r.text
    _ok("an upstream can be removed, once")

    _step("registry changes are in the gateway's audit log, which only the admin can read")
    r = client.get("/audit-log", headers=member_headers)
    assert r.status_code == 403, r.text
    r = client.get("/audit-log")
    assert r.status_code == 401, r.text
    _ok("a member cannot read the audit log (403), and a caller with no token is not signed in (401)")
    r = client.get("/audit-log", params={"category": "admin", "limit": 200}, headers=admin_headers)
    assert r.status_code == 200, r.text
    mine = [event for event in reversed(r.json()) if event["upstream"] == slug]
    assert [event["action"] for event in mine] == ["upstream.create"] + ["upstream.update"] * 4 + ["upstream.delete"], mine
    for event in mine:
        assert event["user_id"] == admin_id and event["user_email"] == admin_email.lower(), event
        assert event["severity"] == "NOTICE" and event["category"] == "admin" and event["client_ip"], event
    assert mine[0]["detail"]["changes"]["credential"] == "set", mine[0]
    assert mine[4]["detail"]["changes"] == {"credential": "none", "store_id": None}, mine[4]
    assert "smoke-secret" not in r.text, "a credential reached the audit log"
    _ok("every change that was made is there, in order, with who made it; the refused ones and the credential are not")
    r = client.get("/audit-log", params={"limit": 2}, headers=admin_headers)
    seqs = [event["seq"] for event in r.json()]
    assert r.status_code == 200 and len(seqs) == 2 and seqs[0] > seqs[1], r.text
    r = client.get("/audit-log", params={"limit": 2, "before": seqs[-1]}, headers=admin_headers)
    assert r.status_code == 200 and all(event["seq"] < seqs[-1] for event in r.json()), r.text
    for bad in ({"limit": 0}, {"limit": 201}, {"before": "x"}, {"category": "a;b"}):
        assert client.get("/audit-log", params=bad, headers=admin_headers).status_code == 422, bad
    _ok("the log is paged newest first, and bad paging or filter values are rejected")
    r = client.get("/audit-log/verify", headers=member_headers)
    assert r.status_code == 403, r.text
    r = client.get("/audit-log/verify", headers=admin_headers)
    assert r.status_code == 200 and r.json()["intact"] is True and r.json()["verified"] >= 6, r.text
    _ok("the admin can ask whether the gateway's log is untouched; a member cannot")

    _step("team, key and store changes are in the platform's own audit log")
    r = client.get("/audit-log/platform", headers=member_headers)
    assert r.status_code == 403, r.text
    r = client.get("/audit-log/platform", params={"limit": 200}, headers=admin_headers)
    assert r.status_code == 200, r.text
    events = list(reversed(r.json()))
    about_member = [(event["action"], event["user_email"]) for event in events if event["target"] == member_email]
    admin_did, member_did = admin_email.lower(), member_email
    assert about_member[:5] == [
        ("member.create", admin_did), ("api_key.issue", admin_did), ("api_key.revoke", admin_did),
        ("store.member_add", admin_did), ("api_key.issue", member_did),
    ], about_member
    assert ("api_key.revoke", member_did) in about_member and ("store.member_remove", admin_did) in about_member, about_member
    assert [event["action"] for event in events if event["target"] == store["slug"]] == ["store.create"], events
    for event in events:
        assert event["category"] == "platform" and event["severity"] == "NOTICE", event
        # Where it came from, except for what the backend did itself at startup: that has no caller.
        assert event["client_ip"] or (event["detail"] or {}).get("by") == "startup", event
    assert raw_key not in r.text and temp_password not in r.text, "a secret reached the audit log"
    _ok("each change is there with who made it (the admin, or the member for their own key), and no key or password")

    _step("the first store and its MCP server were registered at startup, like any later store")
    first = os.environ.get("FIRST_STORE_SUBDOMAIN", env.get("FIRST_STORE_SUBDOMAIN", "demo"))
    r = client.get("/stores", headers=admin_headers)
    first_store = next((s for s in r.json() if s["slug"] == first), None)
    assert first_store and first_store["status"] == "ready" and first_store["url"], r.text
    r = client.get("/upstream-servers", headers=admin_headers)
    woo = next((u for u in r.json() if u["slug"] == f"woo-{first}"), None)
    assert woo and woo["store_id"] == first_store["id"] and woo["has_credential"] and woo["enabled"], r.text
    # Its store.create event is not looked for here: it was written on the database's first start, and
    # `events` is only the latest 200. tests/test_first_store.py checks that it is recorded.
    _ok(f'the store "{first}" is in the list and its server woo-{first} belongs to it')
    for refused in ("UPDATE admin_events SET action = 'x'", "DELETE FROM admin_events", "TRUNCATE admin_events"):
        assert "append-only" in _psql(repo_root, refused, ok=False), refused
    _ok("rows there cannot be changed, deleted or truncated, even by the database owner")

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

    _step("change password (logged in)")
    new_password = "smoke-new-pass-1"
    r = client.post(
        "/auth/change-password",
        json={"current_password": "not-the-password", "new_password": new_password},
        headers=member_headers,
    )
    assert r.status_code == 400, r.text
    _ok("wrong current password is rejected with 400")

    r = client.post(
        "/auth/change-password",
        json={"current_password": temp_password, "new_password": new_password},
        headers=member_headers,
    )
    assert r.status_code == 204, r.text
    # Proven through change-password rather than a fresh login, to stay
    # inside the login rate limit this script trips on purpose at the end.
    r = client.post(
        "/auth/change-password",
        json={"current_password": temp_password, "new_password": new_password},
        headers=member_headers,
    )
    assert r.status_code == 400, r.text
    _ok("password changed; the old one no longer verifies")

    r = client.post(
        "/auth/change-password",
        json={"current_password": new_password, "new_password": "\U0001F600" * 20},
        headers=member_headers,
    )
    assert r.status_code == 422, r.text
    _ok("a 20-emoji password (80 bytes, over bcrypt's limit) is rejected with 422, not a crash")

    _step("forgot password does not reveal which emails exist")
    r_unknown = client.post("/auth/forgot-password", json={"email": f"nobody-{unique}@example.com"})
    r_known = client.post("/auth/forgot-password", json={"email": other_email})
    assert r_unknown.status_code == r_known.status_code == 202, (r_unknown.text, r_known.text)
    assert r_unknown.json() == r_known.json()
    _ok("known and unknown emails get an identical 202")

    _step("emailed set-password links (invite + reset)")
    reset_token = _emailed_token(repo_root, other_email, "Reset your")
    if reset_token is None:
        print("   SKIP: no logged email found (SMTP_PASSWORD is set, or docker compose logs unavailable)")
    else:
        old_invite = _emailed_token(repo_root, other_email, "You've been invited")
        r = client.post(f"/team/members/{other_member_id}/resend-invite", headers=admin_headers)
        assert r.status_code == 204, r.text
        new_invite = _emailed_token(repo_root, other_email, "You've been invited")
        assert old_invite and new_invite and old_invite != new_invite

        r = client.post("/auth/set-password", json={"token": old_invite, "new_password": new_password})
        assert r.status_code == 400, r.text
        _ok("resending an invite kills the previous link")

        r = client.post("/auth/set-password", json={"token": new_invite, "new_password": new_password})
        assert r.status_code == 200 and r.json()["user"]["id"] == other_member_id, r.text
        _ok("invite link sets the password and logs the member in")

        r = client.post("/auth/set-password", json={"token": new_invite, "new_password": new_password})
        assert r.status_code == 400, r.text
        _ok("a link works only once")

        r = client.post("/auth/set-password", json={"token": reset_token, "new_password": new_password})
        assert r.status_code == 400, r.text
        _ok("setting a password kills the user's other outstanding links")

        r = client.post("/auth/forgot-password", json={"email": other_email})
        assert r.status_code == 202, r.text
        fresh_reset = _emailed_token(repo_root, other_email, "Reset your")
        assert fresh_reset and fresh_reset != reset_token
        _expire_auth_tokens(repo_root, other_member_id)
        r = client.post("/auth/set-password", json={"token": fresh_reset, "new_password": new_password})
        assert r.status_code == 400, r.text
        _ok("an expired link is rejected")

    # The agent's answer depends on the model (and is "not set up" without a key), so only
    # the shape of the reply and the rules around the chat are checked here.
    _step("a chat stays open: one conversation, one chat token, never shown to the caller")
    r = client.post("/chat/message", json={"message": "what orders came in today?"}, headers=member_headers)
    assert r.status_code == 200, r.text
    reply = r.json()
    assert reply["reply"] and reply["status"] in ("answered", "needs_confirmation", "stopped", "failed"), reply
    assert reply["store_ids"] == [second_store_id], reply
    assert isinstance(reply["blocks"], list) and isinstance(reply["steps"], list) and "chat_" not in r.text, r.text
    conversation_id = reply["conversation_id"]
    _ok("chat reply received with its results; the raw token is not in it")

    r = client.post("/chat/message", json={"message": "a follow-up question"}, headers=member_headers)
    assert r.status_code == 200 and r.json()["conversation_id"] == conversation_id, r.text
    r = client.get("/chat", headers=member_headers)
    assert r.status_code == 200 and r.json()["conversation_id"] == conversation_id, r.text
    assert [line["from"] for line in r.json()["messages"]][-4:] == ["you", "agent", "you", "agent"], r.text
    live = _psql(repo_root, f"SELECT count(*), bool_and(ciphertext IS NOT NULL) FROM chat_tokens WHERE user_id = '{member_id}' AND NOT revoked").strip()
    assert live == "1|t", live
    _ok("a second message continues the same chat on the same token, and a reload gets the whole chat back")

    _step("ending a chat revokes its token; the chat can then be read, by its owner only, and not continued")
    r = client.post("/chat/end", headers=member_headers)
    assert r.status_code == 204, r.text
    assert _psql(repo_root, f"SELECT count(*) FROM chat_tokens WHERE user_id = '{member_id}' AND NOT revoked").strip() == "0"
    r = client.get("/chat", headers=member_headers)
    assert r.status_code == 200 and r.json()["conversation_id"] is None and r.json()["messages"] == [], r.text
    r = client.get("/chat/history", headers=member_headers)
    assert r.status_code == 200 and conversation_id in [chat["conversation_id"] for chat in r.json()], r.text
    r = client.get(f"/chat/history/{conversation_id}", headers=member_headers)
    assert r.status_code == 200 and r.json()["messages"] and r.json()["pending"] == [], r.text
    r = client.get(f"/chat/history/{conversation_id}", headers=admin_headers)
    assert r.status_code == 404, r.text
    r = client.post("/chat/confirm", json={"approve": True}, headers=member_headers)
    assert r.status_code == 409, r.text
    r = client.post("/chat/message", json={"message": "a new chat"}, headers=member_headers)
    assert r.status_code == 200 and r.json()["conversation_id"] != conversation_id, r.text
    _ok("ended chat: token revoked, listed and readable for its owner, 404 for anyone else; the next message opens a new one")

    _step("chat has its own rate limit bucket, apart from the dashboard's")
    for _ in range(12):  # chat allows a burst of 10 per minute
        if client.post("/chat/message", json={"message": "burst"}, headers=member_headers).status_code == 429:
            break
    else:
        raise AssertionError("expected chat to hit its 10/min limit within 12 messages")
    r = client.get("/stores", headers=member_headers)
    assert r.status_code == 200, r.text  # a shared bucket would still be empty here
    _ok("an exhausted chat limit does not block the dashboard")

    _step("sign out ends the session at once, and the chat tokens with it")
    chat_hash = _psql(repo_root, f"SELECT token_hash FROM chat_tokens WHERE user_id = '{member_id}' AND NOT revoked").strip()
    assert chat_hash and _redis(repo_root, "exists", f"chat_token:{chat_hash}") == "1", chat_hash
    r = client.post("/auth/logout", headers=member_headers)
    assert r.status_code == 204, r.text
    r = client.get("/auth/me", headers=member_headers)
    assert r.status_code == 401, r.text
    _ok("a signed-out token is refused on its next use, although it has hours left")
    assert _psql(repo_root, f"SELECT count(*) FROM chat_tokens WHERE user_id = '{member_id}' AND NOT revoked").strip() == "0"
    assert _redis(repo_root, "exists", f"chat_token:{chat_hash}") == "0"
    _ok("the member's chat token is revoked too, in the database and in the gateway's lookup cache")
    r = browser.post("/auth/logout", headers={"Origin": app_origin})
    assert r.status_code == 204 and 'urumi_session=""' in r.headers["set-cookie"] and "max-age=0" in r.headers["set-cookie"].lower(), r.headers
    r = client.get("/auth/me", headers=admin_headers)  # the same token the cookie held
    assert r.status_code == 401, r.text
    r = httpx.get(f"{BASE_URL}/auth/session", cookies={"urumi_session": session_cookie})
    assert r.status_code == 200 and r.json() == {"user": None}, r.text
    browser.close()
    _ok("signing out in the browser clears the cookie and kills the token inside it")
    r = client.post("/auth/logout")
    assert r.status_code == 204, r.text
    _ok("signing out when nobody is signed in is not an error")

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
