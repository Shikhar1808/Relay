"""Who is calling. Every /mcp request must carry a bearer token; the SDK's
bearer middleware calls Verifier.verify_token for each one and answers 401
itself when it returns None.

The gateway knows no users. It asks an Authenticator, and today there is one:
the backend's /internal/auth/resolve, which recognises API keys and chat
tokens. Nothing is cached, so a revoked key or a removed store assignment is
refused on the very next request.

OAuth for team members is not built yet. When it is, it is one more
Authenticator in the list in main.py (or a new token type in the backend's
resolve) plus AuthSettings.validate_token_resource=True; the 401 challenge and
the protected-resource metadata are already served by the SDK.
"""
import logging
from typing import Protocol

import httpx2
from mcp.server.auth.provider import AccessToken

from app.config import settings

log = logging.getLogger(__name__)


class Authenticator(Protocol):
    async def authenticate(self, token: str) -> AccessToken | None:
        """The caller this token belongs to, or None if this authenticator does not accept it."""


class BackendResolve:
    def __init__(self, http: httpx2.AsyncClient) -> None:
        self._http = http

    async def authenticate(self, token: str) -> AccessToken | None:
        try:
            response = await self._http.post(
                settings.resolve_url,
                json={"token": token},
                headers={"Authorization": f"Bearer {settings.internal_service_token}"},
            )
        except httpx2.HTTPError as exc:
            # Fail closed: if the backend cannot be asked, nobody gets in.
            log.error("cannot reach the backend to resolve a token: %s", type(exc).__name__)
            return None
        if response.status_code != 200:
            return None
        who = response.json()
        return AccessToken(
            token=token,
            client_id=who["token_type"],
            scopes=[],
            subject=who["user_id"],
            claims={"role": who["role"], "store_ids": who["store_ids"], "token_type": who["token_type"]},
        )


class Verifier:
    """The SDK's TokenVerifier: the first authenticator that accepts the token wins."""

    def __init__(self, authenticators: list[Authenticator]) -> None:
        self._authenticators = authenticators

    async def verify_token(self, token: str) -> AccessToken | None:
        for authenticator in self._authenticators:
            if (caller := await authenticator.authenticate(token)) is not None:
                return caller
        return None
