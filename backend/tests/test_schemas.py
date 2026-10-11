"""Input rules: what the API accepts before any route code runs."""
import pytest
from pydantic import ValidationError

from app.schemas.auth import LoginRequest
from app.schemas.stores import CreateStoreRequest
from app.schemas.upstreams import CreateUpstreamRequest, UpdateUpstreamRequest
from app.services.stores import slugify

BASE = {"name": "Woo", "slug": "woo", "url": "http://woo-mcp:9000/mcp"}


def upstream(**overrides) -> CreateUpstreamRequest:
    return CreateUpstreamRequest(**{**BASE, **overrides})


# ---- upstream servers --------------------------------------------------------


def test_upstream_defaults():
    made = upstream()
    assert made.enabled is True and made.credential is None and made.store_id is None
    assert made.credential_header == "Authorization"


@pytest.mark.parametrize("slug", ["woo", "store-a", "a1", "x" * 50, "github-mcp-2"])
def test_good_slugs(slug):
    assert upstream(slug=slug).slug == slug


@pytest.mark.parametrize(
    "slug",
    ["a__b", "a_b", "Woo", "-woo", "woo-", "a--b", "", "woo shop", "woo\n", "x" * 51, "wöo", "woo.mcp"],
)
def test_bad_slugs(slug):
    # No underscore may ever pass: the gateway splits `slug__tool` on the first `__`.
    with pytest.raises(ValidationError):
        upstream(slug=slug)


@pytest.mark.parametrize(
    "url",
    [
        "ftp://example.com/mcp",
        "file:///etc/passwd",
        "javascript:alert(1)",
        "not a url",
        "http://user:pw@example.com/mcp",
        "https://token@example.com/mcp",
    ],
)
def test_bad_urls(url):
    with pytest.raises(ValidationError):
        upstream(url=url)


@pytest.mark.parametrize("url", ["http://woo-mcp:9000/mcp", "https://mcp.example.com/mcp", "http://10.0.0.5:8080/mcp"])
def test_good_urls_including_cluster_internal_ones(url):
    assert str(upstream(url=url).url) == url


@pytest.mark.parametrize("credential", ["a\r\nEvil: 1", "a\nb", "tab\there", "café", "x" * 4001, "bell\x07"])
def test_credential_cannot_carry_control_characters(credential):
    # It becomes a header value on every request the gateway sends upstream.
    with pytest.raises(ValidationError):
        upstream(credential=credential)


def test_credential_accepts_a_normal_header_value():
    assert upstream(credential="Bearer abc.DEF-123_~+/=").credential == "Bearer abc.DEF-123_~+/="


@pytest.mark.parametrize("header", ["X Key", "X-Key\n", "X:Key", "", "Ünï"])
def test_bad_header_names(header):
    with pytest.raises(ValidationError):
        upstream(credential_header=header)


def test_update_tells_omitted_apart_from_null_and_empty():
    assert UpdateUpstreamRequest().model_dump(exclude_unset=True) == {}
    assert UpdateUpstreamRequest(enabled=False).model_dump(exclude_unset=True) == {"enabled": False}
    # "" removes the credential; null store_id makes the server shared. Both must survive as "sent".
    sent = UpdateUpstreamRequest(credential="", store_id=None).model_dump(exclude_unset=True)
    assert sent == {"credential": "", "store_id": None}


def test_update_cannot_change_the_slug_and_applies_the_same_url_rule():
    assert "slug" not in UpdateUpstreamRequest.model_fields
    with pytest.raises(ValidationError):
        UpdateUpstreamRequest(url="http://user:pw@example.com/mcp")


# ---- login and stores ----------------------------------------------------------


def test_login_lowercases_the_email():
    assert LoginRequest(email="Bob@Example.COM", password="password1").email == "bob@example.com"


@pytest.mark.parametrize("password", ["short", "x" * 21, "🔥" * 20, "pass\nword"])
def test_login_password_rule(password):
    with pytest.raises(ValidationError):
        LoginRequest(email="bob@example.com", password=password)


def test_store_name_is_trimmed_and_bounded():
    assert CreateStoreRequest(name="  My Shop  ").name == "My Shop"
    for bad in ("", "   ", "x" * 201):
        with pytest.raises(ValidationError):
            CreateStoreRequest(name=bad)


@pytest.mark.parametrize(
    ("name", "slug"),
    [("My Shop", "my-shop"), ("  Köln & Co.  ", "k-ln-co"), ("a__b", "a-b"), ("!!!", "store"), ("Shop 2", "shop-2")],
)
def test_slugify(name, slug):
    assert slugify(name) == slug
