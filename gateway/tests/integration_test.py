"""Assert-based end-to-end test of the gateway against the LIVE compose stack
(real backend, Postgres, Redis, and the two mock upstreams). From the repo root:

    docker compose --profile test up -d --build
    gateway/.venv/Scripts/python gateway/tests/integration_test.py     (Windows)
    gateway/.venv/bin/python gateway/tests/integration_test.py         (Linux/macOS)

It registers upstreams through the backend's admin API, then talks to the
gateway the way Claude Code or MCP Inspector would, with members' API keys.
It removes the upstreams it created; the two members and two stores stay.

With SMTP_PASSWORD set, one run really sends 11 emails (2 invites and 9
notices) to made-up @example.com addresses. It takes about two minutes: one
step waits out the gateway's 30-second call timeout, and another stops a mock
upstream and waits for the gateway's 30-second refresh to notice, twice.
"""
import asyncio
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path

import httpx2
from mcp import Client
from mcp.client.streamable_http import streamable_http_client

BACKEND = os.environ.get("BACKEND_URL", "http://localhost:8000")
GATEWAY = os.environ.get("GATEWAY_URL", "http://localhost:8080")
REPO_ROOT = Path(__file__).resolve().parents[2]
MOCK = "http://mock-upstream:9000/mcp"
SECRET = "Bearer mock-secret"


def _env(name: str) -> str:
    if name in os.environ:
        return os.environ[name]
    for line in (REPO_ROOT / ".env").read_text().splitlines():
        key, _, value = line.partition("=")
        if key.strip() == name:
            return value.strip()
    raise AssertionError(f"{name} not found in env or repo-root .env")


def _ok(label: str) -> None:
    print(f"   PASS: {label}")


def _compose(*args: str) -> None:
    done = subprocess.run(
        ["docker", "compose", "--profile", "test", *args], cwd=REPO_ROOT, capture_output=True, text=True, timeout=60
    )
    assert done.returncode == 0, done.stderr


def _redis_get(key: str) -> str:
    done = subprocess.run(
        ["docker", "compose", "exec", "-T", "redis", "redis-cli", "get", key],
        cwd=REPO_ROOT, capture_output=True, text=True, timeout=20,
    )
    return done.stdout.strip()


class Session:
    """One MCP client connection to the gateway, as a given API key."""

    def __init__(self, token: str) -> None:
        self._http = httpx2.AsyncClient(headers={"Authorization": f"Bearer {token}"}, timeout=httpx2.Timeout(60))
        self._client = Client(streamable_http_client(f"{GATEWAY}/mcp", http_client=self._http))

    async def __aenter__(self) -> Client:
        await self._http.__aenter__()
        return await self._client.__aenter__()

    async def __aexit__(self, *exc) -> None:
        await self._client.__aexit__(*exc)
        await self._http.__aexit__(*exc)


async def _tool_names(token: str) -> list[str]:
    async with Session(token) as client:
        return sorted(tool.name for tool in (await client.list_tools()).tools)


async def _wait_for_tools(token: str, wanted: set[str], gone: set[str] = frozenset(), seconds: float = 10) -> list[str]:
    """The gateway reloads on the backend's Redis message; give it a few seconds."""
    deadline = time.monotonic() + seconds
    while True:
        names = await _tool_names(token)
        if wanted <= set(names) and not (gone & set(names)):
            return names
        if time.monotonic() > deadline:
            break
        await asyncio.sleep(0.25)
    raise AssertionError(f"wanted {sorted(wanted)} and not {sorted(gone)}, gateway lists {names}")


