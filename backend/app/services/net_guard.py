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

DNS rebinding
-------------
Resolving the host and then letting the HTTP client resolve it *again* at connect
time is a TOCTOU gap: a fast-rebind attacker can answer "public" during the check
and "private/loopback" at connect. To pin against this, resolve the host once,
verify EVERY resolved address is public, and connect to that validated address
(sending the original Host header / TLS SNI so the name is never re-resolved).
``resolve_pinned_ip`` / ``pinned_ip_for_url`` return that single validated address
for a caller that pins its connection; ``host_is_public`` / ``url_is_public`` are
the boolean equivalents built on the same single resolution.
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


def _resolve_ips(host: str) -> list[str]:
    """Resolve ``host`` to its A/AAAA addresses (one ``getaddrinfo`` call, so a
    single point of resolution). Returns [] for an empty or unresolvable host."""
    host = (host or "").strip()
    if not host:
        return []
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError:
        return []
    # Preserve order (prefer the first resolved address as the pin target) while
    # de-duplicating the addresses returned across families.
    seen: dict[str, None] = {}
    for info in infos:
        seen.setdefault(info[4][0], None)
    return list(seen)


def resolve_pinned_ip(host: str) -> str | None:
    """Resolve ``host`` once and return a single address to pin the connection to.

    Returns that address only if the host resolves AND **every** resolved address
    is public; otherwise ``None``. Validating every address (not just the one the
    client happens to pick) closes the DNS-rebind window: the caller connects to
    the returned IP with the original Host header / TLS SNI, so the hostname is
    never re-resolved between the check and the connect.
    """
    ips = _resolve_ips(host)
    if not ips or not all(ip_is_public(ip) for ip in ips):
        return None
    return ips[0]


def pinned_ip_for_url(url: str) -> str | None:
    """URL-level counterpart of :func:`resolve_pinned_ip` — the validated address
    to pin the connection to, or ``None`` if the URL's host is not fully public."""
    return resolve_pinned_ip(urlparse(url).hostname or "")


def host_is_public(host: str) -> bool:
    """True only if ``host`` resolves and EVERY resolved address is public. An
    unresolvable host returns False (a public endpoint must resolve)."""
    return resolve_pinned_ip(host) is not None


def url_is_public(url: str) -> bool:
    """True if the URL's host resolves to only public addresses."""
    return pinned_ip_for_url(url) is not None
