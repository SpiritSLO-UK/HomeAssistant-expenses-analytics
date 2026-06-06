"""Settings RBAC (backlog #28): the general Settings (and tab customisation) are
gated to the owner or a member the owner has granted 'manage settings'."""

from __future__ import annotations


def _hdr(uid: str, name: str | None = None) -> dict[str, str]:
    return {"X-Remote-User-Id": uid, "X-Remote-User-Display-Name": name or uid}


def _approved_member(client, uid: str, name: str) -> int:
    """Make `uid` appear (after the owner already exists) and approve them as a member."""
    client.get("/api/users/me", headers=_hdr(uid, name))
    mid = next(u["id"] for u in client.get("/api/users").json() if u["external_id"] == uid)
    client.patch(f"/api/users/{mid}", json={"role": "member", "status": "approved"})
    return mid


def test_owner_can_manage_settings(client):
    client.get("/api/users/me")  # headerless → the local owner
    assert client.get("/api/users/me").json()["can_manage_settings"] is True
    assert client.put("/api/settings", json={"receipt_match_mode": "suggest"}).status_code == 200


def test_member_blocked_until_granted_then_revoked(client):
    client.get("/api/users/me")  # establish the owner first
    bob = _approved_member(client, "ha-bob", "Bob")
    h = _hdr("ha-bob", "Bob")

    # A member without the grant cannot modify settings.
    assert client.put("/api/settings", json={"receipt_match_mode": "auto"}, headers=h).status_code == 403
    assert client.get("/api/users/me", headers=h).json()["can_manage_settings"] is False

    # The owner grants Bob → he can now manage settings.
    client.patch(f"/api/users/{bob}", json={"can_manage_settings": True})
    assert client.get("/api/users/me", headers=h).json()["can_manage_settings"] is True
    assert client.put("/api/settings", json={"receipt_match_mode": "suggest"}, headers=h).status_code == 200

    # ...and the owner can revoke it again.
    client.patch(f"/api/users/{bob}", json={"can_manage_settings": False})
    assert client.put("/api/settings", json={"receipt_match_mode": "auto"}, headers=h).status_code == 403


def test_user_list_exposes_the_grant(client):
    client.get("/api/users/me")
    bob = _approved_member(client, "ha-bob", "Bob")
    client.patch(f"/api/users/{bob}", json={"can_manage_settings": True})
    users = {u["external_id"]: u for u in client.get("/api/users").json()}
    assert users["ha-bob"]["can_manage_settings"] is True


def test_stats_endpoint_manager_gated_and_shaped(client):
    """Settings stats card (DB size + AI tallies): manager-gated, sane shape."""
    client.get("/api/users/me")  # the local owner (a settings-manager)
    r = client.get("/api/settings/stats")
    assert r.status_code == 200
    body = r.json()
    assert isinstance(body["database_bytes"], int) and body["database_bytes"] >= 0
    for key in ("transactions", "statements", "receipts", "ai_total", "ai_cloud", "ai_local"):
        assert key in body

    # A plain approved member without the grant is blocked.
    _approved_member(client, "ha-bob", "Bob")
    assert client.get("/api/settings/stats", headers=_hdr("ha-bob", "Bob")).status_code == 403
