"""The Team page's routes (app/api/routes/team.py): bringing a deactivated member back."""
import uuid
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from fastapi import BackgroundTasks, HTTPException

from app.api.routes import team
from app.core.security import hash_password, verify_password
from app.db.models import AdminEvent, Role, TokenPurpose, User

pytestmark = pytest.mark.anyio

ADMIN = SimpleNamespace(id=uuid.UUID(int=1), email="admin@example.com")
REQUEST = SimpleNamespace(client=SimpleNamespace(host="203.0.113.7"))


class FakeDb:
    def __init__(self, found: User | None) -> None:
        self.found, self.added, self.commits = found, [], 0

    async def get(self, kind, key):
        return self.found

    def add(self, row) -> None:
        self.added.append(row)

    async def commit(self) -> None:
        self.commits += 1

    async def refresh(self, row) -> None:
        pass


@pytest.fixture(autouse=True)
def invite_tokens(monkeypatch) -> list:
    issued: list = []

    async def issue_auth_token(db, user_id, purpose):
        issued.append((user_id, purpose))
        return "set-password-token"

    monkeypatch.setattr(team, "issue_auth_token", issue_auth_token)
    return issued


def a_member(*, active: bool) -> User:
    return User(
        id=uuid.uuid4(), email="ravi@example.com", name="Ravi", password_hash=hash_password("the old password"),
        role=Role.member, is_active=active, created_at=datetime.now(UTC),
    )


async def reactivate(member: User | None) -> tuple:
    db, background = FakeDb(member), BackgroundTasks()
    out = await team.reactivate_member(member.id if member else uuid.uuid4(), background, REQUEST, ADMIN, db)
    return out, db, background


async def test_a_deactivated_member_can_sign_in_again_with_a_new_temporary_password(invite_tokens):
    member = a_member(active=False)
    out, db, _ = await reactivate(member)
    assert member.is_active and out.user.is_active
    assert verify_password(out.temporary_password, member.password_hash), "the password shown is the one that works"
    assert not verify_password("the old password", member.password_hash), "the old password stops working"
    assert invite_tokens == [(member.id, TokenPurpose.invite)]
    (event,) = [row for row in db.added if isinstance(row, AdminEvent)]
    assert (event.action, event.target) == ("member.reactivate", "ravi@example.com")
    assert db.commits == 1


async def test_the_member_is_emailed_a_new_invite_link():
    member = a_member(active=False)
    _, _, background = await reactivate(member)
    (task,) = background.tasks
    assert task.func is team.send_email and task.args[0] == "ravi@example.com"
    assert "set-password#token=set-password-token" in task.args[2]


async def test_an_active_member_is_left_as_they_are():
    member = a_member(active=True)
    before = member.password_hash
    with pytest.raises(HTTPException) as refused:
        await reactivate(member)
    assert refused.value.status_code == 409 and member.password_hash == before


async def test_an_unknown_member_is_404():
    with pytest.raises(HTTPException) as refused:
        await reactivate(None)
    assert refused.value.status_code == 404
