"""One WooCommerce store's products and orders as MCP tools, over Streamable HTTP at /mcp.

Each tool is one call to the store's REST API (/wp-json/wc/v3), authenticated
with the store's consumer key and secret. Only the gateway may call this
server: every request but the health check must carry MCP_AUTH_TOKEN, and the
service has no published port.

Run with `uvicorn app.main:app`.
"""
import hmac
from typing import Annotated, Any, Literal

import httpx2
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings
from pydantic import Field
from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse

from app.config import settings

mcp = MCPServer("woo-mcp", instructions="Products and orders of one WooCommerce store. Dates are in the store's time zone.")

OrderStatus = Literal["pending", "processing", "on-hold", "completed", "cancelled", "refunded", "failed"]
ProductStatus = Literal["publish", "draft"]
Id = Annotated[int, Field(ge=1)]
Page = Annotated[int, Field(ge=1)]
PerPage = Annotated[int, Field(ge=1, le=100)]
Price = Annotated[float, Field(ge=0)]
Stock = Annotated[int, Field(ge=0)]

# What a tool returns of WooCommerce's much larger objects: enough to answer with,
# small enough for a model's context, and no more customer data than a question needs.
_PRODUCT_FIELDS = ("id", "name", "slug", "status", "price", "regular_price", "stock_status", "stock_quantity")


def _client(transport: httpx2.AsyncBaseTransport | None = None) -> httpx2.AsyncClient:
    return httpx2.AsyncClient(
        base_url=f"{settings.woocommerce_url.rstrip('/')}/wp-json/wc/v3/",
        auth=(settings.woocommerce_key, settings.woocommerce_secret),
        # WooCommerce takes the key pair as Basic auth only when WordPress believes the request
        # came over HTTPS; inside the cluster it is plain HTTP, so say what an Ingress would say.
        headers={"X-Forwarded-Proto": "https"},
        timeout=settings.timeout_seconds,
        transport=transport,
    )


_http = _client()


async def _woo(method: str, path: str, **request: Any) -> Any:
    """One REST call. Anything that goes wrong is a ToolError, so the model reads why."""
    try:
        response = await _http.request(method, path, **request)
    except httpx2.HTTPError as exc:
        raise ToolError(f"The store did not answer ({type(exc).__name__})") from exc
    if not response.is_success:  # a redirect too: it is not followed, so the key pair goes nowhere else
        try:
            reason = response.json()["message"]  # WooCommerce explains itself: {"code": ..., "message": "Invalid ID."}
        except (ValueError, KeyError, TypeError):
            reason = f"HTTP {response.status_code}"
        raise ToolError(f"WooCommerce refused the request: {reason}")
    return response.json()


def _given(**values: Any) -> dict[str, Any]:
    return {name: value for name, value in values.items() if value is not None}


def _money(price: float) -> str:
    return f"{price:.2f}"  # WooCommerce takes prices as strings


def _product(product: dict[str, Any], *extra: str) -> dict[str, Any]:
    return {field: product.get(field) for field in (*_PRODUCT_FIELDS, *extra)}


def _order(order: dict[str, Any]) -> dict[str, Any]:
    billing = order.get("billing") or {}
    return {
        "id": order.get("id"),
        "number": order.get("number"),
        "status": order.get("status"),
        "total": order.get("total"),
        "currency": order.get("currency"),
        "date_created": order.get("date_created"),
        "payment_method": order.get("payment_method_title"),
        "customer": f"{billing.get('first_name', '')} {billing.get('last_name', '')}".strip(),
        "email": billing.get("email"),
        "items": [
            {"name": item.get("name"), "quantity": item.get("quantity"), "total": item.get("total")}
            for item in order.get("line_items") or []
        ],
    }


# ---- products ----------------------------------------------------------------------

@mcp.tool()
async def list_products(search: str | None = None, status: ProductStatus | None = None, page: Page = 1, per_page: PerPage = 20) -> dict[str, Any]:
    """List the store's products, newest first. `search` matches the product name."""
    products = await _woo("GET", "products", params=_given(page=page, per_page=per_page, search=search, status=status))
    return {"products": [_product(product) for product in products]}


@mcp.tool()
async def get_product(product_id: Id) -> dict[str, Any]:
    """One product by its id, with its description."""
    return _product(await _woo("GET", f"products/{product_id}"), "description")


@mcp.tool()
async def create_product(
    name: Annotated[str, Field(min_length=1, max_length=200)],
    regular_price: Price,
    description: str | None = None,
    status: ProductStatus = "publish",
    stock_quantity: Stock | None = None,
) -> dict[str, Any]:
    """Create a product. With status "publish" (the default) it shows on the storefront at once.
    Give `stock_quantity` only if the store should count stock for it."""
    body = {"name": name, "type": "simple", "status": status, "regular_price": _money(regular_price)}
    body |= _given(description=description, stock_quantity=stock_quantity)
    if stock_quantity is not None:
        body["manage_stock"] = True
    return _product(await _woo("POST", "products", json=body), "description")


@mcp.tool()
async def update_product(
    product_id: Id,
    name: Annotated[str, Field(min_length=1, max_length=200)] | None = None,
    regular_price: Price | None = None,
    description: str | None = None,
    status: ProductStatus | None = None,
    stock_quantity: Stock | None = None,
) -> dict[str, Any]:
    """Change a product. Only the fields given are changed."""
    body = _given(name=name, description=description, status=status, stock_quantity=stock_quantity)
    if regular_price is not None:
        body["regular_price"] = _money(regular_price)
    if stock_quantity is not None:
        body["manage_stock"] = True
    if not body:
        raise ToolError("Nothing to change: give at least one field besides product_id")
    return _product(await _woo("PUT", f"products/{product_id}", json=body), "description")


# ---- orders ------------------------------------------------------------------------

@mcp.tool()
async def list_orders(
    status: OrderStatus | None = None,
    after: str | None = None,
    before: str | None = None,
    page: Page = 1,
    per_page: PerPage = 20,
) -> dict[str, Any]:
    """List orders, newest first. `after` and `before` limit them by creation time, in the
    store's time zone: a date (2026-10-12) means that whole day, or give a time (2026-10-12T09:30:00).
    For today's orders pass today's date as both."""
    # WooCommerce wants a full date-time: a bare date starts the day for `after` and ends it for `before`.
    if after and "T" not in after:
        after += "T00:00:00"
    if before and "T" not in before:
        before += "T23:59:59"
    orders = await _woo("GET", "orders", params=_given(page=page, per_page=per_page, status=status, after=after, before=before))
    return {"orders": [_order(order) for order in orders]}


@mcp.tool()
async def get_order(order_id: Id) -> dict[str, Any]:
    """One order by its id: status, total, customer and the items bought."""
    return _order(await _woo("GET", f"orders/{order_id}"))


@mcp.tool()
async def update_order_status(order_id: Id, status: OrderStatus) -> dict[str, Any]:
    """Move an order to another status, for example "completed" once it has been delivered."""
    return _order(await _woo("PUT", f"orders/{order_id}", json={"status": status}))


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
