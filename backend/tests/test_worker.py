"""The store worker (app/worker.py): the kubectl and helm steps that create and delete a
store, in order, with the cluster and the database replaced by fakes."""
import asyncio
import base64
import json
import uuid
from types import SimpleNamespace

import pytest

from app import worker
from app.core.config import settings
from app.core.crypto import decrypt
from app.db.models import Store, StoreStatus, UpstreamServer

pytestmark = pytest.mark.anyio

ADMIN = SimpleNamespace(id=uuid.UUID(int=1), email="admin@example.com")
NS = "store-shop-two"


def a_store(status: StoreStatus = StoreStatus.pending) -> Store:
    return Store(id=uuid.uuid4(), name="Shop Two", slug="shop-two", status=status, url="http://shop-two.example", created_by=ADMIN.id)


class FakeCluster:
    """Stands in for worker.run. `answers` maps "kubectl get" or "helm list" to what the command
    prints, or to an exception it raises; anything else prints nothing."""

    def __init__(self, log: list) -> None:
        self.log = log
        self.calls: list[tuple[tuple[str, ...], str | None]] = []
        self.answers: dict[str, object] = {"helm list": "[]"}

    async def run(self, *args: str, stdin: str | None = None) -> str:
        self.calls.append((args, stdin))
        self.log.append(" ".join(args[:2]))
        answer = self.answers.get(" ".join(args[:2]), "")
        if isinstance(answer, Exception):
            raise answer
        return answer

    def call(self, command: str) -> tuple[tuple[str, ...], str | None]:
        (found,) = [call for call in self.calls if " ".join(call[0][:2]) == command]
        return found


@pytest.fixture
def log() -> list:
    """Everything that happened, in order: commands, saved fields, database steps."""
    return []


@pytest.fixture
def cluster(monkeypatch, log) -> FakeCluster:
    fake = FakeCluster(log)
    monkeypatch.setattr(worker, "run", fake.run)

    async def save(store_id, **fields):
        log.append(("save", fields))

    async def register_server(store, token):
        log.append(("register", token))

    async def remove_servers(store):
        log.append("remove servers")

    async def forget(store):
        log.append("forget")

    for name, fake_step in [("save", save), ("register_server", register_server), ("remove_servers", remove_servers), ("forget", forget)]:
        monkeypatch.setattr(worker, name, fake_step)
    return fake


def commands(log: list) -> list[str]:
    return [entry for entry in log if isinstance(entry, str)]


def saved(log: list) -> list[dict]:
    return [entry[1] for entry in log if isinstance(entry, tuple) and entry[0] == "save"]


# ---- create ---------------------------------------------------------------------------


async def test_a_new_store_is_installed_in_its_own_namespace_then_registered_then_ready(cluster, log):
    await worker.handle(a_store())
    assert commands(log) == ["kubectl apply", "kubectl get", "kubectl create", "helm list", "helm upgrade"]
    assert saved(log)[0] == {"status": StoreStatus.provisioning, "step": "preparing its namespace", "error": None}
    assert saved(log)[-1] == {"status": StoreStatus.ready, "step": None, "error": None}
    assert [entry[0] for entry in log if isinstance(entry, tuple)][-2:] == ["register", "save"]


async def test_the_namespace_is_marked_as_a_store_and_the_worker_is_granted_rights_only_there(cluster, log):
    await worker.handle(a_store())
    args, stdin = cluster.call("kubectl apply")
    namespace, grant = json.loads(stdin)["items"]
    assert namespace["metadata"] == {"name": NS, "labels": {"urumi.io/store": "true"}}
    assert grant["kind"] == "RoleBinding" and grant["metadata"]["namespace"] == NS
    assert grant["roleRef"] == {"apiGroup": "rbac.authorization.k8s.io", "kind": "ClusterRole", "name": "relay-store-installer"}
    assert grant["subjects"] == [{"kind": "ServiceAccount", "name": settings.worker_service_account, "namespace": settings.platform_namespace}]


