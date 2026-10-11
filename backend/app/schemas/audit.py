from datetime import datetime
from typing import Any

from pydantic import BaseModel


class AuditEventOut(BaseModel):
    """One audit row, from the gateway's log or from the Platform DB's
    admin_events (category "platform"). `seq` orders the rows of one source
    and pages them (`before=`)."""

    seq: int
    ts: datetime
    severity: str  # INFO, NOTICE, WARNING, ERROR
    category: str  # tool_call, auth, admin, registry, upstream_health, system; or platform
    action: str
    outcome: str
    user_id: str | None
    user_email: str | None  # for gateway rows added here; the gateway knows users only by id
    upstream: str | None = None
    tool: str | None = None
    duration_ms: int | None = None
    target: str | None = None  # platform rows: the member or store the change was made to
    client_ip: str | None
    detail: dict[str, Any] | None


class ChainCheckOut(BaseModel):
    """Whether the gateway's audit log is untouched. `broken_seq` is the first row that does not fit."""

    intact: bool
    verified: int  # rows that were fine
    last_hash: str
    broken_seq: int | None
