from __future__ import annotations

import socket

import pytest

from flow_app.ssrf import SSRF_ERROR_MSG, resolve_webhook_target, validate_webhook_url


def test_resolve_webhook_target_uses_deterministic_dns(monkeypatch):
    def fake_getaddrinfo(host, port, *args, **kwargs):
        assert host == "example.com"
        assert port == 443
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port))]

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)

    resolved_ip, url = resolve_webhook_target("https://example.com/webhook")

    assert resolved_ip == "93.184.216.34"
    assert url == "https://example.com/webhook"


def test_validate_webhook_url_rejects_private_rebound(monkeypatch):
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda host, port, *args, **kwargs: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", port))
        ],
    )

    with pytest.raises(ValueError, match=SSRF_ERROR_MSG):
        validate_webhook_url("https://example.com/webhook")


def test_resolve_webhook_target_checks_all_resolved_ips(monkeypatch):
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda host, port, *args, **kwargs: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.1", port)),
        ],
    )

    with pytest.raises(ValueError, match=SSRF_ERROR_MSG):
        resolve_webhook_target("https://example.com/webhook")
