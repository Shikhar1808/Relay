"""The first store and its MCP server, registered on startup like any later store
(app/services/bootstrap.py), and the one rule for a store's subdomain (app/services/stores.py).
"""
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy.exc import IntegrityError

from app.api.routes import auth
from app.core.config import settings
from app.core.crypto import decrypt
from app.db.models import AdminEvent, Role, Store, StoreStatus, UpstreamServer
from app.schemas.auth import UserOut
from app.services import bootstrap
from app.services.stores import check_subdomain

pytestmark = pytest.mark.anyio

ADMIN = SimpleNamespace(id=uuid.UUID("11111111-1111-1111-1111-111111111111"), email="admin@example.com")
TOKEN = "t" * 48
REQUEST = SimpleNamespace(client=SimpleNamespace(host="203.0.113.7"))  # the admin's signup, as record() reads it


class FakeDb:
    """A session whose lookups answer from `found`, in order, and which keeps what was added."""

    def __init__(self, *found, commit_error: Exception | None = None) -> None:
        self.found = list(found)
        self.commit_error = commit_error
        self.added: list = []
        self.queries = self.commits = self.rollbacks = 0

    async def execute(self, query):
        self.queries += 1
        result = self.found.pop(0)
        return SimpleNamespace(scalar_one_or_none=lambda: result)

    def add(self, row) -> None:
        self.added.append(row)

    async def commit(self) -> None:
        if self.commit_error:
            raise self.commit_error
        self.commits += 1

    async def rollback(self) -> None:
        self.rollbacks += 1

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc) -> None:
        pass


@pytest.fixture(autouse=True)
def woo(monkeypatch) -> None:
    monkeypatch.setattr(settings, "woo_mcp_url", "http://woo-mcp:8000/mcp")
    monkeypatch.setattr(settings, "woo_mcp_token", TOKEN)
    monkeypatch.setattr(settings, "first_store_subdomain", "demo")
    monkeypatch.setattr(settings, "first_store_name", "Demo Store")
    monkeypatch.setattr(settings, "first_store_url", "http://localhost:8081")


def added(db: FakeDb, kind: type) -> list:
    return [row for row in db.added if isinstance(row, kind)]


async def test_the_first_store_is_a_store_row_with_an_upstream_that_belongs_to_it():
    db = FakeDb(None)  # no store has the subdomain yet
    store = await bootstrap.create_first_store_if_absent(db, ADMIN)
    assert added(db, Store) == [store]
    assert (store.slug, store.name, store.status, store.url, store.created_by) == (
        "demo", "Demo Store", StoreStatus.ready, "http://localhost:8081", ADMIN.id,
    )
    (upstream,) = added(db, UpstreamServer)
    # Same shape a store made from the dashboard will get: woo-<subdomain>, limited to that store's members.
    assert (upstream.slug, upstream.url, upstream.credential_header) == ("woo-demo", "http://woo-mcp:8000/mcp", "Authorization")
    assert upstream.store_id == store.id and store.id is not None
    assert decrypt(upstream.credential_ciphertext) == f"Bearer {TOKEN}"
    assert upstream.admin_only_tools == ["create_product", "update_product", "update_order_status"], "members cannot write products or orders"
    assert db.commits == 1


async def test_creating_the_first_store_is_recorded_like_any_other_store_change():
    db = FakeDb(None)
    await bootstrap.create_first_store_if_absent(db, ADMIN)
    (event,) = added(db, AdminEvent)
    assert (event.action, event.target, event.actor_id, event.actor_email) == ("store.create", "demo", ADMIN.id, ADMIN.email)
    assert event.client_ip is None and event.detail == {"name": "Demo Store", "by": "startup"}


async def test_a_store_registered_by_the_admins_signup_says_so_and_keeps_where_it_came_from():
    db = FakeDb(None)
    await bootstrap.create_first_store_if_absent(db, ADMIN, REQUEST)
    (event,) = added(db, AdminEvent)
    assert event.client_ip == "203.0.113.7" and event.detail == {"name": "Demo Store", "by": "signup"}


async def test_a_store_that_is_already_there_is_left_alone():
    db = FakeDb(uuid.uuid4())
    assert await bootstrap.create_first_store_if_absent(db, ADMIN) is None
    assert db.added == [] and db.commits == 0


