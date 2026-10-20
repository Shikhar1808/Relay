"""The store worker: creates and deletes stores in the cluster. Run as `python -m app.worker`.

The API only writes store rows (app/api/routes/stores.py). Every few seconds this reads
the rows that need work and runs each store in its own task, so several stores are set
up at once. Every step is safe to repeat, so a restarted worker simply starts the store
again from the top and finishes it.

It runs in its own pod with the backend's image plus helm and kubectl, and is the only
part of the platform that may talk to the Kubernetes API. Its rights inside a store's
namespace come from a RoleBinding it makes there (relay-store-installer), so it can
install charts only in the namespaces it made.
"""
import asyncio
import base64
import json
import logging
import pathlib
import secrets
import uuid

from redis.asyncio import from_url
from sqlalchemy import delete, select, update

from app.api.routes.upstreams import UPSTREAMS_CHANGED
from app.core.config import settings
from app.core.crypto import encrypt
from app.db.models import Store, StoreStatus, UpstreamServer, User
from app.db.session import AsyncSessionLocal
from app.services.gateway_audit import record_and_commit
from app.services.stores import WOO_ADMIN_ONLY_TOOLS

log = logging.getLogger("app.worker")

CHART = "/charts/store"  # helm/store, copied into the worker image
WP_INIT = "/charts/wp-init.sh"  # woo-mcp/deploy/wp-init.sh, which the chart's wp-init Job runs
INSTALLER_ROLE = "relay-store-installer"
HEARTBEAT = pathlib.Path("/tmp/worker-alive")  # the liveness probe checks it is fresh
BUSY = (StoreStatus.pending, StoreStatus.provisioning, StoreStatus.deleting)
POLL_SECONDS = 3

redis = from_url(settings.redis_url, decode_responses=True)


class CommandFailed(Exception):
    pass


