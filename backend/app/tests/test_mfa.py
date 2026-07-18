"""App-level MFA (TOTP): enrolment, the entry gate, and admin step-up (#124)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.config import settings
from app.db.session import SessionLocal
from app.models import User, UserSession
from app.services import mfa_service, totp


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


def test_mfa_verify_locks_out_after_repeated_failures(client):
    """CR-SEC-6: repeated bad codes throttle the endpoint — after the limit, even
    a correct code is refused (429) until the lockout window passes. A sniffed/
    brute-forced TOTP can't be hammered."""
    from app.services import mfa_service

    secret = _enable_mfa(client)
    bad = _wrong(totp.current_code(secret))
    for _ in range(mfa_service.MFA_MAX_FAILED):
        assert client.post("/api/auth/mfa/verify", json={"code": bad}).status_code == 400
    # Locked out now — a correct code is still refused with 429 + Retry-After.
    blocked = client.post("/api/auth/mfa/verify", json={"code": totp.current_code(secret)})
    assert blocked.status_code == 429
    assert "Retry-After" in blocked.headers


def test_mfa_success_clears_failure_streak(client):
    """A successful verification resets the throttle, so earlier near-misses don't
    accumulate toward a lockout."""
    from app.services import mfa_service

    secret = _enable_mfa(client)
    bad = _wrong(totp.current_code(secret))
    for _ in range(mfa_service.MFA_MAX_FAILED - 1):  # one short of lockout
        assert client.post("/api/auth/mfa/verify", json={"code": bad}).status_code == 400
    # A good code succeeds and clears the streak...
    assert client.post("/api/auth/mfa/verify", json={"code": totp.current_code(secret)}).status_code == 200
    # ...so the same number of misses again still doesn't lock out.
    for _ in range(mfa_service.MFA_MAX_FAILED - 1):
        assert client.post("/api/auth/mfa/verify", json={"code": bad}).status_code == 400


def test_verify_code_is_single_use(client):
    """CR-SEC-5: a TOTP code can't be replayed on the entry path — the second
    use of the same (still-in-period) code is refused, so a sniffed code can't
    mint a second session."""
    secret = _enable_mfa(client)
    code = totp.current_code(secret)
    assert client.post("/api/auth/mfa/verify", json={"code": code}).status_code == 200
    # Same code again, within its validity window → rejected (already consumed).
    replay = client.post("/api/auth/mfa/verify", json={"code": code})
    assert replay.status_code == 400


def test_reenrolment_requires_current_code_and_keeps_factor_live(client):
    """SR-1: once MFA is enabled, hitting /setup can't silently downgrade it.

    A re-enrolment with no code (or a wrong code) is refused, and the live factor
    stays enabled. A re-enrolment with the *current* code issues a new pending
    secret but leaves the old secret active until the new one is confirmed via
    /enable — so the second factor is never off in between.
    """
    secret = _enable_mfa(client)

    # No code → refused; MFA stays on.
    assert client.post("/api/auth/mfa/setup").status_code == 400
    assert client.get("/api/users/me").json()["mfa_enabled"] is True

    # Wrong code → refused; MFA stays on.
    bad = _wrong(totp.current_code(secret))
    assert client.post("/api/auth/mfa/setup", json={"code": bad}).status_code == 400
    assert client.get("/api/users/me").json()["mfa_enabled"] is True

    # Correct current code → a new pending secret is issued, but the OLD secret is
    # still live (MFA enabled, and a verify with the old secret still works).
    new_secret = client.post("/api/auth/mfa/setup", json={"code": totp.current_code(secret)}).json()["secret"]
    assert new_secret != secret
    me = client.get("/api/users/me").json()
    assert me["mfa_enabled"] is True
    assert client.post("/api/auth/mfa/verify", json={"code": totp.current_code(secret)}).status_code == 200

    # Confirming a code for the NEW secret promotes it; the old secret no longer works.
    assert client.post("/api/auth/mfa/enable", json={"code": totp.current_code(new_secret)}).status_code == 200
    assert client.post("/api/auth/mfa/verify", json={"code": totp.current_code(new_secret)}).status_code == 200


def test_reenrolment_invalidates_old_sessions(client):
    """Re-enrolling mints a new TOTP secret; sessions opened against the OLD
    secret must not survive the swap. Once /enable promotes the new secret, a
    token minted against the old secret is rejected by the entry gate."""
    secret = _enable_mfa(client)
    token = client.post("/api/auth/mfa/verify", json={"code": totp.current_code(secret)}).json()["token"]
    sess = {"X-HAFI-Session": token}
    assert client.get("/api/transactions", headers=sess).status_code == 200

    # Re-enrol with the current code, then confirm the new secret.
    new_secret = client.post(
        "/api/auth/mfa/setup", json={"code": totp.current_code(secret)}
    ).json()["secret"]
    assert new_secret != secret
    assert client.post(
        "/api/auth/mfa/enable", json={"code": totp.current_code(new_secret)}
    ).status_code == 200

    # The old-secret session is gone — its token no longer opens the gate.
    reblocked = client.get("/api/transactions", headers=sess)
    assert reblocked.status_code == 403
    assert reblocked.json()["mfa_required"] is True
    # A session verified against the NEW secret works.
    new_token = client.post(
        "/api/auth/mfa/verify", json={"code": totp.current_code(new_secret)}
    ).json()["token"]
    assert client.get("/api/transactions", headers={"X-HAFI-Session": new_token}).status_code == 200