@pytest.mark.parametrize("unset", ["woo_mcp_url", "woo_mcp_token"])
async def test_nothing_is_registered_without_the_servers_address_and_token(unset, monkeypatch):
    monkeypatch.setattr(settings, unset, None)
    db = FakeDb()
    assert await bootstrap.create_first_store_if_absent(db, ADMIN) is None
    assert db.added == [] and db.queries == 0


async def test_a_subdomain_that_breaks_the_rule_stops_the_start(monkeypatch):
    monkeypatch.setattr(settings, "first_store_subdomain", "Demo_Store")
    db = FakeDb(None)
    with pytest.raises(ValueError, match="subdomain"):
        await bootstrap.create_first_store_if_absent(db, ADMIN)
    assert db.added == []


async def test_losing_the_race_to_another_replica_is_not_an_error():
    db = FakeDb(None, commit_error=IntegrityError("INSERT", {}, Exception("duplicate key")))
    assert await bootstrap.create_first_store_if_absent(db, ADMIN) is None
    assert db.rollbacks == 1


async def test_at_startup_the_store_is_registered_once_an_admin_exists(monkeypatch):
    db = FakeDb(ADMIN, None)  # the admin is found, the store is not
    monkeypatch.setattr(bootstrap, "AsyncSessionLocal", lambda: db)
    await bootstrap.bootstrap_first_store()
    assert [store.slug for store in added(db, Store)] == ["demo"]


async def test_a_bad_subdomain_stops_the_start_even_before_an_admin_exists(monkeypatch):
    # Otherwise the backend would start fine and then fail the admin's signup, after creating the admin.
    monkeypatch.setattr(settings, "first_store_subdomain", "api")
    db = FakeDb(None)
    monkeypatch.setattr(bootstrap, "AsyncSessionLocal", lambda: db)
    with pytest.raises(ValueError, match="reserved"):
        await bootstrap.bootstrap_first_store()


async def test_at_startup_nothing_is_registered_until_an_admin_exists(monkeypatch):
    db = FakeDb(None)  # no admin yet: POST /auth/admin/signup will register the store instead
    monkeypatch.setattr(bootstrap, "AsyncSessionLocal", lambda: db)
    await bootstrap.bootstrap_first_store()
    assert db.added == []


# ---- POST /auth/admin/signup, which registers the store when startup had no admin to do it with ----

ROW = {
    "id": ADMIN.id, "email": "admin@example.com", "name": "Admin", "role": Role.admin, "is_active": True,
    "created_at": datetime(2026, 10, 12, tzinfo=timezone.utc),
}


async def signup(monkeypatch, store_step):
    async def created(db, email, name, password):
        return SimpleNamespace(**ROW)

    monkeypatch.setattr(auth, "create_admin_if_absent", created)
    monkeypatch.setattr(auth, "create_first_store_if_absent", store_step)
    body = SimpleNamespace(email=ROW["email"], name="Admin", password="a-good-password")
    return await auth.admin_signup(body, REQUEST, FakeDb())


async def test_the_signup_passes_on_who_asked_so_the_store_is_recorded_as_its_doing(monkeypatch):
    asked = []

    async def store_step(db, admin, request=None):
        asked.append(request)

    await signup(monkeypatch, store_step)
    assert asked == [REQUEST]


async def test_the_signup_still_answers_when_the_store_was_already_registered_by_someone_else(monkeypatch):
    async def store_step(db, admin, request=None):
        # Losing that race rolls back, and a rollback leaves every row unread: reading it again
        # needs a query, which an async session cannot make while the answer is being written.
        vars(admin).clear()

    answer = await signup(monkeypatch, store_step)
    assert UserOut.model_validate(answer).email == ROW["email"]


# ---- the subdomain rule -----------------------------------------------------------------

@pytest.mark.parametrize("name", ["", "Demo", "my_shop", "-shop", "shop-", "my--shop", "shop.one", "café", "x" * 31])
def test_a_subdomain_must_be_a_short_dns_label(name):
    with pytest.raises(ValueError, match="subdomain"):
        check_subdomain(name)


@pytest.mark.parametrize("name", ["app", "api", "mcp", "www", "dev"])
def test_the_platforms_own_host_names_cannot_be_a_stores(name):
    with pytest.raises(ValueError, match="reserved"):
        check_subdomain(name)
