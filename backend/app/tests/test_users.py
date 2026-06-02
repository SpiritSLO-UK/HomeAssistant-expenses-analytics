"""Identity, RBAC and the new-user approval flow (Stage 12; backlog #82/#126/#74).

Identity is simulated with the HA ingress headers the middleware reads. With no
header the request resolves to the local single-user owner (legacy behaviour),
which is why the rest of the test suite is unaffected.
"""

from __future__ import annotations


def _hdr(uid: str, name: str | None = None) -> dict[str, str]:
    return {"X-Remote-User-Id": uid, "X-Remote-User-Display-Name": name or uid}


def test_default_request_is_local_owner(client):
    me = client.get("/api/users/me").json()
    assert me["role"] == "owner"
    assert me["status"] == "approved"
    assert me["is_admin"] is True
    assert me["can_write"] is True


def test_second_user_appears_pending_and_is_blocked(client):
    # First request (no header) becomes the owner; a new HA user is pending.
    client.get("/api/users/me")
    alice = client.get("/api/users/me", headers=_hdr("ha-alice", "Alice")).json()
    assert alice["role"] == "member"
    assert alice["status"] == "pending"
    assert alice["can_write"] is False

    # A pending user cannot reach data APIs…
    blocked = client.get("/api/transactions", headers=_hdr("ha-alice", "Alice"))
    assert blocked.status_code == 403
    assert blocked.json()["account_status"] == "pending"
    # …but /me stays reachable so they can see *why*.
    assert client.get("/api/users/me", headers=_hdr("ha-alice", "Alice")).status_code == 200


def test_owner_approves_user(client):
    client.get("/api/users/me")  # owner bootstraps
    client.get("/api/users/me", headers=_hdr("ha-alice", "Alice"))  # alice -> pending

    users = client.get("/api/users").json()
    alice_id = next(u["id"] for u in users if u["external_id"] == "ha-alice")
    assert client.get("/api/transactions", headers=_hdr("ha-alice", "Alice")).status_code == 403

    approved = client.post(f"/api/users/{alice_id}/approve").json()
    assert approved["status"] == "approved"
    assert client.get("/api/transactions", headers=_hdr("ha-alice", "Alice")).status_code == 200


def test_viewer_is_read_only(client):
    client.get("/api/users/me")
    client.get("/api/users/me", headers=_hdr("ha-bob", "Bob"))
    bob_id = next(u["id"] for u in client.get("/api/users").json() if u["external_id"] == "ha-bob")
    client.patch(f"/api/users/{bob_id}", json={"role": "viewer", "status": "approved"})

    # Reads allowed, writes rejected before reaching the route.
    assert client.get("/api/transactions", headers=_hdr("ha-bob", "Bob")).status_code == 200
    write = client.post("/api/tags", json={"name": "nope"}, headers=_hdr("ha-bob", "Bob"))
    assert write.status_code == 403
    assert "read-only" in write.json()["detail"].lower()


def test_non_owner_cannot_manage_users(client):
    client.get("/api/users/me")
    client.get("/api/users/me", headers=_hdr("ha-dave", "Dave"))
    dave_id = next(u["id"] for u in client.get("/api/users").json() if u["external_id"] == "ha-dave")
    client.patch(f"/api/users/{dave_id}", json={"role": "member", "status": "approved"})

    assert client.get("/api/users", headers=_hdr("ha-dave", "Dave")).status_code == 403


def test_cannot_strip_the_last_owner(client):
    me = client.get("/api/users/me").json()
    owner_id = me["id"]

    demote = client.patch(f"/api/users/{owner_id}", json={"role": "member"})
    assert demote.status_code == 400
    assert "last active owner" in demote.json()["detail"].lower()

    disable = client.patch(f"/api/users/{owner_id}", json={"status": "disabled"})
    assert disable.status_code == 400

    delete = client.delete(f"/api/users/{owner_id}")
    assert delete.status_code == 400


def test_second_owner_allows_demoting_the_first(client):
    me = client.get("/api/users/me").json()
    client.get("/api/users/me", headers=_hdr("ha-eve", "Eve"))
    eve_id = next(u["id"] for u in client.get("/api/users").json() if u["external_id"] == "ha-eve")
    # Promote Eve to a second approved owner…
    client.patch(f"/api/users/{eve_id}", json={"role": "owner", "status": "approved"})
    # …now the original owner can be demoted.
    assert client.patch(f"/api/users/{me['id']}", json={"role": "member"}).status_code == 200


def test_cannot_lock_yourself_out(client):
    """Self-protection (#28): you can't disable or delete your OWN account, even
    once another owner exists. Handing over ownership stays possible — you may
    step *down* (demote your own role) after promoting someone else, and only an
    owner can change roles, so nobody can seize ownership on their own."""
    me = client.get("/api/users/me").json()
    client.get("/api/users/me", headers=_hdr("ha-frank", "Frank"))
    frank_id = next(u["id"] for u in client.get("/api/users").json() if u["external_id"] == "ha-frank")
    # A second owner exists, so the last-owner guard is NOT what's blocking below.
    client.patch(f"/api/users/{frank_id}", json={"role": "owner", "status": "approved"})

    assert client.patch(f"/api/users/{me['id']}", json={"status": "disabled"}).status_code == 400
    deleted = client.delete(f"/api/users/{me['id']}")
    assert deleted.status_code == 400
    assert "your own account" in deleted.json()["detail"].lower()

    # …but stepping down (handing over, then demoting yourself) is still allowed.
    assert client.patch(f"/api/users/{me['id']}", json={"role": "member"}).status_code == 200
