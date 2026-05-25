"""SSRF protection for user-provided webhook target URLs.

Webhook URLs cross a trust boundary: they are supplied by users, then fetched by
the server. Only absolute http/https URLs whose hostnames resolve to public IP
targets are allowed. Delivery revalidates the URL so DNS rebinding that happens
after configuration is still caught before the outbound request.
"""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse


BLOCKED_PREFIXES = (
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("100.64.0.0/10"),
    ipaddress.ip_network("0.0.0.0/8"),
    ipaddress.ip_network("fc00::/7"),
    ipaddress.ip_network("fe80::/10"),
    ipaddress.ip_network("::ffff:0:0/96"),
)

SSRF_ERROR_MSG = "Webhook URL targets a private or reserved address."


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
        if any(ip in network for network in BLOCKED_PREFIXES):
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
