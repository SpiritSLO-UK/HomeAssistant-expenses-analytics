"""App-level MFA (TOTP): enrolment, the entry gate, and admin step-up (#124)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.db.session import SessionLocal
from app.models import UserSession
from app.services import totp


def _wrong(code: str) -> str:
    """A 6-digit string guaranteed to differ from ``code``."""
    other = "000000" if code != "000000" else "111111"
    return other


def _enable_mfa(client, headers=None) -> str:
    """Enrol + enable MFA for the resolved user; returns the TOTP secret."""
    secret = client.post("/api/auth/mfa/setup", headers=headers).json()["secret"]
    code = totp.current_code(secret)
    r = client.post("/api/auth/mfa/enable", json={"code": code}, headers=headers)
    assert r.status_code == 200
    return secret


def test_setup_and_enable(client):
    secret = client.post("/api/auth/mfa/setup").json()
    assert secret["otpauth_uri"].startswith("otpauth://totp/")
    assert len(secret["secret"]) >= 16

    # Before confirming, MFA is not active.
    assert client.get("/api/users/me").json()["mfa_enabled"] is False

    code = totp.current_code(secret["secret"])
    assert client.post("/api/auth/mfa/enable", json={"code": code}).status_code == 200

    me = client.get("/api/users/me").json()
    assert me["mfa_enabled"] is True
    assert me["mfa_required"] is True  # enabled but no session presented yet


def test_enable_rejects_wrong_code(client):
    secret = client.post("/api/auth/mfa/setup").json()["secret"]
    bad = _wrong(totp.current_code(secret))
    assert client.post("/api/auth/mfa/enable", json={"code": bad}).status_code == 400
    assert client.get("/api/users/me").json()["mfa_enabled"] is False


def test_entry_gate_blocks_until_verified(client):
    secret = _enable_mfa(client)

    # No session token → data APIs blocked.
    blocked = client.get("/api/transactions")
    assert blocked.status_code == 403
    assert blocked.json()["mfa_required"] is True

    # Verify → token → access.
    token = client.post("/api/auth/mfa/verify", json={"code": totp.current_code(secret)}).json()["token"]
    sess = {"X-HAFI-Session": token}
    assert client.get("/api/transactions", headers=sess).status_code == 200
    assert client.get("/api/users/me", headers=sess).json()["mfa_required"] is False


def test_verify_rejects_wrong_code(client):
    secret = _enable_mfa(client)
    bad = _wrong(totp.current_code(secret))
    assert client.post("/api/auth/mfa/verify", json={"code": bad}).status_code == 400


def test_disable_clears_mfa_and_sessions(client):
    secret = _enable_mfa(client)
    token = client.post("/api/auth/mfa/verify", json={"code": totp.current_code(secret)}).json()["token"]
    sess = {"X-HAFI-Session": token}

    assert client.post("/api/auth/mfa/disable", json={"code": totp.current_code(secret)}, headers=sess).status_code == 200
    me = client.get("/api/users/me").json()
    assert me["mfa_enabled"] is False
    assert me["mfa_required"] is False
    # With MFA off, no session header is needed.
    assert client.get("/api/transactions").status_code == 200


def test_admin_action_requires_recent_step_up(client):
    secret = _enable_mfa(client)
    token = client.post("/api/auth/mfa/verify", json={"code": totp.current_code(secret)}).json()["token"]
    sess = {"X-HAFI-Session": token}

    # A second user appears and needs approval.
    client.get("/api/users/me", headers={"X-Remote-User-Id": "ha-zoe", "X-Remote-User-Display-Name": "Zoe"})
    zoe_id = next(
        u["id"] for u in client.get("/api/users", headers=sess).json() if u["external_id"] == "ha-zoe"
    )

    # Force the step-up to be stale, then the admin action must be challenged.
    with SessionLocal() as db:
        row = db.query(UserSession).one()
        row.last_step_up_at = datetime.now(UTC).replace(tzinfo=None) - timedelta(hours=1)
        db.commit()

    stale = client.post(f"/api/users/{zoe_id}/approve", headers=sess)
    assert stale.status_code == 403
    assert stale.json()["detail"] == "step_up_required"

    # Step up with a fresh code, then the same action succeeds.
    assert client.post("/api/auth/mfa/step-up", json={"code": totp.current_code(secret)}, headers=sess).status_code == 200
    assert client.post(f"/api/users/{zoe_id}/approve", headers=sess).status_code == 200


def _member(client, uid: str, name: str) -> tuple[int, dict]:
    hdr = {"X-Remote-User-Id": uid, "X-Remote-User-Display-Name": name}
    client.get("/api/users/me")  # owner bootstraps
    client.get("/api/users/me", headers=hdr)
    mid = next(u["id"] for u in client.get("/api/users").json() if u["external_id"] == uid)
    client.patch(f"/api/users/{mid}", json={"role": "member", "status": "approved"})
    return mid, hdr


def test_app_scope_skips_admin_step_up(client):
    # Enable MFA with the entry-only scope (#157).
    secret = client.post("/api/auth/mfa/setup").json()["secret"]
    client.post("/api/auth/mfa/enable", json={"code": totp.current_code(secret), "scope": "app"})
    token = client.post("/api/auth/mfa/verify", json={"code": totp.current_code(secret)}).json()["token"]
    sess = {"X-HAFI-Session": token}
    assert client.get("/api/users/me", headers=sess).json()["mfa_scope"] == "app"

    client.get("/api/users/me", headers={"X-Remote-User-Id": "ha-zoe", "X-Remote-User-Display-Name": "Zoe"})
    zoe_id = next(u["id"] for u in client.get("/api/users", headers=sess).json() if u["external_id"] == "ha-zoe")

    # Even with a stale step-up, an admin action succeeds — 'app' scope = no step-up.
    with SessionLocal() as db:
        row = db.query(UserSession).one()
        row.last_step_up_at = datetime.now(UTC).replace(tzinfo=None) - timedelta(hours=1)
        db.commit()
    assert client.post(f"/api/users/{zoe_id}/approve", headers=sess).status_code == 200


def test_admin_can_require_mfa(client):
    bob_id, bob = _member(client, "ha-bob", "Bob")
    assert client.get("/api/transactions", headers=bob).status_code == 200  # before

    r = client.patch(f"/api/users/{bob_id}", json={"mfa_policy": "required"})
    assert r.status_code == 200 and r.json()["mfa_policy"] == "required"

    # Blocked until enrolled — but /me + the MFA self-service stay reachable.
    blocked = client.get("/api/transactions", headers=bob)
    assert blocked.status_code == 403 and blocked.json()["mfa_setup_required"] is True
    assert client.get("/api/users/me", headers=bob).json()["mfa_setup_required"] is True

    secret = client.post("/api/auth/mfa/setup", headers=bob).json()["secret"]
    assert client.post("/api/auth/mfa/enable", json={"code": totp.current_code(secret)}, headers=bob).status_code == 200
    # Enrolled → past the setup gate; now it's the normal entry gate (verify to get in).
    token = client.post("/api/auth/mfa/verify", json={"code": totp.current_code(secret)}, headers=bob).json()["token"]
    assert client.get("/api/transactions", headers={**bob, "X-HAFI-Session": token}).status_code == 200


def test_set_mfa_policy_invalid_400(client):
    bob_id, _bob = _member(client, "ha-bob", "Bob")
    assert client.patch(f"/api/users/{bob_id}", json={"mfa_policy": "bogus"}).status_code == 400


def test_totp_roundtrip_and_skew():
    secret = totp.generate_secret()
    assert totp.verify(secret, totp.current_code(secret))
    # A code from one period ago still passes (±1 window for clock skew).
    prev = totp.current_code(secret, now=0)
    assert totp.verify(secret, prev, now=30)
    assert not totp.verify(secret, "000000", now=10_000)
