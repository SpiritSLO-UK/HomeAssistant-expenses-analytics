"""Outbound-request host guard — SSRF mitigation (backlog CR-SEC-3).

The server makes authenticated outbound requests to user-configured endpoints
(the AI base URL, Paperless). A hostile or mistyped URL pointing at an internal
address could turn the server into an SSRF proxy or leak the bearer/API token to
an attacker host.

This module classifies a URL's *resolved* host as public vs private. Callers use
it where a public host is required — e.g. the AI provider in **cloud** modes
(``cloud_manual`` / ``cloud_auto``), where the endpoint must be a real cloud API
and never an internal address. It is deliberately NOT applied to ``local_llm``
(Ollama et al. legitimately live on localhost/LAN) nor to Paperless (a
self-hosted instance is normally on the LAN) — those rely on no-redirects + the
fact that only a trusted settings-manager can set the URL.

Note: this resolves + classifies at call time, which mitigates the common case;
it does not pin the connection IP, so it is not a hard defence against a
fast DNS-rebind attacker (out of scope for an admin-set URL).
"""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse


def ip_is_public(ip: str) -> bool:
    """True if ``ip`` is a normal, globally-routable address (not private,
    loopback, link-local, multicast, reserved or unspecified)."""
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return not (
        addr.is_private
        or addr.is_loopback
        or addr.is_link_local
        or addr.is_multicast
        or addr.is_reserved
        or addr.is_unspecified
    )


def host_is_public(host: str) -> bool:
    """True only if ``host`` resolves and EVERY resolved address is public. An
    unresolvable host returns False (a public endpoint must resolve)."""
    host = (host or "").strip()
    if not host:
        return False
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError:
        return False
    ips = {info[4][0] for info in infos}
    return bool(ips) and all(ip_is_public(ip) for ip in ips)


def url_is_public(url: str) -> bool:
    """True if the URL's host resolves to only public addresses."""
    return host_is_public(urlparse(url).hostname or "")
