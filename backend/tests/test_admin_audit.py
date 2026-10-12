"""Recording team, key and store changes in the Platform DB (app/services/admin_audit.py)."""
import uuid
from types import SimpleNamespace

from app.db.models import AdminEvent
from app.services.admin_audit import record

ADMIN = SimpleNamespace(id=uuid.UUID("11111111-1111-1111-1111-111111111111"), email="admin@example.com")


class FakeDb:
    def __init__(self) -> None:
        self.added: list[AdminEvent] = []
        self.commits = 0

    def add(self, row: AdminEvent) -> None:
        self.added.append(row)

    async def commit(self) -> None:
        self.commits += 1


def test_an_event_says_who_did_what_to_whom_and_from_where():
    db = FakeDb()
    record(db, SimpleNamespace(client=SimpleNamespace(host="203.0.113.9")), ADMIN, "api_key.issue", "alice@example.com", prefix="umk_abcd")
    (event,) = db.added
    assert (event.actor_id, event.actor_email, event.action, event.target) == (ADMIN.id, ADMIN.email, "api_key.issue", "alice@example.com")
    assert event.detail == {"prefix": "umk_abcd"} and event.client_ip == "203.0.113.9"


def test_a_change_made_at_startup_has_no_request_and_so_no_address():
    db = FakeDb()
    record(db, None, ADMIN, "store.create", "demo", by="startup")
    assert db.added[0].client_ip is None and db.added[0].detail == {"by": "startup"}


def test_recording_never_commits_so_the_event_is_saved_with_the_change_or_not_at_all():
    db = FakeDb()
    record(db, SimpleNamespace(client=None), ADMIN, "store.create", "shop")
    assert db.commits == 0
    assert db.added[0].detail is None and db.added[0].client_ip is None