async def main() -> int:
    unique = uuid.uuid4().hex[:8]
    api = httpx2.AsyncClient(base_url=BACKEND, timeout=10)
    raw = httpx2.AsyncClient(timeout=10)
    created: list[str] = []
    admin_key_id: str | None = None

    r = await api.post("/auth/login", json={"email": _env("ADMIN_EMAIL"), "password": _env("ADMIN_PASSWORD")})
    assert r.status_code == 200, r.text
    admin = {"Authorization": f"Bearer {r.json()['access_token']}"}

    async def member(label: str) -> tuple[str, str]:
        r = await api.post("/team/members", json={"email": f"gw-{label}-{unique}@example.com", "name": f"Gateway {label}"}, headers=admin)
        assert r.status_code == 201, r.text
        user_id = r.json()["user"]["id"]
        r = await api.post(f"/team/members/{user_id}/api-keys", json={}, headers=admin)
        assert r.status_code == 201, r.text
        return user_id, r.json()["raw_key"]

    async def store(label: str, user_id: str) -> str:
        r = await api.post("/stores", json={"name": f"GW {label} {unique}"}, headers=admin)
        assert r.status_code == 201, r.text
        store_id = r.json()["id"]
        r = await api.post(f"/stores/{store_id}/members", json={"user_id": user_id}, headers=admin)
        assert r.status_code == 201, r.text
        return store_id

    async def upstream(slug: str, url: str = MOCK, credential: str | None = SECRET, store_id: str | None = None) -> dict:
        body = {"name": slug, "slug": slug, "url": url, "credential": credential, "store_id": store_id}
        r = await api.post("/upstream-servers", json=body, headers=admin)
        assert r.status_code == 201, r.text
        created.append(r.json()["id"])
        return r.json()

    try:
        print("-- setup: two members, each in their own store, and six upstreams")
        alice_id, alice = await member("alice")
        bob_id, bob = await member("bob")
        store_a, store_b = await store("A", alice_id), await store("B", bob_id)
        shared, alpha, beta = f"shared-{unique}", f"alpha-{unique}", f"beta-{unique}"
        hung, dead, wrong = f"hung-{unique}", f"dead-{unique}", f"wrong-{unique}"
        await upstream(shared)
        alpha_row = await upstream(alpha, store_id=store_a)
        await upstream(beta, store_id=store_b)
        hung_row = await upstream(hung, url="http://mock-hung:9000/mcp")
        await upstream(dead, url="http://mock-upstream:9999/mcp")
        wrong_row = await upstream(wrong, credential="Bearer not-the-secret")

        print("-- only authenticated callers get in")
        body = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
        headers = {"accept": "application/json, text/event-stream", "content-type": "application/json"}
        r = await raw.post(f"{GATEWAY}/mcp", json=body, headers=headers)
        assert r.status_code == 401 and "Bearer" in r.headers.get("www-authenticate", ""), (r.status_code, r.headers)
        r = await raw.post(f"{GATEWAY}/mcp", json=body, headers={**headers, "authorization": "Bearer umk_not-a-real-key"})
        assert r.status_code == 401, r.text
        r = await raw.post(f"{GATEWAY}/mcp?token={alice}", json=body, headers=headers)
        assert r.status_code == 401, r.text
        _ok("no token, an unknown token and a token in the URL are all refused with 401")
        await asyncio.sleep(1)
        logs = subprocess.run(
            ["docker", "compose", "logs", "--no-color", "--since", "2m", "gateway"],
            cwd=REPO_ROOT, capture_output=True, text=True, timeout=20,
        )
        assert logs.returncode == 0 and alice not in logs.stdout + logs.stderr, "the key sent in the URL is in the gateway log"
        _ok("and the key that was sent in the URL is not written to the gateway's log")
        r = await raw.post(f"{GATEWAY}/mcp", json=body, headers={**headers, "authorization": f"Bearer {alice}", "host": "evil.example.com"})
        assert r.status_code == 421, r.text
        _ok("a request for a hostname the gateway does not serve is refused, even with a valid key")

        print("-- listing: prefixed names, store scoping, broken upstreams left out")
        mine = {f"{shared}__list_orders", f"{alpha}__list_orders"}
        names = await _wait_for_tools(alice, mine)
        assert not [n for n in names if n.startswith((beta, hung, dead, wrong))], names
        _ok("alice sees the shared server and her store's server, both offering list_orders, told apart by prefix")
        names = await _tool_names(bob)
        assert f"{beta}__list_orders" in names and not [n for n in names if n.startswith(alpha)], names
        _ok("bob sees his store's server and not alice's")
        started = time.monotonic()
        await _tool_names(alice)
        elapsed = time.monotonic() - started
        assert elapsed < 2, f"tools/list took {elapsed:.1f}s with a hung upstream registered"
        _ok(f"a hung, a dead and a wrongly-credentialed upstream do not slow listing ({elapsed:.2f}s) or appear in it")
        # The refresh that includes the last-registered upstream only ends once
        # the hung one's probe has timed out (5 s); wait for it before going on.
        for _ in range(60):
            if _redis_get(f"upstream:{wrong_row['id']}:status"):
                break
            await asyncio.sleep(0.5)
        assert _redis_get(f"upstream:{wrong_row['id']}:status") == "unreachable"
        assert _redis_get(f"upstream:{alpha_row['id']}:status") == "connected"
        assert _redis_get(f"upstream:{hung_row['id']}:status") == "unreachable"
        _ok("each upstream's status is published for the dashboard: connected / unreachable")

        print("-- calling")
        async with Session(alice) as client:
            result = await client.call_tool(f"{shared}__list_orders", {})
            assert not result.is_error and result.content[0].text == "list_orders ok", result
            _ok("a call reaches the upstream with the stored credential (the mock rejects anything else)")
            result = await client.call_tool(f"{beta}__list_orders", {})
            assert result.is_error and "Unknown tool" in result.content[0].text, result
            result = await client.call_tool("no-such-server__x", {})
            assert result.is_error and "Unknown tool" in result.content[0].text, result
            _ok("another store's tool is refused with the same answer as a tool that does not exist")
            result = await client.call_tool(f"{dead}__list_orders", {})
            assert result.is_error and "unreachable" in result.content[0].text, result
            _ok("a call to a dead upstream returns a clear error")
            started = time.monotonic()
            result = await client.call_tool(f"{shared}__slow", {})
            elapsed = time.monotonic() - started
            assert result.is_error and "did not answer" in result.content[0].text and elapsed < 40, (elapsed, result)
            _ok(f"a call that never returns is cut off at the timeout ({elapsed:.0f}s) with a clear error")
            result = await client.call_tool(f"{shared}__list_orders", {})
            assert not result.is_error, result
            _ok("and the gateway keeps working afterwards")

        print("-- several stores, and the admin")
        r = await api.post(f"/stores/{store_a}/members", json={"user_id": bob_id}, headers=admin)
        assert r.status_code == 201, r.text
        names = await _tool_names(bob)
        assert {f"{alpha}__list_orders", f"{beta}__list_orders"} <= set(names), names
        async with Session(bob) as client:
            for slug in (alpha, beta):
                result = await client.call_tool(f"{slug}__list_orders", {})
                assert not result.is_error, result
        _ok("a member in two stores lists and calls both stores' tools in one session")
        r = await api.delete(f"/stores/{store_a}/members/{bob_id}", headers=admin)
        assert r.status_code == 204, r.text
        assert not [n for n in await _tool_names(bob) if n.startswith(alpha)]
        _ok("and loses the first store's tools again the moment he is removed from it")

        r = await api.get("/team/me/api-keys", headers=admin)
        assert r.status_code == 200, r.text
        if any(not key["revoked"] for key in r.json()):
            print("   SKIP: the admin already has an API key of their own; not replacing it")
        else:
            r = await api.post("/team/me/api-keys", json={"name": "gateway integration test"}, headers=admin)
            assert r.status_code == 201, r.text
            admin_key_id, admin_key = r.json()["id"], r.json()["raw_key"]
            names = set(await _tool_names(admin_key))
            assert {f"{shared}__list_orders", f"{alpha}__list_orders", f"{beta}__list_orders"} <= names, names
            _ok("the admin sees every store's tools without being a member of any store")

        print("-- many calls at once")
        async with Session(alice) as client:
            started = time.monotonic()
            results = await asyncio.gather(*(client.call_tool(f"{shared}__list_orders", {}) for _ in range(30)))
            elapsed = time.monotonic() - started
        assert all(not result.is_error for result in results), [r for r in results if r.is_error][:2]
        assert elapsed < 15, f"30 concurrent calls took {elapsed:.1f}s"
        _ok(f"30 concurrent calls all succeed ({elapsed:.1f}s)")

        print("-- control: changes take effect without restarting anything")
        r = await api.patch(f"/upstream-servers/{alpha_row['id']}", json={"enabled": False}, headers=admin)
        assert r.status_code == 200, r.text
        await _wait_for_tools(alice, {f"{shared}__list_orders"}, gone={f"{alpha}__list_orders"})
        async with Session(alice) as client:
            result = await client.call_tool(f"{alpha}__list_orders", {})
            assert result.is_error and "Unknown tool" in result.content[0].text, result
        _ok("disabling an upstream removes its tools and refuses calls to it")

        r = await api.delete(f"/stores/{store_b}/members/{bob_id}", headers=admin)
        assert r.status_code == 204, r.text
        assert not [n for n in await _tool_names(bob) if n.startswith(beta)]
        _ok("removing bob from his store hides its tools on his very next request")

        r = await api.patch(f"/upstream-servers/{wrong_row['id']}", json={"credential": SECRET}, headers=admin)
        assert r.status_code == 200, r.text
        await _wait_for_tools(alice, {f"{wrong}__list_orders"})
        _ok("correcting an upstream's credential brings its tools in")

        gamma = f"gamma-{unique}"
        gamma_row = await upstream(gamma, store_id=store_b)
        await _wait_for_tools(alice, {f"{wrong}__list_orders"}, gone={f"{gamma}__list_orders"})
        r = await api.patch(f"/upstream-servers/{gamma_row['id']}", json={"store_id": None}, headers=admin)
        assert r.status_code == 200, r.text
        await _wait_for_tools(alice, {f"{gamma}__list_orders"})
        _ok("making a store's server shared shows it to members of other stores")
        r = await api.delete(f"/upstream-servers/{gamma_row['id']}", headers=admin)
        assert r.status_code == 204, r.text
        created.remove(gamma_row["id"])
        await _wait_for_tools(alice, {f"{shared}__list_orders"}, gone={f"{gamma}__list_orders"})
        _ok("removing an upstream removes its tools")

        print("-- an upstream dies and comes back (nothing tells the gateway; it notices on its own)")
        victim = f"victim-{unique}"
        victim_row = await upstream(victim, url="http://mock-b:9000/mcp")
        await _wait_for_tools(alice, {f"{victim}__list_orders"})
        _compose("kill", "mock-b")
        async with Session(alice) as client:
            result = await client.call_tool(f"{victim}__list_orders", {})
            assert result.is_error and "unreachable" in result.content[0].text, result
            result = await client.call_tool(f"{shared}__list_orders", {})
            assert not result.is_error, result
        _ok("right after the kill: a call to it fails clearly, and the other upstreams keep working")
        await _wait_for_tools(alice, {f"{shared}__list_orders"}, gone={f"{victim}__list_orders"}, seconds=50)
        assert _redis_get(f"upstream:{victim_row['id']}:status") == "unreachable"
        _ok("within one refresh its tools are gone and its status is unreachable")
        _compose("start", "mock-b")
        await _wait_for_tools(alice, {f"{victim}__list_orders"}, seconds=50)
        assert _redis_get(f"upstream:{victim_row['id']}:status") == "connected"
        _ok("once it is back, its tools and its connected status return by themselves")

        print("-- access is cut off at once")
        r = await api.get(f"/team/members/{alice_id}/api-keys", headers=admin)
        assert r.status_code == 200, r.text
        key_id = next(key["id"] for key in r.json() if not key["revoked"])
        r = await api.post(f"/team/members/{alice_id}/api-keys/{key_id}/revoke", headers=admin)
        assert r.status_code == 200, r.text
        r = await raw.post(f"{GATEWAY}/mcp", json=body, headers={**headers, "authorization": f"Bearer {alice}"})
        assert r.status_code == 401, r.text
        _ok("revoking alice's key rejects her next call")

        r = await raw.post(f"{GATEWAY}/mcp", json=body, headers={**headers, "authorization": f"Bearer {bob}"})
        assert r.status_code == 200, r.text
        r = await api.delete(f"/team/members/{bob_id}", headers=admin)
        assert r.status_code == 204, r.text
        r = await raw.post(f"{GATEWAY}/mcp", json=body, headers={**headers, "authorization": f"Bearer {bob}"})
        assert r.status_code == 401, r.text
        _ok("deactivating bob rejects his next call, though his key was never revoked")
    finally:
        _compose("start", "mock-b")
        if admin_key_id:
            await api.post(f"/team/me/api-keys/{admin_key_id}/revoke", headers=admin)
        for upstream_id in created:
            await api.delete(f"/upstream-servers/{upstream_id}", headers=admin)
        await api.aclose()
        await raw.aclose()

    print("\nALL CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
