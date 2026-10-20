"""The Stores page's routes (app/api/routes/stores.py). They only write rows: app/worker.py
does the cluster work, so a store is created `pending` and deleted by marking it `deleting`."""
import uuid
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError

from app.api.routes import stores
from app.core.config import settings
from app.db.models import AdminEvent, Store, StoreStatus
from app.schemas.stores import CreateStoreRequest, DeleteStoreRequest

pytestmark = pytest.mark.anyio

ADMIN = SimpleNamespace(id=uuid.UUID(int=1), email="admin@example.com")
REQUEST = SimpleNamespace(client=SimpleNamespace(host="203.0.113.7"))


class FakeDb:
    def __init__(self, *found: Store, count: int = 0, commit_error: Exception | None = None) -> None:
        self.found = {store.id: store for store in found}
        self.count, self.commit_error = count, commit_error
        self.added: list = []
        self.commits = self.rollbacks = 0

    async def scalar(self, query):  # the only scalar query here counts the stores
        return self.count

    async def get(self, kind, key):
        return self.found.get(key)

    def add(self, row) -> None:
        self.added.append(row)

    async def commit(self) -> None:
        if self.commit_error:
            raise self.commit_error
        self.commits += 1

    async def rollback(self) -> None:
        self.rollbacks += 1

    async def refresh(self, row) -> None:
        pass


def events(db: FakeDb) -> list[AdminEvent]:
    return [row for row in db.added if isinstance(row, AdminEvent)]


def a_store(status: StoreStatus) -> Store:
    return Store(id=uuid.uuid4(), name="Shop Two", slug="shop-two", status=status, url="http://shop-two.example", created_by=ADMIN.id)


@pytest.fixture(autouse=True)
def store_settings(monkeypatch) -> None:
    monkeypatch.setattr(settings, "store_domain", "127.0.0.1.nip.io")
    monkeypatch.setattr(settings, "store_scheme", "http")
    monkeypatch.setattr(settings, "max_stores", 3)


async def create(db: FakeDb) -> Store:
    return await stores.create_store(CreateStoreRequest(name="Shop Two", subdomain="shop-two"), REQUEST, ADMIN, db)


# ---- create ---------------------------------------------------------------------------


async def test_a_new_store_waits_for_the_worker_and_gets_its_own_address():
    db = FakeDb()
    made = await create(db)
    assert (made.slug, made.status, made.url) == ("shop-two", StoreStatus.pending, "http://shop-two.127.0.0.1.nip.io")
    (event,) = events(db)
    assert (event.action, event.target, event.detail) == ("store.create", "shop-two", {"name": "Shop Two"})
    assert db.commits == 1


async def test_a_subdomain_already_in_use_is_refused():
    db = FakeDb(commit_error=IntegrityError("INSERT", {}, Exception("duplicate key")))
    with pytest.raises(HTTPException) as refused:
        await create(db)
    assert refused.value.status_code == 409 and "subdomain" in refused.value.detail
    assert db.rollbacks == 1


async def test_no_store_is_created_past_the_cap():
    db = FakeDb(count=3)
    with pytest.raises(HTTPException) as refused:
        await create(db)
    assert refused.value.status_code == 409 and "3 stores" in refused.value.detail
    assert db.added == [] and db.commits == 0


# ---- retry ----------------------------------------------------------------------------


async def test_a_failed_store_goes_back_to_the_worker_without_its_old_error():
    failed = a_store(StoreStatus.failed)
    failed.error = "helm upgrade: context deadline exceeded"
    db = FakeDb(failed)
    await stores.retry_store(failed.id, REQUEST, ADMIN, db)
    assert (failed.status, failed.error) == (StoreStatus.pending, None)
    (event,) = events(db)
    assert (event.action, event.target) == ("store.retry", "shop-two")
    assert db.commits == 1


@pytest.mark.parametrize("status", [StoreStatus.pending, StoreStatus.provisioning, StoreStatus.ready, StoreStatus.deleting])
async def test_only_a_failed_store_can_be_retried(status):
    db = FakeDb(a_store(status))
    with pytest.raises(HTTPException) as refused:
        await stores.retry_store(next(iter(db.found)), REQUEST, ADMIN, db)
    assert refused.value.status_code == 409 and db.commits == 0


# ---- delete ---------------------------------------------------------------------------


@pytest.mark.parametrize("status", [StoreStatus.ready, StoreStatus.failed])
async def test_deleting_a_store_hands_it_to_the_worker_and_records_the_reason(status):
    store = a_store(status)
    store.error = "an old failure"
    db = FakeDb(store)
    await stores.delete_store(store.id, DeleteStoreRequest(confirm_name="Shop Two", reason="closed down"), REQUEST, ADMIN, db)
    assert (store.status, store.error) == (StoreStatus.deleting, None)
    (event,) = events(db)
    assert (event.action, event.target, event.detail) == ("store.delete", "shop-two", {"name": "Shop Two", "reason": "closed down"})
    assert db.commits == 1


async def test_a_store_is_deleted_only_when_its_name_is_typed_again():
    store = a_store(StoreStatus.ready)
    db = FakeDb(store)
    with pytest.raises(HTTPException) as refused:
        await stores.delete_store(store.id, DeleteStoreRequest(confirm_name="shop two", reason="closed down"), REQUEST, ADMIN, db)
    assert refused.value.status_code == 400 and store.status == StoreStatus.ready and db.added == []


@pytest.mark.parametrize("status", [StoreStatus.pending, StoreStatus.provisioning, StoreStatus.deleting])
async def test_a_store_the_worker_is_busy_with_cannot_be_deleted(status):
    # The worker would otherwise finish the install and mark it ready over the delete.
    store = a_store(status)
    db = FakeDb(store)
    with pytest.raises(HTTPException) as refused:
        await stores.delete_store(store.id, DeleteStoreRequest(confirm_name="Shop Two", reason="closed down"), REQUEST, ADMIN, db)
    assert refused.value.status_code == 409 and store.status == status and db.commits == 0


@pytest.mark.parametrize("route", ["retry", "delete"])
async def test_an_unknown_store_is_404(route):
    db = FakeDb()
    with pytest.raises(HTTPException) as refused:
        if route == "retry":
            await stores.retry_store(uuid.uuid4(), REQUEST, ADMIN, db)
        else:
            await stores.delete_store(uuid.uuid4(), DeleteStoreRequest(confirm_name="x", reason="y"), REQUEST, ADMIN, db)
    assert refused.value.status_code == 404
