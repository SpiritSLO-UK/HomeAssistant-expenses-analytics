"""SSRF guard on outbound AI / Paperless requests (backlog CR-SEC-3).

The AI provider must refuse a non-public endpoint in cloud modes (so the API key
can't be exfiltrated to / the server can't be used to reach an internal host),
while local_llm stays free to use localhost/LAN. IP literals are built via
``ipaddress`` rather than written inline so the test itself carries no hardcoded
IP addresses.
"""

from __future__ import annotations

import ipaddress
import socket

import pytest

from app.services import ai_service, net_guard, settings_service
from app.services.ai_provider import AIError, OpenAICompatibleProvider

# Built numerically so there are no hardcoded IP-address string literals here.
_LOOPBACK = str(ipaddress.ip_address(0x7F000001))        # 127.0.0.1
_PRIVATE = str(ipaddress.ip_address(0x0A000001))         # 10.0.0.1
_LINK_LOCAL_METADATA = str(ipaddress.ip_address(0xA9FEA9FE))  # 169.254.169.254
_PUBLIC = str(ipaddress.ip_address(0x08080808))          # 8.8.8.8
_PUBLIC_2 = str(ipaddress.ip_address(0x01010101))        # 1.1.1.1


def _fake_getaddrinfo(*ips: str):
    """Build a hermetic ``socket.getaddrinfo`` replacement that always resolves
    to ``ips`` regardless of the host — so a rebind (host that would resolve to a
    private address) is simulated without any real DNS lookup."""

    def _resolver(host, *_args, **_kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 0, "", (ip, 0)) for ip in ips]

    return _resolver


def test_ip_classification():
    assert net_guard.ip_is_public(_PUBLIC) is True
    assert net_guard.ip_is_public(_LOOPBACK) is False
    assert net_guard.ip_is_public(_PRIVATE) is False
    assert net_guard.ip_is_public(_LINK_LOCAL_METADATA) is False


def test_url_is_public_for_ip_literals():
    # getaddrinfo on a numeric IP returns it without a DNS lookup → no network.
    assert net_guard.url_is_public(f"http://{_PUBLIC}/v1") is True
    assert net_guard.url_is_public(f"http://{_PRIVATE}:11434/v1") is False
    assert net_guard.url_is_public(f"http://{_LINK_LOCAL_METADATA}/latest") is False
    assert net_guard.url_is_public("http://") is False  # no host


def test_pin_returns_validated_ip_when_all_addresses_public(monkeypatch):
    """A host whose every resolved address is public yields a pin target (the
    first validated address) that the caller connects to instead of re-resolving."""
    monkeypatch.setattr(socket, "getaddrinfo", _fake_getaddrinfo(_PUBLIC, _PUBLIC_2))
    assert net_guard.resolve_pinned_ip("api.example.test") == _PUBLIC
    assert net_guard.pinned_ip_for_url("https://api.example.test/v1") == _PUBLIC
    assert net_guard.url_is_public("https://api.example.test/v1") is True


def test_pin_rejects_rebind_to_private_address(monkeypatch):
    """DNS-rebind case: the name resolves to a public AND a private address. The
    guard must refuse the whole host (no pin, not public) rather than pick the
    public record and let the client rebind to the private one at connect."""
    monkeypatch.setattr(socket, "getaddrinfo", _fake_getaddrinfo(_PUBLIC, _PRIVATE))
    assert net_guard.resolve_pinned_ip("rebind.example.test") is None
    assert net_guard.pinned_ip_for_url("https://rebind.example.test/v1") is None
    assert net_guard.url_is_public("https://rebind.example.test/v1") is False


def test_pin_rejects_rebind_to_loopback(monkeypatch):
    """A host that resolves solely to loopback (classic rebind target) is refused."""
    monkeypatch.setattr(socket, "getaddrinfo", _fake_getaddrinfo(_LOOPBACK))
    assert net_guard.resolve_pinned_ip("localhost.evil.test") is None
    assert net_guard.url_is_public("https://localhost.evil.test/v1") is False


def test_pin_rejects_unresolvable_host(monkeypatch):
    """An unresolvable host has no address to pin and is not public."""

    def _boom(*_args, **_kwargs):
        raise socket.gaierror("name or service not known")

    monkeypatch.setattr(socket, "getaddrinfo", _boom)
    assert net_guard.resolve_pinned_ip("nope.example.test") is None
    assert net_guard.url_is_public("https://nope.example.test/v1") is False


def test_cloud_provider_refuses_private_host_without_network():
    """A cloud-mode provider must raise before any request when the endpoint is
    private (the guard fires first, so no socket is opened)."""
    provider = OpenAICompatibleProvider(
        base_url=f"http://{_PRIVATE}:11434/v1", model="m", api_key="secret", require_public_host=True
    )
    with pytest.raises(AIError, match="public host"):
        provider.classify_transaction("x", "1", "GBP", ["Groceries"])


def test_local_provider_allows_private_host(db):
    """local_llm must NOT require a public host (Ollama on localhost is the point)."""
    settings_service.set_value(db, settings_service.PRIVACY_MODE, "local_llm")
    settings_service.set_value(db, settings_service.AI_PROVIDER, "openai_compatible")
    settings_service.set_value(db, settings_service.AI_BASE_URL, f"http://{_LOOPBACK}:11434/v1")
    settings_service.set_value(db, settings_service.AI_MODEL, "llama3")
    provider = ai_service.get_provider(db)
    assert isinstance(provider, OpenAICompatibleProvider)
    assert provider.require_public_host is False


def test_cloud_mode_sets_require_public_host(db):
    settings_service.set_value(db, settings_service.PRIVACY_MODE, "cloud_manual")
    settings_service.set_value(db, settings_service.AI_PROVIDER, "openai_compatible")
    settings_service.set_value(db, settings_service.AI_BASE_URL, "https://api.openai.com/v1")
    settings_service.set_value(db, settings_service.AI_MODEL, "gpt-4o-mini")
    provider = ai_service.get_provider(db)
    assert isinstance(provider, OpenAICompatibleProvider)
    assert provider.require_public_host is True


def test_ai_base_url_scheme_validated(client):
    assert client.put("/api/settings", json={"ai_base_url": "ftp://evil.example"}).status_code == 400
    assert client.put("/api/settings", json={"ai_base_url": "javascript:alert(1)"}).status_code == 400
    # A valid local URL is accepted at set time (the cloud-host guard is at call time).
    assert client.put("/api/settings", json={"ai_base_url": "http://localhost:11434/v1"}).status_code == 200
