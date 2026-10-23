"""Turning a bearer token into a caller by asking the backend (app/auth.py)."""
import json

import httpx2
import pytest

from app.auth import BackendResolve, Verifier
from app.config import settings
from tests.conftest import make_caller

pytestmark = pytest.mark.anyio

WHO = {"user_id": "11111111-1111-1111-1111-111111111111", "role": "member", "token_type": "api_key", "store_ids": ["A", "B"]}


def resolver(handler) -> tuple[BackendResolve, list[httpx2.Request]]:
    seen: list[httpx2.Request] = []

    def record(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        return handler(request)

    return BackendResolve(httpx2.AsyncClient(transport=httpx2.MockTransport(record))), seen


async def test_a_known_token_becomes_a_caller_with_their_stores():
    backend, _ = resolver(lambda request: httpx2.Response(200, json=WHO))
    caller = await backend.authenticate("umk_good")
    assert caller.subject == WHO["user_id"] and caller.client_id == "api_key"
    assert caller.claims == {"role": "member", "store_ids": ["A", "B"], "token_type": "api_key"}


async def test_the_backend_is_asked_with_the_service_token_and_the_callers_token_in_the_body():
    backend, seen = resolver(lambda request: httpx2.Response(200, json=WHO))
    await backend.authenticate("umk_good")
    (request,) = seen
    assert str(request.url) == settings.resolve_url and request.method == "POST"
    assert request.headers["authorization"] == f"Bearer {settings.internal_service_token}"
    assert json.loads(request.content) == {"token": "umk_good"}
    assert "umk_good" not in str(request.url), "the caller's token must never travel in a URL"


@pytest.mark.parametrize("status", [401, 403, 404, 429, 500, 503])
async def test_anything_but_200_means_no_caller(status):
    backend, _ = resolver(lambda request: httpx2.Response(status, json={"detail": "no"}))
    assert await backend.authenticate("umk_revoked") is None


async def test_an_unreachable_backend_lets_nobody_in():
    def refuse(request):
        raise httpx2.ConnectError("connection refused")

    backend, _ = resolver(refuse)
    assert await backend.authenticate("umk_good") is None


async def test_a_backend_timeout_lets_nobody_in():
    def hang(request):
        raise httpx2.ReadTimeout("timed out")

    backend, _ = resolver(hang)
    assert await backend.authenticate("umk_good") is None


class Fixed:
    def __init__(self, answer):
        self.answer, self.asked = answer, []

    async def authenticate(self, token):
        self.asked.append(token)
        return self.answer


async def test_verifier_uses_the_first_authenticator_that_accepts():
    first, second = Fixed(None), Fixed(make_caller(["A"], subject="from-second"))
    caller = await Verifier([first, second]).verify_token("tok")
    assert caller.subject == "from-second" and first.asked == ["tok"] and second.asked == ["tok"]


async def test_verifier_refuses_when_nobody_accepts_and_when_there_is_nobody_to_ask():
    assert await Verifier([Fixed(None), Fixed(None)]).verify_token("tok") is None
    assert await Verifier([]).verify_token("tok") is None
