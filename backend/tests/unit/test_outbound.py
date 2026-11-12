"""The guard for URLs a model or a user supplied (design §7.3; ported from llm-steward's tests):
a fake resolver and a fake network."""

from typing import Any

import httpx
import pytest

from banyan.tools.outbound import TooLarge, UrlNotAllowed, check, client, get
from tests.unit.fake_net import PUBLIC, resolve


async def _check(url: str, **kw: Any) -> Any:
    return await check(url, resolve=resolve, **kw)


@pytest.mark.parametrize(
    "address",
    [
        "10.1.2.3",
        "172.16.0.1",
        "192.168.1.1",
        "127.0.0.1",
        "169.254.169.254",  # cloud metadata
        "100.64.0.1",  # CGNAT
        "0.0.0.0",
        "224.0.0.1",
        "[::1]",
        "[fd00::1]",
        "[fe80::1]",
        "[::ffff:127.0.0.1]",  # IPv4 written as IPv6
        "[64:ff9b::7f00:1]",  # NAT64
        "[64:ff9b:1::a00:5]",
        "[::127.0.0.1]",  # IPv4-compatible
        "[2002:7f00:1::1]",  # 6to4
        "[2001:0:4136:e378:8000:63bf:3fff:fdd2]",  # Teredo
    ],
)
async def test_internal_addresses_are_refused(address: str) -> None:
    with pytest.raises(UrlNotAllowed):
        await _check(f"https://{address}:8600/admin")


async def test_a_name_with_any_internal_answer_is_refused() -> None:
    for url in (
        "https://split.example.com/x",
        "https://rebind.example.com/x",
        "https://nowhere.example.com/x",  # does not resolve
    ):
        with pytest.raises(UrlNotAllowed):
            await _check(url)


async def test_https_only_and_no_credentials() -> None:
    for url in (
        "http://docs.example.com/x",
        "ftp://docs.example.com/x",
        "https://user:pass@docs.example.com/x",
    ):
        with pytest.raises(UrlNotAllowed):
            await _check(url)


async def test_the_connection_goes_to_the_checked_address() -> None:
    target = await _check("https://docs.example.com/in?a=1")
    assert target.url == f"https://{PUBLIC}/in?a=1"
    assert (target.host, target.host_header) == ("docs.example.com", "docs.example.com")
    v6 = await _check("https://v6.example.com:8443/x")
    assert v6.url == "https://[2606:4700::1111]:8443/x"
    assert v6.host_header == "v6.example.com:8443"


async def test_local_tests_may_reach_private_addresses_over_http() -> None:
    target = await _check("http://127.0.0.1:9000/page", allow_private=True)
    assert target.url == "http://127.0.0.1:9000/page"


async def _get(routes: dict[str, httpx.Response], url: str) -> Any:
    seen: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        return routes[req.headers["host"] + req.url.path]

    async with client(httpx.MockTransport(handler)) as http:
        return await get(http, url, max_bytes=100, resolve=resolve), seen


async def test_a_request_is_pinned_with_its_hostname_kept() -> None:
    fetched, [sent] = await _get(
        {"docs.example.com/a.md": httpx.Response(200, content=b"# hi")},
        "https://docs.example.com/a.md",
    )
    assert fetched.body == b"# hi" and fetched.status_code == 200
    assert sent.url.host == PUBLIC
    assert sent.headers["host"] == "docs.example.com"
    assert sent.extensions["sni_hostname"] == "docs.example.com"


async def test_redirects_are_checked_like_the_first_url() -> None:
    routes = {
        "docs.example.com/a": httpx.Response(
            302, headers={"location": "https://docs.example.com/b"}
        ),
        "docs.example.com/b": httpx.Response(200, content=b"ok"),
        "docs.example.com/inside": httpx.Response(
            302, headers={"location": "http://169.254.169.254/latest/meta-data"}
        ),
        "docs.example.com/loop": httpx.Response(302, headers={"location": "/loop"}),
    }
    fetched, _ = await _get(routes, "https://docs.example.com/a")
    assert (fetched.body, fetched.url) == (b"ok", "https://docs.example.com/b")
    with pytest.raises(UrlNotAllowed):
        await _get(routes, "https://docs.example.com/inside")
    with pytest.raises(UrlNotAllowed):
        await _get(routes, "https://docs.example.com/loop")


async def test_a_body_over_the_limit_is_refused() -> None:
    with pytest.raises(TooLarge):
        await _get(
            {"docs.example.com/big": httpx.Response(200, content=b"x" * 101)},
            "https://docs.example.com/big",
        )


@pytest.mark.parametrize(
    "url", ["https://docs.example.com:bad/x", "https://[::1/x", "https://[zz::1]/x"]
)
async def test_a_malformed_url_is_refused_not_raised(url: str) -> None:
    with pytest.raises(UrlNotAllowed):
        await check(url, resolve=resolve)
