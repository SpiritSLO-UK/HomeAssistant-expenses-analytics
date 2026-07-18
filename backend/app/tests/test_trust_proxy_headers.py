"""Automated release-check A3 (#370): HAFI_TRUST_PROXY_HEADERS spoofing.

The ``trust_proxy_headers`` flag (env ``HAFI_TRUST_PROXY_HEADERS``, default True)
controls whether the app trusts the inbound ``X-Remote-User-*`` identity headers.
The app reads it as ``settings.trust_proxy_headers`` in
``auth_service._identity_from_request``, so the tests toggle it by monkeypatching
that attribute (matching the existing hardening tests).

This was previously only a manual pre-release check. These tests pin the contract
so a regression that starts trusting forged headers in the standalone hardening
mode is caught in CI:

* flag OFF -> a spoofed ``X-Remote-User-*`` header is IGNORED; the request
  resolves to the single local owner, never the spoofed "attacker" identity;
* flag ON (default) -> the forwarded HA identity IS honoured (clear contrast);
* flag OFF + NO header -> still the single local owner (unchanged fallback).

``/api/users/me`` (MeOut) has no external_id, so the local-vs-spoofed identity is
asserted via display_name there and via external_id on the /api/users list.
"""

from __future__ import annotations

from app.services import auth_service

# The display_name the local fallback assigns when headers are ignored/absent
# (auth_service._identity_from_request). Kept in sync with that literal.
_LOCAL_DISPLAY = "Local User"


def _hdr(uid: str, name: str | None = None) -> dict[str, str]:
    return {"X-Remote-User-Id": uid, "X-Remote-User-Display-Name": name or uid}


def test_flag_off_ignores_spoofed_identity_resolves_local_owner(client, monkeypatch):
    """Flag OFF: a request carrying a spoofed X-Remote-User-* header resolves to the
    LOCAL OWNER, not the forged 'attacker' identity."""
    monkeypatch.setattr(auth_service.settings, "trust_proxy_headers", False)

    me = client.get("/api/users/me", headers=_hdr("ha-attacker", "Attacker")).json()

    # Identity is the local owner, not anything the spoofed header asserted.
    assert me["display_name"] == _LOCAL_DISPLAY
    assert me["display_name"] != "Attacker"
    assert me["role"] == "owner" and me["status"] == "approved"

    # The forged identity created no user: only the single local owner exists, and
    # its external_id is the local fallback, never the spoofed "ha-attacker".
    users = client.get("/api/users", headers=_hdr("ha-attacker")).json()
    assert [u["external_id"] for u in users] == [auth_service.LOCAL_EXTERNAL_ID]


def test_flag_on_default_honours_forwarded_identity(client):
    """Flag ON (default): the X-Remote-User-* header IS honoured, so a forwarded HA
    identity maps to its own distinct user, the contrast to the OFF case."""
    assert auth_service.settings.trust_proxy_headers is True

    client.get("/api/users/me")  # local owner bootstraps first
    alice = client.get("/api/users/me", headers=_hdr("ha-alice", "Alice")).json()

    # The header identity is reflected back, as its own (pending) member row.
    assert alice["display_name"] == "Alice"
    assert alice["role"] == "member" and alice["status"] == "pending"

    # Its external_id on the users list is the forwarded value, not the local owner.
    users = client.get("/api/users").json()  # the local owner may list users
    assert "ha-alice" in [u["external_id"] for u in users]


def test_flag_off_without_header_still_local_owner(client, monkeypatch):
    """Edge: flag OFF with NO identity header behaves exactly as before, the single
    local owner. Turning the flag off never changes the standalone (headerless) path."""
    monkeypatch.setattr(auth_service.settings, "trust_proxy_headers", False)

    me = client.get("/api/users/me").json()
    assert me["display_name"] == _LOCAL_DISPLAY
    assert me["role"] == "owner" and me["status"] == "approved"

    # A later spoofed request maps to the SAME local owner, not a new user.
    me2 = client.get("/api/users/me", headers=_hdr("ha-someone-else")).json()
    assert me2["id"] == me["id"]
    users = client.get("/api/users").json()
    assert [u["external_id"] for u in users] == [auth_service.LOCAL_EXTERNAL_ID]
