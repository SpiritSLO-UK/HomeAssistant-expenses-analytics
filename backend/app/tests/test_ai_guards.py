"""AI endpoint abuse-guard tests (services/ai_guard.py + routes_ai).

Threat model: an already-authenticated caller must not be able to pump
oversized content through the AI gateway or run up unbounded cloud cost.
Covers: the per-user rate limit trips at the threshold and slides back open,
the payload cap rejects oversized bodies with 413, the daily request budget
trips with 429 (and also blocks the vision path in the service layer), and a
knob set to 0 disables its guard.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from app.config import settings
from app.db.session import SessionLocal
from app.models import AIRequest
from app.services import ai_guard, ai_service, settings_service
from app.services.ai_service import AIDisabled


@pytest.fixture(autouse=True)
def _fresh_guard_state():
    """Clear the process-global AI rate-limit window between tests (same
    isolation the MFA throttle gets in conftest)."""
    ai_guard.reset_throttle()
    yield
    ai_guard.reset_throttle()


def _add_ai_rows(n: int) -> None:
    """Insert n audit rows dated now (server default), as the gateway would."""
    with SessionLocal() as db:
        for _ in range(n):
            db.add(
                AIRequest(
                    provider="fake",
                    task_type="classify_transaction",
                    privacy_mode="local_llm",
                    approval_status="not_required",
                    status="completed",
                )
            )
        db.commit()


# --- per-user rate limit ---


def test_rate_limit_trips_at_threshold_and_slides_open(client, monkeypatch):
    monkeypatch.setattr(settings, "ai_rate_limit_per_minute", 3)
    for _ in range(3):
        assert client.post("/api/ai/test").status_code == 200
    r = client.post("/api/ai/test")
    assert r.status_code == 429
    assert "rate limit" in r.json()["detail"].lower()
    assert int(r.headers["Retry-After"]) >= 1
    # Sliding window: once the recorded calls age past the window, a new
    # request is allowed again.
    for times in ai_guard._recent_ai_calls.values():
        times[:] = [t - ai_guard.RATE_WINDOW - timedelta(seconds=1) for t in times]
    assert client.post("/api/ai/test").status_code == 200


def test_rate_limit_is_per_user(monkeypatch):
    monkeypatch.setattr(settings, "ai_rate_limit_per_minute", 1)
    assert ai_guard.rate_limit_wait_seconds(1) == 0
    assert ai_guard.rate_limit_wait_seconds(1) >= 1  # user 1 is over the limit
    assert ai_guard.rate_limit_wait_seconds(2) == 0  # user 2 is unaffected


def test_rate_limit_zero_disables(client, monkeypatch):
    monkeypatch.setattr(settings, "ai_rate_limit_per_minute", 0)
    for _ in range(10):
        assert client.post("/api/ai/test").status_code == 200


# --- payload-size cap ---


def test_payload_cap_rejects_oversized_body(client, monkeypatch):
    monkeypatch.setattr(settings, "ai_max_payload_bytes", 200)
    big = {"approve_ids": list(range(10_000, 11_000)), "reject_ids": []}
    r = client.post("/api/ai/cloud-batch/send", json=big)
    assert r.status_code == 413
    assert "payload cap" in r.json()["detail"]


def test_payload_cap_allows_normal_body(client, monkeypatch):
    monkeypatch.setattr(settings, "ai_max_payload_bytes", 200)
    # Small body passes the guard; AI itself is off (strict_local) -> 400.
    r = client.post("/api/ai/cloud-batch/send", json={"approve_ids": [], "reject_ids": []})
    assert r.status_code == 400


def test_payload_cap_zero_disables(client, monkeypatch):
    monkeypatch.setattr(settings, "ai_max_payload_bytes", 0)
    big = {"approve_ids": list(range(10_000, 11_000)), "reject_ids": []}
    r = client.post("/api/ai/cloud-batch/send", json=big)
    assert r.status_code == 400  # not 413: the guard is off, AI is off


# --- daily request budget ---


def test_daily_cap_trips(client, monkeypatch):
    monkeypatch.setattr(settings, "ai_daily_request_cap", 2)
    _add_ai_rows(2)
    r = client.post("/api/ai/test")
    assert r.status_code == 429
    assert "daily ai budget" in r.json()["detail"].lower()


def test_daily_cap_allows_below_threshold(client, monkeypatch):
    monkeypatch.setattr(settings, "ai_daily_request_cap", 2)
    _add_ai_rows(1)
    assert client.post("/api/ai/test").status_code == 200


def test_daily_cap_zero_disables(client, monkeypatch):
    monkeypatch.setattr(settings, "ai_daily_request_cap", 0)
    _add_ai_rows(3)
    assert client.post("/api/ai/test").status_code == 200


# --- vision extract path: rate limit WITHOUT the payload cap (#29) ---


class _StubVision:
    name = "fake"
    model = "m"

    def available(self) -> bool:
        return True

    def extract_from_image(self, image_b64, mime, *, system, instruction):
        return {"transactions": [{"date": "2026-06-01", "description": "X", "amount": "-1.00"}]}


def _local_llm(db) -> None:
    settings_service.set_value(db, settings_service.PRIVACY_MODE, "local_llm")
    db.commit()


def test_vision_extract_route_enforces_rate_limit(client, monkeypatch):
    # The image-extract routes are outside routes_ai's guard, so the per-user rate
    # limit is enforced in ai_service._require_vision. First call passes; the next
    # within the window trips 429 (+Retry-After), same as the classify routes.
    monkeypatch.setattr(settings, "ai_rate_limit_per_minute", 1)
    with SessionLocal() as db:
        _local_llm(db)
    monkeypatch.setattr(ai_service, "get_provider", lambda _db: _StubVision())
    img = {"file": ("s.png", b"\x89PNG\r\n" + b"0" * 100, "image/png")}
    first = client.post("/api/imports/ai-extract", files=img)
    assert first.status_code != 429  # a normal image is allowed through
    second = client.post("/api/imports/ai-extract", files=img)
    assert second.status_code == 429
    assert int(second.headers["Retry-After"]) >= 1


def test_vision_extract_route_bypasses_payload_cap(client, monkeypatch):
    # A legitimate image far larger than the 100 KB AI JSON payload cap must NOT be
    # 413'd on the vision route — only its own 15 MB image cap applies (#29).
    monkeypatch.setattr(settings, "ai_max_payload_bytes", 50)
    with SessionLocal() as db:
        _local_llm(db)
    monkeypatch.setattr(ai_service, "get_provider", lambda _db: _StubVision())
    big_img = {"file": ("s.png", b"\x89PNG\r\n" + b"0" * 5000, "image/png")}  # >> 50-byte cap
    r = client.post("/api/imports/ai-extract", files=big_img)
    assert r.status_code != 413
    assert r.status_code == 200, r.text


def test_daily_cap_blocks_vision_extract_in_service_layer(db, monkeypatch):
    # The image-extract routes live outside routes_ai's guard, so the budget is
    # also enforced in ai_service._require_vision before any provider dispatch.
    settings_service.set_value(db, settings_service.PRIVACY_MODE, "local_llm")
    monkeypatch.setattr(settings, "ai_daily_request_cap", 1)
    db.add(
        AIRequest(
            provider="fake",
            task_type="classify_transaction",
            privacy_mode="local_llm",
            approval_status="not_required",
            status="completed",
        )
    )
    db.commit()
    with pytest.raises(AIDisabled, match="Daily AI budget"):
        ai_service.extract_statement_image(db, b"img", "image/png")