async def run(*args: str, stdin: str | None = None) -> str:
    """Runs kubectl or helm and returns what it printed. Secrets go in on stdin, never as arguments."""
    process = await asyncio.create_subprocess_exec(
        *args,
        stdin=asyncio.subprocess.PIPE if stdin is not None else asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    out, err = await process.communicate(stdin.encode() if stdin is not None else None)
    if process.returncode != 0:
        raise CommandFailed(f"{args[0]} {args[1]}: {err.decode(errors='replace').strip()[-800:]}")
    return out.decode()


def namespace(store: Store) -> str:
    return f"store-{store.slug}"  # also the Helm release and the Secret, as for the first store


# ---- the database -----------------------------------------------------------------------


async def busy_stores() -> list[Store]:
    async with AsyncSessionLocal() as db:
        return list(await db.scalars(select(Store).where(Store.status.in_(BUSY)).order_by(Store.created_at)))


async def save(store_id: uuid.UUID, **fields) -> None:
    async with AsyncSessionLocal() as db:
        await db.execute(update(Store).where(Store.id == store_id).values(**fields))
        await db.commit()


async def forget(store: Store) -> None:
    """The last step of a delete. Its members and servers go with the row (ON DELETE CASCADE)."""
    async with AsyncSessionLocal() as db:
        await db.execute(delete(Store).where(Store.id == store.id))
        await db.commit()


async def register_server(store: Store, token: str) -> None:
    """Registers the store's MCP server as woo-<subdomain>, for the admin and that store's members,
    the way an admin's own add is made: recorded in the gateway's audit log, then committed, then announced."""
    slug = f"woo-{store.slug}"
    async with AsyncSessionLocal() as db:
        existing = await db.scalar(select(UpstreamServer).where(UpstreamServer.slug == slug))
        if existing is not None:
            if existing.store_id == store.id:
                return  # registered by an earlier run
            raise RuntimeError(f"A server named {slug} already exists and is not this store's. Remove it on the Servers page, then press Retry.")
        db.add(
            UpstreamServer(
                name=store.name,
                slug=slug,
                url=f"http://woo-mcp.{namespace(store)}.svc.cluster.local:8000/mcp",
                credential_header="Authorization",
                credential_ciphertext=encrypt(f"Bearer {token}"),
                store_id=store.id,
                admin_only_tools=list(WOO_ADMIN_ONLY_TOOLS),
            )
        )
        await db.flush()
        creator = await db.get(User, store.created_by)
        await record_and_commit(db, "upstream.create", creator, None, slug, {"store": store.slug, "by": "store worker"})
    await redis.publish(UPSTREAMS_CHANGED, slug)


async def remove_servers(store: Store) -> None:
    """Takes the store's tools off the gateway before its pods go."""
    async with AsyncSessionLocal() as db:
        creator = await db.get(User, store.created_by)
        for server in list(await db.scalars(select(UpstreamServer).where(UpstreamServer.store_id == store.id))):
            await db.delete(server)
            await record_and_commit(db, "upstream.delete", creator, None, server.slug, {"store": store.slug, "by": "store worker"})
            await redis.publish(UPSTREAMS_CHANGED, server.slug)


# ---- the cluster ------------------------------------------------------------------------


def _namespace_and_grant(ns: str) -> str:
    """The store's namespace, marked so the platform's NetworkPolicies let its servers in, and
    the RoleBinding that gives this worker the installer's rights there and nowhere else."""
    return json.dumps({
        "apiVersion": "v1",
        "kind": "List",
        "items": [
            {"apiVersion": "v1", "kind": "Namespace", "metadata": {"name": ns, "labels": {"urumi.io/store": "true"}}},
            {
                "apiVersion": "rbac.authorization.k8s.io/v1",
                "kind": "RoleBinding",
                "metadata": {"name": "relay-store-worker", "namespace": ns},
                "roleRef": {"apiGroup": "rbac.authorization.k8s.io", "kind": "ClusterRole", "name": INSTALLER_ROLE},
                "subjects": [{"kind": "ServiceAccount", "name": settings.worker_service_account, "namespace": settings.platform_namespace}],
            },
        ],
    })


def _new_secrets() -> dict[str, str]:
    # The store half of scripts/make_k8s_secrets.py, without fulfilment: stores made here have none.
    return {
        "MYSQL_PASSWORD": secrets.token_urlsafe(24),
        "MYSQL_ROOT_PASSWORD": secrets.token_urlsafe(24),
        "WP_ADMIN_PASSWORD": secrets.token_urlsafe(12),
        "WOOCOMMERCE_KEY": "ck_" + secrets.token_hex(20),  # WooCommerce's own shape
        "WOOCOMMERCE_SECRET": "cs_" + secrets.token_hex(20),
        "WOO_MCP_TOKEN": secrets.token_urlsafe(48),
    }


async def _ensure_secret(ns: str) -> str:
    """Makes the store's Secret once and returns the token its MCP server checks. An existing
    Secret is never replaced: MySQL keeps the password it was first given on its volume."""
    found = await run("kubectl", "get", "secret", ns, "-n", ns, "-o", "json", "--ignore-not-found")
    if found.strip():
        return base64.b64decode(json.loads(found)["data"]["WOO_MCP_TOKEN"]).decode()
    values = _new_secrets()
    secret = {"apiVersion": "v1", "kind": "Secret", "metadata": {"name": ns, "namespace": ns}, "stringData": values}
    await run("kubectl", "create", "-f", "-", stdin=json.dumps(secret))
    return values["WOO_MCP_TOKEN"]


async def _clear_stuck_release(ns: str) -> None:
    """A worker killed in the middle of helm leaves the release "pending", and Helm then refuses
    every later change to it. Only this task works on this store, so such a state is always stale."""
    releases = json.loads(await run("helm", "list", "-n", ns, "--filter", f"^{ns}$", "-o", "json"))  # every state, in Helm 4
    state = releases[0]["status"] if releases else None
    if state == "pending-install":
        await run("helm", "uninstall", ns, "-n", ns, "--wait")
    elif state in ("pending-upgrade", "pending-rollback"):
        await run("helm", "rollback", ns, "-n", ns, "--wait")


async def provision(store: Store) -> None:
    ns = namespace(store)
    await save(store.id, status=StoreStatus.provisioning, step="preparing its namespace", error=None)
    await run("kubectl", "apply", "-f", "-", stdin=_namespace_and_grant(ns))
    await save(store.id, step="creating its secrets")
    token = await _ensure_secret(ns)
    await save(store.id, step="installing WordPress, WooCommerce and its MCP server")
    await _clear_stuck_release(ns)
    # Only what differs per store, as a values document: in --set a comma in the name would split it.
    values = {"store": {"subdomain": store.slug, "name": store.name}, "fulfilment": {"enabled": False}}
    await run(
        "helm", "upgrade", "--install", ns, CHART, "-n", ns,
        "-f", settings.store_values_file, "-f", "-",
        "--set-file", f"scripts.wpInit={WP_INIT}",
        "--wait", "--wait-for-jobs", "--timeout", "10m", "--rollback-on-failure",
        stdin=json.dumps(values),
    )
    await save(store.id, step="registering its MCP server")
    await register_server(store, token)
    await save(store.id, status=StoreStatus.ready, step=None, error=None)


async def teardown(store: Store) -> None:
    ns = namespace(store)
    await save(store.id, step="removing its MCP server")
    await remove_servers(store)
    await save(store.id, step="deleting its namespace, volumes and secrets")
    # Everything the chart made lives in the namespace, the Helm release included, and each
    # volume is deleted with its claim, so this one command is the whole teardown.
    await run("kubectl", "delete", "namespace", ns, "--wait", "--ignore-not-found", "--timeout=5m")
    await forget(store)


async def handle(store: Store) -> None:
    deleting = store.status == StoreStatus.deleting
    try:
        await (teardown(store) if deleting else provision(store))
    except Exception as exc:
        log.exception("store %s: %s failed", store.slug, "delete" if deleting else "create")
        reason = (str(exc) or type(exc).__name__)[:1000]
        await save(store.id, status=StoreStatus.failed, step=None, error=f"Delete failed: {reason}" if deleting else reason)


async def poll(running: dict[uuid.UUID, asyncio.Task]) -> None:
    """Starts a task for each store that needs work and has none yet."""
    for store in await busy_stores():
        if store.id not in running:
            running[store.id] = asyncio.create_task(handle(store))
            running[store.id].add_done_callback(lambda _, key=store.id: running.pop(key, None))


async def main() -> None:
    # shortcut: one worker replica, so a store is never picked up twice. Claim rows with
    # FOR UPDATE SKIP LOCKED before running more than one.
    running: dict[uuid.UUID, asyncio.Task] = {}
    while True:
        try:
            await poll(running)
            HEARTBEAT.touch()
        except Exception:
            log.exception("could not read the stores; trying again")
        await asyncio.sleep(POLL_SECONDS)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    asyncio.run(main())
