"""SSRF protection for user-provided webhook target URLs.

Webhook URLs cross a trust boundary: they are supplied by users, then fetched by
the server. Only absolute http/https URLs whose hostnames resolve to global
unicast (public) IP addresses are allowed. The check uses ipaddress.is_global
plus explicit guards for IPv4-mapped IPv6 and the zero network. Delivery
revalidates and pins the resolved IP in the HTTP transport so DNS rebinding is
blocked while HTTPS SNI and certificate verification still use the original
hostname.
"""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse


SSRF_ERROR_MSG = "Webhook URL targets a private or reserved address."


def _is_public_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """Check that an IP is a globally routable address (public IP)."""
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        return False
    if ip in ipaddress.ip_network("0.0.0.0/8"):
        return False
    if ip.is_multicast:
        return False
    return ip.is_global


def resolve_webhook_target(url: str) -> tuple[str, str]:
    """Resolve and validate a webhook URL, returning the pinned target IP."""
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("Webhook URL must use http or https scheme.")

    hostname = parsed.hostname
    if not hostname:
        raise ValueError("Webhook URL must have a valid hostname.")

    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    try:
        addrinfos = socket.getaddrinfo(hostname, port, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise ValueError(f"Could not resolve hostname: {hostname}") from exc
    if not addrinfos:
        raise ValueError(f"Could not resolve hostname: {hostname}")

    resolved_ips = [ipaddress.ip_address(addrinfo[4][0]) for addrinfo in addrinfos]
    for ip in resolved_ips:
        if not _is_public_ip(ip):
            raise ValueError(SSRF_ERROR_MSG)

    return str(resolved_ips[0]), url


def validate_webhook_url(url: str) -> str:
    """Validate that a webhook URL is safe to fetch from the server."""
    _, url = resolve_webhook_target(url)
    return url


def is_safe_webhook_target(url: str) -> bool:
    """Return True when the URL passes webhook SSRF validation."""
    try:
        resolve_webhook_target(url)
    except ValueError:
        return False
    return True
