"""Security-health panel + failed-unlock tracking (Stage 12-S3; #128/#130)."""

from __future__ import annotations

from app.services import security_service


def _normalise_unlocks():
    """Clear any failed-unlock state leaked from another test (shared temp dir)."""
    security_service.record_successful_unlock()


def _check(checks: list[dict], cid: str) -> dict | None:
    return next((c for c in checks if c["id"] == cid), None)


def test_failed_unlock_tracking():
    _normalise_unlocks()
    assert security_service.failed_unlock_summary()["recent"] == 0

    security_service.record_failed_unlock()
    n = security_service.record_failed_unlock()
    assert n == 2
    summary = security_service.failed_unlock_summary()
    assert summary["recent"] == 2
    assert summary["last_attempt_at"] is not None

    security_service.record_successful_unlock()
    assert security_service.failed_unlock_summary()["recent"] == 0


def test_health_flags_missing_mfa_and_dismiss(client):
    _normalise_unlocks()
    health = client.get("/api/security/health").json()
    mfa = _check(health["checks"], "mfa")
    assert mfa is not None and mfa["severity"] == "warn" and mfa["active"] is True
    before = health["active_count"]
    assert before >= 1

    # Dismiss it → no longer active.
    d = client.post("/api/security/health/dismiss", json={"check_id": "mfa"}).json()
    assert d["dismissed"] is True
    health2 = client.get("/api/security/health").json()
    assert _check(health2["checks"], "mfa")["active"] is False
    assert health2["active_count"] == before - 1

    # Clear the dismissal → active again.
    client.post("/api/security/health/dismiss", json={"check_id": "mfa", "clear": True})
    assert _check(client.get("/api/security/health").json()["checks"], "mfa")["active"] is True


def test_health_snooze_sets_until(client):
    _normalise_unlocks()
    d = client.post("/api/security/health/dismiss", json={"check_id": "mfa", "snooze_days": 7}).json()
    assert d["dismissed"] is True
    assert d["snoozed_until"] is not None
    # Snoozed checks aren't active.
    assert _check(client.get("/api/security/health").json()["checks"], "mfa")["active"] is False
    client.post("/api/security/health/dismiss", json={"check_id": "mfa", "clear": True})


def test_failed_unlocks_surface_as_a_warning(client):
    _normalise_unlocks()
    for _ in range(3):
        security_service.record_failed_unlock()
    fu = _check(client.get("/api/security/health").json()["checks"], "failed_unlocks")
    assert fu is not None and fu["severity"] == "warn" and fu["active"] is True
    security_service.record_successful_unlock()  # cleanup for other tests


def test_health_is_owner_only(client):
    _normalise_unlocks()
    client.get("/api/users/me")  # bootstrap the local owner first
    member = {"X-Remote-User-Id": "ha-mike", "X-Remote-User-Display-Name": "Mike"}
    client.get("/api/users/me", headers=member)  # appears pending
    mike_id = next(u["id"] for u in client.get("/api/users").json() if u["external_id"] == "ha-mike")
    client.patch(f"/api/users/{mike_id}", json={"role": "member", "status": "approved"})

    assert client.get("/api/security/health", headers=member).status_code == 403
