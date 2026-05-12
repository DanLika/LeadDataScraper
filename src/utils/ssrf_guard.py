"""SSRF guard for outbound HTTP fetches.

Wraps aiohttp's DNS resolver to reject hostnames that resolve to
private / loopback / link-local / reserved / multicast IPs. Because every
new connection (including HTTP redirects) goes through the connector's
resolver, this also defends against DNS-rebinding and redirect-based
bypass attempts.
"""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from urllib.parse import urlparse

from aiohttp.resolver import DefaultResolver


class SSRFError(ValueError):
    """Raised when a URL is rejected by the SSRF guard."""


_BLOCKED_HOSTS = {
    "metadata.google.internal",
    "metadata.goog",
    "metadata",
    "instance-data",
    "instance-data.ec2.internal",
}


def assert_safe_scheme(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise SSRFError(f"Blocked URL scheme: {parsed.scheme!r}")
    if not parsed.hostname:
        raise SSRFError("URL has no host")
    if parsed.hostname.lower() in _BLOCKED_HOSTS:
        raise SSRFError(f"Blocked hostname: {parsed.hostname}")
    try:
        ip = ipaddress.ip_address(parsed.hostname)
    except ValueError:
        return
    _assert_public_ip(ip, parsed.hostname)


def _assert_public_ip(ip: ipaddress._BaseAddress, host: str) -> None:
    if (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    ):
        raise SSRFError(f"Blocked non-public IP {ip} for host {host!r}")


async def assert_safe_url(url: str) -> None:
    """Full check: scheme + DNS-resolved IPs. Use before Playwright / urllib calls
    that don't go through SSRFGuardResolver."""
    assert_safe_scheme(url)
    host = urlparse(url).hostname
    if not host:
        raise SSRFError("URL has no host")
    try:
        ipaddress.ip_address(host)
        return
    except ValueError:
        pass
    loop = asyncio.get_event_loop()
    try:
        infos = await loop.getaddrinfo(host, None)
    except socket.gaierror as e:
        raise SSRFError(f"DNS resolution failed for {host!r}: {e}") from e
    for info in infos:
        ip_str = info[4][0]
        try:
            ip = ipaddress.ip_address(ip_str)
        except ValueError:
            continue
        _assert_public_ip(ip, host)


class SSRFGuardResolver(DefaultResolver):
    """aiohttp resolver that rejects non-public DNS results."""

    async def resolve(self, host: str, port: int = 0, family: int = socket.AF_INET):
        if host.lower() in _BLOCKED_HOSTS:
            raise SSRFError(f"Blocked hostname: {host}")
        results = await super().resolve(host, port, family)
        for r in results:
            try:
                ip = ipaddress.ip_address(r["host"])
            except ValueError:
                continue
            _assert_public_ip(ip, host)
        return results
