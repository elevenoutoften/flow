from __future__ import annotations

import socket

import pytest

from flow_app.ssrf import SSRF_ERROR_MSG, resolve_webhook_target, validate_webhook_url


def _addrinfo(ip: str, port: int):
    family = socket.AF_INET6 if ":" in ip else socket.AF_INET
    sockaddr = (ip, port, 0, 0) if family == socket.AF_INET6 else (ip, port)
    return (family, socket.SOCK_STREAM, 6, "", sockaddr)


def _stub_dns(monkeypatch, *ips: str):
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda host, port, *args, **kwargs: [_addrinfo(ip, port) for ip in ips],
    )


def _assert_rejected_ip(monkeypatch, ip: str):
    _stub_dns(monkeypatch, ip)

    with pytest.raises(ValueError, match=SSRF_ERROR_MSG):
        resolve_webhook_target("https://example.com/webhook")


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


def test_reject_ipv6_unspecified(monkeypatch):
    _assert_rejected_ip(monkeypatch, "::")


def test_reject_ipv6_loopback(monkeypatch):
    _assert_rejected_ip(monkeypatch, "::1")


def test_reject_ipv4_loopback(monkeypatch):
    _assert_rejected_ip(monkeypatch, "127.0.0.1")


def test_reject_ipv4_private_10(monkeypatch):
    _assert_rejected_ip(monkeypatch, "10.0.0.1")


def test_reject_ipv4_private_172(monkeypatch):
    _assert_rejected_ip(monkeypatch, "172.16.0.1")


def test_reject_ipv4_private_192(monkeypatch):
    _assert_rejected_ip(monkeypatch, "192.168.1.1")


def test_reject_link_local(monkeypatch):
    _assert_rejected_ip(monkeypatch, "169.254.1.1")
    _assert_rejected_ip(monkeypatch, "fe80::1")


def test_reject_carrier_grade_nat(monkeypatch):
    _assert_rejected_ip(monkeypatch, "100.64.0.1")


def test_reject_ipv6_unique_local(monkeypatch):
    _assert_rejected_ip(monkeypatch, "fc00::1")


def test_reject_ipv4_mapped_ipv6(monkeypatch):
    _assert_rejected_ip(monkeypatch, "::ffff:93.184.216.34")


def test_reject_ipv4_zero_network(monkeypatch):
    _assert_rejected_ip(monkeypatch, "0.0.0.1")


def test_reject_ipv4_multicast(monkeypatch):
    _assert_rejected_ip(monkeypatch, "224.0.0.1")


def test_reject_ipv4_reserved(monkeypatch):
    _assert_rejected_ip(monkeypatch, "240.0.0.1")


def test_reject_ipv4_documentation(monkeypatch):
    _assert_rejected_ip(monkeypatch, "192.0.2.1")


def test_reject_ipv6_multicast(monkeypatch):
    _assert_rejected_ip(monkeypatch, "ff02::1")


def test_accept_valid_public_ipv4(monkeypatch):
    _stub_dns(monkeypatch, "93.184.216.34")

    resolved_ip, url = resolve_webhook_target("https://example.com/webhook")

    assert resolved_ip == "93.184.216.34"
    assert url == "https://example.com/webhook"


def test_accept_valid_public_ipv6(monkeypatch):
    _stub_dns(monkeypatch, "2606:4700:3037::ac43:b342")

    resolved_ip, url = resolve_webhook_target("https://example.com/webhook")

    assert resolved_ip == "2606:4700:3037::ac43:b342"
    assert url == "https://example.com/webhook"


def test_reject_second_private_ip_in_multi_resolve(monkeypatch):
    _stub_dns(monkeypatch, "93.184.216.34", "10.0.0.1")

    with pytest.raises(ValueError, match=SSRF_ERROR_MSG):
        resolve_webhook_target("https://example.com/webhook")


def test_validate_webhook_url_valid_public(monkeypatch):
    _stub_dns(monkeypatch, "93.184.216.34")

    assert validate_webhook_url("https://example.com/webhook") == "https://example.com/webhook"
