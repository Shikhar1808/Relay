"""find_photos against a fake Pexels, and who may reach the server (app/main.py, app/config.py)."""
import httpx2
import pytest
from mcp.server.mcpserver.exceptions import ToolError, UnexpectedToolError
from pydantic import ValidationError
from starlette.responses import PlainTextResponse

from app import main
from app.config import Settings, settings

pytestmark = pytest.mark.anyio

# What Pexels sends for one photo, cut down to a few of its fields: the tool must drop the rest.
PHOTO = {
    "id": 1251175, "width": 3000, "height": 3000, "url": "https://www.pexels.com/photo/blue-mug-1251175/",
    "photographer": "Asha Rao", "photographer_url": "https://www.pexels.com/@asha", "avg_color": "#2D4A6B",
    "src": {
        "original": "https://images.pexels.com/photos/1251175/pexels-photo-1251175.jpeg",
        "large": "https://images.pexels.com/photos/1251175/pexels-photo-1251175.jpeg?auto=compress&cs=tinysrgb&h=650&w=940",
        "tiny": "https://images.pexels.com/photos/1251175/pexels-photo-1251175.jpeg?h=200",
    },
    "liked": False, "alt": "Blue ceramic mug on a wooden table",
}
SHORT_PHOTO = {
    "id": 1251175, "description": "Blue ceramic mug on a wooden table", "photographer": "Asha Rao",
    "photo": PHOTO["src"]["large"], "pexels_page": "https://www.pexels.com/photo/blue-mug-1251175/",
}


class FakePexels:
    """Stands in for api.pexels.com: records every request and answers with `status` and `body`."""

    def __init__(self) -> None:
        self.requests: list[httpx2.Request] = []
        self.status, self.body = 200, {"photos": [PHOTO], "total_results": 812, "page": 1, "per_page": 5}
        self.down = False

    def handle(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        if self.down:
            raise httpx2.ConnectError("no route to host")
        return httpx2.Response(self.status, json=self.body)


@pytest.fixture
def pexels(monkeypatch) -> FakePexels:
    fake = FakePexels()
    # The real client with only the network swapped out, so its address and key header are the tested ones.
    monkeypatch.setattr(main, "_http", main._client(httpx2.MockTransport(fake.handle)))
    return fake


async def find(query: str):
    return (await main.mcp.call_tool("find_photos", {"query": query})).structured_content


async def test_the_one_tool_only_reads_but_reaches_outside_the_platform():
    (tool,) = await main.mcp.list_tools()
    assert tool.name == "find_photos"
    assert (tool.annotations.read_only_hint, tool.annotations.open_world_hint) == (True, True)


async def test_a_search_sends_the_key_and_asks_for_five_square_photos(pexels):
    await find("blue mug")
    (request,) = pexels.requests
    assert str(request.url).startswith("https://api.pexels.com/v1/search?")
    assert request.headers["authorization"] == settings.pexels_api_key  # Pexels's own scheme: no "Bearer"
    assert dict(request.url.params) == {"query": "blue mug", "per_page": "5", "orientation": "square"}


async def test_photos_come_back_as_short_records(pexels):
    assert await find("blue mug") == {"photos": [SHORT_PHOTO]}


async def test_a_photo_without_a_description_gets_an_empty_one(pexels):
    pexels.body = {"photos": [{**PHOTO, "alt": None}]}
    assert (await find("mug"))["photos"][0]["description"] == ""


async def test_nothing_found_is_an_empty_list(pexels):
    pexels.body = {"photos": [], "total_results": 0}
    assert await find("zzzz") == {"photos": []}


@pytest.mark.parametrize("query", ["", "x" * 101])
async def test_a_query_out_of_bounds_is_refused_before_pexels_is_asked(pexels, query):
    with pytest.raises(ToolError):
        await find(query)
    assert pexels.requests == []


@pytest.mark.parametrize("status", [401, 429, 500, 301])  # a bad key, the hourly limit, an outage, a redirect
async def test_a_refusal_from_pexels_becomes_a_tool_error_with_its_status(pexels, status):
    pexels.status, pexels.body = status, {"error": "no"}
    with pytest.raises(ToolError, match=str(status)) as caught:
        await find("blue mug")
    assert not isinstance(caught.value, UnexpectedToolError)  # that one would reach the model as a bare "Error executing tool"
    assert len(pexels.requests) == 1  # a redirect is not followed, so the key goes nowhere else


async def test_pexels_not_answering_is_a_tool_error_not_a_crash(pexels):
    pexels.down = True
    with pytest.raises(ToolError, match="did not answer") as caught:
        await find("blue mug")
    assert not isinstance(caught.value, UnexpectedToolError)


# ---- who may reach the server ---------------------------------------------------------

GATEWAY = f"Bearer {settings.mcp_auth_token}"


async def status_of(method: str, path: str, authorization: str | None = None) -> int:
    headers = {"authorization": authorization} if authorization else {}
    async with httpx2.AsyncClient(transport=httpx2.ASGITransport(app=main.app), base_url="http://localhost:8000") as client:
        return (await client.request(method, path, headers=headers)).status_code


@pytest.mark.parametrize("authorization", [None, "Bearer wrong", settings.mcp_auth_token, GATEWAY + "x"])
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

def test_the_timeout_stays_under_the_gateways_thirty_seconds():
    assert Settings(_env_file=None, mcp_auth_token="t" * 32).timeout_seconds < 30


def test_settings_refuse_a_token_too_short_to_be_a_secret():
    with pytest.raises(ValidationError, match="mcp_auth_token"):
        Settings(_env_file=None, mcp_auth_token="short")


def test_the_token_has_no_default(monkeypatch):
    monkeypatch.delenv("MCP_AUTH_TOKEN", raising=False)
    with pytest.raises(ValidationError, match="mcp_auth_token"):
        Settings(_env_file=None)


async def test_without_a_pexels_key_the_tool_says_it_is_not_set_up(pexels, monkeypatch):
    # Compose passes PEXELS_API_KEY= when .env has none: the server still starts, so `up --wait` does not fail.
    monkeypatch.setattr(settings, "pexels_api_key", "")
    with pytest.raises(ToolError, match="not set up") as caught:
        await find("blue mug")
    assert not isinstance(caught.value, UnexpectedToolError) and pexels.requests == []
