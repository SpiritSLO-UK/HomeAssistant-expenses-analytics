"""Adversarial access-control tests (Stage 12-S4; backlog #74).

These assert the *negative* cases — that the access model can't be talked into
granting more than it should: no self-promotion, forged identity headers don't
confer privilege, MFA session tokens are bound to their user and expiry, and
role/status inputs are validated. The trust boundary (identity comes from the HA
ingress proxy — don't expose the raw port) is documented in docs/security.md.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.models import User
from app.services import mfa_service, totp


def _hdr(uid: str, name: str | None = None) -> dict[str, str]:
    return {"X-Remote-User-Id": uid, "X-Remote-User-Display-Name": name or uid}


def _enable_mfa(client, headers=None) -> str:
    secret = client.post("/api/auth/mfa/setup", headers=headers).json()["secret"]
    assert client.post("/api/auth/mfa/enable", json={"code": totp.current_code(secret)},
                       headers=headers).status_code == 200
    return secret


def _make_member(client, ext_id: str):
    """Bootstrap the owner, then add an approved member identified by ext_id."""
    client.get("/api/users/me")  # local owner first
    client.get("/api/users/me", headers=_hdr(ext_id))  # appears pending
    uid = next(u["id"] for u in client.get("/api/users").json() if u["external_id"] == ext_id)
    client.patch(f"/api/users/{uid}", json={"role": "member", "status": "approved"})
    return uid


def test_member_cannot_manage_or_self_promote(client):
    mid = _make_member(client, "ha-mallory")
    h = _hdr("ha-mallory")

    # Can't list/manage users at all…
    assert client.get("/api/users", headers=h).status_code == 403
    # …and can't promote itself to owner.
    assert client.patch(f"/api/users/{mid}", json={"role": "owner"}, headers=h).status_code == 403
    # Role is unchanged (read from the stored row, never the request).
    assert client.get("/api/users/me", headers=h).json()["role"] == "member"


def test_forged_identity_headers_do_not_grant_privilege(client):
    client.get("/api/users/me")  # local owner exists first
    # An attacker invents an identity and tries to assert a role via headers.
    forged = {
        **_hdr("ha-attacker", "Attacker"),
        "X-Remote-User-Role": "owner",
        "Role": "owner",
        "X-Hafi-Role": "owner",
    }
    me = client.get("/api/users/me", headers=forged).json()
    assert me["role"] == "member" and me["status"] == "pending" and me["is_admin"] is False
    # No data access — a new identity is pending until the owner approves it.
    assert client.get("/api/transactions", headers=forged).status_code == 403


def test_invalid_role_or_status_rejected(client):
    uid = _make_member(client, "ha-vlad")
    assert client.patch(f"/api/users/{uid}", json={"role": "superadmin"}).status_code == 400
    assert client.patch(f"/api/users/{uid}", json={"status": "banned"}).status_code == 400


def test_disabled_user_is_blocked(client):
    uid = _make_member(client, "ha-dan")
    assert client.get("/api/transactions", headers=_hdr("ha-dan")).status_code == 200
    client.patch(f"/api/users/{uid}", json={"status": "disabled"})
    blocked = client.get("/api/transactions", headers=_hdr("ha-dan"))
    assert blocked.status_code == 403
    assert blocked.json()["account_status"] == "disabled"


def test_child_role_is_confined_to_allowance(client):
    # The child role is a narrow allowance view (backlog #82): it may read only its
    # own allowance summary, never the household data, and never write.
    client.get("/api/users/me")
    client.get("/api/users/me", headers=_hdr("ha-kid"))
    kid_id = next(u["id"] for u in client.get("/api/users").json() if u["external_id"] == "ha-kid")
    client.patch(f"/api/users/{kid_id}", json={"role": "child", "status": "approved"})

    assert client.get("/api/allowance/summary", headers=_hdr("ha-kid")).status_code == 200
    assert client.get("/api/transactions", headers=_hdr("ha-kid")).status_code == 403
    assert client.post("/api/tags", json={"name": "x"}, headers=_hdr("ha-kid")).status_code == 403


def test_backup_routes_require_owner(client):
    """CR-SEC-1: DB download/restore and config export/import are owner-only. A
    non-owner member must get 403 on all six — restoring an attacker-crafted DB is
    a full takeover, and downloading the DB/config is full data exfiltration."""
    _make_member(client, "ha-eve")
    h = _hdr("ha-eve")
    db_file = ("x.db", b"not-a-db", "application/octet-stream")
    enc_file = ("x.db.enc", b"x", "application/octet-stream")
    cfg_file = ("c.json", b"{}", "application/json")

    assert client.get("/api/backup/database", headers=h).status_code == 403
    assert client.post("/api/backup/restore", headers=h, files={"file": db_file}).status_code == 403
    assert client.post("/api/backup/database/encrypted", headers=h, data={"passphrase": "x"}).status_code == 403
    assert client.post(
        "/api/backup/restore/encrypted", headers=h, files={"file": enc_file}, data={"passphrase": "x"}
    ).status_code == 403
    assert client.get("/api/backup/config", headers=h).status_code == 403
    assert client.post("/api/backup/config", headers=h, files={"file": cfg_file}).status_code == 403

    # The owner (local identity, no header) is still allowed through the gate.
    assert client.get("/api/backup/database").status_code == 200
    assert client.get("/api/backup/config").status_code == 200


def test_garbage_mfa_token_is_rejected(client):
    _enable_mfa(client)  # local owner turns MFA on
    blocked = client.get("/api/transactions", headers={"X-HAFI-Session": "not-a-real-token"})
    assert blocked.status_code == 403 and blocked.json()["mfa_required"] is True


def test_mfa_session_is_bound_to_user_and_expiry(db):
    secret = totp.generate_secret()
    owner = User(display_name="A", external_id="a", role="owner", status="approved",
                 mfa_enabled=True, mfa_secret=secret)
    other = User(display_name="B", external_id="b", role="member", status="approved")
    db.add_all([owner, other])
    db.commit()

    token = mfa_service.verify_and_open(db, owner, totp.current_code(secret))
    assert token is not None
    assert mfa_service.get_valid_session(db, owner.id, token) is not None
    # A different user can't reuse the owner's token…
    assert mfa_service.get_valid_session(db, other.id, token) is None
    # …a forged token is rejected…
    assert mfa_service.get_valid_session(db, owner.id, "forged") is None
    # …and an expired session is rejected.
    session = mfa_service.get_valid_session(db, owner.id, token)
    session.expires_at = datetime.now(UTC).replace(tzinfo=None) - timedelta(minutes=1)
    db.commit()
    assert mfa_service.get_valid_session(db, owner.id, token) is None
