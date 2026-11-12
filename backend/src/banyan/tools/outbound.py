"""Requests to URLs a model or a user supplied (`web_fetch`): no SSRF (design §7.3).

Ported from llm-steward's `outbound.py` (steward design §11 P5c), on httpx. Only public internet
addresses are reached. The host is resolved here and every address it resolves to must be global
(no private, loopback, link-local such as cloud metadata, CGNAT, multicast, reserved or unspecified
ranges, and no IPv6 form that embeds an IPv4 address). The connection then goes to the address
that was checked, with the hostname kept for TLS (SNI) and the `Host` header, so DNS cannot answer
differently between the check and the connection (rebinding). Every redirect is checked the same
way; https only. `allow_private` (BANYAN_ALLOW_PRIVATE_URLS, local tests) lifts the address and
scheme rules, never the size or redirect limits.
"""

import asyncio
import functools
import ipaddress
import socket
import ssl
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Literal
from urllib.parse import SplitResult, urljoin, urlsplit, urlunsplit

import httpx

Resolver = Callable[[str, int], Awaitable[list[str]]]
REDIRECTS = 3
TIMEOUT_S = 15.0

UrlRefusal = Literal["form", "unresolved", "not_public", "redirects"]


class UrlNotAllowed(Exception):
    def __init__(self, detail: str, reason: UrlRefusal) -> None:
        super().__init__(detail)
        self.reason = reason


class TooLarge(Exception):
    """The response body is over the byte limit."""


@dataclass(frozen=True)
class Fetched:
    status_code: int
    headers: Mapping[str, str]
    body: bytes
    url: str  # after redirects


async def system_resolve(host: str, port: int) -> list[str]:
    infos = await asyncio.get_running_loop().getaddrinfo(host, port, type=socket.SOCK_STREAM)
    return list(dict.fromkeys(str(info[4][0]) for info in infos))


# IPv6 ranges that carry an IPv4 address a translator or tunnel would reach; refused outright
# (Python counts NAT64 and IPv4-compatible addresses as global, whatever IPv4 they embed).
_EMBEDS_IPV4 = tuple(
    ipaddress.ip_network(n)
    for n in (
        "::/96",  # IPv4-compatible (deprecated)
        "::ffff:0:0/96",  # IPv4-mapped
        "64:ff9b::/96",  # NAT64
        "64:ff9b:1::/48",  # NAT64, local use
        "2002::/16",  # 6to4
        "2001::/32",  # Teredo
    )
)


def _public(address: str) -> bool:
    ip = ipaddress.ip_address(address.split("%", 1)[0])  # drop an IPv6 zone
    if isinstance(ip, ipaddress.IPv6Address) and any(ip in net for net in _EMBEDS_IPV4):
        return False
    return ip.is_global and not ip.is_multicast


def check_form(url: str, *, allow_private: bool = False) -> SplitResult:
    """The checks that need no network: an http(s) URL (https only unless `allow_private`) with
    a host and no credentials."""
    try:
        parts = urlsplit(url)
        _ = parts.port  # raises on a malformed port
        host = parts.hostname
    except ValueError as exc:  # a malformed port or IPv6 literal: a refusal, not a crash
        raise UrlNotAllowed("the URL is malformed", "form") from exc
    scheme = parts.scheme.lower()
    if scheme not in ("https", "http") or not host:
        raise UrlNotAllowed("only http(s) URLs with a host can be requested", "form")
    if scheme != "https" and not allow_private:
        raise UrlNotAllowed("only https URLs can be requested", "form")
    if parts.username or parts.password:
        raise UrlNotAllowed("URLs with credentials are not requested", "form")
    return parts


@dataclass(frozen=True)
class _Target:
    url: str  # what the connection goes to: the checked address
    host: str
    host_header: str


async def check(
    url: str, *, allow_private: bool = False, resolve: Resolver = system_resolve
) -> _Target:
    """Raise `UrlNotAllowed` unless `url` may be requested; the address to connect to."""
    parts = check_form(url, allow_private=allow_private)
    scheme, host = parts.scheme.lower(), parts.hostname or ""
    port = parts.port or (443 if scheme == "https" else 80)
    try:
        addresses = await resolve(host, port)
    except OSError as exc:
        raise UrlNotAllowed(f"{host} does not resolve", "unresolved") from exc
    if not addresses:
        raise UrlNotAllowed(f"{host} does not resolve", "unresolved")
    if not allow_private and not all(_public(a) for a in addresses):
        raise UrlNotAllowed(f"{host} is not a public internet address", "not_public")
    address = addresses[0]
    literal = f"[{address}]" if ":" in address else address
    netloc = literal if parts.port is None else f"{literal}:{parts.port}"
    pinned = urlunsplit((scheme, netloc, parts.path or "/", parts.query, ""))
    host_header = host if parts.port is None else f"{host}:{parts.port}"
    return _Target(pinned, host, host_header)


@functools.cache
def tls() -> ssl.SSLContext:
    """One TLS context (loading the CA bundle costs milliseconds) for every client below."""
    return httpx.create_ssl_context()


def client(transport: httpx.AsyncBaseTransport | None = None) -> httpx.AsyncClient:
    """No redirects followed by the client itself, no proxies from the environment, no connection
    reuse (a pinned connection belongs to one hostname)."""
    return httpx.AsyncClient(
        transport=transport,
        verify=tls(),
        follow_redirects=False,
        trust_env=False,
        timeout=TIMEOUT_S,
        limits=httpx.Limits(max_keepalive_connections=0),
    )


async def get(
    http: httpx.AsyncClient,
    url: str,
    *,
    max_bytes: int,
    headers: Mapping[str, str] | None = None,
    allow_private: bool = False,
    resolve: Resolver = system_resolve,
) -> Fetched:
    """One GET through the guard, following up to REDIRECTS checked redirects."""
    for _ in range(REDIRECTS + 1):
        target = await check(url, allow_private=allow_private, resolve=resolve)
        sent = {**(headers or {}), "Host": target.host_header}
        async with http.stream(
            "GET", target.url, headers=sent, extensions={"sni_hostname": target.host}
        ) as response:
            location = response.headers.get("location")
            if response.is_redirect and location:
                url = urljoin(url, location)
                continue
            body = bytearray()
            async for chunk in response.aiter_bytes():
                body += chunk
                if len(body) > max_bytes:
                    raise TooLarge(f"the response is over the {max_bytes} byte limit")
            return Fetched(response.status_code, dict(response.headers), bytes(body), url)
    raise UrlNotAllowed(f"more than {REDIRECTS} redirects", "redirects")
