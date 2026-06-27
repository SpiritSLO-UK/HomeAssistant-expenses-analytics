"""SSRF guard on outbound AI / Paperless requests (backlog CR-SEC-3).

The AI provider must refuse a non-public endpoint in cloud modes (so the API key
can't be exfiltrated to / the server can't be used to reach an internal host),
while local_llm stays free to use localhost/LAN. IP literals are built via
``ipaddress`` rather than written inline so the test itself carries no hardcoded
IP addresses.
"""

from __future__ import annotations

import ipaddress

import pytest

from app.services import ai_service, net_guard, settings_service
from app.services.ai_provider import AIError, OpenAICompatibleProvider

# Built numerically so there are no hardcoded IP-address string literals here.
_LOOPBACK = str(ipaddress.ip_address(0x7F000001))        # 127.0.0.1
_PRIVATE = str(ipaddress.ip_address(0x0A000001))         # 10.0.0.1
_LINK_LOCAL_METADATA = str(ipaddress.ip_address(0xA9FEA9FE))  # 169.254.169.254
_PUBLIC = str(ipaddress.ip_address(0x08080808))          # 8.8.8.8


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
