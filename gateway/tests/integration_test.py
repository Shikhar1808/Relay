"""Assert-based end-to-end test of the gateway against the LIVE compose stack
(real backend, Postgres, Redis, and the two mock upstreams). From the repo root:

    docker compose --profile test up -d --build
    gateway/.venv/Scripts/python gateway/tests/integration_test.py     (Windows)
    gateway/.venv/bin/python gateway/tests/integration_test.py         (Linux/macOS)

It registers upstreams through the backend's admin API, then talks to the
gateway the way Claude Code or MCP Inspector would, with members' API keys.
It removes the upstreams it created; the two members and two stores stay, and
so do the audit rows (the log is append-only). One step edits an audit row as
the database owner to prove that verification notices, and puts it back.
Another stops the gateway for a moment to prove that the registry cannot be
changed while the audit log cannot record it.

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
        ["docker", "compose", "--profile", "test", *args], cwd=REPO_ROOT, capture_output=True, text=True, timeout=180
    )
    assert done.returncode == 0, done.stderr


def _logs(sql: str, user: str = '"$POSTGRES_USER"', ok: bool = True, db: str = "mcp_logs") -> str:
    """Runs SQL in the audit log database (or `db`); `ok=False` expects the database to refuse it."""
    done = subprocess.run(
        ["docker", "compose", "exec", "-T", "postgres", "sh", "-c", f'psql -U {user} -d {db} -At -v ON_ERROR_STOP=1 -c "{sql}"'],
        cwd=REPO_ROOT, capture_output=True, text=True, timeout=20,
    )
    assert (done.returncode == 0) == ok, (sql, done.stdout, done.stderr)
    return (done.stdout + done.stderr).strip()


def _verify_audit() -> tuple[int, str]:
    done = subprocess.run(
        ["docker", "compose", "exec", "-T", "gateway", "python", "-m", "app.audit"],
        cwd=REPO_ROOT, capture_output=True, text=True, timeout=60,
    )
    return done.returncode, done.stdout + done.stderr


def _forget_limits() -> None:
    subprocess.run(
        ["docker", "compose", "exec", "-T", "redis", "sh", "-c",
         'redis-cli --scan --pattern "ratelimit:gateway:*" | xargs -r redis-cli del'],
        cwd=REPO_ROOT, capture_output=True, text=True, timeout=20,
    )


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


async def _wait_for_tools(token: str, wanted: set[str], gone: set[str] = frozenset(), seconds: float = 20) -> list[str]:
    """The gateway reloads on the backend's Redis message. With the hung mock registered a reload takes 5 s, and a change can land behind one already running."""
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
        r = await api.post("/stores", json={"name": f"GW {label} {unique}", "subdomain": f"gw-{label.lower()}-{unique}"}, headers=admin)
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
        beta_row = await upstream(beta, store_id=store_b)
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

        print("-- tools kept for admins")
        locked, kept = f"{beta}__list_orders", {"admin_only_tools": ["list_orders"]}
        r = await api.patch(f"/upstream-servers/{beta_row['id']}", json=kept, headers=admin)
        assert r.status_code == 200 and r.json()["admin_only_tools"] == ["list_orders"], r.text
        await _wait_for_tools(bob, set(), {locked})
        async with Session(bob) as client:
            result = await client.call_tool(locked, {})
            assert result.is_error and result.content[0].text == f"Unknown tool: {locked}", result
        _ok("a tool the admin keeps for admins is gone from the member's list and refused if called directly")
        assert _logs(f"SELECT severity || ' ' || outcome || ' ' || upstream || ' ' || tool FROM audit_log WHERE user_id = '{bob_id}' AND outcome = 'denied'") == f"WARNING denied {beta} list_orders"
        _ok("and the refusal names the real server and tool in the audit log")
        r = await api.patch(f"/upstream-servers/{beta_row['id']}", json={"admin_only_tools": []}, headers=admin)
        assert r.status_code == 200 and r.json()["admin_only_tools"] == [], r.text
        await _wait_for_tools(bob, {locked})
        async with Session(bob) as client:
            result = await client.call_tool(locked, {})
            assert not result.is_error and result.content[0].text == "list_orders ok", result
        _ok("unlocking it brings it back on the member's next request, with no restart")

        print("-- the audit log")
        mine = f"FROM audit_log WHERE user_id = '{alice_id}'"
        got = _logs(f"SELECT severity || ' ' || outcome || ' ' || coalesce(upstream, '-') || ' ' || tool {mine} ORDER BY seq").splitlines()
        assert got == [
            f"INFO ok {shared} list_orders",
            f"WARNING denied {beta} list_orders",
            "WARNING denied - no-such-server__x",
            f"ERROR unreachable {dead} list_orders",
            f"ERROR timeout {shared} slow",
            f"INFO ok {shared} list_orders",
        ], got
        _ok("every call alice made is there, in order, with a severity: ok, refused, unreachable, timed out")
        assert _logs(f"SELECT count(*) {mine} AND (client_ip IS NULL OR duration_ms IS NULL OR detail NOT LIKE '%args_hash%api_key%')") == "0"
        assert int(_logs(f"SELECT duration_ms {mine} AND outcome = 'timeout'")) >= 29000
        _ok("each row has the caller's address, the duration, the token type and a hash of the arguments")
        assert int(_logs("SELECT count(*) FROM audit_log WHERE category = 'auth' AND outcome = 'rejected' AND ts > now() - interval '10 minutes'")) >= 3
        assert _logs(f"SELECT count(*) FROM audit_log WHERE audit_log::text LIKE '%{alice}%'") == "0"
        _ok("the refused logins are recorded too, and the key sent in the URL is nowhere in the log")
        assert _logs(f"SELECT severity FROM audit_log WHERE category = 'upstream_health' AND upstream = '{dead}'") == "ERROR"
        _ok("an upstream that is down is one ERROR event, not one per health check")

        r = await api.get("/audit-log", params={"category": "tool_call", "limit": 200}, headers=admin)
        assert r.status_code == 200, r.text
        seen = [event for event in r.json() if event["user_email"] == f"gw-alice-{unique}@example.com"]
        assert len(seen) == 6 and seen[0]["seq"] > seen[-1]["seq"], r.text
        assert seen[-1]["upstream"] == shared and seen[-1]["outcome"] == "ok" and seen[-1]["detail"]["token_type"] == "api_key", seen[-1]
        r = await api.get("/audit-log", params={"category": "admin", "limit": 200}, headers=admin)
        assert r.status_code == 200 and "mock-secret" not in r.text, r.text
        added = {event["upstream"]: event for event in r.json() if event["action"] == "upstream.create"}
        assert added[shared]["detail"]["changes"]["credential"] == "set" and added[alpha]["detail"]["changes"]["store_id"] == store_a, added
        assert added[shared]["user_email"] == _env("ADMIN_EMAIL").lower() and added[shared]["severity"] == "NOTICE", added[shared]
        _ok("the admin reads the log through the backend, with each user's email; registry changes are in it, without the credential")

        seq = _logs(f"SELECT seq {mine} AND outcome = 'denied' ORDER BY seq LIMIT 1")
        for statement in (f"UPDATE audit_log SET outcome = 'ok' WHERE seq = {seq}", f"DELETE FROM audit_log WHERE seq = {seq}", "TRUNCATE audit_log"):
            assert "permission denied" in _logs(statement, user="gateway", ok=False)
            assert "append-only" in _logs(statement, ok=False)
        _ok("rows cannot be changed, deleted or truncated: not by the gateway's role, not by the database owner")

        code, output = _verify_audit()
        assert code == 0 and "chain intact" in output, output
        tamper = f"ALTER TABLE audit_log DISABLE TRIGGER audit_log_no_change; UPDATE audit_log SET outcome = '%s' WHERE seq = {seq}; ALTER TABLE audit_log ENABLE TRIGGER audit_log_no_change"
        try:
            _logs(tamper % "ok")  # what an attacker with owner rights could do: switch the rule off and hide a refusal
            code, output = _verify_audit()
            assert code == 1 and f"BROKEN at seq {seq}" in output, output
            r = await api.get("/audit-log/verify", headers=admin)
            assert r.status_code == 200 and r.json()["intact"] is False and r.json()["broken_seq"] == int(seq), r.text
        finally:
            _logs(tamper % "denied")
        code, output = _verify_audit()
        assert code == 0, output
        r = await api.get("/audit-log/verify", headers=admin)
        assert r.status_code == 200 and r.json()["intact"] is True and r.json()["broken_seq"] is None, r.text
        _ok("a row edited behind the rule's back is found, at the right row, by `python -m app.audit` and by the dashboard's check")

        print("-- too many refused logins")
        statuses = []
        while len(statuses) < 30 and (not statuses or statuses[-1] != 429):
            r = await raw.post(f"{GATEWAY}/mcp", json=body, headers={**headers, "authorization": "Bearer umk_a-guess"})
            statuses.append(r.status_code)
        assert statuses[-1] == 429 and set(statuses[:-1]) == {401} and len(statuses) <= 21, statuses
        r = await raw.post(f"{GATEWAY}/mcp", json=body, headers={**headers, "authorization": f"Bearer {alice}"})
        assert r.status_code == 429 and r.headers.get("retry-after") == "60", (r.status_code, r.headers)
        assert int(_logs("SELECT count(*) FROM audit_log WHERE category = 'auth' AND severity = 'WARNING' AND ts > now() - interval '1 minute'")) >= 1
        _ok(f"after {len(statuses) - 1} refused logins the address gets 429 before its token is looked at, and the log has a WARNING")
        _forget_limits()
        r = await raw.post(f"{GATEWAY}/mcp", json=body, headers={**headers, "authorization": f"Bearer {alice}"})
        assert r.status_code == 200, r.text
        _ok("and is let in again once the block is over")

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

        _forget_limits()
        async with Session(alice) as client:
            started = time.monotonic()
            results = await asyncio.gather(*(client.call_tool(f"{shared}__list_orders", {}) for _ in range(160)))
            elapsed = time.monotonic() - started
            refused = [result for result in results if result.is_error]
            passed = len(results) - len(refused)
            # CALLS_PER_MINUTE is 120: a full bucket, plus the 2 a second that came back while the burst ran.
            assert 120 <= passed <= 120 + 2 * elapsed + 2 and refused, (passed, elapsed)
            assert all("Rate limit" in result.content[0].text for result in refused), refused[:1]
            assert _logs(f"SELECT severity || ' ' || count(*) {mine} AND outcome = 'rate_limited' GROUP BY severity") == "WARNING 1"
            await asyncio.sleep(1.5)
            result = await client.call_tool(f"{shared}__list_orders", {})
            assert not result.is_error, result
        async with Session(bob) as client:
            result = await client.call_tool(f"{shared}__list_orders", {})
            assert not result.is_error, result
        _ok(f"a burst of 160 calls: {passed} pass (a bucket of 120, plus what refilled in {elapsed:.1f}s), the rest are refused and recorded once")
        _ok("a moment later the same user can call again, and another user was never affected")
        _forget_limits()

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

        def watched(slug: str) -> list[str]:
            return _logs(f"SELECT action || coalesce(' ' || detail, '') FROM audit_log WHERE category = 'registry' AND upstream = '{slug}' ORDER BY seq").splitlines()

        assert watched(alpha) == ["upstream.added", "upstream.removed"], watched(alpha)
        assert watched(wrong) == ["upstream.added", 'upstream.changed {"changed": ["credential"]}'], watched(wrong)
        _ok("the gateway records the registry changes it sees itself: added, no longer served, credential changed")

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
        health = _logs(f"SELECT severity || ' ' || outcome FROM audit_log WHERE category = 'upstream_health' AND upstream = '{victim}' ORDER BY seq")
        assert health.splitlines() == ["ERROR unreachable", "NOTICE connected"], health
        _ok("and the audit log has both moments")

        print("-- a change made in the database, behind the API's back (nothing tells the gateway)")
        _logs(f"UPDATE upstream_servers SET url = '{MOCK}' WHERE id = '{victim_row['id']}'", db='\"$POSTGRES_DB\"')
        deadline = time.monotonic() + 50
        while not (seen := [line for line in watched(victim) if line.startswith("upstream.changed")]) and time.monotonic() < deadline:
            await asyncio.sleep(1)
        assert seen == ['upstream.changed {"changed": ["url"]}'], watched(victim)
        assert _logs(f"SELECT count(*) FROM audit_log WHERE category = 'admin' AND action = 'upstream.update' AND upstream = '{victim}'") == "0"
        _ok("is noticed within one refresh: the log has the changed URL, and no admin event that explains it")

        print("-- the registry cannot change while the audit log cannot record it")
        unrecorded = f"unrecorded-{unique}"
        _compose("stop", "gateway")
        try:
            r = await api.post("/upstream-servers", json={"name": unrecorded, "slug": unrecorded, "url": MOCK}, headers=admin)
            assert r.status_code == 503 and "audit log" in r.text, (r.status_code, r.text)
            r = await api.patch(f"/upstream-servers/{victim_row['id']}", json={"enabled": False}, headers=admin)
            assert r.status_code == 503, r.text
        finally:
            _compose("up", "-d", "--wait", "gateway")
        r = await api.get("/upstream-servers", headers=admin)
        rows = {row["slug"]: row for row in r.json()}
        assert unrecorded not in rows and rows[victim]["enabled"] is True, r.text
        _ok("with the gateway stopped, adding or disabling a server is refused with 503, and nothing was changed")
        await _wait_for_tools(alice, {f"{shared}__list_orders"}, seconds=60)

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

        code, output = _verify_audit()
        assert code == 0 and "chain intact" in output, output
        _ok("after all of this, 30 calls at once included, the audit chain still verifies")
    finally:
        _forget_limits()
        _compose("start", "mock-b")
        _compose("up", "-d", "--wait", "gateway")  # the clean-up below is recorded in its audit log
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
