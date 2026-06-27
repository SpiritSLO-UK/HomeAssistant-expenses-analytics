"""Disabled/pending account access at the auth chokepoint (SR-6).

A disabled account must keep NO access beyond seeing its own status — in particular a
disabled *owner* must not be able to use their retained admin role (e.g. to reach the
gate-exempt /api/security). Pending accounts are unaffected (they keep the minimal
onboarding access they had before).
"""

from __future__ import annotations


def _hdr(uid: str, name: str) -> dict:
    return {"X-Remote-User-Id": uid, "X-Remote-User-Display-Name": name}


def _make_second_owner(client) -> int:
    """Bootstrap the local owner, add Bob, and promote him to a second approved owner
    (so he can later be disabled without tripping the last-owner guard)."""
    client.get("/api/users/me")  # headerless → local owner (owner, approved)
    client.get("/api/users/me", headers=_hdr("ha-bob", "Bob"))  # Bob appears (pending)
    bob = next(u for u in client.get("/api/users").json() if u["external_id"] == "ha-bob")
    client.patch(f"/api/users/{bob['id']}", json={"role": "owner", "status": "approved"})
    return bob["id"]


def test_disabled_owner_loses_all_access_but_sees_own_status(client):
    bob_id = _make_second_owner(client)
    bob = _hdr("ha-bob", "Bob")

    # Approved owner Bob can act + reach the (gate-exempt) security status.
    assert client.get("/api/users", headers=bob).status_code == 200
    assert client.get("/api/security/status", headers=bob).status_code == 200

    # The local owner disables Bob (not self, not the last owner → allowed).
    assert client.patch(f"/api/users/{bob_id}", json={"status": "disabled"}).status_code == 200

    # Disabled: owner route blocked, AND the gate-exempt /api/security is now blocked
    # too (the bypass SR-6 closes) — a disabled owner can't manage the system.
    assert client.get("/api/users", headers=bob).status_code == 403
    assert client.get("/api/security/status", headers=bob).status_code == 403
    assert client.get("/api/transactions", headers=bob).status_code == 403

    # ...but can still see their own status, so the UI can show "account disabled".
    me = client.get("/api/users/me", headers=bob)
    assert me.status_code == 200 and me.json()["status"] == "disabled"


def test_re_enabling_restores_access(client):
    bob_id = _make_second_owner(client)
    bob = _hdr("ha-bob", "Bob")
    client.patch(f"/api/users/{bob_id}", json={"status": "disabled"})
    assert client.get("/api/users", headers=bob).status_code == 403
    # Re-approve → the retained owner role grants access again.
    client.patch(f"/api/users/{bob_id}", json={"status": "approved"})
    assert client.get("/api/users", headers=bob).status_code == 200


def test_pending_user_access_unchanged(client):
    client.get("/api/users/me")  # local owner
    zoe = _hdr("ha-zoe", "Zoe")
    client.get("/api/users/me", headers=zoe)  # Zoe appears → pending

    # Pending stays blocked from data (awaiting approval)...
    blocked = client.get("/api/transactions", headers=zoe)
    assert blocked.status_code == 403
    # ...but still learns it's pending via /users/me (onboarding path intact).
    me = client.get("/api/users/me", headers=zoe)
    assert me.status_code == 200 and me.json()["status"] == "pending"
