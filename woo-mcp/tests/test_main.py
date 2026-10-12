"""The seven tools against a fake WooCommerce, and who may reach the server (app/main.py, app/config.py)."""
import base64
import json

import httpx2
import pytest
from mcp.server.mcpserver.exceptions import ToolError, UnexpectedToolError
from pydantic import ValidationError
from starlette.responses import PlainTextResponse

from app import main
from app.config import Settings, settings

pytestmark = pytest.mark.anyio

# What WooCommerce sends, cut down to a few of its many fields: the tools must drop the rest.
ORDER = {
    "id": 7, "number": "7", "status": "processing", "total": "38.00", "currency": "INR",
    "date_created": "2026-10-12T10:15:00", "date_created_gmt": "2026-10-12T04:45:00",
    "payment_method": "cod", "payment_method_title": "Cash on delivery", "customer_ip_address": "203.0.113.9",
    "billing": {"first_name": "Asha", "last_name": "Rao", "email": "asha@example.com", "phone": "555-0100", "address_1": "1 Demo St"},
    "line_items": [{"id": 3, "name": "Blue Mug", "product_id": 12, "quantity": 2, "total": "38.00", "sku": "", "meta_data": []}],
    "meta_data": [{"key": "_wc_order_attribution_source_type", "value": "typein"}],
}
PRODUCT = {
    "id": 12, "name": "Blue Mug", "slug": "blue-mug", "status": "publish", "price": "19.00", "regular_price": "19.00",
    "stock_status": "instock", "stock_quantity": None, "description": "<p>Holds tea.</p>",
    "permalink": "https://store.test/product/blue-mug/", "images": [], "meta_data": [], "_links": {},
}
SHORT_PRODUCT = {
    "id": 12, "name": "Blue Mug", "slug": "blue-mug", "status": "publish", "price": "19.00", "regular_price": "19.00",
    "stock_status": "instock", "stock_quantity": None,
}


