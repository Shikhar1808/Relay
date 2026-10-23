"""Prometheus's door on the backend (app/api/routes/internal.py: /internal/metrics)."""
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials

from app.api.routes import internal
from app.core.config import Settings, settings
from app.db.models import StoreStatus

pytestmark = pytest.mark.anyio

TOKEN = "m" * 40


class FakeDb:
    def __init__(self, rows):
        self.rows = rows

    async def execute(self, query):
        return SimpleNamespace(all=lambda: self.rows)


def bearer(value: str) -> HTTPAuthorizationCredentials:
    return HTTPAuthorizationCredentials(scheme="Bearer", credentials=value)


@pytest.fixture
def metrics_on(monkeypatch):
    monkeypatch.setattr(settings, "metrics_token", TOKEN)


async def test_stores_are_counted_per_status_and_a_status_with_none_reads_zero(metrics_on):
    response = await internal.metrics(bearer(TOKEN), FakeDb([(StoreStatus.ready, 2), (StoreStatus.failed, 1)]))
    lines = response.body.decode().splitlines()
    assert 'relay_stores{status="ready"} 2' in lines and 'relay_stores{status="failed"} 1' in lines
    assert 'relay_stores{status="provisioning"} 0' in lines, "a series that vanishes when empty breaks graphs"


@pytest.mark.parametrize("creds", [None, bearer("wrong"), bearer("")])
async def test_metrics_are_refused_without_the_metrics_token(metrics_on, creds):
    with pytest.raises(HTTPException) as refused:
        await internal.metrics(creds, FakeDb([]))
    assert refused.value.status_code == 401


async def test_the_service_token_does_not_open_the_metrics(metrics_on):
    # The service token can resolve tokens and write the audit log; the scrape token is another one.
    with pytest.raises(HTTPException) as refused:
        await internal.metrics(bearer(settings.internal_service_token), FakeDb([]))
    assert refused.value.status_code == 401


@pytest.mark.parametrize("creds", [None, bearer("")])
async def test_metrics_are_off_when_no_token_is_set_and_an_empty_bearer_opens_nothing(creds):
    assert settings.metrics_token == ""
    with pytest.raises(HTTPException) as off:
        await internal.metrics(creds, FakeDb([]))
    assert off.value.status_code == 404


def test_a_metrics_token_too_short_to_be_a_secret_is_refused():
    with pytest.raises(ValueError, match="METRICS_TOKEN"):
        Settings(_env_file=None, database_url="postgresql+asyncpg://u:p@h/d", jwt_secret="j" * 40,
                 gateway_encryption_key=settings.gateway_encryption_key, internal_service_token="t" * 32, metrics_token="short")
