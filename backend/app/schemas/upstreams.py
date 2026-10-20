import uuid
from datetime import datetime
from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, Field, HttpUrl, StringConstraints

_Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=255)]
# No underscores at all: the gateway names tools `slug__tool` and splits on the
# first `__`, so a slug must never contain one.
_Slug = Annotated[str, StringConstraints(pattern=r"^[a-z0-9]+(-[a-z0-9]+)*$", max_length=50)]
_HeaderName = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9-]+$", max_length=100)]
# Printable ASCII only: a CR or LF here would let an admin inject extra headers
# into every request the gateway sends to this server.
_Credential = Annotated[str, StringConstraints(pattern=r"^[\x20-\x7e]*$", max_length=4000)]


_ToolName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]


def _no_login_in_url(url: HttpUrl) -> HttpUrl:
    # http://user:password@host is a valid URL, but the url column is stored in
    # plain text and returned by the API: a secret there would skip encryption.
    if url.username or url.password:
        raise ValueError("put the secret in the credential field, not in the URL")
    return url


_Url = Annotated[HttpUrl, AfterValidator(_no_login_in_url)]


class CreateUpstreamRequest(BaseModel):
    name: _Name
    slug: _Slug
    url: _Url
    enabled: bool = True
    credential_header: _HeaderName = "Authorization"
    # The full header value, e.g. "Bearer abc123". Omit for a server with no credential.
    credential: _Credential | None = None
    store_id: uuid.UUID | None = None
    # The server's own tool names (without the slug) that members cannot see or call.
    admin_only_tools: list[_ToolName] = Field(default=[], max_length=200)


class UpdateUpstreamRequest(BaseModel):
    """Every field is optional; only the ones sent are changed. The slug is
    fixed after creation because it is part of every tool name clients see.
    """

    name: _Name | None = None
    url: _Url | None = None
    enabled: bool | None = None
    credential_header: _HeaderName | None = None
    # Omitted: keep the stored credential. "": remove it. Anything else: replace it.
    credential: _Credential | None = None
    store_id: uuid.UUID | None = None
    # Omitted or null: keep the list. A list replaces it; [] lets members use every tool.
    admin_only_tools: list[_ToolName] | None = Field(default=None, max_length=200)


class UpstreamOut(BaseModel):
    id: uuid.UUID
    name: str
    slug: str
    url: str
    enabled: bool
    credential_header: str
    has_credential: bool  # the credential itself is never returned
    store_id: uuid.UUID | None
    admin_only_tools: list[str]
    created_at: datetime
    # From the gateway's last probe, in the list only. "unknown": not probed yet, disabled, or the gateway is down.
    status: Literal["connected", "unreachable", "unknown"] = "unknown"
    tools: list[str] = []
