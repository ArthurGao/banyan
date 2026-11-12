"""A fake DNS for the outbound guard's tests: names, IP literals, and what does not resolve."""

import ipaddress

PUBLIC = "93.184.216.34"
DNS = {
    "docs.example.com": [PUBLIC],
    "split.example.com": [PUBLIC, "10.0.0.7"],  # one answer internal: refused
    "rebind.example.com": ["127.0.0.1"],
    "v6.example.com": ["2606:4700::1111"],
}


async def resolve(host: str, port: int) -> list[str]:
    if host in DNS:
        return DNS[host]
    try:  # an IP literal resolves to itself
        return [str(ipaddress.ip_address(host))]
    except ValueError:
        raise OSError("no such host") from None