def test_session_cap_evicts_oldest(db, monkeypatch):
    """A verification that would exceed MAX_SESSIONS_PER_USER evicts the oldest
    session, so a user can't accumulate unbounded session rows."""
    monkeypatch.setattr(settings, "db_key", None)
    secret = totp.generate_secret()
    user = _mk_user(db, "Capped", mfa_enabled=True, mfa_secret=secret)

    cap = mfa_service.MAX_SESSIONS_PER_USER
    base = datetime.now(UTC).replace(tzinfo=None) - timedelta(hours=1)
    # Seed exactly `cap` still-valid sessions, oldest first (distinct created_at).
    for i in range(cap):
        db.add(
            UserSession(
                user_id=user.id,
                token_hash=f"{i:064d}",
                created_at=base + timedelta(minutes=i),
                expires_at=base + timedelta(hours=12),
                last_step_up_at=base,
            )
        )
    db.commit()

    # Minting one more would be cap+1 → the single oldest is evicted back to cap.
    assert mfa_service.verify_and_open(db, user, totp.current_code(secret)) is not None
    rows = db.query(UserSession).filter(UserSession.user_id == user.id).all()
    assert len(rows) == cap
    hashes = {r.token_hash for r in rows}
    assert f"{0:064d}" not in hashes  # the oldest seeded session was dropped
    assert f"{cap - 1:064d}" in hashes  # a newer seeded one survived


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


def test_matched_counter_tracks_timestep():
    secret = totp.generate_secret()
    # The matched counter is floor(time / period); used for one-time-use checks.
    assert totp.matched_counter(secret, totp.current_code(secret, now=90), now=90) == 3
    # A code from the previous period still matches (skew) but at the older counter.
    assert totp.matched_counter(secret, totp.current_code(secret, now=0), now=30) == 0
    assert totp.matched_counter(secret, "000000", now=10_000) is None


def test_totp_roundtrip_and_skew():
    secret = totp.generate_secret()
    assert totp.verify(secret, totp.current_code(secret))
    # A code from one period ago still passes (±1 window for clock skew).
    prev = totp.current_code(secret, now=0)
    assert totp.verify(secret, prev, now=30)
    assert not totp.verify(secret, "000000", now=10_000)


def test_verify_window_is_clamped():
    # An absurd window can't widen acceptance beyond ±2 periods (SR-E3).
    secret = totp.generate_secret()
    old = totp.current_code(secret, now=0)  # counter 0
    assert not totp.verify(secret, old, now=5 * totp.PERIOD, window=999)  # 5 periods → rejected
    assert totp.verify(secret, old, now=2 * totp.PERIOD, window=999)      # within ±2 → accepted


# --- TOTP secret at-rest encryption (CR-SEC-13) ------------------------------


def _mk_user(db, name: str, **kw) -> User:
    user = User(display_name=name, external_id=name.lower(), role="owner", status="approved", **kw)
    db.add(user)
    db.commit()
    return user


def test_secret_encrypted_at_rest_when_key_set(db, monkeypatch):
    """With an app key configured, the enrolled seed is stored as app-layer
    ciphertext (marker-prefixed, not the raw base32) and still round-trips
    through enable → verify."""
    monkeypatch.setattr(settings, "db_key", "test-app-key")
    user = _mk_user(db, "Enc")

    secret = mfa_service.start_enrolment(db, user)["secret"]
    # Pending seed is stored encrypted, never as the raw base32.
    assert user.mfa_pending_secret != secret
    assert user.mfa_pending_secret.startswith(mfa_service._ENC_PREFIX)

    assert mfa_service.enable(db, user, totp.current_code(secret)) is True
    assert user.mfa_secret != secret
    assert user.mfa_secret.startswith(mfa_service._ENC_PREFIX)
    # It decrypts back to the original seed and opens a session.
    assert mfa_service.decrypt_secret(user.mfa_secret) == secret
    assert mfa_service.verify_and_open(db, user, totp.current_code(secret)) is not None


def test_legacy_plaintext_secret_still_verifies(db, monkeypatch):
    """Backward compat: a pre-existing plaintext base32 seed (no marker) is used
    as-is and still verifies, even with an app key now configured."""
    monkeypatch.setattr(settings, "db_key", "test-app-key")
    secret = totp.generate_secret()
    user = _mk_user(db, "Legacy", mfa_enabled=True, mfa_secret=secret)

    # An unmarked value is treated as plaintext and returned unchanged.
    assert mfa_service.decrypt_secret(secret) == secret
    assert mfa_service.verify_and_open(db, user, totp.current_code(secret)) is not None


def test_no_app_key_keeps_plaintext_behaviour(db, monkeypatch):
    """No app key → seeds stay plaintext (encrypting would make MFA
    undecryptable). Behaviour is unchanged and nothing crashes."""
    monkeypatch.setattr(settings, "db_key", None)
    user = _mk_user(db, "Plain")

    secret = mfa_service.start_enrolment(db, user)["secret"]
    assert user.mfa_pending_secret == secret  # stored as-is, no marker
    assert not user.mfa_pending_secret.startswith(mfa_service._ENC_PREFIX)

    assert mfa_service.enable(db, user, totp.current_code(secret)) is True
    assert user.mfa_secret == secret
    assert mfa_service.verify_and_open(db, user, totp.current_code(secret)) is not None


def test_encrypted_secret_fails_closed_without_or_wrong_key(monkeypatch):
    """A marked ciphertext is unrecoverable if the key is missing or wrong — it
    returns None (verify fails closed) rather than crashing or mis-firing the
    plaintext fallback."""
    monkeypatch.setattr(settings, "db_key", "real-key")
    blob = mfa_service.encrypt_secret(totp.generate_secret())
    assert blob.startswith(mfa_service._ENC_PREFIX)

    monkeypatch.setattr(settings, "db_key", None)
    assert mfa_service.decrypt_secret(blob) is None  # no key

    monkeypatch.setattr(settings, "db_key", "wrong-key")
    assert mfa_service.decrypt_secret(blob) is None  # wrong key, no exception
