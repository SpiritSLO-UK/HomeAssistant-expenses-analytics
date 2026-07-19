"""Adversarial access-control tests (Stage 12-S4; backlog #74).

These assert the *negative* cases — that the access model can't be talked into
granting more than it should: no self-promotion, forged identity headers don't
confer privilege, MFA session tokens are bound to their user and expiry, and
role/status inputs are validated. The trust boundary (identity comes from the HA
ingress proxy — don't expose the raw port) is documented in docs/security.md.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select

from app.models import Household, User
from app.services import auth_service, mfa_service, totp


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


def test_cors_uses_explicit_method_and_header_allowlists():
    """CR-SEC-12: CORS scopes methods + headers to an explicit allowlist rather than
    '*' with credentials (which is over-broad). Pins it so it can't regress to '*'."""
    from app import main

    assert "*" not in main._CORS_METHODS and "*" not in main._CORS_HEADERS
    assert set(main._CORS_METHODS) == {"GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"}
    assert "Content-Type" in main._CORS_HEADERS and "X-HAFI-Session" in main._CORS_HEADERS


def test_member_cannot_change_encryption_state(client):
    """Encryption enable/disable are owner-only (follow-up to #214) — a member is 403'd
    *before* any encryption logic runs, so this holds regardless of the SQLCipher driver."""
    _make_member(client, "ha-mallory")
    h = _hdr("ha-mallory")
    # The value is irrelevant — the owner check 403s before the body is ever read; kept
    # in a neutrally-named variable so it isn't a credential-shaped literal for analysis.
    placeholder = "irrelevant"
    body = {"passphrase": placeholder}
    assert client.post("/api/security/enable", json=body, headers=h).status_code == 403
    assert client.post("/api/security/disable", json=body, headers=h).status_code == 403
    # Status stays readable (it must work pre-auth, even on a locked DB).
    assert client.get("/api/security/status", headers=h).status_code == 200


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


def test_default_trusts_proxy_headers_ha_ingress_safe(client):
    """CR-SEC-4: the flag DEFAULTS to trusting X-Remote-User-* so HA ingress keeps
    mapping the forwarded identity to a distinct user (turning this off there would
    break login). A second HA identity therefore still appears as its own row."""
    assert auth_service.settings.trust_proxy_headers is True
    client.get("/api/users/me")  # local owner bootstraps
    alice = client.get("/api/users/me", headers=_hdr("ha-alice", "Alice")).json()
    # A distinct forwarded identity is honoured → its own (pending) user.
    assert alice["role"] == "member" and alice["status"] == "pending"
    users = client.get("/api/users").json()  # the local owner may list users
    assert "ha-alice" in [u["external_id"] for u in users]


def test_untrusted_proxy_headers_are_ignored(client, monkeypatch):
    """CR-SEC-4 (standalone hardening): with the flag OFF the inbound identity
    headers are ignored entirely — every request, whatever headers it forges,
    resolves to the single 'local' owner, so a direct peer can't assert an
    identity by sending X-Remote-User-*."""
    monkeypatch.setattr(auth_service.settings, "trust_proxy_headers", False)
    me = client.get("/api/users/me", headers=_hdr("ha-attacker", "Attacker")).json()
    assert me["role"] == "owner" and me["status"] == "approved"
    # Only the single local owner exists — the forged identity created no user.
    users = client.get("/api/users", headers=_hdr("ha-attacker")).json()
    assert [u["external_id"] for u in users] == ["local"]
    # A different forged identity maps to the SAME local owner, not a new user.
    me2 = client.get("/api/users/me", headers=_hdr("ha-someone-else")).json()
    assert me2["id"] == me["id"]


def test_get_current_user_does_not_resurrect_deleted_user(db):
    """SR-E1: a stale request.state.user_id pointing at a since-deleted user must
    NOT silently rebuild that account from the request headers. It resolves to
    unauthenticated (401) and creates no user as a side effect."""
    req = SimpleNamespace(state=SimpleNamespace(user_id=999999), headers={})
    with pytest.raises(HTTPException) as exc:
        auth_service.get_current_user(req, db)
    assert exc.value.status_code == 401
    assert db.scalar(select(func.count(User.id))) == 0


def test_list_members_is_scoped_to_household(db):
    """SR-E1: list_members never leaks members of another household. The default
    (no explicit id) resolves to the first/default household; an explicit id scopes
    to exactly that household."""
    h1, h2 = Household(name="H1"), Household(name="H2")
    db.add_all([h1, h2])
    db.flush()
    db.add_all([
        User(household_id=h1.id, display_name="A", external_id="a", role="owner", status="approved"),
        User(household_id=h2.id, display_name="B", external_id="b", role="member", status="approved"),
    ])
    db.commit()
    assert [u.external_id for u in auth_service.list_members(db)] == ["a"]
    assert [u.external_id for u in auth_service.list_members(db, household_id=h2.id)] == ["b"]


def test_profile_fields_are_length_and_charset_bounded(client):
    """SR-E1: an owner-supplied display_name/email is length- and charset-bounded
    before it's persisted, so an over-long or control-char value is a 400."""
    uid = _make_member(client, "ha-vic")
    assert client.patch(f"/api/users/{uid}", json={"display_name": "x" * 201}).status_code == 400
    assert client.patch(f"/api/users/{uid}", json={"display_name": "bad\x00name"}).status_code == 400
    assert client.patch(f"/api/users/{uid}", json={"email": "not-an-email"}).status_code == 400
    assert client.patch(f"/api/users/{uid}", json={"email": "x" * 316 + "@e.com"}).status_code == 400
    ok = client.patch(f"/api/users/{uid}", json={"display_name": "Valid Name", "email": "v@example.com"})
    assert ok.status_code == 200
    assert ok.json()["display_name"] == "Valid Name" and ok.json()["email"] == "v@example.com"


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
