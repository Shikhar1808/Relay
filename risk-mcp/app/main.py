"""Risk scores for a store's orders as one MCP tool, over Streamable HTTP at /mcp.

score_orders applies a few fixed rules to orders the chat agent passes in, as the
store's list_orders returns them, and says how risky each looks and why. It reads
nothing, stores nothing and calls nothing: the agent acts on the result with the
store's own server (put an order on hold) and the fulfilment server (ship it), each
behind the chat's confirmation. Only the gateway may call this server: every request
but the health check must carry MCP_AUTH_TOKEN, and the service has no published port.

Run with `uvicorn app.main:app`.
"""
import hmac
from collections import Counter
from decimal import Decimal
from typing import Annotated, Any

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations
from pydantic import BaseModel, Field, ValidationError
from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse

from app.config import settings

mcp = MCPServer("risk-mcp", instructions="Risk scores for a store's orders, from fixed rules. It changes nothing.")

# The rules. Amounts are in the store's own currency.
HIGH_TOTAL = Decimal(20000)
COD_TOTAL = Decimal(5000)       # cash on delivery above this: a large order nobody has paid for yet
BULK_QUANTITY = 10              # one item ordered this many times or more
REPEATS = 3                     # one email on this many orders of the same batch
# shortcut: a short fixed list of throwaway email domains, use a maintained list if it ever matters
THROWAWAY_DOMAINS = frozenset({
    "mailinator.com", "yopmail.com", "guerrillamail.com", "10minutemail.com",
    "temp-mail.org", "trashmail.com", "sharklasers.com", "getnada.com",
})
LEVELS = ((60, "high", "put it on hold"), (30, "medium", "check it by hand"), (0, "low", "ship it"))


class Item(BaseModel):
    name: str | None = None
    quantity: int = Field(ge=0)


class Order(BaseModel):
    """The fields the rules read. Any other field of the store's order record is accepted and ignored."""
    id: int
    total: Annotated[Decimal, Field(ge=0)]   # the store sends it as text ("38.00"): read exactly, not as a float
    email: str | None = None
    payment_code: str | None = None          # WooCommerce's own code ("cod"): it survives a renamed title
    payment_method: str | None = None        # the title, read only when the code is missing
    items: list[Item] = []


def assess(order: Order, orders_by_email: Counter[str]) -> dict[str, Any]:
    """One order's score, level and reasons. Each rule that fires adds its points; 100 at most."""
    email = (order.email or "").strip().casefold()
    cod = order.payment_code == "cod" if order.payment_code else "cash on delivery" in (order.payment_method or "").casefold()
    bulk = next((item for item in order.items if item.quantity >= BULK_QUANTITY), None)
    rules = [
        (order.total > HIGH_TOTAL, 40, f"total {order.total} is over {HIGH_TOTAL}"),
        (bulk is not None, 30, f"{bulk.quantity} of {bulk.name or 'one item'} in one order" if bulk else ""),
        (email.rpartition("@")[2] in THROWAWAY_DOMAINS, 40, f"{email.rpartition('@')[2]} is a throwaway email domain"),
        (cod and order.total > COD_TOTAL, 20, f"cash on delivery for {order.total}"),
        (bool(email) and orders_by_email[email] >= REPEATS, 30, f"{orders_by_email[email]} orders from {email} in this batch"),
    ]
    fired = [(points, reason) for applies, points, reason in rules if applies]
    score = min(100, sum(points for points, _ in fired))
    level, advice = next((name, advice) for floor, name, advice in LEVELS if score >= floor)
    # One string, not a list: the results panel shows a list as "2 items".
    return {"order_id": order.id, "score": score, "level": level, "advice": advice,
            "reasons": "; ".join(reason for _, reason in fired) or "none"}


# Plain objects, checked below, and not list[Order]: nested models would put $ref into the input
# format, which every chat message sends to the model, and Groq does not document $ref.
@mcp.tool(annotations=ToolAnnotations(read_only_hint=True, open_world_hint=False))
async def score_orders(orders: Annotated[list[dict[str, Any]], Field(min_length=1, max_length=50)]) -> dict[str, Any]:
    """Score orders for risk with fixed rules: a high total, one item in bulk, a throwaway email domain,
    cash on delivery on a large order, one email on several orders of this batch.
    From each order the store's list_orders returned, copy only id, total, email, payment_code and items
    (each with name and quantity). Send all of a day's orders in one call.
    Each gets a score from 0 to 100, a level and the reasons. "high": put it on hold in the store;
    "medium": a person should look at it; "low": it can be shipped. Amounts are in the store's currency."""
    checked = []
    for number, order in enumerate(orders, 1):
        try:
            checked.append(Order.model_validate(order))
        except ValidationError as exc:
            problem = exc.errors()[0]
            raise ToolError(f"Order {number} of the batch: {'.'.join(map(str, problem['loc']))}: {problem['msg']}") from exc
    orders_by_email = Counter((order.email or "").strip().casefold() for order in checked)
    return {"scores": [assess(order, orders_by_email) for order in checked]}


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
