"""The audit log lives in the gateway, which alone holds the key that signs
each row. The backend reaches it over the gateway's private port: to add an
event when an admin changes the upstream registry, and to read events for the
dashboard.

Both fail closed with 503. A registry change the gateway could not record is
not made.
"""
import asyncio
import contextlib
import http.client
import json
import urllib.request
from typing import Any

from fastapi import HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.models import User


def _call(method: str, suffix: str = "", body: dict[str, Any] | None = None) -> Any:
    request = urllib.request.Request(
        f"{settings.gateway_internal_url}/internal/audit{suffix}",
        data=None if body is None else json.dumps(body).encode(),
        method=method,
        headers={"Authorization": f"Bearer {settings.internal_service_token}", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=5) as response:
        return json.load(response)


async def _gateway(unavailable: str, *call: Any) -> Any:
    try:
        # The standard library's client, in a worker thread: two small calls do not earn an HTTP library in the image.
        return await asyncio.to_thread(_call, *call)
    except (OSError, ValueError, http.client.HTTPException) as exc:  # refused, timed out, not 2xx, or not JSON
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, unavailable) from exc


async def record_admin_event(
    action: str, admin: User, request: Request | None, upstream: str, changes: dict[str, Any], outcome: str = "ok"
) -> None:
    """Call this BEFORE committing the change it describes: it raises 503 if the event was not stored.
    If the commit then fails, call it again with outcome="failed"; the log cannot take a row back.
    `request` is None for a change the store worker makes (app/worker.py)."""
    event = {
        "action": action,
        "outcome": outcome,
        "user_id": str(admin.id),
        "upstream": upstream,
        "client_ip": request.client.host if request and request.client else None,
        "changes": changes,
    }
    await _gateway("The audit log is unavailable, so this change was not made", "POST", "", event)


async def record_and_commit(db: AsyncSession, action: str, admin: User, request: Request | None, slug: str, changes: dict) -> None:
    """Records the change in the audit log, then commits it. Raises 503 if it cannot be recorded."""
    await record_admin_event(action, admin, request, slug, changes)
    try:
        await db.commit()
    except Exception:
        # The log now says this change was made, and its rows cannot be taken back: add the correction.
        # Best effort: if that fails too, the error the admin gets is still the commit's.
        with contextlib.suppress(HTTPException):
            await record_admin_event(action, admin, request, slug, changes, outcome="failed")
        raise


async def read_events(query: str) -> list[dict[str, Any]]:
    return await _gateway("The audit log is unavailable", "GET", query)


async def verify_chain() -> dict[str, Any]:
    """Asks the gateway to walk the whole hash chain."""
    return await _gateway("The audit log is unavailable", "GET", "/verify")