async def test_the_chart_is_installed_safely_repeatable_with_the_stores_values_on_stdin(cluster, log):
    await worker.handle(a_store())
    args, stdin = cluster.call("helm upgrade")
    assert args[:6] == ("helm", "upgrade", "--install", NS, worker.CHART, "-n") and args[6] == NS
    assert ["-f", settings.store_values_file, "-f", "-"] == list(args[7:11])
    for flag in ("--rollback-on-failure", "--wait", "--wait-for-jobs", f"scripts.wpInit={worker.WP_INIT}"):
        assert flag in args
    # The name goes in a values document, not --set, where a comma in it would split it.
    assert json.loads(stdin) == {"store": {"subdomain": "shop-two", "name": "Shop Two"}, "fulfilment": {"enabled": False}}


async def test_a_new_stores_secrets_go_to_the_cluster_on_stdin_and_never_on_a_command_line(cluster, log):
    await worker.handle(a_store())
    args, stdin = cluster.call("kubectl create")
    secret = json.loads(stdin)
    assert secret["metadata"] == {"name": NS, "namespace": NS}
    values = secret["stringData"]
    assert set(values) == {"MYSQL_PASSWORD", "MYSQL_ROOT_PASSWORD", "WP_ADMIN_PASSWORD", "WOOCOMMERCE_KEY", "WOOCOMMERCE_SECRET", "WOO_MCP_TOKEN"}
    assert values["WOOCOMMERCE_KEY"].startswith("ck_") and len(values["WOOCOMMERCE_KEY"]) == 43
    assert ("register", values["WOO_MCP_TOKEN"]) in log
    every_argument = " ".join(" ".join(call[0]) for call in cluster.calls)
    assert not any(value in every_argument for value in values.values())


async def test_an_existing_secret_is_kept_so_the_volume_keeps_its_password(cluster, log):
    token = "kept-token-" + "x" * 40
    cluster.answers["kubectl get"] = json.dumps({"data": {"WOO_MCP_TOKEN": base64.b64encode(token.encode()).decode()}})
    await worker.handle(a_store())
    assert "kubectl create" not in commands(log)
    assert ("register", token) in log


@pytest.mark.parametrize(("left", "fix"), [("pending-install", "helm uninstall"), ("pending-upgrade", "helm rollback"), ("pending-rollback", "helm rollback")])
async def test_a_release_left_half_done_by_a_killed_worker_is_cleared_before_installing(cluster, log, left, fix):
    cluster.answers["helm list"] = json.dumps([{"name": NS, "status": left}])
    await worker.handle(a_store())
    assert commands(log)[-2:] == [fix, "helm upgrade"]


async def test_the_release_is_looked_up_with_flags_helm_4_has(cluster, log):
    # Helm 4 lists releases in every state by default and no longer knows --all.
    await worker.handle(a_store())
    args, _ = cluster.call("helm list")
    assert args == ("helm", "list", "-n", NS, "--filter", f"^{NS}$", "-o", "json")


async def test_a_failed_step_marks_the_store_failed_with_the_reason_and_registers_nothing(cluster, log):
    cluster.answers["helm upgrade"] = worker.CommandFailed("helm upgrade: Error: context deadline exceeded")
    await worker.handle(a_store())
    assert saved(log)[-1] == {"status": StoreStatus.failed, "step": None, "error": "helm upgrade: Error: context deadline exceeded"}
    assert not any(entry[0] == "register" for entry in log if isinstance(entry, tuple))


# ---- delete ---------------------------------------------------------------------------


async def test_deleting_takes_the_tools_away_first_then_the_namespace_then_the_row(cluster, log):
    await worker.handle(a_store(StoreStatus.deleting))
    assert commands(log) == ["remove servers", "kubectl delete", "forget"]
    args, _ = cluster.call("kubectl delete")
    # The namespace holds everything the chart made: volumes, Secrets, the release itself.
    assert args[2:4] == ("namespace", NS) and "--wait" in args and "--ignore-not-found" in args


async def test_a_failed_delete_leaves_a_failed_store_that_can_be_deleted_again(cluster, log):
    cluster.answers["kubectl delete"] = worker.CommandFailed("kubectl delete: timed out waiting for the condition")
    await worker.handle(a_store(StoreStatus.deleting))
    assert saved(log)[-1]["status"] == StoreStatus.failed
    assert saved(log)[-1]["error"] == "Delete failed: kubectl delete: timed out waiting for the condition"
    assert "forget" not in commands(log)


