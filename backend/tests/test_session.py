"""Where the dashboard JWT comes from, and when a cookie is not enough
(app/core/deps.py: session_token; app/api/routes/auth.py: the cookie itself)."""
import uuid
from types import SimpleNamespace

import pytest
from fastapi import HTTPException, Response
from fastapi.security import HTTPAuthorizationCredentials
from starlette.requests import Request

from app.api.routes import auth
from app.core.config import settings
from app.core.deps import SESSION_COOKIE, get_current_user, get_optional_user, revoked_session_key, session_token
from app.core.security import create_access_token, decode_access_token
from app.db.models import Role

OURS = "http://localhost:3000"  # the default APP_BASE_URL
HEADER = HTTPAuthorizationCredentials(scheme="Bearer", credentials="from-the-header")


def request(method: str = "GET", cookie: str | None = None, origin: str | None = None) -> Request:
    headers = []
    if cookie:
        headers.append((b"cookie", f"{SESSION_COOKIE}={cookie}".encode()))
    if origin:
        headers.append((b"origin", origin.encode()))
    return Request({"type": "http", "method": method, "path": "/", "query_string": b"", "headers": headers})


def refused(method: str, origin: str | None, cookie: str | None = "from-the-cookie") -> int:
    with pytest.raises(HTTPException) as error:
        session_token(request(method, cookie, origin), None)
    return error.value.status_code


def test_with_neither_header_nor_cookie_nobody_is_signed_in():
    assert refused("GET", None, cookie=None) == 401


def test_the_header_is_used_as_it_is_whatever_the_origin():
    assert session_token(request("DELETE", "from-the-cookie", "http://evil.example"), HEADER) == "from-the-header"


def test_the_cookie_signs_in_reads_from_anywhere_and_changes_from_our_own_pages():
    assert session_token(request("GET", "from-the-cookie", "http://evil.example"), None) == "from-the-cookie"
    for method in ("POST", "PATCH", "DELETE"):
        assert session_token(request(method, "from-the-cookie", OURS), None) == "from-the-cookie"


@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE"])
@pytest.mark.parametrize("origin", ["http://evil.example", "http://localhost:4000", "https://localhost:3000", "null"])
def test_a_change_sent_with_the_cookie_from_any_other_page_is_refused(method, origin):
    assert refused(method, origin) == 403


def test_a_caller_that_sends_no_origin_is_not_a_browser_and_is_let_through():
    assert session_token(request("POST", "from-the-cookie"), None) == "from-the-cookie"


USER = SimpleNamespace(
    id=uuid.uuid4(), email="a@example.com", name="A", role=Role.member, is_active=True, created_at="2026-10-11T00:00:00Z"
)


def test_signing_in_sets_a_cookie_that_scripts_cannot_read_and_other_sites_cannot_send():
    response = Response()
    body = auth._start_session(response, USER)
    cookie = response.headers["set-cookie"]
    assert cookie.startswith(f"{SESSION_COOKIE}={body.access_token};"), "the cookie and the body carry the same token"
    assert decode_access_token(body.access_token)["sub"] == str(USER.id)
    flags = cookie.lower()
    assert "httponly" in flags and "samesite=strict" in flags and f"max-age={settings.jwt_expire_minutes * 60}" in flags
    assert "secure" not in flags, "a Secure cookie would never be sent back over plain http in local dev"


def test_over_https_the_cookie_is_never_sent_in_the_clear(monkeypatch):
    monkeypatch.setattr(settings, "app_base_url", "https://app.example.com")
    response = Response()
    auth._start_session(response, USER)
    assert "; secure" in response.headers["set-cookie"].lower()


# ---- who is signed in, and signing out ---------------------------------------------------


class FakeRedis:
    def __init__(self, revoked: set[str] | None = None) -> None:
        self.revoked = revoked or set()

    async def exists(self, key: str) -> int:
        return int(key in self.revoked)


class FakeDb:
    def __init__(self, user) -> None:
        self.user = user

    async def get(self, model, key):
        return self.user if key == self.user.id else None


def bearer(token: str) -> HTTPAuthorizationCredentials:
    return HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)


@pytest.mark.anyio
async def test_a_valid_token_signs_its_user_in_and_is_kept_for_sign_out():
    token, seen = create_access_token(USER.id, "member"), request()
    assert await get_current_user(seen, bearer(token), FakeDb(USER), FakeRedis()) is USER
    assert seen.state.session == (token, decode_access_token(token)["exp"])


@pytest.mark.anyio
async def test_a_signed_out_token_is_refused_although_it_has_not_expired():
    token = create_access_token(USER.id, "member")
    with pytest.raises(HTTPException) as error:
        await get_current_user(request(), bearer(token), FakeDb(USER), FakeRedis({revoked_session_key(token)}))
    assert error.value.status_code == 401
    other = create_access_token(uuid.uuid4(), "member")
    assert revoked_session_key(token) != revoked_session_key(other), "signing one session out must not sign out another"


@pytest.mark.anyio
async def test_a_deactivated_user_is_refused_even_with_a_good_token():
    gone = SimpleNamespace(**{**vars(USER), "is_active": False})
    with pytest.raises(HTTPException) as error:
        await get_current_user(request(), bearer(create_access_token(gone.id, "member")), FakeDb(gone), FakeRedis())
    assert error.value.status_code == 401


@pytest.mark.anyio
async def test_asking_who_is_signed_in_never_fails_when_nobody_is():
    token = create_access_token(USER.id, "member")
    db = FakeDb(USER)
    assert await get_optional_user(request(), bearer(token), db, FakeRedis()) is USER
    assert await get_optional_user(request(), None, db, FakeRedis()) is None  # no token
    assert await get_optional_user(request(), bearer("not-a-token"), db, FakeRedis()) is None
    assert await get_optional_user(request(), bearer(token), db, FakeRedis({revoked_session_key(token)})) is None


@pytest.mark.anyio
async def test_asking_who_is_signed_in_still_refuses_a_change_from_another_site():
    token = create_access_token(USER.id, "member")
    with pytest.raises(HTTPException) as error:
        await get_optional_user(request("POST", token, "http://evil.example"), None, FakeDb(USER), FakeRedis())
    assert error.value.status_code == 403