class FakeStore:
    """Stands in for WooCommerce: records every request and answers with `status` and `body`."""

    def __init__(self) -> None:
        self.requests: list[httpx2.Request] = []
        self.status, self.body = 200, []
        self.headers: dict[str, str] = {}
        self.down = False

    def handle(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        if self.down:
            raise httpx2.ConnectError("no route to host")
        if isinstance(self.body, str):
            return httpx2.Response(self.status, text=self.body, headers=self.headers)
        return httpx2.Response(self.status, json=self.body, headers=self.headers)


@pytest.fixture
def store(monkeypatch) -> FakeStore:
    fake = FakeStore()
    # The real client with only the network swapped out, so its address, key pair and headers are the tested ones.
    monkeypatch.setattr(main, "_http", main._client(httpx2.MockTransport(fake.handle)))
    return fake


async def call(tool: str, /, **arguments):  # positional-only: create_product has an argument called `name`
    return (await main.mcp.call_tool(tool, arguments)).structured_content


async def test_the_server_offers_the_seven_tools():
    assert {tool.name for tool in await main.mcp.list_tools()} == {
        "list_products", "get_product", "create_product", "update_product", "list_orders", "get_order", "update_order_status",
    }


async def test_every_tool_says_whether_it_reads_adds_or_overwrites():
    # A client such as the chat agent reads these to ask before a write and to know what is safe to repeat.
    hints = {tool.name: tool.annotations for tool in await main.mcp.list_tools()}
    for name, hint in hints.items():
        assert hint is not None, name
        assert hint.read_only_hint is (name in {"list_products", "get_product", "list_orders", "get_order"}), name
        assert hint.open_world_hint is False, name  # a tool reaches this one store and nothing else
    for name in ("update_product", "update_order_status"):  # they overwrite; the same values again change nothing more
        assert (hints[name].destructive_hint, hints[name].idempotent_hint) == (True, True), name
    # It only adds, and the store refuses a second product of the same name, so a repeat is safe.
    assert (hints["create_product"].destructive_hint, hints["create_product"].idempotent_hint) == (False, True)


async def test_every_request_carries_the_key_pair_and_says_it_came_over_https(store):
    await call("list_products")
    (request,) = store.requests
    pair = f"{settings.woocommerce_key}:{settings.woocommerce_secret}".encode()
    assert request.headers["authorization"] == "Basic " + base64.b64encode(pair).decode()
    assert request.headers["x-forwarded-proto"] == "https"


# ---- orders ------------------------------------------------------------------------

async def test_list_orders_asks_for_the_status_and_the_whole_days_given(store):
    await call("list_orders", status="processing", after="2026-10-12", before="2026-10-12")
    (request,) = store.requests
    assert request.method == "GET" and request.url.path == "/wp-json/wc/v3/orders"
    assert dict(request.url.params) == {
        "page": "1", "per_page": "20", "status": "processing",
        "after": "2026-10-12T00:00:00", "before": "2026-10-12T23:59:59",
    }


async def test_a_time_of_day_is_passed_on_as_given_and_unset_filters_are_left_out(store):
    await call("list_orders", after="2026-10-12T09:30:00")
    assert dict(store.requests[0].url.params) == {"page": "1", "per_page": "20", "after": "2026-10-12T09:30:00"}


async def test_orders_come_back_as_short_records(store):
    store.body = [ORDER]
    assert (await call("list_orders"))["orders"] == [{
        "id": 7, "number": "7", "status": "processing", "total": "38.00", "currency": "INR",
        "date_created": "2026-10-12T10:15:00", "payment_method": "Cash on delivery",
        "customer": "Asha Rao", "email": "asha@example.com",
        "items": [{"name": "Blue Mug", "quantity": 2, "total": "38.00"}],
    }]


async def test_get_order_asks_for_that_order(store):
    store.body = ORDER
    assert (await call("get_order", order_id=7))["customer"] == "Asha Rao"
    assert store.requests[0].method == "GET" and store.requests[0].url.path == "/wp-json/wc/v3/orders/7"


async def test_update_order_status_sends_only_the_new_status(store):
    store.body = {**ORDER, "status": "completed"}
    assert (await call("update_order_status", order_id=7, status="completed"))["status"] == "completed"
    (request,) = store.requests
    assert request.method == "PUT" and request.url.path == "/wp-json/wc/v3/orders/7"
    assert json.loads(request.content) == {"status": "completed"}


async def test_a_status_woocommerce_does_not_have_is_refused_before_the_store_is_asked(store):
    with pytest.raises(ToolError):
        await call("update_order_status", order_id=7, status="shipped")
    assert store.requests == []


# ---- products ----------------------------------------------------------------------

async def test_list_products_passes_the_search_and_returns_short_records(store):
    store.body = [PRODUCT]
    assert (await call("list_products", search="mug", per_page=5))["products"] == [SHORT_PRODUCT]
    (request,) = store.requests
    assert request.method == "GET" and request.url.path == "/wp-json/wc/v3/products"
    assert dict(request.url.params) == {"page": "1", "per_page": "5", "search": "mug"}


async def test_get_product_also_returns_the_description(store):
    store.body = PRODUCT
    assert await call("get_product", product_id=12) == {**SHORT_PRODUCT, "description": "<p>Holds tea.</p>"}
    assert store.requests[0].url.path == "/wp-json/wc/v3/products/12"


async def test_create_product_publishes_a_simple_product_with_a_two_decimal_price(store):
    store.status, store.body = 201, PRODUCT
    assert (await call("create_product", name="Blue Mug", regular_price=19))["id"] == 12
    (request,) = store.requests
    assert request.method == "POST" and request.url.path == "/wp-json/wc/v3/products"
    body = json.loads(request.content)
    assert body.pop("sku").startswith("mcp-")
    assert body == {"name": "Blue Mug", "type": "simple", "status": "publish", "regular_price": "19.00"}


async def test_a_stock_quantity_turns_stock_management_on(store):
    store.status, store.body = 201, PRODUCT
    await call("create_product", name="Blue Mug", regular_price=19, stock_quantity=5, description="Holds tea.", status="draft")
    body = json.loads(store.requests[0].content)
    assert (body["manage_stock"], body["stock_quantity"], body["description"], body["status"]) == (True, 5, "Holds tea.", "draft")


async def test_a_price_that_is_not_a_number_of_money_is_refused_before_the_store_is_asked(store):
    with pytest.raises(ToolError):
        await call("create_product", name="Blue Mug", regular_price=float("inf"))
    assert store.requests == []


async def test_update_product_sends_only_what_was_given(store):
    store.body = PRODUCT
    await call("update_product", product_id=12, regular_price=24.5)
    (request,) = store.requests
    assert request.method == "PUT" and request.url.path == "/wp-json/wc/v3/products/12"
    assert json.loads(request.content) == {"regular_price": "24.50"}


async def test_update_product_with_nothing_to_change_is_refused_before_the_store_is_asked(store):
    with pytest.raises(ToolError, match="Nothing to change"):
        await call("update_product", product_id=12)
    assert store.requests == []


# ---- no duplicates: a create repeated after a timeout must not make a second product ------
# The store refuses a SKU that is taken, also when two requests arrive at once (checked against a
# real WooCommerce: eight at once made one product). So the SKU is made from the name.

async def sku_sent_for(store, name: str) -> str:
    store.status, store.body = 201, PRODUCT
    await call("create_product", name=name, regular_price=19)
    return json.loads(store.requests[-1].content)["sku"]


async def test_the_same_name_always_gets_the_same_sku_however_it_is_typed(store):
    assert await sku_sent_for(store, "Blue Mug") == await sku_sent_for(store, "  blue   MUG ")


async def test_a_different_name_gets_a_different_sku(store):
    assert await sku_sent_for(store, "Blue Mug") != await sku_sent_for(store, "Red Mug")


async def test_a_repeated_create_reports_the_product_that_is_already_there(store):
    store.status = 400  # WooCommerce's own answer, word for word
    store.body = {"code": "product_invalid_sku", "message": "Invalid or duplicated SKU.", "data": {"status": 400, "resource_id": 12, "unique_sku": "mcp-x-1"}}
    with pytest.raises(ToolError, match=r"already exists \(id 12\)") as caught:
        await call("create_product", name="Blue Mug", regular_price=19)
    assert not isinstance(caught.value, UnexpectedToolError)


@pytest.mark.parametrize("message", [
    "The product with SKU (mcp-0123456789abcdef) you are trying to insert is already present in the lookup table",
    # A store in another language words it differently; only the SKU itself is the same.
    "Le produit avec l'UGS (mcp-0123456789abcdef) que vous essayez d'insérer est déjà présent dans la table",
])
async def test_a_name_held_by_a_product_in_the_trash_is_explained_without_talk_of_skus(store, message):
    store.status, store.body = 400, {"code": "woocommerce_rest_product_not_created", "message": message, "data": {"status": 400}}
    with pytest.raises(ToolError, match="trash") as caught:
        await call("create_product", name="Blue Mug", regular_price=19)
    assert "mcp-" not in str(caught.value)


async def test_a_create_that_failed_for_another_reason_keeps_woocommerces_own_words(store):
    # The same code covers every failed create; only one that names a SKU of ours is about the name.
    store.status, store.body = 400, {"code": "woocommerce_rest_product_not_created", "message": "Invalid product type.", "data": {"status": 400}}
    with pytest.raises(ToolError, match="Invalid product type"):
        await call("create_product", name="Blue Mug", regular_price=19)


async def test_renaming_a_product_made_here_moves_its_sku_to_the_new_name(store):
    store.body = {**PRODUCT, "sku": "mcp-0123456789abcdef"}
    await call("update_product", product_id=12, name="Red Mug")
    looked, changed = store.requests
    assert (looked.method, changed.method) == ("GET", "PUT") and looked.url.path == changed.url.path == "/wp-json/wc/v3/products/12"
    assert json.loads(changed.content) == {"name": "Red Mug", "sku": await sku_sent_for(store, "Red Mug")}


@pytest.mark.parametrize("theirs", ["MUG-BLUE-01", "mcp-cable-01", ""])  # the second only starts like one of ours
async def test_renaming_leaves_a_sku_the_merchant_chose_alone(store, theirs):
    store.body = {**PRODUCT, "sku": theirs}
    await call("update_product", product_id=12, name="Red Mug")
    assert json.loads(store.requests[-1].content) == {"name": "Red Mug"}


# ---- lists: one page, and how many there are in all ------------------------------------

@pytest.mark.parametrize(("tool", "item"), [("list_orders", ORDER), ("list_products", PRODUCT)])
async def test_a_list_says_how_many_match_in_all_not_only_on_this_page(store, tool, item):
    # Otherwise a model asked "how many orders today?" answers with the size of one page.
    store.body, store.headers = [item], {"X-WP-Total": "37"}
    assert (await call(tool, per_page=1))["total"] == 37


async def test_a_list_without_the_stores_count_still_comes_back(store):
    store.body = [ORDER]
    listed = await call("list_orders")
    assert listed["total"] is None and len(listed["orders"]) == 1


# ---- when the store says no, or says nothing ------------------------------------------

async def test_a_refusal_from_woocommerce_becomes_a_tool_error_with_its_message(store):
    store.status = 404
    store.body = {"code": "woocommerce_rest_shop_order_invalid_id", "message": "Invalid ID.", "data": {"status": 404}}
    with pytest.raises(ToolError, match="Invalid ID") as caught:
        await call("get_order", order_id=999)
    assert not isinstance(caught.value, UnexpectedToolError)  # that one would reach the model as a bare "Error executing tool"


async def test_an_error_page_that_is_not_json_still_names_the_status(store):
    store.status, store.body = 502, "<html>Bad Gateway</html>"
    with pytest.raises(ToolError, match="502") as caught:
        await call("list_products")
    assert not isinstance(caught.value, UnexpectedToolError)


async def test_a_redirect_is_reported_and_neither_followed_nor_read_as_an_answer(store):
    store.status, store.body = 301, ""  # e.g. a store that forces HTTPS: the key pair must not be sent on elsewhere
    with pytest.raises(ToolError, match="301") as caught:
        await call("list_orders")
    assert not isinstance(caught.value, UnexpectedToolError) and len(store.requests) == 1


async def test_a_store_that_does_not_answer_is_a_tool_error_not_a_crash(store):
    store.down = True
    with pytest.raises(ToolError, match="did not answer") as caught:
        await call("list_orders")
    assert not isinstance(caught.value, UnexpectedToolError)


# ---- who may reach the server ---------------------------------------------------------

GATEWAY = f"Bearer {settings.mcp_auth_token}"


async def status_of(method: str, path: str, authorization: str | None = None) -> int:
    headers = {"authorization": authorization} if authorization else {}
    async with httpx2.AsyncClient(transport=httpx2.ASGITransport(app=main.app), base_url="http://localhost:8000") as client:
        return (await client.request(method, path, headers=headers)).status_code


@pytest.mark.parametrize("authorization", [None, "Bearer wrong", settings.mcp_auth_token, "Basic " + settings.mcp_auth_token, GATEWAY + "x"])
@pytest.mark.parametrize("path", ["/mcp", "/", "/healthz/"])
async def test_everything_but_the_health_check_refuses_a_caller_without_the_gateways_token(path, authorization):
    assert await status_of("POST", path, authorization) == 401


async def test_the_health_check_answers_without_a_token():
    assert await status_of("GET", "/healthz") == 200


async def test_the_gateways_token_gets_through_to_the_server():
    reached: list[str] = []

    async def inner(scope, receive, send):
        reached.append(scope["path"])
        await PlainTextResponse("ok")(scope, receive, send)

    async with httpx2.AsyncClient(transport=httpx2.ASGITransport(app=main.RequireCredential(inner)), base_url="http://localhost:8000") as client:
        assert (await client.post("/mcp", headers={"authorization": GATEWAY})).status_code == 200
    assert reached == ["/mcp"]


# ---- settings ---------------------------------------------------------------------------

def good_settings(**overrides) -> Settings:
    values = {
        "woocommerce_url": "http://wordpress", "woocommerce_key": "ck_x", "woocommerce_secret": "cs_x",
        "mcp_auth_token": "t" * 32,
    }
    return Settings(_env_file=None, **{**values, **overrides})


def test_the_timeout_stays_under_the_gateways_thirty_seconds():
    assert good_settings().timeout_seconds < 30


def test_settings_refuse_a_token_too_short_to_be_a_secret():
    with pytest.raises(ValidationError, match="mcp_auth_token"):
        good_settings(mcp_auth_token="short")


@pytest.mark.parametrize("missing", ["woocommerce_url", "woocommerce_key", "woocommerce_secret", "mcp_auth_token"])
def test_no_secret_or_address_has_a_default(missing, monkeypatch):
    monkeypatch.delenv(missing.upper(), raising=False)
    values = {"woocommerce_url": "http://wordpress", "woocommerce_key": "ck_x", "woocommerce_secret": "cs_x", "mcp_auth_token": "t" * 32}
    del values[missing]
    with pytest.raises(ValidationError, match=missing):
        Settings(_env_file=None, **values)