# ---- the loop ---------------------------------------------------------------------------


async def test_each_busy_store_gets_one_task_at_a_time_and_several_run_at_once(monkeypatch):
    first, second = a_store(), a_store(StoreStatus.deleting)
    release = asyncio.Event()
    started: list = []

    async def busy_stores():
        return [first, second]

    async def handle(store):
        started.append(store.id)
        await release.wait()

    monkeypatch.setattr(worker, "busy_stores", busy_stores)
    monkeypatch.setattr(worker, "handle", handle)
    running: dict = {}
    await worker.poll(running)
    await worker.poll(running)  # both still running: neither starts again
    await asyncio.sleep(0)
    assert started == [first.id, second.id]
    release.set()
    await asyncio.gather(*running.values())
    await asyncio.sleep(0)
    assert running == {}


# ---- the registry -----------------------------------------------------------------------


class FakeDb:
    def __init__(self, log: list, existing=None, servers=()) -> None:
        self.log, self.existing, self.servers = log, existing, list(servers)
        self.added: list = []

    async def scalar(self, query):
        return self.existing

    async def scalars(self, query):
        return self.servers

    async def get(self, kind, key):
        return ADMIN

    def add(self, row) -> None:
        self.added.append(row)

    async def delete(self, row) -> None:
        self.log.append(("delete", row.slug))

    async def flush(self) -> None:
        pass

    async def commit(self) -> None:
        self.log.append("commit")

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc) -> None:
        pass


@pytest.fixture
def registry(monkeypatch, log):
    async def record_and_commit(db, action, admin, request, slug, changes):
        log.append(("event", action, slug, request))
        await db.commit()

    async def publish(channel, slug):
        log.append(("publish", channel, slug))

    monkeypatch.setattr(worker, "record_and_commit", record_and_commit)
    monkeypatch.setattr(worker, "redis", SimpleNamespace(publish=publish))


async def test_a_ready_store_gets_its_server_registered_for_its_members_only(monkeypatch, log, registry):
    db = FakeDb(log)
    monkeypatch.setattr(worker, "AsyncSessionLocal", lambda: db)
    store = a_store(StoreStatus.provisioning)
    await worker.register_server(store, "t" * 48)
    (server,) = [row for row in db.added if isinstance(row, UpstreamServer)]
    assert (server.slug, server.url, server.store_id) == ("woo-shop-two", "http://woo-mcp.store-shop-two.svc.cluster.local:8000/mcp", store.id)
    assert decrypt(server.credential_ciphertext) == "Bearer " + "t" * 48
    # Recorded in the gateway's audit log before it is committed, like an admin's own change.
    assert log == [("event", "upstream.create", "woo-shop-two", None), "commit", ("publish", "upstreams:changed", "woo-shop-two")]


async def test_a_server_that_is_already_registered_is_left_as_it_is(monkeypatch, log, registry):
    db = FakeDb(log, existing=uuid.uuid4())
    monkeypatch.setattr(worker, "AsyncSessionLocal", lambda: db)
    await worker.register_server(a_store(StoreStatus.provisioning), "t" * 48)
    assert db.added == [] and log == []


async def test_removing_a_stores_servers_records_and_announces_each(monkeypatch, log, registry):
    servers = [SimpleNamespace(slug="woo-shop-two"), SimpleNamespace(slug="fulfilment-shop-two")]
    monkeypatch.setattr(worker, "AsyncSessionLocal", lambda: FakeDb(log, servers=servers))
    await worker.remove_servers(a_store(StoreStatus.deleting))
    assert log == [
        ("delete", "woo-shop-two"), ("event", "upstream.delete", "woo-shop-two", None), "commit", ("publish", "upstreams:changed", "woo-shop-two"),
        ("delete", "fulfilment-shop-two"), ("event", "upstream.delete", "fulfilment-shop-two", None), "commit", ("publish", "upstreams:changed", "fulfilment-shop-two"),
    ]
