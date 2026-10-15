"""Stock photos from Pexels as one MCP tool, over Streamable HTTP at /mcp.

find_photos searches Pexels with this server's own API key, which the gateway
and the model never see. The chat agent passes a photo's address on to the
store's create_product or update_product, and the store copies the file. Only
the gateway may call this server: every request but the health check must
carry MCP_AUTH_TOKEN, and the service has no published port.

Run with `uvicorn app.main:app`.
"""
import hmac
from typing import Annotated, Any

import httpx2
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations
from pydantic import Field
from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse

from app.config import settings

mcp = MCPServer("photos-mcp", instructions="Stock photos from Pexels, to use as product photos.")

# It only reads, but from Pexels: outside this platform (open_world_hint).
_READS_OUTSIDE = ToolAnnotations(read_only_hint=True, open_world_hint=True)


def _client(transport: httpx2.AsyncBaseTransport | None = None) -> httpx2.AsyncClient:
    return httpx2.AsyncClient(
        base_url="https://api.pexels.com/v1/",
        headers={"Authorization": settings.pexels_api_key},  # the bare key, no "Bearer": Pexels's own scheme
        timeout=settings.timeout_seconds,
        transport=transport,
    )


_http = _client()


@mcp.tool(annotations=_READS_OUTSIDE)
async def find_photos(query: Annotated[str, Field(min_length=1, max_length=100)]) -> dict[str, Any]:
    """Search Pexels for square stock photos, five at most, for example "blue ceramic mug".
    Pick the one whose description fits the product and pass its `photo` as `image_url` to the
    store's create_product or update_product. When you show a photo, credit its photographer and Pexels."""
    if not settings.pexels_api_key:
        raise ToolError("Photo search is not set up: this server has no Pexels API key (PEXELS_API_KEY)")
    try:
        response = await _http.get("search", params={"query": query, "per_page": 5, "orientation": "square"})
    except httpx2.HTTPError as exc:
        raise ToolError(f"Pexels did not answer ({type(exc).__name__})") from exc
    if not response.is_success:  # a redirect too: it is not followed, so the key goes nowhere else
        raise ToolError(f"Pexels refused the search (HTTP {response.status_code})")
    return {
        "photos": [
            {
                "id": photo["id"],
                "description": photo.get("alt") or "",
                "photographer": photo.get("photographer"),
                "photo": photo["src"]["large"],  # 940 × 650 at most: enough for a shop, light enough to copy
                "pexels_page": photo.get("url"),
            }
            for photo in response.json().get("photos", [])
        ]
    }


# ---- the HTTP app ------------------------------------------------------------------

@mcp.custom_route("/healthz", methods=["GET"])
async def healthz(request: Request) -> JSONResponse:
    return JSONResponse({"status": "ok"})


class RequireCredential:
    """Refuses every request but the health check unless it carries the gateway's token."""

    def __init__(self, app) -> None:
        self.app = app
        self.expected = f"Bearer {settings.mcp_auth_token}".encode()

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] == "http" and scope["path"] != "/healthz":
            sent = dict(scope["headers"]).get(b"authorization", b"")
            # compare_digest: a plain == would leak how many leading characters matched.
            if not hmac.compare_digest(sent, self.expected):
                return await PlainTextResponse("unauthorized", 401)(scope, receive, send)
        await self.app(scope, receive, send)


app = RequireCredential(
    mcp.streamable_http_app(
        # No session lives in this process: the gateway opens a fresh connection per call.
        stateless_http=True,
        # The SDK only answers to "localhost" unless told otherwise; the gateway reaches this by its service name.
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=True, allowed_hosts=settings.allowed_hosts),
    )
)
