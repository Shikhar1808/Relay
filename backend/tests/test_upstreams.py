"""The Servers page's routes (app/api/routes/upstreams.py): the tools kept for admins."""
import uuid
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from app.api.routes import upstreams
from app.db.models import UpstreamServer
from app.schemas.upstreams import CreateUpstreamRequest, UpdateUpstreamRequest

pytestmark = pytest.mark.anyio

ADMIN = SimpleNamespace(id=uuid.UUID(int=1), email="admin@example.com")
REQUEST = SimpleNamespace(client=SimpleNamespace(host="203.0.113.7"))
LOCKED = ["create_product", "update_product", "update_order_status"]


class FakeDb:
    def __init__(self, found: UpstreamServer | None = None) -> None:
        self.found, self.added, self.commits = found, [], 0

    async def get(self, kind, key):
        return self.found

    def add(self, row) -> None:
        self.added.append(row)

    async def flush(self) -> None:
        pass

    async def refresh(self, row) -> None:
        row.id = row.id or uuid.uuid4()
        row.created_at = datetime.now(UTC)


@pytest.fixture(autouse=True)
def recorded(monkeypatch) -> list:
    """The gateway's audit log is not reachable here: remember what would be written."""
    log: list = []

    async def record_and_commit(db, action, admin, request, slug, changes):
        log.append((action, slug, changes))

    monkeypatch.setattr(upstreams, "record_and_commit", record_and_commit)
    return log


REDIS = SimpleNamespace(publish=lambda channel, slug: _done())


async def _done() -> None:
    pass


def a_server(admin_only_tools: list[str]) -> UpstreamServer:
    return UpstreamServer(
        id=uuid.uuid4(), name="Demo", slug="woo-demo", url="http://woo-mcp:8000/mcp", enabled=True,
        credential_header="Authorization", credential_ciphertext=None, store_id=None,
        created_at=datetime.now(UTC), admin_only_tools=admin_only_tools,
    )


async def update(server: UpstreamServer, **sent) -> UpstreamServer:
    out = await upstreams.update_upstream(server.id, UpdateUpstreamRequest(**sent), REQUEST, ADMIN, FakeDb(server), REDIS)
    assert out.admin_only_tools == server.admin_only_tools, "the page is told what is now locked"
    return server


async def test_an_admin_replaces_the_list_of_tools_kept_for_admins(recorded):
    server = await update(a_server(LOCKED), admin_only_tools=["create_product"])
    assert server.admin_only_tools == ["create_product"]
    # Recorded in the gateway's audit log with the rest of what was sent.
    assert recorded == [("upstream.update", "woo-demo", {"admin_only_tools": ["create_product"]})]


async def test_an_empty_list_unlocks_every_tool():
    assert (await update(a_server(LOCKED), admin_only_tools=[])).admin_only_tools == []


async def test_leaving_the_list_out_changes_nothing():
    assert (await update(a_server(LOCKED), enabled=False)).admin_only_tools == LOCKED


